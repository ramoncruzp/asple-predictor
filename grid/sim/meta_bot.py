"""Replay acotado de un bot Meta de una celda, sin recompra tras su venta."""
from __future__ import annotations

from decimal import Decimal

from grid.sim.runner import FILTERS


def run_meta_bot(candles, *, capital, buy_price, sell_price, target_usdt, fee_pct=.1,
                 filters=None):
    if candles is None or len(candles.timestamp) < 2:
        raise ValueError("el replay del bot requiere al menos dos velas")
    capital, buy_price, sell_price = map(Decimal, map(str, (capital, buy_price, sell_price)))
    fee = Decimal(str(fee_pct)) / Decimal(100)
    filters = FILTERS if filters is None else filters
    if capital <= 0 or buy_price <= 0 or sell_price <= buy_price or fee < 0:
        raise ValueError("capital, rango o comisión inválidos para el bot Meta")
    if (sell_price / buy_price - 1) * 100 - fee * 200 < Decimal("0.30"):
        raise ValueError("margen neto inferior al mínimo del bot Meta")
    qty = filters.round_qty_down(capital / (buy_price * (1 + fee)))
    if qty < filters.min_qty or qty * buy_price < filters.min_notional:
        raise ValueError("notional de compra inferior al mínimo")
    sell_qty = filters.round_qty_down(qty * (1 - fee))
    if sell_qty < filters.min_qty or sell_qty * sell_price < filters.min_notional:
        raise ValueError("notional neto de venta inferior al mínimo")

    cash, inventory, bought, sold = capital, Decimal(0), False, False
    event = None
    peak, max_drawdown_pct = Decimal(0), Decimal(0)
    target_cash = capital + Decimal(str(target_usdt))
    for idx, (low, high, close) in enumerate(zip(candles.low, candles.high, candles.close)):
        was_bought = bought
        if not bought and Decimal(str(low)) <= buy_price:
            total = qty * buy_price * (1 + fee)
            if total <= cash:
                cash -= total
                inventory = qty * (1 - fee)
                bought = True
        elif was_bought and not sold and Decimal(str(high)) >= sell_price:
            cash += inventory * sell_price * (1 - fee)
            inventory = Decimal(0)
            sold = True
        marked = cash + inventory * Decimal(str(close)) * (1 - fee)
        peak = max(peak, marked)
        if peak > 0:
            max_drawdown_pct = max(max_drawdown_pct, (peak - marked) / peak * 100)
        if sold and cash >= target_cash and event is None:
            event = {"type": "TARGET_REACHED", "ts": int(candles.timestamp[idx]),
                     "details": {"cash_total": str(cash), "state": "DONE"}}
    final_mark = cash + inventory * Decimal(str(candles.close[-1])) * (1 - fee)
    return {"reached": event is not None, "event": event,
            "days": (None if event is None else
                     (int(event["ts"]) - int(candles.timestamp[0])) / 86400),
            "drawdown_pct": float(max_drawdown_pct), "windows": 1,
            "final_cash": float(cash), "final_inventory": float(inventory),
            "pnl_usdt": float(final_mark - capital),
            "state": "DONE" if sold else ("SELL_OPEN" if bought else "BUY_OPEN"),
            "rebuys": 0}
