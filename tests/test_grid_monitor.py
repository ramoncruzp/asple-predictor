from __future__ import annotations

from decimal import Decimal
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from grid.engine import GridEngine
from grid.monitor import GridMonitor
from database.db_manager import DBManager
from tests.test_grid_engine import create, make_engine
from data.testnet_client import TestnetOrderError as _TestnetOrderError


@pytest.mark.parametrize("path", ["paused", "cancel", "repository"])
def test_canceled_partial_buy_is_accounted_and_protected(path):
    engine, db, exchange = make_engine(fee_rate="0", fee_asset="USDT")
    grid = create(engine)
    buy_level = next(row for row in db.get_grid_levels(grid["id"]) if row["state"] == "BUY_OPEN")
    exchange.fill(buy_level["order_id"], partial=True)
    original_cancel = exchange.cancel_order
    def cancel(symbol, order_id):
        order = original_cancel(symbol, order_id)
        order["status"] = "CANCELED"
        exchange.orders[int(order_id)]["status"] = "CANCELED"
        return order
    exchange.cancel_order = cancel
    if path == "paused":
        db.update_grid(grid["id"], status="PAUSED")
        engine.sync_paused(grid["id"])
    elif path == "cancel":
        engine.cancel_grid_orders(grid["id"], finalize=False)
    else:
        db.update_grid(grid["id"], status="CLOSING")
        engine._close_repository(grid["id"])
    if path != "repository":
        level = next(row for row in db.get_grid_levels(grid["id"])
                     if int(row["level_idx"]) == int(buy_level["level_idx"]))
        assert Decimal(str(level["held_qty"])) > 0
        assert level["state"] != "IDLE"
    else:
        repo = next(row for row in db.list_grids_by_status({"HOLDING"}) if row["symbol"] == "XRPUSDT")
        moved = db.get_grid_levels(repo["id"])[0]
        assert Decimal(str(moved["held_qty"])) > 0
    events = db.list_grid_events(grid_id=grid["id"], event_type="BUY_PARTIAL_SETTLED")
    assert len(events) == 1
    assert events[0]["details"]["path"] == {"paused": "sync_paused", "cancel": "cancel_grid_orders",
                                               "repository": "close_repository"}[path]


def test_zero_execution_cancelled_buy_keeps_existing_state():
    engine, db, _exchange = make_engine(fee_rate="0", fee_asset="USDT")
    grid = create(engine)
    level = next(row for row in db.get_grid_levels(grid["id"]) if row["state"] == "BUY_OPEN")
    before = dict(level)
    assert not engine._settle_canceled_buy_partial(grid, level,
        {"order_id": level["order_id"], "status": "CANCELED", "executed_qty": 0}, "test")
    assert db.get_grid_levels(grid["id"])[int(level["level_idx"])] == before
    assert not db.list_grid_events(grid_id=grid["id"], event_type="BUY_PARTIAL_SETTLED")


def test_final_cancel_accounts_partial_buy_without_placing_sell():
    engine, db, exchange = make_engine(fee_rate="0", fee_asset="USDT")
    grid = create(engine)
    level = next(row for row in db.get_grid_levels(grid["id"]) if row["state"] == "BUY_OPEN")
    exchange.fill(level["order_id"], partial=True)
    partial_qty = exchange.orders[level["order_id"]]["executed_qty"]
    original_cancel = exchange.cancel_order

    def cancel(symbol, order_id):
        result = original_cancel(symbol, order_id)
        result["status"] = "CANCELED"
        exchange.orders[int(order_id)]["status"] = "CANCELED"
        return result

    exchange.cancel_order = cancel
    create_count = len(exchange.create_calls)
    result = engine.cancel_grid_orders(grid["id"], finalize=True)
    settled = db.get_grid_levels(grid["id"])[int(level["level_idx"])]
    assert result["status"] == "CANCELLED"
    assert settled["state"] == "ERROR" and Decimal(str(settled["held_qty"])) > 0
    assert Decimal(str(settled["held_qty"])) == partial_qty
    assert result["remaining_inventory"] == result["unmanaged_inventory"]
    assert result["remaining_inventory"][0]["level_idx"] == level["level_idx"]
    assert Decimal(str(result["remaining_inventory"][0]["held_qty"])) == partial_qty
    assert len(exchange.create_calls) == create_count


def test_final_cancel_accounts_buy_filled_before_cancel_without_new_sell():
    engine, db, exchange = make_engine(fee_rate="0", fee_asset="USDT")
    grid = create(engine)
    level = next(row for row in db.get_grid_levels(grid["id"]) if row["state"] == "BUY_OPEN")
    exchange.fill(level["order_id"])
    create_count = len(exchange.create_calls)
    result = engine.cancel_grid_orders(grid["id"], finalize=True)
    settled = db.get_grid_levels(grid["id"])[int(level["level_idx"])]
    assert result["status"] == "CANCELLED"
    assert settled["state"] == "ERROR" and Decimal(str(settled["held_qty"])) > 0
    assert len(exchange.create_calls) == create_count
    assert result["unmanaged_inventory"][0]["level_idx"] == level["level_idx"]


