from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import inspect

from database.db_manager import DBManager


@pytest.fixture
def db():
    manager = DBManager("sqlite:///:memory:")
    yield manager
    manager.engine.dispose()


def test_monitor_tables_have_exact_audit_columns(db):
    inspector = inspect(db.engine)
    assert {"monitor_runs", "grid_events", "grid_snapshots"} <= set(inspector.get_table_names())
    assert {c["name"] for c in inspector.get_columns("monitor_runs")} == {
        "id", "started_at", "finished_at", "trigger", "status", "grids_checked",
        "grids_failed", "events_written", "duration_ms", "note",
    }
    assert {c["name"] for c in inspector.get_columns("grid_events")} == {
        "id", "run_id", "source", "ts", "grid_id", "level_idx", "client_order_id",
        "order_id", "event_type", "reason", "price", "details",
    }
    assert {c["name"] for c in inspector.get_columns("grid_snapshots")} == {
        "id", "run_id", "ts", "grid_id", "level_idx", "symbol", "grid_status", "level_state",
        "buy_price", "sell_price", "held_qty", "cycles_completed", "pnl_realized", "fee_paid",
        "market_mid", "unrealized_pnl", "open_orders_db", "inventory_value_usdt", "in_repository",
        "origin_grid_id", "origin_level_idx", "age_hours",
        "break_prob", "sigma_24h", "trapped_capital_pct", "free_cells",
    }


def test_monitor_run_heartbeat_and_stale_interruption(db):
    first = db.start_monitor_run("STARTUP")
    assert first["status"] == "RUNNING"
    second = db.start_monitor_run("SCHEDULED")
    assert db.get_last_monitor_run(exclude_id=second["id"])["id"] == first["id"]
    assert db.mark_stale_runs_interrupted(before_id=second["id"]) == 1
    assert db.get_last_monitor_run(exclude_id=second["id"])["status"] == "INTERRUPTED"
    finished = db.finish_monitor_run(second["id"], status="OK", grids_checked=2, duration_ms=9)
    assert finished["finished_at"] is not None
    assert finished["grids_checked"] == 2


def test_events_and_snapshots_round_trip_and_snapshot_batch_is_atomic(db, monkeypatch):
    run = db.start_monitor_run("SCHEDULED")
    event = db.add_grid_event(
        run_id=run["id"], source="MONITOR", grid_id=7, level_idx=2,
        client_order_id="g7L2B0", order_id=9, event_type="BUY_PLACED",
        details={"qty": 1.2}, price=100.0,
    )
    assert db.list_grid_events(grid_id=7)[0]["details"] == {"qty": 1.2}
    assert event["run_id"] == run["id"]
    rows = [
        {"run_id": run["id"], "grid_id": 7, "level_idx": 0, "symbol": "XRPUSDT", "grid_status": "ACTIVE"},
        {"run_id": run["id"], "grid_id": 7, "level_idx": 1, "symbol": "XRPUSDT", "grid_status": "ACTIVE"},
    ]
    original = db.grid_snapshots.insert
    monkeypatch.setattr(db.grid_snapshots, "insert", lambda: (_ for _ in ()).throw(RuntimeError("insert failed")))
    with pytest.raises(RuntimeError, match="insert failed"):
        db.add_snapshots(rows)
    assert db.list_grid_snapshots(grid_id=7) == []
    monkeypatch.setattr(db.grid_snapshots, "insert", original)
    assert db.add_snapshots(rows) == 2
    assert len(db.list_grid_snapshots(grid_id=7)) == 2


def test_grid_status_transition_is_compare_and_set(db):
    db.add_or_reactivate_coin("XRPUSDT")
    grid = db.create_grid_with_levels(
        {"symbol": "XRPUSDT", "range_low": 90, "range_high": 110, "n_levels": 4,
         "capital_total": 100, "status": "ACTIVE", "environment": "testnet", "open_price": 100},
        [{"level_idx": 0, "price": 90, "sell_price": 91, "capital": 25, "state": "IDLE"}],
    )
    assert db.transition_grid_status(grid["id"], {"ACTIVE", "PAUSED"}, "CLOSING") is True
    assert db.transition_grid_status(grid["id"], {"ACTIVE", "PAUSED"}, "CLOSING") is False
    assert db.get_grid(grid["id"])["status"] == "CLOSING"


def test_status_queries_count_holding_for_delete_but_not_grid_limit(db):
    db.add_or_reactivate_coin("XRPUSDT")
    base = {"symbol": "XRPUSDT", "range_low": 90, "range_high": 110, "n_levels": 4,
            "capital_total": 100, "environment": "testnet", "open_price": 100}
    levels = [{"level_idx": 0, "price": 90, "sell_price": 91, "capital": 25, "state": "IDLE"}]
    holding = db.create_grid_with_levels({**base, "status": "HOLDING"}, levels)
    assert db.count_open_grids() == 0
    assert db.has_open_grid("XRPUSDT") is True
    assert db.list_grids_by_status({"HOLDING"})[0]["id"] == holding["id"]
    active = db.create_grid_with_levels({**base, "status": "ACTIVE"}, levels)
    assert db.count_open_grids() == 1
    assert {g["id"] for g in db.list_grids_by_status({"ACTIVE", "HOLDING"})} == {holding["id"], active["id"]}


def test_holding_repository_still_blocks_coin_delete_with_409(tmp_path):
    from tests.test_grid_db import grid_values, level_values, make_route_app, request

    route_db = DBManager(f"sqlite:///{tmp_path / 'holding-coin-route.db'}")
    route_db.add_or_reactivate_coin("DOGEUSDT")
    route_db.create_grid_with_levels({**grid_values(), "status": "HOLDING"}, level_values())
    status, _ = request(make_route_app(route_db), "DELETE", "/api/coins/DOGEUSDT")
    assert status == 409
    route_db.engine.dispose()


def test_settings_expose_grid_monitor_defaults():
    from config.settings import Settings

    settings = Settings(_env_file=None)
    assert settings.grid_monitor_interval == 900
    assert settings.grid_monitor_gap_minutes == 20
    assert settings.grid_monitor_enabled is True
