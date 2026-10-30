"""Métricas para evaluar sigma acumulada por horizonte."""
from __future__ import annotations

import numpy as np


def qlike(pred, realized):
    pred_var = np.maximum(np.asarray(pred, dtype=float) ** 2, 1e-12)
    realized_var = np.asarray(realized, dtype=float) ** 2
    ratio = realized_var / pred_var
    return ratio - np.log(np.maximum(ratio, 1e-12)) - 1.0


def calibration(pred, realized):
    errors = np.log(np.maximum(np.asarray(pred, float), 1e-12) /
        np.maximum(np.asarray(realized, float), 1e-12))
    return {"log_error_median": float(np.median(errors)), "log_error_mean": float(np.mean(errors)),
        "factor_calibracion": float(np.exp(-np.median(errors))), "n": int(len(errors))}


def interval_coverage(pred, realized, train_pred, train_realized):
    train_error = np.log(np.maximum(train_pred, 1e-12) / np.maximum(train_realized, 1e-12))
    low, high = np.quantile(train_error, [0.1, 0.9]) if len(train_error) else (-np.inf, np.inf)
    current = np.log(np.maximum(pred, 1e-12) / np.maximum(realized, 1e-12))
    return float(np.mean((current >= low) & (current <= high)))


def block_bootstrap_difference(a, b, *, block=7, replicas=1000, seed=43):
    """Intervalo de confianza de media(a-b), con bloques consecutivos."""
    a, b = np.asarray(a, float), np.asarray(b, float)
    n = min(len(a), len(b))
    if not n:
        return float("nan"), float("nan")
    delta = a[:n] - b[:n]
    rng = np.random.default_rng(seed)
    count = max(1, int(np.ceil(n / block)))
    means = np.empty(replicas)
    for i in range(replicas):
        starts = rng.integers(0, n, size=count)
        sample = np.concatenate([delta[(s + np.arange(block)) % n] for s in starts])[:n]
        means[i] = sample.mean()
    return tuple(float(v) for v in np.quantile(means, [0.025, 0.975]))