def test_cancel_does_not_clobber_sell_open_level_with_historical_filled_buy():
    engine, db, exchange = make_engine(fee_rate="0", fee_asset="USDT")
    grid = create(engine)
    buy = next(row for row in exchange.get_open_orders("XRPUSDT") if row["side"] == "BUY")
    exchange.fill(buy["order_id"])
    engine.sync_grid(grid["id"])
    level = next(row for row in db.get_grid_levels(grid["id"])
                 if row.get("buy_client_order_id") == buy["client_order_id"])
    assert level["state"] == "SELL_OPEN"
    sell_id = int(level["order_id"])
    # Simulate the cancel loop holding a stale BUY snapshot while storage has the SELL.
    original_levels = db.get_grid_levels
    first_read = {"pending": True}

    def stale_first_read(grid_id):
        rows = original_levels(grid_id)
        if first_read["pending"]:
            first_read["pending"] = False
            rows = [dict(row) for row in rows]
            stale = rows[int(level["level_idx"])]
            stale.update(state="BUY_OPEN", order_id=int(buy["order_id"]),
                         client_order_id=buy["client_order_id"])
        return rows

    db.get_grid_levels = stale_first_read
    result = engine.cancel_grid_orders(grid["id"], finalize=False)
    after = db.get_grid_levels(grid["id"])[int(level["level_idx"])]
    db.get_grid_levels = original_levels
    assert after["state"] == "SELL_OPEN"
    assert after["held_qty"] == level["held_qty"]
    assert after["order_id"] == sell_id
    assert sell_id in exchange.orders and exchange.orders[sell_id]["status"] == "NEW"
    assert not result["filled_during_cancel"]


@pytest.mark.parametrize("path", ["partial_response", "cancel_exception"])
def test_cancel_summary_only_captures_partial_buy_when_settlement_succeeds(path):
    engine, db, exchange = make_engine(fee_rate="0", fee_asset="USDT")
    grid = create(engine)
    level = next(row for row in db.get_grid_levels(grid["id"]) if row["state"] == "BUY_OPEN")
    exchange.fill(level["order_id"], partial=True)
    original_cancel = exchange.cancel_order

    def cancel_with_state_race(symbol, order_id):
        result = original_cancel(symbol, order_id)
        db.update_level(grid["id"], int(level["level_idx"]), state="SELL_OPEN")
        if path == "cancel_exception":
            exchange.orders[int(order_id)]["status"] = "FILLED"
            raise RuntimeError("cancel response interrupted")
        return result

    exchange.cancel_order = cancel_with_state_race
    result = engine.cancel_grid_orders(grid["id"], finalize=False)
    assert result["filled_during_cancel"] == []
    assert db.list_grid_events(grid_id=grid["id"], event_type="BUY_PARTIAL_SETTLED") == []


def test_monitor_detects_testnet_reset_once_and_skips_affected_grid():
    engine, db, exchange = make_engine(fee_rate="0", fee_asset="USDT")
    grid = create(engine)
    before_levels = db.get_grid_levels(grid["id"])
    before_writes = list(exchange.create_calls)
    exchange.get_open_orders = lambda symbol=None: []
    exchange.get_order = lambda *args, **kwargs: (_ for _ in ()).throw(_TestnetOrderError(-2013, "unknown"))
    monitor = GridMonitor(db, exchange, engine, monitor_settings())
    monitor.run_once()
    monitor.run_once()
    events = db.list_grid_events(event_type="TESTNET_RESET_DETECTED")
    assert len(events) == 1
    restarted = GridMonitor(db, exchange, engine, monitor_settings())
    restarted.run_once()
    assert len(db.list_grid_events(event_type="TESTNET_RESET_DETECTED")) == 1
    assert restarted.testnet_reset_suspected is True and restarted.testnet_reset_since is not None
    assert db.get_grid_levels(grid["id"]) == before_levels
    assert exchange.create_calls == before_writes


def test_monitor_does_not_call_a_single_missing_order_a_reset():
    engine, db, exchange = make_engine(fee_rate="0", fee_asset="USDT")
    grid = create(engine)
    ids = {int(row["order_id"]) for row in db.get_grid_levels(grid["id"]) if row.get("order_id")}
    missing_id = min(ids)
    original_get_order = exchange.get_order
    original_open_orders = exchange.get_open_orders
    exchange.get_open_orders = lambda symbol=None: [row for row in original_open_orders(symbol)
                                                    if int(row["order_id"]) != missing_id]
    exchange.get_order = lambda symbol, order_id=None, client_order_id=None: (
        (_ for _ in ()).throw(_TestnetOrderError(-2013, "unknown"))
        if int(order_id) == missing_id else original_get_order(symbol, order_id, client_order_id))
    monitor = GridMonitor(db, exchange, engine, monitor_settings())
    monitor.run_once()
    assert not db.list_grid_events(event_type="TESTNET_RESET_DETECTED")


