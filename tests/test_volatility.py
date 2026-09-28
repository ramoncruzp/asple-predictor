from __future__ import annotations

import numpy as np
import pandas as pd

from data.splits import chronological_split
from data.binance_client import BinanceClient
from data.volatility import (
    aggregate_intraday_to_hourly,
    build_volatility_frame,
    feature_columns,
    range_variance_estimators,
)
from models.volatility.evaluation import moving_block_bootstrap_difference
from models.volatility.garch import GARCHModel
from models.volatility.gbm import GBMModel
from models.volatility.har import HARModel, HARRangeModel
from models.volatility.harq import HARQModel
from models.volatility.nexo_har import NexoHARModel
from models.volatility.persistence import PersistenceModel


def synthetic_ohlcv(rows: int = 900, seed: int = 20260928) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    returns = rng.standard_t(df=7, size=rows) * 0.0015
    close = 100.0 * np.exp(np.cumsum(returns))
    open_ = np.r_[close[0], close[:-1]]
    high = np.maximum(open_, close) * (1.0 + rng.uniform(0.0001, 0.002, rows))
    low = np.minimum(open_, close) * (1.0 - rng.uniform(0.0001, 0.002, rows))
    return pd.DataFrame({
        "timestamp": pd.date_range("2025-01-01", periods=rows, freq="h", tz="UTC"),
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": rng.uniform(10.0, 100.0, rows),
    })


def synthetic_intraday_ohlcv(hours: int = 210, seed: int = 205):
    rng = np.random.default_rng(seed)
    rows = hours * 12
    returns = rng.normal(0.0, 0.0008, rows)
    close = 100.0 * np.exp(np.cumsum(returns))
    open_ = np.r_[100.0, close[:-1]]
    timestamps = pd.date_range("2025-01-01", periods=rows, freq="5min", tz="UTC")
    candles_5m = pd.DataFrame({
        "timestamp": timestamps,
        "open": open_,
        "high": np.maximum(open_, close) * 1.0003,
        "low": np.minimum(open_, close) * 0.9997,
        "close": close,
        "volume": rng.uniform(10.0, 30.0, rows),
    })
    intraday = aggregate_intraday_to_hourly(candles_5m)
    hourly = intraday[["timestamp", "open", "high", "low", "close", "volume", "close_time"]].copy()
    return candles_5m, hourly, intraday


def test_five_minute_interval_is_supported():
    assert "5m" in BinanceClient.VALID_INTERVALS
    assert BinanceClient.INTERVAL_MINUTES["5m"] == 5


def test_aggregate_intraday_rv_includes_previous_hour_return_and_marks_incomplete():
    timestamps = pd.date_range("2025-01-01", periods=18, freq="5min", tz="UTC")
    close = np.exp(np.arange(1, 19, dtype="float64") * 0.001)
    open_ = np.r_[1.0, close[:-1]]
    candles = pd.DataFrame({
        "timestamp": timestamps, "open": open_, "high": close * 1.001,
        "low": open_ * 0.999, "close": close, "volume": 1.0,
    })

    hourly = aggregate_intraday_to_hourly(candles)
    expected_returns = np.log(close / open_)

    assert hourly.loc[0, "n_bars"] == 12
    assert hourly.loc[0, "complete_hour"]
    assert np.isclose(hourly.loc[0, "rv_intra"], np.square(expected_returns[1:12]).sum())
    assert hourly.loc[1, "n_bars"] == 6
    assert not hourly.loc[1, "complete_hour"]
    assert np.isclose(hourly.loc[1, "rv_intra"], np.square(expected_returns[12:]).sum())


