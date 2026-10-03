from __future__ import annotations

import pytest
from decimal import Decimal

from tests.test_grid_engine import create, make_engine


def test_close_grid_cancel_transitions_atomically_and_reports_inventory():
    engine, db, exchange = make_engine(fee_rate="0", fee_asset="USDT")
    grid = create(engine)
    buy = exchange.get_open_orders("XRPUSDT")[-1]
    exchange.fill(buy["order_id"])
    result = engine.close_grid(grid["id"], "cancel")
    assert result["status"] == "CANCELLED"
    assert result["mode"] == "cancel"
    assert result["unmanaged_inventory"]
    assert not exchange.get_open_orders("XRPUSDT")


def test_close_grid_liquidate_sells_only_owned_held_quantity_and_is_idempotent():
    engine, db, exchange = make_engine(fee_rate="0", fee_asset="USDT")
    grid = create(engine)
    buy = exchange.get_open_orders("XRPUSDT")[-1]
    exchange.fill(buy["order_id"])
    result = engine.close_grid(grid["id"], "liquidate")
    level = db.get_grid_levels(grid["id"])[2]
    assert result["status"] == "CLOSED"
    assert result["liquidated_cells"]
    assert level["state"] == "DONE" and level["held_qty"] == 0
    assert db.get_grid(grid["id"])["status"] == "CLOSED"
    assert not exchange.get_open_orders("XRPUSDT")


def test_close_grid_liquidate_sells_partial_buy_canceled_with_error_state():
    engine, db, exchange = make_engine(fee_rate="0", fee_asset="USDT")
    grid = create(engine)
    buy = next(row for row in exchange.get_open_orders("XRPUSDT") if row["side"] == "BUY")
    exchange.fill(buy["order_id"], partial=True)
    result = engine.close_grid(grid["id"], "liquidate")
    level_idx = int(buy["client_order_id"].split("L", 1)[1].split("B", 1)[0])
    level = db.get_grid_levels(grid["id"])[level_idx]
    market_sells = [row for row in exchange.orders.values()
                    if row.get("type") == "MARKET" and row.get("side") == "SELL"]
    assert result["status"] == "CLOSED"
    assert result["liquidated_cells"]
    assert len(market_sells) == 1
    assert market_sells[0]["quantity"] == buy["quantity"] / 2
    assert level["state"] == "DONE" and Decimal(str(level["held_qty"])) == 0
    assert not result.get("unmanaged_inventory")


def test_close_grid_rejects_invalid_mode_and_repeated_closed_grid():
    engine, _, _ = make_engine()
    grid = create(engine)
    with pytest.raises(ValueError, match="mode"):
        engine.close_grid(grid["id"], "auto")


def test_liquidate_retry_recovers_accepted_market_order_without_duplicate():
    engine, db, exchange = make_engine(fee_rate="0", fee_asset="USDT")
    grid = create(engine)
    buy = next(order for order in exchange.get_open_orders("XRPUSDT") if order["client_order_id"] == "g1L2B0")
    exchange.fill(buy["order_id"])
    original_place = exchange.place_order
    lost_market_response = {"done": False}
    def lose_market_response_once(symbol, side, quantity, price=None, order_type="LIMIT", client_order_id=None):
        order = original_place(symbol, side, quantity, price, order_type, client_order_id)
        if order_type == "MARKET" and not lost_market_response["done"]:
            lost_market_response["done"] = True
            raise RuntimeError("simulated lost market response")
        return order
    exchange.place_order = lose_market_response_once

    first = engine.close_grid(grid["id"], "liquidate")
    assert first["status"] == "CLOSING"
    assert db.get_grid_levels(grid["id"])[2]["held_qty"] > 0
    accepted = [order for order in exchange.orders_by_client if order.endswith("X0")]
    assert accepted == ["g1L2X0"]

    second = engine.close_grid(grid["id"], "liquidate")
    assert second["status"] == "CLOSED"
    assert [order for order in exchange.orders_by_client if order.endswith("X0")] == ["g1L2X0"]
    assert db.get_grid_levels(grid["id"])[2]["state"] == "DONE"