def test_monitor_treats_terminal_order_absent_from_open_orders_as_legitimate():
    engine, db, exchange = make_engine(fee_rate="0", fee_asset="USDT")
    grid = create(engine)
    orders = exchange.get_open_orders("XRPUSDT")
    terminal_id = int(orders[0]["order_id"])
    exchange.get_open_orders = lambda symbol=None: []
    original_get_order = exchange.get_order
    def get_order(symbol, order_id=None, client_order_id=None):
        if int(order_id) == terminal_id:
            return {**original_get_order(symbol, order_id, client_order_id), "status": "FILLED"}
        raise _TestnetOrderError(-2013, "unknown")
    exchange.get_order = get_order
    monitor = GridMonitor(db, exchange, engine, monitor_settings())
    monitor.run_once()
    assert not db.list_grid_events(event_type="TESTNET_RESET_DETECTED")


def test_monitor_closes_reset_episode_when_missing_orders_recover():
    engine, db, exchange = make_engine(fee_rate="0", fee_asset="USDT")
    grid = create(engine)
    original_get_order = exchange.get_order
    exchange.get_open_orders = lambda symbol=None: []
    exchange.get_order = lambda *args, **kwargs: (_ for _ in ()).throw(_TestnetOrderError(-2013, "unknown"))
    monitor = GridMonitor(db, exchange, engine, monitor_settings())
    monitor.run_once()
    assert monitor.testnet_reset_suspected
    exchange.get_order = original_get_order
    monitor.run_once()
    assert not monitor.testnet_reset_suspected and monitor.testnet_reset_since is None
    assert len(db.list_grid_events(event_type="TESTNET_RESET_RECOVERED")) == 1
    exchange.get_open_orders = lambda symbol=None: []
    exchange.get_order = lambda *args, **kwargs: (_ for _ in ()).throw(_TestnetOrderError(-2013, "unknown"))
    monitor.run_once()
    assert len(db.list_grid_events(event_type="TESTNET_RESET_DETECTED")) == 2


def test_engine_event_sink_receives_events_and_sink_failure_is_isolated(caplog):
    engine, db, exchange = make_engine(fee_rate="0", fee_asset="USDT")
    events = []
    engine.event_sink = events.append
    grid = create(engine)
    assert any(row["event_type"] == "BUY_PLACED" and row["grid_id"] == grid["id"] for row in events)

    engine.event_sink = lambda event: (_ for _ in ()).throw(RuntimeError("sink unavailable"))
    engine._emit("TEST_EVENT", grid["id"], 0, details={"value": 1})
    assert "event sink failed" in caplog.text


def test_sync_closing_settles_fills_without_rearming_buy():
    engine, db, exchange = make_engine(fee_rate="0", fee_asset="USDT")
    grid = create(engine)
    db.update_grid(grid["id"], status="CLOSING")
    buy = exchange.get_open_orders("XRPUSDT")[-1]
    exchange.fill(buy["order_id"])
    engine.sync_closing(grid["id"])
    level = db.get_grid_levels(grid["id"])[2]
    sell_id = level["order_id"]
    exchange.fill(sell_id)
    before = len(exchange.create_calls)
    result = engine.sync_closing(grid["id"])
    level = db.get_grid_levels(grid["id"])[2]
    assert result["cycles_completed"] == 1
    assert level["state"] == "DONE"
    assert len(exchange.create_calls) == before


def test_repository_sync_sells_only_and_closes_when_last_cell_is_done():
    engine, db, exchange = make_engine(fee_rate="0", fee_asset="USDT")
    grid = create(engine)
    levels = db.get_grid_levels(grid["id"])
    for row in levels:
        db.update_level(grid["id"], row["level_idx"], state="ERROR", order_id=None, client_order_id=None)
    level = levels[2]
    sell = exchange.place_order(
        "XRPUSDT", "SELL", Decimal("1.0"), Decimal("102.0"), client_order_id="g1L2S0",
    )
    db.update_grid(grid["id"], status="HOLDING")
    db.update_level(grid["id"], 2, state="SELL_OPEN", held_qty=1.0,
                    client_order_id="g1L2S0", order_id=sell["order_id"])
    exchange.fill(sell["order_id"])
    before_calls = len(exchange.create_calls)
    result = engine.sync_repository(grid["id"])
    assert result["cycles_completed"] == 1
    assert db.get_grid_levels(grid["id"])[2]["state"] == "DONE"
    assert db.get_grid(grid["id"])["status"] == "CLOSED"
    assert len(exchange.create_calls) == before_calls


def monitor_settings():
    return SimpleNamespace(
        usdt_por_grid=1000.0, max_grids_simultaneos=5,
        capital_max_por_nivel_pct=0.30, grid_min_step_pct=0.003,
        grid_monitor_interval=900, grid_monitor_gap_minutes=20, grid_monitor_enabled=True,
    )