def test_intraday_target_uses_future_hour_variance_without_future_feature_leakage():
    candles_5m, hourly, intraday = synthetic_intraday_ohlcv()
    horizon = 2
    original = build_volatility_frame(hourly, horizon, intraday=intraday)
    changed_5m = candles_5m.copy()
    origin_hour = 180
    future_start = origin_hour * 12 + 12
    changed_5m.loc[future_start:, ["open", "high", "low", "close"]] *= 1.04
    changed_intraday = aggregate_intraday_to_hourly(changed_5m)
    changed_hourly = changed_intraday[["timestamp", "open", "high", "low", "close", "volume", "close_time"]]
    changed = build_volatility_frame(changed_hourly, horizon, intraday=changed_intraday)

    assert original.loc[origin_hour, "target_logvol"] != changed.loc[origin_hour, "target_logvol"]
    for column in feature_columns(horizon):
        assert np.isclose(original.loc[origin_hour, column], changed.loc[origin_hour, column], equal_nan=True)


def test_hourly_build_without_intraday_keeps_hourly_realized_variance_definition():
    candles = synthetic_ohlcv(400)
    frame = build_volatility_frame(candles, 4)
    returns = np.log(candles["close"]).diff()
    expected_rv = returns.pow(2).rolling(24, min_periods=24).mean()
    expected_target = np.log(np.sqrt(returns.pow(2).shift(-1).iloc[::-1].rolling(
        4, min_periods=4
    ).mean().iloc[::-1]) + 1e-8)

    np.testing.assert_allclose(frame["rv_24"], expected_rv, equal_nan=True)
    np.testing.assert_allclose(frame["target_logvol"], expected_target, equal_nan=True)


def test_future_target_changes_with_future_returns_but_features_at_t_do_not():
    frame = synthetic_ohlcv(500)
    horizon = 4
    original = build_volatility_frame(frame, horizon)
    changed_frame = frame.copy()
    t = 240
    changed_frame.loc[t + 1:, "close"] *= 1.03
    changed = build_volatility_frame(changed_frame, horizon)

    assert original.loc[t, "target_logvol"] != changed.loc[t, "target_logvol"]
    for column in feature_columns(horizon):
        assert original.loc[t, column] == changed.loc[t, column]


def test_parkinson_estimator_matches_reference_candle():
    candle = pd.DataFrame({"open": [1.0], "close": [1.0], "high": [1.1], "low": [1.0]})
    estimate = range_variance_estimators(candle).loc[0, "parkinson_bar"]
    expected = np.log(1.1) ** 2 / (4.0 * np.log(2.0))
    assert np.isclose(estimate, expected, rtol=1e-12)


def test_persistence_matches_target_for_constant_realized_volatility():
    returns = np.full(300, 0.002)
    close = 100.0 * np.exp(np.cumsum(returns))
    open_ = np.r_[close[0] / np.exp(returns[0]), close[:-1]]
    frame = pd.DataFrame({
        "timestamp": pd.date_range("2025-01-01", periods=len(returns), freq="h", tz="UTC"),
        "open": open_,
        "high": close * 1.001,
        "low": open_ * 0.999,
        "close": close,
        "volume": 10.0,
    })
    features = build_volatility_frame(frame, 4)
    prediction = PersistenceModel(4).fit(features).predict(features)
    valid = features["target_logvol"].notna() & prediction.notna()
    np.testing.assert_allclose(prediction.loc[valid], features.loc[valid, "target_logvol"], atol=1e-12)


def test_har_recovers_known_ols_coefficients():
    rng = np.random.default_rng(410)
    x = rng.normal(size=(600, 3))
    coefficients = np.array([0.15, -0.35, 0.8])
    intercept = -4.2
    frame = pd.DataFrame(x, columns=HARModel.FEATURE_COLUMNS)
    frame["target_logvol"] = intercept + x @ coefficients

    model = HARModel().fit(frame)

    np.testing.assert_allclose(model.coef_, coefficients, atol=1e-12)
    assert np.isclose(model.intercept_, intercept, atol=1e-12)


def test_validation_calibration_offset_removes_known_log_bias():
    from scripts.vol_research import validation_calibration_offset

    target = pd.Series(np.linspace(-8.0, -4.0, 100))
    predicted = target - 0.3
    offset = validation_calibration_offset(target, predicted)

    assert np.isclose(offset, 0.3)
    np.testing.assert_allclose(predicted + offset, target, atol=1e-12)


