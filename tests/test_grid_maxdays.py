from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import numpy as np
import pytest

from grid.policy import evaluate_max_days, validate_params
from grid.policy import PolicyDecision
import grid.monitor as monitor_module
from grid.sim.runner import run_simulation
from grid.sim.exchange import SimExchange
from tests.test_grid_engine import make_engine
from tests.test_grid_target import _monitor_target


def test_max_days_validation_and_wall_clock_boundary_including_pause():
    created = datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert not evaluate_max_days({}, created, created + timedelta(days=500))["expired"]
    assert not evaluate_max_days({"max_days": 2}, created, created + timedelta(days=1, hours=23))["expired"]
    result = evaluate_max_days({"max_days": 2}, created, created + timedelta(days=2))
    assert result == {"expired": True, "age_days": 2.0, "max_days": 2.0}
    # The helper uses created_at to now; no pause subtraction is accepted.
    paused_at = created + timedelta(hours=12)
    resumed_at = created + timedelta(days=1, hours=12)
    assert evaluate_max_days({"max_days": 1, "paused_days": 1}, created, resumed_at)["expired"]
    assert paused_at < resumed_at


@pytest.mark.parametrize("value", [0, -1, "nan"])
def test_max_days_must_be_finite_and_positive(value):
    with pytest.raises(ValueError):
        validate_params({"max_days": value}, 4)


def _expiring_grid(strategy="simple", *, params=None, paused=False):
    engine, db, exchange = make_engine(fee_asset="USDT")
    values = {"max_days": 1}
    if params:
        values.update(params)
    grid = engine.create_grid("XRPUSDT", 90, 110, 5, capital=1000,
        strategy=strategy, params=values)
    created = datetime.now(timezone.utc) - timedelta(days=2)
    db.update_grid(grid["id"], created_at=created.replace(tzinfo=None))
    if paused:
        db.update_grid(grid["id"], status="PAUSED")
    monitor = _monitor_target(engine, db, exchange)
    monitor.clock = lambda: created + timedelta(days=2)
    return engine, db, exchange, grid, monitor, created


def test_max_days_age_is_identical_for_naive_and_aware_created_at():
    naive = datetime(2026, 3, 1, 12)
    aware = naive.replace(tzinfo=timezone.utc)
    now = aware + timedelta(days=3, hours=4)
    assert evaluate_max_days({"max_days": 3}, naive, now) == evaluate_max_days(
        {"max_days": 3}, aware, now)


def test_closed_grid_does_not_emit_max_days_again_after_restart():
    engine, db, exchange, grid, monitor, _ = _expiring_grid()
    monitor.run_once("SCHEDULED")
    assert db.get_grid(grid["id"])["status"] == "CLOSED"
    assert len(db.list_grid_events(grid_id=grid["id"], event_type="MAX_DAYS_REACHED")) == 1
    assert not db.list_grid_events(grid_id=grid["id"], event_type="TARGET_REACHED")
    assert db.get_grid(grid["id"])["status"] == "CLOSED"
    assert not [event for event in db.list_grid_events(grid_id=grid["id"], event_type="POLICY_ACTION_FAILED")
                if (event.get("details") or {}).get("action") == "TARGET_CLOSE_FINALIZE"]
    restarted = _monitor_target(engine, db, exchange)
    restarted.run_once("SCHEDULED")
    assert len(db.list_grid_events(grid_id=grid["id"], event_type="MAX_DAYS_REACHED")) == 1


def test_close_grid_target_rejects_empty_plan_without_database_changes():
    engine, db, exchange, grid, monitor, _ = _expiring_grid()
    before_grid = db.get_grid(grid["id"])
    before_events = db.list_grid_events(grid_id=grid["id"])
    with pytest.raises(ValueError, match="requires a target_close_plan"):
        engine.close_grid_target(grid["id"], {})
    assert db.get_grid(grid["id"]) == before_grid
    assert db.list_grid_events(grid_id=grid["id"]) == before_events