def _freeze_monitor_policy(monkeypatch):
    import grid.monitor as monitor_module
    from grid.policy import PolicyDecision
    monkeypatch.setattr(monitor_module, "evaluate_grid",
        lambda *args, **kwargs: PolicyDecision("NONE", (), {"break_prob": .01, "sigma_24h": .02}))
    monkeypatch.setattr(monitor_module, "adjust_decision",
        lambda *args, **kwargs: PolicyDecision("NONE", (), {}))


def test_monitor_uses_per_symbol_horizon_cache_and_records_snapshot_contract(monkeypatch):
    _freeze_monitor_policy(monkeypatch)
    engine, db, exchange = make_engine(fee_rate="0", fee_asset="USDT")
    class Provider:
        def __init__(self): self.calls = []
        def get(self, symbol, horizon_h=24):
            self.calls.append((symbol, horizon_h))
            return SimpleNamespace(sigma_24h=.02, source="model", fallback=False)
    provider = Provider()
    engine.vol_provider = provider
    first = engine.create_grid("XRPUSDT", 90, 110, 5, strategy="smart", params={"horizon_h": 1})
    first_params = dict(db.get_grid(first["id"])["params"])
    first_params["horizon_h"] = 6  # legacy stored value remains valid for the policy, provider uses 24 h
    db.update_grid(first["id"], params=db._json(first_params))
    second = engine.create_grid("XRPUSDT", 89, 111, 5, strategy="smart", params={"horizon_h": 4})
    third = engine.create_grid("XRPUSDT", 88, 112, 5, strategy="smart", params={"horizon_h": 2})
    params = dict(db.get_grid(third["id"])["params"])
    params.pop("horizon_h")
    db.update_grid(third["id"], params=db._json(params))
    provider.calls.clear()
    monitor = GridMonitor(db, exchange, engine, monitor_settings(), vol_provider=provider)
    result = monitor.run_once()
    assert result["status"] == "OK"
    assert sorted(provider.calls) == [("XRPUSDT", 4), ("XRPUSDT", 24)]
    snapshots = [row for grid in (first, second, third)
                 for row in db.list_grid_snapshots(grid_id=grid["id"], run_id=result["run_id"])
                 if row["level_idx"] is None]
    by_grid = {row["grid_id"]: row for row in snapshots}
    assert by_grid[first["id"]]["monitor_horizon_h"] == 6
    assert by_grid[second["id"]]["monitor_horizon_h"] == 4
    assert by_grid[third["id"]]["monitor_horizon_h"] == 24
    assert all(row["source"] == "model" and row["sigma_monitor_h"] is not None
               and row["market_mid"] is not None for row in snapshots)
    assert by_grid[first["id"]]["sigma_monitor_h"] == pytest.approx(.02 * (6 / 24) ** .5)
    assert by_grid[second["id"]]["sigma_monitor_h"] == pytest.approx(.02 * (4 / 24) ** .5)
    assert by_grid[third["id"]]["sigma_monitor_h"] == pytest.approx(.02)


def test_monitor_deduplicates_fallback_event_and_rearms_after_fresh_forecast(monkeypatch):
    _freeze_monitor_policy(monkeypatch)
    engine, db, exchange = make_engine(fee_rate="0", fee_asset="USDT")
    class Provider:
        def __init__(self): self.index = 0
        def get(self, symbol, horizon_h=24):
            self.index += 1
            fallback = self.index in {1, 2, 4}
            return SimpleNamespace(sigma_24h=.02, source="model", fallback=fallback,
                fallback_reason="forecast_unavailable" if fallback else None)
    provider = Provider()
    engine.vol_provider = provider
    grid = engine.create_grid("XRPUSDT", 90, 110, 5, strategy="smart", params={"horizon_h": 4})
    provider.index = 0
    monitor = GridMonitor(db, exchange, engine, monitor_settings(), vol_provider=provider)
    for _ in range(4):
        assert monitor.run_once()["status"] == "OK"
    events = db.list_grid_events(grid_id=grid["id"], event_type="vol_fallback_24h")
    assert len(events) == 2
    assert all(event["details"]["fallback_horizon_h"] == 24 for event in events)


def test_monitor_run_records_heartbeat_snapshots_and_dust_baseline():
    engine, db, exchange = make_engine(fee_rate="0", fee_asset="USDT")
    copied = create(engine)
    monitor = GridMonitor(db, exchange, engine, monitor_settings())
    result = monitor.run_once("SCHEDULED")
    assert result["status"] == "OK" and result["grids_checked"] == 1
    assert result["events_written"] == 1
    event = db.list_grid_events(event_type="DUST_RECONCILIATION")[0]
    assert event["grid_id"] is None and event["details"]["severity"] == "info"
    assert event["details"]["drift"] is None
    assert db.get_monitor_run(result["run_id"])["status"] == "OK"
    snapshots = db.list_grid_snapshots(grid_id=copied["id"], run_id=result["run_id"])
    assert len(snapshots) == 6
    assert sum(row["level_idx"] is None for row in snapshots) == 1


