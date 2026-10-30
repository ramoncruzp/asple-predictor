"""Consultas agregadas y de solo lectura para la vista Inteligencia."""
from __future__ import annotations

import json
import logging
from collections import Counter
from datetime import datetime, timedelta, timezone
import math
import statistics
import time

from fastapi import APIRouter, HTTPException, Query, Request
from sqlalchemy import func, select

from api.routes.grids import _authorize
from grid.readiness import evaluate_readiness, paired_bootstrap_mean_ci

router = APIRouter()
_logger = logging.getLogger("asple.slow")


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


def _grid_intelligence_summary(db, target_grid_id=None):
    from grid.grid_intelligence import GRID_INTEL_METHOD_VERSION, _json as grid_json

    now = datetime.now(timezone.utc)
    recommendation_query = select(db.grid_intel_recommendations).where(
        db.grid_intel_recommendations.c.created_at >= now - timedelta(hours=24),
        db.grid_intel_recommendations.c.method_version == GRID_INTEL_METHOD_VERSION)
    if target_grid_id is not None:
        recommendation_query = recommendation_query.where(
            db.grid_intel_recommendations.c.grid_id == int(target_grid_id))
    with db.engine.connect() as conn:
        recommendations = conn.execute(recommendation_query.order_by(
            db.grid_intel_recommendations.c.created_at.desc())).mappings().all()
        label_query = (select(db.grid_intel_labels.c.recommendation_id, db.grid_intel_labels.c.label_json)
            .join(db.grid_intel_recommendations,
                db.grid_intel_recommendations.c.id == db.grid_intel_labels.c.recommendation_id)
            .where(db.grid_intel_labels.c.status == "mature",
                db.grid_intel_recommendations.c.method_version == GRID_INTEL_METHOD_VERSION))
        grid_query = select(db.grids)
        if target_grid_id is not None:
            label_query = label_query.where(
                db.grid_intel_recommendations.c.grid_id == int(target_grid_id))
            grid_query = grid_query.where(db.grids.c.id == int(target_grid_id))
        labels = conn.execute(label_query).all()
        grids = {int(row["id"]): dict(row) for row in conn.execute(grid_query).mappings().all()}
    label_by_rec = {int(row[0]): _json(row[1]) for row in labels}
    grouped = {}
    for recommendation in recommendations:
        grid_id = int(recommendation["grid_id"])
        grid = grids.get(grid_id) or {}
        params = _json(grid.get("params"))
        item = grouped.setdefault(grid_id, {"grid_id": grid_id,
            "symbol": recommendation["symbol"], "strategy": recommendation["strategy"],
            "intel_apply_enabled": bool(params.get("intel_apply_enabled", False)),
            "recommendations_24h": [], "mature_labels": []})
        item["recommendations_24h"].append({"at": str(recommendation["created_at"]),
            "action": recommendation["action"], "expected_gain_7d": recommendation["expected_gain_7d"],
            "baseline_gain_7d": recommendation["expected_gain_7d_baseline"],
            "drawdown": recommendation["drawdown"],
            "paired_diff_median": (grid_json(recommendation["candidate_json"]).get("paired_diff_median")),
            "paired_diff_p10": (grid_json(recommendation["candidate_json"]).get("paired_diff_p10")),
            "would_apply": bool(recommendation["would_apply"])})
    # Las etiquetas se agrupan por grid y acción usando la recomendación de origen.
    with db.engine.connect() as conn:
        rec_query = (select(db.grid_intel_recommendations.c.id,
            db.grid_intel_recommendations.c.grid_id, db.grid_intel_recommendations.c.action,
            db.grid_intel_recommendations.c.candidate_json,
            db.grid_intel_recommendations.c.baseline_json).join(db.grid_intel_labels,
                db.grid_intel_labels.c.recommendation_id == db.grid_intel_recommendations.c.id)
            .where(db.grid_intel_labels.c.status == "mature",
                db.grid_intel_recommendations.c.method_version == GRID_INTEL_METHOD_VERSION))
        if target_grid_id is not None:
            rec_query = rec_query.where(db.grid_intel_recommendations.c.grid_id == int(target_grid_id))
        rec_rows = conn.execute(rec_query).all()
    stats_by_grid = {}
    for rec_id, grid_id, action, candidate_json, baseline_json in rec_rows:
        label = label_by_rec.get(int(rec_id)) or {}
        diff = label.get("gain_difference_usdt")
        if diff is None:
            continue
        current = stats_by_grid.setdefault(int(grid_id), {"values": [], "actions": {}})
        current["values"].append(float(diff))
        evaluated_action = label.get("evaluated_action") or action
        current["actions"].setdefault(str(evaluated_action), []).append(float(diff))
    for grid_id, metrics in stats_by_grid.items():
        item = grouped.setdefault(grid_id, {"grid_id": grid_id, "recommendations_24h": [],
            "mature_labels": [], "intel_apply_enabled": bool(_json(grids.get(grid_id, {}).get("params")).get("intel_apply_enabled", False))})
        values = metrics["values"]
        ci = paired_bootstrap_mean_ci(values) if values else None
        n = len(values)
        readiness_by_action = {}
        for action, action_values in metrics["actions"].items():
            action_ci = paired_bootstrap_mean_ci(action_values) if len(action_values) >= 2 else None
            action_mean = statistics.fmean(action_values) if action_values else None
            criteria = [{"text": "Al menos 30 etiquetas maduras", "ok": len(action_values) >= 30, "value": len(action_values)},
                {"text": "Ganancia media de aplicar mayor que mantener", "ok": action_mean is not None and action_mean > 0, "value": action_mean},
                {"text": "El intervalo de confianza no cruza cero", "ok": bool(action_ci and action_ci[0] > 0), "value": action_ci}]
            readiness_by_action[action] = evaluate_readiness(n=len(action_values), required=30,
                criteria=criteria, sample_times=(), summary="evidencia suficiente" if len(action_values) >= 30 else "sin datos suficientes")
            if action_ci and action_ci[1] < 0:
                readiness_by_action[action] = evaluate_readiness(n=len(action_values), required=30,
                    criteria=criteria, adverse_significant=True, sample_times=(),
                    summary="la evidencia indica un resultado adverso")
        item["mature_labels"] = {"n": n, "mean_gain_vs_maintain_usdt": statistics.fmean(values) if values else None,
            "ci95": ci, "win_rate": statistics.fmean(1.0 if value > 0 else 0.0 for value in values) if values else None,
            "by_action": {key: {"n": len(rows), "mean_gain_vs_maintain_usdt": statistics.fmean(rows)}
                for key, rows in metrics["actions"].items()}, "readiness_by_action": readiness_by_action,
            "status": "evidencia disponible" if n >= 30 else "sin datos suficientes"}
    return {"updated_at": now.isoformat(), "method": "replay de 7 días sobre la parte afectada",
        "grids": list(grouped.values())}


