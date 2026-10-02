from decimal import Decimal
import numpy as np
from types import SimpleNamespace

import pytest

from grid.policy import evaluate_target, validate_params
from grid.sim.runner import FILTERS
from grid.sim.data import CandleData
from grid.sim.runner import run_simulation
from grid.sim.exchange import SimExchange
from scripts import grid_ctl
from tests.test_grid_ctl_open import _args, _context
from grid.sim import target_study
from tests.test_grid_engine import create, make_engine
from grid.monitor import GridMonitor


def cell(idx, qty, basis, fee=0):
    return {"level_idx": idx, "held_qty": qty, "entry_cost": basis,
            "entry_fee_usdt": fee}


def test_validate_target_params_and_basis():
    assert validate_params({"target_pct": 10, "target_usdt": 18}, 5)["target_basis"] == "cash"
    assert validate_params({"target_basis": "EQUITY"}, 5)["target_basis"] == "equity"
    for values in ({"target_pct": 0}, {"target_pct": 100.01}, {"target_usdt": 0},
                   {"target_basis": "market"}):
        with pytest.raises(ValueError):
            validate_params(values, 5)


def test_evaluate_target_sells_profitable_cells_best_first_only_until_cash_target():
    cells = [cell(2, 1, 105), cell(0, 1, 102), cell(1, 1, 101)]
    result = evaluate_target({"target_usdt": 1}, 100, 99, cells, 110, FILTERS, 0)
    assert result["reached"] is True
    assert [row["level_idx"] for row in result["sell_cells"]] == [1]
    assert result["projected_cash"] == 209
    assert {row["level_idx"] for row in result["repo_cells"]} == {0, 2}


def test_evaluate_target_includes_sweepable_dust_in_cash_projection():
    result = evaluate_target({"target_usdt": 5}, 100, 100, [], 100, FILTERS, 0,
                             dust={"dust_qty": "0.1"})
    assert result["reached"]
    assert result["projected_cash"] == 110


def test_simple_target_records_cash_after_sweeping_dust(monkeypatch):
    prices = np.full(3, 100.0)
    candles = CandleData(np.arange(3, dtype=np.int64) * 300, prices, prices, prices, prices, 0)
    original = SimExchange.process

    def add_recorded_dust(exchange, i, low, high, close, cells):
        fills = original(exchange, i, low, high, close, cells)
        if i == 1 and exchange.dust_qty == 0:
            exchange.dust_qty = Decimal("0.1")
            exchange.base += Decimal("0.1")
        return fills

    monkeypatch.setattr(SimExchange, "process", add_recorded_dust)
    result = run_simulation(candles, strategy="simple", n=10, capital=1000,
        width_pct=9, resync_candles=1, params={"target_usdt": 5})
    target = next(event for event in result["events"] if event["type"] == "TARGET_REACHED")
    assert target["details"]["cash_total"] == pytest.approx(target["details"]["projected_cash"])
    assert result["metrics"]["dust_swept_usdt"] > 0


def test_loss_cells_and_unmarketable_lots_never_count_as_sellable():
    tiny = cell(0, .01, .1)
    loss = cell(1, 1, 120)
    result = evaluate_target({"target_usdt": 1}, 100, 99, [tiny, loss], 110, FILTERS, .1)
    assert not result["reached"]
    assert result["sell_cells"] == []
    assert {row["level_idx"] for row in result["repo_cells"]} == {0, 1}


def test_equity_basis_counts_remaining_inventory_losses_against_target():
    profitable = cell(0, 1, 90)
    losing = cell(1, 1, 150)
    cash_result = evaluate_target({"target_usdt": 5, "target_basis": "cash"},
                                  100, 0, [profitable, losing], 110, FILTERS, 0,
                                  equity_now=110)
    equity_result = evaluate_target({"target_usdt": 5, "target_basis": "equity"},
                                    100, 0, [profitable, losing], 110, FILTERS, 0,
                                    equity_now=90)
    assert cash_result["reached"] is True
    assert equity_result["reached"] is False


