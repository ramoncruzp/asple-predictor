"""Diagnósticos secundarios del marcador con datos pasados/futuros separados."""
from __future__ import annotations

import numpy as np
from math import erfc, sqrt
from datetime import datetime, timezone
from scipy.stats import rankdata, spearmanr


def rupture_probabilities(price, low, high, sigma, horizon_h):
    from grid.meta_learning import meta_break_prob_v2
    return {float(k): float(meta_break_prob_v2(price, price * (1-k*sigma),
        price * (1+k*sigma), sigma, horizon_h=horizon_h)) for k in (1, 1.5, 2, 3)}


def _tail_factor(prices, sigma_h, outcomes, horizon_h):
    if not len(outcomes):
        return float("nan")
    observed = float(np.clip(np.mean(outcomes), 1e-9, 1-1e-9))

    def mean_probability(multiplier):
        return min(1.0, 2 * erfc(1 / (multiplier * sqrt(2))))

    left, right = .5, 3.0
    for _ in range(50):
        middle = (left + right) / 2
        if mean_probability(middle) < observed:
            left = middle
        else:
            right = middle
    return (left + right) / 2


def rupture_evaluate(price, sigma_h, future_high, future_low, horizon_h):
    """Evalúa bandas log-simétricas; sigma_h es acumulada en el horizonte H."""
    from grid.meta_learning import meta_break_prob_v2
    price, sigma_h = np.asarray(price, float), np.asarray(sigma_h, float)
    future_high, future_low = np.asarray(future_high, float), np.asarray(future_low, float)
    rows = []
    for k in (1.0, 1.5, 2.0, 3.0):
        lower, upper = price * np.exp(-k*sigma_h), price * np.exp(k*sigma_h)
        actual = ((future_low <= lower) | (future_high >= upper)).astype(float)
        probability = []
        for p, lo, hi, sig in zip(price, lower, upper, sigma_h):
            sigma24 = sig * np.sqrt(24 / horizon_h)
            probability.append(meta_break_prob_v2(p, lo, hi, sigma24, horizon_h=horizon_h))
        probability = np.asarray(probability)
        base = np.asarray([np.mean(actual[:i]) if i else np.nan for i in range(len(actual))])
        valid = np.isfinite(base)
        model_error = (probability-actual)**2
        brier = float(np.mean(model_error[valid])) if valid.any() else float("nan")
        base_brier = float(np.mean((base[valid]-actual[valid])**2)) if valid.any() else float("nan")
        skill = 1-brier/base_brier if base_brier > 0 else float("nan")
        clipped = np.clip(probability, 1e-6, 1-1e-6)
        design = np.column_stack((np.ones(len(clipped)), np.log(clipped/(1-clipped))))
        beta = np.zeros(2)
        for _ in range(30):
            fitted = 1/(1+np.exp(-np.clip(design@beta, -30, 30)))
            weights = np.maximum(fitted*(1-fitted), 1e-8)
            delta = np.linalg.solve(design.T@(weights[:,None]*design)+np.eye(2)*1e-8,
                design.T@(actual-fitted))
            beta += delta
            if np.max(np.abs(delta)) < 1e-7:
                break
        deciles = []
        for subset in np.array_split(np.argsort(probability), 10):
            if len(subset):
                deciles.append({"n": int(len(subset)), "predicted": float(np.mean(probability[subset])),
                    "observed": float(np.mean(actual[subset]))})
        rows.append({"k": k, "n": len(actual), "brier": brier,
            "brier_base_causal": base_brier, "brier_skill": skill,
            "calibration_slope": float(beta[1]), "deciles": deciles,
            "actual": actual, "probability": probability,
            "brier_error": model_error[valid],
            "base_error": (base[valid]-actual[valid])**2})
    return rows, _tail_factor(price, sigma_h, rows[0]["actual"], horizon_h)


def tail_factor_ci(price, sigma_h, outcomes, horizon_h, *, block=7, replicas=1000, seed=43):
    price, sigma_h, outcomes = np.asarray(price), np.asarray(sigma_h), np.asarray(outcomes)
    n = len(outcomes)
    if not n:
        return float("nan"), float("nan")
    rng = np.random.default_rng(seed)
    count = max(1, int(np.ceil(n/block)))
    values = []
    for _ in range(replicas):
        starts = rng.integers(0, n, count)
        indices = np.concatenate([(start + np.arange(block)) % n for start in starts])[:n]
        values.append(_tail_factor(price[indices], sigma_h[indices], outcomes[indices], horizon_h))
    return tuple(float(v) for v in np.quantile(values, [.025, .975]))


def efficiency_ratio(close, length):
    values = np.asarray(close, dtype=float)
    if len(values) <= length:
        return float("nan")
    path = np.abs(np.diff(values[-length-1:])).sum()
    return float(abs(values[-1] - values[-length-1]) / path) if path else 0.0


