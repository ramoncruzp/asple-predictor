from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import pytest

from database.db_manager import DBManager
from data.testnet_client import TestnetOrderError
from data.exchange_filters import FilterViolation
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


def test_smart_grid_requests_volatility_at_its_selected_horizon():
    engine, _db, _exchange = make_engine()
    class Provider:
        def __init__(self): self.calls = []
        def get(self, symbol, horizon_h=24):
            self.calls.append((symbol, horizon_h))
            return SimpleNamespace(sigma_24h=.02)
    provider = Provider()
    engine.vol_provider = provider
    engine.create_grid("XRPUSDT", 90, 110, 5, strategy="smart", params={"horizon_h": 4})
    assert provider.calls == [("XRPUSDT", 4)]


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


def test_closing_does_not_repost_missing_write_ahead_buy_intent():
    engine, db, exchange = make_engine()
    grid = create(engine)
    level = db.get_grid_levels(grid["id"])[3]
    db.update_grid(grid["id"], status="CLOSING")
    db.update_level(grid["id"], level["level_idx"], state="BUY_OPEN", order_id=None,
                    client_order_id=f"g{grid['id']}L{level['level_idx']}B0")
    before = len(exchange.create_calls)

    engine.close_grid(grid["id"], "cancel")

    assert len(exchange.create_calls) == before
    recovered = db.get_grid_levels(grid["id"])[level["level_idx"]]
    assert recovered["state"] == "IDLE"
    assert recovered["order_id"] is None and recovered["client_order_id"] is None


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
    exchange.free_usdt = Decimal("0")
    with pytest.raises(GridConfigError, match="free USDT balance is below the required buy-cell capital"):
        create(engine)
    assert db.list_open_grids() == []
    assert exchange.create_calls == []
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


def test_orders_placed_counts_transient_sell_retry_only_when_exchange_accepts_it():
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
    assert first["orders_placed"] == 0
    second = engine.sync_grid(grid["id"])
    assert second["orders_placed"] == 1


def test_transient_rearm_buy_keeps_write_ahead_intent_for_next_sync():
    engine, db, exchange = make_engine(fee_rate="0", fee_asset="USDT")
    grid = create(engine)
    buy = exchange.get_open_orders("XRPUSDT")[-1]
    exchange.fill(buy["order_id"])
    engine.sync_grid(grid["id"])
    level = db.get_grid_levels(grid["id"])[2]
    sell_id = int(level["order_id"])
    exchange.fill(sell_id)
    original = exchange.place_order
    failed = False
    def fail_first_rearm(symbol, side, *args, **kwargs):
        nonlocal failed
        if side == "BUY" and not failed:
            failed = True
            raise RuntimeError("temporary rearm failure")
        return original(symbol, side, *args, **kwargs)
    exchange.place_order = fail_first_rearm
    result = engine.sync_grid(grid["id"])
    level = db.get_grid_levels(grid["id"])[2]
    assert result["errors"] == 0
    assert level["state"] == "BUY_OPEN" and level["order_id"] is None
    assert level["client_order_id"] == "g1L2B1"
    assert engine.sync_grid(grid["id"])["errors"] == 0
    assert db.get_grid_levels(grid["id"])[2]["order_id"] is not None


def test_transient_idle_arm_keeps_intent_for_next_sync():
    engine, db, exchange = make_engine()
    grid = create(engine)
    exchange.move_price("104", "104.01", "104")
    original = exchange.place_order
    failed = False
    def fail_first_buy(symbol, side, *args, **kwargs):
        nonlocal failed
        if side == "BUY" and not failed:
            failed = True
            raise RuntimeError("temporary idle-arm failure")
        return original(symbol, side, *args, **kwargs)
    exchange.place_order = fail_first_buy
    first = engine.sync_grid(grid["id"])
    level = db.get_grid_levels(grid["id"])[3]
    assert first["errors"] == 1
    assert level["state"] == "BUY_OPEN" and level["order_id"] is None
    assert engine.sync_grid(grid["id"])["errors"] == 0
    assert db.get_grid_levels(grid["id"])[3]["order_id"] is not None