def test_both_target_amounts_trigger_at_the_first_lower_threshold():
    result = evaluate_target({"target_pct": 10, "target_usdt": 5}, 100, 100,
                             [cell(0, 1, 100)], 106, FILTERS, 0)
    assert result["reached"] is True
    assert result["target_usdt"] == 5


def test_simple_target_simulation_runs_the_same_target_policy_and_reports_both_values():
    prices = [100.0, 99.5, 100.2, 100.2]
    timestamps = [i * 300 for i in range(len(prices))]
    candles = CandleData(np.asarray(timestamps), np.asarray(prices), np.asarray([100, 99.8, 100.2, 100.2]),
                         np.asarray([99.5, 99.19, 99.5, 100.1]), np.asarray(prices), 0)
    result = run_simulation(candles, strategy="simple", n=10, capital=100000,
                            width_pct=9, fee_pct=.1, resync_candles=1,
                            params={"target_usdt": .01})
    targets = [row for row in result["events"] if row["type"] == "TARGET_REACHED"]
    assert targets
    assert "cash_total" in targets[0]["details"]
    assert "equity_total_at_close" in targets[0]["details"]
    assert targets[0]["details"]["cash_total"] == pytest.approx(
        targets[0]["details"]["projected_cash"], abs=1e-6)
    assert result["metrics"]["target_reached"] is True
    assert result["metrics"]["equity_final_after_repository_liquidation"] > 0


def test_synthetic_target_reaches_same_result_in_simulator_and_grid_monitor():
    prices = np.asarray([100.0, 112.0, 112.0])
    candles = CandleData(np.arange(3, dtype=np.int64) * 300, prices,
                         np.asarray([100.0, 112.0, 112.0]),
                         np.asarray([100.0, 97.0, 112.0]), prices, 0)
    sim = run_simulation(candles, strategy="smart", n=10, capital=1000,
                         low=90, high=110, fee_pct=.1, fee_asset="USDT",
                         resync_candles=1,
                         params={"target_usdt": .05, "adjust_enabled": False},
                         include_details=True)
    sim_target = next(event for event in sim["events"] if event["type"] == "TARGET_REACHED")
    assert sim_target["details"]["sell_cells"]
    assert sim_target["details"]["repo_cells"] == []
    baseline = run_simulation(candles, strategy="smart", n=10, capital=1000,
        low=90, high=110, fee_pct=.1, fee_asset="USDT", resync_candles=1,
        params={"adjust_enabled": False})
    assert baseline["metrics"]["max_cash"] >= sim_target["details"]["cash_total"]
    assert baseline["metrics"]["max_usdt_balance"] < sim_target["details"]["cash_total"]

    engine, db, exchange = make_engine(fee_rate="0.001", fee_asset="USDT")
    grid = engine.create_grid("XRPUSDT", 90, 110, 10, capital=1000, strategy="smart",
        params={"target_usdt": .05, "adjust_enabled": False})
    buy = next(order for order in exchange.get_open_orders("XRPUSDT")
               if order["client_order_id"] == "g1L4B0")
    exchange.fill(buy["order_id"])
    # FakeExchange models commission in units; set this USDT fee to the same
    # notional-based amount used by SimExchange and the target estimate.
    exchange.orders[buy["order_id"]]["_trades"][0]["commission"] = Decimal("0.098")
    engine.sync_grid(grid["id"])
    open_buys = [order for order in exchange.get_open_orders("XRPUSDT") if order["side"] == "BUY"]
    assert open_buys, "test requires unfilled BUY capital to remain reserved"
    exchange.bid = exchange.ask = exchange.avg = Decimal("112")
    _monitor_target(engine, db, exchange).run_once("SCHEDULED")
    motor_target = db.get_last_event(grid["id"], "TARGET_REACHED")["details"]

    sim_sold = [row["level_idx"] for row in sim_target["details"]["sell_cells"]]
    motor_sold = [row["level_idx"] for row in motor_target["sell_cells"]]
    assert sim_sold == motor_sold == [4]
    assert sim_target["details"]["repo_cells"] == motor_target["repo_cells"] == []
    assert sim_target["details"]["cash_total"] == pytest.approx(motor_target["cash_total"])
    assert sim["details"]["balances"]["XRP"] == "0.0"
    assert db.get_grid(grid["id"])["status"] == "CLOSED"