def test_harq_recovers_known_level_variance_coefficients():
    rng = np.random.default_rng(891)
    rv_24 = rng.uniform(0.001, 0.02, 800)
    sqrt_rq_24 = rng.uniform(0.01, 0.2, 800)
    rv_1 = rng.uniform(0.001, 0.03, 800)
    rv_168 = rng.uniform(0.001, 0.015, 800)
    b0, b1, b1q, b2, b3 = 0.001, 0.7, 0.15, 0.2, 0.1
    future_var = b0 + (b1 + b1q * sqrt_rq_24) * rv_24 + b2 * rv_1 + b3 * rv_168
    frame = pd.DataFrame({
        "rv_24": rv_24,
        "sqrt_rq_24": sqrt_rq_24,
        "rv_1": rv_1,
        "rv_168": rv_168,
        "future_var_4": future_var,
        "target_logvol": 0.5 * np.log(future_var),
    })

    model = HARQModel(4).fit(frame)

    np.testing.assert_allclose(model.coef_, [b1, b1q, b2, b3], atol=1e-10)
    assert np.isclose(model.intercept_, b0, atol=1e-10)
    assert np.isfinite(model.predict(frame)).all()


def test_har_range_selects_estimator_by_validation_mse_only():
    frame = build_volatility_frame(synthetic_ohlcv(900), 4)
    train = frame.iloc[:550]
    val = frame.iloc[550:750]
    model = HARRangeModel(4).fit(train, val)

    assert model.selected_estimator == min(model.selection_mse_val, key=model.selection_mse_val.get)
    assert len(model.predict(val)) == len(val)


def test_gbm_stops_using_chronological_validation(monkeypatch):
    import models.volatility.gbm as gbm_module

    fit_iterations = []

    class FakeHistGradientBoostingRegressor:
        def __init__(self, **kwargs):
            self.max_iter = kwargs["max_iter"]

        def set_params(self, **kwargs):
            self.max_iter = kwargs["max_iter"]
            return self

        def fit(self, x, y):
            fit_iterations.append(self.max_iter)
            return self

        def predict(self, x):
            return np.full(len(x), float(self.max_iter))

    monkeypatch.setattr(gbm_module, "HistGradientBoostingRegressor", FakeHistGradientBoostingRegressor)
    frame = build_volatility_frame(synthetic_ohlcv(700), 4)
    model = GBMModel(4, patience=2).fit(frame.iloc[:500], frame.iloc[500:650])

    assert model.best_iteration_ == 1
    assert fit_iterations == [1, 2, 3]


def test_moving_block_bootstrap_is_deterministic_and_detects_constant_difference():
    same = np.linspace(0.0, 2.0, 500)
    identical = moving_block_bootstrap_difference(same, same, seed=42)
    assert identical["ci95_low"] <= 0.0 <= identical["ci95_high"]
    assert identical == moving_block_bootstrap_difference(same, same, seed=42)

    positive = moving_block_bootstrap_difference(same + 0.5, same, seed=42)
    assert positive["ci95_low"] > 0.0
    assert positive["mean_difference"] == 0.5


def test_garch_fixed_parameters_predict_after_warmup_without_nan():
    candles = synthetic_ohlcv(900, seed=127)
    frame = build_volatility_frame(candles, 4)
    valid = np.isfinite(frame[feature_columns(4) + ["target_logvol"]]).all(axis=1)
    data = frame.loc[valid].reset_index(drop=True)
    train_slice, _val_slice, _test_slice = chronological_split(len(data), embargo=4)
    model = GARCHModel(4).fit(data.iloc[train_slice])

    forecast = model.predict(data)

    assert len(forecast) == len(data)
    assert forecast.iloc[200:].notna().all()
    assert np.isfinite(forecast.iloc[200:]).all()


