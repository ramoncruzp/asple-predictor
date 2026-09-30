from __future__ import annotations

import sqlite3
from datetime import datetime

import pytest

from database.db_manager import DBManager
from grid.levels import GridConfigError
from tests.test_grid_engine import make_engine


def _legacy_15a(path):
    connection = sqlite3.connect(path)
    connection.executescript("""
        CREATE TABLE grids (id INTEGER PRIMARY KEY, symbol VARCHAR NOT NULL, range_low FLOAT NOT NULL,
          range_high FLOAT NOT NULL, n_levels INTEGER NOT NULL, capital_total FLOAT NOT NULL,
          status VARCHAR NOT NULL, created_at DATETIME NOT NULL, environment VARCHAR NOT NULL,
          open_price FLOAT, closed_at DATETIME, fail_reason VARCHAR);
        CREATE TABLE grid_levels (grid_id INTEGER NOT NULL, level_idx INTEGER NOT NULL, price FLOAT NOT NULL,
          capital FLOAT NOT NULL, order_id INTEGER, state VARCHAR NOT NULL, cycles_completed INTEGER NOT NULL,
          pnl FLOAT NOT NULL, sell_price FLOAT NOT NULL, held_qty FLOAT NOT NULL, client_order_id VARCHAR,
          fee_paid FLOAT NOT NULL, updated_at DATETIME NOT NULL, PRIMARY KEY (grid_id, level_idx));
        CREATE TABLE grid_snapshots (id INTEGER PRIMARY KEY, run_id INTEGER NOT NULL, ts DATETIME NOT NULL,
          grid_id INTEGER NOT NULL, level_idx INTEGER, symbol VARCHAR NOT NULL, grid_status VARCHAR NOT NULL,
          level_state VARCHAR, buy_price FLOAT, sell_price FLOAT, held_qty FLOAT, cycles_completed INTEGER,
          pnl_realized FLOAT, fee_paid FLOAT, market_mid FLOAT, unrealized_pnl FLOAT, open_orders_db INTEGER,
          inventory_value_usdt FLOAT, in_repository INTEGER NOT NULL, origin_grid_id INTEGER,
          origin_level_idx INTEGER, age_hours FLOAT);
        INSERT INTO grids VALUES (1,'XRPUSDT',90,110,2,100,'ACTIVE','2026-01-01','testnet',100,NULL,NULL);
        INSERT INTO grid_levels VALUES (1,0,95,50,NULL,'IDLE',0,0,96,0,NULL,0,'2026-01-01');
    """)
    connection.commit()
    connection.close()


def test_15a_schema_migration_is_idempotent_and_preserves_existing_rows(tmp_path):
    path = tmp_path / "legacy.db"
    _legacy_15a(path)
    db = DBManager(f"sqlite:///{path}")
    DBManager(f"sqlite:///{path}")
    grid = db.get_grid(1)
    level = db.get_grid_levels(1)[0]
    assert grid["strategy"] == "simple" and grid["params"] is None
    assert (grid["symbol"], grid["capital_total"], level["price"], level["sell_price"]) == (
        "XRPUSDT", 100.0, 95.0, 96.0,
    )
    assert level["entry_price"] is None and level["bought_at"] is None
    with db.engine.connect() as conn:
        assert {row["name"] for row in conn.exec_driver_sql("PRAGMA table_info(grid_snapshots)").mappings()} >= {
            "break_prob", "sigma_24h", "trapped_capital_pct", "free_cells",
        }


def test_engine_guard_is_by_symbol_and_strategy_and_holding_does_not_block():
    engine, db, _exchange = make_engine(fee_rate="0", fee_asset="USDT")
    simple = engine.create_grid("XRPUSDT", 90, 110, 5, capital=1000)
    with pytest.raises(GridConfigError, match="open simple grid"):
        engine.create_grid("XRPUSDT", 90, 110, 5, capital=1000)
    smart = engine.create_grid("XRPUSDT", 90, 110, 5, capital=1000, strategy="smart")
    assert simple["strategy"] == "simple" and smart["strategy"] == "smart"
    db.update_grid(smart["id"], status="HOLDING")
    db.update_grid(simple["id"], status="CLOSED")
    another_simple = engine.create_grid("XRPUSDT", 90, 110, 5, capital=1000)
    assert another_simple["strategy"] == "simple"


@pytest.mark.parametrize("params", [
    {"unknown": 1}, {"pause_enter_prob": 0}, {"pause_exit_prob": 0.2},
    {"sigma_scale": 0}, {"close_out_of_range_pct": 100}, {"min_free_cells": 5},
])
def test_smart_grid_rejects_invalid_parameters(params):
    engine, _db, _exchange = make_engine()
    with pytest.raises(GridConfigError):
        engine.create_grid("XRPUSDT", 90, 110, 5, strategy="smart", params=params)


def test_new_smart_levels_store_sell_stop_loss_and_filled_buy_entry_data():
    engine, db, exchange = make_engine(fee_rate="0", fee_asset="USDT")
    grid = engine.create_grid("XRPUSDT", 90, 110, 5, capital=1000,
                              strategy="smart", stop_loss_pct=4)
    levels = db.get_grid_levels(grid["id"])
    assert all(row["sell_price"] is not None and row["stop_loss_pct"] == 4 for row in levels)
    order = exchange.get_open_orders("XRPUSDT")[-1]
    exchange.fill(order["order_id"])
    engine.sync_grid(grid["id"])
    filled = next(row for row in db.get_grid_levels(grid["id"]) if row["state"] == "SELL_OPEN")
    assert filled["entry_price"] == pytest.approx(98)
    assert isinstance(filled["bought_at"], datetime)


def test_old_cell_with_null_sell_price_can_be_derived_from_next_level():
    engine, db, exchange = make_engine(fee_rate="0", fee_asset="USDT")
    grid = engine.create_grid("XRPUSDT", 90, 110, 5, capital=1000)
    level = db.get_grid_levels(grid["id"])[0]
    db.set_level_fields(grid["id"], 0, sell_price=None)
    exchange.fill(level["order_id"])
    engine.sync_grid(grid["id"])
    updated = db.get_grid_levels(grid["id"])[0]
    assert updated["sell_price"] == 94
    assert updated["state"] == "SELL_OPEN"


def test_legacy_inventory_recovers_entry_from_buy_client_id_and_keeps_unknown_age(caplog):
    engine, db, exchange = make_engine(fee_rate="0", fee_asset="USDT")
    grid = engine.create_grid("XRPUSDT", 90, 110, 5, capital=1000)
    cell = db.get_grid_levels(grid["id"])[0]
    exchange.fill(cell["order_id"])
    engine.sync_grid(grid["id"])
    filled = db.get_grid_levels(grid["id"])[0]
    db.set_level_fields(grid["id"], 0, entry_price=None, bought_at=None)
    legacy_updated_at = db.get_grid_levels(grid["id"])[0]["updated_at"]
    engine.sync_grid(grid["id"])
    recovered = db.get_grid_levels(grid["id"])[0]
    assert recovered["entry_price"] == pytest.approx(90)
    assert recovered["bought_at"] == legacy_updated_at
    assert "fill time estimated from updated_at" in caplog.text
