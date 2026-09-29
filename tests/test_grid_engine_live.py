from __future__ import annotations

import time
from decimal import Decimal

import pytest

from config.settings import Settings
from database.db_manager import DBManager
from data.exchange_filters import SymbolFilters
from data.testnet_client import TestnetClient as _TestnetClient
from grid.engine import GridEngine


def live_context(tmp_path):
    settings = Settings()
    key = (settings.testnet_api_key or "").strip()
    secret = (settings.testnet_api_secret or "").strip()
    if (
        not key or not secret
        or key.casefold().startswith("tu_")
        or secret.casefold().startswith("tu_")
    ):
        pytest.skip("faltan credenciales de Testnet")
    exchange = _TestnetClient(key, secret, production_api_key=settings.binance_api_key)
    db = DBManager(f"sqlite:///{tmp_path / 'grid-live.db'}")
    db.add_or_reactivate_coin("XRPUSDT", "Fase 14 live test")
    engine = GridEngine(db, exchange, settings)
    return settings, exchange, db, engine


def live_range(exchange):
    avg = exchange.get_avg_price("XRPUSDT")
    return avg * Decimal("0.975"), avg * Decimal("1.025")


def verify_no_grid_orders(exchange, expected_ids=()):
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        current = {int(row["order_id"]) for row in exchange.get_open_orders("XRPUSDT")}
        if current == set(expected_ids):
            return current
        time.sleep(1)
    raise AssertionError(f"órdenes XRPUSDT restantes tras limpieza: {sorted(current)}")


def test_live_grid_l1_create_and_cancel_roundtrip(tmp_path):
    settings, exchange, db, engine = live_context(tmp_path)
    before_orders = exchange.get_open_orders("XRPUSDT")
    assert not before_orders, "la cuenta Testnet ya tiene órdenes XRPUSDT; no se tocarán"
    before_usdt = exchange.get_balance("USDT").get("USDT", {"free": 0, "locked": 0})
    grid = None
    try:
        low, high = live_range(exchange)
        grid = engine.create_grid(
            "XRPUSDT", low, high, 5,
            capital=Decimal(str(settings.usdt_por_grid)) if settings.usdt_por_grid < 100 else Decimal("50"),
        )
        levels = db.get_grid_levels(grid["id"])
        expected = {int(row["order_id"]) for row in levels if row["state"] == "BUY_OPEN"}
        opened = {int(row["order_id"]) for row in exchange.get_open_orders("XRPUSDT")}
        assert opened == expected
        filters = SymbolFilters.from_symbol_info(exchange.get_symbol_info("XRPUSDT"))
        avg = exchange.get_avg_price("XRPUSDT")
        for level in levels:
            if level["state"] == "BUY_OPEN":
                filters.validate_order("BUY", Decimal(str(level["price"])), Decimal(str(exchange.get_order("XRPUSDT", order_id=level["order_id"])["quantity"])), avg)
        cancel_result = engine.cancel_grid_orders(grid["id"])
        assert cancel_result["cancel_errors"] == []
        verify_no_grid_orders(exchange)
        after_usdt = exchange.get_balance("USDT").get("USDT", {"free": 0, "locked": 0})
        assert Decimal(str(after_usdt["locked"])) == Decimal(str(before_usdt["locked"]))
        assert abs(Decimal(str(after_usdt["free"])) - Decimal(str(before_usdt["free"]))) <= Decimal("0.02")
        print({"grid_id": grid["id"], "buy_order_ids": sorted(expected), "cancelled": cancel_result["canceled_order_ids"]})
    finally:
        if grid is not None and db.get_grid(grid["id"])["status"] != "CANCELLED":
            engine.cancel_grid_orders(grid["id"])
        verify_no_grid_orders(exchange)


