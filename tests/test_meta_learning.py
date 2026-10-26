from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace
import time

import numpy as np
import pytest
from sqlalchemy import select

from database.db_manager import DBManager
from grid.meta_learning import (
    causal_touch_buy_to_sell,
    causal_replay_probability,
    effective_sample_rows,
    fit_signal_weights,
    extract_learning_features,
    holm_adjust,
    make_learning_label,
    non_overlapping_72h_starts,
    point_in_time_signals,
    replay_alternative_labels,
    temporal_learning_split,
    stable_template,
)
from grid.meta_operator import MetaOperator
from grid.sim.data import CandleData


def _candles(timestamps, *, lows=None, highs=None, closes=None):
    closes = np.asarray(closes if closes is not None else np.full(len(timestamps), 100.0))
    return SimpleNamespace(timestamp=np.asarray(timestamps, dtype=np.int64),
        open=closes.copy(), high=np.asarray(highs if highs is not None else closes + .01),
        low=np.asarray(lows if lows is not None else closes - .01), close=closes)


def test_72h_labels_use_proportional_target_and_keep_profile_horizon_separate():
    label = make_learning_label(horizon="72h", net_advance_usdt=1.6,
        target_usdt=10, profile_days=20, capital_usdt=100)
    assert label["required_progress_usdt"] == 1.5
    assert label["reached"] is True
    assert label["net_advance_pct"] == 1.6

    profile = make_learning_label(horizon="20d", net_advance_usdt=10,
        target_usdt=10, profile_days=20, capital_usdt=100, reached_target=True)
    assert profile["required_progress_usdt"] == 10
    assert profile["reached"] is True


def test_non_overlapping_samples_align_to_72h_utc_blocks():
    step = 72 * 3600
    times = np.arange(0, step * 5 + 1, 300, dtype=np.int64)
    starts = non_overlapping_72h_starts(times)
    assert starts == [0, step, step * 2, step * 3, step * 4]
    assert all(right - left == step for left, right in zip(starts, starts[1:]))


def test_point_in_time_reconstruction_ignores_future_candles_forecasts_and_predictions():
    timestamps = np.arange(0, 601 * 300, 300, dtype=np.int64)
    lows, highs = np.full(len(timestamps), 100.0), np.full(len(timestamps), 100.2)
    lows[90] = 98.0
    highs[95] = 102.0
    candles = _candles(timestamps, lows=lows, highs=highs)
    at = int(timestamps[500])
    before = datetime.fromtimestamp(at - 300, timezone.utc).isoformat()
    after = datetime.fromtimestamp(at + 300, timezone.utc).isoformat()
    forecasts = [
        {"made_at": before, "forecast_at": before, "model_name": "past", "pred_logvol_cal": -3.0},
        {"made_at": after, "forecast_at": after, "model_name": "future", "pred_logvol_cal": 4.0},
    ]
    predictions = [
        {"predicted_at": before, "model_name": "past", "signal": "ALCISTA", "probability_up": .7},
        {"predicted_at": after, "model_name": "future", "signal": "BAJISTA", "probability_up": .1},
    ]
    snapshot = point_in_time_signals(at=at, candles=candles, buy_price=99,
        sell_price=101, volatility_forecasts=forecasts, direction_predictions=predictions)
    assert snapshot["signals"]["volatility_forecasts"][0]["model"] == "past"
    forecast = snapshot["signals"]["volatility_forecasts"][0]
    assert forecast["sigma_per_hour"] == pytest.approx(np.exp(-3.0))
    assert forecast["sigma_24h"] == pytest.approx(np.exp(-3.0) * np.sqrt(24))
    assert forecast["sigma"] == pytest.approx(forecast["sigma_24h"])
    assert snapshot["signals"]["direction_predictions"][0]["signal"] == "ALCISTA"
    assert "touch_buy_to_sell" in snapshot["signals"]

    changed = _candles(timestamps, lows=np.where(timestamps > at, 1, lows),
        highs=np.where(timestamps > at, 10000, highs), closes=np.where(timestamps > at, 1, 100.0))
    again = point_in_time_signals(at=at, candles=changed, buy_price=99,
        sell_price=101, volatility_forecasts=forecasts, direction_predictions=predictions)
    assert again == snapshot


