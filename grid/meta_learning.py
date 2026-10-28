"""Etiquetas causales para el aprendizaje en sombra del operador Meta."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from math import erfc, log, sqrt
import numpy as np


HOURS_72 = 72
VOL_FORECAST_HORIZON_H = 24
BACKFILL_DAYS = 180
REPLAY_METHOD_VERSION = "rolling_7d_v3_equity"
LEARNING_SIGNALS = ("volatility", "break_prob", "direction", "scanner", "advisor",
                    "touch_buy_to_sell")
DEFAULT_SIGNAL_WEIGHTS = {name: .25 for name in LEARNING_SIGNALS}
DEFAULT_SIGNAL_WEIGHTS["touch_buy_to_sell"] = 0.0
REPLAY_CACHE_STEP = 0.0025
BREAK_V2_HORIZON_H = 72
BREAK_V2_MARGIN_PCT = 0.0
BREAK_V2_SIGMA_SCALE = 1.0
BREAK_V2_METHOD_VERSION = 1


def meta_break_prob_v2(price, low, high, sigma_24h, *, horizon_h=BREAK_V2_HORIZON_H):
    """Probabilidad de tocar cualquiera de los dos límites del rango Meta.

    Usa distancias logarítmicas, barreras en los límites del rango y sigma
    escalada por la raíz del horizonte. Es una señal experimental en sombra.
    """
    price, low, high, sigma_24h = map(float, (price, low, high, sigma_24h))
    horizon_h = float(horizon_h)
    if min(price, low, high) <= 0 or low >= high or sigma_24h < 0 or horizon_h <= 0:
        raise ValueError("price, range, sigma_24h, or horizon_h is invalid")
    if price < low or price > high:
        return 1.0
    sigma_h = sigma_24h * BREAK_V2_SIGMA_SCALE * sqrt(horizon_h / 24.0)
    if sigma_h == 0:
        return 0.0
    z_low = log(price / low) / sigma_h
    z_high = log(high / price) / sigma_h
    lower = erfc(z_low / sqrt(2.0))
    upper = erfc(z_high / sqrt(2.0))
    return min(1.0, max(0.0, lower + upper))


def normalize_down_probability(p_down, threshold=0.60):
    """Normaliza P(baja) de model_a_down como señal no positiva para compra spot."""
    probability = float(p_down)
    return -min(1.0, max(0.0, (probability - 0.5) / 0.1)) if probability >= threshold else 0.0


def stable_template(parameters, *, meta_capital_usdt):
    """Forma relativa de la cartera, sin precios ni capital absolutos."""
    pieces = []
    denominator = max(float(meta_capital_usdt or parameters.get("capital_usdt") or 0), 1e-12)
    for piece in parameters.get("pieces") or []:
        mid = max(float(piece.get("mid_at_plan") or 0), 1e-12)
        floor = float(piece.get("floor", piece.get("range_low", 0))) / mid
        ceiling = float(piece.get("ceiling", piece.get("range_high", 0))) / mid
        pieces.append({"symbol": str(piece.get("symbol") or "").upper(),
            "piece_type": str(piece.get("piece_type") or "grid"),
            "strategy": str(piece.get("strategy") or "simple"), "n_levels": int(piece.get("n_levels") or 1),
            "floor_mid_q": round(round(floor / REPLAY_CACHE_STEP) * REPLAY_CACHE_STEP, 4),
            "ceiling_mid_q": round(round(ceiling / REPLAY_CACHE_STEP) * REPLAY_CACHE_STEP, 4),
            "capital_fraction": round(float(piece.get("capital_usdt") or 0) / denominator, 4),
            "margin_pct": round(float(piece.get("margin_pct") or 0), 4)})
    canonical = {"base_method_version": REPLAY_METHOD_VERSION,
        "profile": str(parameters.get("profile") or "RAPIDO").upper(),
        "profile_days": int(parameters.get("profile_days") or 20),
        "target_fraction": round(float(parameters.get("target_usdt") or 0) / denominator, 6),
        "max_loss_pct": round(float(parameters.get("max_loss_pct") or 0), 4),
        "pieces": sorted(pieces,
            key=lambda p: (p["symbol"], p["piece_type"], p["strategy"], p["n_levels"],
                           p["floor_mid_q"], p["ceiling_mid_q"], p["capital_fraction"]))}
    encoded = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest(), canonical


def monday_utc_window_starts(earliest, latest, *, step_days=7):
    """Ventanas en una rejilla fija de lunes 00:00 UTC."""
    step, anchor = int(step_days) * 86400, 4 * 86400  # 1970-01-05 fue lunes.
    first = anchor + max(0, (int(earliest) - anchor + step - 1) // step) * step
    return range(first, int(latest) + 1, step)


def _utc_seconds(value):
    if isinstance(value, (int, float, np.integer, np.floating)):
        return int(value)
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return int(value.astimezone(timezone.utc).timestamp())


def non_overlapping_72h_starts(timestamps, *, start=None, end=None):
    """Devuelve inicios alineados a UTC cada 72 h; los bloques no se solapan."""
    times = np.asarray(timestamps, dtype=np.int64)
    if not len(times):
        return []
    lo = int(times[0]) if start is None else _utc_seconds(start)
    hi = int(times[-1]) if end is None else _utc_seconds(end)
    step = HOURS_72 * 3600
    cursor = ((lo + step - 1) // step) * step
    return list(range(cursor, hi - step + 1, step))


def proportional_target_usdt(target_usdt, profile_days, horizon_hours=HOURS_72):
    duration_h = max(1.0, float(profile_days) * 24.0)
    return float(target_usdt) * min(1.0, max(0.0, float(horizon_hours) / duration_h))


def make_learning_label(*, horizon, net_advance_usdt, target_usdt,
                        profile_days, capital_usdt, reached_target=None,
                        elapsed_hours=None, drawdown_pct=None):
    """Forma una etiqueta inmutable; el avance neto y la meta parcial son explícitos."""
    horizon = str(horizon)
    if horizon == "72h":
        required = proportional_target_usdt(target_usdt, profile_days, 72)
        reached = (float(net_advance_usdt) >= required if reached_target is None
                   else bool(reached_target))
    else:
        required = float(target_usdt)
        reached = (float(net_advance_usdt) >= required if reached_target is None
                   else bool(reached_target))
    capital = max(0.0, float(capital_usdt))
    return {
        "horizon": horizon,
        "net_advance_usdt": float(net_advance_usdt),
        "net_advance_pct": (float(net_advance_usdt) / capital * 100.0) if capital else None,
        "required_progress_usdt": required,
        "reached": bool(reached),
        "elapsed_hours": None if elapsed_hours is None else float(elapsed_hours),
        "drawdown_pct": None if drawdown_pct is None else float(drawdown_pct),
        "profile_days": int(profile_days),
        "target_usdt": float(target_usdt),
    }


def causal_replay_probability(candles_by_symbol, *, at, parameters, lookback_days=BACKFILL_DAYS,
                              fixed_week_anchor=False, window_cache=None, template_key=None,
                              filters_by_symbol=None):
    """Estima p_replay con ventanas del plazo y paso de siete días, antes del corte."""
    at_s = _utc_seconds(at)
    days = max(1, int(parameters.get("profile_days", 20)))
    width = days * 86400
    earliest = max(at_s - int(lookback_days) * 86400,
        max((int(candles.timestamp[0]) for candles in candles_by_symbol.values()), default=at_s))
    step = 7 * 86400
    # La misma cadencia que build_windows en el operador. El ancla queda en
    # el primer timestamp disponible para que los cortes sean reproducibles.
    anchor = (4 * 86400 if fixed_week_anchor else
        max((int(c.timestamp[0]) for c in candles_by_symbol.values()), default=earliest))
    first = anchor + max(0, (earliest - anchor + step - 1) // step) * step
    starts = range(first, at_s - width, step)
    outcomes = []
    cached_count = 0
    simulated_count = 0
    label_key = f"{days}d"
    for start_s in starts:
        cache_key = (str(template_key or ""), int(start_s))
        cached = window_cache.get(cache_key) if window_cache is not None else None
        if cached is not None:
            outcomes.append(bool(cached))
            cached_count += 1
            continue
        labels = replay_alternative_labels(candles_by_symbol, start_ts=start_s,
            parameters=parameters, include_horizons=(label_key,),
            filters_by_symbol=filters_by_symbol)
        label = labels.get(label_key, {})
        if label.get("status") == "labeled":
            outcomes.append(bool(label.get("reached")))
            simulated_count += 1
            if window_cache is not None:
                window_cache[cache_key] = bool(label.get("reached"))
    count = len(outcomes)
    raw = (sum(outcomes) / count) if count else None
    return {"probability": raw, "raw_probability": raw,
            "smoothed_probability": ((sum(outcomes) + 1) / (count + 2)) if count else None,
            "independent_windows": min(count, (count * 7) // days),
            "window_count": count, "successes": sum(outcomes), "step_days": 7,
            "cached_windows": cached_count, "simulated_windows": simulated_count,
            "method_version": REPLAY_METHOD_VERSION, "window_days": days, "through_ts": at_s,
            "training_exclusion_reason": "sin base suficiente" if (count * 7) // days < 3 else None}


def effective_sample_rows(rows, *, horizon_days):
    """Conserva una observación por moneda y bloque no solapado del horizonte."""
    width = max(1, int(horizon_days)) * 86400
    selected, last_by_symbol = [], {}
    for row in sorted(rows, key=lambda item: (_utc_seconds(item["sample_at"]), int(item.get("id", 0)))):
        symbol = str(row.get("symbol") or "").upper()
        timestamp = _utc_seconds(row["sample_at"])
        if timestamp - last_by_symbol.get(symbol, -10**30) < width:
            continue
        selected.append(row)
        last_by_symbol[symbol] = timestamp
    return selected


def temporal_learning_split(rows, *, train_fraction=0.7):
    """Divide por fechas de inicio completas: ninguna fecha aparece en ambos lados."""
    dates = sorted({str(row["sample_at"])[:10] for row in rows})
    if len(dates) < 2:
        return [], []
    cut = min(len(dates) - 1, max(1, int(len(dates) * float(train_fraction))))
    validation_start = dates[cut]
    return ([row for row in rows if str(row["sample_at"])[:10] < validation_start],
            [row for row in rows if str(row["sample_at"])[:10] >= validation_start])


def extract_learning_features(signals, weights_by_symbol=None):
    """Mapea únicamente señales numéricas presentes; ausentes quedan neutrales y sin máscara."""
    values, present = {}, set()
    if not isinstance(signals, dict):
        return values, present
    version = signals.get("signals_version", 0)
    try:
        version = int(version)
    except (TypeError, ValueError):
        version = 0
    # v1 arrastraba volatility, break_prob y direction constantes por el defecto de escala/signo.
    legacy_unreliable = {"volatility", "break_prob", "direction"} if version < 2 else set()
    live = signals.get("signals_used")
    if isinstance(live, list):
        for item in live:
            if not isinstance(item, dict):
                continue
            name, value = str(item.get("name") or ""), item.get("normalized")
            if (name in LEARNING_SIGNALS and name not in legacy_unreliable and
                    isinstance(value, (int, float)) and np.isfinite(value)):
                values[name] = float(np.clip(value, -1.0, 1.0))
                present.add(name)
    historical = signals.get("point_in_time_by_symbol")
    volatility_values, direction_values, touch_values = [], [], []
    if isinstance(historical, dict):
        weights_by_symbol = weights_by_symbol or signals.get("point_in_time_weights") or {}
        for symbol, snapshot in historical.items():
            piece_weight = max(0.0, float(weights_by_symbol.get(symbol, 1.0)))
            if not piece_weight:
                continue
            source = (snapshot or {}).get("signals", {})
            touch_rate = source.get("touch_buy_to_sell")
            if touch_rate is not None and np.isfinite(float(touch_rate)):
                touch_values.append((float(np.clip(2 * float(touch_rate) - 1, -1, 1)), piece_weight))
            realized = source.get("realized_volatility_24h")
            forecasts = [float(row["sigma"]) for row in source.get("volatility_forecasts", [])
                if row.get("sigma") is not None and float(row["sigma"]) > 0]
            if version >= 2 and realized is not None and float(realized) > 0 and forecasts:
                normalized = float(np.clip(np.log(float(np.median(forecasts)) / float(realized)), -1, 1))
                volatility_values.append((normalized, piece_weight))
            for row in source.get("direction_predictions", []) if version >= 2 else []:
                if str(row.get("model") or "").lower() != "model_a_down" or str(row.get("interval") or "") != "1h":
                    continue
                probability_down = row.get("probability_up")
                if probability_down is not None:
                    direction_values.append((normalize_down_probability(probability_down), piece_weight))
    if volatility_values:
        values["volatility"] = float(np.average([v for v, _ in volatility_values],
            weights=[w for _, w in volatility_values]))
        present.add("volatility")
    if direction_values:
        values["direction"] = float(np.average([v for v, _ in direction_values],
            weights=[w for _, w in direction_values]))
        present.add("direction")
    if touch_values:
        values["touch_buy_to_sell"] = float(np.average([v for v, _ in touch_values],
            weights=[w for _, w in touch_values]))
        present.add("touch_buy_to_sell")
    return values, present


def holm_adjust(p_values, *, family_size=None):
    """Valores p ajustados por Holm, conservando las claves originales."""
    ordered = sorted((float(p), key) for key, p in p_values.items())
    adjusted, running = {}, 0.0
    total = max(len(ordered), int(family_size or len(ordered)))
    for index, (p_value, key) in enumerate(ordered):
        running = max(running, min(1.0, p_value * (total - index)))
        adjusted[key] = running
    return adjusted


def fit_signal_weights(rows, current_weights=None, *, min_effective=30,
                       max_delta=0.05, max_abs_weight=1.0, logit_cap=1.0,
                       regularization=1.0, alpha=0.05, validation_rows=None):
    """Ajuste penalizado con offset replay, errores cluster bidireccionales y Holm."""
    versions = {row.get("base_method_version") for row in list(rows) + list(validation_rows or [])}
    if len(versions) > 1:
        raise ValueError("no se mezclan versiones de base replay en un ajuste")
    from scipy.optimize import minimize
    from scipy.special import expit, logit
    from scipy.stats import norm

    current = {key: float((current_weights or {}).get(key, DEFAULT_SIGNAL_WEIGHTS[key]))
        for key in LEARNING_SIGNALS}
    parsed = []
    for row in rows:
        base = row.get("base_probability_smoothed")
        if base is None:
            base = row.get("base_probability")
        if base is None or int(row.get("base_independent_windows", row.get("base_window_count")) or 0) < 3:
            continue
        label = row.get("label") or {}
        if "reached" not in label:
            continue
        feature_values, present = extract_learning_features(row.get("signals") or {},
            row.get("feature_weights"))
        parsed.append({**row, "base": float(np.clip(base, 1e-6, 1 - 1e-6)),
            "label": int(bool(label["reached"])), "features": feature_values,
            "present": present})
    parsed_counts = {name: sum(name in item["present"] for item in parsed)
        for name in LEARNING_SIGNALS}
    if len(parsed) < 2:
        return {"status": "insufficient", "weights": current,
                "signals": {name: {"weight": current[name], "candidate": current[name],
                    "effective_samples": parsed_counts[name], "reason": "sin evidencia suficiente"}
                    for name in LEARNING_SIGNALS},
                "reason": "sin base suficiente"}
    names = list(LEARNING_SIGNALS)
    X = np.array([[item["features"].get(name, 0.0) for name in names] for item in parsed])
    y = np.array([item["label"] for item in parsed], dtype=float)
    offsets = logit([item["base"] for item in parsed])
    present_matrix = np.array([[name in item["present"] for name in names] for item in parsed])
    counts = {name: int(present_matrix[:, j].sum()) for j, name in enumerate(names)}
    active = [j for j, name in enumerate(names) if counts[name] >= min_effective]
    if not active:
        return {"status": "insufficient", "weights": current,
            "signals": {name: {"effective_samples": counts[name], "reason": "sin evidencia suficiente",
                "weight": current[name]} for name in names}, "reason": "sin evidencia suficiente"}
    # Señales con menos de 30 observaciones no aportan coeficiente entrenable.
    fixed = np.array([current[n] if j not in active else 0.0 for j, n in enumerate(names)])
    Xa = X[:, active]
    active_names = [names[j] for j in active]
    base_offset = offsets + X @ fixed
    initial = np.array([current[names[j]] for j in active])

    def objective(beta):
        full = fixed.copy()
        full[active] = beta
        logits = np.clip(offsets + np.clip(X @ full, -logit_cap, logit_cap), -35, 35)
        probs = expit(logits)
        loss = -np.sum(y * np.log(np.maximum(probs, 1e-12)) +
            (1-y) * np.log(np.maximum(1-probs, 1e-12)))
        priors = np.array([DEFAULT_SIGNAL_WEIGHTS[active_names[i]] for i in range(len(active_names))])
        return loss + .5 * regularization * float(np.sum((beta - priors) ** 2))
    result = minimize(objective, initial, method="L-BFGS-B",
        bounds=[(-max_abs_weight, max_abs_weight)] * len(active))
    candidate = fixed.copy()
    candidate[active] = result.x
    # Clamp per-row total signal contribution as the runtime does.
    candidate_logits = offsets + np.clip(X @ candidate, -logit_cap, logit_cap)
    baseline_logits = offsets + np.clip(X @ np.array([current[n] for n in names]), -logit_cap, logit_cap)
    p_candidate, p_baseline = expit(candidate_logits), expit(baseline_logits)

    # Sandwich covariance with date and symbol clusters (two-way inclusion-exclusion).
    fitted_full = fixed.copy()
    fitted_full[active] = result.x
    raw_adjustment = X @ fitted_full
    active_rows = (np.abs(raw_adjustment) < logit_cap).astype(float)
    probabilities = expit(offsets + np.clip(raw_adjustment, -logit_cap, logit_cap))
    weights_diag = probabilities * (1 - probabilities)
    hessian = Xa.T @ (weights_diag[:, None] * Xa) + regularization * np.eye(len(active))
    inverse = np.linalg.pinv(hessian)
    residual_score = Xa * ((y - probabilities) * active_rows)[:, None]
    def meat(group_key):
        groups = {}
        for index, item in enumerate(parsed):
            groups.setdefault(group_key(item), np.zeros(len(active)))
            groups[group_key(item)] += residual_score[index]
        return sum((np.outer(score, score) for score in groups.values()), np.zeros((len(active), len(active))))
    date_meat, symbol_meat = meat(lambda item: str(item["sample_at"])[:10]), meat(lambda item: item.get("symbol"))
    joint_meat = meat(lambda item: (str(item["sample_at"])[:10], item.get("symbol")))
    covariance = inverse @ (date_meat + symbol_meat - joint_meat) @ inverse
    se = np.sqrt(np.maximum(0.0, np.diag(covariance)))
    p_values = {name: float(2 * norm.sf(abs(result.x[i]) / max(se[i], 1e-12)))
        for i, name in enumerate(active_names)}
    corrected = holm_adjust(p_values, family_size=len(names))
    signals = {}
    for j, name in enumerate(names):
        if j not in active:
            signals[name] = {"weight": current[name], "candidate": current[name],
                "effective_samples": counts[name], "p_value": None, "p_value_holm": None,
                "reason": "sin evidencia suficiente"}
            continue
        k = active.index(j)
        moved = float(np.clip(candidate[j] - current[name], -max_delta, max_delta))
        proposed = current[name] + moved
        significant = corrected.get(name, 1.0) <= alpha
        signals[name] = {"weight": proposed if significant else current[name],
            "candidate": float(candidate[j]), "effective_samples": counts[name],
            "standard_error_clustered": float(se[k]), "p_value": p_values[name],
            "p_value_holm": corrected[name], "reason": "cambio limitado a 0,05" if significant and abs(moved) > 1e-12
                else ("sin evidencia estadística tras Holm" if not significant else "sin cambio material")}
    accepted = np.array([signals[name]["weight"] for name in names])
    def metrics_for(items, weights):
        yy, xx, pp = [], [], []
        for item in items:
            base = item.get("base_probability_smoothed", item.get("base_probability"))
            if base is None:
                base = item.get("base_probability")
            label = item.get("label") or {}
            if base is None or int(item.get("base_independent_windows", item.get("base_window_count")) or 0) < 3 or "reached" not in label:
                continue
            features, _ = extract_learning_features(item.get("signals") or {},
                item.get("feature_weights"))
            xx.append([features.get(name, 0.0) for name in names])
            yy.append(int(bool(label["reached"])))
            pp.append(float(np.clip(base, 1e-6, 1-1e-6)))
        if not yy:
            return None
        probs_base = expit(logit(pp))
        probs_model = expit(logit(pp) + np.clip(np.asarray(xx) @ weights, -logit_cap, logit_cap))
        def scores(probabilities):
            return {"log_loss": float(-np.mean(np.asarray(yy)*np.log(np.maximum(probabilities, 1e-12))+
                (1-np.asarray(yy))*np.log(np.maximum(1-probabilities, 1e-12)))),
                "brier": float(np.mean((probabilities-np.asarray(yy))**2))}
        return {"baseline": scores(probs_base), "model": scores(probs_model), "n": len(yy)}
    validation = metrics_for(validation_rows or [], accepted)
    baseline_validation = metrics_for(validation_rows or [], np.array([current[n] for n in names]))
    improves = bool(validation and baseline_validation and
        validation["model"]["log_loss"] < baseline_validation["baseline"]["log_loss"] and
        validation["model"]["brier"] < baseline_validation["baseline"]["brier"])
    if not improves:
        accepted = np.array([current[n] for n in names])
        for name, detail in signals.items():
            detail["weight"] = current[name]
            if detail.get("reason") not in {"sin evidencia suficiente"}:
                detail["reason"] = "validación no mejora a solo p_replay"
    return {"status": "fitted", "weights": dict(zip(names, accepted.tolist())),
        "signals": signals, "validation": validation,
        "baseline_validation": baseline_validation, "improves": improves,
        "effective_samples": len(parsed), "optimizer_success": bool(result.success)}


def point_in_time_signals(*, at, candles, buy_price=None, sell_price=None,
                          volatility_forecasts=(), direction_predictions=()):
    """Reconstruye solo señales cuya fuente tiene timestamp <= instante de decisión."""
    at_s = _utc_seconds(at)
    times = np.asarray(candles.timestamp, dtype=np.int64)
    closes = np.asarray(candles.close, dtype=float)
    highs = np.asarray(candles.high, dtype=float)
    lows = np.asarray(candles.low, dtype=float)
    end = int(np.searchsorted(times, at_s, side="right"))
    prior_times, prior_closes = times[:end], closes[:end]
    result = {"signals": {}, "provenance": {}}

    # Volatilidad realizada usa exclusivamente cierres del día anterior al corte.
    cutoff = at_s - 24 * 3600
    lo = int(np.searchsorted(prior_times, cutoff, side="left"))
    trail = prior_closes[lo:]
    if len(trail) >= 2 and np.all(trail > 0):
        returns = np.diff(np.log(trail))
        result["signals"]["realized_volatility_24h"] = float(np.sqrt(np.sum(returns ** 2)))
        result["provenance"]["realized_volatility_24h"] = {
            "source": "velas previas", "through_ts": int(prior_times[-1])}

    def dated(row, names):
        for name in names:
            if row.get(name) is not None:
                try:
                    return _utc_seconds(row[name])
                except (TypeError, ValueError, OverflowError):
                    return None
        return None

    forecasts_by_model = {}
    for row in volatility_forecasts:
        observed_at = dated(row, ("made_at", "forecast_at"))
        if observed_at is None or observed_at > at_s:
            continue
        key = (str(row.get("model_name") or ""), int(row.get("horizon_h") or 0))
        if key not in forecasts_by_model or observed_at > forecasts_by_model[key][0]:
            forecasts_by_model[key] = (observed_at, row)
    if forecasts_by_model:
        forecasts = []
        provenance = []
        for (_model, _horizon), (observed_at, row) in sorted(forecasts_by_model.items()):
            sigma_per_hour = (float(np.exp(row["pred_logvol_cal"]))
                if row.get("pred_logvol_cal") is not None else None)
            forecast = {"model": row.get("model_name"), "value": row.get("pred_logvol_cal"),
                "sigma_per_hour": sigma_per_hour,
                "sigma": (sigma_per_hour * np.sqrt(VOL_FORECAST_HORIZON_H)
                    if sigma_per_hour is not None else None),
                "sigma_24h": (sigma_per_hour * np.sqrt(VOL_FORECAST_HORIZON_H)
                    if sigma_per_hour is not None else None),
                "horizon_h": row.get("horizon_h")}
            forecasts.append(forecast)
            provenance.append({"source": "vol_forecasts", "forecast_at": row.get("forecast_at"),
                "made_at": row.get("made_at"), "effective_at_ts": observed_at})
        result["signals"]["volatility_forecasts"] = forecasts
        result["provenance"]["volatility_forecasts"] = provenance

    predictions_by_model = {}
    for row in direction_predictions:
        observed_at = dated(row, ("predicted_at",))
        if observed_at is None or observed_at > at_s:
            continue
        key = (str(row.get("model_name") or ""), str(row.get("interval") or ""))
        if key not in predictions_by_model or observed_at > predictions_by_model[key][0]:
            predictions_by_model[key] = (observed_at, row)
    if predictions_by_model:
        predictions, provenance = [], []
        for (_model, _interval), (observed_at, row) in sorted(predictions_by_model.items()):
            predictions.append({"signal": row.get("signal"),
                "probability_up": row.get("probability_up"), "model": row.get("model_name"),
                "interval": row.get("interval")})
            provenance.append({"source": "predictions", "predicted_at": row.get("predicted_at"),
                "effective_at_ts": observed_at})
        result["signals"]["direction_predictions"] = predictions
        result["provenance"]["direction_predictions"] = provenance

    # No se reutiliza la tasa de toques retrospectiva de 40b-5: se deriva solo
    # del tramo anterior al corte y la geometría relativa de la alternativa.
    touch = causal_touch_buy_to_sell(at=at_s, candles=candles,
        buy_price=buy_price, sell_price=sell_price)
    if touch["sell_rate"] is not None:
        result["signals"]["touch_buy_to_sell"] = touch["sell_rate"]
        result["provenance"]["touch_buy_to_sell"] = touch["provenance"]
    return result


def causal_touch_buy_to_sell(*, at, candles, buy_price, sell_price):
    """Tasa compra→venta sobre 30 días previos, sin velas posteriores al corte."""
    at_s = _utc_seconds(at)
    times = np.asarray(candles.timestamp, dtype=np.int64)
    end = int(np.searchsorted(times, at_s, side="right"))
    if not end or buy_price is None or sell_price is None or float(buy_price) >= float(sell_price):
        return {"sell_rate": None, "provenance": {"source": "velas previas",
            "lookback_days": 30, "through_ts": None, "stats": None}}
    touch_start = int(np.searchsorted(times, at_s - 30 * 86400, side="left"))
    from grid.sim.data import CandleData
    from grid.sim.touch_stats import touch_stats
    sample = CandleData(timestamp=times[touch_start:end],
        open=np.asarray(candles.open, dtype=float)[touch_start:end],
        high=np.asarray(candles.high, dtype=float)[touch_start:end],
        low=np.asarray(candles.low, dtype=float)[touch_start:end],
        close=np.asarray(candles.close, dtype=float)[touch_start:end], gaps=0)
    stats = touch_stats(sample, float(buy_price), float(sell_price),
        min(24 * 12, max(1, len(sample.timestamp) - 1)))
    return {"sell_rate": stats.get("sell_rate"), "provenance": {
        "source": "velas previas", "lookback_days": 30,
        "through_ts": int(times[end - 1]), "stats": stats}}


def reconstruct_historical_samples(templates, *, candles_by_symbol, source_rows_by_symbol,
                                   now=None, lookback_days=BACKFILL_DAYS, existing_cuts=None,
                                   window_cache=None, max_new_cuts=None, deadline=None,
                                   pause_callback=None, filters_by_symbol=None):
    """Reconstruye snapshots contrafactuales; cada feature queda cortada en su fecha.

    La geometría sirve solo como plantilla de la alternativa. Valores de Scanner,
    Advisor y break_prob no se inventan si no hay un registro fechado anterior.
    """
    now_s = _utc_seconds(now or datetime.now(timezone.utc))
    earliest = now_s - int(lookback_days) * 86400
    output = []
    seen = set()
    existing_cuts = existing_cuts or set()
    pending = []
    new_count = 0
    for template in templates:
        params = template["parameters"]
        template_id = str(template.get("template_id") or template.get("sample_key") or "")
        pieces = list(params.get("pieces") or [])
        if not pieces:
            continue
        primary = str(pieces[0].get("symbol") or "").upper()
        primary_candles = candles_by_symbol.get(primary)
        if primary_candles is None:
            continue
        starts = non_overlapping_72h_starts(primary_candles.timestamp,
            start=max(earliest, int(primary_candles.timestamp[0])), end=now_s)
        for start_s in starts:
            sample_key = hashlib.sha256(("historical:" + template_id + ":" + str(start_s)).encode()).hexdigest()
            if sample_key in existing_cuts:
                continue
            if ((max_new_cuts is not None and new_count >= max_new_cuts) or
                    (deadline is not None and __import__("time").monotonic() >= deadline)):
                pending.append(template_id)
                break
            key = (primary, params.get("profile"), tuple(sorted(
                (str(p.get("symbol")), str(p.get("piece_type")), str(p.get("strategy")),
                 int(p.get("n_levels", 1)), round(float(p.get("floor", 0)) /
                     max(float(p.get("mid_at_plan") or 1), 1e-12), 4),
                 round(float(p.get("ceiling", 0)) /
                     max(float(p.get("mid_at_plan") or 1), 1e-12), 4)) for p in pieces)), start_s)
            if key in seen:
                continue
            seen.add(key)
            rebuilt_pieces, snapshots = [], {}
            valid = True
            for piece in pieces:
                symbol = str(piece.get("symbol") or "").upper()
                candles = candles_by_symbol.get(symbol)
                if candles is None:
                    valid = False
                    break
                times = np.asarray(candles.timestamp, dtype=np.int64)
                idx = int(np.searchsorted(times, start_s, side="right")) - 1
                if idx < 0 or idx + 1 >= len(times):
                    valid = False
                    break
                ref = float(candles.close[idx])
                old_ref = float(piece.get("mid_at_plan") or ref)
                floor_ratio = float(piece.get("floor", piece.get("range_low", 0))) / old_ref
                ceiling_ratio = float(piece.get("ceiling", piece.get("range_high", 0))) / old_ref
                rebuilt = {**piece, "floor": floor_ratio * ref, "ceiling": ceiling_ratio * ref,
                           "mid_at_plan": ref}
                rebuilt_pieces.append(rebuilt)
                forecasts, predictions = source_rows_by_symbol.get(symbol, ([], []))
                snapshots[symbol] = point_in_time_signals(at=start_s, candles=candles,
                    buy_price=rebuilt["floor"], sell_price=rebuilt["ceiling"],
                    volatility_forecasts=forecasts, direction_predictions=predictions)
            if not valid:
                continue
            historical_params = {**params, "pieces": rebuilt_pieces}
            baseline = causal_replay_probability(candles_by_symbol, at=start_s,
                parameters=historical_params, lookback_days=lookback_days,
                fixed_week_anchor=True, window_cache=window_cache, template_key=template_id,
                filters_by_symbol=filters_by_symbol)
            output.append({"template_sample_key": template_id, "stable_sample_key": sample_key,
                "profile": str(params.get("profile", "RAPIDO")).upper(),
                "symbol": primary, "sample_at": start_s, "parameters": historical_params,
                "base_probability": baseline["raw_probability"],
                "base_probability_smoothed": baseline["smoothed_probability"],
                "base_window_count": baseline["window_count"],
                "base_independent_windows": baseline["independent_windows"],
                "base_method_version": REPLAY_METHOD_VERSION,
                "recalculate_base": False,
                "training_exclusion_reason": baseline["training_exclusion_reason"],
                "signals": {"point_in_time_by_symbol": snapshots,
                    "point_in_time_weights": {symbol: sum(float(piece.get("capital_usdt", 0))
                        for piece in rebuilt_pieces if str(piece.get("symbol") or "").upper() == symbol)
                        for symbol in snapshots},
                    "omitted": ["scanner", "advisor", "break_prob"]},
                "provenance": {"source_kind": "historical_reconstructed",
                    "signal_cutoff_ts": start_s, "template_sample_key": template_id,
                    "template_id": template_id,
                    "base_replay": baseline,
                    "template_is_counterfactual": True,
                    "note": "Señales reconstruidas solo con registros y velas anteriores al corte."}})
            new_count += 1
            if pause_callback is not None and new_count % 3 == 0:
                pause_callback()
    return {"samples": output, "pending_templates": sorted(set(pending)),
            "processed_cuts": new_count,
            "cached_windows": sum(int((row.get("provenance", {}).get("base_replay") or {}).get("cached_windows") or 0) for row in output),
            "simulated_windows": sum(int((row.get("provenance", {}).get("base_replay") or {}).get("simulated_windows") or 0) for row in output)}


def replay_alternative_labels(candles_by_symbol, *, start_ts, parameters,
                              include_horizons=None, filters_by_symbol=None):
    """Simula la misma cartera desde un corte y crea etiquetas 72 h y al plazo.

    `parameters` es la instantánea persistida en la evaluación: perfil, capital,
    objetivo, pérdida máxima y piezas. Cada pieza conserva su geometría relativa.
    """
    from grid.sim.data import CandleData
    from grid.sim.meta_bot import run_meta_bot
    from grid.sim.runner import run_simulation
    from grid.levels import GridConfigError
    from grid.sim.runner import FILTERS as SIMULATION_FILTERS

    start_s = _utc_seconds(start_ts)
    profile_days = max(1, int(parameters.get("profile_days", 20)))
    horizons = {"72h": 72, f"{profile_days}d": profile_days * 24}
    if include_horizons is not None:
        allowed = {str(value) for value in include_horizons}
        horizons = {key: value for key, value in horizons.items() if key in allowed}
    total_capital = max(0.0, float(parameters.get("capital_usdt", 0)))
    target = max(0.0, float(parameters.get("target_usdt", 0)))
    pieces = list(parameters.get("pieces") or [])
    if not pieces or total_capital <= 0:
        return {name: {"status": "insufficient", "reason": "sin piezas o capital"}
                for name in horizons}
    result = {}
    for label_horizon, hours in horizons.items():
        end_s = start_s + int(hours) * 3600
        samples = {}
        immature = False
        for piece in pieces:
            symbol = str(piece.get("symbol") or "").upper()
            candles = candles_by_symbol.get(symbol)
            if candles is None:
                immature = True
                break
            timestamps = np.asarray(candles.timestamp, dtype=np.int64)
            lo = int(np.searchsorted(timestamps, start_s, side="left"))
            hi = int(np.searchsorted(timestamps, end_s, side="right"))
            if (hi - lo < 2 or int(timestamps[lo]) > start_s + 300
                    or int(timestamps[hi - 1]) < end_s - 300):
                immature = True
                break
            samples[symbol] = CandleData(timestamp=timestamps[lo:hi],
                open=np.asarray(candles.open)[lo:hi], high=np.asarray(candles.high)[lo:hi],
                low=np.asarray(candles.low)[lo:hi], close=np.asarray(candles.close)[lo:hi], gaps=0)
        if immature:
            result[label_horizon] = {"status": "immature", "required_until": end_s}
            continue
        from grid.sim.meta_portfolio import simulate_piece_curve, evaluate_portfolio
        curves = []
        required_total = proportional_target_usdt(target, profile_days, hours)
        invalid_geometry = None
        for piece in pieces:
            capital = float(piece.get("capital_usdt", 0))
            if capital <= 0:
                continue
            sample = samples[str(piece.get("symbol") or "").upper()]
            ref = float(piece.get("mid_at_plan") or piece.get("reference_price") or sample.open[0])
            scale = float(sample.open[0]) / ref if ref > 0 else 1.0
            floor = float(piece.get("floor", piece.get("range_low", 0))) * scale
            ceiling = float(piece.get("ceiling", piece.get("range_high", 0))) * scale
            strategy = ("individual_bot" if piece.get("piece_type") == "individual_bot_grid"
                else piece.get("strategy", "simple"))
            n_levels = max(1, int(piece.get("n_levels", 4)))
            filters = (filters_by_symbol or {}).get(str(piece.get("symbol") or "").upper())
            try:
                replay = simulate_piece_curve(sample, strategy=strategy,
                    n=n_levels, capital=capital, low=floor, high=ceiling,
                    filters=filters, min_margin_pct=piece.get("margin_pct", .3))
            except (GridConfigError, ValueError) as exc:
                if not isinstance(exc, GridConfigError) and not any(token in str(exc).lower()
                        for token in ("n must", "low must", "valid low/high", "grid lines", "rounded grid")):
                    raise
                tick = getattr(filters or SIMULATION_FILTERS, "tick_size", None)
                invalid_geometry = {"status": "invalid_geometry", "reason": {
                    "symbol": str(piece.get("symbol") or "").upper(),
                    "low": floor, "high": ceiling, "n": n_levels,
                    "tick_used": str(tick) if tick is not None else None,
                    "error": f"{type(exc).__name__}: {exc}"}}
                break
            curves.append(replay["curve"])
        if invalid_geometry is not None:
            result[label_horizon] = invalid_geometry
            continue
        outcome = evaluate_portfolio(curves, capital_initial=total_capital,
            target_usdt=required_total, max_loss_pct=parameters.get("max_loss_pct"))
        result[label_horizon] = make_learning_label(horizon=label_horizon,
            net_advance_usdt=outcome["pnl_usdt"], target_usdt=target,
            profile_days=profile_days, capital_usdt=total_capital,
            reached_target=outcome["reached"], elapsed_hours=(
                outcome["days"] * 24 if outcome["days"] is not None else None),
            drawdown_pct=outcome["drawdown_pct"])
        result[label_horizon].update({"status": "labeled", "sample_start": start_s,
            "sample_end": end_s, "base_method_version": REPLAY_METHOD_VERSION,
            "cash_final": outcome["cash_final"], "loss_cut": outcome["loss_cut"],
            "worst_loss_vs_capital_pct": outcome["worst_loss_vs_capital_pct"],
            "max_advance_usdt": outcome["max_advance_usdt"]})
    return result

