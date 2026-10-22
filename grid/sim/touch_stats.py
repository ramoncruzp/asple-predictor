"""Estadísticas puras de toques compra→venta para el Modo Meta.

No calcula un puntaje ni decide si una pieza es viable. Una venta se reconoce
solo en una vela posterior a la compra porque el OHLC no revela el orden
intracandle cuando X e Y aparecen en la misma vela.
"""
from __future__ import annotations

from statistics import median

import numpy as np


def touch_stats(candles, buy_price, sell_price, horizon_candles):
    """Mide toques X→Y con deduplicación y censura del horizonte incompleto.

    El rearmado ocurre tras una venta o después de que el high alcanza el punto
    medio entre X e Y. Una nueva compra solo puede contarse en una vela posterior.
    """
    lows, highs = _prices(candles)
    buy = float(buy_price)
    sell = float(sell_price)
    horizon = int(horizon_candles)
    if not np.isfinite([buy, sell]).all() or buy <= 0 or sell <= buy or horizon <= 0:
        raise ValueError("X, Y y el horizonte deben ser válidos, con 0 < X < Y")

    raw_touches = np.flatnonzero(lows <= buy)
    sell_hits = np.flatnonzero(highs >= sell)
    rebound_hits = np.flatnonzero(highs >= buy + (sell - buy) / 2)
    accepted = sold = censored = 0
    delays: list[int] = []
    adverse: list[float] = []
    cursor = 0
    size = len(lows)
    while cursor < len(raw_touches):
        index = int(raw_touches[cursor])
        if index + horizon >= size:
            censored += 1
            # No hay evidencia completa; no se etiqueta como fallo. Todavía
            # aplicamos el rearmado para evitar contar una racha como varios toques.
        else:
            accepted += 1
        end = min(size, index + horizon + 1)
        sell_pos = int(np.searchsorted(sell_hits, index + 1, side="left"))
        next_sell_index = int(sell_hits[sell_pos]) if sell_pos < len(sell_hits) else size
        sold_index = next_sell_index if next_sell_index < end else size
        rebound_pos = int(np.searchsorted(rebound_hits, index + 1, side="left"))
        rebound_index = int(rebound_hits[rebound_pos]) if rebound_pos < len(rebound_hits) else size
        event_index = min(next_sell_index, rebound_index)

        if index + horizon < size:
            finish = sold_index if sold_index < size else end - 1
            if sold_index < size:
                sold += 1
                delays.append(sold_index - index)
            worst_low = float(np.min(lows[index:finish + 1]))
            adverse.append(max(0.0, (buy - worst_low) / buy * 100.0))

        if event_index >= size:
            break
        cursor = int(np.searchsorted(raw_touches, event_index + 1, side="left"))

    return {
        "touches": accepted,
        "sold_within_horizon": sold,
        "sell_rate": None if accepted == 0 else sold / accepted,
        "median_candles_to_sell": None if not delays else float(median(delays)),
        "adverse_excursion_mean_pct": None if not adverse else float(np.mean(adverse)),
        "adverse_excursion_max_pct": None if not adverse else float(max(adverse)),
        "censored_touches": censored,
        # Muestras para que el agregado entre ventanas calcule una mediana exacta.
        "sell_candles": delays,
    }


def aggregate_touch_stats(rows):
    """Agrega ventanas/celdas ponderando tasas por sus toques observados."""
    rows = list(rows)
    touches = sum(int(row.get("touches") or 0) for row in rows)
    sold = sum(int(row.get("sold_within_horizon") or 0) for row in rows)
    delays = [int(value) for row in rows for value in row.get("sell_candles", [])]
    adverse_values = [(float(row["adverse_excursion_mean_pct"]), int(row["touches"]))
        for row in rows if row.get("adverse_excursion_mean_pct") is not None and row.get("touches")]
    adverse_count = sum(count for _value, count in adverse_values)
    return {
        "touches": touches,
        "sold_within_horizon": sold,
        "sell_rate": None if touches == 0 else sold / touches,
        "median_candles_to_sell": None if not delays else float(median(delays)),
        "adverse_excursion_mean_pct": None if not adverse_count else
            sum(value * count for value, count in adverse_values) / adverse_count,
        "adverse_excursion_max_pct": max((float(row["adverse_excursion_max_pct"])
            for row in rows if row.get("adverse_excursion_max_pct") is not None), default=None),
        "censored_touches": sum(int(row.get("censored_touches") or 0) for row in rows),
        "sell_candles": delays,
    }


def _prices(candles):
    if isinstance(candles, dict):
        lows, highs = candles["low"], candles["high"]
    elif hasattr(candles, "low") and hasattr(candles, "high"):
        lows, highs = candles.low, candles.high
    else:
        lows, highs = candles["low"], candles["high"]
    lows = np.asarray(lows, dtype=float)
    highs = np.asarray(highs, dtype=float)
    if lows.ndim != 1 or highs.ndim != 1 or len(lows) != len(highs):
        raise ValueError("Las velas deben tener low/high unidimensionales del mismo tamaño")
    return lows, highs
