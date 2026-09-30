"""OHLC replay adapter for running GridEngine against the simulator's candles."""
from __future__ import annotations

import re
from decimal import Decimal

from tests.grid_fakes import FakeExchange


class CandleFakeExchange(FakeExchange):
    """Fill only candle-start orders with the 15A strict penetration rules."""

    def __init__(self, fee_rate="0.001", fee_asset="XRP"):
        super().__init__(fee_rate=fee_rate, fee_asset=fee_asset)
        self.candle_fills = []
        self.candle_index = -1

    @staticmethod
    def _cell_idx(order):
        match = re.search(r"L(\d+)", str(order.get("client_order_id") or ""))
        return int(match.group(1)) if match else 2**31

    def move_price(self, bid, ask, avg=None):
        # advance() already did OHLC matching; update the reference market without
        # letting FakeExchange's bid/ask matcher add a close-only fill.
        self.bid = Decimal(str(bid))
        self.ask = Decimal(str(ask))
        self.avg = Decimal(str(avg if avg is not None else (self.bid + self.ask) / 2))

    def advance(self, low, high, close):
        self.candle_index += 1
        open_at_start = list(self.get_open_orders("XRPUSDT"))
        eligible = []
        for order in open_at_start:
            price = Decimal(str(order["price"]))
            hit = (order["side"] == "BUY" and Decimal(str(low)) < price) or (
                order["side"] == "SELL" and Decimal(str(high)) > price
            )
            if hit:
                eligible.append(order)
        for order in sorted(eligible, key=lambda row: (self._cell_idx(row), int(row["order_id"]))):
            filled = self.fill(int(order["order_id"]))
            trade = self.get_my_trades("XRPUSDT", int(order["order_id"]))[0]
            fee = Decimal(str(trade["commission"]))
            qty = Decimal(str(filled["executed_qty"]))
            net_qty = qty - fee if self.fee_asset == "XRP" and order["side"] == "BUY" else qty
            if order["side"] == "BUY":
                net_qty = self.filters.round_qty_down(net_qty)
            self.candle_fills.append({
                "candle": self.candle_index,
                "level_idx": self._cell_idx(order),
                "side": order["side"],
                "price": Decimal(str(order["price"])),
                "qty": qty,
                "qty_net": net_qty,
                "fee": fee,
                "order_id": int(order["order_id"]),
            })
        self.move_price(close, close, close)
        return tuple(self.candle_fills[-len(eligible):]) if eligible else ()
