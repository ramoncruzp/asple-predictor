"""In-memory spot exchange with deterministic candle-based full fills."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass
class SimOrder:
    cell: int
    side: str
    price: Decimal
    qty: Decimal
    active_from: int
    created_at: int


class SimExchange:
    def __init__(self, capital, fee_pct=.1):
        self.usdt = Decimal(str(capital))
        self.base = Decimal(0)
        self.fees = Decimal(0)
        self.fee_rate = Decimal(str(fee_pct)) / 100
        self.orders = {}
        self.realized = Decimal(0)
        self.events = []

    def place(self, cell, side, price, qty, active_from, created_at):
        self.cancel(cell)
        self.orders[cell] = SimOrder(cell, side, Decimal(str(price)), Decimal(str(qty)), active_from, created_at)

    def cancel(self, cell, side=None):
        order = self.orders.get(cell)
        if order and (side is None or order.side == side):
            del self.orders[cell]

    def process(self, i, low, high, close, cells):
        fills = []
        for idx, order in sorted(tuple(self.orders.items())):
            if i < order.active_from:
                continue
            hit = (order.side == "BUY" and low < float(order.price)) or (order.side == "SELL" and high > float(order.price))
            if not hit:
                continue
            cell = cells[idx]
            gross = order.qty
            if order.side == "BUY":
                cost = order.price * gross
                if cost > self.usdt:
                    raise ValueError("simulated USDT balance would become negative")
                fee = gross * self.fee_rate
                net = gross - fee
                self.usdt -= cost
                self.base += net
                self.fees += fee * order.price
                cell.update(state="SELL_OPEN", held_qty=net, entry_price=order.price, bought_at=i)
                self.cancel(idx)
                fills.append((idx, "BUY", order.price, gross, fee))
            else:
                if gross > self.base or gross > cell["held_qty"]:
                    raise ValueError("simulated base balance would become negative")
                proceeds = order.price * gross
                fee = proceeds * self.fee_rate
                self.usdt += proceeds - fee
                self.base -= gross
                self.fees += fee
                pnl = proceeds - fee - cell["entry_price"] * (gross / (Decimal(1) - self.fee_rate))
                self.realized += pnl
                cell.update(state="IDLE", held_qty=Decimal(0), entry_price=None, bought_at=None,
                            cycles_completed=cell["cycles_completed"] + 1, pnl=cell["pnl"] + pnl)
                self.cancel(idx)
                fills.append((idx, "SELL", order.price, gross, fee))
        return fills

    def market_sell(self, cell, price):
        qty = cell["held_qty"]
        proceeds = qty * Decimal(str(price))
        fee = proceeds * self.fee_rate
        basis = cell["entry_price"] * (qty / (Decimal(1) - self.fee_rate))
        self.base -= qty
        self.usdt += proceeds - fee
        self.fees += fee
        self.realized += proceeds - fee - basis
        cell.update(state="DONE", held_qty=Decimal(0), entry_price=None, bought_at=None,
                    pnl=cell["pnl"] + proceeds - fee - basis)
        return proceeds - fee - basis
