from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import pytest

from database.db_manager import DBManager
from grid.engine import GridCreationError, GridEngine
from grid.levels import GridConfigError
from tests.grid_fakes import FakeExchange


def make_engine(*, fee_rate="0.001", fee_asset="XRP"):
    db = DBManager("sqlite:///:memory:")
    db.add_or_reactivate_coin("XRPUSDT")
    exchange = FakeExchange(fee_rate=fee_rate, fee_asset=fee_asset)
    settings = SimpleNamespace(
        usdt_por_grid=1000.0, max_grids_simultaneos=5,
        capital_max_por_nivel_pct=0.30, grid_min_step_pct=0.003,
    )
    return GridEngine(db, exchange, settings), db, exchange


def create(engine, **overrides):
    return engine.create_grid("XRPUSDT", 90, 110, 5, **overrides)


def test_create_grid_places_only_buys_below_exchange_bid():
    engine, db, exchange = make_engine()
    grid = create(engine)
    levels = db.get_grid_levels(grid["id"])
    assert grid["status"] == "ACTIVE"
    assert [row["state"] for row in levels] == ["BUY_OPEN", "BUY_OPEN", "BUY_OPEN", "IDLE", "IDLE"]
    assert len(exchange.get_open_orders("XRPUSDT")) == 3
    assert all(order["side"] == "BUY" for order in exchange.get_open_orders("XRPUSDT"))


def test_grid_capital_default_is_copied_and_simultaneous_limit_is_enforced():
    engine, db, exchange = make_engine()
    engine.settings.max_grids_simultaneos = 1
    first = engine.create_grid("XRPUSDT", 90, 110, 5)
    assert db.get_grid(first["id"])["capital_total"] == 1000
    engine.settings.usdt_por_grid = 1500
    try:
        engine.create_grid("XRPUSDT", 90, 110, 5)
    except GridConfigError as exc:
        assert "maximum simultaneous" in str(exc)
    else:
        raise AssertionError("expected simultaneous grid cap")
    assert db.get_grid(first["id"])["capital_total"] == 1000
    engine.cancel_grid_orders(first["id"])
    second = engine.create_grid("XRPUSDT", 90, 110, 5)
    assert db.get_grid(second["id"])["capital_total"] == 1500


def test_filled_buy_places_sell_for_net_base_quantity():
    engine, db, exchange = make_engine()
    grid = create(engine)
    buy = exchange.get_open_orders("XRPUSDT")[-1]
    exchange.fill(buy["order_id"])
    summary = engine.sync_grid(grid["id"])
    level = db.get_grid_levels(grid["id"])[2]
    sell = exchange.get_order("XRPUSDT", order_id=level["order_id"])
    assert summary["buys_filled"] == 1
    assert level["state"] == "SELL_OPEN"
    assert level["held_qty"] == 1.9
    assert sell["side"] == "SELL"
    assert sell["quantity"] == Decimal("1.9")
    assert sell["price"] == Decimal("102")


def test_filled_sell_computes_cycle_pnl_and_rearms_buy():
    engine, db, exchange = make_engine()
    grid = create(engine)
    buy = exchange.get_open_orders("XRPUSDT")[-1]
    exchange.fill(buy["order_id"])
    engine.sync_grid(grid["id"])
    sell_level = db.get_grid_levels(grid["id"])[2]
    sell = exchange.get_order("XRPUSDT", order_id=sell_level["order_id"])
    exchange.fill(sell["order_id"])
    summary = engine.sync_grid(grid["id"])
    level = db.get_grid_levels(grid["id"])[2]
    expected = Decimal("102") * Decimal("1.9") - Decimal("98") * Decimal("2.0")
    expected -= Decimal("0.002") * Decimal("98") + Decimal("0.0019") * Decimal("102")
    assert summary["cycles_completed"] == 1
    assert level["cycles_completed"] == 1
    assert abs(Decimal(str(level["pnl"])) - expected) < Decimal("1e-10")
    assert level["state"] == "BUY_OPEN"
    assert level["client_order_id"] == "g1L2B1"


