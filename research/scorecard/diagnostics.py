"""Diagnósticos secundarios del marcador con datos pasados/futuros separados."""
from __future__ import annotations

import numpy as np


def rupture_probabilities(price, low, high, sigma, horizon_h):
    from grid.meta_learning import meta_break_prob_v2
    return {float(k): float(meta_break_prob_v2(price, price * (1-k*sigma),
        price * (1+k*sigma), sigma, horizon_h=horizon_h)) for k in (1, 1.5, 2, 3)}


def efficiency_ratio(close, length):
    values = np.asarray(close, dtype=float)
    if len(values) <= length:
        return float("nan")
    path = np.abs(np.diff(values[-length-1:])).sum()
    return float(abs(values[-1] - values[-length-1]) / path) if path else 0.0


def cycle_count(close, high, low, price, sigma):
    """Cuenta cruces de ida y vuelta de niveles adyacentes de 0,5 sigma."""
    step = max(float(price) * float(sigma) * 0.5, 1e-12)
    levels = np.asarray([price + i * step for i in range(-20, 21)])
    position = int(np.argmin(abs(levels - price)))
    cycles = 0
    for hi, lo in zip(high, low):
        touched = set(np.flatnonzero((levels >= lo) & (levels <= hi)).tolist())
        for level in list(touched):
            if abs(level - position) == 1:
                position = level
                if abs(position) == 1:
                    cycles += 1
                    position = 0
    return cycles


def monthly_hourly_correlations(symbol_hourly_returns):
    """Matriz mensual alineada por timestamp; faltantes permanecen como NaN."""
    symbols = sorted(symbol_hourly_returns)
    common_months = sorted(set().union(*(set(v) for v in symbol_hourly_returns.values())))
    output = {}
    for month in common_months:
        vectors = [np.asarray(symbol_hourly_returns[symbol].get(month, []), float) for symbol in symbols]
        width = max((len(vector) for vector in vectors), default=0)
        if width < 2:
            continue
        matrix = np.full((len(symbols), width), np.nan)
        for row, vector in enumerate(vectors):
            matrix[row, :len(vector)] = vector
        output[month] = np.ma.corrcoef(np.ma.masked_invalid(matrix)).filled(np.nan)
    return symbols, output