def test_dust_reconciliation_uses_drift_to_ignore_constant_foreign_balance():
    engine, db, exchange = make_engine(fee_rate="0", fee_asset="USDT")
    grid = create(engine)
    times = [datetime(2026, 10, 3, 12, tzinfo=timezone.utc)]
    exchange.get_balance = lambda asset=None: {asset: {"free": 1000, "locked": 0}}
    monitor = GridMonitor(db, exchange, engine, monitor_settings(), clock=lambda: times[0])
    mids = {grid["symbol"]: 1.0}
    monitor._reconcile_registered_dust([grid], mids, times[0])
    first = db.list_grid_events(event_type="DUST_RECONCILIATION")[0]["details"]
    assert first["drift"] is None and first["severity"] == "info"
    times[0] += timedelta(hours=1)
    monitor._reconcile_registered_dust([grid], mids, times[0])
    stable = db.list_grid_events(event_type="DUST_RECONCILIATION")[0]["details"]
    assert Decimal(stable["drift"]) == 0 and stable["severity"] == "info"
    exchange.get_balance = lambda asset=None: {asset: {"free": 1002, "locked": 0}}
    times[0] += timedelta(hours=1)
    monitor._reconcile_registered_dust([grid], mids, times[0])
    changed = db.list_grid_events(event_type="DUST_RECONCILIATION")[0]["details"]
    assert Decimal(changed["drift"]) == 2 and changed["severity"] == "warning"
    second_grid = {**grid, "id": grid["id"] + 99}
    original_levels = db.get_grid_levels
    db.get_grid_levels = lambda grid_id: [] if int(grid_id) == second_grid["id"] else original_levels(grid_id)
    times[0] += timedelta(hours=1)
    monitor._reconcile_registered_dust([grid, second_grid], mids, times[0])
    changed_set = db.list_grid_events(event_type="DUST_RECONCILIATION")[0]
    assert changed_set["details"]["grid_ids"] == sorted([grid["id"], second_grid["id"]])
    assert changed_set["details"]["drift"] is None and changed_set["reason"] == "baseline"


def test_dust_reconciliation_is_hourly_and_balance_errors_are_omitted(caplog):
    engine, db, exchange = make_engine(fee_rate="0", fee_asset="USDT")
    grid = create(engine)
    now = datetime(2026, 10, 3, 12, tzinfo=timezone.utc)
    monitor = GridMonitor(db, exchange, engine, monitor_settings(), clock=lambda: now)
    exchange.get_balance = lambda _asset=None: (_ for _ in ()).throw(RuntimeError("offline"))
    monitor._reconcile_registered_dust([grid], {grid["symbol"]: 1.0}, now)
    assert not db.list_grid_events(event_type="DUST_RECONCILIATION")
    assert "balance unavailable" in caplog.text


def test_monitor_isolates_grid_sync_failure_and_keeps_other_grids_running():
    engine, db, exchange = make_engine(fee_rate="0", fee_asset="USDT")
    # Seed legacy duplicate grids directly: the production create_grid guard now
    # prevents creating this invalid same-symbol/same-strategy setup.
    grids = [db.create_grid_with_levels(
        {"symbol": "XRPUSDT", "range_low": 90, "range_high": 110, "n_levels": 5,
         "capital_total": 1000, "status": "ACTIVE", "environment": "testnet"},
        [{"level_idx": i, "price": 90 + i, "sell_price": 91 + i, "capital": 200, "state": "IDLE"}
         for i in range(5)],
    ) for _ in range(3)]
    seen = []
    def sync(grid_id):
        seen.append(grid_id)
        if grid_id == grids[0]["id"]:
            raise RuntimeError("one grid failed")
        return {}
    engine.sync_grid = sync
    monitor = GridMonitor(db, exchange, engine, monitor_settings())
    result = monitor.run_once("SCHEDULED")
    assert result["status"] == "PARTIAL"
    assert result["grids_checked"] == 3 and result["grids_failed"] == 1
    assert len(seen) == 3
    assert len(db.list_grid_events(event_type="SYNC_FAILED")) == 1


def test_monitor_detects_run_gap_and_interrupts_stale_startup_runs():
    db = DBManager("sqlite:///:memory:")
    first = db.start_monitor_run("SCHEDULED")
    old = datetime.now(timezone.utc) - timedelta(minutes=26)
    with db.engine.begin() as conn:
        conn.execute(db.monitor_runs.update().where(db.monitor_runs.c.id == first["id"]).values(started_at=old, status="RUNNING"))
    engine, _, exchange = make_engine()
    monitor = GridMonitor(db, exchange, engine, monitor_settings())
    result = monitor.run_once("STARTUP")
    assert db.get_monitor_run(first["id"])["status"] == "INTERRUPTED"
    gap = db.list_grid_events(event_type="RUN_GAP")[0]
    assert gap["details"]["minutes"] >= 26
    assert gap["details"]["previous_run_id"] == first["id"]
    assert db.get_monitor_run(result["run_id"])["trigger"] == "STARTUP"


