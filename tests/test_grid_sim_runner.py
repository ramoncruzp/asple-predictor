import numpy as np

from grid.sim.data import CandleData
from grid.sim.runner import run_simulation
from data.exchange_filters import SymbolFilters
from tests.grid_fakes import fake_symbol_info


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


def test_runner_infers_hourly_candles_for_sigma_and_rejects_unknown_spacing():
    import pytest
    rng = np.random.default_rng(206)
    close = 100 * np.exp(np.cumsum(rng.normal(0, .01 / np.sqrt(24), 12000)))
    ts = np.arange(len(close), dtype=np.int64) * 3600 + 1_700_000_100
    candles = CandleData(ts, close.copy(), close * 1.001, close * .999, close.copy(), 0)
    seen = []
    run_simulation(candles, n=4, capital=100, low=50, high=150, trace_callback=seen.append)
    values = [row["sigma_24h"] for row in seen if row["sigma_24h"] is not None]
    assert np.median(values[-100:]) == pytest.approx(.01, abs=.002)

    irregular = CandleData(ts[:5] + np.array([0, 300, 900, 1800, 3600]),
                           close[:5], close[:5] * 1.001, close[:5] * .999, close[:5], 0)
    with pytest.raises(ValueError, match="300-second or 3600-second"):
        run_simulation(irregular, n=4, capital=100, low=50, high=150)


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
    assert abs(result["metrics"]["pnl_total_net_usdt"] - .07) < 1e-8


def test_params_hash_includes_fee_asset_and_injected_filters():
    candles = _candles()
    fake_filters = SymbolFilters.from_symbol_info(fake_symbol_info())
    baseline = run_simulation(candles, n=4, capital=100, low=99, high=101, fee_pct=0)
    base_fee = run_simulation(candles, n=4, capital=100, low=99, high=101,
                              fee_pct=0, fee_asset="XRP")
    fake_filters_run = run_simulation(candles, n=4, capital=100, low=99, high=101,
                                      fee_pct=0, filters=fake_filters)
    assert baseline["params_sha256"] != base_fee["params_sha256"]
    assert baseline["params_sha256"] != fake_filters_run["params_sha256"]
    assert fake_filters_run["filters"]["tick_size"] == "0.01"