def test_simulator_diagnostics_include_cash_cells_dust_and_cycles():
    prices = np.asarray([100.0, 112.0, 112.0])
    candles = CandleData(np.arange(3, dtype=np.int64) * 300, prices,
                         np.asarray([100.0, 112.0, 112.0]),
                         np.asarray([100.0, 97.0, 112.0]), prices, 0)
    result = run_simulation(candles, strategy="simple", n=10, capital=1000,
        low=90, high=110, fee_pct=.1, resync_candles=1,
        params={"target_usdt": .05})
    metrics = result["metrics"]
    assert metrics["max_cash"] >= metrics["target_cash_total"]
    assert metrics["max_cash"] >= metrics["max_usdt_balance"]
    assert metrics["max_equity_incl_dust"] >= metrics["max_equity_cells"]
    assert metrics["dust_qty_final"] == pytest.approx(.099)
    assert 0 <= metrics["pct_time_in_range"] <= 100
    assert metrics["cycles"] == metrics["cycles_completed"]
    assert metrics["realized_net"] == metrics["pnl_realized_net_usdt"]


def test_target_market_sale_cancels_cell_sell_limit_before_closing():
    prices = np.asarray([100.0, 98.05, 99.95, 99.95])
    candles = CandleData(np.arange(4, dtype=np.int64) * 300, prices,
                         np.asarray([100.0, 98.05, 99.95, 99.95]),
                         np.asarray([100.0, 97.0, 99.9, 99.9]), prices, 0)
    result = run_simulation(candles, strategy="simple", n=10, capital=1000,
        low=90, high=110, fee_pct=.1, fee_asset="USDT", resync_candles=1,
        params={"target_usdt": .05}, include_details=True)
    assert result["metrics"]["target_reached"] is True
    assert not any(event["type"] == "SELL_FILLED" for event in result["events"])
    target = next(event for event in result["events"] if event["type"] == "TARGET_REACHED")
    assert [cell["level_idx"] for cell in target["details"]["sell_cells"]] == [4]
    assert result["details"]["balances"]["XRP"] == "0.0"


def test_open_cli_target_flags_smart_only_and_validated(monkeypatch, capsys):
    monkeypatch.setattr(grid_ctl, "build_context", _context)
    assert grid_ctl.main(_args("--dry-run", "--target-pct", "10", "--target-usdt", "18",
                               "--target-basis", "equity")) == 0
    result = __import__("json").loads(capsys.readouterr().out)
    assert result["params"]["target_pct"] == 10
    assert result["params"]["target_usdt"] == 18
    assert result["params"]["target_basis"] == "equity"
    simple_args = ["open", "--symbol", "XRPUSDT", "--low", "90", "--high", "110", "--n", "5",
                   "--capital", "1000", "--strategy", "simple", "--dry-run", "--target-pct", "5"]
    assert grid_ctl.main(simple_args) == 1
    assert "solo se admiten" in capsys.readouterr().err


def test_no_target_simple_simulation_remains_identical_when_no_target_is_omitted():
    prices = [100.0, 96.0, 103.0, 99.0, 101.0]
    timestamps = [i * 300 for i in range(len(prices))]
    candles = CandleData(np.asarray(timestamps), np.asarray(prices), np.asarray([x + .1 for x in prices]),
                         np.asarray([x - .1 for x in prices]), np.asarray(prices), 0)
    old = run_simulation(candles, strategy="simple", n=10, capital=1000, width_pct=9)
    explicit = run_simulation(candles, strategy="simple", n=10, capital=1000, width_pct=9, params=None)
    assert old["metrics"] == explicit["metrics"]
    assert old["events"] == explicit["events"]


