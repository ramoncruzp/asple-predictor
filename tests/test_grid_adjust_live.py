"""L10-L11: real Testnet mechanics only; commission and profitability are not tested."""

from decimal import Decimal
from datetime import datetime, timezone
import time

import pytest

from tests.test_grid_engine_live import live_context, live_range, verify_no_grid_orders


def _live_plan(engine, exchange, db, grid_id):
    orders = exchange.get_open_orders("XRPUSDT")
    rows = db.get_grid_levels(grid_id)
    owned = {int(row["order_id"]): row for row in rows if row.get("order_id") is not None}
    assert all(int(order["order_id"]) in owned for order in orders), "órdenes Testnet sin celda dueña"
    assert len({int(row["order_id"]) for row in orders}) == len(orders)
    return orders


def _unique_grid_sequence(db):
    # Testnet retains client IDs indefinitely; each run gets a fresh local
    # sequence so a previous market SELL cannot satisfy a new cleanup.
    seed = int(time.time() * 1000)
    with db.engine.begin() as conn:
        conn.execute(db.grids.insert().values(
            id=seed, symbol="XRPUSDT", range_low=1.0, range_high=2.0,
            n_levels=4, capital_total=0.0, status="CLOSED",
            created_at=datetime.now(timezone.utc).replace(tzinfo=None),
            environment="testnet", strategy="simple", params=None,
        ))


def _total_xrp(exchange):
    row = exchange.get_balance("XRP").get("XRP", {"free": 0, "locked": 0})
    return Decimal(str(row.get("free", 0))) + Decimal(str(row.get("locked", 0)))


def test_live_l10_adjust_keeps_owned_sell_and_replaces_free_buys(tmp_path):
    settings, exchange, db, engine = live_context(tmp_path)
    if exchange.get_open_orders("XRPUSDT"):
        pytest.skip("la cuenta Testnet ya tiene órdenes XRPUSDT; no se tocarán")
    baseline_xrp = _total_xrp(exchange)
    _unique_grid_sequence(db)
    filters = engine._market_context("XRPUSDT")[0]
    avg = Decimal(str(exchange.get_avg_price("XRPUSDT")))
    low, high = live_range(exchange)
    capital = Decimal("50")
    grid = None
    owned_buy_id = None
    try:
        grid = engine.create_grid("XRPUSDT", low, high, 5, capital=capital,
                                  strategy="smart", params={"adjust_enabled": True})
        # Spend only one grid cell on an owned, marketable limit BUY so this
        # test can exercise a genuinely pinned sell without selling old XRP.
        cell = db.get_grid_levels(grid["id"])[-1]
        buy_price = filters.round_price(avg * Decimal("1.01"), "up")
        qty = filters.round_qty_down(Decimal(str(cell["capital"])) / buy_price)
        cid = f"gL10{grid['id']}B0"
        order = exchange.place_order("XRPUSDT", "BUY", qty, price=buy_price, client_order_id=cid)
        owned_buy_id = int(order["order_id"])
        pinned_idx = int(cell["level_idx"])
        db.update_level(grid["id"], pinned_idx, state="BUY_OPEN", price=float(buy_price),
                        sell_price=float(filters.round_price(buy_price * Decimal("1.02"), "up")),
                        order_id=owned_buy_id, client_order_id=cid, buy_client_order_id=cid)
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            order = exchange.get_order("XRPUSDT", order_id=owned_buy_id)
            if str(order["status"]).upper() == "FILLED":
                break
            time.sleep(1)
        if str(order["status"]).upper() != "FILLED":
            pytest.skip("la compra limitada de prueba no se ejecutó dentro del plazo")
        engine.sync_grid(grid["id"])
        pinned = db.get_grid_levels(grid["id"])[pinned_idx]
        assert pinned["state"] == "SELL_OPEN" and pinned["order_id"] != owned_buy_id
        sell_id = int(pinned["order_id"])
        moved_low, moved_high = low * Decimal("1.005"), high * Decimal("1.005")
        result = engine.adjust_grid(grid["id"], moved_low, moved_high, reason="L10", details={"source": "TEST"})
        assert result["ok"]
        updated_pinned = db.get_grid_levels(grid["id"])[pinned_idx]
        assert updated_pinned["state"] == "SELL_OPEN" and int(updated_pinned["order_id"]) == sell_id
        orders = _live_plan(engine, exchange, db, grid["id"])
        assert any(int(row["order_id"]) == sell_id for row in orders)
        sell_ids = {int(row["order_id"]) for row in db.get_grid_levels(grid["id"])
                    if row["state"] == "SELL_OPEN" and row.get("order_id") is not None}
        assert all((row["side"] == "SELL" and int(row["order_id"]) in sell_ids)
                   or (row["side"] == "BUY" and str(row.get("client_order_id", "")).startswith("gA"))
                   for row in orders)
        locked = exchange.get_balance("USDT").get("USDT", {}).get("locked", 0)
        expected_locked = sum(float(row["price"]) * float(row.get("quantity", 0))
                              for row in orders if row.get("side") == "BUY")
        assert abs(float(locked) - expected_locked) <= max(0.05, expected_locked * 0.03)
        print({"L10": "passed", "live_orders": len(orders), "usdt_locked": locked,
               "pinned_sell_order_id": sell_id})
    finally:
        if grid is not None:
            try:
                if db.get_grid(grid["id"])["status"] == "ACTIVE":
                    engine.sync_grid(grid["id"])
                    engine.close_grid(grid["id"], "liquidate")
            except Exception:
                for row in db.get_grid_levels(grid["id"]):
                    if row.get("order_id") is not None:
                        try:
                            exchange.cancel_order("XRPUSDT", int(row["order_id"]))
                        except Exception:
                            pass
        elif owned_buy_id is not None:
            try:
                exchange.cancel_order("XRPUSDT", owned_buy_id)
            except Exception:
                pass
        verify_no_grid_orders(exchange)
        assert abs(_total_xrp(exchange) - baseline_xrp) <= filters.step_size


