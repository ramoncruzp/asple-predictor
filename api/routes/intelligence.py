"""Consultas agregadas y de solo lectura para la vista Inteligencia."""
from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timedelta, timezone
import math
import statistics

from fastapi import APIRouter, HTTPException, Query, Request
from sqlalchemy import func, select

from api.routes.grids import _authorize

router = APIRouter()


def _json(value, default=None):
    try:
        return json.loads(value or "{}")
    except (TypeError, ValueError):
        return {} if default is None else default


def _ready(weight_rows):
    from grid.meta_learning import DEFAULT_SIGNAL_WEIGHTS
    rows = list(weight_rows)
    result = {}
    for profile in ("RAPIDO", "PRUDENTE"):
        item = next((row for row in rows if row["profile"] == profile), None)
        summary = _json(item["summary_json"]) if item else {}
        signals = summary.get("signals") or {}
        best = max((int(v.get("effective_samples") or 0) for v in signals.values()), default=0)
        conditions = {
            "muestra": best >= 30,
            "significancia_y_cambio": any(float(v.get("p_value_holm") or 1) < .05
                and abs(float(v.get("weight", DEFAULT_SIGNAL_WEIGHTS.get(name, .25))) -
                    DEFAULT_SIGNAL_WEIGHTS.get(name, .25)) > 1e-9 for name, v in signals.items()),
            "validacion": int(summary.get("validation_effective") or 0) >= 5,
            "mejora": bool(summary.get("improves")) and _metrics_improved(summary),
        }
        if not conditions["muestra"]:
            status, label = "gris", "Recolectando datos"
        elif all(conditions.values()):
            status, label = "verde", "Listo para encender"
        else:
            status, label = "amarillo", "Hay datos, sin señal útil todavía"
        result[profile] = {"status": status, "label": label, "best_effective_samples": best,
            "remaining": max(0, 30 - best), "conditions": conditions,
            "signals": [{"name": name, "weight": detail.get("weight", DEFAULT_SIGNAL_WEIGHTS.get(name, .25)),
                "effective_samples": detail.get("effective_samples", 0)} for name, detail in signals.items()]}
    return result


def _metrics_improved(summary):
    model = (summary.get("validation") or {}).get("model") or {}
    baseline = (summary.get("baseline_validation") or {}).get("baseline") or {}
    return bool(model and baseline and model.get("log_loss", 1e9) < baseline.get("log_loss", -1)
        and model.get("brier", 1e9) < baseline.get("brier", -1))


