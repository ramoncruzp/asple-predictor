from datetime import datetime, timezone
import json
from types import SimpleNamespace
import time

import numpy as np
from sqlalchemy import select

from database.db_manager import DBManager
from grid.meta_learning import (
    make_learning_label,
    non_overlapping_72h_starts,
    point_in_time_signals,
    replay_alternative_labels,
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
    assert snapshot["signals"]["direction_predictions"][0]["signal"] == "ALCISTA"
    assert "touch_buy_to_sell" in snapshot["signals"]

    changed = _candles(timestamps, lows=np.where(timestamps > at, 1, lows),
        highs=np.where(timestamps > at, 10000, highs), closes=np.where(timestamps > at, 1, 100.0))
    again = point_in_time_signals(at=at, candles=changed, buy_price=99,
        sell_price=101, volatility_forecasts=forecasts, direction_predictions=predictions)
    assert again == snapshot


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