def test_transient_order_lookup_does_not_permanently_error_cell():
    engine, db, exchange = make_engine(fee_rate="0", fee_asset="USDT")
    grid = create(engine)
    buy = exchange.get_open_orders("XRPUSDT")[-1]
    exchange.fill(buy["order_id"])
    original = exchange.get_order
    failed = False
    def fail_once(symbol, order_id=None, client_order_id=None):
        nonlocal failed
        if order_id is not None and int(order_id) == int(buy["order_id"]) and not failed:
            failed = True
            raise RuntimeError("temporary lookup failure")
        return original(symbol, order_id=order_id, client_order_id=client_order_id)
    exchange.get_order = fail_once
    first = engine.sync_grid(grid["id"])
    assert first["errors"] == 1
    assert db.get_grid_levels(grid["id"])[2]["state"] == "BUY_OPEN"
    assert engine.sync_grid(grid["id"])["errors"] == 0
    assert db.get_grid_levels(grid["id"])[2]["state"] == "SELL_OPEN"


def test_cancel_reports_buy_that_fills_during_cancel_with_cell_and_quantity():
    engine, db, exchange = make_engine(fee_rate="0", fee_asset="USDT")
    grid = create(engine)
    buy = exchange.get_open_orders("XRPUSDT")[-1]
    original = exchange.cancel_order
    def fill_before_cancel_returns(symbol, order_id):
        if int(order_id) == int(buy["order_id"]):
            return exchange.fill(order_id)
        return original(symbol, order_id)
    exchange.cancel_order = fill_before_cancel_returns
    result = engine.cancel_grid_orders(grid["id"])
    fill = next(row for row in result["filled_during_cancel"] if row["order_id"] == buy["order_id"])
    assert fill["grid_id"] == grid["id"]
    assert fill["level_idx"] == 2
    assert Decimal(str(fill["executed_qty"])) == Decimal("2.0")
    assert "has_sell" not in fill


def test_failed_grid_creation_resets_canceled_buy_cells_to_idle_and_keeps_client_id():
    engine, db, exchange = make_engine()
    exchange.fail_on_create = len(exchange.create_calls) + 2
    with pytest.raises(GridCreationError):
        create(engine)
    with db.engine.connect() as conn:
        grid_row = conn.execute(db.grids.select()).mappings().first()
    levels = db.get_grid_levels(grid_row["id"])
    first = levels[0]
    assert grid_row["status"] == "FAILED"
    assert first["state"] == "IDLE" and first["order_id"] is None
    assert first["client_order_id"] == "g1L0B0"


def test_grid_can_open_for_symbol_with_only_a_holding_repository():
    engine, db, exchange = make_engine()
    db.create_grid_with_levels(
        {"symbol": "XRPUSDT", "range_low": 90, "range_high": 110, "n_levels": 1,
         "capital_total": 20, "status": "HOLDING", "environment": "testnet", "open_price": 100},
        [{"level_idx": 0, "price": 99, "sell_price": 101, "capital": 20, "state": "SELL_OPEN", "held_qty": 0.2}],
    )
    assert create(engine)["status"] == "ACTIVE"


def test_pause_resume_twice_never_reuses_buy_cid_or_orphans_live_buy():
    engine, db, exchange = make_engine(fee_rate="0", fee_asset="USDT")
    grid = create(engine)
    history = {int(row["level_idx"]): [row["client_order_id"]]
               for row in db.get_grid_levels(grid["id"]) if row["state"] == "BUY_OPEN"}
    for _ in range(2):
        assert engine.pause_grid(grid["id"], "test", {})["ok"]
        assert not [order for order in exchange.get_open_orders("XRPUSDT") if order["side"] == "BUY"]
        assert engine.resume_grid(grid["id"], "test", {})["ok"]
        assert engine.sync_grid(grid["id"])["errors"] == 0
        levels = db.get_grid_levels(grid["id"])
        open_buys = [order for order in exchange.get_open_orders("XRPUSDT") if order["side"] == "BUY"]
        for row in levels:
            if row["state"] == "BUY_OPEN":
                owned = [order for order in open_buys if int(order["order_id"]) == int(row["order_id"])]
                assert len(owned) == 1
                idx = int(row["level_idx"])
                cid = row["client_order_id"]
                assert cid not in history[idx], f"reused CID for level {idx}: {cid}"
                assert len(cid) <= 36
                history[idx].append(cid)
        assert len(open_buys) == sum(row["state"] == "BUY_OPEN" for row in levels)