def test_touch_learning_uses_same_causal_rate_and_normalization_live_and_historical():
    timestamps = np.arange(0, 40 * 24 * 12 * 300, 300, dtype=np.int64)
    lows = np.full(len(timestamps), 100.0)
    highs = np.full(len(timestamps), 100.2)
    lows[10::100] = 98.0
    highs[20::100] = 102.0
    candles = _candles(timestamps, lows=lows, highs=highs)
    cut = int(timestamps[-10])
    live = causal_touch_buy_to_sell(at=cut, candles=candles, buy_price=99, sell_price=101)
    snapshot = point_in_time_signals(at=cut, candles=candles, buy_price=99, sell_price=101)
    assert live["sell_rate"] == snapshot["signals"]["touch_buy_to_sell"]
    historical_features, historical_present = extract_learning_features({"point_in_time_by_symbol": {
        "XRPUSDT": snapshot}, "point_in_time_weights": {"XRPUSDT": 100}})
    live_features, live_present = extract_learning_features({"signals_used": [{
        "name": "touch_buy_to_sell", "normalized": 2 * live["sell_rate"] - 1}]})
    assert historical_features["touch_buy_to_sell"] == pytest.approx(live_features["touch_buy_to_sell"])
    assert "touch_buy_to_sell" in historical_present & live_present
    changed = _candles(timestamps, lows=np.where(timestamps > cut, 1, lows),
        highs=np.where(timestamps > cut, 10000, highs))
    assert causal_touch_buy_to_sell(at=cut, candles=changed, buy_price=99,
        sell_price=101)["sell_rate"] == live["sell_rate"]


def test_synthetic_forward_replay_labels_known_bot_sale_at_72h():
    step = 300
    timestamps = np.arange(0, 72 * 3600 + step, step, dtype=np.int64)
    lows = np.full(len(timestamps), 100.0)
    highs = np.full(len(timestamps), 100.2)
    closes = np.full(len(timestamps), 100.0)
    lows[1] = 98.0
    highs[2] = 102.0
    candles = _candles(timestamps, lows=lows, highs=highs, closes=closes)
    result = replay_alternative_labels({"XRPUSDT": candles}, start_ts=0, parameters={
        "profile": "RAPIDO", "profile_days": 20, "capital_usdt": 100,
        "target_usdt": 10, "max_loss_pct": 10,
        "pieces": [{"symbol": "XRPUSDT", "piece_type": "individual_bot_grid",
            "strategy": "simple", "capital_usdt": 100, "n_levels": 1,
            "floor": 99, "ceiling": 101, "mid_at_plan": 100}],
    })
    assert result["72h"]["status"] == "labeled"
    assert result["72h"]["net_advance_usdt"] > 1.5
    assert result["72h"]["reached"] is True
    assert result["20d"]["status"] == "immature"


