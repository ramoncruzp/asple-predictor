from __future__ import annotations

from decimal import Decimal

import pytest

from tests.test_grid_engine import create, make_engine


def _filled_cell(engine, db, exchange, status="ACTIVE"):
    grid = create(engine)
    buy = exchange.get_open_orders("XRPUSDT")[0]
    exchange.fill(buy["order_id"])
    engine.sync_grid(grid["id"])
    level = next(row for row in db.get_grid_levels(grid["id"]) if row["state"] == "SELL_OPEN")
    db.update_level(grid["id"], level["level_idx"], entry_price=110, stop_loss_pct=5)
    if status != "ACTIVE":
        db.update_grid(grid["id"], status=status)
    return grid, level


@pytest.mark.parametrize("status", ["ACTIVE", "PAUSED", "HOLDING"])
def test_stoploss_sells_only_owned_cell_marks_done_and_does_not_count_cycle(status):
    engine, db, exchange = make_engine(fee_rate="0", fee_asset="USDT")
    grid, level = _filled_cell(engine, db, exchange, status=status)
    before = int(level["cycles_completed"])
    result = engine.stoploss_cell(grid["id"], level["level_idx"], "stop_loss_pct", {"reason": "unit"})
    updated = next(row for row in db.get_grid_levels(grid["id"]) if row["level_idx"] == level["level_idx"])
    assert result["ok"] and updated["state"] == "DONE"
    assert Decimal(result["qty"]) == Decimal(str(level["held_qty"]))
    assert updated["cycles_completed"] == before
    event = db.get_last_event(grid["id"], "CELL_STOPLOSS")
    assert event["details"]["entry_price"] == 110 and event["details"]["stop_loss_pct"] == 5


def test_stoploss_does_not_market_sell_if_limit_sell_filled_during_cancel():
    engine, db, exchange = make_engine(fee_rate="0", fee_asset="USDT")
    grid, level = _filled_cell(engine, db, exchange)
    before_market_sells = sum(row["type"] == "MARKET" and row["side"] == "SELL" for row in exchange.orders.values())
    exchange.fill(level["order_id"])
    outcome = engine.stoploss_cell(grid["id"], level["level_idx"], "stop", {})
    assert outcome["status"] == "SELL_FILLED"
    after_market_sells = sum(row["type"] == "MARKET" and row["side"] == "SELL" for row in exchange.orders.values())
    assert after_market_sells == before_market_sells
    assert db.get_last_event(grid["id"], "CELL_STOPLOSS") is None


def test_stoploss_retries_idempotently_after_market_order_was_accepted():
    engine, db, exchange = make_engine(fee_rate="0", fee_asset="USDT")
    grid, level = _filled_cell(engine, db, exchange)
    original = exchange.place_order
    failed_once = False
    def lose_response(*args, **kwargs):
        nonlocal failed_once
        result = original(*args, **kwargs)
        if kwargs.get("order_type") == "MARKET" and not failed_once:
            failed_once = True
            raise RuntimeError("accepted response lost")
        return result
    exchange.place_order = lose_response
    first = engine.stoploss_cell(grid["id"], level["level_idx"], "stop", {})
    assert not first["ok"]
    # The next call sees the deterministic X client id and settles that one order.
    second = engine.stoploss_cell(grid["id"], level["level_idx"], "stop", {})
    assert second["ok"]
    market_orders = [row for row in exchange.orders.values() if row["type"] == "MARKET" and row["side"] == "SELL"]
    assert len(market_orders) == 1


def _interrupt_stoploss(engine, exchange, grid, level):
    original = exchange.place_order

    def fail_market_sell(*args, **kwargs):
        if kwargs.get("order_type") == "MARKET":
            raise RuntimeError("injected market failure")
        return original(*args, **kwargs)

    exchange.place_order = fail_market_sell
    result = engine.stoploss_cell(grid["id"], level["level_idx"], "stop_loss_pct", {})
    exchange.place_order = original
    assert not result["ok"]
    assert exchange.get_order("XRPUSDT", order_id=level["order_id"])["status"] == "CANCELED"


def _run_recovery_monitor(engine, db, exchange):
    from grid.monitor import GridMonitor
    from tests.test_grid_monitor import monitor_settings

    monitor = GridMonitor(db, exchange, engine, monitor_settings())
    monitor.run_once()