def test_pause_cancel_crash_and_engine_restart_reuses_write_ahead_cid():
    engine, db, exchange = make_engine(fee_rate="0", fee_asset="USDT")
    grid = create(engine)
    old = next(row for row in db.get_grid_levels(grid["id"]) if row["level_idx"] == 0)
    old_cid = old["client_order_id"]
    assert engine.pause_grid(grid["id"], "test", {})["ok"]
    assert engine.resume_grid(grid["id"], "test", {})["ok"]
    place = exchange.place_order
    failed = {"once": False}

    def fail_once(*args, **kwargs):
        if not failed["once"] and kwargs.get("client_order_id") != old_cid:
            failed["once"] = True
            raise RuntimeError("injected process crash after write-ahead CID")
        return place(*args, **kwargs)

    exchange.place_order = fail_once
    engine.sync_grid(grid["id"])
    exchange.place_order = place
    intent = db.get_grid_levels(grid["id"])[0]
    intent_cid = intent["client_order_id"]
    assert intent["state"] == "BUY_OPEN" and intent["order_id"] is None
    assert exchange.find_order_by_client_id("XRPUSDT", intent_cid) is None

    restarted = GridEngine(db, exchange, engine.settings)
    assert restarted.sync_grid(grid["id"])["errors"] == 0
    recovered = db.get_grid_levels(grid["id"])[0]
    assert recovered["client_order_id"] == intent_cid
    assert recovered["order_id"] is not None
    assert exchange.find_order_by_client_id("XRPUSDT", intent_cid)["order_id"] == recovered["order_id"]
    assert len([order for order in exchange.get_open_orders("XRPUSDT")
                if order["client_order_id"] == intent_cid]) == 1


def test_buy_filled_during_pause_becomes_sell_without_rearming_buy():
    engine, db, exchange = make_engine(fee_rate="0", fee_asset="USDT")
    grid = create(engine)
    buy = exchange.get_open_orders("XRPUSDT")[0]
    original_cancel = exchange.cancel_order

    def fill_during_cancel(symbol, order_id):
        if int(order_id) == int(buy["order_id"]):
            return exchange.fill(order_id)
        return original_cancel(symbol, order_id)

    exchange.cancel_order = fill_during_cancel
    result = engine.pause_grid(grid["id"], "test", {})
    exchange.cancel_order = original_cancel
    assert result["ok"] and result["sync"]["errors"] == 0
    level = db.get_grid_levels(grid["id"])[0]
    assert level["state"] == "SELL_OPEN"
    assert level["order_id"] is not None
    order = exchange.get_order("XRPUSDT", order_id=level["order_id"])
    assert order["side"] == "SELL" and order["status"] == "NEW"
    assert not any(row["side"] == "BUY" and int(row["order_id"]) == int(buy["order_id"])
                   for row in exchange.get_open_orders("XRPUSDT"))


def test_after_sell_cycle_next_buy_uses_flat_cycle_cid():
    engine, db, exchange = make_engine(fee_rate="0", fee_asset="USDT")
    grid = create(engine)
    level = db.get_grid_levels(grid["id"])[2]
    exchange.fill(level["order_id"])
    engine.sync_grid(grid["id"])
    level = db.get_grid_levels(grid["id"])[2]
    assert level["state"] == "SELL_OPEN"
    exchange.fill(level["order_id"])
    engine.sync_grid(grid["id"])
    level = db.get_grid_levels(grid["id"])[2]
    assert level["cycles_completed"] == 1
    assert level["state"] == "BUY_OPEN"
    assert level["buy_client_order_id"] == f"g{grid['id']}L2B1"
    assert level["client_order_id"] == f"g{grid['id']}L2B1"