def test_target_study_is_deterministic_with_one_and_two_workers(monkeypatch):
    monkeypatch.setattr(target_study, "TARGET_DURATIONS", (30,))
    monkeypatch.setattr(target_study, "TARGET_PCTS", (3, 5))
    monkeypatch.setattr(target_study, "TARGET_BASES", ("cash",))
    monkeypatch.setattr(target_study, "TARGET_STRATEGIES", ("simple",))
    size = 30 * 24 * 12 + 2
    prices = np.full(size, .5)
    candles = CandleData(np.arange(size, dtype=np.int64) * 300, prices, prices + .0001,
                         prices - .0001, prices, 0)
    serial = target_study.run_target_study(candles, seed=13, workers=1)
    parallel = target_study.run_target_study(candles, seed=13, workers=2)
    assert serial["rows"] == parallel["rows"]
    assert serial["summary"] == parallel["summary"]
    assert {.5, 1, 2}.issubset({row["target_pct"] for row in serial["rows"]})
    assert {"max_cash", "max_equity_cells", "max_equity_incl_dust", "dust_qty_final", "cash_final",
            "pct_time_in_range", "cycles", "realized_net", "dust_cycle_cost_usdt_mean",
            "dust_spacing_fraction_mean", "max_cash_upper_bound_reaches_target"}.issubset(
                serial["rows"][0])


def _engine_target_case(include_repository_cell=False):
    engine, db, exchange = make_engine(fee_rate="0", fee_asset="USDT")
    grid = create(engine, strategy="smart", params={"target_usdt": 1, "adjust_enabled": False})
    to_fill = {"g1L2B0"} | ({"g1L1B0"} if include_repository_cell else set())
    for buy in exchange.get_open_orders("XRPUSDT"):
        if buy["client_order_id"] in to_fill:
            exchange.fill(buy["order_id"])
    engine.sync_grid(grid["id"])
    return engine, db, exchange, grid


def _plan_for_grid(db, grid):
    level = next(row for row in db.get_grid_levels(grid["id"]) if row["level_idx"] == 2)
    qty = level["held_qty"]
    return {"reached": True, "basis": "cash", "cash_now": 800,
            "projected_cash": 1001, "equity_now": 1002, "target_threshold": 1001,
            "sell_cells": [{"level_idx": 2, "qty": qty, "bid": 100, "gain_usdt": 1}],
            "repo_cells": [], "bid_used": 100, "estimated_fees_usdt": 0}


def test_engine_target_close_writes_audit_and_closes_market_filled_cell_once():
    engine, db, exchange, grid = _engine_target_case()
    plan = _plan_for_grid(db, grid)
    result = engine.close_grid_target(grid["id"], plan, 100)
    assert result["status"] == "CLOSED"
    assert result["cash_total"] is not None and result["equity_total_at_close"] is not None
    assert db.get_grid(grid["id"])["params"]["target_close_plan"]["phase"] == "COMPLETE"
    target = db.get_last_event(grid["id"], "TARGET_REACHED")["details"]
    assert target["basis"] == "cash"
    assert "cash_total" in target and "equity_total_at_close" in target
    market_sells = [row for row in exchange.orders.values() if row["type"] == "MARKET" and row["side"] == "SELL"]
    assert len(market_sells) == 1
    assert market_sells[0]["client_order_id"] == "g1L2X0"


def test_engine_target_restart_after_market_fill_does_not_duplicate_order_or_cid():
    engine, db, exchange, grid = _engine_target_case()
    original = engine._market_sell_owned_cell
    def crash_after_fill(*args, **kwargs):
        original(*args, **kwargs)
        raise KeyboardInterrupt("injected process crash after durable fill projection")
    engine._market_sell_owned_cell = crash_after_fill
    with pytest.raises(KeyboardInterrupt):
        engine.close_grid_target(grid["id"], _plan_for_grid(db, grid), 100)
    engine._market_sell_owned_cell = original
    result = engine.close_grid_target(grid["id"], {}, 100)
    assert result["status"] == "CLOSED"
    market_sells = [row for row in exchange.orders.values() if row["type"] == "MARKET" and row["side"] == "SELL"]
    assert len(market_sells) == 1
    assert len({row["client_order_id"] for row in market_sells}) == 1


