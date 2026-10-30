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
    by_ts = {int(stamp): index for index, stamp in enumerate(stamps)}
    start = int(stamps[0] + warmup * nominal)
    end = int(stamps[-1] + nominal)
    points, discarded_gaps = [], 0
    hourly_rv = []
    cached_har_coef = None
    last_har_fit = None
    tick = step_h * 3600
    t = start
    while t + horizon_h * 3600 <= end:
        history_indices = np.flatnonzero(stamps < t)
        target_ts = np.arange(t, t + horizon_h * 3600, CANDLE_SECONDS, dtype=np.int64)
        if len(target_ts) and target_ts[-1] > stamps[-1]:
            break
        target_indices = tuple(by_ts.get(int(ts), -1) for ts in target_ts)
        if len(history_indices) < warmup or any(index < 0 for index in target_indices):
            discarded_gaps += 1
            t += tick
            continue
        history_returns = _log_returns(close[history_indices])
        recent = history_returns[-horizon:]
        if len(recent) != horizon:
            t += tick
            continue
        lam = ewma_lambda_hourly ** (1.0 / 12.0)
        weights = lam ** np.arange(len(history_returns) - 1, -1, -1)
        weights /= weights.sum()
        forecast_ewma = float(np.sqrt(np.sum(weights * history_returns ** 2) * horizon))
        # HAR-RV sobre RV horaria. Reestima cada siete días; cada fila de regresión
        # usa RV diaria, semanal y mensual que terminan antes de su objetivo.
        usable = len(history_returns) // 12 * 12
        rv_h = np.sum(history_returns[:usable].reshape(-1, 12) ** 2, axis=1)
        current_hour = int(t // 3600)
        if last_har_fit is None or current_hour - last_har_fit >= 168:
            design, response = [], []
            for j in range(24 * 30, len(rv_h) - 1):
                design.append([1.0, rv_h[j-23:j+1].sum(),
                    rv_h[j-167:j+1].sum(), rv_h[j-719:j+1].sum()])
                response.append(rv_h[j+1])
            if len(response) >= 24:
                cached_har_coef = np.linalg.lstsq(np.asarray(design), np.asarray(response), rcond=None)[0]
            last_har_fit = current_hour
        forecast_har = forecast_ewma
        if cached_har_coef is not None and len(rv_h) >= 720:
            components = np.asarray([1.0, rv_h[-24:].sum(), rv_h[-168:].sum(), rv_h[-720:].sum()])
            forecast_har = float(np.sqrt(max(0.0, components @ cached_har_coef) * horizon_h))
        target_close = close[np.asarray(target_indices, dtype=int)]
        target_returns = _log_returns(np.concatenate(([close[history_indices[-1]]], target_close)))
        if len(target_returns) != horizon:
            discarded_gaps += 1
            t += tick
            continue
        points.append(EvaluationPoint(t, int(stamps[history_indices[-1]]), target_indices,
            _sigma(target_returns), _sigma(recent), forecast_ewma, forecast_har))
        t += tick
    return points, discarded_gaps