def test_monitor_market_read_failure_keeps_snapshots_with_null_mid(caplog):
    engine, db, exchange = make_engine()
    grid = db.create_grid_with_levels(
        {"symbol": "XRPUSDT", "range_low": 90, "range_high": 110, "n_levels": 1,
         "capital_total": 20, "status": "ACTIVE", "environment": "testnet", "open_price": 100},
        [{"level_idx": 0, "price": 99, "sell_price": 101, "capital": 20, "state": "IDLE"}],
    )
    exchange.get_book_ticker = lambda symbol: (_ for _ in ()).throw(RuntimeError("ticker unavailable"))
    engine.sync_grid = lambda grid_id: {}
    result = GridMonitor(db, exchange, engine, monitor_settings()).run_once("SCHEDULED")
    rows = db.list_grid_snapshots(grid_id=grid["id"], run_id=result["run_id"])
    assert len(rows) == 2 and all(row["market_mid"] is None for row in rows)
    assert "grid snapshot mid unavailable" in caplog.text


def test_monitor_persists_fill_events_with_run_and_level_identity_and_unrealized_snapshot():
    engine, db, exchange = make_engine(fee_rate="0", fee_asset="USDT")
    grid = create(engine)
    buy = exchange.get_open_orders("XRPUSDT")[-1]
    exchange.fill(buy["order_id"])
    monitor = GridMonitor(db, exchange, engine, monitor_settings())
    run = monitor.run_once("SCHEDULED")
    events = db.list_grid_events(grid_id=grid["id"])
    buy_event = next(event for event in events if event["event_type"] == "BUY_FILLED")
    sell_event = next(event for event in events if event["event_type"] == "SELL_PLACED")
    for event in (buy_event, sell_event):
        assert event["run_id"] == run["run_id"]
        assert event["grid_id"] == grid["id"] and event["level_idx"] == 2
        assert event["client_order_id"] and event["order_id"] is not None
    snapshots = db.list_grid_snapshots(grid_id=grid["id"], run_id=run["run_id"])
    level = next(row for row in snapshots if row["level_idx"] == 2)
    assert level["level_state"] == "SELL_OPEN"
    assert level["unrealized_pnl"] == pytest.approx((level["market_mid"] - level["buy_price"]) * level["held_qty"])
    summary = next(row for row in snapshots if row["level_idx"] is None)
    assert summary["open_orders_db"] == 3
    assert summary["inventory_value_usdt"] == pytest.approx(level["market_mid"] * level["held_qty"])


def test_monitor_warns_when_closing_grid_is_stale():
    engine, db, exchange = make_engine()
    grid = create(engine)
    old = datetime.now(timezone.utc) - timedelta(minutes=40)
    db.update_grid(grid["id"], status="CLOSING", created_at=old)
    monitor = GridMonitor(db, exchange, engine, monitor_settings())
    run = monitor.run_once("SCHEDULED")
    event = db.list_grid_events(grid_id=grid["id"], event_type="CLOSE_PENDING")[0]
    assert event["run_id"] == run["run_id"]
    assert event["details"]["age_minutes"] >= 40


def test_monitor_does_not_sync_closing_grid_but_keeps_its_snapshot():
    engine, db, exchange = make_engine()
    grid = create(engine)
    db.update_grid(grid["id"], status="CLOSING")
    engine.sync_grid = lambda _grid_id: (_ for _ in ()).throw(AssertionError("CLOSING grid synchronized"))
    engine.sync_repository = lambda _grid_id: (_ for _ in ()).throw(AssertionError("CLOSING grid synchronized as repository"))

    result = GridMonitor(db, exchange, engine, monitor_settings()).run_once("SCHEDULED")

    assert result["status"] == "OK" and result["grids_checked"] == 1
    snapshots = db.list_grid_snapshots(grid_id=grid["id"], run_id=result["run_id"])
    assert len(snapshots) == 6
    assert all(row["grid_status"] == "CLOSING" for row in snapshots)


class CapturingScheduler:
    running = False

    def __init__(self):
        self.jobs = []

    def add_job(self, function, trigger, **kwargs):
        self.jobs.append({"function": function, "trigger": trigger, **kwargs})

    def start(self):
        self.running = True

    def shutdown(self, wait=True):
        self.running = False


def test_monitor_start_schedules_interval_and_nonblocking_startup_job():
    scheduler = CapturingScheduler()
    engine, db, exchange = make_engine()
    monitor = GridMonitor(db, exchange, engine, monitor_settings(), scheduler=scheduler)
    monitor.start()
    interval, startup = scheduler.jobs
    assert interval["trigger"] == "interval" and interval["seconds"] == 900
    assert interval["max_instances"] == 1 and interval["coalesce"] is True
    assert startup["trigger"] == "date" and startup["args"] == ["STARTUP"]
    assert startup["misfire_grace_time"] == 300
    monitor.stop()
    assert scheduler.running is False


