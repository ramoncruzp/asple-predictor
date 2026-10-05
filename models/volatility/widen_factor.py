"""Causal, daily widening-factor estimation from saved volatility forecasts."""
from __future__ import annotations

from bisect import insort
from datetime import datetime, timedelta, timezone
from math import ceil, erf, exp, isfinite, sqrt

import numpy as np

from config.models_config import (
    STRESS_PERCENTILE, WIDEN_DISAGREEMENT_MIN, WIDEN_EMA_ALPHA,
    WIDEN_MAX_CI_WIDTH, WIDEN_MIN_EFFECTIVE,
)

BLOCK_HOURS = 168
BOOTSTRAP_SAMPLES = 1000
SEED = 42
K_MIN = 0.25
K_MAX = 4.0
BISECTION_TOL = 1e-4


def _normal_coverage(scale, rho, multiplier=1.0):
    return sum(erf(multiplier * scale / (value * sqrt(2.0))) for value in rho) / len(rho)


def solve_coverage_factor(rho, target=0.68, multiplier=1.0):
    """Solve mean normal coverage for a forecast-scale multiplier by bisection."""
    values = [float(value) for value in rho if isfinite(float(value)) and float(value) > 0]
    if not values:
        return None
    low, high = K_MIN, K_MAX
    if _normal_coverage(high, values, multiplier) < target:
        return high
    if _normal_coverage(low, values, multiplier) > target:
        return low
    while high - low > BISECTION_TOL:
        mid = (low + high) / 2.0
        if _normal_coverage(mid, values, multiplier) < target:
            low = mid
        else:
            high = mid
    return (low + high) / 2.0