def test_daily_label_job_persists_72h_result_and_step_timings_without_exchange_calls(tmp_path):
    db = DBManager(f"sqlite:///{tmp_path / 'learning-job.sqlite'}")
    settings = SimpleNamespace(meta_mode_enabled=True)
    operator = MetaOperator(SimpleNamespace(monitor=None), db, None, None, settings)
    now_s = int(time.time() // 300 * 300)
    start_s = now_s - 72 * 3600
    timestamps = np.arange(start_s, now_s + 300, 300, dtype=np.int64)
    lows, highs = np.full(len(timestamps), 100.0), np.full(len(timestamps), 100.2)
    closes = np.full(len(timestamps), 100.0)
    lows[1], highs[2] = 98.0, 102.0
    candles = CandleData(timestamp=timestamps, open=closes.copy(), high=highs, low=lows,
                         close=closes.copy(), gaps=0)
    operator._learning_candles = lambda _symbol: candles
    operator._learning_history_rows = lambda _symbol: ([], [])
    params = {"profile": "RAPIDO", "profile_days": 20, "capital_usdt": 100,
        "target_usdt": 10, "max_loss_pct": 10,
        "pieces": [{"symbol": "XRPUSDT", "piece_type": "individual_bot_grid",
            "strategy": "simple", "capital_usdt": 100, "n_levels": 1,
            "floor": 99, "ceiling": 101, "mid_at_plan": 100}]}
    with db.engine.begin() as conn:
        conn.execute(db.meta_learning_samples.insert().values(sample_key="job-sample",
            meta_id=None, decision_id="job-decision", alternative_id="job-alt",
            symbol="XRPUSDT", profile="RAPIDO",
            sample_at=datetime.fromtimestamp(start_s, timezone.utc),
            source_kind="historical_reconstructed", base_probability=.5,
            parameters_json=json.dumps(params), signals_json="{}", provenance_json="{}",
            created_at=datetime.now(timezone.utc)))

    result = operator.run_learning_daily()
    assert result["status"] == "completed"
    assert result["labels_72h"] == 1
    assert result["ops_lock"] is False
    assert "label_simulation_s" in result["timings_s"]
    with db.engine.connect() as conn:
        stored = conn.execute(select(db.meta_learning_labels)).mappings().one()
        run = conn.execute(select(db.meta_learning_runs)).mappings().one()
    assert stored["horizon"] == "72h"
    assert json.loads(stored["label_json"])["reached"] is True
    assert run["status"] == "completed"
    with db.engine.connect() as conn:
        sample = conn.execute(select(db.meta_learning_samples)).mappings().one()
        versions = conn.execute(select(db.meta_learning_weight_versions)).mappings().all()
    assert sample["base_method_version"] == "rolling_7d_v2"
    assert sample["recalculate_base"] is False
    assert {row["source_kind"] for row in versions} == {"live", "historical_reconstructed"}
    assert all(row["status"] == "insufficient" for row in versions)


def test_causal_replay_uses_seven_day_stride_and_only_windows_before_cut(monkeypatch):
    import grid.meta_learning as learning
    step = 300
    candles = _candles(np.arange(0, 121 * 86400 + step, step, dtype=np.int64))
    at = 100 * 86400
    seen = []
    def fake_replay(_market, *, start_ts, parameters, include_horizons):
        seen.append(start_ts)
        return {"20d": {"status": "labeled", "reached": start_ts % (14 * 86400) == 0}}
    monkeypatch.setattr(learning, "replay_alternative_labels", fake_replay)
    result = causal_replay_probability({"XRPUSDT": candles}, at=at,
        parameters={"profile_days": 20, "pieces": [{"symbol": "XRPUSDT"}]})
    assert seen and all(b - a == 7 * 86400 for a, b in zip(seen, seen[1:]))
    assert all(start + 20 * 86400 < at for start in seen)
    assert result["step_days"] == 7
    assert result["window_count"] == len(seen)
    assert result["method_version"] == "rolling_7d_v2"


def test_replay_probability_smoothing_handles_zero_and_one_windows(monkeypatch):
    import grid.meta_learning as learning
    candles = _candles(np.arange(0, 31 * 86400 + 300, 300, dtype=np.int64))
    monkeypatch.setattr(learning, "replay_alternative_labels", lambda *_a, **_k:
        {"20d": {"status": "labeled", "reached": False}})
    result = causal_replay_probability({"XRPUSDT": candles}, at=21 * 86400,
        parameters={"profile_days": 20})
    assert result["window_count"] == 1
    assert result["raw_probability"] == 0
    assert result["smoothed_probability"] == pytest.approx(1 / 3)
    assert result["training_exclusion_reason"] == "sin base suficiente"
    empty = causal_replay_probability({"XRPUSDT": candles}, at=20 * 86400,
        parameters={"profile_days": 20})
    assert empty["raw_probability"] is None
    assert empty["smoothed_probability"] is None
    assert empty["independent_windows"] == 0
    no_logit = fit_signal_weights([{"sample_at": datetime.now(timezone.utc), "symbol": "XRPUSDT",
        "base_probability": 0.0, "base_window_count": 1, "label": {"reached": False}}])
    assert no_logit["status"] == "insufficient"


def test_learning_template_uses_relative_shape_and_capital_fraction():
    first = {"profile": "RAPIDO", "profile_days": 20, "capital_usdt": 100,
        "pieces": [{"symbol": "XRPUSDT", "piece_type": "grid", "strategy": "simple",
            "n_levels": 5, "floor": 90, "ceiling": 110, "mid_at_plan": 100,
            "capital_usdt": 50}]}
    second = {**first, "capital_usdt": 200,
        "pieces": [{**first["pieces"][0], "floor": 180, "ceiling": 220,
            "mid_at_plan": 200, "capital_usdt": 100}]}
    first_id, first_shape = stable_template(first, meta_capital_usdt=100)
    second_id, second_shape = stable_template(second, meta_capital_usdt=200)
    assert first_id == second_id
    assert first_shape == second_shape
    assert "floor_mid_q" in first_shape["pieces"][0]
    assert "mid_at_plan" not in json.dumps(first_shape)
    assert "mid_at_plan" not in json.dumps(first_shape)


def test_replay_window_cache_is_shared_without_changing_result(monkeypatch):
    import grid.meta_learning as learning
    step = 300
    candles = _candles(np.arange(0, 160 * 86400 + step, step, dtype=np.int64))
    calls = []
    def fake_replay(_market, *, start_ts, parameters, include_horizons):
        calls.append(start_ts)
        return {"20d": {"status": "labeled", "reached": start_ts % (14 * 86400) == 0}}
    monkeypatch.setattr(learning, "replay_alternative_labels", fake_replay)
    params = {"profile_days": 20, "pieces": [{"symbol": "XRPUSDT"}]}
    cache = {}
    first = causal_replay_probability({"XRPUSDT": candles}, at=120 * 86400,
        parameters=params, fixed_week_anchor=True, window_cache=cache, template_key="same")
    first_calls = len(calls)
    second = causal_replay_probability({"XRPUSDT": candles}, at=127 * 86400,
        parameters=params, fixed_week_anchor=True, window_cache=cache, template_key="same")
    assert len(calls) - first_calls < first_calls
    without_cache = causal_replay_probability({"XRPUSDT": candles}, at=127 * 86400,
        parameters=params, fixed_week_anchor=True, window_cache={}, template_key="same")
    assert {key: value for key, value in second.items() if key not in {"cached_windows", "simulated_windows"}} == {
        key: value for key, value in without_cache.items() if key not in {"cached_windows", "simulated_windows"}}
    assert second["cached_windows"] > 0
    assert second["simulated_windows"] < without_cache["simulated_windows"]


def test_historical_backfill_skips_existing_cut_and_respects_budget(monkeypatch):
    import hashlib
    import grid.meta_learning as learning
    step = 300
    timestamps = np.arange(0, 200 * 86400 + step, step, dtype=np.int64)
    candles = _candles(timestamps)
    template = {"template_id": "template-a", "parameters": {"profile": "RAPIDO",
        "profile_days": 20, "capital_usdt": 100, "pieces": [{"symbol": "XRPUSDT",
            "piece_type": "individual_bot_grid", "strategy": "simple", "n_levels": 1,
            "capital_usdt": 100, "floor": 99, "ceiling": 101, "mid_at_plan": 100}]}}
    starts = learning.non_overlapping_72h_starts(timestamps, start=20 * 86400, end=190 * 86400)
    first_key = hashlib.sha256(("historical:template-a:" + str(starts[0])).encode()).hexdigest()
    calls = []
    monkeypatch.setattr(learning, "causal_replay_probability", lambda *_a, **_k:
        calls.append(1) or {"raw_probability": .5, "smoothed_probability": .5,
            "window_count": 3, "independent_windows": 3, "training_exclusion_reason": None})
    skipped = learning.reconstruct_historical_samples([template], candles_by_symbol={"XRPUSDT": candles},
        source_rows_by_symbol={}, now=190 * 86400, lookback_days=180,
        existing_cuts={first_key}, max_new_cuts=1)
    assert len(skipped["samples"]) == 1
    assert skipped["samples"][0]["sample_at"] != starts[0]
    assert skipped["pending_templates"] == ["template-a"]
    assert len(calls) == 1
    next_run = learning.reconstruct_historical_samples([template],
        candles_by_symbol={"XRPUSDT": candles}, source_rows_by_symbol={},
        now=190 * 86400, lookback_days=180,
        existing_cuts={first_key, skipped["samples"][0]["stable_sample_key"]}, max_new_cuts=1)
    assert len(next_run["samples"]) == 1
    assert next_run["samples"][0]["sample_at"] not in {starts[0], skipped["samples"][0]["sample_at"]}


def test_effective_samples_and_temporal_split_prevent_overlapping_or_mixed_dates():
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    rows = [{"id": i, "symbol": "XRPUSDT",
        "sample_at": start + timedelta(days=offset)}
        for i, offset in enumerate((0, 1, 3, 6, 9, 12, 15))]
    effective = effective_sample_rows(rows, horizon_days=3)
    assert [r["sample_at"] for r in effective] == [rows[0]["sample_at"], rows[2]["sample_at"],
        rows[3]["sample_at"], rows[4]["sample_at"], rows[5]["sample_at"], rows[6]["sample_at"]]
    train, validation = temporal_learning_split(rows, train_fraction=.6)
    assert train and validation
    assert max(str(r["sample_at"])[:10] for r in train) < min(
        str(r["sample_at"])[:10] for r in validation)


def test_holm_correction_is_monotonic_and_controls_familywise_threshold():
    adjusted = holm_adjust({"a": .01, "b": .03, "c": .04})
    assert adjusted == {"a": pytest.approx(.03), "b": pytest.approx(.06),
        "c": pytest.approx(.06)}


def test_touch_is_sixth_holm_family_member_with_zero_prior():
    from grid.meta_learning import DEFAULT_SIGNAL_WEIGHTS, LEARNING_SIGNALS
    assert len(LEARNING_SIGNALS) == 6
    assert DEFAULT_SIGNAL_WEIGHTS["touch_buy_to_sell"] == 0
    adjusted = holm_adjust({name: .01 for name in LEARNING_SIGNALS})
    assert len(adjusted) == 6 and adjusted["touch_buy_to_sell"] == pytest.approx(.06)
    assert holm_adjust({"volatility": .01}, family_size=6)["volatility"] == pytest.approx(.06)


def test_historical_features_rebuild_only_available_normalized_signals():
    values, present = extract_learning_features({"signals_version": 2, "point_in_time_by_symbol": {
        "XRPUSDT": {"signals": {"realized_volatility_24h": .01,
            "volatility_forecasts": [{"sigma": .02}],
            "direction_predictions": [{"model": "model_a_down", "interval": "1h",
                "probability_up": .8}]}}}})
    assert values["volatility"] == pytest.approx(np.log(2))
    assert values["direction"] == -1.0
    assert present == {"volatility", "direction"}


def test_absent_signal_stays_at_prior_and_reported_without_evidence():
    base = datetime(2025, 1, 1, tzinfo=timezone.utc)
    rows = []
    for i in range(45):
        rows.append({"id": i, "symbol": f"COIN{i % 5}",
            "sample_at": base + timedelta(days=i * 3), "base_probability": .5,
            "base_probability_smoothed": .5, "base_window_count": 5,
            "signals": {"signals_used": [{"name": "volatility", "normalized": .8}]},
            "label": {"reached": bool(i % 2)}})
    validation = [{**row, "label": {"reached": bool(row["id"] % 2)}} for row in rows[-10:]]
    result = fit_signal_weights(rows, validation_rows=validation, min_effective=30,
        max_delta=.05)
    assert result["signals"]["scanner"]["weight"] == .25
    assert result["signals"]["scanner"]["reason"] == "sin evidencia suficiente"
    assert result["signals"]["scanner"]["effective_samples"] == 0
    assert result["signals"]["touch_buy_to_sell"]["weight"] == 0
    assert result["signals"]["touch_buy_to_sell"]["effective_samples"] == 0


def test_insufficient_base_still_reports_all_six_signals():
    result = fit_signal_weights([{"sample_at": datetime(2025, 1, 1, tzinfo=timezone.utc),
        "symbol": "XRPUSDT", "base_probability": None, "signals": {}}])
    assert len(result["signals"]) == 6
    assert result["signals"]["touch_buy_to_sell"]["weight"] == 0
    assert result["signals"]["volatility"]["effective_samples"] == 0
    assert result["weights"]["volatility"] == .25


def test_weight_update_is_significant_holm_adjusted_and_limited_to_005():
    base = datetime(2024, 1, 1, tzinfo=timezone.utc)
    training, validation = [], []
    identifier = 0
    for day in range(180):
        for coin in range(10):
            signal = 1.0 if (day + coin) % 2 else -1.0
            row = {"id": identifier, "symbol": f"COIN{coin}",
                "sample_at": base + timedelta(days=day * 3),
            "base_probability": .5, "base_probability_smoothed": .5,
            "base_window_count": 5, "base_independent_windows": 5,
                "signals": {"signals_used": [{"name": "volatility", "normalized": signal}]},
                "label": {"reached": signal > 0}}
            identifier += 1
            (training if day < 126 else validation).append(row)
    result = fit_signal_weights(training, validation_rows=validation,
        min_effective=30, max_delta=.05)
    assert result["improves"] is True
    assert result["signals"]["volatility"]["p_value_holm"] <= .05
    assert result["weights"]["volatility"] == pytest.approx(.30)


def test_weight_that_fails_later_validation_is_not_applied():
    base = datetime(2024, 1, 1, tzinfo=timezone.utc)
    training, validation = [], []
    identifier = 0
    for day in range(180):
        for coin in range(10):
            signal = 1.0 if (day + coin) % 2 else -1.0
            row = {"id": identifier, "symbol": f"COIN{coin}",
                "sample_at": base + timedelta(days=day * 3),
                "base_probability": .5, "base_probability_smoothed": .5,
                "base_window_count": 5, "base_independent_windows": 5,
                "signals": {"signals_used": [{"name": "volatility", "normalized": signal}]},
                "label": {"reached": signal > 0}}
            identifier += 1
            if day >= 126:
                row["label"] = {"reached": signal < 0}
                validation.append(row)
            else:
                training.append(row)
    result = fit_signal_weights(training, validation_rows=validation,
        min_effective=30, max_delta=.05)
    assert result["improves"] is False
    assert result["weights"]["volatility"] == .25
    assert result["signals"]["volatility"]["reason"] == "validación no mejora a solo p_replay"


def test_validation_calibration_persists_model_and_replay_deciles():
    from grid.meta_operator import validation_calibration
    rows = [{"base_probability_smoothed": .1 + index * .04,
        "signals": {}, "label": {"reached": index >= 10}} for index in range(20)]
    result = validation_calibration(rows, {}, logit_cap=1)
    assert len(result["model"]) == len(result["replay_only"]) == 10
    assert sum(row["samples"] for row in result["model"]) == 20
    assert all("predicted_mean" in row and "success_rate" in row for row in result["replay_only"])