def test_monitor_snapshot_failure_isolated_per_grid():
    engine, db, exchange = make_engine(fee_rate="0", fee_asset="USDT")
    # Seed legacy duplicate grids directly; see the guard test in 15B-1A.
    grids = [db.create_grid_with_levels(
        {"symbol": "XRPUSDT", "range_low": 90, "range_high": 110, "n_levels": 5,
         "capital_total": 1000, "status": "ACTIVE", "environment": "testnet"},
        [{"level_idx": i, "price": 90 + i, "sell_price": 91 + i, "capital": 200, "state": "IDLE"}
         for i in range(5)],
    ) for _ in range(3)]
    original_add_snapshots = db.add_snapshots
    failed_id = grids[0]["id"]
    def fail_one_grid(rows):
        if rows[0]["grid_id"] == failed_id:
            raise RuntimeError("snapshot database unavailable")
        return original_add_snapshots(rows)
    db.add_snapshots = fail_one_grid
    monitor = GridMonitor(db, exchange, engine, monitor_settings())

    result = monitor.run_once("SCHEDULED")

    assert result["status"] == "PARTIAL"
    assert result["grids_checked"] == 3 and result["grids_failed"] == 1
    assert len(db.list_grid_snapshots(grid_id=grids[1]["id"], run_id=result["run_id"])) == 6
    assert len(db.list_grid_snapshots(grid_id=grids[2]["id"], run_id=result["run_id"])) == 6
    assert len(db.list_grid_events(grid_id=failed_id, event_type="SNAPSHOT_FAILED")) == 1


def test_app_lifespan_starts_without_monitor_when_testnet_credentials_missing(tmp_path, monkeypatch, caplog):
    from types import SimpleNamespace
    from api import main as main_module

    settings = SimpleNamespace(
        binance_api_key="tu_api_key_aqui", binance_api_secret="tu_api_secret_aqui",
        testnet_api_key="tu_testnet_api_key_aqui", testnet_api_secret="tu_testnet_secret_aqui",
        database_url="sqlite:///:memory:", log_level="ERROR", grid_monitor_enabled=True,
        grid_monitor_interval=900, grid_monitor_gap_minutes=20,
        usdt_por_grid=100, max_grids_simultaneos=5,
        capital_max_por_nivel_pct=0.30, grid_min_step_pct=0.003,
    )
    class DummyDB:
        def seed_coin_if_missing(self, *args):
            pass
        def get_active_coins(self):
            return []
        def list_readiness(self):
            return []
        def get_readiness(self, *args):
            return None
        def set_readiness(self, *args):
            return {}
        def has_active_training_job(self, *args):
            return False
    class DummyModel:
        def load(self, *args):
            pass
    class DummyLoop:
        def __init__(self, *args, **kwargs):
            self.models = {}
        def start(self):
            pass
        def stop(self):
            pass
    class FailingTrainingJobService:
        def __init__(self, *args, **kwargs):
            pass
        def recover_interrupted(self):
            raise RuntimeError("recovery boom")
    monkeypatch.setattr(main_module, "Settings", lambda: settings)
    monkeypatch.setattr(main_module, "BinanceClient", lambda *args: object())
    monkeypatch.setattr(main_module, "DBManager", lambda *args: DummyDB())
    monkeypatch.setattr(main_module, "ModelA", DummyModel)
    monkeypatch.setattr(main_module, "ShadowPredictor", lambda *args, **kwargs: object())
    monkeypatch.setattr(main_module, "PredictionLoop", DummyLoop)
    monkeypatch.setattr(main_module, "VerificationLoop", DummyLoop)
    monkeypatch.setattr(main_module, "VolLoop", DummyLoop)
    monkeypatch.setattr(main_module, "TrainingJobService", FailingTrainingJobService)
    monkeypatch.setattr(main_module, "VOL_ARTIFACT_DIR", tmp_path / "missing-vol")

    lifespan_entered = {"value": False}
    async def run_lifespan():
        app = SimpleNamespace(state=SimpleNamespace())
        async with main_module.lifespan(app):
            lifespan_entered["value"] = True
            assert app.state.grid_monitor is None
    import asyncio
    try:
        asyncio.run(run_lifespan())
    except Exception:
        pass
    assert lifespan_entered["value"] is True
    assert "Grid monitor disabled: faltan credenciales" in caplog.text
    assert "No se pudieron recuperar los trabajos de entrenamiento interrumpidos" in caplog.text