@router.get("")
def intelligence_summary(request: Request, page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200),
                         meta_id: int | None = Query(None, ge=1)):
    _authorize(request)
    db = getattr(request.app.state, "db", None)
    if db is None:
        raise HTTPException(503, "Base de datos no disponible")
    with db.engine.connect() as conn:
        forecasts = conn.execute(select(db.vol_forecasts.c.symbol, db.vol_forecasts.c.model_name,
            db.vol_forecasts.c.horizon_h, func.count().label("count"),
            func.min(db.vol_forecasts.c.made_at).label("first_at"),
            func.max(db.vol_forecasts.c.made_at).label("last_at")).group_by(
                db.vol_forecasts.c.symbol, db.vol_forecasts.c.model_name,
                db.vol_forecasts.c.horizon_h)).mappings().all()
        predictions = conn.execute(select(db.predictions.c.symbol, db.predictions.c.model_name,
            db.predictions.c.interval, func.count().label("count"),
            func.min(db.predictions.c.predicted_at).label("first_at"),
            func.max(db.predictions.c.predicted_at).label("last_at")).group_by(
                db.predictions.c.symbol, db.predictions.c.model_name,
                db.predictions.c.interval)).mappings().all()
        observations = conn.execute(select(db.meta_signal_observations.c.symbol,
            func.count().label("count"), func.min(db.meta_signal_observations.c.created_at).label("first_at"),
            func.max(db.meta_signal_observations.c.created_at).label("last_at")).group_by(
                db.meta_signal_observations.c.symbol)).mappings().all()
        observation_json = conn.execute(select(db.meta_signal_observations.c.symbol,
            db.meta_signal_observations.c.decision_id, db.meta_signal_observations.c.alternative_id,
            db.meta_signal_observations.c.stats_json, db.meta_signal_observations.c.created_at)).all()
        ledger = conn.execute(select(db.meta_ledger.c.meta_id, db.meta_ledger.c.event_type,
            func.count().label("count")).group_by(db.meta_ledger.c.meta_id,
                db.meta_ledger.c.event_type).order_by(db.meta_ledger.c.meta_id,
                db.meta_ledger.c.event_type)).mappings().all()
        allocation_shadow_rows = conn.execute(select(db.meta_ledger.c.meta_id, db.meta_ledger.c.ts,
            db.meta_ledger.c.details).where(db.meta_ledger.c.event_type == "META_OPERATOR_ALLOCATION_SHADOW")
            .order_by(db.meta_ledger.c.id.desc()).limit(10)).mappings().all()
        bot_shadow_rows = conn.execute(select(db.meta_ledger.c.id, db.meta_ledger.c.meta_id,
            db.meta_ledger.c.ts, db.meta_ledger.c.event_type, db.meta_ledger.c.details).where(
                db.meta_ledger.c.event_type.in_(("META_BOT_TOUCH_SHADOW", "META_BOT_TOUCH_SHADOW_LABEL")))
            .order_by(db.meta_ledger.c.id.desc()).limit(200)).mappings().all()
        sample_groups = conn.execute(select(db.meta_learning_samples.c.profile,
            db.meta_learning_samples.c.source_kind, db.meta_learning_samples.c.symbol,
            func.count().label("count"), func.min(db.meta_learning_samples.c.sample_at).label("first_at"),
            func.max(db.meta_learning_samples.c.sample_at).label("last_at")).group_by(
                db.meta_learning_samples.c.profile, db.meta_learning_samples.c.source_kind,
                db.meta_learning_samples.c.symbol)).mappings().all()
        alternatives_today = conn.execute(select(func.count()).select_from(db.meta_learning_samples).where(
            db.meta_learning_samples.c.sample_at >= datetime.now(timezone.utc).replace(hour=0, minute=0,
                second=0, microsecond=0))).scalar_one()
        plans_today = conn.execute(select(db.meta_ledger.c.details).where(
            db.meta_ledger.c.event_type == "META_PLAN_EVALUATED",
            db.meta_ledger.c.ts >= datetime.now(timezone.utc).replace(hour=0, minute=0,
                second=0, microsecond=0))).all()
        exclusions = conn.execute(select(db.meta_learning_samples.c.training_exclusion_reason,
            func.count().label("count")).where(db.meta_learning_samples.c.training_exclusion_reason.is_not(None),
                db.meta_learning_samples.c.training_exclusion_reason != "").group_by(
                    db.meta_learning_samples.c.training_exclusion_reason)).mappings().all()
        recalc = conn.execute(select(func.count()).select_from(db.meta_learning_samples).where(
            db.meta_learning_samples.c.recalculate_base.is_(True))).scalar_one()
        label_rows = conn.execute(select(db.meta_learning_labels.c.sample_id,
            db.meta_learning_labels.c.horizon, db.meta_learning_labels.c.label_json)).all()
        sample_rows = conn.execute(select(db.meta_learning_samples.c.id,
            db.meta_learning_samples.c.profile,
            db.meta_learning_samples.c.source_kind, db.meta_learning_samples.c.symbol,
            db.meta_learning_samples.c.base_window_count,
            db.meta_learning_samples.c.base_probability,
            db.meta_learning_samples.c.base_probability_smoothed,
            db.meta_learning_samples.c.signals_json,
            db.meta_learning_samples.c.training_exclusion_reason).order_by(
                db.meta_learning_samples.c.id.desc()).limit(10000)).all()
        latest_run = conn.execute(select(db.meta_learning_runs).order_by(
            db.meta_learning_runs.c.id.desc()).limit(1)).mappings().first()
        runs = conn.execute(select(db.meta_learning_runs.c.id, db.meta_learning_runs.c.started_at,
            db.meta_learning_runs.c.finished_at, db.meta_learning_runs.c.status,
            db.meta_learning_runs.c.summary_json).order_by(db.meta_learning_runs.c.id.desc()).offset(
                (page - 1) * page_size).limit(page_size)).mappings().all()
        version_rows = conn.execute(select(db.meta_learning_weight_versions).order_by(
            db.meta_learning_weight_versions.c.id.desc()).offset((page - 1) * page_size).limit(page_size)).mappings().all()
        switch_rows = conn.execute(select(db.meta_learning_switch_events).order_by(
            db.meta_learning_switch_events.c.id.desc()).limit(20)).mappings().all()
        ledger_stmt = select(db.meta_ledger.c.id, db.meta_ledger.c.ts, db.meta_ledger.c.meta_id,
            db.meta_ledger.c.event_type, db.meta_ledger.c.details).where(
                db.meta_ledger.c.event_type.in_(("META_PLAN_EVALUATED", "META_OPERATOR_REVIEW_DECISION"))
            )
        if meta_id is not None:
            ledger_stmt = ledger_stmt.where(db.meta_ledger.c.meta_id == meta_id)
        ledger_events = conn.execute(ledger_stmt.order_by(db.meta_ledger.c.id.desc()).offset(
            (page - 1) * page_size).limit(page_size)).mappings().all()
        ledger_total = conn.execute(select(func.count()).select_from(db.meta_ledger).where(
            db.meta_ledger.c.event_type.in_(("META_PLAN_EVALUATED", "META_OPERATOR_REVIEW_DECISION")),
            *([db.meta_ledger.c.meta_id == meta_id] if meta_id is not None else []))).scalar_one()
        all_decision_events = conn.execute(select(db.meta_ledger.c.details).where(
            db.meta_ledger.c.event_type.in_(("META_PLAN_EVALUATED", "META_OPERATOR_REVIEW_DECISION")))).all()
        outcome_rows = conn.execute(select(db.predictions.c.symbol, db.predictions.c.model_name,
            db.predictions.c.predicted_at, db.predictions.c.probability_up, db.predictions.c.signal,
            db.outcomes.c.was_correct, db.outcomes.c.price_change_pct, db.outcomes.c.verified_at)
            .select_from(db.predictions.join(db.outcomes,
                db.predictions.c.prediction_id == db.outcomes.c.prediction_id))
            .where(db.outcomes.c.was_correct.is_not(None))).all()
        vol_rows = conn.execute(select(db.vol_forecasts.c.symbol, db.vol_forecasts.c.model_name,
            db.vol_forecasts.c.forecast_at, db.vol_forecasts.c.pred_logvol_cal,
            db.vol_forecasts.c.realized_logvol).where(db.vol_forecasts.c.realized_logvol.is_not(None))).all()
        touch_stmt = select(db.meta_learning_samples.c.symbol,
            db.meta_learning_samples.c.sample_at, db.meta_learning_samples.c.signals_json,
            db.meta_signal_observations.c.stats_json).select_from(db.meta_signal_observations.join(
                db.meta_learning_samples, (db.meta_learning_samples.c.decision_id == db.meta_signal_observations.c.decision_id)
                & (db.meta_learning_samples.c.alternative_id == db.meta_signal_observations.c.alternative_id)))
        touch_sample_rows = conn.execute(touch_stmt).all()
        label_detail_rows = conn.execute(select(db.meta_learning_samples.c.id,
            db.meta_learning_samples.c.symbol, db.meta_learning_samples.c.profile,
            db.meta_learning_samples.c.sample_at, db.meta_learning_samples.c.alternative_id,
            db.meta_learning_labels.c.horizon, db.meta_learning_labels.c.label_json)
            .select_from(db.meta_learning_samples.join(db.meta_learning_labels,
                db.meta_learning_samples.c.id == db.meta_learning_labels.c.sample_id))).all()
        last_plan_row = conn.execute(select(db.meta_ledger.c.details).where(
            db.meta_ledger.c.event_type == "META_PLAN_EVALUATED").order_by(
                db.meta_ledger.c.id.desc()).limit(1)).first()
        now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
        unknown_review_rows = conn.execute(select(db.meta_ledger.c.ts, db.meta_ledger.c.meta_id,
            db.meta_ledger.c.details).where(db.meta_ledger.c.event_type == "META_OPERATOR_REVIEW_DECISION",
                db.meta_ledger.c.ts >= now_utc - timedelta(days=7)).order_by(
                    db.meta_ledger.c.ts.desc()).limit(5000)).all()

    unknown_reviews = []
    for stamp, unknown_meta_id, raw_details in unknown_review_rows:
        detail = _json(raw_details)
        valuation = detail.get("valuation") or {}
        diagnostic = detail.get("valuation_unknown") or {}
        if valuation.get("status") == "DESCONOCIDO" or diagnostic:
            unknown_reviews.append({"at": str(stamp), "meta_id": int(unknown_meta_id),
                "symbol": diagnostic.get("symbol") or valuation.get("symbol"),
                "reason": diagnostic.get("reason") or valuation.get("reason"),
                "pieces": diagnostic.get("pieces", [])})
    cutoff_24h = (now_utc - timedelta(hours=24)).isoformat(sep=" ")
    unknown_valuation_summary = {"last_24h": sum(row["at"] >= cutoff_24h for row in unknown_reviews),
        "last_7d": len(unknown_reviews), "reasons": unknown_reviews[:20]}
    bot_labels = {}
    bot_proposals = []
    for row in bot_shadow_rows:
        details = _json(row["details"])
        if row["event_type"] == "META_BOT_TOUCH_SHADOW_LABEL":
            bot_labels[(int(details.get("source_event_id", 0)), int(details.get("horizon_hours", 0)))] = details
        else:
            proposal = {**details, "event_id": int(row["id"]), "meta_id": int(row["meta_id"]),
                "at": str(row["ts"]), "labels": {}}
            for horizon in (24, 72):
                if (int(row["id"]), horizon) in bot_labels:
                    proposal["labels"][str(horizon)] = bot_labels[(int(row["id"]), horizon)]
            bot_proposals.append(proposal)
    for proposal in bot_proposals:
        proposal["labels"] = {str(h): bot_labels[(proposal["event_id"], h)]
            for h in (24, 72) if (proposal["event_id"], h) in bot_labels}
    bot_label_metrics = {}
    for horizon in (24, 72):
        labels = [value for (event_id, h), value in bot_labels.items() if h == horizon]
        bot_label_metrics[str(horizon)] = {"n": len(labels),
            "sell_after_buy_rate": (sum(bool(item.get("sell_after_buy")) for item in labels) / len(labels)
                if labels else None),
            "mean_expected_rate": (statistics.fmean(float(item["expected_sell_rate"]) for item in labels
                if item.get("expected_sell_rate") is not None) if any(item.get("expected_sell_rate") is not None
                    for item in labels) else None)}

    observation_counts = Counter()
    observation_counts_by_symbol = Counter()
    for symbol, _decision_id, _alternative_id, _raw, _created_at in observation_json:
        observation_counts["touch_buy_to_sell"] += 1
        observation_counts_by_symbol[("touch_buy_to_sell", symbol)] += 1
    label_summary = Counter()
    labels_by_sample = {}
    for sample_id, horizon, raw in label_rows:
        label = _json(raw)
        labels_by_sample[(int(sample_id), str(horizon))] = label
        label_summary[(horizon, label.get("status", "desconocida"),
            "cruzó" if label.get("reached") is True else "no cruzó" if label.get("reached") is False else "sin resultado")] += 1
    windows = Counter("<3" if int(row[4] or 0) < 3 else "3-10" if int(row[4] or 0) <= 10 else ">10"
        for row in sample_rows)
    replay_bins = {kind: dict(Counter("sin base" if row[index] is None else
        f"{min(9, int(float(row[index])*10)) * 10}–{min(9, int(float(row[index])*10)) * 10 + 10}%"
        for row in sample_rows)) for kind, index in (("raw", 5), ("suavizada", 6))}
    from grid.meta_learning import LEARNING_SIGNALS
    signal_stats = {name: {"observations": observation_counts.get(name, 0)} for name in LEARNING_SIGNALS}
    for name, count in observation_counts.items():
        signal_stats.setdefault(name, {"observations": count})
    all_signals = Counter()
    feature_values = {}
    direction_active_samples = 0
    try:
        from grid.meta_learning import extract_learning_features
    except ImportError:
        extract_learning_features = None
    for row in sample_rows:
        raw_signals = _json(row[7])
        for signal in raw_signals.get("signals_used", []) if isinstance(raw_signals, dict) else []:
            if signal.get("name") == "direction":
                try:
                    direction_active_samples += int(float(signal.get("p_down", signal.get("value"))) >= .60)
                except (TypeError, ValueError):
                    pass
        if extract_learning_features:
            try:
                features, present = extract_learning_features(raw_signals)
            except (TypeError, ValueError, KeyError):
                continue
            label = labels_by_sample.get((int(row[0]), "72h"), {})
            success = bool(label.get("reached")) if label.get("status") == "labeled" else None
            for name in present:
                all_signals[name] += 1
                feature_values.setdefault(name, []).append((float(features[name]), success))
    total_samples = len(sample_rows)
    for name in set(signal_stats) | set(all_signals):
        signal_stats.setdefault(name, {})["sample_coverage"] = all_signals[name]
        signal_stats[name]["coverage_pct"] = (100 * all_signals[name] / total_samples) if total_samples else 0
        values = feature_values.get(name, [])
        numeric_values = [value for value, _ in values]
        signal_stats[name]["mean"] = statistics.fmean(numeric_values) if numeric_values else None
        signal_stats[name]["stddev"] = statistics.pstdev(numeric_values) if len(numeric_values) > 1 else 0.0 if numeric_values else None
        labeled = [(value, success) for value, success in values if success is not None]
        threshold = statistics.median(value for value, _ in labeled) if labeled else None
        high = [success for value, success in labeled if value >= threshold] if threshold is not None else []
        low = [success for value, success in labeled if value < threshold] if threshold is not None else []
        signal_stats[name]["high_success_rate"] = statistics.fmean(high) if high else None
        signal_stats[name]["low_success_rate"] = statistics.fmean(low) if low else None
    signal_stats.setdefault("direction", {})["direction_active_coverage"] = {
        "count": direction_active_samples,
        "total": total_samples,
        "coverage_pct": 100 * direction_active_samples / total_samples if total_samples else 0,
        "threshold_p_down": .60}

    # Resumen verbal y B3: intervalos de diez puntos, medias cruda/suavizada y ventanas.
    sample_n = len(sample_rows)
    distinct_symbols = len({str(row[3]) for row in sample_rows})
    plan_alternatives_today = sum(int(_json(row[0]).get("alternatives_total",
        len(_json(row[0]).get("alternatives") or [])) or 0) for row in plans_today)
    alternatives_today = int(plan_alternatives_today or alternatives_today or 0)
    models_by_symbol = {}
    for row in forecasts:
        models_by_symbol.setdefault(str(row["symbol"]), set()).add(str(row["model_name"]))
    strongest_forecast_symbol = max(models_by_symbol, key=lambda symbol: len(models_by_symbol[symbol]), default=None)
    latest_run_at = str(latest_run["finished_at"] or latest_run["started_at"]) if latest_run else None
    replay_chart = []
    for low in range(0, 100, 10):
        group = [row for row in sample_rows if row[5] is not None and
                 low <= min(99, int(float(row[5]) * 100)) < low + 10]
        smooth = [float(row[6]) for row in group if row[6] is not None]
        replay_chart.append({"bin": f"{low}–{low+10}%", "count": len(group),
            "raw_mean": statistics.fmean(float(row[5]) for row in group) if group else None,
            "smoothed_mean": statistics.fmean(smooth) if smooth else None,
            "windows_mean": statistics.fmean(int(row[4] or 0) for row in group) if group else 0})

    def wilson(successes, n, z=1.959963984540054):
        if not n: return None
        p = successes / n; denom = 1 + z*z/n
        center = (p + z*z/(2*n))/denom
        margin = z*math.sqrt((p*(1-p)+z*z/(4*n))/n)/denom
        return [max(0.0, center-margin), min(1.0, center+margin)]

    now_utc = datetime.now(timezone.utc)
    def in_days(value, days):
        if value is None: return False
        if getattr(value, "tzinfo", None) is None: value = value.replace(tzinfo=timezone.utc)
        return value >= now_utc - timedelta(days=days)
    direction_accuracy = []
    direction_groups = {}
    for row in outcome_rows:
        for days in (7, 30):
            if in_days(row[2], days):
                key = (str(row[1]), str(row[0]), days)
                bucket = direction_groups.setdefault(key, [0, 0])
                bucket[0] += int(bool(row[5])); bucket[1] += 1
    for (model, symbol, days), (hits, n) in sorted(direction_groups.items()):
        direction_accuracy.append({"model": model, "symbol": symbol, "days": days,
            "n": n, "accuracy": hits/n, "wilson95": wilson(hits, n), "few_samples": n < 30})
    volatility_accuracy = []
    vol_groups = {}
    for row in vol_rows:
        for days in (7, 30):
            if in_days(row[2], days):
                key = (str(row[1]), str(row[0]), days)
                bucket = vol_groups.setdefault(key, [[], 0])
                err = abs(float(row[3]) - float(row[4])); bucket[0].append(err)
                bucket[1] += int(abs(math.exp(float(row[4])-float(row[3])) - 1) <= .20)
    for (model, symbol, days), (errors, within) in sorted(vol_groups.items()):
        volatility_accuracy.append({"model": model, "symbol": symbol, "days": days,
            "n": len(errors), "mae_log": statistics.fmean(errors), "within_20_pct": within/len(errors),
            "few_samples": len(errors) < 30})
    touch_bins = {}
    for symbol, sample_at, signal_raw, stats_raw in touch_sample_rows:
        stats = _json(stats_raw); signals = _json(signal_raw).get("signals_used", [])
        pred = next((float(item.get("normalized")) for item in signals if item.get("name") == "touch_buy_to_sell"
                     and item.get("normalized") is not None), None)
        if pred is None or stats.get("sell_rate") is None: continue
        predicted_rate = max(0.0, min(1.0, (pred + 1) / 2))
        bin_start = min(80, int(predicted_rate * 5) * 20)
        bucket_name = f"{bin_start}–{bin_start+20}%"
        bucket = touch_bins.setdefault(bucket_name, [[], []])
        bucket[0].append((pred+1)/2); bucket[1].append(float(stats["sell_rate"]))
    touch_calibration = [{"bin": key, "n": len(pair[0]), "predicted": statistics.fmean(pair[0]),
        "observed": statistics.fmean(pair[1]), "few_samples": len(pair[0]) < 30}
        for key, pair in sorted(touch_bins.items())]
    replay_groups = {}
    for sample in sample_rows:
        probability = sample[6] if sample[6] is not None else sample[5]
        if probability is None: continue
        for horizon in ("72h", "20d", "45d"):
            label = labels_by_sample.get((int(sample[0]), horizon), {})
            if label.get("status") != "labeled" or label.get("reached") is None: continue
            value = max(0.0, min(.999999, float(probability)))
            low = int(value * 10) * 10
            bucket = replay_groups.setdefault((horizon, low), [[], []])
            bucket[0].append(value); bucket[1].append(int(bool(label["reached"])))
    replay_calibration = [{"horizon": horizon, "bin": f"{low}–{low+10}%", "n": len(values[0]),
        "predicted": statistics.fmean(values[0]), "observed": statistics.fmean(values[1]),
        "few_samples": len(values[0]) < 30}
        for (horizon, low), values in sorted(replay_groups.items())]
    centered_rows = {horizon: {kind: [] for kind in ("actual", "centrada")}
        for horizon in ("72h", "20d", "45d")}
    for sample in sample_rows:
        raw = _json(sample[7])
        if not isinstance(raw, dict):
            continue
        for horizon in centered_rows:
            label = labels_by_sample.get((int(sample[0]), horizon), {})
            if label.get("status") != "labeled" or label.get("reached") is None:
                continue
            truth = int(bool(label["reached"]))
            for kind, key in (("actual", "probability_adjusted_actual"),
                              ("centrada", "probability_adjusted_centered")):
                probability = raw.get(key)
                if probability is not None:
                    centered_rows[horizon][kind].append((
                        max(1e-12, min(1 - 1e-12, float(probability))), truth))
    centered_comparison = {}
    for horizon, kinds in centered_rows.items():
        centered_comparison[horizon] = {}
        for kind, values in kinds.items():
            if not values:
                centered_comparison[horizon][kind] = {"n": 0, "brier": None, "log_loss": None}
                continue
            ps = [p for p, _ in values]; ys = [y for _, y in values]
            centered_comparison[horizon][kind] = {"n": len(values),
                "brier": statistics.fmean((p-y)**2 for p, y in values),
                "log_loss": statistics.fmean(-y*math.log(p)-(1-y)*math.log(1-p)
                    for p, y in values)}
    break_v2_samples = []
    for sample in sample_rows:
        raw = _json(sample[7])
        label = labels_by_sample.get((int(sample[0]), "72h"), {})
        v2 = raw.get("break_prob_v2") or {}
        actual_p = raw.get("probability_adjusted_break_current")
        v2_p = raw.get("probability_adjusted_break_v2")
        if (label.get("status") != "labeled" or label.get("reached") is None or
                v2.get("value") is None or actual_p is None or v2_p is None):
            continue
        exits = [piece.get("exited") for piece in label.get("piece_range_exits", [])
                 if piece.get("exited") is not None]
        break_v2_samples.append({"actual": float(actual_p), "v2": float(v2_p),
            "truth": int(bool(label["reached"])), "break": float(v2["value"]),
            "piece_exits": exits,
            "sample_start": label.get("sample_start"), "sample_end": label.get("sample_end")})

    def probability_metrics(rows, key):
        values = [(max(1e-12, min(1-1e-12, row[key])), row["truth"]) for row in rows]
        if not values:
            return {"n": 0, "brier": None, "log_loss": None}
        return {"n": len(values), "brier": statistics.fmean((p-y)**2 for p, y in values),
            "log_loss": statistics.fmean(-y*math.log(p)-(1-y)*math.log(1-p) for p, y in values)}

    break_bins = []
    ordered_breaks = sorted(row["break"] for row in break_v2_samples)
    if ordered_breaks:
        q33 = ordered_breaks[min(len(ordered_breaks)-1, len(ordered_breaks)//3)]
        q67 = ordered_breaks[min(len(ordered_breaks)-1, 2*len(ordered_breaks)//3)]
        grouped = {"bajo": [], "medio": [], "alto": []}
        for row in break_v2_samples:
            group = "bajo" if row["break"] <= q33 else "medio" if row["break"] <= q67 else "alto"
            grouped[group].extend(row["piece_exits"])
        for name, values in grouped.items():
            observed = [bool(value) for value in values if value is not None]
            break_bins.append({"tercile": name, "n": len(observed),
                "break_prob_min": min((row["break"] for row in break_v2_samples
                    if (row["break"] <= q33 if name == "bajo" else q33 < row["break"] <= q67
                        if name == "medio" else row["break"] > q67)), default=None),
                "break_prob_max": max((row["break"] for row in break_v2_samples
                    if (row["break"] <= q33 if name == "bajo" else q33 < row["break"] <= q67
                        if name == "medio" else row["break"] > q67)), default=None),
                "range_exit_rate": statistics.fmean(int(value) for value in observed) if observed else None,
                "few_samples": len(observed) < 30})
    break_v2_comparison = {"status": "ready" if len(break_v2_samples) >= 30 else "insufficient_data",
        "message": None if len(break_v2_samples) >= 30 else "sin datos suficientes",
        "n": len(break_v2_samples),
        "interval": {"first": min((row["sample_start"] for row in break_v2_samples
            if row.get("sample_start") is not None), default=None),
            "last": max((row["sample_end"] for row in break_v2_samples
            if row.get("sample_end") is not None), default=None)},
        "actual": probability_metrics(break_v2_samples, "actual"),
        "break_v2": probability_metrics(break_v2_samples, "v2"),
        "terciles": break_bins}
    label_lookup = {}
    for sid, symbol, profile, sample_at, alt_id, horizon, raw in label_detail_rows:
        label = _json(raw); label_lookup[(str(alt_id), str(horizon))] = (label, profile)
    decision_success = {name: {h: [0, 0] for h in ("72h", "20d", "45d")}
        for name in ("real", "scanner_advisor", "learned")}
    for event in all_decision_events:
        choices = _json(event[0]).get("decision_choices", {})
        for name in decision_success:
            choice = choices.get(name) or {}
            alt_id = choice.get("alternative_id")
            if not alt_id: continue
            for horizon in decision_success[name]:
                found = label_lookup.get((str(alt_id), horizon))
                if found and found[0].get("status") == "labeled" and found[0].get("reached") is not None:
                    decision_success[name][horizon][0] += int(bool(found[0]["reached"]))
                    decision_success[name][horizon][1] += 1
    decision_rates = {name: {h: {"successes": pair[0], "n": pair[1],
        "rate": pair[0]/pair[1] if pair[1] else None, "few_samples": pair[1] < 30}
        for h, pair in horizons.items()} for name, horizons in decision_success.items()}
    eval_msg = f"Hoy se evaluaron {alternatives_today} alternativas; hay {sample_n} muestras de aprendizaje"
    coverage_msg = (f" en {distinct_symbols} monedas" if distinct_symbols else "")
    forecast_msg = (f"; hay pronósticos de {len(models_by_symbol[strongest_forecast_symbol])} modelos para {strongest_forecast_symbol}"
        if strongest_forecast_symbol else "; aún no hay pronósticos de volatilidad")
    learning_msg = f"; última corrida del aprendizaje: {latest_run_at}" if latest_run_at else "; el aprendizaje todavía no tiene corridas"

    latest_versions = {}
    for row in version_rows:
        latest_versions.setdefault(row["profile"], row)
    readiness = _ready(list(latest_versions.values()))
    profile_learning = {}
    operator = getattr(request.app.state, "meta_operator", None)
    if operator is not None:
        for profile in ("RAPIDO", "PRUDENTE"):
            profile_learning[profile] = operator.learning_status(profile)
    settings = getattr(request.app.state, "settings", None)
    try:
        meta_config = operator.core.config()
        profile_configs = meta_config.get("profiles", {})
    except Exception:
        profile_configs = {}
    shadow_switches = {profile: {"use_centered_signals": bool((profile_configs.get(profile) or {}).get(
            "use_centered_signals", False)), "use_touch_bots": bool((profile_configs.get(profile) or {}).get(
            "use_touch_bots", False)), "use_break_prob_v2": bool((profile_configs.get(profile) or {}).get(
            "use_break_prob_v2", False))} for profile in ("RAPIDO", "PRUDENTE")}
    switches = {profile: {"enabled": bool(profile_learning.get(profile, {}).get("enabled", False)),
        "readiness": readiness[profile]} for profile in ("RAPIDO", "PRUDENTE")}
    global_flags = {name: getattr(settings, name, None) for name in (
        "meta_mode_enabled", "advisor_vol_spacing_enabled", "smart_adjust_enabled",
        "stoploss_enabled", "smart_close_wait_enabled", "smart_respacing_enabled",
        "smart_direction_filter_enabled") if settings is not None and hasattr(settings, name)}

    return {"updated_at": datetime.now(timezone.utc).isoformat(),
        "data": {"summary_text": eval_msg + coverage_msg + forecast_msg + learning_msg,
            "candles": {"available": False,
            "reason": "La base de datos de la aplicación no conserva velas; el análisis usa el proveedor histórico existente."},
            "volatility_forecasts": [dict(row) for row in forecasts],
            "direction_predictions": [dict(row) for row in predictions],
            "meta_signal_observations_by_symbol": [dict(row) for row in observations],
            "meta_signal_counts": dict(observation_counts),
            "meta_signal_counts_by_symbol": [{"signal": signal, "symbol": symbol, "count": count}
                for (signal, symbol), count in observation_counts_by_symbol.items()],
            "unknown_valuation_reviews": unknown_valuation_summary,
            "touch_bot_shadow": {"proposals": list(reversed(bot_proposals)), "labels": bot_label_metrics},
            "meta_ledger": [dict(row) for row in ledger]},
        "samples": {"by_profile_source_symbol": [dict(row) for row in sample_groups],
            "exclusions": [dict(row) for row in exclusions], "recalculate_pending": int(recalc),
            "labels": [{"horizon": h, "status": s, "outcome": o, "count": n}
                for (h, s, o), n in sorted(label_summary.items())],
            "effective_by_signal": {profile: {name: detail.get("effective_samples", 0)
                for name, detail in (profile_learning.get(profile, {}).get("signals") or {}).items()}
                for profile in ("RAPIDO", "PRUDENTE")}},
        "replay": {"window_histogram": dict(windows), "raw_probability_bins": dict(replay_bins),
            "chart": replay_chart,
            "interpretation": (f"La mayoría de las {sample_n} muestras queda por debajo de 20 % de probabilidad histórica."
                if sample_n and sum(1 for row in sample_rows if row[5] is not None and float(row[5]) < .2) > sample_n/2
                else f"El gráfico compara {sample_n} muestras por probabilidad histórica; las ventanas indican el soporte de cada muestra."),
            "cache_entries": _cache_count(request.app.state.db),
            "last_analysis": _json(last_plan_row[0]) if last_plan_row else None,
            "last_run": _json(latest_run["summary_json"]) if latest_run else None},
        "signals": signal_stats,
        "hits": {"direction": direction_accuracy, "volatility": volatility_accuracy,
            "touch_calibration": touch_calibration, "replay_calibration": replay_calibration,
            "centered_signal_comparison": centered_comparison,
            "break_v2_comparison": break_v2_comparison,
            "decision_rates": decision_rates},
        "regressions": {"versions": [{"id": int(row["id"]), "profile": row["profile"],
            "source": row["source_kind"], "status": row["status"], "created_at": str(row["created_at"]),
            "summary": _json(row["summary_json"])} for row in version_rows],
            "profiles": profile_learning},
        "decisions": {"profiles": profile_learning, "total": int(ledger_total),
            "page": page, "page_size": page_size, "meta_filter": meta_id,
            "recent": [{"at": str(row["ts"]), "meta_id": int(row["meta_id"]),
            "event_type": row["event_type"], "choices": _json(row["details"]).get("decision_choices", {}),
            "details": _json(row["details"])} for row in ledger_events]},
        "allocation_shadow": [{"meta_id": int(row["meta_id"]), "at": str(row["ts"]),
            "details": _json(row["details"])} for row in allocation_shadow_rows],
        "runs": [{"id": int(row["id"]), "started_at": str(row["started_at"]),
            "finished_at": str(row["finished_at"]), "status": row["status"],
            "summary": _json(row["summary_json"])} for row in runs],
        "switches": switches, "shadow_switches": shadow_switches, "global_flags": global_flags,
        "models_link": "#models", "battle_link": "#battle"}


def _cache_count(db):
    with db.engine.connect() as conn:
        return int(conn.execute(select(func.count()).select_from(db.meta_learning_replay_cache)).scalar_one())