def test_engine_target_restart_after_repository_close_finalizes_write_ahead_plan():
    engine, db, exchange, grid = _engine_target_case(include_repository_cell=True)
    original = engine.close_grid
    def crash_after_repository(grid_id, mode):
        result = original(grid_id, mode)
        raise KeyboardInterrupt("injected crash after repository transaction")
    engine.close_grid = crash_after_repository
    with pytest.raises(KeyboardInterrupt):
        engine.close_grid_target(grid["id"], _plan_for_grid(db, grid), 100)
    assert db.get_grid(grid["id"])["status"] == "CLOSED"
    repository_rows = [row for repo in db.list_grids_by_status({"HOLDING"})
                       for row in db.get_grid_levels(repo["id"]) if row["held_qty"] > 0]
    assert len(repository_rows) == 1
    engine.close_grid = original
    result = engine.close_grid_target(grid["id"], {}, 100)
    assert result["status"] == "CLOSED"
    assert db.get_grid(grid["id"])["params"]["target_close_plan"]["phase"] == "COMPLETE"


def test_engine_target_restart_after_write_ahead_marker_continues_safely():
    engine, db, exchange, grid = _engine_target_case()
    original = engine._market_context
    crashed = {"done": False}
    def crash_after_mark(*args, **kwargs):
        if not crashed["done"]:
            crashed["done"] = True
            raise KeyboardInterrupt("injected crash after CLOSING marker")
        return original(*args, **kwargs)
    engine._market_context = crash_after_mark
    with pytest.raises(KeyboardInterrupt):
        engine.close_grid_target(grid["id"], _plan_for_grid(db, grid), 100)
    assert db.get_grid(grid["id"])["status"] == "CLOSING"
    engine._market_context = original
    assert engine.close_grid_target(grid["id"], {}, 100)["status"] == "CLOSED"


def test_engine_target_cancel_failure_falls_back_to_sell_repository_and_records_rejection():
    engine, db, exchange, grid = _engine_target_case()
    exchange.fail_on_create = len(exchange.create_calls) + 1
    exchange.create_failure = RuntimeError("simulated MARKET rejection")
    result = engine.close_grid_target(grid["id"], _plan_for_grid(db, grid), 100)
    assert result["status"] == "CLOSED"
    assert result["target_missed_after_fills"] is True
    failure = db.get_last_event(grid["id"], "TARGET_MARKET_SELL_FAILED")
    assert failure is not None
    repos = db.list_grids_by_status({"HOLDING"})
    assert repos and any(row["held_qty"] > 0 for row in db.get_grid_levels(repos[0]["id"]))


def test_target_cancels_buys_before_first_market_sale():
    engine, db, exchange, grid = _engine_target_case()
    assert [row for row in exchange.get_open_orders("XRPUSDT") if row["side"] == "BUY"]
    original = engine._market_sell_owned_cell
    buy_counts = []
    def check_canceled(*args, **kwargs):
        buy_counts.append(sum(row["side"] == "BUY" for row in exchange.get_open_orders("XRPUSDT")))
        return original(*args, **kwargs)
    engine._market_sell_owned_cell = check_canceled
    assert engine.close_grid_target(grid["id"], _plan_for_grid(db, grid), 100)["status"] == "CLOSED"
    assert buy_counts == [0]


def test_fake_engine_and_sim_exchange_match_target_market_fill_and_cash():
    engine, db, exchange, grid = _engine_target_case()
    level = next(row for row in db.get_grid_levels(grid["id"]) if row["level_idx"] == 2)
    buy_order = exchange.get_order("XRPUSDT", client_order_id=level["buy_client_order_id"])
    trades = exchange.get_my_trades("XRPUSDT", buy_order["order_id"])
    cost = sum(float(row["qty"] * row["price"]) for row in trades)
    qty = float(level["held_qty"])
    cash = float(grid["capital_total"]) - cost
    decision = evaluate_target({"target_usdt": 1}, grid["capital_total"], cash,
        [{"level_idx": 2, "held_qty": qty, "entry_cost": cost, "entry_fee_usdt": 0}],
        100, exchange.filters, 0, equity_now=cash + qty * 100)
    assert decision["reached"]
    decision["bid_used"] = 100
    decision["estimated_fees_usdt"] = 0
    engine_result = engine.close_grid_target(grid["id"], decision, 100)
    simulator = SimExchange(cash, fee_pct=0, filters=exchange.filters, fee_asset="USDT")
    simulator.base = Decimal(str(qty))
    sim_cell = {"held_qty": Decimal(str(qty)), "entry_cost": Decimal(str(cost)),
                "entry_fee_usdt": Decimal(0), "pnl": Decimal(0)}
    sim_pnl = simulator.market_sell(sim_cell, 100)
    assert sim_cell["held_qty"] == 0
    assert abs(float(simulator.usdt) - float(engine_result["cash_total"])) < 1e-8
    engine_fills = [row for row in exchange.orders.values()
                    if row["type"] == "MARKET" and row["side"] == "SELL"]
    assert len(engine_fills) == 1
    assert float(engine_fills[0]["executed_qty"]) == qty
    assert sim_pnl > 0


