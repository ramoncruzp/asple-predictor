import numpy as np

from grid.policy import DEFAULT_SMART_PARAMS, evaluate_grid
from grid.sim.data import CandleData
from grid.sim.runner import run_simulation


def test_simple_and_smart_have_explicit_deterministic_strategies():
    close = np.array([100.] * 12)
    ts = np.arange(12, dtype=np.int64) * 300 + 1_700_000_100
    candles = CandleData(ts, close, close + 1, close - 1, close, 0)
    simple = run_simulation(candles, n=4, capital=1000, low=95, high=105, strategy="simple")
    smart = run_simulation(candles, n=4, capital=1000, low=95, high=105, strategy="smart",
                           params={"close_out_of_range_pct": 1})
    expected = evaluate_grid("ACTIVE", {**DEFAULT_SMART_PARAMS, "close_out_of_range_pct": 1},
                             [{"level_idx": i, "state": "IDLE", "held_qty": 0, "entry_price": None,
                               "bought_at": None, "pnl": 0} for i in range(4)],
                             100, float(smart["low"]), float(smart["high"]), 1000, 0, None,
                             __import__("datetime").datetime.fromtimestamp(ts[0], __import__("datetime").timezone.utc))
    assert not any(event["type"] in {"PAUSE", "RESUME", "ADJUST", "CLOSE_REPOSITORY"}
                   for event in simple["events"])
    assert smart["strategy"] == "smart"
    assert expected.action == "NONE"


def test_simulator_respects_close_as_repository_without_market_liquidation():
    close = np.array([100., 100., 110., 110., 110., 110., 110.])
    ts = np.arange(len(close), dtype=np.int64) * 300 + 1_700_000_100
    candles = CandleData(ts, close, close + .1, close - .1, close, 0)
    result = run_simulation(candles, strategy="smart", n=4, capital=1000, low=95, high=105,
                            fee_pct=0, params={"close_out_of_range_pct": 1,
                                              "adjust_enabled": False})
    assert any(event["type"] == "CLOSE_REPOSITORY" for event in result["events"])
    assert not any(event["type"] == "STOP_LOSS" for event in result["events"])


def test_stop_loss_sells_at_monitor_close_and_records_fee():
    close = np.array([100., 100., 92., 92., 92.])
    low = np.array([99.9, 97., 91.9, 91.9, 91.9])
    high = np.array([100.1, 100.1, 92.1, 92.1, 92.1])
    ts = np.arange(len(close), dtype=np.int64) * 300 + 1_700_000_100
    candles = CandleData(ts, close, high, low, close, 0)
    result = run_simulation(candles, strategy="smart", n=4, capital=1000, low=95, high=105,
                            params={"adjust_enabled": False}, fee_pct=.1)
    stops = [event for event in result["events"] if event["type"] == "STOP_LOSS"]
    assert stops and result["metrics"]["fees_usdt"] > 0
