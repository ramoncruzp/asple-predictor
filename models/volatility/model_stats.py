"""Pure live volatility statistics and causal adaptive-weight helpers."""

from __future__ import annotations

from collections import defaultdict
from collections import deque
from datetime import datetime, timedelta, timezone
from math import exp, isfinite, log, sqrt
from statistics import mean, median

import numpy as np

N_MIN = 30
ROLLING_HOURS = 168
ROLLING_VERIFICATIONS = 168
EMA_ALPHA = 0.20
MAX_MODEL_WEIGHT = 0.50
MIN_LIVE_ELIGIBLE = 3
LIVE_VALIDATION_MIN_EFFECTIVE = 50


def as_utc(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def is_mature_verified(row, now):
    """A realized value counts only when its forecast horizon has elapsed."""
    if row.get("realized_logvol") is None or row.get("verified_at") is None:
        return False
    now_utc = as_utc(now)
    return (as_utc(row["forecast_at"]) + timedelta(hours=int(row["horizon_h"])) <= now_utc
            and as_utc(row["verified_at"]) <= now_utc)


def verified_rows(rows, now):
    return [row for row in rows if is_mature_verified(row, now)]


def _metrics(rows, sigma_ref):
    n = len(rows)
    if not n:
        return {"n": 0, "mse": None, "mae": None, "qlike": None, "var_ratio": None,
                "bias_mean": None, "over_pct": None, "under_pct": None,
                "success_1sigma": 0, "success_2sigma": 0, "failures": 0,
                "coverage_1sigma": None, "coverage_2sigma": None,
                "sigma_ref": sigma_ref}
    errors = np.asarray([float(r["pred_logvol_cal"]) - float(r["realized_logvol"]) for r in rows])
    predicted_var = np.exp(2.0 * np.asarray([float(r["pred_logvol_cal"]) for r in rows]))
    realized_var = np.exp(2.0 * np.asarray([float(r["realized_logvol"]) for r in rows]))
    ratio = predicted_var / realized_var
    qlike = float(np.mean(ratio - np.log(ratio) - 1.0))
    abs_error = np.abs(errors)
    hit1 = int(np.sum(abs_error <= sigma_ref)) if sigma_ref is not None else 0
    hit2 = int(np.sum(abs_error <= 2.0 * sigma_ref)) if sigma_ref is not None else 0
    over = int(np.sum(errors > 0))
    under = int(np.sum(errors < 0))
    return {"n": n, "mse": float(np.mean(errors ** 2)), "mae": float(np.mean(abs_error)),
            "qlike": qlike, "var_ratio": float(np.mean(predicted_var) / np.mean(realized_var)),
            "bias_mean": float(np.mean(errors)), "over_pct": over * 100.0 / n,
            "under_pct": under * 100.0 / n, "success_1sigma": hit1,
            "success_2sigma": hit2, "failures": n - hit2 if sigma_ref is not None else 0,
            "coverage_1sigma": hit1 / n if sigma_ref is not None else None,
            "coverage_2sigma": hit2 / n if sigma_ref is not None else None,
            "sigma_ref": sigma_ref}


def capped_normalize(raw, cap=MAX_MODEL_WEIGHT):
    """Normalize nonnegative weights while respecting the per-model cap."""
    cap = max(0.0, float(cap) - 1e-12)
    names = list(raw)
    values = {name: max(0.0, float(raw[name])) for name in names}
    total = sum(values.values())
    if total <= 0:
        return {name: 0.0 for name in names}
    values = {name: value / total for name, value in values.items()}
    for _ in range(len(values) + 1):
        over = [name for name, value in values.items() if value > cap + 1e-12]
        if not over:
            break
        excess = sum(values[name] - cap for name in over)
        for name in over:
            values[name] = cap
        under = [name for name in names if name not in over]
        denom = sum(values[name] for name in under)
        if not under or denom <= 0:
            break
        for name in under:
            values[name] += excess * values[name] / denom
    total = sum(values.values())
    return {name: value / total for name, value in values.items()}


def _rank(values):
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i + 1
        while j < len(order) and values[order[j]] == values[order[i]]:
            j += 1
        rank = (i + 1 + j) / 2.0
        for k in range(i, j):
            ranks[order[k]] = rank
        i = j
    return ranks


def _spearman(xs, ys):
    if len(xs) < 2:
        return None
    rx, ry = np.asarray(_rank(xs)), np.asarray(_rank(ys))
    if np.std(rx) == 0 or np.std(ry) == 0:
        return None
    return float(np.corrcoef(rx, ry)[0, 1])


def dispersion_bucket(predictions, eligible):
    vals = [float(predictions[m]) for m in eligible if m in predictions and isfinite(float(predictions[m]))]
    if len(vals) < 3:
        return None, "baja"
    q25, q75 = np.quantile(vals, [0.25, 0.75])
    iqr = float(q75 - q25)
    level = "alta" if iqr < 0.10 else "media" if iqr <= 0.25 else "baja"
    return iqr, level


def _weights_from_mse(mse, eligible, power=1):
    raw = {name: (1.0 / (max(mse[name], 1e-12) ** power)) for name in eligible
           if name in mse and mse[name] is not None and mse[name] >= 0 and isfinite(mse[name])}
    return capped_normalize(raw) if raw else {}


def adaptive_weight_history(rows, models, val_weights, now, horizon_h=None):
    """Build causal P/P2 weights at each forecast timestamp, then return latest."""
    horizon_h = max(1, int(horizon_h or (rows[0].get("horizon_h", 1) if rows else 1)))
    rolling_window = max(ROLLING_VERIFICATIONS, N_MIN * horizon_h)
    mature = verified_rows(rows, now)
    mature_by_model = defaultdict(list)
    for row in mature:
        mature_by_model[row["model_name"]].append(row)
    for model in mature_by_model:
        mature_by_model[model].sort(key=lambda row: as_utc(row["verified_at"]))
    timestamps = sorted({as_utc(row["forecast_at"]) for row in rows})
    positions = defaultdict(int)
    active = {model: deque() for model in models}
    squared_errors = defaultdict(float)
    previous = {"P": {}, "P2": {}}
    snapshots = []
    for at in timestamps:
        eligible, mse = [], {}
        for model in models:
            ordered = mature_by_model[model]
            window = active[model]
            while (positions[model] < len(ordered)
                   and as_utc(ordered[positions[model]]["verified_at"]) <= at):
                row = ordered[positions[model]]
                error = float(row["pred_logvol_cal"]) - float(row["realized_logvol"])
                window.append(error)
                squared_errors[model] += error * error
                positions[model] += 1
                if len(window) > rolling_window:
                    squared_errors[model] -= window.popleft() ** 2
            if len(window) / horizon_h >= N_MIN:
                mse[model] = squared_errors[model] / len(window)
            else:
                mse[model] = None
        ref = mse.get("Persistence")
        eligible = [m for m in models if m != "Persistence" and mse[m] is not None
                    and ref is not None and mse[m] <= ref]
        raw_p = _weights_from_mse(mse, eligible, 1) if len(eligible) >= MIN_LIVE_ELIGIBLE else {}
        raw_p2 = _weights_from_mse(mse, eligible, 2) if len(eligible) >= MIN_LIVE_ELIGIBLE else {}
        for key, raw in (("P", raw_p), ("P2", raw_p2)):
            if raw:
                smoothed = {m: (1.0 - EMA_ALPHA) * previous[key].get(m, 0.0)
                            + EMA_ALPHA * raw.get(m, 0.0) for m in raw}
                previous[key] = capped_normalize(smoothed)
            else:
                previous[key] = {}
        source = "vivo" if len(eligible) >= MIN_LIVE_ELIGIBLE else "val"
        snapshots.append({"forecast_at": at, "eligible": eligible, "mse": mse,
                          "P": previous["P"].copy() if source == "vivo" else
                              {m: float(val_weights.get(m, 0.0)) for m in models},
                          "P2": previous["P2"].copy(),
                          "confidence": "alta" if source == "vivo" else "baja",
                          "source": source})
    if snapshots:
        latest = dict(snapshots[-1])
    else:
        latest = {"forecast_at": None, "eligible": [], "mse": {}, "P": {}, "P2": {},
                  "confidence": "baja", "source": "val"}
    if latest["source"] == "val":
        latest["P"] = {m: float(val_weights.get(m, 0.0)) for m in models}
        latest["P2"] = {}
    latest["snapshots"] = snapshots
    return latest


def calculate_model_stats(rows, models, champions, sigma_refs, val_weights, now,
                          dispersion_models=None, historical_aggregates=None):
    horizon_value = max(1, int(rows[0]["horizon_h"])) if rows else 1
    rolling_window = max(ROLLING_VERIFICATIONS, N_MIN * horizon_value)
    mature = verified_rows(rows, now)
    by_model = {name: [] for name in models}
    for row in rows:
        if row.get("model_name") in by_model:
            by_model[row["model_name"]].append(row)
    model_results = []
    for model in models:
        all_verified = [r for r in mature if r["model_name"] == model]
        sigma = sigma_refs.get(model)
        time_cutoff = max((as_utc(r["forecast_at"]) for r in rows), default=as_utc(now)) - timedelta(hours=ROLLING_HOURS)
        recent_time = [r for r in all_verified if as_utc(r["forecast_at"]) >= time_cutoff]
        recent_n = all_verified[-rolling_window:]
        full_metrics = (historical_aggregates or {}).get(model, _metrics(all_verified, sigma))
        model_results.append({
            "model_name": model, "n_predicciones": len(by_model[model]), "n_verificadas": len(all_verified),
            "estado": "activo" if len(all_verified) / horizon_value >= N_MIN else "acumulando",
            "all": full_metrics, "bias_alert": _bias_alert(full_metrics, sigma, horizon_value), "last_168h": _metrics(recent_time, sigma),
            "last_168_verifications": _metrics(recent_n, sigma),
            "streak_last_10": _metrics(all_verified[-10:], sigma),
        })
    dispersion_models = dispersion_models or models
    dispersion_rows = []
    by_timestamp = defaultdict(dict)
    for row in rows:
        if row.get("pred_logvol_cal") is not None:
            by_timestamp[(as_utc(row["forecast_at"]), int(row["horizon_h"]))][row["model_name"]] = float(row["pred_logvol_cal"])
    horizon_value = int(rows[0]["horizon_h"]) if rows else None
    horizon_champion = champions.get(horizon_value)
    for champion in ([horizon_champion] if horizon_champion else []):
        for name in ("alta", "media", "baja"):
            selected = []
            iqr_abs = []
            for row in mature:
                if row["model_name"] != champion:
                    continue
                key = (as_utc(row["forecast_at"]), int(row["horizon_h"]))
                iqr, bucket = dispersion_bucket(by_timestamp.get(key, {}), dispersion_models)
                if bucket == name and iqr is not None:
                    selected.append(row)
                    iqr_abs.append((iqr, abs(float(row["pred_logvol_cal"]) - float(row["realized_logvol"]))))
            sigma = sigma_refs.get(champion)
            dispersion_rows.append({"champion": champion, "level": name,
                                    "stats": _metrics(selected, sigma),
                                    "estado": "activo" if len(selected) / horizon_value >= N_MIN else "acumulando"})
    champ_pairs = []
    for row in mature:
        if champions.get(int(row["horizon_h"])) != row["model_name"]:
            continue
        iqr, _bucket = dispersion_bucket(by_timestamp.get((as_utc(row["forecast_at"]), int(row["horizon_h"])), {}), dispersion_models)
        if iqr is not None:
            champ_pairs.append((iqr, abs(float(row["pred_logvol_cal"]) - float(row["realized_logvol"]))))
    weights = adaptive_weight_history(rows, models, val_weights, now,
                                      horizon_h=horizon_value)
    return {"models": model_results, "dispersion": dispersion_rows,
            "dispersion_iqr_error_spearman": _spearman([p[0] for p in champ_pairs], [p[1] for p in champ_pairs]),
            "adaptive": weights, "n_verified": sum(
                1 for row in mature
                if row["model_name"] == champions.get(int(row["horizon_h"]))
            ),
            "validation_status_live": "en_evaluacion"}


def _bias_alert(metrics, sigma_ref, horizon_h=1):
    bias = metrics.get("bias_mean")
    n = int(metrics.get("n") or 0)
    if bias is None or sigma_ref is None or n / max(1, int(horizon_h)) < N_MIN:
        return False
    same_sign = metrics.get("over_pct") if bias > 0 else metrics.get("under_pct") if bias < 0 else 0
    return abs(float(bias)) > 0.5 * float(sigma_ref) and float(same_sign or 0) >= 80.0


def forward_consensus_metrics(rows, models, champions, val_weights, now, sigma_ref=None):
    """Evaluate P/P2/M only on outcomes occurring after each causal weight snapshot."""
    states = adaptive_weight_history(rows, models, val_weights, now,
                                     horizon_h=int(rows[0]["horizon_h"]) if rows else 1)
    mature = verified_rows(rows, now)
    by_time = defaultdict(dict)
    for row in rows:
        by_time[as_utc(row["forecast_at"])][row["model_name"]] = row
    outcomes = defaultdict(list)
    for row in mature:
        outcomes[as_utc(row["forecast_at"])].append(row)
    snapshot_at = {s["forecast_at"]: s for s in states.get("snapshots", [])}
    errors = defaultdict(list)
    cover = defaultdict(lambda: [0, 0])
    for at, outcome_rows in outcomes.items():
        forecasts = by_time.get(at, {})
        snap = snapshot_at.get(at)
        if not snap:
            continue
        realized = float(outcome_rows[0]["realized_logvol"])
        eligible = snap["eligible"] if snap["source"] == "vivo" else [m for m, w in val_weights.items() if w > 0]
        predictions = {m: float(forecasts[m]["pred_logvol_cal"]) for m in eligible if m in forecasts}
        if not predictions:
            continue
        champ = next((r for r in outcome_rows if r["model_name"] == champions.get(int(r["horizon_h"]))), None)
        if champ is None:
            continue
        for variant, weights in (("P", snap["P"]), ("P2", snap["P2"])):
            used = {m: weights.get(m, 0.0) for m in predictions}
            denom = sum(used.values())
            if denom:
                prediction = sum(predictions[m] * used[m] for m in predictions) / denom
                errors[variant].append((prediction - realized, snap["forecast_at"]))
        med = median(predictions.values())
        errors["M"].append((med - realized, snap["forecast_at"]))
        errors["champion"].append((float(champ["pred_logvol_cal"]) - realized, snap["forecast_at"]))
    summary = {}
    for name, values in errors.items():
        err = [v[0] for v in values]
        summary[name] = {"n": len(err), "mse": float(np.mean(np.square(err))) if err else None,
                         "mae": float(np.mean(np.abs(err))) if err else None}
    p_errors = [value[0] for value in errors.get("P", [])]
    if p_errors and sigma_ref is not None:
        summary["P"]["coverage_1sigma"] = sum(abs(e) <= sigma_ref for e in p_errors) / len(p_errors)
        summary["P"]["coverage_2sigma"] = sum(abs(e) <= 2 * sigma_ref for e in p_errors) / len(p_errors)
    else:
        summary.setdefault("P", {"n": 0, "mse": None, "mae": None})
        summary["P"].update({"coverage_1sigma": None, "coverage_2sigma": None})
    n_p = summary.get("P", {}).get("n", 0)
    champion_mse = summary.get("champion", {}).get("mse")
    p_mse = summary.get("P", {}).get("mse")
    # Effective sample count approximates overlap-adjusted independent outcomes.
    coverage_1 = summary["P"].get("coverage_1sigma")
    coverage_2 = summary["P"].get("coverage_2sigma")
    coverage_ok = (coverage_1 is not None and coverage_2 is not None
                   and abs(coverage_1 - 0.68) <= 0.10 and abs(coverage_2 - 0.95) <= 0.10)
    horizon = int(rows[0]["horizon_h"]) if rows else 1
    n_effective = n_p / max(horizon, 1)
    validated = (n_effective >= LIVE_VALIDATION_MIN_EFFECTIVE and champion_mse is not None
                 and p_mse <= champion_mse and coverage_ok)
    return {"forward_evaluation": summary, "outcomes_used": n_p, "n": n_p,
            "n_efectivas": n_effective, "n_efectivas_min": LIVE_VALIDATION_MIN_EFFECTIVE,
            "validation_status_live": "validated" if validated else "en_evaluacion"}