def test_closed_max_days_start_plan_emits_only_max_days_event():
    engine, db, exchange, grid, monitor, _ = _expiring_grid()
    params = dict(db.get_grid(grid["id"]).get("params") or {})
    params["max_days_close_plan"] = {"phase": "STARTED", "age_days": 2, "max_days": 1}
    db.update_grid(grid["id"], status="CLOSED", params=db._json(params))
    close_target_calls = []
    real_close_target = engine.close_grid_target
    def track_real_close_target(*args, **kwargs):
        close_target_calls.append(args)
        return real_close_target(*args, **kwargs)
    engine.close_grid_target = track_real_close_target
    monitor.run_once("SCHEDULED")
    assert close_target_calls == []
    assert db.get_grid(grid["id"])["status"] == "CLOSED"
    assert len(db.list_grid_events(grid_id=grid["id"], event_type="MAX_DAYS_REACHED")) == 1
    assert not db.list_grid_events(grid_id=grid["id"], event_type="TARGET_REACHED")
    assert not [event for event in db.list_grid_events(grid_id=grid["id"], event_type="POLICY_ACTION_FAILED")
                if (event.get("details") or {}).get("action") == "TARGET_CLOSE_FINALIZE"]
    assert (db.get_grid(grid["id"]).get("params") or {}).get("max_days_close_plan", {}).get("phase") == "EVENT_EMITTED"


def test_holding_repository_is_not_reprocessed_as_an_expiring_grid():
    engine, db, exchange, grid, monitor, created = _grid_with_inventory()
    monitor.run_once("SCHEDULED")
    repositories = [row for row in db.list_grids_by_status({"HOLDING"})
                    if row["symbol"] == "XRPUSDT"]
    assert len(repositories) == 1
    restarted_engine, restarted = _restart_monitor(engine, db, exchange, created)
    restarted.run_once("SCHEDULED")
    assert len(db.list_grid_events(grid_id=grid["id"], event_type="MAX_DAYS_REACHED")) == 1
    assert db.get_grid(repositories[0]["id"])["status"] == "HOLDING"


def test_target_precedes_expired_max_days_on_same_monitor_pass(monkeypatch):
    engine, db, exchange, grid, monitor, _ = _expiring_grid("smart", params={
        "target_usdt": 1, "adjust_enabled": False})
    monkeypatch.setattr(monitor_module, "evaluate_grid",
        lambda *args, **kwargs: PolicyDecision("NONE", (), {}))
    monkeypatch.setattr(monitor_module, "adjust_decision",
        lambda *args, **kwargs: PolicyDecision("NONE", (), {}))
    monkeypatch.setattr(monitor_module, "evaluate_target", lambda *args, **kwargs: {
        "reached": True, "basis": "cash", "cash_now": 1000, "projected_cash": 1001,
        "equity_now": 1000, "sell_cells": [], "repo_cells": [], "reason": "target_reached",
        "target_usdt": 1, "target_threshold": 1001})

    def close_target(grid_id, target, price):
        db.update_grid(grid_id, status="CLOSED")
        return {"status": "CLOSED"}

    monkeypatch.setattr(engine, "close_grid_target", close_target)
    monitor.run_once("SCHEDULED")
    assert db.get_grid(grid["id"])["status"] == "CLOSED"
    assert not db.list_grid_events(grid_id=grid["id"], event_type="MAX_DAYS_REACHED")
    assert not db.list_grid_events(grid_id=grid["id"], event_type="GRID_AUTO_CLOSE")


def test_policy_close_precedes_expired_max_days(monkeypatch):
    engine, db, exchange, grid, monitor, _ = _expiring_grid("smart", params={"adjust_enabled": False})
    monkeypatch.setattr(monitor_module, "evaluate_grid", lambda *args, **kwargs:
        PolicyDecision("CLOSE_REPOSITORY", ("max_loss_pct",), {}))
    monkeypatch.setattr(monitor_module, "adjust_decision", lambda *args, **kwargs:
        PolicyDecision("NONE", (), {}))
    monitor.run_once("SCHEDULED")
    assert db.get_grid(grid["id"])["status"] == "CLOSED"
    assert not db.list_grid_events(grid_id=grid["id"], event_type="MAX_DAYS_REACHED")
    assert db.list_grid_events(grid_id=grid["id"], event_type="GRID_AUTO_CLOSE")


