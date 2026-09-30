"""Offline regression for a replacement order accepted but not linked in SQLite."""

from decimal import Decimal

from tests.grid_fakes import FakeExchange
from tests.test_grid_engine_live import cancel_orders_created_since


def test_adjust_cleanup_cancels_gA_order_missing_from_database_and_preserves_baseline():
    exchange = FakeExchange(fee_rate="0", fee_asset="USDT")
    baseline_order = exchange.place_order(
        "XRPUSDT", "BUY", Decimal("1"), Decimal("90"), client_order_id="baseline-order",
    )
    baseline_ids = {int(baseline_order["order_id"])}
    accepted = exchange.place_order(
        "XRPUSDT", "BUY", Decimal("1"), Decimal("100"),
        client_order_id="gA-replacement-not-linked",
    )
    # Models the ADJUST write-ahead window: the exchange accepted the gA order,
    # but the live test's grid-level row did not receive order_id before error.
    grid_levels = [{"client_order_id": accepted["client_order_id"], "order_id": None}]

    assert grid_levels[0]["order_id"] is None
    cancel_orders_created_since(exchange, baseline_ids)
    remaining = exchange.get_open_orders("XRPUSDT")
    assert [int(order["order_id"]) for order in remaining] == list(baseline_ids)
    assert exchange.get_order("XRPUSDT", order_id=accepted["order_id"])["status"] == "CANCELED"