def test_engine_target_repository_receives_unsold_inventory_and_cancels_all_buys():
    engine, db, exchange = make_engine(fee_rate="0", fee_asset="USDT")
    grid = create(engine, strategy="smart", params={"target_usdt": 1, "adjust_enabled": False})
    orders = {row["client_order_id"]: row for row in exchange.get_open_orders("XRPUSDT")}
    exchange.fill(orders["g1L1B0"]["order_id"])
    exchange.fill(orders["g1L2B0"]["order_id"])
    engine.sync_grid(grid["id"])
    plan = _plan_for_grid(db, grid)
    result = engine.close_grid_target(grid["id"], plan, 100)
    assert result["status"] == "CLOSED"
    assert not [row for row in exchange.get_open_orders("XRPUSDT") if row["side"] == "BUY"]
    repository = next(row for row in db.list_grids_by_status({"HOLDING"}) if row["symbol"] == "XRPUSDT")
    held = db.get_grid_levels(repository["id"])
    assert len(held) == 1 and held[0]["held_qty"] > 0
    assert held[0]["sell_price"] > 0


def test_engine_target_restart_after_cancel_boundary_is_idempotent():
    engine, db, exchange, grid = _engine_target_case()
    original = exchange.cancel_order
    crashed = {"done": False}
    def crash_after_cancel(*args, **kwargs):
        value = original(*args, **kwargs)
        if not crashed["done"]:
            crashed["done"] = True
            raise KeyboardInterrupt("injected crash after cancel")
        return value
    exchange.cancel_order = crash_after_cancel
    with pytest.raises(KeyboardInterrupt):
        engine.close_grid_target(grid["id"], _plan_for_grid(db, grid), 100)
    exchange.cancel_order = original
    assert engine.close_grid_target(grid["id"], {}, 100)["status"] == "CLOSED"
    assert not [row for row in exchange.get_open_orders("XRPUSDT") if row["side"] == "BUY"]
    assert len([row for row in exchange.orders.values() if row["type"] == "MARKET" and row["side"] == "SELL"]) == 1


def _monitor_settings():
    return SimpleNamespace(grid_monitor_interval=900, grid_monitor_gap_minutes=20,
                           grid_monitor_enabled=True, grid_policy_enabled=True)


def _monitor_target(engine, db, exchange):
    class Vol:
        def get(self, symbol):
            return SimpleNamespace(sigma_24h=None)
    return GridMonitor(db, exchange, engine, _monitor_settings(), vol_provider=Vol())


def test_monitor_target_precedes_adjust_and_closes_smart_grid_with_both_totals():
    engine, db, exchange = make_engine(fee_rate="0", fee_asset="USDT")
    grid = create(engine, strategy="smart", params={"target_usdt": 1, "adjust_enabled": False})
    buy = next(order for order in exchange.get_open_orders("XRPUSDT")
               if order["client_order_id"] == "g1L2B0")
    exchange.fill(buy["order_id"])
    engine.sync_grid(grid["id"])
    params = dict(db.get_grid(grid["id"])["params"])
    params["target_usdt"] = 1
    params["adjust_enabled"] = False
    db.update_grid(grid["id"], params=db._json(params))
    result = _monitor_target(engine, db, exchange).run_once("SCHEDULED")
    assert result["status"] == "OK"
    assert db.get_grid(grid["id"])["status"] == "CLOSED"
    target = db.get_last_event(grid["id"], "TARGET_REACHED")["details"]
    assert target["basis"] == "cash"
    assert target["cash_total"] is not None and target["equity_total_at_close"] is not None