@pytest.mark.parametrize(("policy_action", "paused"), [("PAUSE", False), ("RESUME", True)])
def test_max_days_precedes_pause_resume_and_adjust(monkeypatch, policy_action, paused):
    engine, db, exchange, grid, monitor, _ = _expiring_grid("smart",
        params={"adjust_enabled": True}, paused=paused)
    monkeypatch.setattr(monitor_module, "evaluate_grid", lambda *args, **kwargs:
        PolicyDecision(policy_action, ("synthetic",), {}))
    monkeypatch.setattr(monitor_module, "adjust_decision", lambda *args, **kwargs:
        PolicyDecision("ADJUST", (), {"range_low": 91, "range_high": 109, "n_levels": 5}))
    preview_calls = []
    monkeypatch.setattr(engine, "preview_adjust", lambda *args, **kwargs:
        preview_calls.append(True) or {"ok": True})
    monkeypatch.setattr(engine, "sync_paused", lambda grid_id: {})
    monitor.run_once("SCHEDULED")
    assert db.get_grid(grid["id"])["status"] == "CLOSED"
    assert db.list_grid_events(grid_id=grid["id"], event_type="MAX_DAYS_REACHED")
    assert not preview_calls
    assert not db.list_grid_events(grid_id=grid["id"], event_type="GRID_PAUSED")
    assert not db.list_grid_events(grid_id=grid["id"], event_type="GRID_RESUMED")


def _grid_with_inventory():
    engine, db, exchange, grid, monitor, created = _expiring_grid()
    level = db.get_grid_levels(grid["id"])[2]
    buy = exchange.get_order("XRPUSDT", order_id=level["order_id"])
    exchange.fill(buy["order_id"])
    engine.sync_grid(grid["id"])
    assert any(row["side"] == "BUY" for row in exchange.get_open_orders("XRPUSDT"))
    db.update_grid(grid["id"], dust_qty="10.05")
    exchange.get_my_trades = lambda symbol, order_id: [{
        "commission": Decimal("1"), "commission_asset": "USDT", "price": Decimal("100")
    }]
    return engine, db, exchange, grid, monitor, created


def _restart_monitor(engine, db, exchange, created):
    restarted_engine = type(engine)(db, exchange, engine.settings)
    monitor = _monitor_target(restarted_engine, db, exchange)
    monitor.clock = lambda: created + timedelta(days=2)
    return restarted_engine, monitor


def _assert_single_repository_close(db, exchange, grid_id):
    events = db.list_grid_events(grid_id=grid_id, event_type="MAX_DAYS_REACHED")
    assert len(events) == 1
    repositories = [row for row in db.list_grids_by_status({"HOLDING"})
                    if row["symbol"] == "XRPUSDT"]
    assert len(repositories) == 1
    assert len(db.get_grid_levels(repositories[0]["id"])) == 1
    sells = [row for row in exchange.create_calls if row[1] == "SELL"]
    dust_sells = [row for row in sells if str(row[-1]).startswith("gS")]
    assert len(dust_sells) == 1
    assert not [row for row in sells if "X" in str(row[-1])]
    cids = [str(row[-1]) for row in exchange.create_calls if row[-1] is not None]
    assert len(cids) == len(set(cids))


def test_started_close_plan_resumes_after_restart_even_if_clock_moves_back(monkeypatch):
    engine, db, exchange, grid, monitor, created = _grid_with_inventory()
    original_close = engine.close_grid
    monkeypatch.setattr(engine, "close_grid", lambda *args, **kwargs:
        (_ for _ in ()).throw(RuntimeError("crash after STARTED plan")))
    monitor.run_once("SCHEDULED")
    assert (db.get_grid(grid["id"])["params"] or {}).get("max_days_close_plan", {}).get("phase") == "STARTED"
    restarted_engine, restarted = _restart_monitor(engine, db, exchange, created)
    restarted.clock = lambda: created + timedelta(hours=12)
    monkeypatch.setattr(restarted_engine, "close_grid", original_close)
    restarted.run_once("SCHEDULED")
    _assert_single_repository_close(db, exchange, grid["id"])


