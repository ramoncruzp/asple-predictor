"""Evaluación walk-forward causal para el marcador de pronósticos."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

CANDLE_SECONDS = 300


@dataclass(frozen=True)
class EvaluationPoint:
    timestamp: int
    history_end: int
    target_indices: tuple[int, ...]
    realized_sigma: float
    forecast_simple: float
    forecast_ewma: float
    forecast_har: float


def _log_returns(close):
    values = np.asarray(close, dtype=float)
    if len(values) < 2 or np.any(values <= 0):
        return np.asarray([], dtype=float)
    return np.diff(np.log(values))


def _sigma(values):
    return float(np.sqrt(np.sum(np.square(values)))) if len(values) else float("nan")


def walk_forward(candles, *, horizon_h=24, step_h=None, warmup_days=30,
        ewma_lambda_hourly=0.97):
    """Pronostica con filas anteriores a t y puntúa el horizonte futuro.

    Sigma es raíz de la suma de retornos log² acumulada en H. EWMA convierte
    lambda horario a 5 min como lambda_h ** (1/12).
    """
    if horizon_h <= 0:
        raise ValueError("horizon_h debe ser positivo")
    step_h = int(step_h or horizon_h)
    stamps = np.asarray(candles.timestamp, dtype=np.int64)
    close = np.asarray(candles.close, dtype=float)
    horizon = horizon_h * 12
    warmup = warmup_days * 288
    if len(stamps) != len(close):
        raise ValueError("timestamps y cierres deben tener la misma longitud")
    if len(stamps) < 2:
        return [], 0
    diffs = np.diff(stamps)
    nominal = int(np.median(diffs)) if len(diffs) else CANDLE_SECONDS
    nominal = nominal if nominal > 0 else CANDLE_SECONDS
    start = int(stamps[0] + warmup * nominal)
    end = int(stamps[-1] + nominal)
    points, discarded_gaps = [], 0
    cached_har_coef = None
    last_har_fit = None
    log_close = np.log(close)
    returns = np.diff(log_close)
    return_sq_prefix = np.concatenate(([0.0], np.cumsum(returns * returns)))
    lam = ewma_lambda_hourly ** (1.0 / 12.0)
    ewma_num = np.empty(len(returns) + 1, dtype=float)
    ewma_mass = np.empty(len(returns) + 1, dtype=float)
    ewma_num[0] = ewma_mass[0] = 0.0
    for index, ret in enumerate(returns, start=1):
        ewma_num[index] = lam * ewma_num[index - 1] + ret * ret
        ewma_mass[index] = lam * ewma_mass[index - 1] + 1.0
    usable = len(returns) // 12 * 12
    hourly_rv = np.sum(returns[:usable].reshape(-1, 12) ** 2, axis=1)
    hourly_prefix = np.concatenate(([0.0], np.cumsum(hourly_rv)))
    tick = step_h * 3600
    t = start
    while t + horizon_h * 3600 <= end:
        history_end = int(np.searchsorted(stamps, t, side="left"))
        return_end = max(0, history_end - 1)
        target_ts = np.arange(t, t + horizon_h * 3600, CANDLE_SECONDS, dtype=np.int64)
        if len(target_ts) and target_ts[-1] > stamps[-1]:
            break
        target_indices_array = np.arange(history_end, history_end + len(target_ts), dtype=np.int64)
        has_gap = (history_end >= len(stamps) or len(target_indices_array) != len(target_ts) or
            (len(target_indices_array) and
             not np.array_equal(stamps[target_indices_array], target_ts)))
        if history_end < warmup or has_gap:
            discarded_gaps += 1
            t += tick
            continue
        if return_end < horizon:
            t += tick
            continue
        recent_sq = return_sq_prefix[return_end] - return_sq_prefix[return_end - horizon]
        forecast_simple = float(np.sqrt(max(0.0, recent_sq)))
        ewma_var = ewma_num[return_end] / max(ewma_mass[return_end], 1e-300)
        forecast_ewma = float(np.sqrt(max(0.0, ewma_var) * horizon))
        # HAR-RV sobre RV horaria. Reestima cada siete días; cada fila de regresión
        # usa RV diaria, semanal y mensual que terminan antes de su objetivo.
        rv_len = return_end // 12
        rv_h = hourly_rv[:rv_len]
        current_hour = int(t // 3600)
        if last_har_fit is None or current_hour - last_har_fit >= 168:
            j = np.arange(24 * 30, len(rv_h) - 1, dtype=np.int64)
            if len(j) >= 24:
                design = np.column_stack((np.ones(len(j)),
                    hourly_prefix[j + 1] - hourly_prefix[j - 23],
                    hourly_prefix[j + 1] - hourly_prefix[j - 167],
                    hourly_prefix[j + 1] - hourly_prefix[j - 719]))
                cached_har_coef = np.linalg.lstsq(design, rv_h[j + 1], rcond=None)[0]
            last_har_fit = current_hour
        forecast_har = forecast_ewma
        if cached_har_coef is not None and len(rv_h) >= 720:
            components = np.asarray([1.0, hourly_prefix[rv_len] - hourly_prefix[rv_len - 24],
                hourly_prefix[rv_len] - hourly_prefix[rv_len - 168],
                hourly_prefix[rv_len] - hourly_prefix[rv_len - 720]])
            forecast_har = float(np.sqrt(max(0.0, components @ cached_har_coef) * horizon_h))
        target_close = close[target_indices_array]
        target_returns = np.diff(np.log(np.concatenate(([close[history_end - 1]], target_close))))
        points.append(EvaluationPoint(t, int(stamps[history_end - 1]),
            tuple(int(index) for index in target_indices_array),
            _sigma(target_returns), forecast_simple, forecast_ewma, forecast_har))
        t += tick
    return points, discarded_gaps