def test_insufficient_balance_business_rejection_defers_rearm_cell():
    engine, db, exchange = make_engine(fee_rate="0", fee_asset="USDT")
    grid = create(engine)
    events = []
    engine.event_sink = events.append
    exchange.move_price("108", "108.01", "108")
    rejected = {"done": False}
    place = exchange.place_order

    def reject_first_buy(symbol, side, quantity, price=None, order_type="LIMIT", client_order_id=None):
        if side == "BUY" and not rejected["done"]:
            rejected["done"] = True
            raise TestnetOrderError(-2010, "Account has insufficient balance")
        return place(symbol, side, quantity, price, order_type, client_order_id)

    exchange.place_order = reject_first_buy
    summary = engine.sync_grid(grid["id"])
    assert rejected["done"]
    assert summary["errors"] == 0
    assert summary["buys_deferred"] == 1
    levels = db.get_grid_levels(grid["id"])
    deferred = [row for row in levels if row["state"] == "IDLE" and row.get("buy_client_order_id")]
    assert len(deferred) == 1
    assert deferred[0]["order_id"] is None
    assert deferred[0]["client_order_id"] is None
    assert not exchange.find_order_by_client_id("XRPUSDT", deferred[0]["buy_client_order_id"])
    event = next(item for item in events if item["event_type"] == "BUY_DEFERRED_FUNDS")
    assert event["details"]["error_code"] == -2010


def _prepare_idle_rearm(engine, db, exchange):
    grid = create(engine)
    exchange.move_price("108", "108.01", "108")
    return grid


def test_insufficient_balance_retry_uses_fresh_cid_after_funds_return():
    engine, db, exchange = make_engine(fee_rate="0", fee_asset="USDT")
    grid = _prepare_idle_rearm(engine, db, exchange)
    events = []
    engine.event_sink = events.append
    place = exchange.place_order
    failed_cids = []

    def reject_once(symbol, side, quantity, price=None, order_type="LIMIT", client_order_id=None):
        if side == "BUY" and not failed_cids:
            failed_cids.append(client_order_id)
            raise TestnetOrderError(-2010, "Account has insufficient balance")
        return place(symbol, side, quantity, price, order_type, client_order_id)

    exchange.place_order = reject_once
    first = engine.sync_grid(grid["id"])
    assert first["buys_deferred"] == 1 and first["errors"] == 0
    exchange.place_order = place
    second = engine.sync_grid(grid["id"])
    assert second["orders_placed"] == 1
    level = next(row for row in db.get_grid_levels(grid["id"])
                 if row["level_idx"] == int(failed_cids[0].split("L")[1].split("B")[0]))
    assert level["state"] == "BUY_OPEN"
    assert level["client_order_id"] != failed_cids[0]
    assert exchange.find_order_by_client_id("XRPUSDT", level["client_order_id"])


def test_insufficient_balance_repeated_syncs_emit_one_defer_event():
    engine, db, exchange = make_engine(fee_rate="0", fee_asset="USDT")
    grid = _prepare_idle_rearm(engine, db, exchange)
    events = []
    engine.event_sink = events.append
    place = exchange.place_order

    def reject_all(symbol, side, quantity, price=None, order_type="LIMIT", client_order_id=None):
        if side == "BUY":
            raise TestnetOrderError(-2010, "Account has insufficient balance")
        return place(symbol, side, quantity, price, order_type, client_order_id)

    exchange.place_order = reject_all
    for _ in range(5):
        result = engine.sync_grid(grid["id"])
        assert result["errors"] == 0
    deferred_events = [event for event in events if event["event_type"] == "BUY_DEFERRED_FUNDS"]
    level_ids = {event["level_idx"] for event in deferred_events}
    assert level_ids
    assert all(sum(event["level_idx"] == level_idx for event in deferred_events) == 1
               for level_idx in level_ids)
    assert not any(row["state"] == "ERROR" for row in db.get_grid_levels(grid["id"]))