def test_sync_is_idempotent_when_market_and_open_orders_are_unchanged():
    engine, db, exchange = make_engine()
    grid = create(engine)
    before = len(exchange.create_calls)
    engine.sync_grid(grid["id"])
    engine.sync_grid(grid["id"])
    assert len(exchange.create_calls) == before


def test_lost_response_intent_recovers_order_by_client_id_without_duplicate():
    engine, db, exchange = make_engine()
    grid = create(engine)
    level = db.get_grid_levels(grid["id"])[3]
    cid = "g1L3B0"
    exchange.lose_next_response = True
    try:
        exchange.place_order(
            "XRPUSDT", "BUY", Decimal("1.9"), Decimal("102"),
            "LIMIT", client_order_id=cid,
        )
    except RuntimeError as exc:
        assert "lost response" in str(exc)
    else:
        raise AssertionError("fake should lose the first create response")
    order_id = exchange.find_order_by_client_id("XRPUSDT", cid)["order_id"]
    db.update_level(grid["id"], 3, state="BUY_OPEN", client_order_id=cid, order_id=None)
    before = len(exchange.create_calls)
    engine.sync_grid(grid["id"])
    assert db.get_grid_levels(grid["id"])[3]["order_id"] == order_id
    assert len(exchange.create_calls) == before


def test_creation_failure_cancels_prior_orders_and_marks_grid_failed():
    engine, db, exchange = make_engine()
    exchange.fail_on_create = 2
    try:
        create(engine)
    except GridCreationError:
        pass
    else:
        raise AssertionError("expected a fail-closed grid creation error")
    grid = db.list_open_grids()
    assert grid == []
    assert db.grids is not None
    with db.engine.connect() as conn:
        row = conn.execute(db.grids.select()).mappings().first()
    assert row["status"] == "FAILED"
    assert exchange.get_open_orders("XRPUSDT") == []


def test_ninth_cell_grid_failure_on_fifth_buy_cleans_prior_orders():
    engine, db, exchange = make_engine()
    exchange.fail_on_create = 5
    try:
        engine.create_grid("XRPUSDT", 90, 110, 9)
    except GridCreationError:
        pass
    else:
        raise AssertionError("expected injected fifth-buy failure")
    with db.engine.connect() as conn:
        row = conn.execute(db.grids.select()).mappings().first()
    assert row["status"] == "FAILED"
    assert len(exchange.create_calls) == 5
    assert exchange.get_open_orders("XRPUSDT") == []


def test_insufficient_balance_is_not_retried_and_grid_fails_closed():
    engine, db, exchange = make_engine()
    exchange.fail_on_create = 1
    exchange.create_failure = exchange.insufficient_balance()
    try:
        create(engine)
    except GridCreationError as exc:
        assert "insufficient balance" in str(exc)
    else:
        raise AssertionError("expected insufficient-balance failure")
    assert len(exchange.create_calls) == 1
    with db.engine.connect() as conn:
        row = conn.execute(db.grids.select()).mappings().first()
    assert row["status"] == "FAILED"
    assert exchange.get_open_orders("XRPUSDT") == []


def test_partial_fill_does_not_create_a_sell_order():
    engine, db, exchange = make_engine()
    grid = create(engine)
    buy = exchange.get_open_orders("XRPUSDT")[-1]
    exchange.fill(buy["order_id"], partial=True)
    engine.sync_grid(grid["id"])
    level = db.get_grid_levels(grid["id"])[2]
    assert level["state"] == "BUY_OPEN"
    assert len(exchange.create_calls) == 3


