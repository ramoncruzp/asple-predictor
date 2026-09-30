from __future__ import annotations

from datetime import datetime, timedelta, timezone

from grid.monitor import GridMonitor
from tests.test_grid_engine import create, make_engine
from tests.test_grid_monitor import monitor_settings


def test_repository_keeps_sell_order_and_tracks_origin_age_and_unrealized_pnl():
    engine, db, exchange = make_engine(fee_rate="0", fee_asset="USDT")
    grid = create(engine)
    buy = next(order for order in exchange.get_open_orders("XRPUSDT") if order["client_order_id"] == "g1L2B0")
    exchange.fill(buy["order_id"])
    engine.sync_grid(grid["id"])
    old_sell = next(order for order in exchange.get_open_orders("XRPUSDT") if order["side"] == "SELL")

    result = engine.close_grid(grid["id"], "repository")

    assert result["status"] == "CLOSED"
    assert result["moved_cells"]
    repository_id = result["repository_grid_id"]
    assert db.get_grid(repository_id)["status"] == "HOLDING"
    moved = db.get_grid_levels(repository_id)[0]
    assert moved["state"] == "SELL_OPEN"
    assert moved["order_id"] == old_sell["order_id"]
    assert moved["client_order_id"] == old_sell["client_order_id"]
    assert next(order for order in exchange.get_open_orders("XRPUSDT") if order["order_id"] == old_sell["order_id"])

    move_event = db.list_grid_events(grid_id=repository_id, event_type="CELL_MOVED_TO_REPOSITORY")[0]
    assert move_event["details"]["origin_grid_id"] == grid["id"]
    assert move_event["details"]["origin_level_idx"] == 2
    assert move_event["details"]["pnl"] == 0
    assert move_event["price"] is not None

    later = datetime.now(timezone.utc) + timedelta(hours=1)
    exchange.move_price("90", "90.01", avg="90")
    monitor = GridMonitor(db, exchange, engine, monitor_settings(), clock=lambda: later)
    run1 = monitor.run_once("SCHEDULED")
    snapshots1 = db.list_grid_snapshots(grid_id=repository_id, run_id=run1["run_id"])
    cell1 = next(row for row in snapshots1 if row["level_idx"] is not None)
    assert cell1["in_repository"] == 1
    assert cell1["origin_grid_id"] == grid["id"]
    assert cell1["origin_level_idx"] == 2
    assert cell1["age_hours"] >= 0.9
    assert cell1["unrealized_pnl"] < 0

    monitor.clock = lambda: later + timedelta(hours=1)
    run2 = monitor.run_once("SCHEDULED")
    snapshots2 = db.list_grid_snapshots(grid_id=repository_id, run_id=run2["run_id"])
    cell2 = next(row for row in snapshots2 if row["level_idx"] is not None)
    assert cell2["age_hours"] > cell1["age_hours"]


def test_repository_sale_uses_original_buy_client_id_and_never_rebuys(caplog):
    engine, db, exchange = make_engine(fee_rate="0", fee_asset="USDT")
    grid = create(engine)
    buy = next(order for order in exchange.get_open_orders("XRPUSDT") if order["client_order_id"] == "g1L2B0")
    exchange.fill(buy["order_id"])
    engine.sync_grid(grid["id"])
    engine.close_grid(grid["id"], "repository")
    repository = next(row for row in db.list_grids_by_status({"HOLDING"}) if row["symbol"] == "XRPUSDT")
    moved = db.get_grid_levels(repository["id"])[0]
    sell_id = moved["order_id"]
    before_orders = len(exchange.create_calls)
    exchange.fill(sell_id)

    summary = engine.sync_repository(repository["id"])

    assert summary["cycles_completed"] == 1
    assert db.get_grid(repository["id"])["status"] == "CLOSED"
    assert db.get_grid_levels(repository["id"])[0]["state"] == "DONE"
    assert db.get_grid_levels(repository["id"])[0]["pnl"] == 8.0
    assert len(exchange.create_calls) == before_orders
    assert "PNL_ESTIMATE" not in caplog.text
    moved_event = db.list_grid_events(grid_id=repository["id"], event_type="CELL_MOVED_TO_REPOSITORY")[0]
    assert moved_event["details"]["pnl"] == moved["pnl"]
