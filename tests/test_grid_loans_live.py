"""L14-L15 prove loan mechanics on Binance Testnet; Testnet commission is zero."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

pytestmark = pytest.mark.live

from data.exchange_filters import SymbolFilters
from tests.test_grid_monitor_live import _assert_clean, _context


def test_live_l14_l15_binance_loan_resize_repay_and_cleanup():
    """Checks real order resizing and repayment, not profit or live selection thresholds."""
    ctx = _context()
    db, client, engine = ctx["db"], ctx["client"], ctx["engine"]
    try:
        avg = Decimal(str(client.get_avg_price("XRPUSDT")))
        filters = SymbolFilters.from_symbol_info(client.get_symbol_info("XRPUSDT"))
        low = filters.round_price(avg * Decimal("0.93"), "up")
        high = filters.round_price(avg * Decimal("1.10"), "up")
        params = {
            "loans_enabled": True, "reserve_pct": 5.0, "loan_idle_h": 0.001,
            "loan_borrower_min_cycles": 3, "loan_recent_sell_h": 1.0,
            "loan_topup_pct": 50.0, "loan_lender_max_pct": 50.0,
            "loan_cooldown_cycles": 0, "loan_min_margin": 1.1,
            "loan_cap_pct": 30.0, "loan_min_amount": 1.0,
        }
        grid = engine.create_grid("XRPUSDT", low, high, 4, capital=Decimal("120"),
                                  strategy="smart", params=params)
        ctx["grid"] = grid
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        db.update_grid(grid["id"], created_at=now - timedelta(hours=13))
        buy_cells = [row for row in db.get_grid_levels(grid["id"]) if row["state"] == "BUY_OPEN"]
        assert len(buy_cells) >= 2, "L14 needs a borrower and lender BUY"
        borrower, lender = buy_cells[0], buy_cells[1]
        db.update_level(grid["id"], int(borrower["level_idx"]), cycles_completed=3)
        db.add_grid_event(run_id=None, source="CLI", grid_id=grid["id"],
                          level_idx=int(borrower["level_idx"]), event_type="SELL_FILLED",
                          details={"live_test_seed": True}, ts=now)
        lender_before = client.get_order("XRPUSDT", order_id=int(lender["order_id"]))
        borrower_before = client.get_order("XRPUSDT", order_id=int(borrower["order_id"]))
        open_count_before = len(client.get_open_orders("XRPUSDT"))

        l14 = engine.process_grid_loans(grid["id"], now=now)
        assert l14["created"] == 1
        loan = db.list_grid_loans(grid["id"])[0]
        assert loan["status"] == "OPEN"
        levels = {int(row["level_idx"]): row for row in db.get_grid_levels(grid["id"])}
        lender_after = client.get_order("XRPUSDT", order_id=int(levels[lender["level_idx"]]["order_id"]))
        borrower_after = client.get_order("XRPUSDT", order_id=int(levels[borrower["level_idx"]]["order_id"]))
        assert Decimal(str(lender_after["quantity"])) < Decimal(str(lender_before["quantity"]))
        assert Decimal(str(borrower_after["quantity"])) > Decimal(str(borrower_before["quantity"]))
        assert Decimal(str(lender_after["quantity"])) == filters.round_qty_down(
            Decimal(str(levels[lender["level_idx"]]["capital"])) / Decimal(str(lender["price"]))
        )
        assert Decimal(str(borrower_after["quantity"])) == filters.round_qty_down(
            Decimal(str(levels[borrower["level_idx"]]["capital"])) / Decimal(str(borrower["price"]))
        )
        assert len(client.get_open_orders("XRPUSDT")) == open_count_before

        # Seed only the cycle trigger. L15 verifies the actual inverse resize mechanics.
        db.update_level(grid["id"], int(lender["level_idx"]),
                        cycles_completed=int(lender["cycles_completed"]) + 1)
        db.add_grid_event(run_id=None, source="CLI", grid_id=grid["id"],
                          level_idx=int(lender["level_idx"]), event_type="SELL_FILLED",
                          details={"live_test_seed": True}, ts=now + timedelta(seconds=1))
        l15 = engine.process_grid_loans(grid["id"], now=now + timedelta(seconds=2))
        repaid = db.get_grid_loan(int(loan["id"]))
        assert repaid["status"] == "REPAID"
        assert l15["repaid"] == 1
        assert all(row["status"] == "REPAID" for row in db.list_grid_loans(grid["id"]))
        print(f"L14-L15 TESTNET grid={grid['id']} loan={loan['id']} amount={loan['amount']} "
              f"lender_qty={lender_before['quantity']}->{lender_after['quantity']} "
              f"borrower_qty={borrower_before['quantity']}->{borrower_after['quantity']} "
              f"status={repaid['status']}; commission=0; rentabilidad=no evaluada")
    finally:
        _assert_clean(ctx)