def _utc(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _quantile(values, q):
    return float(np.quantile(np.asarray(values, dtype="float64"), q)) if values else None


def _bootstrap_ci(values, block_size=BLOCK_HOURS, n_bootstrap=BOOTSTRAP_SAMPLES, seed=SEED):
    values = np.asarray(values, dtype="float64")
    if len(values) < block_size:
        return None, None
    rng = np.random.default_rng(seed)
    count = int(ceil(len(values) / block_size))
    offsets = np.arange(block_size)
    boot = np.empty(n_bootstrap, dtype="float64")
    for i in range(n_bootstrap):
        starts = rng.integers(0, len(values), size=count)
        indexes = ((starts[:, None] + offsets[None, :]) % len(values)).reshape(-1)[:len(values)]
        boot[i] = solve_coverage_factor(values[indexes])
    return float(np.quantile(boot, .025)), float(np.quantile(boot, .975))


def _bootstrap_stress_ci(rho_values, is_stress, n_bootstrap=BOOTSTRAP_SAMPLES, seed=SEED):
    rho = np.asarray(rho_values, dtype="float64")
    stress = np.asarray(is_stress, dtype=bool)
    if len(rho) < BLOCK_HOURS or not stress.any() or stress.all():
        return None, None
    rng = np.random.default_rng(seed + 1)
    count = int(ceil(len(rho) / BLOCK_HOURS))
    offsets = np.arange(BLOCK_HOURS)
    boot = []
    for _ in range(n_bootstrap):
        starts = rng.integers(0, len(rho), size=count)
        indexes = ((starts[:, None] + offsets[None, :]) % len(rho)).reshape(-1)[:len(rho)]
        chosen = stress[indexes]
        outside = rho[indexes][~chosen]
        inside = rho[indexes][chosen]
        if not len(inside) or not len(outside):
            continue
        denominator = solve_coverage_factor(outside)
        numerator = solve_coverage_factor(inside)
        if denominator is not None and denominator > 0:
            boot.append(min(2.0, max(1.0, numerator / denominator)))
    if not boot:
        return None, None
    return float(np.quantile(boot, .025)), float(np.quantile(boot, .975))


def _suggestion_ema(raw, previous):
    series = [row for row in previous if row.get("kind", "suggestion") == "suggestion"]
    if raw is None:
        return float(series[-1]["k_stress_smoothed"]) if series and series[-1].get("k_stress_smoothed") is not None else None
    prior = (series[-1].get("k_stress_smoothed") if series else None)
    if prior is None:
        prior = next((row.get("k_stress_raw") for row in reversed(series)
                      if row.get("k_stress_raw") is not None), raw)
    return float(WIDEN_EMA_ALPHA * float(raw) + (1.0 - WIDEN_EMA_ALPHA) * float(prior))


def classify_widen_status(n_effective, ci_width, k_global, k2_global):
    if k_global is not None and k2_global is not None and abs(k_global - k2_global) > .25:
        return "inconsistente"
    if n_effective < WIDEN_MIN_EFFECTIVE or ci_width is None or ci_width > WIDEN_MAX_CI_WIDTH:
        return "acumulando"
    return "disponible"


def effective_sample_count(n, horizon_h):
    return int(n) / int(horizon_h)


def _is_high_stress_iqr(iqr, previous_sorted):
    if iqr is None or not previous_sorted:
        return False
    greater = sum(value > iqr for value in previous_sorted)
    equal = sum(value == iqr for value in previous_sorted)
    total = len(previous_sorted) + 1
    slots = max(0.0, min(float(equal + 1), .20 * total - greater))
    tie_rank = equal + 1
    return int(tie_rank * slots / (equal + 1)) > int((tie_rank - 1) * slots / (equal + 1))


def calculate_widen_factor(rows, horizon_h, champion, val_weights, eligible_models,
                           sigma_ref, computed_at, previous=(), k_active=1.25,
                           disagreement_pct_active=15.0):
    """Calculate champion error scale and causal high-dispersion multiplier."""
    horizon = int(horizon_h)
    now = _utc(computed_at)
    by_time = {}
    for row in rows:
        at = _utc(row["forecast_at"])
        if at <= now:
            by_time.setdefault(at, {})[row["model_name"]] = row
    history_iqr = []
    sorted_iqr = []
    last_threshold = None
    result_rows = []
    disagreement = []
    model_names = [name for name in eligible_models if float(val_weights.get(name, 0.0)) > 0]
    for at in sorted(by_time):
        group = by_time[at]
        values = {name: float(group[name]["pred_logvol_cal"])
                  for name in model_names
                  if name in group and group[name].get("pred_logvol_cal") is not None}
        threshold = None
        if sorted_iqr:
            pos = (len(sorted_iqr) - 1) * STRESS_PERCENTILE / 100.0
            left, right = int(pos), min(int(pos) + 1, len(sorted_iqr) - 1)
            threshold = sorted_iqr[left] + (pos - left) * (sorted_iqr[right] - sorted_iqr[left])
        last_threshold = threshold
        iqr = None
        if len(values) >= 3:
            iqr = float(np.quantile(list(values.values()), .75) - np.quantile(list(values.values()), .25))
        champ = group.get(champion)
        mature = bool(champ and champ.get("realized_logvol") is not None
                      and champ.get("verified_at") is not None
                      and _utc(champ["verified_at"]) <= now
                      and at + timedelta(hours=horizon) <= now)
        if mature:
            pred = float(champ["pred_logvol_cal"])
            realized = float(champ["realized_logvol"])
            rho = exp(realized - pred)
            result_rows.append((at, rho, _is_high_stress_iqr(iqr, sorted_iqr), pred - realized))
        if champ is not None and values and all(name in values for name in model_names):
            total = sum(float(val_weights.get(name, 0.0)) for name in model_names)
            if total > 0:
                consensus = sum(values[name] * float(val_weights[name]) for name in model_names) / total
                relative = abs(exp(consensus) - exp(float(champ["pred_logvol_cal"]))) / exp(float(champ["pred_logvol_cal"]))
                if isfinite(relative):
                    disagreement.append((at, relative * 100.0))
        if iqr is not None:
            insort(sorted_iqr, iqr)
            history_iqr.append((at, iqr))

    rho_values = [item[1] for item in result_rows]
    stress = [item[2] for item in result_rows]
    k_global = solve_coverage_factor(rho_values)
    k2_global = solve_coverage_factor(rho_values, target=.95, multiplier=2.0)
    stress_values = [value for value, flag in zip(rho_values, stress) if flag]
    outside_values = [value for value, flag in zip(rho_values, stress) if not flag]
    denominator = solve_coverage_factor(outside_values)
    numerator = solve_coverage_factor(stress_values)
    raw = numerator / denominator if numerator is not None and denominator is not None and denominator > 0 else None
    global_ci = _bootstrap_ci(rho_values)
    stress_ci = _bootstrap_stress_ci(rho_values, stress)
    global_width = global_ci[1] - global_ci[0] if global_ci[0] is not None else None
    stress_width = stress_ci[1] - stress_ci[0] if stress_ci[0] is not None else None
    n = len(rho_values)
    n_effective = effective_sample_count(n, horizon)
    ci_width = stress_width
    status = classify_widen_status(n_effective, ci_width, k_global, k2_global)
    if raw is not None and raw < 1.0:
        status = "no_aplicable"
    progress_n = min(1.0, n_effective / WIDEN_MIN_EFFECTIVE)
    progress_ci = (min(1.0, WIDEN_MAX_CI_WIDTH / ci_width) if ci_width and ci_width > 0
                   else 1.0 if ci_width is None or ci_width == 0 else 0.0)
    progress = 100.0 * min(progress_n, progress_ci)
    smoothed_raw = _suggestion_ema(raw, previous)
    smoothed = min(2.0, max(1.0, smoothed_raw)) if smoothed_raw is not None else None
    bias_log = float(np.mean([item[3] for item in result_rows])) if result_rows else None
    vol_scale_suggested = exp(-bias_log) if bias_log is not None else None
    threshold_suggested = (_quantile([value for _, value in disagreement], .90)
                           if len(disagreement) >= WIDEN_DISAGREEMENT_MIN else None)
    old = [row for row in previous if row.get("kind", "suggestion") == "suggestion"]
    days_estimate = None
    if status == "acumulando" and n_effective > 0 and n > 1:
        times = [item[0] for item in result_rows]
        span_days = max(1 / 24, (times[-1] - times[0]).total_seconds() / 86400) if len(times) > 1 else 0
        effective_rate = (n / span_days / horizon) if span_days > 0 else 0
        remaining_effective = max(0.0, WIDEN_MIN_EFFECTIVE - n_effective)
        remaining_ci = (max(0.0, n_effective * ((ci_width / WIDEN_MAX_CI_WIDTH) ** 2 - 1.0))
                        if ci_width else 0.0)
        if effective_rate > 0:
            days_estimate = max(remaining_effective, remaining_ci) / effective_rate
    if len(old) >= 2 and n_effective > 0 and days_estimate is None and status == "acumulando":
        first, last = old[-2], old[-1]
        elapsed = (_utc(last["computed_at"]) - _utc(first["computed_at"])).total_seconds() / 86400
        rate = ((float(last.get("n") or 0) - float(first.get("n") or 0)) / horizon / elapsed
                if elapsed > 0 else 0)
        remaining_effective = max(0.0, WIDEN_MIN_EFFECTIVE - n_effective)
        remaining_ci = (max(0.0, n_effective * ((ci_width / WIDEN_MAX_CI_WIDTH) ** 2 - 1.0))
                        if ci_width else 0.0)
        if rate > 0:
            days_estimate = max(remaining_effective, remaining_ci) / rate
    return {
        "symbol": "XRPUSDT", "horizon_h": horizon, "computed_at": now,
        "n": n, "n_effective": n_effective, "k_global": k_global, "k2_global": k2_global,
        "k_raw": raw, "k_stress_raw": raw, "k_stress_smoothed": smoothed,
        "bias_log": bias_log, "vol_scale_suggested": vol_scale_suggested,
        "k_stress_q68": numerator, "k_base_q68": denominator,
        "ci_low": stress_ci[0], "ci_high": stress_ci[1], "ci_width": ci_width,
        "k_global_ci_low": global_ci[0], "k_global_ci_high": global_ci[1],
        "k_global_ci_width": global_width, "k_stress_ci_low": stress_ci[0],
        "k_stress_ci_high": stress_ci[1], "k_stress_ci_width": stress_width,
        "k_active": float(k_active), "status": status,
        "disagreement_threshold_suggested": threshold_suggested,
        "disagreement_n": len(disagreement),
        "disagreement_status": "disponible" if threshold_suggested is not None else "acumulando",
        "disagreement_progress": min(100.0, len(disagreement) * 100.0 / WIDEN_DISAGREEMENT_MIN),
        "progress_pct": progress, "days_estimated": days_estimate,
        "stress_count": sum(stress), "base_count": n - sum(stress),
        "overestimate_message": "El modelo sobreestima la volatilidad ahora; no se sugiere ampliar" if raw is not None and raw < 1.0 else None,
        "stress_threshold": last_threshold,
        "dispersion_n": len(history_iqr),
    }
