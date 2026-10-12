from grid import status_view as sv


def test_cells_view_has_live_order_requires_open_database_state_and_order_id():
    levels = [
        {"level_idx": 0, "price": 100.0, "sell_price": 101.0, "capital": 50.0,
         "state": "BUY_OPEN", "order_id": 73, "held_qty": 0.0},
        {"level_idx": 1, "price": 99.0, "sell_price": 100.0, "capital": 50.0,
         "state": "BUY_OPEN", "order_id": None, "client_order_id": "buy-intent-1", "held_qty": 0.0},
        {"level_idx": 2, "price": 98.0, "sell_price": 99.0, "capital": 50.0,
         "state": "IDLE", "order_id": 88, "held_qty": 0.0},
    ]

    rows = {row["level_idx"]: row for row in sv.cells_view(levels, 100.0)["rows"]
            if "level_idx" in row}

    assert rows[0]["has_live_order"] is True
    assert rows[1]["has_live_order"] is False
    assert rows[2]["has_live_order"] is False