def test_external_cancel_is_visible_as_error_and_not_reposted():
    engine, db, exchange = make_engine()
    grid = create(engine)
    buy = exchange.get_open_orders("XRPUSDT")[0]
    exchange.cancel_order("XRPUSDT", buy["order_id"])
    before = len(exchange.create_calls)
    engine.sync_grid(grid["id"])
    level = db.get_grid_levels(grid["id"])[0]
    assert level["state"] == "ERROR"
    assert len(exchange.create_calls) == before


def test_idle_cell_arms_when_price_moves_below_its_buy_price():
    engine, db, exchange = make_engine()
    grid = create(engine)
    exchange.move_price("104", "104.01", "104")
    engine.sync_grid(grid["id"])
    assert db.get_grid_levels(grid["id"])[3]["state"] == "BUY_OPEN"


def test_cancel_grid_reports_base_inventory_without_liquidating_it():
    engine, db, exchange = make_engine()
    grid = create(engine)
    buy = exchange.get_open_orders("XRPUSDT")[-1]
    exchange.fill(buy["order_id"])
    engine.sync_grid(grid["id"])
    result = engine.cancel_grid_orders(grid["id"])
    assert result["status"] == "CANCELLED"
    assert result["remaining_inventory"] == [{"level_idx": 2, "asset": "XRP", "held_qty": 1.9}]
    assert exchange.get_open_orders("XRPUSDT") == []
    assert db.get_grid(grid["id"])["status"] == "CANCELLED"


def test_commission_in_non_base_asset_does_not_reduce_held_quantity():
    engine, db, exchange = make_engine(fee_asset="USDT")
    grid = create(engine)
    buy = exchange.get_open_orders("XRPUSDT")[-1]
    exchange.fill(buy["order_id"])
    engine.sync_grid(grid["id"])
    level = db.get_grid_levels(grid["id"])[2]
    assert level["held_qty"] == 2.0


def test_sell_intent_crash_recovers_with_persisted_held_quantity():
    engine, db, exchange = make_engine(fee_rate="0", fee_asset="USDT")
    grid = create(engine)
    buy = exchange.get_open_orders("XRPUSDT")[-1]
    exchange.fill(buy["order_id"])

    class SimulatedCrash(BaseException):
        pass

    original = exchange.place_order
    def crash_on_sell(symbol, side, *args, **kwargs):
        if side == "SELL":
            raise SimulatedCrash()
        return original(symbol, side, *args, **kwargs)
    exchange.place_order = crash_on_sell
    try:
        engine.sync_grid(grid["id"])
    except SimulatedCrash:
        pass
    else:
        raise AssertionError("expected process interruption after SELL intent")
    level = db.get_grid_levels(grid["id"])[2]
    assert level["state"] == "SELL_OPEN"
    assert level["held_qty"] == 2.0
    cid = level["client_order_id"]
    exchange.place_order = original
    result = engine.sync_grid(grid["id"])
    recovered = db.get_grid_levels(grid["id"])[2]
    assert result["errors"] == 0
    assert recovered["state"] == "SELL_OPEN"
    assert recovered["client_order_id"] == cid
    assert exchange.get_order("XRPUSDT", order_id=recovered["order_id"])["quantity"] == Decimal("2.0")
    assert len([o for o in exchange.get_open_orders("XRPUSDT") if o["side"] == "SELL"]) == 1


def test_transient_sell_send_retries_without_error_state():
    engine, db, exchange = make_engine(fee_rate="0", fee_asset="USDT")
    grid = create(engine)
    buy = exchange.get_open_orders("XRPUSDT")[-1]
    exchange.fill(buy["order_id"])
    original = exchange.place_order
    failed = False
    def fail_first_sell(symbol, side, *args, **kwargs):
        nonlocal failed
        if side == "SELL" and not failed:
            failed = True
            raise RuntimeError("temporary network failure")
        return original(symbol, side, *args, **kwargs)
    exchange.place_order = fail_first_sell
    first = engine.sync_grid(grid["id"])
    level = db.get_grid_levels(grid["id"])[2]
    assert first["errors"] == 0
    assert level["state"] == "SELL_OPEN"
    cid = level["client_order_id"]
    second = engine.sync_grid(grid["id"])
    level = db.get_grid_levels(grid["id"])[2]
    assert second["errors"] == 0
    assert level["state"] == "SELL_OPEN" and level["client_order_id"] == cid
    assert len([o for o in exchange.get_open_orders("XRPUSDT") if o["side"] == "SELL"]) == 1