def test_restart_after_buy_cancellation_finishes_repository_close_without_repeating_actions():
    engine, db, exchange, grid, monitor, created = _grid_with_inventory()
    original_cancel = exchange.cancel_order
    injected = {"done": False}

    def cancel_then_crash(symbol, order_id):
        result = original_cancel(symbol, order_id)
        if not injected["done"]:
            injected["done"] = True
            raise RuntimeError("crash after buy cancel")
        return result

    exchange.cancel_order = cancel_then_crash
    monitor.run_once("SCHEDULED")
    assert db.get_grid(grid["id"])["status"] == "CLOSING"
    exchange.cancel_order = original_cancel
    restarted_engine, restarted = _restart_monitor(engine, db, exchange, created)
    restarted.run_once("SCHEDULED")
    _assert_single_repository_close(db, exchange, grid["id"])


def test_restart_after_repository_move_before_sweep_sweeps_once(monkeypatch):
    engine, db, exchange, grid, monitor, created = _grid_with_inventory()
    original_sweep = engine.sweep_grid_dust
    monkeypatch.setattr(engine, "sweep_grid_dust", lambda *args, **kwargs:
        (_ for _ in ()).throw(RuntimeError("crash after repository move")))
    monitor.run_once("SCHEDULED")
    assert db.get_grid(grid["id"])["status"] == "CLOSING"
    assert len([row for row in db.list_grids_by_status({"HOLDING"})
                if row["symbol"] == "XRPUSDT"]) == 1
    restarted_engine, restarted = _restart_monitor(engine, db, exchange, created)
    monkeypatch.setattr(restarted_engine, "sweep_grid_dust", original_sweep)
    restarted.run_once("SCHEDULED")
    _assert_single_repository_close(db, exchange, grid["id"])
    assert Decimal(db.get_grid(grid["id"])["dust_qty"]) == Decimal("0.05")


def test_restart_after_sweep_before_event_recovers_one_max_days_event(monkeypatch):
    engine, db, exchange, grid, monitor, created = _grid_with_inventory()
    original_emit = monitor._emit
    injected = {"done": False}

    def fail_before_max_event(event):
        if event.get("event_type") == "MAX_DAYS_REACHED" and not injected["done"]:
            injected["done"] = True
            raise RuntimeError("crash after sweep before max-days event")
        return original_emit(event)

    monitor._emit = fail_before_max_event
    monitor.run_once("SCHEDULED")
    assert db.get_grid(grid["id"])["status"] == "CLOSED"
    assert Decimal(db.get_grid(grid["id"])["dust_qty"]) == Decimal("0.05")
    _, restarted = _restart_monitor(engine, db, exchange, created)
    restarted.run_once("SCHEDULED")
    _assert_single_repository_close(db, exchange, grid["id"])
    restarted = _monitor_target(engine, db, exchange)
    restarted.run_once("SCHEDULED")
    assert len(db.list_grid_events(grid_id=grid["id"], event_type="MAX_DAYS_REACHED")) == 1


def test_simple_simulation_accepts_max_days_and_repositories_without_market_sell():
    start = 1_700_000_000
    candles = SimpleNamespace(
        timestamp=np.asarray([start, start + 86400, start + 172800], dtype=np.int64),
        low=np.asarray([100.0, 90.0, 91.0]),
        high=np.asarray([100.0, 95.0, 96.0]),
        close=np.asarray([100.0, 92.0, 93.0]),
        gaps=0,
    )
    result = run_simulation(candles, strategy="simple", n=4, capital=100,
        low=90, high=110, params={"max_days": 1}, sigma_values=np.zeros(3))
    reached = [event for event in result["events"] if event["type"] == "MAX_DAYS_REACHED"]
    assert reached
    assert reached[0]["details"]["age_days"] >= 1
    assert reached[0]["details"]["repository_cells"]
    assert not any(event["type"] == "SELL_FILLED" and event["ts"] >= reached[0]["ts"]
                   for event in result["events"])


def test_real_monitor_expires_grid_through_repository_close_without_market_sale():
    engine, db, exchange = make_engine(fee_asset="USDT")
    grid = engine.create_grid("XRPUSDT", 90, 110, 5, capital=1000, strategy="smart",
                              params={"max_days": 1, "adjust_enabled": False})
    db.update_grid(grid["id"], created_at=datetime.now(timezone.utc).replace(tzinfo=None)-timedelta(days=2))
    _monitor_target(engine, db, exchange).run_once("SCHEDULED")
    current = db.get_grid(grid["id"])
    assert current["status"] in {"CLOSED", "HOLDING"}
    event = db.get_last_event(grid["id"], "MAX_DAYS_REACHED")
    assert event and event["details"]["age_days"] >= 1
    assert not [order for order in exchange.create_calls if order[1] == "SELL"]


