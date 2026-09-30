from decimal import Decimal
import time

import pytest

from data.exchange_filters import SymbolFilters
from tests.test_grid_adjust_live import _unique_grid_sequence
from tests.test_grid_compound_engine import assert_capital_ledger
from tests.test_grid_engine_live import live_context, live_range, verify_no_grid_orders


def _total_xrp(exchange):
    row = exchange.get_balance("XRP").get("XRP", {"free": 0, "locked": 0})
    return Decimal(str(row.get("free", 0))) + Decimal(str(row.get("locked", 0)))


def test_live_l13_compound_enabled_loss_cycle_skips_and_cleans_testnet(tmp_path):
    _settings, exchange, db, engine = live_context(tmp_path)
    if exchange.get_open_orders("XRPUSDT"):
        pytest.skip("la cuenta Testnet ya tiene órdenes XRPUSDT; no se tocarán")
    baseline_xrp = _total_xrp(exchange)
    free_usdt = Decimal(str(exchange.get_balance("USDT")["USDT"]["free"]))
    capital = Decimal("50")
    if free_usdt < capital:
        pytest.skip("USDT libre insuficiente para el ciclo mínimo L13")
    filters = SymbolFilters.from_symbol_info(exchange.get_symbol_info("XRPUSDT"))
    avg = Decimal(str(exchange.get_avg_price("XRPUSDT")))
    low, high = live_range(exchange)
    _unique_grid_sequence(db)
    grid = None
    try:
        grid = engine.create_grid(
            "XRPUSDT", low, high, 5, capital=capital, strategy="smart",
            params={"compound_enabled": True, "compound_ratio": 1.0,
                    "compound_max_growth_pct": 100.0},
        )
        assert_capital_ledger(db, grid["id"])
        cell = next(row for row in db.get_grid_levels(grid["id"]) if row["state"] == "BUY_OPEN")
        original_buy_id = int(cell["order_id"])
        exchange.cancel_order("XRPUSDT", original_buy_id)

        # Cross both sides of the book using only this cell's minimum-sized order.
        buy_price = filters.round_price(avg * Decimal("1.01"), "up")
        sell_price = filters.round_price(avg * Decimal("0.99"), "down")
        quantity = filters.round_qty_down(Decimal(str(cell["capital"])) / buy_price)
        filters.validate_order("BUY", buy_price, quantity, avg)
        cid = f"gL13{grid['id']}L{cell['level_idx']}B0"
        db.update_level(
            grid["id"], cell["level_idx"], state="BUY_OPEN", order_id=None,
            client_order_id=cid, buy_client_order_id=cid,
            price=float(buy_price), sell_price=float(sell_price),
        )
        buy = exchange.place_order(
            "XRPUSDT", "BUY", quantity, price=buy_price, client_order_id=cid,
        )
        db.update_level(grid["id"], cell["level_idx"], order_id=int(buy["order_id"]))
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            buy = exchange.get_order("XRPUSDT", order_id=int(buy["order_id"]))
            if str(buy["status"]).upper() == "FILLED":
                break
            time.sleep(1)
        if str(buy["status"]).upper() != "FILLED":
            pytest.skip("la BUY marketable de Testnet no se llenó dentro del plazo")

        engine.sync_grid(grid["id"])
        sell_cell = db.get_grid_levels(grid["id"])[int(cell["level_idx"])]
        assert sell_cell["state"] == "SELL_OPEN" and sell_cell["held_qty"] > 0
        sell_id = int(sell_cell["order_id"])
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            sell = exchange.get_order("XRPUSDT", order_id=sell_id)
            if str(sell["status"]).upper() == "FILLED":
                break
            time.sleep(1)
        assert str(sell["status"]).upper() == "FILLED", "la SELL marketable de Testnet no se llenó"
        before_capital = (sell_cell["capital"], sell_cell["capital_base"], sell_cell["capital_compound"])
        engine.sync_grid(grid["id"])
        completed = db.get_grid_levels(grid["id"])[int(cell["level_idx"])]
        skipped = db.get_last_event(grid["id"], "COMPOUND_SKIPPED")
        assert completed["cycles_completed"] == 1
        assert skipped is not None and skipped["reason"] == "no_profit"
        assert (completed["capital"], completed["capital_base"], completed["capital_compound"]) == before_capital
        assert completed["capital_compound"] == 0
        assert_capital_ledger(db, grid["id"])
        print({"L13": "passed", "grid_id": grid["id"], "buy_order_id": buy["order_id"],
               "sell_order_id": sell_id, "cycle_pnl": skipped["details"]["cycle_pnl"]})
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