@pytest.mark.parametrize("price, expected_state", [(110, "SELL_OPEN"), (100, "DONE")])
def test_interrupted_stoploss_active_is_retried_or_reprotected(price, expected_state):
    engine, db, exchange = make_engine(fee_rate="0", fee_asset="USDT")
    grid, level = _filled_cell(engine, db, exchange)
    _interrupt_stoploss(engine, exchange, grid, level)
    exchange.move_price(price, price + 0.01, price)

    _run_recovery_monitor(engine, db, exchange)

    updated = db.get_grid_levels(grid["id"])[level["level_idx"]]
    assert updated["state"] == expected_state
    if expected_state == "SELL_OPEN":
        assert updated["held_qty"] == level["held_qty"]
        assert updated["order_id"] != level["order_id"]
        assert updated["client_order_id"] != level["client_order_id"]
        assert exchange.find_order_by_client_id("XRPUSDT", level["client_order_id"])["status"] == "CANCELED"
        assert exchange.find_order_by_client_id("XRPUSDT", updated["client_order_id"])["order_id"] == updated["order_id"]
        live = [row for row in exchange.get_open_orders("XRPUSDT") if row["order_id"] == updated["order_id"]]
        assert len(live) == 1 and live[0]["side"] == "SELL"
        assert live[0]["quantity"] == Decimal(str(level["held_qty"]))
    else:
        markets = [row for row in exchange.orders.values() if row["type"] == "MARKET" and row["side"] == "SELL"]
        assert len(markets) == 1
        assert Decimal(str(markets[0]["quantity"])) == Decimal(str(level["held_qty"]))


def test_interrupted_stoploss_paused_inventory_is_protected_after_resume():
    engine, db, exchange = make_engine(fee_rate="0", fee_asset="USDT")
    grid, level = _filled_cell(engine, db, exchange, status="PAUSED")
    _interrupt_stoploss(engine, exchange, grid, level)
    exchange.move_price(110, 110.01, 110)

    _run_recovery_monitor(engine, db, exchange)
    engine.resume_grid(grid["id"], "test", {})
    engine.sync_grid(grid["id"])

    updated = db.get_grid_levels(grid["id"])[level["level_idx"]]
    assert updated["state"] == "SELL_OPEN" and updated["held_qty"] == level["held_qty"]
    assert updated["order_id"] != level["order_id"]
    assert exchange.get_order("XRPUSDT", order_id=updated["order_id"])["side"] == "SELL"


def test_interrupted_stoploss_holding_inventory_is_reprotected():
    engine, db, exchange = make_engine(fee_rate="0", fee_asset="USDT")
    origin, _ = _filled_cell(engine, db, exchange)
    moved = engine.close_grid(origin["id"], "repository")
    grid_id = moved["repository_grid_id"]
    grid = db.get_grid(grid_id)
    level = db.get_grid_levels(grid_id)[0]
    _interrupt_stoploss(engine, exchange, grid, level)
    exchange.move_price(110, 110.01, 110)

    _run_recovery_monitor(engine, db, exchange)

    updated = db.get_grid_levels(grid_id)[level["level_idx"]]
    assert updated["state"] == "SELL_OPEN" and updated["held_qty"] == level["held_qty"]
    assert updated["order_id"] != level["order_id"]
    assert updated["client_order_id"] != level["client_order_id"]
    assert exchange.get_order("XRPUSDT", order_id=updated["order_id"])["side"] == "SELL"


def test_interrupted_stoploss_reprotection_write_ahead_retries_without_duplicate():
    engine, db, exchange = make_engine(fee_rate="0", fee_asset="USDT")
    grid, level = _filled_cell(engine, db, exchange)
    _interrupt_stoploss(engine, exchange, grid, level)
    exchange.move_price(110, 110.01, 110)
    original = exchange.place_order

    def fail_recovery_limit(*args, **kwargs):
        if len(args) > 4 and args[4] == "LIMIT" and args[1] == "SELL" \
                and kwargs.get("client_order_id") != level["client_order_id"]:
            raise RuntimeError("injected recovery placement failure")
        return original(*args, **kwargs)

    exchange.place_order = fail_recovery_limit
    _run_recovery_monitor(engine, db, exchange)
    pending = db.get_grid_levels(grid["id"])[level["level_idx"]]
    assert pending["state"] == "SELL_OPEN" and pending["held_qty"] == level["held_qty"]
    assert pending["order_id"] is None and pending["client_order_id"] != level["client_order_id"]

    from data.testnet_client import TestnetOrderError

    def reject_recovery_limit(*args, **kwargs):
        if len(args) > 4 and args[4] == "LIMIT" and args[1] == "SELL":
            raise TestnetOrderError(-2010, "injected temporary rejection")
        return original(*args, **kwargs)

    exchange.place_order = reject_recovery_limit
    _run_recovery_monitor(engine, db, exchange)
    still_pending = db.get_grid_levels(grid["id"])[level["level_idx"]]
    assert still_pending["state"] == "SELL_OPEN" and still_pending["held_qty"] == level["held_qty"]
    assert still_pending["order_id"] is None and still_pending["client_order_id"] == pending["client_order_id"]

    exchange.place_order = original
    _run_recovery_monitor(engine, db, exchange)
    recovered = db.get_grid_levels(grid["id"])[level["level_idx"]]
    assert recovered["state"] == "SELL_OPEN" and recovered["held_qty"] == level["held_qty"]
    assert exchange.find_order_by_client_id("XRPUSDT", recovered["client_order_id"])["order_id"] == recovered["order_id"]
    assert len([row for row in exchange.get_open_orders("XRPUSDT") if row["side"] == "SELL"]) == 1