def test_max_days_engine_and_simulator_match_inventory_free_cash_and_dust(monkeypatch):
    start = datetime(2023, 11, 14, 22, 13, 20, tzinfo=timezone.utc)
    end = start + timedelta(days=1)
    engine, db, exchange = make_engine(fee_rate="0.001", fee_asset="USDT")
    grid = engine.create_grid("XRPUSDT", 90, 110, 5, capital=1000,
        strategy="simple", params={"max_days": 1})
    db.update_grid(grid["id"], created_at=start.replace(tzinfo=None))
    levels = db.get_grid_levels(grid["id"])
    bought = levels[2]
    buy = exchange.get_order("XRPUSDT", order_id=bought["order_id"])
    exchange.fill(buy["order_id"])
    exchange.orders[buy["order_id"]]["_trades"][0].update(
        commission=Decimal("0.196"), commission_asset="USDT")
    buy = exchange.get_order("XRPUSDT", order_id=buy["order_id"])
    engine.sync_grid(grid["id"])
    db.update_grid(grid["id"], dust_qty="10.05")
    exchange.bid = exchange.ask = exchange.avg = Decimal("96")
    exchange.get_my_trades = lambda symbol, order_id: [{
        "commission": Decimal("0.96"), "commission_asset": "USDT", "price": Decimal("96")
    }]

    prices = np.asarray([100.0, 96.0, 96.0])
    candles = SimpleNamespace(timestamp=np.asarray([int(start.timestamp()),
        int(end.timestamp()), int((end + timedelta(days=1)).timestamp())]),
        low=np.asarray([100.0, 94.9, 94.9]), high=np.asarray([100.0, 100.0, 100.0]),
        close=prices, gaps=0)
    original_process = SimExchange.process

    def include_same_registered_dust(sim, i, low, high, close, cells):
        fills = original_process(sim, i, low, high, close, cells)
        if i == 1:
            sim.dust_qty = Decimal("10.05")
            sim.base += Decimal("10.05")
        return fills

    monkeypatch.setattr(SimExchange, "process", include_same_registered_dust)
    sim = run_simulation(candles, strategy="simple", n=5, capital=1000,
        low=90, high=110, params={"max_days": 1}, fee_asset="USDT",
        filters=exchange.filters, resync_candles=1, sigma_values=np.zeros(3))
    monitor = _monitor_target(engine, db, exchange)
    monitor.clock = lambda: end
    monitor.run_once("SCHEDULED")

    motor_grid = db.get_grid(grid["id"])
    motor_event = db.get_last_event(grid["id"], "MAX_DAYS_REACHED")
    sim_event = next(row for row in sim["events"] if row["type"] == "MAX_DAYS_REACHED")
    repository = next(row for row in db.list_grids_by_status({"HOLDING"})
                      if row.get("origin_grid_id") == grid["id"] or row["symbol"] == "XRPUSDT")
    motor_cells = sorted(int(row["level_idx"]) for row in db.get_grid_levels(repository["id"]))
    assert motor_event["event_type"] == sim_event["type"] == "MAX_DAYS_REACHED"
    assert motor_event["details"]["repository_cells"] == sim_event["details"]["repository_cells"] == [2]
    assert motor_cells == [0]
    buy_cost = Decimal(str(buy["cummulative_quote_qty"]))
    buy_fee = Decimal("0.196")
    motor_cash_free = Decimal("1000") - buy_cost - buy_fee + Decimal(
        str((motor_grid.get("params") or {})["dust_cash_proceeds"]))
    assert float(motor_cash_free) == pytest.approx(sim["metrics"]["cash_final"], abs=1e-8)
    assert Decimal(motor_grid["dust_qty"]) == Decimal(str(sim["metrics"]["dust_residual_qty"])) == Decimal("0.05")
    assert motor_event["details"]["dust_swept"] == sim_event["details"]["dust_swept"] == "10.0"
    assert not [row for row in exchange.get_open_orders("XRPUSDT") if row["side"] == "BUY"]
