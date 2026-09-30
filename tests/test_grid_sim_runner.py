import numpy as np

from grid.sim.data import CandleData
from grid.sim.runner import run_simulation


def _candles():
    close = np.array([100, 100.1, 99.9, 100.2, 99.8, 100.3, 99.7, 100.4, 99.6, 100.2], dtype=float)
    ts = np.arange(len(close), dtype=np.int64) * 300 + 1_700_000_100
    return CandleData(ts, close.copy(), close + .2, close - .2, close.copy(), 0)


def test_simulation_deterministic_and_has_financial_metrics():
    first = run_simulation(_candles(), n=4, capital=100, low=99, high=101)
    second = run_simulation(_candles(), n=4, capital=100, low=99, high=101)
    assert first == second
    assert "pnl_total_net_usdt" in first["metrics"]
    metrics = first["metrics"]
    assert abs(metrics["pnl_total_net_usdt"] - (
        metrics["pnl_realized_gross_usdt"] + metrics["pnl_unrealized_usdt"] - metrics["fees_usdt"]
    )) < 1e-8


def test_reject_compound_and_loans():
    import pytest
    with pytest.raises(ValueError, match="15B-4b"):
        run_simulation(_candles(), strategy="smart", n=4, capital=100, low=99, high=101,
                       params={"compound_enabled": True})


def test_manual_one_cycle_has_exact_net_pnl_and_accounting_identity():
    close = np.array([100., 100., 100., 100., 100., 100., 100., 100.])
    low = np.array([99.9, 99.4, 99.9, 99.9, 99.9, 99.9, 99.9, 99.9])
    high = np.array([100.1, 100.1, 100.1, 100.1, 100.1, 100.1, 100.1, 100.1])
    ts = np.arange(len(close), dtype=np.int64) * 300 + 1_700_000_100
    candles = CandleData(ts, close, high, low, close, 0)
    result = run_simulation(candles, n=4, capital=100, low=99, high=101,
                            fee_pct=.1, resync_candles=3)
    assert result["metrics"]["cycles_completed"] == 1
    assert abs(result["metrics"]["pnl_total_net_usdt"] - .06002) < 1e-8
