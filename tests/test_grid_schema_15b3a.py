import sqlite3

from database.db_manager import DBManager
from tests.test_grid_schema_15b import _legacy_15a


def test_compound_columns_migrate_idempotently_and_keep_legacy_capital(tmp_path):
    path = tmp_path / "grid-15b2.db"
    _legacy_15a(path)
    first = DBManager(f"sqlite:///{path}")
    before = first.get_grid_levels(1)[0]
    assert before["capital"] == 50.0
    assert before["capital_base"] == 50.0
    assert before["capital_compound"] == 0.0
    DBManager(f"sqlite:///{path}")
    with sqlite3.connect(path) as conn:
        columns = {row[1]: row for row in conn.execute("PRAGMA table_info(grid_levels)")}
        assert columns["capital_base"][2] == "FLOAT"
        assert columns["capital_compound"][2] == "FLOAT"
        assert columns["capital_compound"][3] == 1
        assert columns["capital_compound"][4] == "0"
        assert conn.execute("SELECT capital, capital_base, capital_compound FROM grid_levels WHERE grid_id=1").fetchone() == (50.0, None, 0.0)


def test_new_grid_levels_start_with_base_capital_and_zero_compound():
    from grid.engine import GridEngine
    from tests.test_grid_engine import make_engine
    engine, db, _exchange = make_engine()
    grid = engine.create_grid("XRPUSDT", 80, 120, 5, capital=1000, strategy="smart")
    for row in db.get_grid_levels(grid["id"]):
        assert row["capital_base"] == row["capital"]
        assert row["capital_compound"] == 0


def test_legacy_null_base_is_read_as_effective_capital_minus_compound():
    from tests.test_grid_engine import create, make_engine
    engine, db, _exchange = make_engine()
    grid = create(engine, strategy="smart")
    level = db.get_grid_levels(grid["id"])[0]
    db.update_level(grid["id"], 0, capital_base=None, capital_compound=12.5, capital=112.5)
    level = db.get_grid_levels(grid["id"])[0]
    assert level["capital_base"] == 100.0


def test_repository_move_copies_both_compound_columns():
    from tests.test_grid_engine import create, make_engine
    engine, db, _exchange = make_engine()
    grid = create(engine, strategy="smart")
    db.update_level(grid["id"], 0, capital=125.0, capital_base=100.0, capital_compound=25.0)
    repo_id = db.create_grid_with_levels(
        {"symbol": "XRPUSDT", "range_low": 90, "range_high": 100, "n_levels": 0,
         "capital_total": 0, "status": "HOLDING", "strategy": "repository"}, [],
    )["id"]
    moved = db.move_level_to_grid(grid["id"], 0, repo_id, 0)
    assert moved["capital"] == 125.0
    assert moved["capital_base"] == 100.0
    assert moved["capital_compound"] == 25.0
