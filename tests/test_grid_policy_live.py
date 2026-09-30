"""L7-L9 mechanics against Binance Testnet. Testnet commission is zero and is not validated."""

from __future__ import annotations

from decimal import Decimal

import pytest

from data.exchange_filters import SymbolFilters
from grid.monitor import GridMonitor
from tests.test_grid_monitor_live import _assert_clean, _balance, _context, _create_and_fill_one_buy


def _require_clean_baseline(ctx):
    if ctx["baseline_open"]:
        pytest.skip("Testnet XRPUSDT tiene órdenes preexistentes; no se tocarán")


def test_live_l7_binance_pause_resume_preserves_sell_and_rearms_after_resume():
    """Testnet commission is zero; this verifies order mechanics only."""
    ctx = _context()
    _require_clean_baseline(ctx)
    try:
        grid = _create_and_fill_one_buy(ctx)
        ctx["engine"].sync_grid(grid["id"])
        levels = ctx["db"].get_grid_levels(grid["id"])
        held = next(row for row in levels if row["state"] == "SELL_OPEN")
        kept_sell_id = int(held["order_id"])
        result = ctx["engine"].pause_grid(grid["id"], "live_l7", {"reasons": ["manual_test"]})
        assert result["ok"]
        open_orders = ctx["client"].get_open_orders("XRPUSDT")
        assert all(row["side"] == "SELL" for row in open_orders)
        assert any(int(row["order_id"]) == kept_sell_id for row in open_orders)
        creates = len([row for row in open_orders if row["side"] == "BUY"])
        ctx["engine"].sync_paused(grid["id"])
        assert not any(row["side"] == "BUY" for row in ctx["client"].get_open_orders("XRPUSDT"))
        # Ensure an idle line is safely below the current bid so the subsequent
        # normal ACTIVE synchronization has one deterministic rearm candidate.
        book = ctx["client"].get_book_ticker("XRPUSDT")
        filters = SymbolFilters.from_symbol_info(ctx["client"].get_symbol_info("XRPUSDT"))
        idle = next(row for row in ctx["db"].get_grid_levels(grid["id"]) if row["state"] == "IDLE")
        rearm_price = filters.round_price(book["bid_price"] * Decimal("0.95"), "down")
        ctx["db"].update_level(grid["id"], int(idle["level_idx"]), price=float(rearm_price))
        assert ctx["engine"].resume_grid(grid["id"], "live_l7_clear", {})["ok"]
        ctx["engine"].sync_grid(grid["id"])
        assert any(row["side"] == "BUY" for row in ctx["client"].get_open_orders("XRPUSDT"))
        print(f"LIVE L7 grid={grid['id']} kept_sell_order={kept_sell_id} paused_status=PAUSED resumed_status=ACTIVE")
    finally:
        _assert_clean(ctx)


def test_live_l8_binance_stoploss_sells_exact_owned_quantity():
    """Testnet commission is zero; the elevated entry_price isolates cancel-and-market-sell mechanics."""
    ctx = _context()
    _require_clean_baseline(ctx)
    try:
        grid = _create_and_fill_one_buy(ctx)
        ctx["engine"].sync_grid(grid["id"])
        cell = next(row for row in ctx["db"].get_grid_levels(grid["id"]) if row["state"] == "SELL_OPEN")
        mid = ctx["client"].get_book_ticker("XRPUSDT")
        market_mid = (mid["bid_price"] + mid["ask_price"]) / 2
        elevated_entry = market_mid * Decimal("1.10")
        ctx["db"].set_level_fields(grid["id"], int(cell["level_idx"]),
                                   entry_price=float(elevated_entry), stop_loss_pct=5.0)
        before = ctx["baseline_total"]
        outcome = ctx["engine"].stoploss_cell(grid["id"], int(cell["level_idx"]), "live_l8", {})
        assert outcome["ok"] and outcome["status"] == "FILLED"
        updated = next(row for row in ctx["db"].get_grid_levels(grid["id"]) if row["level_idx"] == cell["level_idx"])
        assert updated["state"] == "DONE" and updated["cycles_completed"] == cell["cycles_completed"]
        assert Decimal(outcome["qty"]) == Decimal(str(cell["held_qty"]))
        final_free, final_locked = _balance(ctx["client"], "XRP")
        filters = SymbolFilters.from_symbol_info(ctx["client"].get_symbol_info("XRPUSDT"))
        assert abs(final_free + final_locked - before) <= filters.step_size
        event = ctx["db"].get_last_event(grid["id"], "CELL_STOPLOSS")
        assert event and event["details"]["entry_price"] == float(elevated_entry)
        assert not ctx["client"].get_open_orders("XRPUSDT")
        print(f"LIVE L8 grid={grid['id']} held_qty={cell['held_qty']} sold_qty={outcome['qty']} xrp_final={final_free + final_locked}")
    finally:
        _assert_clean(ctx)


def test_live_l9_binance_smart_grid_auto_closes_outside_range():
    """Testnet commission is zero; this verifies cancellation and repository-close mechanics only."""
    ctx = _context()
    _require_clean_baseline(ctx)
    try:
        client, engine, db = ctx["client"], ctx["engine"], ctx["db"]
        avg = client.get_avg_price("XRPUSDT")
        filters = SymbolFilters.from_symbol_info(client.get_symbol_info("XRPUSDT"))
        low = filters.round_price(avg * Decimal("0.975"), "up")
        high = filters.round_price(avg * Decimal("1.025"), "up")
        grid = engine.create_grid("XRPUSDT", low, high, 5, capital=Decimal("120"),
                                  strategy="smart", params={"close_out_of_range_pct": 5.0})
        ctx["grid"] = grid
        # The live engine requires the current mid strictly inside the opening
        # range. Seed the out-of-range condition after opening and canceling its
        # owned buys; the monitor then exercises the automatic close path.
        for cell in db.get_grid_levels(grid["id"]):
            if cell.get("order_id") is not None:
                client.cancel_order("XRPUSDT", int(cell["order_id"]))
            db.update_level(grid["id"], int(cell["level_idx"]), state="DONE",
                            order_id=None, client_order_id=None)
        low = filters.round_price(avg * Decimal("0.75"), "up")
        high = filters.round_price(avg * Decimal("0.80"), "up")
        db.update_grid(grid["id"], range_low=float(low), range_high=float(high))
        settings = ctx["settings"]
        monitor = GridMonitor(db, client, engine, settings, vol_provider=type("NoVol", (), {
            "last_reason": "test_scenario", "get": lambda self, _symbol: None,
        })())
        result = monitor.run_once("SCHEDULED")
        assert result["status"] == "OK"
        assert db.get_grid(grid["id"])["status"] == "CLOSED"
        assert db.get_last_event(grid["id"], "GRID_AUTO_CLOSE")
        assert not client.get_open_orders("XRPUSDT")
        print(f"LIVE L9 grid={grid['id']} status=CLOSED trigger=out_of_range remaining_xrp_orders=0")
    finally:
        _assert_clean(ctx)
