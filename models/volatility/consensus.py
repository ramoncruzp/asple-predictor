"""Pure helpers for weighted log-volatility consensus forecasts."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

import numpy as np


def inverse_mse_weights(mse_by_model: Mapping[str, float], eligible: Sequence[str]) -> dict[str, float]:
    """Return normalized inverse-MSE weights, with zero for ineligible models."""
    weights = {name: 0.0 for name in mse_by_model}
    valid = {
        name: float(mse_by_model[name])
        for name in eligible
        if name in mse_by_model and np.isfinite(mse_by_model[name]) and mse_by_model[name] > 0
    }
    if valid:
        inverse = {name: 1.0 / mse for name, mse in valid.items()}
        total = sum(inverse.values())
        weights.update({name: value / total for name, value in inverse.items()})
    return weights


def weighted_logvol(predictions: Mapping[str, float], weights: Mapping[str, float]) -> float | None:
    """Combine log-volatility forecasts using injected weights."""
    active = {
        name: float(weight)
        for name, weight in weights.items()
        if weight > 0 and name in predictions and np.isfinite(predictions[name])
    }
    total = sum(active.values())
    if total <= 0:
        return None
    return float(sum(float(predictions[name]) * weight for name, weight in active.items()) / total)


def median_logvol(predictions: Mapping[str, float], eligible: Sequence[str]) -> float | None:
    """Return the median forecast among the supplied eligible models."""
    values = [float(predictions[name]) for name in eligible
              if name in predictions and np.isfinite(predictions[name])]
    return float(np.median(values)) if values else None


def dispersion_confidence(predictions: Mapping[str, float], eligible: Sequence[str]) -> dict:
    """Measure forecast IQR and fixed confidence bucket for one timestamp."""
    values = np.asarray([
        float(predictions[name]) for name in eligible
        if name in predictions and np.isfinite(predictions[name])
    ], dtype="float64")
    if not len(values):
        return {"dispersion_iqr": None, "confidence": "baja", "eligible_count": 0}
    q25, q75 = np.quantile(values, [0.25, 0.75])
    iqr = float(q75 - q25)
    if len(values) < 3 or iqr > 0.25:
        confidence = "baja"
    elif iqr < 0.10:
        confidence = "alta"
    else:
        confidence = "media"
    return {"dispersion_iqr": iqr, "confidence": confidence, "eligible_count": int(len(values))}


def weighted_series(predictions: Mapping[str, np.ndarray], weights: Mapping[str, float]) -> np.ndarray:
    """Combine aligned model prediction arrays using injected weights."""
    active = [name for name, weight in weights.items() if weight > 0 and name in predictions]
    if not active:
        return np.full(0, np.nan, dtype="float64")
    arrays = [np.asarray(predictions[name], dtype="float64") for name in active]
    if len({array.shape for array in arrays}) != 1:
        raise ValueError("Las predicciones ponderadas deben estar alineadas")
    values = np.vstack(arrays)
    model_weights = np.asarray([weights[name] for name in active], dtype="float64")
    finite = np.isfinite(values)
    effective = finite * model_weights[:, None]
    denominator = effective.sum(axis=0)
    numerator = np.where(finite, values, 0.0) * model_weights[:, None]
    result = np.full(values.shape[1], np.nan, dtype="float64")
    valid = denominator > 0
    result[valid] = numerator.sum(axis=0)[valid] / denominator[valid]
    return result


def median_series(predictions: Mapping[str, np.ndarray], eligible: Sequence[str]) -> np.ndarray:
    """Return timestamp-aligned medians for eligible forecasts."""
    names = [name for name in eligible if name in predictions]
    if not names:
        return np.full(0, np.nan, dtype="float64")
    arrays = [np.asarray(predictions[name], dtype="float64") for name in names]
    if len({array.shape for array in arrays}) != 1:
        raise ValueError("Las predicciones medianas deben estar alineadas")
    with np.errstate(all="ignore"):
        return np.nanmedian(np.vstack(arrays), axis=0)
