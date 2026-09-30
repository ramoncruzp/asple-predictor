import sqlite3

from database.db_manager import DBManager
from tests.test_grid_schema_15b import _legacy_15a


def test_buy_client_order_id_migration_is_idempotent_and_preserves_rows(tmp_path):
    path = tmp_path / "grid-15b1.db"
    _legacy_15a(path)
    first = DBManager(f"sqlite:///{path}")
    first_level = first.get_grid_levels(1)[0]
    assert first_level["price"] == 95 and first_level["buy_client_order_id"] is None
    DBManager(f"sqlite:///{path}")
    with sqlite3.connect(path) as conn:
        names = {row[1] for row in conn.execute("PRAGMA table_info(grid_levels)")}
        assert "buy_client_order_id" in names
        assert conn.execute("SELECT price, state FROM grid_levels WHERE grid_id=1").fetchone() == (95.0, "IDLE")
