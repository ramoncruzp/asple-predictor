from decimal import Decimal
import sqlite3

from database.db_manager import DBManager
from tests.test_grid_engine import create, make_engine


def test_loan_schema_has_only_approved_reserve_and_loan_fields_and_is_idempotent(tmp_path):
    url = f"sqlite:///{tmp_path / 'grid-loans-schema.db'}"
    db = DBManager(url)
    assert {"reserve"} <= set(db.grids.c.keys())
    assert {"capital_loan"} <= set(db.grid_levels.c.keys())
    assert set(db.grid_loans.c.keys()) == {
        "id", "grid_id", "lender_idx", "borrower_idx", "amount", "reserve_part",
        "status", "created_at", "updated_at", "closed_at", "close_reason",
        "lender_cycles_at_open", "borrower_cycles_at_open", "plan", "details",
    }
    assert "ix_grid_loans_grid_status" in {
        index.name for index in db.grid_loans.indexes
    }
    db2 = DBManager(url)
    assert {"reserve"} <= set(db2.grids.c.keys())
    assert {"capital_loan"} <= set(db2.grid_levels.c.keys())


def test_migration_adds_loan_columns_to_existing_15b3a_database_and_keeps_data(tmp_path):
    path = tmp_path / "legacy-15b3a.db"
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE grids (id INTEGER PRIMARY KEY, symbol VARCHAR NOT NULL, range_low FLOAT NOT NULL, "
            "range_high FLOAT NOT NULL, n_levels INTEGER NOT NULL, capital_total FLOAT NOT NULL, "
            "status VARCHAR NOT NULL, created_at DATETIME NOT NULL, environment VARCHAR NOT NULL, "
            "open_price FLOAT, closed_at DATETIME, fail_reason VARCHAR, strategy VARCHAR, params TEXT)"
        )
        conn.execute("INSERT INTO grids(id,symbol,range_low,range_high,n_levels,capital_total,status,created_at,environment,strategy) "
                     "VALUES (11,'XRPUSDT',1,2,4,123.5,'ACTIVE','2026-01-01','testnet','smart')")
        conn.execute(
            "CREATE TABLE grid_levels (grid_id INTEGER, level_idx INTEGER, price FLOAT NOT NULL, capital FLOAT NOT NULL, "
            "capital_base FLOAT, capital_compound FLOAT NOT NULL DEFAULT 0, order_id INTEGER, state VARCHAR NOT NULL, "
            "cycles_completed INTEGER NOT NULL DEFAULT 0, pnl FLOAT NOT NULL DEFAULT 0, sell_price FLOAT, "
            "entry_price FLOAT, bought_at DATETIME, stop_loss_pct FLOAT, held_qty FLOAT NOT NULL DEFAULT 0, "
            "client_order_id VARCHAR, buy_client_order_id VARCHAR, fee_paid FLOAT NOT NULL DEFAULT 0, updated_at DATETIME, "
            "PRIMARY KEY(grid_id,level_idx))"
        )
        conn.execute("INSERT INTO grid_levels(grid_id,level_idx,price,capital,state,updated_at) "
                     "VALUES (11,2,1.5,123.5,'IDLE','2026-01-01')")
    db = DBManager(f"sqlite:///{path}")
    db2 = DBManager(f"sqlite:///{path}")
    with db2.engine.connect() as conn:
        grid = conn.exec_driver_sql("SELECT id,status,reserve FROM grids WHERE id=11").one()
        level = conn.exec_driver_sql(
            "SELECT grid_id,level_idx,capital,capital_loan FROM grid_levels WHERE grid_id=11"
        ).one()
    assert tuple(grid) == (11, "ACTIVE", 0.0)
    assert tuple(level) == (11, 2, 123.5, 0.0)


def test_smart_grid_reserve_reduces_cell_base_capital_and_keeps_total():
    engine, db, exchange = make_engine(fee_rate="0", fee_asset="USDT")
    grid = create(
        engine, capital=1000, strategy="smart",
        params={"loans_enabled": True, "reserve_pct": 10.0},
    )
    levels = db.get_grid_levels(grid["id"])
    assert grid["capital_total"] == 1000
    assert grid["reserve"] == 100
    assert sum(Decimal(str(row["capital_base"])) for row in levels) == Decimal("900")
    for row in levels:
        assert Decimal(str(row["capital"])) == Decimal(str(row["capital_base"]))
        assert row["capital_loan"] == 0
        if row["state"] == "BUY_OPEN":
            order = exchange.get_order("XRPUSDT", order_id=row["order_id"])
            assert order["quantity"] == exchange.filters.round_qty_down(
                Decimal(str(row["capital_base"])) / Decimal(str(row["price"]))
            )


def test_zero_reserve_preserves_existing_cell_allocation():
    engine, db, _exchange = make_engine(fee_rate="0", fee_asset="USDT")
    grid = create(
        engine, capital=1000, strategy="smart",
        params={"loans_enabled": True, "reserve_pct": 0.0},
    )
    levels = db.get_grid_levels(grid["id"])
    assert grid["reserve"] == 0
    assert sum(Decimal(str(row["capital_base"])) for row in levels) == Decimal("1000")


def test_simple_grid_retains_zero_reserve_and_zero_loan_capital():
    engine, db, _exchange = make_engine(fee_rate="0", fee_asset="USDT")
    grid = create(engine, capital=1000)
    levels = db.get_grid_levels(grid["id"])
    assert grid["reserve"] == 0
    assert sum(Decimal(str(row["capital_base"])) for row in levels) == Decimal("1000")
    assert all(row["capital_loan"] == 0 for row in levels)
