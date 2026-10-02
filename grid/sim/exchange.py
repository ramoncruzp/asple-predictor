"""In-memory spot exchange with deterministic candle-based full fills."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


class SimInsufficientFunds(ValueError):
    """An exchange-style rejection when an order cannot reserve quote funds."""


@dataclass
class SimOrder:
    cell: int
    side: str
    price: Decimal
    qty: Decimal
    active_from: int
    created_at: int
    reserved_usdt: Decimal = Decimal(0)
    order_id: int = 0


class SimExchange:
    def __init__(self, capital, fee_pct=.1, *, filters=None, fee_asset=None):
        self.usdt = Decimal(str(capital))
        self.base = Decimal(0)
        self.fees = Decimal(0)
        self.fee_rate = Decimal(str(fee_pct)) / 100
        self.filters = filters
        self.buy_fee_asset = "XRP" if fee_asset is None else str(fee_asset).upper()
        self.sell_fee_asset = "USDT" if fee_asset is None else str(fee_asset).upper()
        self.orders = {}
        self.next_order_id = 1
        self.realized = Decimal(0)
        self.events = []
        self.dust_qty = Decimal(0)
        self.dust_swept_usdt = Decimal(0)
        self.dust_sweep_fee_usdt = Decimal(0)

    def place(self, cell, side, price, qty, active_from, created_at):
        self.cancel(cell)
        price, qty = Decimal(str(price)), Decimal(str(qty))
        reserve = Decimal(0)
        if side == "BUY":
            reserve = price * qty
            if self.sell_fee_asset == "USDT":
                reserve += reserve * self.fee_rate
            locked = sum((order.reserved_usdt for order in self.orders.values()), Decimal(0))
            if locked + reserve > self.usdt:
                available = self.usdt - locked
                raise SimInsufficientFunds(f"simulated USDT balance insufficient at order placement: "
                                           f"free={available}, required={reserve}")
        self.orders[cell] = SimOrder(cell, side, price, qty, active_from, created_at,
                                     reserve, self.next_order_id)
        self.next_order_id += 1

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
                fee = cost * self.fee_rate if self.buy_fee_asset == "USDT" else Decimal(0)
                if cost + fee > order.reserved_usdt or cost + fee > self.usdt:
                    raise ValueError(f"simulated USDT balance would become negative at candle {i}, "
                                     f"cell {idx}: free={self.usdt}, required={cost + fee}")
                fee = (gross * self.fee_rate if self.buy_fee_asset == "XRP" else fee)
                net = (gross - fee if self.buy_fee_asset == "XRP" else gross)
                raw_net = gross - (fee if self.buy_fee_asset == "XRP" else Decimal(0))
                if self.filters is not None:
                    net = self.filters.round_qty_down(raw_net)
                self.dust_qty += raw_net - net
                self.usdt -= cost + (fee if self.buy_fee_asset == "USDT" else Decimal(0))
                self.base += gross - (fee if self.buy_fee_asset == "XRP" else Decimal(0))
                self.fees += fee * order.price if self.buy_fee_asset == "XRP" else fee
                cell.update(state="SELL_OPEN", held_qty=net, entry_price=order.price,
                            entry_cost=cost,
                            entry_fee_usdt=fee * order.price if self.buy_fee_asset == "XRP" else fee,
                            bought_at=i)
                self.cancel(idx)
                fills.append((idx, "BUY", order.price, gross, fee, net))
            else:
                fee = (gross * self.fee_rate if self.sell_fee_asset == "XRP"
                       else order.price * gross * self.fee_rate)
                base_debit = gross + fee if self.sell_fee_asset == "XRP" else gross
                if base_debit > self.base or gross > cell["held_qty"]:
                    raise ValueError("simulated base balance would become negative")
                proceeds = order.price * gross
                fee_usdt = fee * order.price if self.sell_fee_asset == "XRP" else fee
                self.usdt += proceeds - (fee if self.sell_fee_asset == "USDT" else Decimal(0))
                self.base -= base_debit
                self.fees += fee_usdt
                pnl = proceeds - fee_usdt - cell.get("entry_fee_usdt", Decimal(0)) - cell["entry_cost"]
                self.realized += pnl
                cell.update(state="IDLE", held_qty=Decimal(0), entry_price=None, entry_cost=Decimal(0),
                            entry_fee_usdt=Decimal(0), bought_at=None,
                            cycles_completed=cell["cycles_completed"] + 1, pnl=cell["pnl"] + pnl)
                self.cancel(idx)
                fills.append((idx, "SELL", order.price, gross, fee, gross))
        return fills

    def market_sell(self, cell, price):
        qty = cell["held_qty"]
        proceeds = qty * Decimal(str(price))
        fee = qty * self.fee_rate if self.sell_fee_asset == "XRP" else proceeds * self.fee_rate
        basis = cell["entry_cost"]
        self.base -= qty + (fee if self.sell_fee_asset == "XRP" else Decimal(0))
        self.usdt += proceeds - (fee if self.sell_fee_asset == "USDT" else Decimal(0))
        fee_usdt = fee * Decimal(str(price)) if self.sell_fee_asset == "XRP" else fee
        self.fees += fee_usdt
        realized = proceeds - fee_usdt - cell.get("entry_fee_usdt", Decimal(0)) - basis
        self.realized += realized
        cell.update(state="DONE", held_qty=Decimal(0), entry_price=None, entry_cost=Decimal(0),
                    entry_fee_usdt=Decimal(0), bought_at=None, pnl=cell["pnl"] + realized)
        return realized

    def sweep_dust(self, price, filters):
        from grid.policy import plan_dust_sweep
        plan = plan_dust_sweep(self.dust_qty, price, filters, self.fee_rate * 100)
        if not plan["sweepable"]:
            return plan
        qty = plan["qty"]
        proceeds = qty * Decimal(str(price))
        fee = proceeds * self.fee_rate if self.sell_fee_asset == "USDT" else qty * self.fee_rate
        self.base -= qty + (fee if self.sell_fee_asset == "XRP" else Decimal(0))
        self.usdt += proceeds - (fee if self.sell_fee_asset == "USDT" else Decimal(0))
        self.fees += fee * Decimal(str(price)) if self.sell_fee_asset == "XRP" else fee
        self.dust_qty -= qty
        self.dust_swept_usdt += proceeds - (fee if self.sell_fee_asset == "USDT" else fee * Decimal(str(price)))
        self.dust_sweep_fee_usdt += fee * Decimal(str(price)) if self.sell_fee_asset == "XRP" else fee
        return {**plan, "proceeds_net": proceeds - (fee if self.sell_fee_asset == "USDT" else fee * Decimal(str(price)))}
