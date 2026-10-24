"""Window-level performance and exposure metrics."""
from __future__ import annotations

import numpy as np


def calculate_metrics(equity, capital, cells, exchange, closes, paused=0, trapped_values=None):
    values = np.asarray(equity, dtype=float)
    peak = np.maximum.accumulate(values)
    dd = peak - values
    final = values[-1]
    trapped = trapped_values or [sum(float(c["held_qty"] * c["entry_price"])
                                      for c in cells if c["entry_price"] is not None)]
    return {
        "pnl_realized_gross_usdt": float(exchange.realized + exchange.fees),
        "fees_usdt": float(exchange.fees),
        "pnl_realized_net_usdt": float(exchange.realized),
        "pnl_unrealized_usdt": float(final - capital - float(exchange.realized)),
        "pnl_total_net_usdt": float(final - capital),
        "cycles_completed": sum(c["cycles_completed"] for c in cells),
        "max_drawdown_usdt": float(dd.max()),
        "max_drawdown_pct": float((dd / np.maximum(peak, 1e-12)).max() * 100),
        # Caída máxima medida contra el CAPITAL INICIAL (no contra el máximo de la
        # ventana como max_drawdown_pct): esta es la que se compara con el límite
        # de pérdida del perfil Meta. Ver REPORTE_FASE40C.md, parte B3.
        "worst_loss_vs_capital_pct": float(max(0.0, (capital - values.min()) / capital * 100)),
        "average_trapped_capital_pct": float(np.mean(trapped) / capital * 100),
        "pause_pct": float(paused / max(1, len(values)) * 100),
        "buy_hold_pnl_usdt": float(capital * (closes[-1] / closes[0] - 1)),
    }