def test_insufficient_balance_rejection_restart_leaves_no_orphan_buy():
    engine, db, exchange = make_engine(fee_rate="0", fee_asset="USDT")
    grid = _prepare_idle_rearm(engine, db, exchange)
    events = []
    def persist_event(event):
        events.append(event)
        db.add_grid_event(
            run_id=None, source="CLI", grid_id=event["grid_id"],
            level_idx=event.get("level_idx"), client_order_id=event.get("client_order_id"),
            order_id=event.get("order_id"), event_type=event["event_type"],
            reason=event.get("reason"), price=event.get("price"), details=event.get("details"),
        )

    engine.event_sink = persist_event
    level = next(row for row in db.get_grid_levels(grid["id"]) if row["state"] == "IDLE"
                 and row["price"] < 108)
    write_ahead_cid = f"g{grid['id']}L{level['level_idx']}B77"
    db.update_level(grid["id"], level["level_idx"], state="BUY_OPEN",
                    client_order_id=write_ahead_cid, buy_client_order_id=write_ahead_cid,
                    order_id=None)
    place = exchange.place_order

    def reject_buy(symbol, side, quantity, price=None, order_type="LIMIT", client_order_id=None):
        if side == "BUY":
            raise TestnetOrderError(-2010, "Account has insufficient balance")
        return place(symbol, side, quantity, price, order_type, client_order_id)

    exchange.place_order = reject_buy
    engine.sync_grid(grid["id"])
    persisted = db.get_grid_levels(grid["id"])
    deferred = [row for row in persisted if row.get("buy_client_order_id") and row["state"] == "IDLE"]
    assert deferred and not any(row["state"] == "ERROR" for row in persisted)
    assert all(row["state"] != "BUY_OPEN" or row["order_id"] is not None for row in persisted)
    restarted = GridEngine(db, exchange, engine.settings, event_sink=persist_event)
    restarted.sync_grid(grid["id"])
    assert all(row["state"] != "BUY_OPEN" or row["order_id"] is not None
               for row in db.get_grid_levels(grid["id"]))
    assert not any(row["side"] == "BUY" for row in exchange.get_open_orders("XRPUSDT")
                   if not any(level.get("order_id") == row["order_id"]
                              for level in db.get_grid_levels(grid["id"])))
    assert sum(event["level_idx"] == level["level_idx"] for event in db.list_grid_events(
        grid_id=grid["id"], event_type="BUY_DEFERRED_FUNDS")) == 1


def test_filter_rejection_still_sets_rearm_cell_error():
    engine, db, exchange = make_engine(fee_rate="0", fee_asset="USDT")
    grid = _prepare_idle_rearm(engine, db, exchange)
    def reject_filter(*args, **kwargs):
        raise FilterViolation("LOT_SIZE", "quantity below minimum")

    engine._send_limit = reject_filter
    result = engine.sync_grid(grid["id"])
    assert result["errors"] >= 1
    assert result["buys_deferred"] == 0
    assert any(row["state"] == "ERROR" for row in db.get_grid_levels(grid["id"]))


def test_initial_buy_race_after_balance_precheck_defers_only_that_cell():
    engine, db, exchange = make_engine(fee_rate="0", fee_asset="USDT")
    events = []
    engine.event_sink = events.append
    balance_reads = []
    get_balance = exchange.get_balance
    def record_balance(asset=None):
        result = get_balance(asset)
        balance_reads.append(Decimal(str(result["USDT"]["free"])))
        return result
    exchange.get_balance = record_balance
    place = exchange.place_order
    rejected = {"done": False}

    def reject_one(symbol, side, quantity, price=None, order_type="LIMIT", client_order_id=None):
        if side == "BUY" and not rejected["done"]:
            rejected["done"] = True
            raise TestnetOrderError(-2010, "Account has insufficient balance")
        return place(symbol, side, quantity, price, order_type, client_order_id)

    exchange.place_order = reject_one
    grid = create(engine)
    levels = db.get_grid_levels(grid["id"])
    assert grid["status"] == "ACTIVE"
    assert balance_reads and balance_reads[0] >= Decimal("1000")
    assert any(row["state"] == "IDLE" and row.get("buy_client_order_id") for row in levels)
    assert len([event for event in events if event["event_type"] == "BUY_DEFERRED_FUNDS"]) == 1


