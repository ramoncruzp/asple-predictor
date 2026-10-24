"""Fase 40c, parte B3: worst_loss_vs_capital_pct (contra el capital inicial) es
distinto de max_drawdown_pct (desde el maximo de la ventana) -- ver
REPORTE_FASE40C.md para la aclaracion completa."""
from __future__ import annotations

from types import SimpleNamespace

from grid.sim.metrics import calculate_metrics


def test_worst_loss_vs_capital_differs_from_peak_relative_drawdown():
    # Equity sube de 100 a 130 (pico), luego cae a 110: drawdown desde el pico
    # es (130-110)/130 = 15.4%, pero nunca bajo el capital inicial (100), asi
    # que worst_loss_vs_capital_pct debe ser 0.
    equity = [100, 115, 130, 120, 110]
    exchange = SimpleNamespace(realized=10.0, fees=0.5)
    metrics = calculate_metrics(equity, 100, [{"cycles_completed": 1, "entry_price": None, "held_qty": 0}],
                                 exchange, [1, 1, 1, 1, 1])
    assert metrics["max_drawdown_pct"] > 10
    assert metrics["worst_loss_vs_capital_pct"] == 0.0


def test_worst_loss_vs_capital_reflects_a_real_drop_below_initial_capital():
    # Equity cae de 100 a 80 (un 20% bajo el capital inicial) y luego se
    # recupera a 95: worst_loss_vs_capital_pct = (100-80)/100*100 = 20.
    equity = [100, 90, 80, 85, 95]
    exchange = SimpleNamespace(realized=-5.0, fees=0.5)
    metrics = calculate_metrics(equity, 100, [{"cycles_completed": 0, "entry_price": None, "held_qty": 0}],
                                 exchange, [1, 1, 1, 1, 1])
    assert metrics["worst_loss_vs_capital_pct"] == 20.0