def test_live_grid_l2_filled_buy_uses_actual_net_quantity(tmp_path):
    settings, exchange, db, engine = live_context(tmp_path)
    before = exchange.get_balance("XRP").get("XRP", {"free": 0, "locked": 0})
    before_orders = exchange.get_open_orders("XRPUSDT")
    assert not before_orders, "la cuenta Testnet ya tiene órdenes XRPUSDT; no se tocarán"
    grid = None
    purchased_delta = Decimal(0)
    market_buy_order_id = None
    executed = Decimal(0)
    filters = None
    cleanup_sell_order_id = None
    try:
        low, high = live_range(exchange)
        grid = engine.create_grid("XRPUSDT", low, high, 5, capital=Decimal("50"))
        levels = db.get_grid_levels(grid["id"])
        ask = exchange.get_book_ticker("XRPUSDT")["ask_price"]
        candidates = [
            row for row in levels
            if row["state"] == "BUY_OPEN" and Decimal(str(row["sell_price"])) > ask
        ]
        assert candidates
        level = max(candidates, key=lambda row: Decimal(str(row["price"])))
        old_buy_id = int(level["order_id"])
        canceled = exchange.cancel_order("XRPUSDT", old_buy_id)
        assert canceled["status"] == "CANCELED"
        filters = SymbolFilters.from_symbol_info(exchange.get_symbol_info("XRPUSDT"))
        qty = filters.round_qty_down(Decimal(str(level["capital"])) / Decimal(str(level["price"])))
        market_buy = exchange.client.create_order(
            symbol="XRPUSDT", side="BUY", type="MARKET", quantity=format(qty, "f"),
            newOrderRespType="FULL",
        )
        market_order_id = int(market_buy["orderId"])
        market_buy_order_id = market_order_id
        executed = Decimal(str(market_buy["executedQty"]))
        trades = exchange.get_my_trades("XRPUSDT", market_order_id)
        base_fee = sum((t["commission"] for t in trades if t["commission_asset"] == "XRP"), Decimal(0))
        purchased_delta = filters.round_qty_down(executed - base_fee)
        after_purchase = exchange.get_balance("XRP").get("XRP", {"free": 0, "locked": 0})
        actual_base_delta = Decimal(str(after_purchase["free"])) - Decimal(str(before["free"]))
        db.update_level(grid["id"], int(level["level_idx"]), order_id=market_order_id)
        result = engine.sync_grid(grid["id"])
        assert result["errors"] == 0
        live_level = db.get_grid_levels(grid["id"])[int(level["level_idx"])]
        assert live_level["state"] == "SELL_OPEN"
        assert Decimal(str(live_level["held_qty"])) == purchased_delta
        assert Decimal(str(live_level["held_qty"])) <= actual_base_delta
        if base_fee == 0:
            assert Decimal(str(live_level["held_qty"])) == actual_base_delta
        sells = exchange.get_open_orders("XRPUSDT")
        sell = next(row for row in sells if int(row["order_id"]) == int(live_level["order_id"]))
        assert Decimal(str(sell["quantity"])) == purchased_delta
        filters.validate_order(
            "SELL", Decimal(str(sell["price"])), purchased_delta, exchange.get_avg_price("XRPUSDT"),
        )
        print({
            "grid_id": grid["id"], "buy_order_id": market_order_id,
            "executed_qty": str(executed),
            "commission_by_asset": {
                asset: str(sum((t["commission"] for t in trades if t["commission_asset"] == asset), Decimal(0)))
                for asset in sorted({t["commission_asset"] for t in trades})
            },
            "base_commission": str(base_fee), "held_qty": str(purchased_delta),
            "actual_base_delta": str(actual_base_delta),
            "sell_order_id": live_level["order_id"], "sync": result,
        })
    finally:
        try:
            if grid is not None:
                engine.cancel_grid_orders(grid["id"])
        finally:
            cleanup_filters = filters or SymbolFilters.from_symbol_info(exchange.get_symbol_info("XRPUSDT"))
            if market_buy_order_id is not None and executed > 0:
                cleanup_trades = exchange.get_my_trades("XRPUSDT", market_buy_order_id)
                cleanup_base_fee = sum(
                    (t["commission"] for t in cleanup_trades if t["commission_asset"] == "XRP"),
                    Decimal(0),
                )
                safe_delta = cleanup_filters.round_qty_down(executed - cleanup_base_fee)
                remaining_free = Decimal(str(exchange.get_balance("XRP").get("XRP", {}).get("free", 0))) - Decimal(str(before["free"]))
                sell_qty = min(safe_delta, cleanup_filters.round_qty_down(max(remaining_free, Decimal(0))))
                if sell_qty >= cleanup_filters.min_qty:
                    cleanup_sell = exchange.place_order("XRPUSDT", "SELL", sell_qty, order_type="MARKET")
                    cleanup_sell_order_id = cleanup_sell["order_id"]
            verify_no_grid_orders(exchange)
            after = exchange.get_balance("XRP").get("XRP", {"free": 0, "locked": 0})
            assert Decimal(str(after["locked"])) == Decimal(str(before["locked"]))
            base_delta = Decimal(str(after["free"])) - Decimal(str(before["free"]))
            tolerance = cleanup_filters.step_size * 2
            assert abs(base_delta) <= tolerance
            print({
                "cleanup_sell_order_id": cleanup_sell_order_id,
                "base_free_before": before["free"], "base_free_after": after["free"],
                "base_delta": str(base_delta), "remaining_xrp_orders": 0,
            })