def test_sell_fill_rearm_insufficient_balance_counts_as_deferred_not_error():
    engine, db, exchange = make_engine(fee_rate="0", fee_asset="USDT")
    grid = create(engine)
    cell = db.get_grid_levels(grid["id"])[2]
    exchange.fill(cell["order_id"])
    engine.sync_grid(grid["id"])
    cell = db.get_grid_levels(grid["id"])[2]
    exchange.fill(cell["order_id"])
    exchange.move_price("108", "108.01", "108")
    events = []
    engine.event_sink = events.append
    place = exchange.place_order
    rejected = {"done": False}

    def reject_first_buy(symbol, side, quantity, price=None, order_type="LIMIT", client_order_id=None):
        if side == "BUY" and not rejected["done"]:
            rejected["done"] = True
            raise TestnetOrderError(-2010, "Account has insufficient balance")
        return place(symbol, side, quantity, price, order_type, client_order_id)

    exchange.place_order = reject_first_buy
    result = engine.sync_grid(grid["id"])
    cell = db.get_grid_levels(grid["id"])[2]
    assert result["errors"] == 0
    assert result["buys_deferred"] >= 1
    assert cell["state"] == "IDLE" and cell["order_id"] is None
    assert any(event["event_type"] == "BUY_DEFERRED_FUNDS" and event["level_idx"] == 2
               for event in events)


def test_adjust_buy_insufficient_balance_leaves_cell_idle_and_reports_deferred():
    engine, db, exchange = make_engine(fee_rate="0", fee_asset="USDT")
    grid = create(engine)
    events = []
    engine.event_sink = events.append
    place = exchange.place_order
    rejected = {"done": False}

    def reject_first_buy(symbol, side, quantity, price=None, order_type="LIMIT", client_order_id=None):
        if side == "BUY" and not rejected["done"]:
            rejected["done"] = True
            raise TestnetOrderError(-2010, "Account has insufficient balance")
        return place(symbol, side, quantity, price, order_type, client_order_id)

    exchange.place_order = reject_first_buy
    result = engine.adjust_grid(grid["id"], 88, 112, 5, reason="test")
    assert result["ok"] and rejected["done"]
    deferred = [row for row in db.get_grid_levels(grid["id"])
                if row["state"] == "IDLE" and row.get("buy_client_order_id")]
    assert deferred
    event = next(event for event in events if event["event_type"] == "BUY_DEFERRED_FUNDS")
    assert any(row["level_idx"] == event["level_idx"]
               and row["buy_client_order_id"] == event["client_order_id"] for row in deferred)


def test_engine_treats_absent_exchange_order_limit_as_unlimited():
    engine, db, exchange = make_engine()
    info = exchange.get_symbol_info("XRPUSDT")
    info["filters"] = [row for row in info["filters"] if row["filterType"] != "MAX_NUM_ORDERS"]
    exchange.get_symbol_info = lambda symbol: info
    grid = create(engine)
    exchange.move_price("104", "104.01", "104")
    result = engine.sync_grid(grid["id"])
    assert result["errors"] == 0
    assert db.get_grid(grid["id"])["status"] == "ACTIVE"


def test_smart_grid_rejects_horizons_outside_supported_set():
    from grid.levels import GridConfigError
    engine, _db, exchange = make_engine()
    before = list(exchange.create_calls)
    with pytest.raises(GridConfigError, match="horizon_h debe ser 1, 2, 4 o 24"):
        create(engine, strategy="smart", params={"horizon_h": 12})
    assert exchange.create_calls == before