def _grid_intelligence_readiness_alerts(grid_summary):
    """Devuelve medidores terminales con claves estables por grid y acción."""
    return {
        f"grid_{int(grid['grid_id'])}_{action}": readiness
        for grid in (grid_summary or {}).get("grids") or []
        for action, readiness in ((grid.get("mature_labels") or {}).get("readiness_by_action") or {}).items()
    }


@router.get("")
def intelligence_summary(request: Request, page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200),
                         meta_id: int | None = Query(None, ge=1)):
    _authorize(request)
    service = getattr(request.app.state, "intelligence_snapshot", None)
    if service is not None and page == 1 and page_size == 50 and meta_id is None:
        snapshot = service.snapshot
        if snapshot is None:
            if not service.calculating:
                service.request_refresh()
            return _calculating_summary()
        age_s = (datetime.now(timezone.utc) - datetime.fromisoformat(
            snapshot["computed_at"].replace("Z", "+00:00"))).total_seconds()
        if age_s >= 3600 and not service.calculating:
            service.request_refresh()
        result = dict(snapshot)
        result["refresh_status"] = "calculando" if service.calculating else "listo"
        return result
    return _intelligence_summary(request, page=page, page_size=page_size, meta_id=meta_id)


def _calculating_summary():
    """Contrato vacío rápido para el primer arranque, mientras se genera la instantánea."""
    return {"status": "calculando", "refresh_status": "calculando", "computed_at": None,
        "updated_at": None, "timings_ms": {},
        "data": {"summary_text": "Calculando el primer resumen de Inteligencia en segundo plano.",
            "candles": {"available": False, "reason": "El resumen aparecerá al terminar el precálculo."},
            "volatility_forecasts": [], "direction_predictions": [],
            "meta_signal_observations_by_symbol": [], "meta_signal_counts": {},
            "meta_signal_counts_by_symbol": [], "meta_ledger": [],
            "unknown_valuation_reviews": {"last_24h": 0, "last_7d": 0, "reasons": []}},
        "samples": {"by_profile_source_symbol": [], "exclusions": [], "recalculate_pending": 0,
            "labels": [], "effective_by_signal": {}},
        "replay": {"chart": [], "window_histogram": {}, "cache_entries": 0, "last_run": None},
        "signals": {}, "hits": {}, "regressions": {"versions": [], "profiles": {}},
        "decisions": {"recent": [], "total": 0, "page": 1, "page_size": 50},
        "allocation_shadow": [], "runs": [], "grid_intelligence": {"grids": []},
        "grid_intelligence_readiness": {}, "readiness_switches": {}, "switches": {},
        "shadow_switches": {}, "global_flags": {}}