def test_live_l11_adjust_changes_n_up_and_down(tmp_path):
    _settings, exchange, db, engine = live_context(tmp_path)
    if exchange.get_open_orders("XRPUSDT"):
        pytest.skip("la cuenta Testnet ya tiene órdenes XRPUSDT; no se tocarán")
    baseline_xrp = _total_xrp(exchange)
    filters = engine._market_context("XRPUSDT")[0]
    _unique_grid_sequence(db)
    low, high = live_range(exchange)
    grid = None
    try:
        grid = engine.create_grid("XRPUSDT", low, high, 5, capital=Decimal("70"),
                                  strategy="smart", params={"adjust_enabled": True})
        mid = Decimal(str(exchange.get_avg_price("XRPUSDT")))
        assert engine.adjust_grid(grid["id"], low * Decimal("1.002"), high * Decimal("1.002"), 7)["ok"]
        assert len(db.get_grid_levels(grid["id"])) == 7
        _live_plan(engine, exchange, db, grid["id"])
        assert engine.adjust_grid(grid["id"], low * Decimal("0.998"), high * Decimal("0.998"), 4)["ok"]
        rows = db.get_grid_levels(grid["id"])
        assert len(rows) == 7 and db.get_grid(grid["id"])["n_levels"] == 4
        _live_plan(engine, exchange, db, grid["id"])
        print({"L11": "passed", "target_n": 4,
               "active_buy_cells": sum(row["state"] == "BUY_OPEN" for row in rows), "mid": str(mid)})
    finally:
        if grid is not None:
            try:
                if db.get_grid(grid["id"])["status"] == "ACTIVE":
                    engine.close_grid(grid["id"], "liquidate")
            except Exception:
                for row in db.get_grid_levels(grid["id"]):
                    if row.get("order_id") is not None:
                        try:
                            exchange.cancel_order("XRPUSDT", int(row["order_id"]))
                        except Exception:
                            pass
        verify_no_grid_orders(exchange)
        assert abs(_total_xrp(exchange) - baseline_xrp) <= filters.step_size


def test_live_l12_adjust_cid_a_to_b_to_a_creates_fresh_owned_buy(tmp_path):
    _settings, exchange, db, engine = live_context(tmp_path)
    if exchange.get_open_orders("XRPUSDT"):
        pytest.skip("la cuenta Testnet ya tiene órdenes XRPUSDT; no se tocarán")
    baseline_xrp = _total_xrp(exchange)
    filters = engine._market_context("XRPUSDT")[0]
    _unique_grid_sequence(db)
    low, high = live_range(exchange)
    grid = None
    try:
        grid = engine.create_grid("XRPUSDT", low, high, 5, capital=Decimal("50"),
                                  strategy="smart", params={"adjust_enabled": True})
        original = next(row for row in db.get_grid_levels(grid["id"])
                        if row["state"] == "BUY_OPEN" and row.get("order_id") is not None)
        idx = int(original["level_idx"])
        initial_id, initial_cid = int(original["order_id"]), original["client_order_id"]
        initial_price = Decimal(str(original["price"]))

        shift = Decimal("1.004")
        moved_low, moved_high = low * shift, high * shift
        assert engine.adjust_grid(grid["id"], moved_low, moved_high)["ok"]
        moved = db.get_grid_levels(grid["id"])[idx]
        assert moved["state"] == "BUY_OPEN" and moved.get("order_id") is not None
        moved_id, moved_cid = int(moved["order_id"]), moved["client_order_id"]
        assert Decimal(str(moved["price"])) != initial_price
        assert moved_id != initial_id and moved_cid != initial_cid
        assert str(exchange.get_order("XRPUSDT", order_id=initial_id)["status"]).upper() == "CANCELED"
        assert _live_plan(engine, exchange, db, grid["id"])

        assert engine.adjust_grid(grid["id"], low, high)["ok"]
        returned = db.get_grid_levels(grid["id"])[idx]
        assert returned["state"] == "BUY_OPEN" and returned.get("order_id") is not None
        returned_id, returned_cid = int(returned["order_id"]), returned["client_order_id"]
        assert Decimal(str(returned["price"])) == filters.round_price(initial_price, "down")
        assert returned_id not in {initial_id, moved_id}
        assert returned_cid not in {initial_cid, moved_cid}
        live = _live_plan(engine, exchange, db, grid["id"])
        cell_buys = [order for order in live if int(order["order_id"]) == returned_id]
        assert len(cell_buys) == 1 and cell_buys[0]["side"] == "BUY"
        assert sum(1 for order in live if int(order["order_id"]) == returned_id) == 1
        assert str(exchange.get_order("XRPUSDT", order_id=moved_id)["status"]).upper() == "CANCELED"
        print({"L12": "passed", "level_idx": idx,
               "order_ids": [initial_id, moved_id, returned_id],
               "client_order_ids": [initial_cid, moved_cid, returned_cid],
               "live_orders": len(live)})
    finally:
        if grid is not None:
            try:
                if db.get_grid(grid["id"])["status"] == "ACTIVE":
                    engine.close_grid(grid["id"], "liquidate")
            except Exception:
                for row in db.get_grid_levels(grid["id"]):
                    if row.get("order_id") is not None:
                        try:
                            exchange.cancel_order("XRPUSDT", int(row["order_id"]))
                        except Exception:
                            pass
        verify_no_grid_orders(exchange)
        assert abs(_total_xrp(exchange) - baseline_xrp) <= filters.step_size