def cycle_count(close, high, low, price, sigma):
    """Cuenta ciclos completos entre niveles logarítmicos adyacentes.

    El estado es el desplazamiento entero i (inicia en cero); un ciclo se cierra
    cuando el precio vuelve al nivel del que salió en el movimiento anterior.
    """
    close, high, low = np.asarray(close, float), np.asarray(high, float), np.asarray(low, float)
    if not (len(close) == len(high) == len(low)):
        raise ValueError("close, high y low deben tener igual longitud")
    step = max(float(sigma) * .5, 1e-12)
    state, pending_origin, cycles = 0, None, 0
    for close_value, high_value, low_value in zip(close, high, low):
        up = price * np.exp((state + 1) * step)
        down = price * np.exp((state - 1) * step)
        touched_up, touched_down = low_value <= up <= high_value, low_value <= down <= high_value
        if not touched_up and not touched_down:
            continue
        if touched_up and touched_down:
            up_distance = abs(np.log(max(close_value, 1e-300) / up))
            down_distance = abs(np.log(max(close_value, 1e-300) / down))
            direction = 1 if up_distance < down_distance else -1 if down_distance < up_distance else 0
        else:
            direction = 1 if touched_up else -1
        if not direction:
            continue
        next_state = state + direction
        if pending_origin == next_state:
            cycles += 1
            pending_origin = None
        else:
            pending_origin = state
        state = next_state
    return cycles


def monthly_hourly_correlations(symbol_hourly_returns):
    """Retornos por símbolo {timestamp: retorno}, alineados antes de correlacionar."""
    symbols = sorted(symbol_hourly_returns)
    stamps = sorted(set().union(*(set(row) for row in symbol_hourly_returns.values())))
    monthly = {}
    for stamp in stamps:
        month = datetime.fromtimestamp(int(stamp), timezone.utc).strftime("%Y-%m")
        monthly.setdefault(month, []).append(int(stamp))
    output = {}
    for month, month_stamps in monthly.items():
        aligned = [[symbol_hourly_returns[symbol].get(stamp, np.nan) for stamp in month_stamps]
            for symbol in symbols]
        matrix = np.asarray(aligned, float)
        output[month] = np.ma.corrcoef(np.ma.masked_invalid(matrix)).filled(np.nan)
    return symbols, output


def regime_features(close, *, length_h):
    """ER y razón RV_horaria/RV_5m en una ventana pasada completa."""
    values = np.asarray(close, float)
    length = int(length_h) * 12
    if len(values) < length + 1 or np.any(values[-length-1:] <= 0):
        return {"er": float("nan"), "variance_ratio": float("nan")}
    window = values[-length-1:]
    changes = np.diff(window)
    path = float(np.abs(changes).sum())
    er = float(abs(window[-1]-window[0])/path) if path else 0.0
    log_returns = np.diff(np.log(window))
    usable = len(log_returns)//12*12
    hourly = np.sum(log_returns[:usable].reshape(-1, 12), axis=1)
    rv5 = float(np.sum(log_returns**2))
    rvh = float(np.sum(hourly**2))
    return {"er": er, "variance_ratio": rvh/rv5 if rv5 else float("nan")}


def direction_auc(past_return, future_return):
    past, future = np.asarray(past_return, float), np.asarray(future_return, float)
    labels = (future > 0).astype(int)
    if len(labels) != len(past) or len(labels) == 0 or labels.min() == labels.max():
        return float("nan")
    ranks = rankdata(past)
    positive = labels == 1
    n_pos, n_neg = int(positive.sum()), int((~positive).sum())
    return float((ranks[positive].sum()-n_pos*(n_pos+1)/2)/(n_pos*n_neg))


def spearman_block_ci(x, y, *, block=7, replicas=1000, seed=43):
    x, y = np.asarray(x, float), np.asarray(y, float)
    n = min(len(x), len(y))
    rho = float(spearmanr(x[:n], y[:n]).statistic) if n >= 3 else float("nan")
    if n < 3:
        return rho, float("nan"), float("nan")
    rng, values = np.random.default_rng(seed), []
    count = max(1, int(np.ceil(n/block)))
    for _ in range(replicas):
        starts = rng.integers(0, n, count)
        indices = np.concatenate([(start+np.arange(block))%n for start in starts])[:n]
        if np.ptp(x[indices]) and np.ptp(y[indices]):
            values.append(spearmanr(x[indices], y[indices]).statistic)
    low, high = np.quantile(values, [.025,.975]) if values else (float("nan"),float("nan"))
    return rho, float(low), float(high)


def direction_auc_block_ci(past_return, future_return, *, block=7, replicas=1000, seed=43):
    past, future = np.asarray(past_return, float), np.asarray(future_return, float)
    estimate = direction_auc(past, future)
    n = min(len(past), len(future))
    if n < 3:
        return estimate, float("nan"), float("nan")
    rng = np.random.default_rng(seed)
    count = max(1, int(np.ceil(n/block)))
    values = []
    for _ in range(replicas):
        starts = rng.integers(0, n, count)
        idx = np.concatenate([(start+np.arange(block))%n for start in starts])[:n]
        value = direction_auc(past[idx], future[idx])
        if np.isfinite(value):
            values.append(value)
    low, high = np.quantile(values, [.025,.975]) if values else (float("nan"),float("nan"))
    return estimate, float(low), float(high)
