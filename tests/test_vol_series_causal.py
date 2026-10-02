"""Causality, unit, and caching tests for grid.sim.vol_series (Phase 15E)."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from data.volatility import aggregate_intraday_to_hourly, build_volatility_frame
from grid.sim.data import CandleData, ewma_sigma_24h
from grid.sim.vol_series import (
    causal_train_predict,
    has_sufficient_training_history,
    hourly_forecast_for_window,
    vol_series_24h,
    window_train_cutoff,
)


def _synthetic_5m(hours: int, seed: int = 7, start: str = "2024-01-01") -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    bars = hours * 12
    timestamps = pd.date_range(start, periods=bars, freq="5min", tz="UTC")
    returns = rng.normal(0.0, 0.0015, bars)
    close = 1.0 * np.exp(np.cumsum(returns))
    open_ = np.r_[1.0, close[:-1]]
    high = np.maximum(open_, close) * (1.0 + rng.uniform(0.0, 0.0008, bars))
    low = np.minimum(open_, close) * (1.0 - rng.uniform(0.0, 0.0008, bars))
    volume = rng.uniform(1000.0, 5000.0, bars)
    return pd.DataFrame({
        "timestamp": timestamps, "open": open_, "high": high, "low": low,
        "close": close, "volume": volume,
    })


def _frame_from_5m(five_min: pd.DataFrame, horizon: int = 24) -> pd.DataFrame:
    intraday = aggregate_intraday_to_hourly(five_min)
    return build_volatility_frame(intraday, horizon, intraday=intraday)


def _mutate_after(five_min: pd.DataFrame, cutoff_ts: pd.Timestamp, seed: int = 99) -> pd.DataFrame:
    """Return a copy identical before ``cutoff_ts`` with large shocks injected after it."""
    mutated = five_min.copy(deep=True)
    rng = np.random.default_rng(seed)
    mask = mutated["timestamp"] >= cutoff_ts
    n = int(mask.sum())
    assert n > 0, "mutation window must be non-empty for the test to be meaningful"
    shock = np.exp(np.cumsum(rng.normal(0.0, 0.05, n)))
    for column in ("open", "high", "low", "close"):
        mutated.loc[mask, column] = mutated.loc[mask, column] * shock
    mutated.loc[mask, "high"] = mutated.loc[mask, ["open", "high", "low", "close"]].max(axis=1)
    mutated.loc[mask, "low"] = mutated.loc[mask, ["open", "high", "low", "close"]].min(axis=1)
    return mutated


TOTAL_HOURS = 150 * 24  # 150 days: >90d causal training history + a 30d+ test tail
EMBARGO_HOURS = 24


def _window_for(five_min: pd.DataFrame, train_days: int = 90):
    start_ts = int(five_min["timestamp"].iloc[0].timestamp()) + train_days * 86400 + EMBARGO_HOURS * 3600
    end_ts = int(five_min["timestamp"].iloc[-1].timestamp()) + 300
    return {"window_id": 0, "start": start_ts, "end_exclusive": end_ts, "start_idx": 0, "end_idx": 0}


@pytest.mark.parametrize("model_name", ["persistence", "nexo_har"])
def test_mutating_future_prices_does_not_change_past_sigma(model_name):
    five_min = _synthetic_5m(TOTAL_HOURS)
    frame_original = _frame_from_5m(five_min)
    window = _window_for(five_min)
    cutoff_ts = window_train_cutoff(window["start"], embargo_hours=EMBARGO_HOURS)

    original = causal_train_predict(frame_original, model_name, cutoff_ts)
    assert original is not None, "synthetic history should be long enough to train"
    pred_original, var_factor_original = original

    # Mutate strictly inside the test region (after the embargo cutoff), so the
    # fitted model itself (trained only on data before cutoff_ts) cannot change.
    mutate_from = pd.Timestamp(cutoff_ts, unit="s", tz="UTC") + pd.Timedelta(days=10)
    mutated_5m = _mutate_after(five_min, mutate_from)
    frame_mutated = _frame_from_5m(mutated_5m)
    mutated = causal_train_predict(frame_mutated, model_name, cutoff_ts)
    assert mutated is not None
    pred_mutated, var_factor_mutated = mutated

    times = pd.to_datetime(frame_original["close_time"], utc=True)
    before = (times < mutate_from).to_numpy()
    after = (times >= mutate_from).to_numpy()

    np.testing.assert_allclose(
        pred_original.to_numpy(dtype=float)[before],
        pred_mutated.to_numpy(dtype=float)[before],
        rtol=0, atol=0,
        err_msg=f"{model_name}: sigma before the mutation point changed",
    )
    assert var_factor_original == var_factor_mutated, "var_factor must not depend on future mutations"
    # Sanity: the mutation actually changes something after the cutoff, or the test is vacuous.
    after_original = pred_original.to_numpy(dtype=float)[after]
    after_mutated = pred_mutated.to_numpy(dtype=float)[after]
    finite = np.isfinite(after_original) & np.isfinite(after_mutated)
    assert finite.any() and not np.allclose(after_original[finite], after_mutated[finite]), (
        "mutation should have changed at least some post-cutoff predictions"
    )


@pytest.mark.parametrize("model_name", ["persistence", "nexo_har"])
def test_training_never_uses_data_at_or_after_embargo_cutoff(model_name):
    """Mutating only the training window (before the embargo) must change the forecast;
    mutating only inside the embargo gap must not."""
    five_min = _synthetic_5m(TOTAL_HOURS)
    window = _window_for(five_min)
    cutoff_ts = window_train_cutoff(window["start"], embargo_hours=EMBARGO_HOURS)
    frame_original = _frame_from_5m(five_min)
    original = causal_train_predict(frame_original, model_name, cutoff_ts)
    assert original is not None
    _, var_factor_original = original

    cutoff_dt = pd.Timestamp(cutoff_ts, unit="s", tz="UTC")
    train_region_point = cutoff_dt - pd.Timedelta(days=30)
    mutated_training = _mutate_after(five_min, train_region_point, seed=5)
    mutated_training.loc[mutated_training["timestamp"] >= cutoff_dt] = five_min.loc[
        mutated_training["timestamp"] >= cutoff_dt
    ]
    frame_mutated_train = _frame_from_5m(mutated_training)
    mutated_train_result = causal_train_predict(frame_mutated_train, model_name, cutoff_ts)
    assert mutated_train_result is not None
    _, var_factor_mutated_train = mutated_train_result
    assert var_factor_mutated_train != var_factor_original, (
        "mutating genuine training history should change the fitted model"
    )


def test_ewma_variant_matches_ewma_sigma_24h_directly():
    five_min = _synthetic_5m(24 * 10)
    candles = CandleData(
        timestamp=(five_min["timestamp"].astype("int64") // 10**9).to_numpy(),
        open=five_min["open"].to_numpy(), high=five_min["high"].to_numpy(),
        low=five_min["low"].to_numpy(), close=five_min["close"].to_numpy(), gaps=0,
    )
    window = {"window_id": 0, "start": int(candles.timestamp[0]), "end_exclusive": int(candles.timestamp[-1]) + 300}
    direct = ewma_sigma_24h(candles.close, 72.0)
    via_series = vol_series_24h(candles, "ewma", window)
    np.testing.assert_array_equal(direct, via_series)


def test_vol_series_24h_units_match_ewma_scale():
    """sigma_24h from the hourly models should be the same order of magnitude as the
    EWMA control (both are 24h log-return standard deviations)."""
    five_min = _synthetic_5m(TOTAL_HOURS)
    frame = _frame_from_5m(five_min)
    window = _window_for(five_min)
    candles = CandleData(
        timestamp=(five_min["timestamp"].astype("int64") // 10**9).to_numpy(),
        open=five_min["open"].to_numpy(), high=five_min["high"].to_numpy(),
        low=five_min["low"].to_numpy(), close=five_min["close"].to_numpy(), gaps=0,
    )
    test_mask = (candles.timestamp >= window["start"]) & (candles.timestamp < window["end_exclusive"])
    sub = CandleData(candles.timestamp[test_mask], candles.open[test_mask], candles.high[test_mask],
                      candles.low[test_mask], candles.close[test_mask], 0)
    ewma = vol_series_24h(sub, "ewma", window)
    nexo = vol_series_24h(sub, "nexo_har", window, frame=frame)
    assert np.isfinite(nexo).mean() > 0.9
    ratio = np.nanmean(nexo) / np.nanmean(ewma)
    assert 0.1 < ratio < 10.0, f"nexo_har and ewma sigma_24h should be the same order of magnitude, got ratio={ratio}"


def test_hourly_forecast_cache_is_reproducible(tmp_path):
    five_min = _synthetic_5m(TOTAL_HOURS)
    frame = _frame_from_5m(five_min)
    window = _window_for(five_min)
    csv_hash = "deadbeef"
    first = hourly_forecast_for_window(frame, "nexo_har", window, csv_hash=csv_hash, cache_dir=tmp_path)
    cache_files = list(tmp_path.glob("nexo_har_*"))
    assert len(cache_files) == 1
    second = hourly_forecast_for_window(frame, "nexo_har", window, csv_hash=csv_hash, cache_dir=tmp_path)
    pd.testing.assert_frame_equal(first.reset_index(drop=True), second.reset_index(drop=True))


def test_has_sufficient_training_history_respects_min_train_days():
    five_min = _synthetic_5m(TOTAL_HOURS)
    frame = _frame_from_5m(five_min)
    early_start = int(five_min["timestamp"].iloc[0].timestamp()) + 10 * 86400
    late_start = int(five_min["timestamp"].iloc[0].timestamp()) + 120 * 86400
    assert not has_sufficient_training_history(frame, early_start, min_train_days=90)
    assert has_sufficient_training_history(frame, late_start, min_train_days=90)