@router.post("/recalculate", status_code=202)
def recalculate_intelligence(request: Request):
    _authorize(request)
    service = getattr(request.app.state, "intelligence_snapshot", None)
    if service is None:
        raise HTTPException(503, "El precálculo de Inteligencia no está disponible")
    result = service.request_refresh()
    return {**result, "computed_at": (service.snapshot or {}).get("computed_at")}


def _intelligence_summary(request: Request, *, page=1, page_size=50, meta_id=None):
    request_started = time.perf_counter()
    section_started = request_started
    timings_ms = {}

    def mark_timing(name):
        nonlocal section_started
        now = time.perf_counter()
        timings_ms[name] = round((now - section_started) * 1000, 2)
        section_started = now

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
        allocation_readiness_rows = conn.execute(select(db.meta_goals.c.profile, db.meta_ledger.c.details)
            .select_from(db.meta_ledger.join(db.meta_goals, db.meta_goals.c.id == db.meta_ledger.c.meta_id))
            .where(db.meta_ledger.c.event_type == "META_OPERATOR_ALLOCATION_SHADOW")).all()
        allocation_label_rows = conn.execute(select(db.meta_goals.c.profile, db.meta_ledger.c.details)
            .select_from(db.meta_ledger.join(db.meta_goals, db.meta_goals.c.id == db.meta_ledger.c.meta_id))
            .where(db.meta_ledger.c.event_type == "META_OPERATOR_ALLOCATION_LABEL")).all()
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
            db.meta_learning_samples.c.training_exclusion_reason,
            db.meta_learning_samples.c.sample_at,
            db.meta_learning_samples.c.base_method_version).order_by(
                db.meta_learning_samples.c.id.desc()).limit(10000)).all()
        latest_run = conn.execute(select(db.meta_learning_runs).order_by(
            db.meta_learning_runs.c.id.desc()).limit(1)).mappings().first()
        runs = conn.execute(select(db.meta_learning_runs.c.id, db.meta_learning_runs.c.started_at,
            db.meta_learning_runs.c.finished_at, db.meta_learning_runs.c.status,
            db.meta_learning_runs.c.summary_json).order_by(db.meta_learning_runs.c.id.desc()).offset(
                (page - 1) * page_size).limit(page_size)).mappings().all()
        version_rows = conn.execute(select(db.meta_learning_weight_versions).order_by(
            db.meta_learning_weight_versions.c.id.desc()).offset((page - 1) * page_size).limit(page_size)).mappings().all()
        calibration_version_rows = conn.execute(select(db.meta_learning_weight_versions.c.id,
            db.meta_learning_weight_versions.c.profile, db.meta_learning_weight_versions.c.source_kind,
            db.meta_learning_weight_versions.c.created_at, db.meta_learning_weight_versions.c.summary_json)
            .order_by(db.meta_learning_weight_versions.c.id.desc())).mappings().all()
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

    mark_timing("lecturas_sql")
    unknown_reviews = []
    for stamp, unknown_meta_id, raw_details in unknown_review_rows:
        detail = _json(raw_details)
        valuation = detail.get("valuation") or {}
        diagnostic = detail.get("valuation_unknown") or {}
        if valuation.get("status") == "DESCONOCIDO" or diagnostic:
            unknown_reviews.append({"at": str(stamp), "meta_id": int(unknown_meta_id),
                "symbol": diagnostic.get("symbol") or valuation.get("symbol"),
                "reason": diagnostic.get("reason") or valuation.get("reason"),
                "pieces": diagnostic.get("pieces") or []})
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
        if label.get("status") == "superseded":
            continue
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
        for signal in (raw_signals.get("signals_used") or []) if isinstance(raw_signals, dict) else []:
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
    mark_timing("muestras_y_senales")

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
    mark_timing("resultados_modelos")
    touch_bins = {}
    for symbol, sample_at, signal_raw, stats_raw in touch_sample_rows:
        stats = _json(stats_raw); signals = _json(signal_raw).get("signals_used") or []
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
    replay_calibration, replay_calibration_source = [], {
        "version_id": None, "profile": None, "source_kind": None, "created_at": None,
        "message": "sin deciles guardados"}
    for version in calibration_version_rows:
        summary = _json(version["summary_json"])
        deciles = summary.get("calibration_deciles") or {}
        baseline_deciles = deciles.get("replay_only") or []
        if not baseline_deciles:
            continue
        replay_calibration = [{"horizon": "validación temporal",
            "bin": f"Decil {int(item.get('decile') or index + 1)}",
            "n": int(item.get("samples") or 0),
            "predicted": item.get("predicted_mean"),
            "observed": item.get("success_rate"),
            "few_samples": int(item.get("samples") or 0) < 30}
            for index, item in enumerate(baseline_deciles)]
        replay_calibration_source = {"version_id": int(version["id"]),
            "profile": str(version["profile"]), "source_kind": str(version["source_kind"]),
            "created_at": str(version["created_at"]), "message": None}
        break
    mark_timing("comparaciones_y_calibracion")
    centered_rows = {horizon: {kind: [] for kind in ("actual", "centrada")}
        for horizon in ("72h", "20d", "45d")}
    centered_by_profile = {profile: [] for profile in ("RAPIDO", "PRUDENTE")}
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
            if horizon == "72h":
                actual = raw.get("probability_adjusted_actual")
                centered = raw.get("probability_adjusted_centered")
                if (actual is not None and centered is not None
                        and sample[10] == "rolling_7d_v3_equity"
                        and not sample[8]):
                    profile = str(sample[1]).upper()
                    if profile in centered_by_profile:
                        centered_by_profile[profile].append((float(actual), float(centered), truth,
                            sample[9]))
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
        exits = [piece.get("exited") for piece in (label.get("piece_range_exits") or [])
                 if piece.get("exited") is not None]
        break_v2_samples.append({"profile": str(sample[1]).upper(),
            "sample_at": sample[9], "method_version": sample[10],
            "excluded": bool(sample[8]), "actual": float(actual_p), "v2": float(v2_p),
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
        label = _json(raw)
        if label.get("status") == "superseded":
            continue
        label_lookup[(str(alt_id), str(horizon))] = (label, profile)
    decision_success = {name: {h: [0, 0] for h in ("72h", "20d", "45d")}
        for name in ("real", "scanner_advisor", "learned")}
    for event in all_decision_events:
        choices = _json(event[0]).get("decision_choices") or {}
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
    learning_status_started = time.perf_counter()
    if operator is not None:
        for profile in ("RAPIDO", "PRUDENTE"):
            profile_learning[profile] = operator.learning_status(profile)
    timings_ms["estado_aprendizaje"] = round((time.perf_counter() - learning_status_started) * 1000, 2)
    settings = getattr(request.app.state, "settings", None)
    try:
        meta_config = operator.core.config()
        profile_configs = meta_config.get("profiles") or {}
    except Exception:
        meta_config = {}
        profile_configs = {}
    learning_v1 = (operator.learning_v1_status() if operator is not None else {
        "enabled": bool(meta_config.get("learning_v1_enabled", True)), "changed_at": None,
        "label": "Encendido"})
    if not learning_v1["enabled"]:
        for profile in ("RAPIDO", "PRUDENTE"):
            readiness[profile]["label"] = learning_v1["label"]
            readiness[profile]["status"] = "gris"
            if profile in profile_learning:
                profile_learning[profile]["readiness"]["label"] = learning_v1["label"]
                profile_learning[profile]["readiness"]["status"] = "gris"
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

    # Evidencia por interruptor/perfil. Se calcula en lectura y queda separada
    # de los valores efectivos: esta respuesta nunca escribe ni activa flags.
    from grid.readiness import (MIN_MATURE_CASES, STATE_READY, evaluate_readiness,
        evaluate_allocation_readiness, paired_bootstrap_mean_ci, probability_comparison)
    readiness_switches = {}
    allocation_decision_counts = Counter()
    allocation_labels_by_profile = {"RAPIDO": {}, "PRUDENTE": {}}
    for profile_name, raw_detail in allocation_label_rows:
        profile_key = str(profile_name or "").upper()
        detail = _json(raw_detail)
        event_id = detail.get("allocation_event_id")
        if profile_key in allocation_labels_by_profile and event_id is not None:
            combined = allocation_labels_by_profile[profile_key].setdefault(int(event_id),
                {"source_kind": detail.get("source_kind"), "labels": {},
                 "labeled_at": detail.get("labeled_at")})
            combined["labels"].update(detail.get("labels") or {})
            combined["labeled_at"] = max(str(combined.get("labeled_at") or ""),
                str(detail.get("labeled_at") or ""))
    for profile_name, raw_detail in allocation_readiness_rows:
        detail = _json(raw_detail)
        allocation_decision_counts[(str(profile_name or "").upper(),
            str(detail.get("decision") or "no_agregar"))] += 1
    for profile in ("RAPIDO", "PRUDENTE"):
        learning_mature_times = [row[9] for row in sample_rows
            if str(row[1]).upper() == profile and row[9] is not None
            and row[10] == "rolling_7d_v3_equity" and not row[8]
            and labels_by_sample.get((int(row[0]), "72h"), {}).get("status") == "labeled"]
        learning_item = profile_learning.get(profile) or {}
        learning_n = int((learning_item.get("readiness") or {}).get("best_effective_samples") or 0)
        learn_criteria = [
            {"text": "Al menos 30 muestras efectivas en una señal", "ok": learning_n >= MIN_MATURE_CASES, "value": learning_n},
            {"text": "Holm < 0,05 y peso distinto del valor base", "ok": bool((learning_item.get("readiness") or {}).get("conditions", {}).get("significancia_y_cambio")), "value": None},
            {"text": "Validación temporal ≥ 5", "ok": bool((learning_item.get("readiness") or {}).get("conditions", {}).get("validacion")), "value": None},
            {"text": "Log-loss y Brier mejores que solo p_replay", "ok": bool((learning_item.get("readiness") or {}).get("conditions", {}).get("mejora")), "value": None},
        ]
        learned_ready = evaluate_readiness(n=learning_n, criteria=learn_criteria,
            sample_times=learning_mature_times)
        # Reutiliza la decisión estadística ya implementada por el aprendizaje.
        if (learning_item.get("readiness") or {}).get("status") == "verde":
            learned_ready["state"] = STATE_READY
            learned_ready["summary"] = "La evidencia indica que mejora; puedes encenderlo si estás de acuerdo"
        learned_ready["enabled"] = bool(learning_item.get("enabled", False))

        centered = []
        for actual, candidate, truth, _stamp in centered_by_profile[profile]:
            centered.append(((actual, truth), (candidate, truth)))
        centered_eval = probability_comparison((x[0] for x in centered), (x[1] for x in centered))
        centered_ci = centered_eval["log_loss_delta_ci95"]
        centered_criteria = [
            {"text": "Log-loss de señales centradas menor que el actual", "ok": centered_eval["n"] > 0 and centered_eval["candidate_log_loss"] < centered_eval["actual_log_loss"], "value": centered_eval["candidate_log_loss"]},
            {"text": "Brier de señales centradas menor que el actual", "ok": centered_eval["n"] > 0 and centered_eval["candidate_brier"] < centered_eval["actual_brier"], "value": centered_eval["candidate_brier"]},
            {"text": "IC bootstrap 95 % de Δ log-loss no cruza cero", "ok": bool(centered_ci and centered_ci[1] < 0), "value": centered_ci},
        ]
        centered_times = [stamp for *_row, stamp in centered_by_profile[profile] if stamp is not None]
        centered_ready = evaluate_readiness(n=centered_eval["n"], criteria=centered_criteria,
            adverse_significant=bool(centered_ci and centered_ci[0] > 0), sample_times=centered_times)
        centered_ready["metrics"] = centered_eval
        centered_ready["enabled"] = bool((profile_configs.get(profile) or {}).get("use_centered_signals", False))

        break_rows = [item for item in break_v2_samples if item["profile"] == profile
            and item["method_version"] == "rolling_7d_v3_equity" and not item["excluded"]]
        break_eval = probability_comparison(((x["actual"], x["truth"]) for x in break_rows),
            ((x["v2"], x["truth"]) for x in break_rows))
        exits_by_probability = sorted((item["break"], bool(exited)) for item in break_rows
            for exited in item.get("piece_exits", []) if exited is not None)
        tercile_rates = []
        if exits_by_probability:
            cut_one, cut_two = len(exits_by_probability) // 3, 2 * len(exits_by_probability) // 3
            groups = (exits_by_probability[:cut_one], exits_by_probability[cut_one:cut_two],
                      exits_by_probability[cut_two:])
            tercile_rates = [statistics.fmean(float(value) for _, value in group) if group else None
                for group in groups]
        rates = tercile_rates
        break_ci = break_eval["log_loss_delta_ci95"]
        break_criteria = [
            {"text": "Log-loss de ruptura v2 menor que el actual", "ok": break_eval["n"] > 0 and break_eval["candidate_log_loss"] < break_eval["actual_log_loss"], "value": break_eval["candidate_log_loss"]},
            {"text": "Brier de ruptura v2 menor que el actual", "ok": break_eval["n"] > 0 and break_eval["candidate_brier"] < break_eval["actual_brier"], "value": break_eval["candidate_brier"]},
            {"text": "Salidas del rango ordenadas por tercil", "ok": len(rates) == 3 and all(value is not None for value in rates) and rates[0] < rates[1] < rates[2], "value": rates},
            {"text": "IC bootstrap 95 % de Δ log-loss no cruza cero", "ok": bool(break_ci and break_ci[1] < 0), "value": break_ci},
        ]
        break_times = [item["sample_at"] for item in break_rows if item.get("sample_at") is not None]
        break_ready = evaluate_readiness(n=break_eval["n"], criteria=break_criteria,
            adverse_significant=bool(break_ci and break_ci[0] > 0), sample_times=break_times)
        break_ready["metrics"] = break_eval
        break_ready["enabled"] = bool((profile_configs.get(profile) or {}).get("use_break_prob_v2", False))

        # Bots: la evidencia usa etiquetas 72 h de propuestas persistidas para
        # el perfil; la rentabilidad por propuesta se aproxima al margen neto
        # guardado por la propuesta multiplicado por la venta observada.
        bot_pairs = []
        for proposal in bot_proposals:
            if str(proposal.get("profile") or "RAPIDO").upper() != profile:
                continue
            if proposal.get("method_version") != "rolling_7d_v3_equity":
                continue
            label = proposal.get("labels", {}).get("72") or {}
            if label.get("sell_after_buy") is not None:
                expected = proposal.get("expected_sell_rate_72h")
                if expected is not None:
                    bot_pairs.append((float(expected), float(bool(label["sell_after_buy"])),
                        float(proposal.get("sale_net_target_pct") or .95), proposal.get("at")))
        bot_n = len(bot_pairs)
        observed_rate = statistics.fmean(x[1] for x in bot_pairs) if bot_pairs else None
        expected_rate = statistics.fmean(x[0] for x in bot_pairs) if bot_pairs else None
        net_ci = paired_bootstrap_mean_ci([x[1] * x[2] for x in bot_pairs]) if bot_pairs else None
        bot_criteria = [
            {"text": "Venta real dentro de ±10 puntos de la tasa esperada", "ok": bot_n > 0 and abs(observed_rate-expected_rate) <= .10, "value": None if observed_rate is None else observed_rate-expected_rate},
            {"text": "Ganancia neta esperada positiva y su IC no cruza cero", "ok": bool(net_ci and net_ci[0] > 0), "value": net_ci},
        ]
        bot_times = []
        for _expected, _observed, _margin, stamp in bot_pairs:
            if stamp:
                try: bot_times.append(datetime.fromisoformat(str(stamp)))
                except ValueError: pass
        bot_ready = evaluate_readiness(n=bot_n, criteria=bot_criteria, sample_times=bot_times)
        bot_ready["metrics"] = {"expected_sell_rate": expected_rate, "observed_sell_rate": observed_rate,
            "net_return_ci95_pct": net_ci}
        bot_ready["enabled"] = bool((profile_configs.get(profile) or {}).get("use_touch_bots", False))

        allocation_n = int(allocation_decision_counts.get((profile, "agregar"), 0))
        profile_days = 45 if profile == "PRUDENTE" else 20
        allocation_ready = evaluate_allocation_readiness(
            allocation_labels_by_profile[profile].values(), profile_days=profile_days)
        allocation_ready["proposals"] = allocation_n
        allocation_ready["enabled"] = bool((profile_configs.get(profile) or {}).get("use_extended_allocation", False))
        readiness_switches[profile] = {
            "learned_weights": learned_ready,
            "centered_signals": centered_ready,
            "break_prob_v2": break_ready,
            "touch_bots": bot_ready,
            "extended_allocation": allocation_ready,
        }

    mark_timing("bootstrap_y_medidores")
    grid_intelligence = _grid_intelligence_summary(db)
    mark_timing("inteligencia_grids")
    grid_readiness_alerts = _grid_intelligence_readiness_alerts(grid_intelligence)
    dispatcher = getattr(request.app.state, "alert_dispatcher", None)
    note_readiness = getattr(dispatcher, "note_intelligence_readiness", None)
    if callable(note_readiness):
        note_readiness({**readiness_switches, "GRIDS": grid_readiness_alerts})
    timings_ms["total"] = round((time.perf_counter() - request_started) * 1000, 2)
    if timings_ms["total"] >= 1000:
        _logger.warning("INTELLIGENCE_TIMINGS timings_ms=%s active_threads=%s",
            timings_ms, __import__("threading").active_count())
    return {"updated_at": datetime.now(timezone.utc).isoformat(), "timings_ms": timings_ms,
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
            "replay_calibration_source": replay_calibration_source,
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
            "event_type": row["event_type"], "choices": _json(row["details"]).get("decision_choices") or {},
            "details": _json(row["details"])} for row in ledger_events]},
        "allocation_shadow": [{"meta_id": int(row["meta_id"]), "at": str(row["ts"]),
            "details": _json(row["details"])} for row in allocation_shadow_rows],
        "runs": [{"id": int(row["id"]), "started_at": str(row["started_at"]),
            "finished_at": str(row["finished_at"]), "status": row["status"],
            "summary": _json(row["summary_json"])} for row in runs],
        "grid_intelligence": grid_intelligence,
        "grid_intelligence_readiness": grid_readiness_alerts,
        "switches": switches, "shadow_switches": shadow_switches,
        "readiness_switches": readiness_switches, "global_flags": global_flags,
        "learning_v1": learning_v1,
        "models_link": "#models", "battle_link": "#battle"}


def _cache_count(db):
    with db.engine.connect() as conn:
        return int(conn.execute(select(func.count()).select_from(db.meta_learning_replay_cache)).scalar_one())
