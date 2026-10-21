import numpy as np
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from grid.sim.data import CandleData
from grid.sim.runner import FILTERS, run_simulation
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
    cycle_fills = [event for event in first["events"] if event["type"] == "SELL_FILLED"]
    assert metrics["cycle_realized_net_usdt"] == sum(
        float(event["cycle_pnl_net_usdt"]) for event in cycle_fills)


def test_smart_runner_respaces_from_sigma24_with_fixed_range():
    candles = _candles()
    sigmas = np.array([.02] + [.04] * (len(candles.close) - 1))
    result = run_simulation(candles, strategy="smart", n=10, capital=1000,
        low=90, high=110, fee_pct=0, sigma_values=sigmas,
        params={"adjust_enabled": False, "pause_enter_prob": None,
                "pause_exit_prob": None, "stop_loss_pct": 50},
        close_wait_enabled=True, respacing_enabled=True, include_details=True)
    respaced = [event for event in result["events"] if event["type"] == "ADJUST"
                and event.get("details", {}).get("decision", {}).get("source") == "RESPACE"]
    assert respaced
    assert respaced[0]["details"]["decision"]["sigma_now"] == .04
    assert respaced[0]["details"]["decision"]["range_low"] == 90
    assert respaced[0]["details"]["decision"]["range_high"] == 110


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


def test_simple_runner_compounds_completed_cycle_profit():
    close = np.array([100.] * 8)
    low = np.array([99.9, 99.4, 99.9, 99.9, 99.9, 99.9, 99.9, 99.9])
    high = np.array([100.1] * 8)
    ts = np.arange(len(close), dtype=np.int64) * 300 + 1_700_000_100
    candles = CandleData(ts, close, high, low, close, 0)
    result = run_simulation(candles, n=4, capital=100, low=99, high=101,
                            fee_pct=0, resync_candles=3,
                            params={"compound_enabled": True})
    applied = [event for event in result["events"] if event["type"] == "COMPOUND_APPLIED"]
    assert len(applied) == 1
    assert float(applied[0]["amount"]) > 0


def test_smart_runner_creates_reserve_loan_and_honors_repayment_margin():
    close = np.full(17, 100.0)
    low = np.full(17, 99.99)
    high = np.full(17, 100.01)
    low[12] = low[14] = 94.9
    high[13] = high[15] = 100.1
    ts = np.arange(len(close), dtype=np.int64) * 3600 + 1_700_000_100
    candles = CandleData(ts, close, high, low, close, 0)
    filters = replace(FILTERS, step_size=Decimal("0.001"), min_qty=Decimal("0.001"),
                      min_notional=Decimal("5"))
    result = run_simulation(candles, strategy="smart", n=4, capital=30, low=90, high=110,
        fee_pct=0, resync_candles=1, filters=filters, sigma_values=np.full(len(close), .001),
        params={"pause_enter_prob": None, "pause_exit_prob": None, "adjust_enabled": False,
                "stop_loss_pct": 50, "loans_enabled": True, "loan_idle_h": 12,
                "loan_min_margin": 2.0, "reserve_pct": 20}, include_details=True)
    created = [event for event in result["events"] if event["type"] == "LOAN_CREATED"]
    assert len(created) == 1
    assert created[0]["details"]["borrower_idx"] == 1
    assert created[0]["details"]["amount"] == "3.00000000"
    assert not any(event["type"] == "LOAN_REPAID" for event in result["events"])
    borrower = result["details"]["cells"][1]
    assert borrower["capital_loan"] == "3.00000000"


def test_smart_runner_loan_matches_monitor_planner_cells_and_amount():
    from tests.test_grid_engine import make_engine

    engine, db, _exchange = make_engine(fee_rate="0", fee_asset="USDT")
    params = {"loans_enabled": True, "reserve_pct": 20, "loan_idle_h": 12,
              "pause_enter_prob": None, "pause_exit_prob": None,
              "adjust_enabled": False, "stop_loss_pct": 50}
    grid = engine.create_grid("XRPUSDT", 90, 110, 5, strategy="smart",
                              capital=1000, params=params)
    raw_created = db.get_grid(grid["id"])["created_at"]
    created = (datetime.fromisoformat(raw_created.replace("Z", "+00:00"))
               if isinstance(raw_created, str) else raw_created)
    created = created.astimezone(timezone.utc)
    loan_time = created + timedelta(hours=13)
    db.update_level(grid["id"], 2, state="IDLE", cycles_completed=1)
    db.add_grid_event(run_id=None, source="MONITOR", event_type="SELL_FILLED",
        grid_id=grid["id"], level_idx=2, price=102,
        ts=loan_time, details={"cycles_completed": 1})
    monitor_result = engine.process_grid_loans(grid["id"], now=loan_time)
    monitor_loan = monitor_result["loan"]

    close = np.full(17, 100.0)
    low = np.full(17, 99.99)
    high = np.full(17, 100.01)
    low[12], high[13] = 97.9, 102.1
    stamps = int(created.timestamp()) + np.arange(17, dtype=np.int64) * 3600
    result = run_simulation(CandleData(stamps, close, high, low, close, 0),
        strategy="smart", n=5, capital=1000, low=90, high=110, fee_pct=0,
        resync_candles=1, sigma_values=np.full(len(close), .001), params=params)
    runner_loan = next(event for event in result["events"] if event["type"] == "LOAN_CREATED")
    assert monitor_loan is not None
    assert Decimal(str(monitor_loan["amount"])) == Decimal(runner_loan["details"]["amount"])
    assert monitor_loan["borrower_idx"] == runner_loan["details"]["borrower_idx"] == 2
    assert monitor_loan["lender_idx"] == runner_loan["details"]["lender_idx"]


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