def test_live_grid_l3_market_cycle_matches_account_balances(tmp_path):
    """Testnet commission is currently zero; this cycle does not validate fee deduction."""
    settings, exchange, db, engine = live_context(tmp_path)
    before_orders = exchange.get_open_orders("XRPUSDT")
    assert not before_orders, "la cuenta Testnet ya tiene órdenes XRPUSDT; no se tocará"
    before_usdt = exchange.get_balance("USDT").get("USDT", {"free": 0, "locked": 0})
    before_xrp = exchange.get_balance("XRP").get("XRP", {"free": 0, "locked": 0})
    grid = None
    bought_qty = Decimal(0)
    buy_order_id = None
    sale_order_id = None
    try:
        low, high = live_range(exchange)
        grid = engine.create_grid("XRPUSDT", low, high, 5, capital=Decimal("50"))
        levels = db.get_grid_levels(grid["id"])
        ask = exchange.get_book_ticker("XRPUSDT")["ask_price"]
        candidates = [row for row in levels if row["state"] == "BUY_OPEN"]
        assert candidates
        level = max(candidates, key=lambda row: Decimal(str(row["price"])))
        idx = int(level["level_idx"])
        for other in levels:
            if int(other["level_idx"]) == idx:
                continue
            if other.get("order_id") is not None:
                exchange.cancel_order("XRPUSDT", int(other["order_id"]))
            db.update_level(grid["id"], int(other["level_idx"]), state="ERROR", order_id=None, client_order_id=None)
        if level.get("order_id") is not None:
            canceled = exchange.cancel_order("XRPUSDT", int(level["order_id"]))
            assert canceled["status"] == "CANCELED"

        filters = SymbolFilters.from_symbol_info(exchange.get_symbol_info("XRPUSDT"))
        qty = filters.round_qty_down(Decimal(str(level["capital"])) / Decimal(str(level["price"])))
        buy_cid = f"g{grid['id']}L{idx}B1"
        market_buy = exchange.place_order("XRPUSDT", "BUY", qty, order_type="MARKET", client_order_id=buy_cid)
        buy_order_id = int(market_buy["order_id"])
        buy_record = exchange.get_order("XRPUSDT", client_order_id=buy_cid)
        assert buy_record["status"] == "FILLED"
        buy_trades = exchange.get_my_trades("XRPUSDT", buy_order_id)
        bought_qty = Decimal(str(buy_record["executed_qty"]))
        bought_base_fee = sum((t["commission"] for t in buy_trades if t["commission_asset"] == "XRP"), Decimal(0))
        held_qty = filters.round_qty_down(bought_qty - bought_base_fee)
        db.update_level(
            grid["id"], idx, state="BUY_OPEN", cycles_completed=1,
            client_order_id=buy_cid, order_id=buy_order_id,
        )
        buy_sync = engine.sync_grid(grid["id"])
        assert buy_sync["errors"] == 0
        level = db.get_grid_levels(grid["id"])[idx]
        assert level["state"] == "SELL_OPEN"
        assert Decimal(str(level["held_qty"])) == held_qty

        resting_sell_id = int(level["order_id"])
        canceled_sell = exchange.cancel_order("XRPUSDT", resting_sell_id)
        assert canceled_sell["status"] == "CANCELED"
        sale_cid = f"g{grid['id']}L{idx}S1M"
        market_sell = exchange.place_order("XRPUSDT", "SELL", held_qty, order_type="MARKET", client_order_id=sale_cid)
        sale_order_id = int(market_sell["order_id"])
        db.update_level(grid["id"], idx, client_order_id=sale_cid, order_id=sale_order_id)
        sale = exchange.get_order("XRPUSDT", client_order_id=sale_cid)
        assert sale["status"] == "FILLED"
        sale_trades = exchange.get_my_trades("XRPUSDT", sale_order_id)
        assert sale_trades
        sale_sync = engine.sync_grid(grid["id"])
        closed_level = db.get_grid_levels(grid["id"])[idx]
        assert sale_sync["cycles_completed"] == 1
        assert closed_level["cycles_completed"] == 2

        after_usdt = exchange.get_balance("USDT").get("USDT", {"free": 0, "locked": 0})
        balance_delta = (
            Decimal(str(after_usdt["free"])) + Decimal(str(after_usdt["locked"]))
            - Decimal(str(before_usdt["free"])) - Decimal(str(before_usdt["locked"]))
        )
        # Testnet currently reports 0 commission; 0.05 USDT allows balance/quote precision.
        assert abs(Decimal(str(closed_level["pnl"])) - balance_delta) <= Decimal("0.05")
        print({
            "grid_id": grid["id"], "level_idx": idx, "buy_client_order_id": buy_cid,
            "sale_client_order_id": sale_cid, "buy_order_id": buy_order_id,
            "sale_order_id": sale_order_id, "sale_trades": len(sale_trades),
            "testnet_commission_zero_l3_does_not_validate_fee_deduction": True,
            "pnl": closed_level["pnl"], "usdt_balance_delta": str(balance_delta),
            "cycles_completed": closed_level["cycles_completed"],
        })
    finally:
        if grid is not None:
            try:
                engine.cancel_grid_orders(grid["id"])
            finally:
                verify_no_grid_orders(exchange)
        after_xrp = exchange.get_balance("XRP").get("XRP", {"free": 0, "locked": 0})
        excess = Decimal(str(after_xrp["free"])) - Decimal(str(before_xrp["free"]))
        cleanup_filters = SymbolFilters.from_symbol_info(exchange.get_symbol_info("XRPUSDT"))
        cleanup_qty = min(bought_qty, cleanup_filters.round_qty_down(max(excess, Decimal(0))))
        if cleanup_qty >= cleanup_filters.min_qty:
            exchange.place_order("XRPUSDT", "SELL", cleanup_qty, order_type="MARKET")
        verify_no_grid_orders(exchange)
        final_xrp = exchange.get_balance("XRP").get("XRP", {"free": 0, "locked": 0})
        delta_xrp = Decimal(str(final_xrp["free"])) - Decimal(str(before_xrp["free"]))
        assert abs(delta_xrp) <= cleanup_filters.step_size * 2
        print({
            "cleanup_sale_order_id": sale_order_id, "base_free_before": before_xrp["free"],
            "base_free_after": final_xrp["free"], "base_delta": str(delta_xrp),
            "remaining_xrp_orders": 0,
        })
