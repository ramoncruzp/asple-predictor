import pandas as pd
import numpy as np
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from grid.policy import direction_gate
from scripts.train_models import monthly_down_splits
from grid.sim.data import CandleData
from grid.sim.runner import run_simulation
from grid.monitor import GridMonitor
from config.settings import APP_FLAG_DEFINITIONS, DIRECTION_FILTER_THRESHOLD, Settings


def test_direction_gate_blocks_only_at_or_above_threshold():
    assert direction_gate(0.60, 0.60)
    assert not direction_gate(0.599, 0.60)
    assert not direction_gate(None, 0.60)


def test_direction_filter_flag_is_off_by_default_at_documented_threshold():
    assert Settings(_env_file=None).smart_direction_filter_enabled is False
    assert APP_FLAG_DEFINITIONS["smart_direction_filter_enabled"]["default"] is False
    assert DIRECTION_FILTER_THRESHOLD == .60


def test_month_model_never_sees_targets_ending_inside_its_test_month():
    timestamps = pd.date_range("2025-01-01", periods=24 * 40, freq="h", tz="UTC")
    prepared = pd.DataFrame({
        "timestamp": timestamps,
        "label_end": timestamps + pd.Timedelta(hours=4),
        "target": [i % 2 for i in range(len(timestamps))],
    })
    start, train, test = next(monthly_down_splits(prepared))
    assert test.timestamp.dt.to_period("M").nunique() == 1
    assert train.label_end.max() < start


def test_runner_uses_previous_candle_signal_and_cancels_open_buys():
    close = np.full(12, 100.0)
    low = np.full(12, 100.0)
    low[3] = 97.0
    timestamps = np.arange(12, dtype=np.int64) * 300 + 1_700_000_100
    candles = CandleData(timestamps, close, close + .1, low, close, 0)
    p_down = np.zeros(12)
    p_down[2] = .9
    result = run_simulation(candles, strategy="smart", n=8, capital=1000, low=90, high=110,
        fee_pct=0, direction_signal=p_down, direction_threshold=.6,
        params={"pause_enter_prob": None, "pause_exit_prob": None,
                "adjust_enabled": False, "stop_loss_pct": 50})
    canceled = [e for e in result["events"] if e["type"] == "BUY_CANCELED"
                and e.get("reason") == "DIRECTION_GATE"]
    assert canceled
    assert all(e["ts"] >= int(timestamps[3]) for e in canceled)


def test_runner_does_not_use_current_candle_signal():
    close = np.full(9, 100.0)
    low = np.full(9, 100.0)
    low[3] = 97.0
    timestamps = np.arange(9, dtype=np.int64) * 300 + 1_700_000_100
    candles = CandleData(timestamps, close, close + .1, low, close, 0)
    p_down = np.zeros(9)
    p_down[3] = .9
    result = run_simulation(candles, strategy="smart", n=8, capital=1000, low=90, high=110,
        fee_pct=0, direction_signal=p_down, direction_threshold=.6,
        params={"pause_enter_prob": None, "pause_exit_prob": None,
                "adjust_enabled": False, "stop_loss_pct": 50})
    canceled_at_current = [e for e in result["events"] if e["type"] == "BUY_CANCELED"
                           and e.get("reason") == "DIRECTION_GATE" and e["ts"] == int(timestamps[3])]
    assert not canceled_at_current


class _ShadowDB:
    def __init__(self, predicted_at):
        self.predicted_at = predicted_at
        self.events = []

    def get_latest_prediction(self, *_args):
        return {"probability_up": .9, "predicted_at": self.predicted_at}

    def get_last_event(self, *_args):
        return None

    def add_grid_event(self, **event):
        self.events.append(event)
        return event

    def update_level(self, *_args, **_kwargs):
        return {}


class _CancelExchange:
    def __init__(self):
        self.canceled = []

    def cancel_order(self, symbol, order_id):
        self.canceled.append((symbol, order_id))
        return {"status": "CANCELED", "executed_qty": 0}


def test_shadow_records_signal_without_touching_orders_and_stale_is_not_fresh():
    now = datetime(2026, 10, 21, 12, tzinfo=timezone.utc)
    db = _ShadowDB(now - timedelta(hours=3))
    monitor = GridMonitor(db, object(), object(), SimpleNamespace(smart_direction_filter_enabled=False))
    p_down, fresh = monitor._direction_shadow(
        {"id": 7, "symbol": "XRPUSDT", "strategy": "smart"}, now, 100.0,
        [{"state": "BUY_OPEN"}, {"state": "SELL_OPEN"}])
    assert p_down == .9 and not fresh
    assert db.events[0]["event_type"] == "DIRECTION_SHADOW"
    assert db.events[0]["details"]["would_block"] is False
    assert db.events[0]["details"]["buys_open"] == 1


def test_direction_filter_cancels_only_buys_and_leaves_sells_alone():
    db, exchange = _ShadowDB(None), _CancelExchange()
    monitor = GridMonitor(db, exchange, object(), SimpleNamespace())
    monitor._cancel_direction_buys({"id": 7, "symbol": "XRPUSDT"}, [
        {"state": "BUY_OPEN", "level_idx": 1, "order_id": 11},
        {"state": "SELL_OPEN", "level_idx": 2, "order_id": 12}], 100.0)
    assert exchange.canceled == [("XRPUSDT", 11)]