def test_monitor_skips_target_when_loan_saga_is_pending_and_emits_throttled_event(monkeypatch):
    engine, db, exchange = make_engine(fee_rate="0", fee_asset="USDT")
    grid = create(engine, strategy="smart", params={"target_usdt": 1, "loans_enabled": True,
                                                       "adjust_enabled": False})
    params = dict(db.get_grid(grid["id"])["params"])
    params.update(target_usdt=1, loans_enabled=True, adjust_enabled=False)
    db.update_grid(grid["id"], params=db._json(params))
    monkeypatch.setattr(db, "list_grid_loans", lambda grid_id, statuses=None: [{"status": "PENDING"}])
    monkeypatch.setattr(engine, "process_grid_loans", lambda *args, **kwargs: {})
    monitor = _monitor_target(engine, db, exchange)
    monitor.run_once("SCHEDULED")
    monitor.run_once("SCHEDULED")
    assert db.get_grid(grid["id"])["status"] == "ACTIVE"
    assert db.get_last_event(grid["id"], "TARGET_SKIPPED")["reason"] == "open_or_pending_grid_loan"
    assert len(db.list_grid_events(grid_id=grid["id"], event_type="TARGET_SKIPPED", limit=100)) == 1


def test_risk_close_precedes_and_suppresses_profit_target():
    engine, db, exchange = make_engine(fee_rate="0", fee_asset="USDT")
    grid = create(engine, strategy="smart", params={"target_usdt": 1, "adjust_enabled": False})
    levels = db.get_grid_levels(grid["id"])
    for row in levels:
        db.update_level(grid["id"], row["level_idx"], pnl=-100)
    _monitor_target(engine, db, exchange).run_once("SCHEDULED")
    assert db.get_last_event(grid["id"], "GRID_AUTO_CLOSE") is not None
    assert db.get_last_event(grid["id"], "TARGET_REACHED") is None


def test_paused_grid_can_reach_target_without_volatility():
    engine, db, exchange = make_engine(fee_rate="0", fee_asset="USDT")
    grid = create(engine, strategy="smart", params={"target_usdt": 1, "adjust_enabled": False})
    db.update_grid(grid["id"], status="PAUSED")
    for level in db.get_grid_levels(grid["id"]):
        db.update_level(grid["id"], level["level_idx"], pnl=1)
    _monitor_target(engine, db, exchange).run_once("SCHEDULED")
    assert db.get_grid(grid["id"])["status"] == "CLOSED"
    assert db.get_last_event(grid["id"], "TARGET_REACHED") is not None


def test_grid_ctl_status_exposes_target_cash_equity_and_progress_without_market_calls():
    class StatusDB:
        def list_grids_by_status(self, statuses):
            if "ACTIVE" in statuses:
                return [{"id": 77, "symbol": "XRPUSDT", "status": "ACTIVE", "strategy": "smart",
                         "capital_total": 1001, "params": {"target_pct": 10, "target_basis": "equity"}}]
            return []
        def get_grid_levels(self, grid_id):
            return [{"level_idx": 0, "state": "SELL_OPEN", "held_qty": 10,
                     "entry_price": 100, "pnl": 0, "buy_client_order_id": "g77L0B0"}]
        def list_grid_snapshots(self, grid_id, limit):
            return [{"level_idx": None, "market_mid": 111, "inventory_value_usdt": 1110}]
        def list_grid_events(self, **kwargs):
            return [{"level_idx": 0, "client_order_id": "g77L0B0",
                     "details": {"executed_qty": "10", "fee_usdt": "1"}}]
        def get_last_monitor_run(self):
            return None
    target = grid_ctl._status({"db": StatusDB()})["grids"][0]["target"]
    assert target["basis"] == "equity" and target["pct"] == 10
    assert target["cash_now"] == 0
    assert target["equity_now"] == pytest.approx(1108.89)
    assert target["progress_pct"] == pytest.approx(107.7822)
    assert "cash_total" in target and "equity_total_at_close" in target

