from __future__ import annotations

from grid.engine import GridEngine
from tests.test_grid_engine import create, make_engine


def test_pause_cancels_buys_preserves_sell_and_never_rebuys_until_resume():
    engine, db, exchange = make_engine(fee_rate="0", fee_asset="USDT")
    grid = create(engine)
    buy = exchange.get_open_orders("XRPUSDT")[0]
    exchange.fill(buy["order_id"])
    engine.sync_grid(grid["id"])
    levels = db.get_grid_levels(grid["id"])
    sell_level = next(row for row in levels if row["state"] == "SELL_OPEN")
    original_sell_id = sell_level["order_id"]
    result = engine.pause_grid(grid["id"], "manual_test", {"reasons": ["break_prob"]})
    assert result["ok"] and db.get_grid(grid["id"])["status"] == "PAUSED"
    assert exchange.get_order("XRPUSDT", order_id=original_sell_id)["status"] == "NEW"
    assert all(row["side"] == "SELL" for row in exchange.get_open_orders("XRPUSDT"))
    creates = len(exchange.create_calls)
    engine.sync_paused(grid["id"])
    assert len(exchange.create_calls) == creates
    exchange.fill(original_sell_id)
    engine.sync_paused(grid["id"])
    assert db.get_grid_levels(grid["id"])[sell_level["level_idx"]]["state"] == "IDLE"
    assert all(row["side"] == "SELL" for row in exchange.get_open_orders("XRPUSDT"))
    assert engine.resume_grid(grid["id"], "test_clear", {})["ok"]
    engine.sync_grid(grid["id"])
    assert any(row["side"] == "BUY" for row in exchange.get_open_orders("XRPUSDT"))


def test_pause_resolves_unsent_buy_intent_without_sending_order():
    engine, db, exchange = make_engine(fee_rate="0", fee_asset="USDT")
    grid = create(engine)
    cell = db.get_grid_levels(grid["id"])[-1]
    old_count = len(exchange.create_calls)
    db.update_level(grid["id"], cell["level_idx"], state="BUY_OPEN", order_id=None,
                    client_order_id=f"g{grid['id']}L{cell['level_idx']}B91")
    engine.pause_grid(grid["id"], "test", {})
    result = next(row for row in db.get_grid_levels(grid["id"]) if row["level_idx"] == cell["level_idx"])
    assert result["state"] == "IDLE" and result["order_id"] is None and result["client_order_id"] is None
    assert len(exchange.create_calls) == old_count


def test_buy_filled_during_pause_cancel_is_settled_with_sell_and_no_rebuy():
    engine, db, exchange = make_engine(fee_rate="0", fee_asset="USDT")
    grid = create(engine)
    buy = exchange.get_open_orders("XRPUSDT")[0]
    original_cancel = exchange.cancel_order
    def fill_during_cancel(symbol, order_id):
        if int(order_id) == int(buy["order_id"]):
            exchange.fill(order_id)
            return exchange.get_order(symbol, order_id=order_id)
        return original_cancel(symbol, order_id)
    exchange.cancel_order = fill_during_cancel
    engine.pause_grid(grid["id"], "race_test", {})
    cell = db.get_grid_levels(grid["id"])[0]
    assert cell["state"] == "SELL_OPEN" and cell["held_qty"] > 0
    assert all(row["side"] == "SELL" for row in exchange.get_open_orders("XRPUSDT"))


def test_pause_atomic_transition_rejects_repeated_pause_and_resume_wrong_state():
    engine, db, _exchange = make_engine()
    grid = create(engine)
    assert engine.pause_grid(grid["id"], "one", {})["ok"]
    assert not engine.pause_grid(grid["id"], "two", {})["ok"]
    assert engine.resume_grid(grid["id"], "resume", {})["ok"]
    assert not engine.resume_grid(grid["id"], "again", {})["ok"]