def test_coin_onboarding_start_failure_does_not_prevent_lifespan_start(tmp_path, monkeypatch, caplog):
    from types import SimpleNamespace
    import asyncio
    from api import main as main_module

    settings = SimpleNamespace(
        binance_api_key="tu_api_key_aqui", binance_api_secret="tu_api_secret_aqui",
        testnet_api_key="tu_testnet_api_key_aqui", testnet_api_secret="tu_testnet_secret_aqui",
        database_url="sqlite:///:memory:", log_level="ERROR", grid_monitor_enabled=False,
        grid_monitor_interval=900, grid_monitor_gap_minutes=20,
        usdt_por_grid=100, max_grids_simultaneos=5, capital_max_por_nivel_pct=0.30,
        grid_min_step_pct=0.003,
    )

    class DummyDB:
        def seed_coin_if_missing(self, *args):
            pass

    class DummyModel:
        def load(self, *args):
            pass

    class DummyLoop:
        def __init__(self, *args, **kwargs):
            self.models = {}
        def start(self):
            pass
        def stop(self):
            pass

    class DummyTrainingJobs:
        def __init__(self, *args, **kwargs):
            pass
        def recover_interrupted(self):
            pass

    class FailingOnboarding:
        def __init__(self, *args, **kwargs):
            pass
        def start(self):
            raise RuntimeError("onboarding boom")
        def stop(self):
            raise RuntimeError("stop boom")

    class DummyRegistry:
        def load_available(self):
            pass
        def ready_symbols(self):
            return []
        def get(self, symbol):
            return None

    artifact = tmp_path / "model-a.bin"
    artifact.write_bytes(b"test")
    monkeypatch.setattr(main_module, "Settings", lambda: settings)
    monkeypatch.setattr(main_module, "BinanceClient", lambda *args: object())
    monkeypatch.setattr(main_module, "DBManager", lambda *args: DummyDB())
    monkeypatch.setattr(main_module, "ModelA", DummyModel)
    monkeypatch.setattr(main_module, "SHADOW_ARTIFACT", artifact)
    monkeypatch.setattr(main_module, "load_optional_shadow_models", lambda: ({}, {}))
    monkeypatch.setattr(main_module, "ShadowPredictor", lambda *args, **kwargs: object())
    monkeypatch.setattr(main_module, "PredictionLoop", DummyLoop)
    monkeypatch.setattr(main_module, "VerificationLoop", DummyLoop)
    monkeypatch.setattr(main_module, "VolLoop", DummyLoop)
    monkeypatch.setattr(main_module, "VolPredictorRegistry", DummyRegistry)
    monkeypatch.setattr(main_module, "CoinOnboardingService", FailingOnboarding)
    monkeypatch.setattr(main_module, "TrainingJobService", DummyTrainingJobs)
    monkeypatch.setattr(main_module, "BackupLoop", DummyLoop)
    monkeypatch.setattr(main_module, "WidenFactorLoop", DummyLoop)
    monkeypatch.setattr(main_module, "GridScanService", DummyLoop)
    monkeypatch.setattr(main_module, "GridAutoOpen", DummyLoop)
    monkeypatch.setattr(main_module, "VOL_ARTIFACT_DIR", tmp_path / "missing-vol")

    async def run_lifespan():
        app = SimpleNamespace(state=SimpleNamespace())
        async with main_module.lifespan(app):
            assert app.state.coin_onboarding_service is not None

    asyncio.run(run_lifespan())
    assert "No se pudo iniciar el servicio de preparación de monedas" in caplog.text
    assert "No se pudo detener el servicio de preparación de monedas" in caplog.text


@pytest.mark.parametrize(
    ("policy_action", "adjust_action", "expected_event"),
    [("NONE", "ADJUST", "GRID_ADJUSTED"), ("PAUSE", "NONE", "GRID_PAUSED")],
)
def test_monitor_adjust_and_pause_keep_their_decisions_after_removed_noop(
    monkeypatch, policy_action, adjust_action, expected_event
):
    import grid.monitor as monitor_module
    from grid.policy import PolicyDecision

    # Reproduce the former A2 expression: it is an identity for every action.
    before_phase21 = lambda decision: (
        decision if decision.action == "CLOSE_REPOSITORY" else decision
    )
    requested = (
        PolicyDecision("ADJUST", (), {"range_low": 85, "range_high": 115, "n_levels": 5})
        if adjust_action == "ADJUST" else PolicyDecision("NONE", (), {})
    )
    policy = PolicyDecision(policy_action, ("test_reason",), {"break_prob": .2})
    expected = before_phase21(requested if adjust_action == "ADJUST" else policy)
    monkeypatch.setattr(monitor_module, "evaluate_grid", lambda *a, **k: policy)
    monkeypatch.setattr(monitor_module, "adjust_decision", lambda *a, **k: requested)

    engine, db, exchange = make_engine(fee_rate="0", fee_asset="USDT")
    engine.vol_provider = SimpleNamespace(
        get=lambda symbol, horizon_h=24: SimpleNamespace(
            sigma_24h=.02, source="model", fallback=False
        )
    )
    grid = engine.create_grid("XRPUSDT", 90, 110, 5, strategy="smart", params={"horizon_h": 4})
    monitor = GridMonitor(db, exchange, engine, monitor_settings(), vol_provider=engine.vol_provider)

    result = monitor.run_once()
    events = db.list_grid_events(grid_id=grid["id"], event_type=expected_event)

    assert result["status"] == "OK"
    assert len(events) == 1
    assert expected.action == {"GRID_ADJUSTED": "ADJUST", "GRID_PAUSED": "PAUSE"}[expected_event]
    if expected_event == "GRID_ADJUSTED":
        assert db.get_grid(grid["id"])["range_low"] == 85
        assert db.get_grid(grid["id"])["range_high"] == 115
    else:
        assert db.get_grid(grid["id"])["status"] == "PAUSED"
