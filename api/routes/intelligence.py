"""Consultas agregadas y de solo lectura para la vista Inteligencia."""
from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
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
def intelligence_summary(request: Request, page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200)):
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
            db.meta_signal_observations.c.stats_json)).all()
        ledger = conn.execute(select(db.meta_ledger.c.meta_id, db.meta_ledger.c.event_type,
            func.count().label("count")).group_by(db.meta_ledger.c.meta_id,
                db.meta_ledger.c.event_type).order_by(db.meta_ledger.c.meta_id,
                db.meta_ledger.c.event_type)).mappings().all()
        sample_groups = conn.execute(select(db.meta_learning_samples.c.profile,
            db.meta_learning_samples.c.source_kind, db.meta_learning_samples.c.symbol,
            func.count().label("count"), func.min(db.meta_learning_samples.c.sample_at).label("first_at"),
            func.max(db.meta_learning_samples.c.sample_at).label("last_at")).group_by(
                db.meta_learning_samples.c.profile, db.meta_learning_samples.c.source_kind,
                db.meta_learning_samples.c.symbol)).mappings().all()
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
        ledger_events = conn.execute(select(db.meta_ledger.c.ts, db.meta_ledger.c.meta_id,
            db.meta_ledger.c.event_type, db.meta_ledger.c.details).where(
                db.meta_ledger.c.event_type.in_(("META_PLAN_EVALUATED", "META_OPERATOR_REVIEW_DECISION"))
            ).order_by(db.meta_ledger.c.id.desc()).offset((page - 1) * page_size).limit(page_size)).mappings().all()
        last_plan_row = conn.execute(select(db.meta_ledger.c.details).where(
            db.meta_ledger.c.event_type == "META_PLAN_EVALUATED").order_by(
                db.meta_ledger.c.id.desc()).limit(1)).first()

    observation_counts = Counter()
    observation_counts_by_symbol = Counter()
    for symbol, raw in observation_json:
        stats = _json(raw)
        for name in stats:
            observation_counts[name] += 1
            observation_counts_by_symbol[(name, symbol)] += 1
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
    try:
        from grid.meta_learning import extract_learning_features
    except ImportError:
        extract_learning_features = None
    for row in sample_rows:
        raw_signals = _json(row[7])
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
    switches = {profile: {"enabled": bool(profile_learning.get(profile, {}).get("enabled", False)),
        "readiness": readiness[profile]} for profile in ("RAPIDO", "PRUDENTE")}
    global_flags = {name: getattr(settings, name, None) for name in (
        "meta_mode_enabled", "advisor_vol_spacing_enabled", "smart_adjust_enabled",
        "stoploss_enabled", "smart_close_wait_enabled", "smart_respacing_enabled",
        "smart_direction_filter_enabled") if settings is not None and hasattr(settings, name)}

    return {"updated_at": datetime.now(timezone.utc).isoformat(),
        "data": {"candles": {"available": False,
            "reason": "La base de datos de la aplicación no conserva velas; el análisis usa el proveedor histórico existente."},
            "volatility_forecasts": [dict(row) for row in forecasts],
            "direction_predictions": [dict(row) for row in predictions],
            "meta_signal_observations_by_symbol": [dict(row) for row in observations],
            "meta_signal_counts": dict(observation_counts),
            "meta_signal_counts_by_symbol": [{"signal": signal, "symbol": symbol, "count": count}
                for (signal, symbol), count in observation_counts_by_symbol.items()],
            "meta_ledger": [dict(row) for row in ledger]},
        "samples": {"by_profile_source_symbol": [dict(row) for row in sample_groups],
            "exclusions": [dict(row) for row in exclusions], "recalculate_pending": int(recalc),
            "labels": [{"horizon": h, "status": s, "outcome": o, "count": n}
                for (h, s, o), n in sorted(label_summary.items())],
            "effective_by_signal": {profile: {name: detail.get("effective_samples", 0)
                for name, detail in (profile_learning.get(profile, {}).get("signals") or {}).items()}
                for profile in ("RAPIDO", "PRUDENTE")}},
        "replay": {"window_histogram": dict(windows), "raw_probability_bins": dict(replay_bins),
            "cache_entries": _cache_count(request.app.state.db),
            "last_analysis": _json(last_plan_row[0]) if last_plan_row else None,
            "last_run": _json(latest_run["summary_json"]) if latest_run else None},
        "signals": signal_stats,
        "regressions": {"versions": [{"id": int(row["id"]), "profile": row["profile"],
            "source": row["source_kind"], "status": row["status"], "created_at": str(row["created_at"]),
            "summary": _json(row["summary_json"])} for row in version_rows],
            "profiles": profile_learning},
        "decisions": {"profiles": profile_learning, "recent": [{"at": str(row["ts"]),
            "meta_id": int(row["meta_id"]), "event_type": row["event_type"],
            "choices": _json(row["details"]).get("decision_choices", {})} for row in ledger_events]},
        "runs": [{"id": int(row["id"]), "started_at": str(row["started_at"]),
            "finished_at": str(row["finished_at"]), "status": row["status"],
            "summary": _json(row["summary_json"])} for row in runs],
        "switches": switches, "global_flags": global_flags,
        "models_link": "#models", "battle_link": "#battle"}


def _cache_count(db):
    with db.engine.connect() as conn:
        return int(conn.execute(select(func.count()).select_from(db.meta_learning_replay_cache)).scalar_one())