def test_liquidate_dust_is_reported_and_retains_owned_quantity():
    engine, db, exchange = make_engine(fee_rate="0", fee_asset="USDT")
    grid = create(engine)
    db.update_level(grid["id"], 2, state="ERROR", order_id=None, client_order_id="g1L2B0", held_qty=0.04)

    result = engine.close_grid(grid["id"], "liquidate")

    cell = db.get_grid_levels(grid["id"])[2]
    assert result["status"] == "CLOSED"
    assert result["dust_cells"][0]["status"] == "DUST"
    assert cell["state"] == "DONE" and cell["held_qty"] == 0.04
    assert db.list_grid_events(grid_id=grid["id"], event_type="CELL_DUST")
    assert not any(order[1] == "SELL" for order in exchange.create_calls)


def test_liquidate_uses_only_owned_quantity_after_base_asset_buy_fee():
    engine, db, exchange = make_engine(fee_rate="0.001", fee_asset="XRP")
    grid = create(engine)
    buy = next(order for order in exchange.get_open_orders("XRPUSDT") if order["client_order_id"] == "g1L2B0")
    exchange.fill(buy["order_id"])

    result = engine.close_grid(grid["id"], "liquidate")

    market_sell = exchange.get_order("XRPUSDT", client_order_id="g1L2X0")
    assert result["status"] == "CLOSED"
    assert market_sell["quantity"] == Decimal("1.9")
    assert db.get_grid_levels(grid["id"])[2]["held_qty"] == 0


def test_repository_reports_unmanaged_inventory_and_reuses_existing_symbol_repository():
    engine, db, exchange = make_engine(fee_rate="0", fee_asset="USDT")
    first = create(engine)
    buy = next(order for order in exchange.get_open_orders("XRPUSDT") if order["client_order_id"] == "g1L2B0")
    exchange.fill(buy["order_id"])
    engine.sync_grid(first["id"])
    db.update_level(first["id"], 3, state="ERROR", held_qty=0.5, order_id=None, client_order_id=None)

    closed_first = engine.close_grid(first["id"], "repository")

    assert closed_first["status"] == "CLOSED"
    assert closed_first["unmanaged_inventory"] == [{"level_idx": 3, "held_qty": 0.5}]
    assert "unmanaged inventory" in db.get_grid(first["id"])["fail_reason"]
    repository_id = closed_first["repository_grid_id"]
    assert len(db.get_grid_levels(repository_id)) == 1

    exchange.move_price(120, 120.01, 120)
    second = engine.create_grid("XRPUSDT", 110, 130, 5, capital=1000)
    buy2 = next(order for order in exchange.get_open_orders("XRPUSDT") if order["client_order_id"] == f"g{second['id']}L2B0")
    exchange.fill(buy2["order_id"])
    engine.sync_grid(second["id"])
    previous_cell = db.get_grid_levels(repository_id)[0]
    moved = engine.close_grid(second["id"], "repository")

    assert moved["status"] == "CLOSED"
    assert moved["repository_grid_id"] == repository_id
    assert db.get_grid(repository_id)["status"] == "HOLDING"
    assert len(db.get_grid_levels(repository_id)) == 2
    preserved = next(row for row in db.get_grid_levels(repository_id) if row["order_id"] == previous_cell["order_id"])
    assert preserved["client_order_id"] == previous_cell["client_order_id"]


def _grid_with_held_inventory():
    engine, db, exchange = make_engine(fee_rate="0", fee_asset="USDT")
    grid = create(engine)
    buy = next(order for order in exchange.get_open_orders("XRPUSDT") if order["client_order_id"] == "g1L2B0")
    exchange.fill(buy["order_id"])
    return engine, db, exchange, grid