def test_garch_recovers_one_step_true_log_volatility_on_simulated_garch():
    rng = np.random.default_rng(55821)
    rows = 5000
    omega, alpha, beta = 2e-5, 0.08, 0.90
    variance = np.empty(rows, dtype="float64")
    returns = np.empty(rows, dtype="float64")
    variance[0] = omega / (1.0 - alpha - beta)
    innovations = rng.standard_t(8, size=rows) / np.sqrt(8.0 / 6.0)
    for index in range(rows):
        returns[index] = np.sqrt(variance[index]) * innovations[index]
        if index + 1 < rows:
            variance[index + 1] = omega + alpha * returns[index] ** 2 + beta * variance[index]
    close = 100.0 * np.exp(np.cumsum(returns))
    open_ = np.r_[100.0, close[:-1]]
    candles = pd.DataFrame({
        "timestamp": pd.date_range("2024-01-01", periods=rows, freq="h", tz="UTC"),
        "open": open_,
        "high": np.maximum(open_, close) * 1.001,
        "low": np.minimum(open_, close) * 0.999,
        "close": close,
        "volume": 100.0,
    })
    frame = build_volatility_frame(candles, 1)
    model = GARCHModel(1).fit(frame.iloc[:3500])
    predicted = model.predict(frame).to_numpy()
    true_next_logvol = 0.5 * np.log(variance[1:])
    predicted_eval = predicted[500:-1]
    target_eval = true_next_logvol[500:]
    finite = np.isfinite(predicted_eval) & np.isfinite(target_eval)
    bias = float(np.mean(predicted_eval[finite] - target_eval[finite]))
    r2 = 1.0 - float(np.square(predicted_eval[finite] - target_eval[finite]).sum()) / float(
        np.square(target_eval[finite] - target_eval[finite].mean()).sum()
    )

    assert r2 > 0.2
    assert abs(bias) < 0.1


def test_nexo_har_seasonal_factors_use_train_only_and_reseason_predictions():
    rng = np.random.default_rng(777)
    rows = 24 * 160
    timestamps = pd.date_range("2025-01-01", periods=rows, freq="h", tz="UTC")
    hour = timestamps.hour.to_numpy()
    hourly_sigma = np.where(hour == 12, 0.03, 0.003)
    returns = rng.normal(0.0, hourly_sigma)
    close = 100.0 * np.exp(np.cumsum(returns))
    open_ = np.r_[100.0, close[:-1]]
    candles = pd.DataFrame({
        "timestamp": timestamps,
        "open": open_,
        "high": np.maximum(open_, close) * np.exp(hourly_sigma * 0.5),
        "low": np.minimum(open_, close) * np.exp(-hourly_sigma * 0.5),
        "close": close,
        "volume": rng.uniform(50.0, 150.0, rows),
    })
    frame = build_volatility_frame(candles, 1)
    train_end = 24 * 100
    train = frame.iloc[:train_end]
    test = frame.iloc[train_end:].copy()
    model = NexoHARModel(1).fit(train, test)
    factors = model.seasonal_factors_.copy()

    changed = candles.copy()
    changed.loc[train_end:, ["high", "low"]] *= 1.5
    changed_frame = build_volatility_frame(changed, 1)
    repeated_fit = NexoHARModel(1).fit(changed_frame.iloc[:train_end], changed_frame.iloc[train_end:])
    assert repeated_fit.seasonal_factors_ == factors

    forecast = model.predict(frame)
    evaluation = frame.iloc[train_end:].copy()
    evaluation_hour = pd.to_datetime(evaluation["timestamp"], utc=True).dt.hour
    forecast_eval = forecast.loc[evaluation.index]
    high_hour_origin = (evaluation_hour == 11) & evaluation["timestamp"].dt.dayofweek.lt(5)
    low_hour_origin = (evaluation_hour == 3) & evaluation["timestamp"].dt.dayofweek.lt(5)
    assert forecast_eval.loc[high_hour_origin].mean() > forecast_eval.loc[low_hour_origin].mean()

    t = train_end + 100
    original_prediction = forecast.loc[t]
    changed.loc[t + 1:, "close"] *= 1.02
    changed_frame = build_volatility_frame(changed, 1)
    changed_prediction = model.predict(changed_frame).loc[t]
    assert np.isclose(original_prediction, changed_prediction, atol=1e-12)