def test_cancel_failure_keeps_grid_open_for_coin_delete_guard():
    from fastapi import HTTPException
    from api.routes.coins import _assert_no_open_grid

    engine, db, exchange = make_engine()
    grid = create(engine)
    original = exchange.cancel_order
    exchange.cancel_order = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("cancel unavailable"))
    result = engine.cancel_grid_orders(grid["id"])
    assert result["status"] == "ACTIVE"
    assert result["cancel_errors"]
    assert db.has_open_grid("XRPUSDT")
    try:
        _assert_no_open_grid(db, "XRPUSDT")
    except HTTPException as exc:
        assert exc.status_code == 409
    else:
        raise AssertionError("coin deletion guard must reject an active grid")
    exchange.cancel_order = original


def test_cancel_finds_and_cancels_orphaned_client_order_intent():
    engine, db, exchange = make_engine()
    grid = create(engine)
    level = db.get_grid_levels(grid["id"])[3]
    cid = "g1L3B0"
    order = exchange.place_order("XRPUSDT", "BUY", Decimal("2.0"), Decimal("102"), client_order_id=cid)
    db.update_level(grid["id"], 3, state="BUY_OPEN", client_order_id=cid, order_id=None)
    result = engine.cancel_grid_orders(grid["id"])
    assert result["status"] == "CANCELLED"
    assert exchange.get_order("XRPUSDT", order_id=order["order_id"])["status"] == "CANCELED"
    assert not exchange.get_open_orders("XRPUSDT")


def test_unexpected_pnl_lookup_type_error_is_not_swallowed():
    engine, db, exchange = make_engine()
    grid = create(engine)
    buy = exchange.get_open_orders("XRPUSDT")[-1]
    exchange.fill(buy["order_id"])
    engine.sync_grid(grid["id"])
    level = db.get_grid_levels(grid["id"])[2]
    exchange.fill(level["order_id"])
    original = exchange.get_order
    def fail_buy_cid(symbol, order_id=None, client_order_id=None):
        if client_order_id:
            raise TypeError("programming defect")
        return original(symbol, order_id=order_id, client_order_id=client_order_id)
    exchange.get_order = fail_buy_cid
    with pytest.raises(TypeError, match="programming defect"):
        engine.sync_grid(grid["id"])


def test_pnl_fallback_does_not_subtract_fees_from_prior_cycles():
    engine, db, exchange = make_engine()
    grid = create(engine)
    buy = exchange.get_open_orders("XRPUSDT")[-1]
    exchange.fill(buy["order_id"])
    engine.sync_grid(grid["id"])
    level = db.get_grid_levels(grid["id"])[2]
    sell_id = int(level["order_id"])
    exchange.fill(sell_id)
    old_get_order = exchange.get_order
    def fail_cycle_two_buy(symbol, order_id=None, client_order_id=None):
        if client_order_id == "g1L2B1":
            raise KeyError("buy order not found")
        return old_get_order(symbol, order_id=order_id, client_order_id=client_order_id)
    exchange.get_order = fail_cycle_two_buy
    db.update_level(grid["id"], 2, state="SELL_OPEN", cycles_completed=1, fee_paid=100.0)
    expected = (Decimal(str(level["sell_price"])) - Decimal(str(level["price"]))) * Decimal(str(exchange.orders[sell_id]["executed_qty"]))
    engine.sync_grid(grid["id"])
    updated = db.get_grid_levels(grid["id"])[2]
    assert Decimal(str(updated["pnl"])) == expected