@pytest.mark.parametrize("failure_point", ["process_kill", "market_context"])
def test_liquidate_failure_after_cancel_stays_retryable(failure_point):
    engine, db, exchange, grid = _grid_with_held_inventory()
    real_market_context = engine._market_context
    real_market_sell = engine._market_sell_owned_cell
    if failure_point == "process_kill":
        engine._market_sell_owned_cell = lambda *args, **kwargs: (_ for _ in ()).throw(KeyboardInterrupt("simulated process kill after cancel"))
        expected = KeyboardInterrupt
    else:
        canceled = {"value": False}
        real_cancel = engine.cancel_grid_orders
        def cancel_then_flag(grid_id, **kwargs):
            result = real_cancel(grid_id, **kwargs)
            canceled["value"] = True
            return result
        def fail_after_cancel(symbol):
            if canceled["value"]:
                canceled["value"] = False
                raise RuntimeError("simulated network failure after cancel")
            return real_market_context(symbol)
        engine.cancel_grid_orders = cancel_then_flag
        engine._market_context = fail_after_cancel
        expected = RuntimeError

    with pytest.raises(expected):
        engine.close_grid(grid["id"], "liquidate")

    assert db.get_grid(grid["id"])["status"] == "CLOSING"
    assert db.has_open_grid("XRPUSDT") is True
    engine._market_sell_owned_cell = real_market_sell
    if failure_point == "market_context":
        engine.cancel_grid_orders = real_cancel
    engine._market_context = real_market_context
    result = engine.close_grid(grid["id"], "liquidate")
    assert result["status"] == "CLOSED"
    assert db.get_grid_levels(grid["id"])[2]["state"] == "DONE"
    assert [cid for cid in exchange.orders_by_client if cid.endswith("X0")] == ["g1L2X0"]


def test_repository_close_retries_transient_live_sell_lookup_failure():
    engine, db, exchange = make_engine(fee_rate="0", fee_asset="USDT")
    grid = create(engine)
    buy = next(order for order in exchange.get_open_orders("XRPUSDT") if order["client_order_id"] == "g1L2B0")
    exchange.fill(buy["order_id"])
    engine.sync_grid(grid["id"])
    original_get_order = exchange.get_order
    fail_once = {"value": True}
    def transient_get_order(symbol, order_id=None, client_order_id=None):
        if order_id is not None and fail_once["value"]:
            fail_once["value"] = False
            raise RuntimeError("temporary order lookup failure")
        return original_get_order(symbol, order_id=order_id, client_order_id=client_order_id)
    exchange.get_order = transient_get_order

    first = engine.close_grid(grid["id"], "repository")

    assert first["status"] == "CLOSING"
    assert first["errors"]
    assert db.get_grid(grid["id"])["status"] == "CLOSING"
    assert db.get_grid_levels(grid["id"])[2]["grid_id"] == grid["id"]
    assert db.list_grids_by_status({"HOLDING"}) == []
    second = engine.close_grid(grid["id"], "repository")
    assert second["status"] == "CLOSED"
    assert second["moved_cells"]
    assert db.get_grid_levels(second["repository_grid_id"])[0]["held_qty"] > 0


def test_close_liquidation_does_not_increment_completed_cycles():
    engine, db, exchange, grid = _grid_with_held_inventory()
    engine.sync_grid(grid["id"])
    before = db.get_grid_levels(grid["id"])[2]
    assert before["cycles_completed"] == 0

    result = engine.close_grid(grid["id"], "liquidate")

    cell = db.get_grid_levels(grid["id"])[2]
    liquidation = result["liquidated_cells"][0]
    event = db.list_grid_events(grid_id=grid["id"], event_type="CELL_LIQUIDATED")[0]
    assert result.get("cycles_completed", 0) == 0
    assert cell["state"] == "DONE" and cell["cycles_completed"] == 0
    assert liquidation["cycles_completed"] == 0
    assert cell["pnl"] == pytest.approx(float(liquidation["cycle_pnl"]))
    assert event["details"]["cycle_pnl"] == liquidation["cycle_pnl"]
