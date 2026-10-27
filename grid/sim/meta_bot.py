"""Replay acotado de un bot Meta de una celda, sin recompra tras su venta."""
from __future__ import annotations

from decimal import Decimal

from grid.sim.runner import FILTERS


def run_meta_bot(candles, *, capital, buy_price, sell_price, target_usdt, fee_pct=.1,
                 filters=None, max_loss_pct=None, min_margin_pct=.30, trace_callback=None):
    if candles is None or len(candles.timestamp) < 2:
        raise ValueError("el replay del bot requiere al menos dos velas")
    capital, buy_price, sell_price = map(Decimal, map(str, (capital, buy_price, sell_price)))
    fee = Decimal(str(fee_pct)) / Decimal(100)
    filters = FILTERS if filters is None else filters
    if capital <= 0 or buy_price <= 0 or sell_price <= buy_price or fee < 0:
        raise ValueError("capital, rango o comisión inválidos para el bot Meta")
    if (sell_price / buy_price - 1) * 100 - fee * 200 < Decimal(str(min_margin_pct)):
        raise ValueError("margen neto inferior al mínimo del bot Meta")
    qty = filters.round_qty_down(capital / (buy_price * (1 + fee)))
    if qty < filters.min_qty or qty * buy_price < filters.min_notional:
        raise ValueError("notional de compra inferior al mínimo")
    sell_qty = filters.round_qty_down(qty * (1 - fee))
    if sell_qty < filters.min_qty or sell_qty * sell_price < filters.min_notional:
        raise ValueError("notional neto de venta inferior al mínimo")

    cash, inventory, bought, sold = capital, Decimal(0), False, False
    event = None
    loss_cut = False
    peak, max_drawdown_pct = Decimal(0), Decimal(0)
    trough = capital
    target_cash = capital + Decimal(str(target_usdt)) if target_usdt is not None else None
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
        if trace_callback is not None:
            trace_callback({"timestamp": int(candles.timestamp[idx]), "equity": float(marked),
                            "cash": float(cash), "inventory": float(inventory)})
        peak = max(peak, marked)
        trough = min(trough, marked)
        if peak > 0:
            max_drawdown_pct = max(max_drawdown_pct, (peak - marked) / peak * 100)
        if sold and target_cash is not None and cash >= target_cash and event is None:
            event = {"type": "TARGET_REACHED", "ts": int(candles.timestamp[idx]),
                     "details": {"cash_total": str(cash), "state": "DONE"}}
        if (max_loss_pct is not None and not loss_cut and
                cash + inventory * Decimal(str(low)) * (1 - fee)
                <= capital * (1 - Decimal(str(max_loss_pct)) / 100)):
            marked_low = cash + inventory * Decimal(str(low)) * (1 - fee)
            cash += inventory * Decimal(str(low)) * (1 - fee)
            inventory = Decimal(0)
            loss_cut = True
            event = {"type": "META_LOSS_CUT", "reason": "CORTADA_POR_PERDIDA",
                     "ts": int(candles.timestamp[idx]),
                     "details": {"threshold_pct": float(max_loss_pct),
                         "mark_at_low_usdt": str(marked_low), "realized_cash_usdt": str(cash)}}
            break
    final_mark = cash + inventory * Decimal(str(candles.close[-1])) * (1 - fee)
    trough = min(trough, final_mark)
    worst_loss_vs_capital_pct = max(Decimal(0), (capital - trough) / capital * 100) if capital > 0 else Decimal(0)
    reached = event is not None and event.get("type") == "TARGET_REACHED"
    return {"reached": reached, "event": event,
            "days": (None if not reached else
                     (int(event["ts"]) - int(candles.timestamp[0])) / 86400),
            "drawdown_pct": float(max_drawdown_pct),
            "worst_loss_vs_capital_pct": float(worst_loss_vs_capital_pct), "windows": 1,
            "final_cash": float(cash), "final_inventory": float(inventory),
            "pnl_usdt": float(final_mark - capital),
            "state": ("CORTADA_POR_PERDIDA" if loss_cut else
                      "DONE" if sold else ("SELL_OPEN" if bought else "BUY_OPEN")),
            "bought": bought, "sold": sold, "loss_cut": loss_cut, "rebuys": 0}
