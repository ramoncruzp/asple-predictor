"""Operador y estimador aislados del comportamiento de grids normales."""
from __future__ import annotations

import json
import itertools
import hashlib
import math
import logging
import os
import statistics
import threading
import time
import uuid
from datetime import timedelta, datetime, timezone
from decimal import Decimal

import numpy as np

from grid.sim.sweep import build_windows, _slice
from grid.sim.touch_stats import aggregate_touch_stats, touch_stats
from grid.sim.runner import run_simulation
from grid.sim.runner import FILTERS
from grid.levels import compute_lines, plan_cells, GridConfigError
from grid.market_book import book_is_two_sided, book_mid
from grid.guards import sell_level_conflicts
from data.exchange_filters import SymbolFilters
from apscheduler.schedulers.background import BackgroundScheduler
from grid.policy import break_prob, DEFAULT_SMART_PARAMS, validate_params


def summarize_replays(rows, independent_window_days=30, target_usdt=None):
    """Resume ventanas alineadas; separa cantidad solapada e independiente."""
    hits = [row for row in rows if row.get("reached")]
    times = [float(row["days"]) for row in hits if row.get("days") is not None]
    drawdowns = [float(row.get("drawdown_pct", 0.0)) for row in rows]
    worst_loss_vs_capital = [float(row.get("worst_loss_vs_capital_pct", 0.0)) for row in rows]
    step_days = [float(row.get("step_days", 7.0)) for row in rows]
    step = max(1.0, min(step_days, default=7.0))
    independent = max(1, int(math.ceil(float(independent_window_days) / step)))
    return {"probability": len(hits) / len(rows) if rows else 0.0,
            "days_median": float(np.median(times)) if times else None,
            "days_p10": float(np.percentile(times, 10)) if times else None,
            "days_p90": float(np.percentile(times, 90)) if times else None,
            # worst_drawdown_pct: desde el máximo de la ventana (puede superar el
            # límite de pérdida sin haberlo tocado nunca). worst_loss_vs_capital_pct:
            # contra el capital inicial, la que de verdad se compara con el límite
            # del perfil. Ver REPORTE_FASE40C.md, parte B3.
            "worst_drawdown_pct": max(drawdowns, default=0.0),
            "worst_loss_vs_capital_pct": max(worst_loss_vs_capital, default=0.0),
            "median_progress_pct": (float(np.median([float(row.get("pnl_usdt", 0.0)) for row in rows]))
                                    / float(target_usdt) * 100 if rows and target_usdt else None),
            "rolling_windows": len(rows),
            "independent_windows": len(rows) // independent,
            "evidence_sufficient": len(rows) // independent >= 3,
            "window_days": float(independent_window_days),
            "evidence_warning": (None if len(rows) // independent >= 3 else
                f"Hay {len(rows) // independent} ventanas independientes de {independent_window_days} días; la evidencia histórica es poca."),
            "low_likelihood": (len(hits) / len(rows) if rows else 0.0) < .25}


def rank_alternatives(alternatives, probability_key="probability_replay"):
    """Probabilidad de replay, avance mediano, días y drawdown."""
    def probability(row):
        if probability_key in row:
            return float(row.get(probability_key) or 0)
        estimate = row.get("estimate") or {}
        return float(estimate.get(probability_key,
            estimate.get("probability_replay", estimate.get("probability", 0))) or 0)

    return sorted(alternatives, key=lambda row: (
        0 if int(row["estimate"].get("rolling_windows", 1) or 0) > 0 else 1,
        -probability(row),
        -float(row["estimate"].get("median_progress_pct") or 0),
        float(row["estimate"].get("days_median") if row["estimate"].get("days_median") is not None else math.inf),
        float(row["estimate"].get("worst_drawdown_pct") or 0)))


def loss_reallocation_allowed(expected_gain_usdt, realized_loss_usdt, close_fee_usdt, reopen_fee_usdt):
    """Permite rotar solo si la ganancia esperada supera pérdida y ambas comisiones."""
    return float(expected_gain_usdt) > (abs(float(realized_loss_usdt)) +
        float(close_fee_usdt) + float(reopen_fee_usdt))


def actual_close_reallocation_allowed(expected_gain_usdt, close_result, reopen_fee_usdt):
    """Use the verified liquidation P&L, which already includes recorded trade fees."""
    liquidated = close_result.get("liquidated_cells", [])
    realized_pnl = sum(float(row.get("pnl_realized", row.get("cycle_pnl", 0)) or 0)
                       for row in liquidated)
    fees = sum(float(row.get("buy_fee_usdt", 0) or 0) + float(row.get("sell_fee_usdt", 0) or 0)
               for row in liquidated)
    realized_loss = max(0.0, -realized_pnl)
    return float(expected_gain_usdt) > realized_loss + float(reopen_fee_usdt), {
        "realized_pnl_usdt": realized_pnl, "realized_loss_usdt": realized_loss,
        "recorded_trade_fees_usdt": fees, "reopen_fee_estimate_usdt": float(reopen_fee_usdt),
    }


def signal_adjusted_probability(replay_probability, signals, weights, max_abs_logit=1.0):
    """logit(p_meta)=logit(p_replay)+sum(peso_i*señal_i normalizada)."""
    from grid.meta_learning import DEFAULT_SIGNAL_WEIGHTS
    p0 = max(.01, min(.99, float(replay_probability)))
    evidence = sum(float(weights.get(row["name"], DEFAULT_SIGNAL_WEIGHTS.get(row["name"], .25))) * float(row["normalized"])
                   for row in signals)
    cap = max(0.0, float(max_abs_logit))
    evidence = max(-cap, min(cap, evidence))
    logit = math.log(p0 / (1 - p0)) + evidence
    return 1 / (1 + math.exp(-max(-30, min(30, logit))))


def combine_aligned_replays(alternatives, target_usdt, window_days):
    """Suma resultados de piezas por inicio de ventana compartido."""
    maps = []
    for alternative in alternatives:
        maps.append({str(row["window_start"]): row for row in alternative.get("window_results", [])})
    common = set.intersection(*(set(row) for row in maps)) if maps else set()
    rows = []
    for start in sorted(common):
        outcomes = [mapping[start] for mapping in maps]
        pnl = sum(float(row.get("pnl_usdt", 0.0)) for row in outcomes)
        reached_days = [float(row["days"]) for row in outcomes if row.get("days") is not None]
        rows.append({"reached": pnl >= float(target_usdt), "pnl_usdt": pnl,
                     "days": max(reached_days) if reached_days else None,
                     "drawdown_pct": max(float(row.get("drawdown_pct", 0)) for row in outcomes),
                     "worst_loss_vs_capital_pct": max(
                         float(row.get("worst_loss_vs_capital_pct", 0)) for row in outcomes),
                     "step_days": 7})
    return summarize_replays(rows, independent_window_days=window_days, target_usdt=target_usdt), rows


def allocation_vectors(piece_count, per_coin_cap_pct):
    """Repartos en pasos de 10 % y acotados al capital máximo por moneda."""
    cap = int(float(per_coin_cap_pct) // 10) * 10
    max_total = min(100, piece_count * cap)
    allowed_totals = {max_total, max_total - 10}
    result = []
    def visit(values, left):
        if len(values) == piece_count:
            if sum(values) in allowed_totals and sum(values) >= piece_count * 10:
                result.append(tuple(values))
            return
        for share in range(10, min(cap, left - 10 * (piece_count - len(values) - 1)) + 1, 10):
            visit([*values, share], left - share)
    visit([], 100)
    return result


def profile_candidate_exclusion(profile, sigma, q33, q67, model_ready, min_verifications=30):
    """Retorna solo exclusiones propias del perfil; Scanner mantiene sus gates."""
    if sigma is None:
        return "sigma realizada no disponible"
    if str(profile).upper() == "PRUDENTE":
        if q33 is None or q67 is None:
            return "evidencia insuficiente para terciles (mínimo 3 candidatas)"
        if not float(q33) <= float(sigma) <= float(q67):
            return "sigma fuera del tercil medio Prudente"
        if not model_ready:
            return f"Prudente requiere readiness y al menos {int(min_verifications)} verificaciones"
    return None


def counterfactual_policy_decision(current, alternatives, history=(), state=None, *,
        required_reviews=2, cooldown_seconds=3600, max_actions_per_24h=4, now=None):
    """Política pura compartida: aplica materialidad, persistencia y frenos temporales."""
    state = state or {}
    current = current or {}
    options = [dict(row) for row in (alternatives or []) if row.get("probability") is not None]
    if not options:
        return {"action": "mantener", "destination": None, "reason": "sin alternativa evaluable", "streak": 0}
    best = max(options, key=lambda row: (float(row.get("probability") or 0),
        float(row.get("median_progress_pct") or 0)))
    old_p = float(current.get("probability") or 0)
    new_p = float(best.get("probability") or 0)
    delta_pp = (new_p - old_p) * 100
    old_progress = float(current.get("median_progress_pct") or 0)
    new_progress = float(best.get("median_progress_pct") or 0)
    progress_delta = (new_progress / old_progress - 1) * 100 if old_progress > 0 else (100 if new_progress > 0 else 0)
    material = (delta_pp >= float(best.get("min_probability_delta_pp", 5)) or
        (abs(delta_pp) <= float(best.get("probability_tie_tolerance_pp", 2)) and
         progress_delta >= float(best.get("min_progress_delta_pct", 10))))
    key = str(best.get("alternative_key") or best.get("alternative_id") or best.get("destination") or "")
    prior_streak = 0
    for row in reversed(list(history)):
        if row.get("material") and row.get("alternative_key") == key:
            prior_streak += 1
        else:
            break
    streak = prior_streak + 1 if material else 0
    opened_at = state.get("opened_at")
    if not material:
        reason = "mejora inferior a la materialidad"
    elif streak < max(1, int(required_reviews)):
        reason = "persistencia insuficiente"
    elif "opened_at" in state and opened_at is None:
        reason = "pieza sin fecha de apertura"
    elif opened_at is not None and (now is None or float(now) - float(opened_at) < cooldown_seconds):
        reason = "pieza aún no cumple la antigüedad mínima"
    elif state.get("last_action_at") is not None and now is not None and float(now) - float(state["last_action_at"]) < cooldown_seconds:
        reason = "pieza en enfriamiento"
    elif int(state.get("actions_24h") or 0) >= max_actions_per_24h:
        reason = "límite diario alcanzado"
    else:
        reason = "alternativa material y persistente"
    return {"action": str(best.get("action") or "rotar") if reason == "alternativa material y persistente" else "mantener",
        "destination": best.get("destination") if reason == "alternativa material y persistente" else None,
        "alternative_key": key, "material": material, "streak": streak,
        "probability_improvement_pp": delta_pp, "progress_improvement_pct": progress_delta,
        "reason": reason}


def validation_calibration(rows, weights, logit_cap=1.0):
    """Deciles de calibración en validación para el modelo y p_replay puro."""
    from grid.meta_learning import extract_learning_features
    scored = {"model": [], "replay_only": []}
    for row in rows or []:
        base = row.get("base_probability_smoothed", row.get("base_probability"))
        label = row.get("label") or {}
        if base is None or "reached" not in label:
            continue
        p = max(1e-6, min(1 - 1e-6, float(base)))
        features, _present = extract_learning_features(row.get("signals") or row.get("signals_json") or {},
            row.get("feature_weights"))
        evidence = sum(float(weights.get(name, 0)) * float(value) for name, value in features.items())
        logit = math.log(p / (1 - p)) + max(-logit_cap, min(logit_cap, evidence))
        adjusted = 1 / (1 + math.exp(-max(-30, min(30, logit))))
        scored["model"].append((adjusted, int(bool(label["reached"]))))
        scored["replay_only"].append((p, int(bool(label["reached"]))))
    output = {}
    for name, values in scored.items():
        values.sort(key=lambda item: item[0])
        groups = []
        for decile in range(10):
            start, end = round(decile * len(values) / 10), round((decile + 1) * len(values) / 10)
            chunk = values[start:end]
            if chunk:
                groups.append({"decile": decile + 1,
                    "predicted_mean": statistics.fmean(item[0] for item in chunk),
                    "success_rate": statistics.fmean(item[1] for item in chunk), "samples": len(chunk)})
        output[name] = groups
    return output


class MetaOperator:
    """Planes de cinco minutos, análisis asíncrono y revisiones de quince minutos."""
    PLAN_TTL_SECONDS = 600  # Fase 40c-3: Ramón subió el vencimiento de 5 a 10 min; editable por perfil (plan_ttl_seconds)
    # Fase 40c, parte B4: el precio se mueve continuamente y una clave de caché
    # redondeada a 8 decimales casi nunca se repite. Se cuantiza el rango
    # relativo en pasos de 0.25% del precio: un movimiento intradía menor a eso
    # reutiliza el mismo resultado de replay.
    REPLAY_RANGE_QUANT_STEP = 0.0025
    # El Advisor se cachea en relativo (piso/techo sobre el precio de cuando se
    # calculó) y se reancla al precio actual al leer; si el precio se movió más
    # de este umbral (o quedó fuera del rango original), se recalcula.
    ADVISOR_REANCHOR_DRIFT_PCT = 3.0

    def __init__(self, core, db, scanner, engine, settings, *, advisor=None, alert=None,
                 clock=time.time, price_drift_pct=1.0, cache_enabled=True, cache_writes=True):
        self.core, self.db, self.scanner = core, db, scanner
        self.engine, self.settings, self.advisor = engine, settings, advisor
        self.alert = alert or (lambda *_args: None)
        self.clock, self.price_drift_pct = clock, float(price_drift_pct)
        self.cache_enabled = bool(cache_enabled)
        self.cache_writes = bool(cache_writes)
        self._lock = threading.RLock()
        self._jobs = {}
        self._plans = {}
        self._cache = {}
        self._advisor_cache = {}
        self._candles_cache = {}
        self._above_ceiling_count = {}
        self._outside_range_count = {}
        self._hard_filter_misses = {}
        self._symbol_filters_cache = {}
        self._review_guard = threading.Lock()
        self._precompute_guard = threading.Lock()
        self._learning_guard = threading.Lock()
        self._thread = None
        self._opener = None
        self.scheduler = BackgroundScheduler(timezone="UTC")
        self._operation_lock = getattr(getattr(self.core, "monitor", None), "ops_lock", None) or threading.RLock()

    def _ledger(self, meta_id, event, details, **kwargs):
        self.core._ledger(meta_id, None, event, details, **kwargs)

    def _cache_key(self, kind, payload):
        day = datetime.fromtimestamp(self.clock(), timezone.utc).date().isoformat()
        encoded = json.dumps({"day": day, "kind": kind, "payload": payload}, sort_keys=True, default=str)
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest(), day

    def _effective_learned_weights(self, profile):
        """Resuelve por señal: live suficiente, luego histórico aceptado, luego prior."""
        from sqlalchemy import select
        from grid.meta_learning import LEARNING_SIGNALS, DEFAULT_SIGNAL_WEIGHTS
        with self.db.engine.connect() as conn:
            versions = conn.execute(select(self.db.meta_learning_weight_versions).where(
                self.db.meta_learning_weight_versions.c.profile == str(profile).upper()).order_by(
                    self.db.meta_learning_weight_versions.c.created_at.desc())).mappings().all()
        accepted = {}
        for row in versions:
            kind = str(row["source_kind"])
            if kind not in accepted and row["status"] == "updated":
                accepted[kind] = row
        weights, sources, details = {}, {}, {}
        for signal in LEARNING_SIGNALS:
            chosen = None
            live = accepted.get("live")
            historical = accepted.get("historical_reconstructed")
            if live:
                summary = json.loads(live["summary_json"] or "{}")
                item = (summary.get("signals") or {}).get(signal, {})
                if int(item.get("effective_samples") or 0) >= 30:
                    chosen = (live, item, "live")
            if chosen is None and historical:
                summary = json.loads(historical["summary_json"] or "{}")
                item = (summary.get("signals") or {}).get(signal, {})
                if int(item.get("effective_samples") or 0) >= 30:
                    chosen = (historical, item, "historical_reconstructed")
            if chosen:
                version, item, source = chosen
                weights[signal] = float(json.loads(version["weights_json"] or "{}").get(
                    signal, DEFAULT_SIGNAL_WEIGHTS[signal]))
                sources[signal] = source
                details[signal] = {**item, "version_id": int(version["id"])}
            else:
                latest_signal = {}
                for row in versions:
                    candidate_detail = (json.loads(row["summary_json"] or "{}").get("signals") or {}).get(signal)
                    if candidate_detail:
                        latest_signal = candidate_detail
                        break
                weights[signal], sources[signal] = DEFAULT_SIGNAL_WEIGHTS[signal], "sin evidencia"
                details[signal] = {**latest_signal, "effective_samples": max(
                    [int(((json.loads(row["summary_json"] or "{}").get("signals") or {}).get(signal, {})).get("effective_samples") or 0)
                     for row in versions], default=0),
                     "reason": latest_signal.get("reason", "sin evidencia suficiente")}
        version_ids = sorted({item.get("version_id") for item in details.values() if item.get("version_id")})
        return weights, sources, details, version_ids

    def set_learning_switch(self, profile, enabled, *, confirm, actor):
        from sqlalchemy import select
        profile = str(profile).upper()
        if profile not in {"PRUDENTE", "RAPIDO"}:
            raise ValueError("Perfil Meta no válido.")
        if not confirm or not str(actor or "").strip():
            raise ValueError("Confirma el cambio e indica quién lo autorizó.")
        weights, sources, _details, version_ids = self._effective_learned_weights(profile)
        version_id = version_ids[-1] if version_ids else None
        with self.db.engine.begin() as conn:
            raw = conn.execute(select(self.db.meta_profile_settings.c.settings).where(
                self.db.meta_profile_settings.c.profile == profile)).scalar_one_or_none()
            values = json.loads(raw or "{}")
            values["use_learned_weights"] = bool(enabled)
            found = conn.execute(select(self.db.meta_profile_settings.c.profile).where(
                self.db.meta_profile_settings.c.profile == profile)).first()
            if found:
                conn.execute(self.db.meta_profile_settings.update().where(
                    self.db.meta_profile_settings.c.profile == profile).values(
                        settings=json.dumps(values, sort_keys=True), updated_at=datetime.now(timezone.utc)))
            else:
                conn.execute(self.db.meta_profile_settings.insert().values(profile=profile,
                    settings=json.dumps(values, sort_keys=True), updated_at=datetime.now(timezone.utc)))
            conn.execute(self.db.meta_learning_switch_events.insert().values(profile=profile,
                enabled=bool(enabled), actor=str(actor).strip()[:80], weight_version_id=version_id,
                changed_at=datetime.now(timezone.utc)))
        return {"profile": profile, "enabled": bool(enabled), "actor": str(actor).strip(),
            "weight_version_id": version_id, "weights": weights, "sources": sources}

    def learning_status(self, profile):
        from sqlalchemy import select
        profile = str(profile).upper()
        weights, sources, details, versions = self._effective_learned_weights(profile)
        with self.db.engine.connect() as conn:
            raw = conn.execute(select(self.db.meta_profile_settings.c.settings).where(
                self.db.meta_profile_settings.c.profile == profile)).scalar_one_or_none()
            config = json.loads(raw or "{}")
            weight_rows = conn.execute(select(self.db.meta_learning_weight_versions).where(
                self.db.meta_learning_weight_versions.c.profile == profile).order_by(
                    self.db.meta_learning_weight_versions.c.created_at.desc()).limit(10)).mappings().all()
            run = conn.execute(select(self.db.meta_learning_runs).order_by(
                self.db.meta_learning_runs.c.id.desc()).limit(1)).mappings().first()
            switch = conn.execute(select(self.db.meta_learning_switch_events).where(
                self.db.meta_learning_switch_events.c.profile == profile).order_by(
                    self.db.meta_learning_switch_events.c.id.desc()).limit(1)).mappings().first()
            goal_ids = [int(row[0]) for row in conn.execute(select(self.db.meta_goals.c.id).where(
                self.db.meta_goals.c.profile == profile)).all()]
            ledger_rows = (conn.execute(select(self.db.meta_ledger.c.ts, self.db.meta_ledger.c.details).where(
                self.db.meta_ledger.c.meta_id.in_(goal_ids),
                self.db.meta_ledger.c.event_type.in_(("META_PLAN_EVALUATED", "META_OPERATOR_REVIEW_DECISION"))).order_by(
                    self.db.meta_ledger.c.id.desc()).limit(500)).all() if goal_ids else [])
            comparison_first = (conn.execute(select(self.db.meta_ledger.c.ts).where(
                self.db.meta_ledger.c.meta_id.in_(goal_ids),
                self.db.meta_ledger.c.event_type == "META_OPERATOR_REVIEW_DECISION",
                self.db.meta_ledger.c.details.like('%"comparison_version": 2%')).order_by(
                    self.db.meta_ledger.c.id.asc()).limit(1)).first() if goal_ids else None)
            sample_rows = conn.execute(select(self.db.meta_learning_samples.c.id,
                self.db.meta_learning_samples.c.alternative_id,
                self.db.meta_learning_samples.c.parameters_json).where(
                    self.db.meta_learning_samples.c.alternative_id.is_not(None),
                    self.db.meta_learning_samples.c.profile == profile).order_by(
                    self.db.meta_learning_samples.c.id.desc()).limit(5000)).all()
            sample_ids = [int(row[0]) for row in sample_rows]
            label_rows = conn.execute(select(self.db.meta_learning_labels.c.sample_id,
                self.db.meta_learning_labels.c.horizon, self.db.meta_learning_labels.c.label_json).where(
                    self.db.meta_learning_labels.c.sample_id.in_(sample_ids))).all() if sample_ids else []
        alt_samples = {str(row[1]): (int(row[0]), json.loads(row[2] or "{}")) for row in sample_rows}
        label_map = {(int(row[0]), str(row[1])): json.loads(row[2] or "{}") for row in label_rows}
        comparison = {key: {"decisions": 0, "different_from_real": 0,
            "72h": {"n": 0, "reached": 0, "log_loss": 0.0, "brier": 0.0},
            "profile": {"n": 0, "reached": 0, "log_loss": 0.0, "brier": 0.0}}
            for key in ("real", "scanner_advisor", "learned")}
        comparison_since = str(comparison_first[0]) if comparison_first else None
        for raw_ts, raw_event in ledger_rows:
            event = json.loads(raw_event or "{}")
            choices = event.get("decision_choices") or {}
            # Solo se cuentan comparaciones nuevas con contrato explícito.
            # Los eventos previos permanecen intactos como evidencia histórica.
            if int(choices.get("comparison_version") or 0) != 2:
                continue
            real_choice = choices.get("real") or {}
            real_id = (choices.get("real") or {}).get("alternative_id")
            real_decision = real_choice.get("decision")
            for name in comparison:
                choice = choices.get(name) or {}
                alt_id = choice.get("alternative_id")
                decision = choice.get("decision")
                if not decision:
                    continue
                comparison[name]["decisions"] += 1
                differs = decision != real_decision
                if decision in {"seleccionar_alternativa", "rotar", "reajustar", "cerrar"} and decision == real_decision:
                    differs = alt_id != real_id
                comparison[name]["different_from_real"] += int(name != "real" and differs)
                if not alt_id:
                    continue
                saved = alt_samples.get(str(alt_id))
                if saved is None:
                    continue
                sample_id, params = saved
                for horizon_name, horizon in (("72h", "72h"),
                    ("profile", f"{int(params.get('profile_days', 20))}d")):
                    label = label_map.get((sample_id, horizon))
                    if not label or label.get("status") != "labeled" or label.get("reached") is None:
                        continue
                    metric = comparison[name][horizon_name]
                    outcome = int(bool(label["reached"]))
                    probability = max(.001, min(.999, float(choice.get("probability") or 0)))
                    metric["n"] += 1
                    metric["reached"] += outcome
                    metric["log_loss"] += -(outcome * math.log(probability) +
                        (1-outcome) * math.log(1-probability))
                    metric["brier"] += (probability-outcome) ** 2
        for item in comparison.values():
            for metric in (item["72h"], item["profile"]):
                if metric["n"]:
                    metric["hit_rate"] = metric["reached"] / metric["n"]
                    metric["log_loss"] /= metric["n"]
                    metric["brier"] /= metric["n"]
        enabled = bool(config.get("use_learned_weights", False))
        from grid.meta_learning import DEFAULT_SIGNAL_WEIGHTS
        signals = {name: {"current_weight": weights[name] if enabled else DEFAULT_SIGNAL_WEIGHTS[name], "learned_weight": weights[name],
            "source": sources[name], "effective_samples": details[name].get("effective_samples", 0),
            "p_value_holm": details[name].get("p_value_holm"),
            "reason": details[name].get("reason", "peso aceptado"),
            "version_id": details[name].get("version_id")} for name in weights}
        latest_summary = json.loads(weight_rows[0]["summary_json"] or "{}") if weight_rows else {}
        baseline = latest_summary.get("baseline_validation") or {}
        validation = latest_summary.get("validation") or {}
        summary_signals = latest_summary.get("signals") or {}
        best_effective = max((int(item.get("effective_samples") or 0)
            for item in summary_signals.values()), default=0)
        readiness_conditions = {
            "muestra": best_effective >= 30,
            "significancia_y_cambio": any(float(item.get("p_value_holm") or 1) < .05
                and abs(float(item.get("weight", DEFAULT_SIGNAL_WEIGHTS.get(name, .25))) -
                    DEFAULT_SIGNAL_WEIGHTS.get(name, .25)) > 1e-9
                for name, item in summary_signals.items()),
            "validacion": int(latest_summary.get("validation_effective") or 0) >= 5,
            "mejora": bool(latest_summary.get("improves")) and bool(
                validation.get("model") and baseline.get("baseline") and
                validation["model"].get("log_loss", math.inf) < baseline["baseline"].get("log_loss", -math.inf) and
                validation["model"].get("brier", math.inf) < baseline["baseline"].get("brier", -math.inf)),
        }
        if not readiness_conditions["muestra"]:
            readiness_status, readiness_label = "gris", "Recolectando datos"
        elif all(readiness_conditions.values()):
            readiness_status, readiness_label = "verde", "Listo para encender"
        else:
            readiness_status, readiness_label = "amarillo", "Hay datos, sin señal útil todavía"
        return {"profile": profile, "enabled": enabled,
            "readiness": {"status": readiness_status, "label": readiness_label,
                "best_effective_samples": best_effective, "remaining": max(0, 30-best_effective),
                "conditions": readiness_conditions, "signals": summary_signals},
            "signals": signals, "version_ids": versions,
            "decision_comparison": comparison,
            "decision_comparison_since": comparison_since,
            "validation": {"model": validation.get("model"), "replay_only": baseline.get("baseline")},
            "weight_versions": [{"id": int(row["id"]), "source": row["source_kind"],
                "status": row["status"], "created_at": str(row["created_at"]),
                "summary": json.loads(row["summary_json"] or "{}")} for row in weight_rows],
            "last_run": None if run is None else {"id": int(run["id"]), "status": run["status"],
                "started_at": str(run["started_at"]), "finished_at": str(run["finished_at"]),
                "summary": json.loads(run["summary_json"] or "{}").get("timings_s"),
                "pending": json.loads(run["summary_json"] or "{}").get("pending_template_ids", [])},
            "last_switch": None if switch is None else {"enabled": bool(switch["enabled"]),
                "actor": switch["actor"], "changed_at": str(switch["changed_at"]),
                "weight_version_id": switch["weight_version_id"]}}

    @staticmethod
    def _learned_is_worse(comparison):
        learned = (comparison or {}).get("learned", {}).get("72h", {})
        real = (comparison or {}).get("real", {}).get("72h", {})
        return bool(learned.get("n", 0) and real.get("n", 0) and
            learned.get("log_loss", 0) > real.get("log_loss", 0) and
            learned.get("brier", 0) > real.get("brier", 0))

    def _alert_if_learned_worse(self, profile, current_comparison, current_version_ids):
        from sqlalchemy import select
        with self.db.engine.connect() as conn:
            raw = conn.execute(select(self.db.meta_profile_settings.c.settings).where(
                self.db.meta_profile_settings.c.profile == profile)).scalar_one_or_none()
            goals = conn.execute(select(self.db.meta_goals.c.id).where(
                self.db.meta_goals.c.profile == profile).order_by(self.db.meta_goals.c.id.desc())).all()
            runs = conn.execute(select(self.db.meta_learning_runs.c.summary_json).where(
                self.db.meta_learning_runs.c.status == "completed").order_by(
                    self.db.meta_learning_runs.c.id.desc()).limit(1)).all()
        if not json.loads(raw or "{}").get("use_learned_weights") or not goals or not runs:
            return False
        prior_record = json.loads(runs[0][0] or "{}").get("learned_vs_real", {}).get(profile) or {}
        prior = prior_record.get("metrics", prior_record)
        prior_versions = prior_record.get("weight_version_ids", [])
        if (prior_versions != current_version_ids and self._learned_is_worse(prior)
                and self._learned_is_worse(current_comparison)):
            self.alert("META_LEARNED_WEIGHTS_WORSE", int(goals[0][0]), {
                "profile": profile, "runs_in_a_row": 2,
                "learned": current_comparison.get("learned", {}).get("72h"),
                "real": current_comparison.get("real", {}).get("72h"),
                "automatic_disable": False})
            return True
        return False

    def _quantize_relative_range(self, ratio, step=None):
        """Redondea un ratio precio/precio a pasos fijos (0.25% por defecto)
        para que pequeños movimientos intradía no invaliden el caché de replay."""
        step = self.REPLAY_RANGE_QUANT_STEP if step is None else step
        if ratio <= 0 or step <= 0:
            return ratio
        return round(round(ratio / step) * step, 6)

    def _store_advisor_cache_entry(self, advisory, anchor_price):
        """Guarda la recomendación del Advisor en relativo (piso/techo sobre el
        precio de hoy) para poder reanclarla al precio actual al leerla."""
        entry = dict(advisory)
        floor_val, ceiling_val = advisory.get("recommended_floor"), advisory.get("recommended_ceiling")
        if floor_val is not None and ceiling_val is not None and anchor_price > 0:
            entry["recommended_floor_ratio"] = float(floor_val) / anchor_price
            entry["recommended_ceiling_ratio"] = float(ceiling_val) / anchor_price
            entry["anchor_price"] = anchor_price
            entry.pop("recommended_floor", None)
            entry.pop("recommended_ceiling", None)
        return entry

    def _reanchor_advisor_cache(self, entry, price):
        """Reancla una entrada de caché del Advisor al precio actual.

        Devuelve None (caché inservible; hay que recalcular) si la entrada es de
        un formato viejo sin ratio, si el precio se movió más del umbral, o si
        el precio actual quedó fuera del rango original con el que se calculó.
        """
        if not entry or "recommended_floor_ratio" not in entry or "recommended_ceiling_ratio" not in entry:
            return None
        anchor_price = float(entry.get("anchor_price") or 0)
        if anchor_price <= 0 or price <= 0:
            return None
        drift_pct = abs(price / anchor_price - 1) * 100
        floor_ratio, ceiling_ratio = float(entry["recommended_floor_ratio"]), float(entry["recommended_ceiling_ratio"])
        original_floor, original_ceiling = floor_ratio * anchor_price, ceiling_ratio * anchor_price
        if drift_pct > self.ADVISOR_REANCHOR_DRIFT_PCT or not (original_floor < price < original_ceiling):
            return None
        reanchored = dict(entry)
        reanchored["recommended_floor"] = floor_ratio * price
        reanchored["recommended_ceiling"] = ceiling_ratio * price
        reanchored.pop("recommended_floor_ratio", None)
        reanchored.pop("recommended_ceiling_ratio", None)
        reanchored.pop("anchor_price", None)
        return reanchored

    def _persistent_cache_get(self, kind, payload):
        if not self.cache_enabled or self.db is None or not hasattr(self.db, "meta_analysis_cache"):
            return None
        key, _day = self._cache_key(kind, payload)
        try:
            from sqlalchemy import select
            with self.db.engine.connect() as conn:
                value = conn.execute(select(self.db.meta_analysis_cache.c.value_json).where(
                    self.db.meta_analysis_cache.c.cache_key == key)).scalar_one_or_none()
            return None if value is None else json.loads(value)
        except Exception:
            return None

    def _persistent_cache_set(self, kind, payload, value):
        if not self.cache_enabled or not self.cache_writes or self.db is None or not hasattr(self.db, "meta_analysis_cache"):
            return
        key, day = self._cache_key(kind, payload)
        try:
            from sqlalchemy import select
            table = self.db.meta_analysis_cache
            encoded = json.dumps(value, sort_keys=True, default=str)
            with self.db.engine.begin() as conn:
                exists = conn.execute(select(table.c.cache_key).where(table.c.cache_key == key)).first()
                values = {"cache_kind": kind, "analysis_day": day, "value_json": encoded,
                          "created_at": datetime.now(timezone.utc).replace(tzinfo=None)}
                if exists:
                    conn.execute(table.update().where(table.c.cache_key == key).values(**values))
                else:
                    conn.execute(table.insert().values(cache_key=key, **values))
        except Exception:
            # Cache is an optimization only; read-only contexts and older DBs continue.
            return

    def analyze(self, meta_id):
        goal = self.core.get_goal(meta_id)
        if goal["status"] != "DRAFT":
            raise ValueError("El análisis solo está disponible para una meta DRAFT.")
        job_id = uuid.uuid4().hex
        with self._lock:
            self._jobs[job_id] = {"status": "PENDING", "meta_id": int(meta_id), "created_at": self.clock()}
            self._thread = threading.Thread(target=self._run_analysis, args=(job_id, goal), daemon=True,
                                            name=f"meta-analysis-{meta_id}")
            self._thread.start()
        return {"job_id": job_id, "status": "PENDING"}

    def job(self, job_id):
        with self._lock:
            row = self._jobs.get(str(job_id))
            if row is None:
                raise ValueError("Análisis no encontrado.")
            return dict(row)

    def _run_analysis(self, job_id, goal):
        started = time.perf_counter()
        try:
            data = self._build_plan(goal)
            plan_id = uuid.uuid4().hex
            now = self.clock()
            seconds = round(time.perf_counter() - started, 4)
            ttl = int((goal.get("config_snapshot") or {}).get("plan_ttl_seconds", self.PLAN_TTL_SECONDS))
            data.update({"plan_id": plan_id, "meta_id": int(goal["id"]),
                         "created_at": now, "expires_at": now + ttl,
                         "expires_in_seconds": ttl,
                         "analysis_seconds": seconds})
            with self._lock:
                self._plans[plan_id] = data
                self._jobs[job_id] = {"job_id": job_id, "meta_id": int(goal["id"]),
                                      "status": "COMPLETE", "plan": data}
            self._ledger(goal["id"], "META_ANALYSIS_COMPLETE",
                         {"job_id": job_id, "plan_id": plan_id, "analysis_seconds": seconds})
        except Exception as exc:
            seconds = round(time.perf_counter() - started, 4)
            with self._lock:
                self._jobs[job_id] = {"job_id": job_id, "status": "FAILED", "error": str(exc),
                                      "analysis_seconds": seconds}
            self._ledger(goal["id"], "META_ANALYSIS_FAILED",
                         {"job_id": job_id, "error": str(exc), "analysis_seconds": seconds})

    def _book_ticker(self, symbol):
        """Libro bid/ask desde el cliente público del Scanner.

        El cliente público real (BinanceClient) no tiene get_book_ticker; igual que
        GridScanService, se cae al cliente crudo de python-binance.
        """
        client = self.scanner.client
        if callable(getattr(client, "get_book_ticker", None)):
            book = client.get_book_ticker(symbol)
        else:
            raw = getattr(client, "client", client)
            book = raw.get_orderbook_ticker(symbol=symbol)
        book = dict(book)
        book["bid_price"] = book.get("bid_price", book.get("bidPrice"))
        book["ask_price"] = book.get("ask_price", book.get("askPrice"))
        return book

    def _testnet_openability(self, piece, goal, *, replacing_grid_id=None, include_held_proceeds=True):
        """Valida Testnet y las restricciones de celdas sin crear órdenes."""
        symbol = str(piece.get("symbol") or "").upper()
        try:
            if callable(getattr(self.engine, "_active_coin", None)) and not self.engine._active_coin(symbol):
                return {"allowed": False, "reason": f"{symbol} no está activa en Coin Registry"}
            filters, snapshot, avg_price = self.engine._market_context(symbol)
            if not book_is_two_sided(snapshot):
                return {"allowed": False, "reason": (f"libro de Testnet unilateral para {symbol}: "
                    f"bid {snapshot.get('bid_price')}, ask {snapshot.get('ask_price')}")}
            mid = book_mid(snapshot)
            low, high = Decimal(str(piece["floor"])), Decimal(str(piece["ceiling"]))
            if mid is None or not low < mid < high:
                return {"allowed": False, "reason": f"precio de Testnet {mid} fuera del rango {low}–{high}"}
            kind = piece.get("piece_type", "grid")
            strategy = str(piece.get("strategy", "simple")).lower()
            capital = Decimal(str(piece.get("capital_usdt") or 0))
            cfg = goal.get("config_snapshot") or {}
            if isinstance(cfg, str):
                cfg = json.loads(cfg)
            coin_cap = Decimal(str(goal["capital_initial"])) * Decimal(str(
                cfg.get("capital_per_coin_pct", 100))) / Decimal(100)
            if capital < Decimal("5.5") or capital > coin_cap:
                return {"allowed": False, "reason": f"capital {capital} fuera del mínimo/tope por moneda {coin_cap}"}
            n_levels = 1 if kind == "individual_bot_grid" else int(piece["n_levels"])
            if kind == "individual_bot_grid":
                if strategy != "simple":
                    return {"allowed": False, "reason": "el bot individual requiere estrategia Simple"}
                lines = compute_lines(low, high, 1, filters)
                buy, sell = lines[0], lines[1]
                if (sell - buy) / buy < Decimal(str(getattr(self.settings, "grid_min_step_pct", .003))):
                    return {"allowed": False, "reason": "margen por debajo del paso mínimo"}
                qty = filters.round_qty_down(capital / buy)
                sell_qty = filters.round_qty_down(qty * Decimal("0.999"))
                if qty < filters.min_qty or qty * buy < filters.min_notional:
                    return {"allowed": False, "reason": "compra bajo cantidad o notional mínimo"}
                if sell_qty < filters.min_qty or sell_qty * sell < filters.min_notional:
                    return {"allowed": False, "reason": "venta neta bajo cantidad o notional mínimo"}
                if (sell / buy - 1) * 100 - Decimal("0.20") < Decimal(str(cfg.get("min_margin_pct", .3))):
                    return {"allowed": False, "reason": "margen neto bajo el mínimo del perfil"}
                plans = []
                buy_plans = [type("MetaBotBuy", (), {"capital": capital})()]
                sell_prices = [sell]
            else:
                if strategy not in {"simple", "smart"}:
                    return {"allowed": False, "reason": "estrategia no permitida"}
                lines = compute_lines(low, high, n_levels, filters)
                reserve = Decimal(0)
                if strategy == "smart":
                    from grid.meta import smart_grid_params_for_goal
                    params = validate_params(smart_grid_params_for_goal(goal), n_levels)
                    reserve = capital * Decimal(str(params.get("reserve_pct", 0))) / 100
                plans = plan_cells(lines, capital - reserve, snapshot, filters, self.settings)
                buy_plans = [row for row in plans if row.initial_state == "BUY_OPEN"]
                sell_prices = [row.sell_price for row in plans]
            exchange = self.engine.exchange
            open_orders = exchange.get_open_orders(symbol)
            replaced_prefix = f"g{int(replacing_grid_id)}L" if replacing_grid_id is not None else None
            released_orders = [order for order in open_orders if replaced_prefix and
                str(order.get("clientOrderId") or order.get("origClientOrderId") or "").startswith(replaced_prefix)]
            existing_levels = []
            for status in ("OPENING", "ACTIVE", "PAUSED", "CLOSING", "HOLDING"):
                for active in self.db.list_grids_by_status({status}):
                    active_id = int(active["id"])
                    if active_id == int(replacing_grid_id or -1):
                        continue
                    if active.get("symbol") == symbol:
                        existing_levels.extend(self.db.get_grid_levels(active_id))
            current_order_count = max(0, len(open_orders) - len(released_orders))
            if filters.max_num_orders is not None and current_order_count + len(buy_plans) > filters.max_num_orders:
                return {"allowed": False, "reason": "se alcanzaría MAX_NUM_ORDERS"}
            max_grids = int(getattr(self.settings, "max_grids_simultaneos", 5))
            open_grid_count = int(self.db.count_open_grids())
            if open_grid_count - (1 if replacing_grid_id is not None else 0) >= max_grids:
                return {"allowed": False, "reason": "se alcanzaría el máximo de grids simultáneos"}
            conflicts = sell_level_conflicts(sell_prices, existing_levels, filters.tick_size,
                getattr(self.settings, "same_coin_sell_tolerance_pct", .05))
            if conflicts:
                return {"allowed": False, "reason": "conflicto con niveles de venta vivos"}
            balance = exchange.get_balance("USDT").get("USDT", {"free": 0})
            free = Decimal(str(balance.get("free", 0)))
            releasable = Decimal(0)
            if replacing_grid_id is not None:
                current = self.db.get_grid(int(replacing_grid_id)) or {}
                prefix = f"g{int(replacing_grid_id)}L"
                current_orders = [order for order in exchange.get_open_orders(current.get("symbol", symbol))
                    if str(order.get("clientOrderId") or order.get("origClientOrderId") or "").startswith(prefix)]
                for order in current_orders:
                    if str(order.get("side", "")).upper() == "BUY":
                        releasable += Decimal(str(order.get("price") or 0)) * Decimal(str(order.get("origQty") or 0))
                held_qty = sum((Decimal(str(level.get("held_qty") or 0))
                    for level in self.db.get_grid_levels(int(replacing_grid_id))), Decimal(0))
                if held_qty > 0 and include_held_proceeds:
                    old_book = exchange.get_book_ticker(current.get("symbol", symbol))
                    old_bid = Decimal(str(old_book.get("bid_price") or 0))
                    if old_bid <= 0:
                        return {"allowed": False, "reason": "no se puede estimar el efectivo Testnet de la venta actual"}
                    releasable += held_qty * old_bid * Decimal("0.999")
            required = sum((row.capital for row in buy_plans), Decimal(0))
            if free + releasable < required:
                return {"allowed": False, "reason": f"USDT libre tras liberar la pieza {free + releasable} < requerido {required}"}
            return {"allowed": True, "reason": None, "bid": str(snapshot["bid_price"]),
                    "ask": str(snapshot["ask_price"]), "mid": str(mid),
                    "required_usdt": str(required)}
        except Exception as exc:
            return {"allowed": False, "reason": f"prevalidación Testnet: {type(exc).__name__}: {exc}"}

    def _testnet_book_status(self, symbol):
        try:
            book = self.engine.exchange.get_book_ticker(symbol)
            if not book_is_two_sided(book):
                return {"state": "one_sided", "reason": (f"libro de Testnet unilateral: bid "
                    f"{book.get('bid_price')}, ask {book.get('ask_price')}")}
            return {"state": "two_sided", "reason": None}
        except Exception as exc:
            return {"state": "unavailable", "reason": f"libro de Testnet no disponible: {type(exc).__name__}"}

    def _candidate_alternatives(self, row, capital, target_usdt, cfg):
        self._analysis_timing.setdefault("advisor_cache_hits", 0)
        symbol = row["symbol"]
        book = self._book_ticker(symbol)
        price = (float(book["bid_price"]) + float(book["ask_price"])) / 2
        row = {**row, "book": book}
        margins = [float(cfg.get("min_margin_pct", .3)) + extra for extra in (0, .2, .4)]
        advisor_rows = []
        advisor_capital = float(cfg.get("_advisor_reference_capital", capital))
        if self.advisor is not None:
            for margin in margins:
                key = (self.clock() // 86400, symbol, round(advisor_capital, 4), round(float(margin), 4),
                       int(cfg.get("max_days", 20)))
                in_process = self._advisor_cache.get(key)
                reanchored = self._reanchor_advisor_cache(in_process, price) if in_process is not None else None
                if reanchored is not None:
                    self._analysis_timing["advisor_cache_hits"] += 1
                    self._analysis_timing["advisor_process_cache_hits"] = self._analysis_timing.get("advisor_process_cache_hits", 0) + 1
                    advisor_rows.append({"margin_target_pct": margin, "recommendation": reanchored, "cached": True})
                    continue
                cached_advisor = self._persistent_cache_get("advisor", key)
                reanchored = self._reanchor_advisor_cache(cached_advisor, price) if cached_advisor is not None else None
                if reanchored is not None:
                    self._analysis_timing["advisor_cache_hits"] += 1
                    self._analysis_timing["advisor_persistent_cache_hits"] = self._analysis_timing.get("advisor_persistent_cache_hits", 0) + 1
                    self._advisor_cache[key] = cached_advisor
                    advisor_rows.append({"margin_target_pct": margin, "recommendation": reanchored, "cached": True})
                    continue
                try:
                    timer = time.perf_counter()
                    advisory = self.advisor(symbol, advisor_capital, margin, int(cfg.get("max_days", 20)))
                    self._analysis_timing["advisor_s"] += time.perf_counter() - timer
                    stored = self._store_advisor_cache_entry(advisory, price)
                    self._advisor_cache[key] = stored
                    self._persistent_cache_set("advisor", key, stored)
                    advisor_rows.append({"margin_target_pct": margin, "recommendation": advisory})
                except Exception as exc:
                    advisor_rows.append({"margin_target_pct": margin, "error": str(exc)})
        else:
            structure = row.get("suggested_structure") or {}
            advisor_rows.append({"margin_target_pct": float(cfg.get("min_margin_pct", .3)),
                "recommendation": {"recommended_floor": structure.get("range_low"),
                    "recommended_ceiling": structure.get("range_high"),
                    "suggested_grids": structure.get("n_levels", 4),
                    "suggested_strategy": "simple", "net_per_cycle_after_dust_usdt":
                    structure.get("net_edge_pct_per_cycle", 0)}})
        alternatives = []
        for advisor_row in advisor_rows:
            advisory = advisor_row.get("recommendation") or {}
            low, high = advisory.get("recommended_floor"), advisory.get("recommended_ceiling")
            if low is None or high is None or not float(low) < price < float(high):
                continue
            levels = min(int(advisory.get("suggested_grids") or 4), int(float(capital) // 5.5))
            planned = levels if levels >= 4 else 1
            strategies = ("simple", "smart") if planned >= 4 else ("individual_bot",)
            ranges = [(low, high, None)]
            if planned == 1:
                # Para bots se comparan compras más cercanas y lejanas al mercado,
                # conservando el margen neto recomendado por el Advisor.
                minimum = float(cfg.get("min_margin_pct", .3))
                target_margin = max(minimum, float(advisor_row["margin_target_pct"]))
                ranges = []
                for buy_distance_pct in (.5, 1.5, 3.0):
                    buy_x = price * (1 - buy_distance_pct / 100)
                    sell_y = buy_x * (1 + (target_margin + .21) / 100)
                    ranges.append((buy_x, sell_y, buy_distance_pct))
            for strategy in strategies:
                for variant_low, variant_high, buy_distance in ranges:
                    alternative = self._evaluate(row, strategy, capital, target_usdt, variant_low, variant_high,
                        planned, cfg, margin_pct=advisor_row["margin_target_pct"])
                    alternative["mid_at_plan"] = price
                    alternative["advisor"] = advisory
                    alternative["advisor_margin_target_pct"] = advisor_row["margin_target_pct"]
                    if buy_distance is not None:
                        alternative["bot_buy_distance_pct"] = buy_distance
                        alternative["bot_x"] = variant_low
                        alternative["bot_y"] = variant_high
                    for piece in alternative["pieces"]:
                        piece["mid_at_plan"] = price
                    self._apply_intelligence(alternative, row, price, low, high, cfg,
                        advisor_result=advisory if self.advisor is not None else None)
                    alternatives.append(alternative)
        return alternatives, [{"symbol": symbol, **item} for item in advisor_rows]

    def _build_plan(self, goal, *, record=True):
        from grid.meta_learning import LEARNING_SIGNALS
        timing_started = time.perf_counter()
        decision_id = uuid.uuid4().hex
        self._analysis_timing = {"advisor_s": 0.0, "replay_s": 0.0,
                                 "touch_signal_s": 0.0, "touch_signal_cache_hits": 0,
                                 "advisor_cache_hits": 0, "replay_cache_hits": 0,
                                 "advisor_process_cache_hits": 0, "advisor_persistent_cache_hits": 0,
                                 "replay_process_cache_hits": 0, "replay_persistent_cache_hits": 0}
        scan = self.scanner.scan(capital=float(goal["capital_initial"]), with_sim=False)
        scan_seconds = time.perf_counter() - timing_started
        candidates = list(scan.get("results", []))
        preparation_started = time.perf_counter()
        active_rows = self.db.get_active_coins()
        active = {str(row["symbol"]).upper() for row in active_rows}
        usable = [row for row in candidates if row.get("eligible") and row.get("symbol") in active]
        sigmas = sorted(float(row["sigma_24h_open"]) for row in usable
                        if row.get("sigma_24h_open") is not None and float(row["sigma_24h_open"]) > 0)
        profile = str(goal["profile"]).upper()
        cfg = dict(goal.get("config_snapshot") or {})
        scanner_advisor_weights = dict(cfg.get("signal_weights") or {})
        try:
            from sqlalchemy import select
            with self.db.engine.connect() as conn:
                stored = conn.execute(select(self.db.meta_profile_settings.c.settings).where(
                    self.db.meta_profile_settings.c.profile == profile)).scalar_one_or_none()
            stored = json.loads(stored or "{}")
            learned_weights, learned_sources, learned_details, learned_versions = self._effective_learned_weights(profile)
        except (AttributeError, KeyError):
            stored, learned_weights, learned_sources, learned_details, learned_versions = {}, {}, {}, {}, []
        use_learned = bool(stored.get("use_learned_weights", False))
        cfg.update({"use_learned_weights": use_learned,
            "scanner_advisor_weights": scanner_advisor_weights,
            "learned_signal_weights": learned_weights,
            "learned_signal_sources": learned_sources,
            "learned_signal_details": learned_details,
            "learned_weight_version_ids": learned_versions})
        if use_learned:
            cfg["signal_weights"] = learned_weights
        else:
            cfg["signal_weights"] = dict(scanner_advisor_weights)
            cfg["signal_weights"]["touch_buy_to_sell"] = 0.0
        sigma_cfg = cfg.get("sigma_quantiles") or [cfg.get("sigma_q33", 1/3), cfg.get("sigma_q67", 2/3)]
        q33, q67 = (np.quantile(sigmas, sigma_cfg).tolist() if len(sigmas) >= 3 else (None, None))
        prepared = []
        for row in candidates:
            symbol = row.get("symbol")
            sigma = row.get("sigma_24h_open")
            reason = None
            testnet_book = self._testnet_book_status(symbol) if symbol and row.get("eligible") else {
                "state": "not_checked", "reason": None}
            if not row.get("eligible"):
                reason = "; ".join(row.get("reasons") or [row.get("error") or "filtro duro Scanner"])
            elif str(symbol).upper() not in active:
                reason = "moneda inactiva o ausente de Coin Registry"
            elif testnet_book["state"] != "two_sided":
                reason = f"alternativa no abrible en Testnet: {testnet_book['reason']}"
            else:
                required_verifications = int(cfg.get("min_effective_verifications", 30))
                model_ready = self._model_ready(symbol, required_verifications) if profile == "PRUDENTE" else False
                reason = profile_candidate_exclusion(profile, sigma, q33, q67, model_ready,
                                                     required_verifications)
            if reason is None:
                model_statistics = self._model_snapshot(symbol)
                model_statistics["required_verifications"] = int(cfg.get("min_effective_verifications", 30))
                prepared.append({**row, "scanner_eligible": True, "book_state": "two_sided",
                                 "testnet_book_state": testnet_book["state"],
                                 "testnet_book_reason": testnet_book["reason"],
                                 "model_statistics": model_statistics, "excluded_reason": None})
            else:
                prepared.append({"symbol": symbol, "eligible": bool(row.get("eligible")),
                                 "scanner_eligible": bool(row.get("eligible")),
                                 "hard_filters": row.get("hard_filters", []),
                                 "sigma_24h": sigma, "score": row.get("score"),
                                 "book_state": "two_sided" if row.get("eligible") else "excluded",
                                 "testnet_book_state": testnet_book["state"],
                                 "testnet_book_reason": testnet_book["reason"],
                                 "model_statistics": ({**self._model_snapshot(symbol),
                                     "required_verifications": int(cfg.get("min_effective_verifications", 30))}
                                     if symbol else None),
                                 "excluded_reason": reason})
        eligible = [r for r in prepared if r.get("excluded_reason") is None]
        preparation_seconds = time.perf_counter() - preparation_started
        if not eligible:
            raise ValueError("Ninguna moneda cumple los límites del perfil.")
        capital = float(goal["capital_initial"])
        target_usdt = float(goal["target_usdt"])
        portfolio_started = time.perf_counter()
        shortlist = sorted(eligible, key=lambda item: float(item.get("score") or 0), reverse=True)[:3]
        alternatives, advisor_comparisons, evaluated_combinations = [], [], []
        profile_cap = float(cfg.get("capital_per_coin_pct", 100))
        cfg["_advisor_reference_capital"] = capital * profile_cap / 100
        option_cache = {}
        max_pieces = max(1, min(3, int(cfg.get("max_grids", 1)) + int(cfg.get("max_bots", 0))))
        for piece_count in range(1, max_pieces + 1):
            target_piece = target_usdt / piece_count
            for selected_rows in itertools.combinations(shortlist, piece_count):
                symbols = [row["symbol"] for row in selected_rows]
                for shares in allocation_vectors(piece_count, profile_cap):
                    combination = []
                    for row, share in zip(selected_rows, shares):
                        cache_key = (row["symbol"], share, piece_count)
                        if cache_key not in option_cache:
                            options, comparisons = self._candidate_alternatives(row,
                                capital * share / 100, target_piece, cfg)
                            valid_options = []
                            testnet_reasons = []
                            for option in options:
                                piece_check = self._testnet_openability(option["pieces"][0], goal)
                                option["testnet_openability"] = piece_check
                                if piece_check["allowed"]:
                                    valid_options.append(option)
                                else:
                                    testnet_reasons.append(piece_check["reason"])
                            if not valid_options:
                                row["excluded_reason"] = ("alternativa no abrible en Testnet: " +
                                    (testnet_reasons[0] if testnet_reasons else "sin estructuras válidas"))
                                row["testnet_openability"] = testnet_reasons
                            option_cache[cache_key] = rank_alternatives(valid_options)
                            advisor_comparisons.extend(comparisons)
                        options = option_cache[cache_key]
                        if not options:
                            combination = []
                            break
                        combination.append(options[0])
                    if len(combination) != piece_count:
                        continue
                    grid_count = sum(item["pieces"][0]["piece_type"] == "grid" for item in combination)
                    bot_count = piece_count - grid_count
                    if grid_count > int(cfg.get("max_grids", 0)) or bot_count > int(cfg.get("max_bots", 0)):
                        continue
                    estimate, combined_rows = combine_aligned_replays(combination, target_usdt,
                        int(cfg.get("max_days", 20)))
                    if not combined_rows:
                        continue
                    signals = []
                    for name in LEARNING_SIGNALS:
                        values = [(float(item["pieces"][0]["capital_usdt"]), signal)
                            for item in combination for signal in item.get("signals_used", [])
                            if signal["name"] == name]
                        if values:
                            denominator = sum(amount for amount, _ in values)
                            signals.append({"name": name, "normalized": sum(amount * signal["normalized"]
                                for amount, signal in values) / denominator,
                                "value_by_piece": [signal.get("value") for _, signal in values]})
                    replay_probability = estimate["probability"]
                    cap = float(cfg.get("signal_logit_cap", 1.0))
                    probability = (signal_adjusted_probability(replay_probability, signals,
                        cfg.get("signal_weights") or {}, cap) if estimate["evidence_sufficient"]
                        else replay_probability)
                    estimate.update({"probability_replay": replay_probability,
                        "probability_adjusted": probability, "probability": probability,
                        "signal_logit_adjustment_cap": cap, "low_likelihood": probability < .25})
                    shadow_signals = [signal for signal in signals if signal["name"] in {"scanner", "advisor"}]
                    shadow = (signal_adjusted_probability(replay_probability, shadow_signals,
                        cfg.get("scanner_advisor_weights") or {}, cap) if estimate["evidence_sufficient"]
                        else replay_probability)
                    learned_probability = (signal_adjusted_probability(replay_probability, signals,
                        cfg.get("learned_signal_weights") or {}, cap) if estimate["evidence_sufficient"]
                        else replay_probability)
                    pieces = [dict(item["pieces"][0]) for item in combination]
                    piece_estimates = [{"symbol": item["symbol"], "strategy": item["strategy"],
                        "skipped_windows": item["estimate"].get("skipped_windows", 0),
                        "skipped_reason": item["estimate"].get("skipped_reason"),
                        "loss_cut_windows": item["estimate"].get("loss_cut_windows", 0),
                        "bot_windows_bought": item["estimate"].get("bot_windows_bought"),
                        "bot_windows_sold": item["estimate"].get("bot_windows_sold"),
                        "rolling_windows": item["estimate"].get("rolling_windows", 0)}
                        for item in combination]
                    # combine_aligned_replays sólo alinea ventanas compartidas por fecha y no
                    # sabe de "loss_cut"; el total de cartera se toma de la suma por pieza para
                    # que coincida con lo que ve el medidor (piece_estimates_summary).
                    estimate["loss_cut_windows"] = sum(item["loss_cut_windows"] for item in piece_estimates)
                    row = {"symbol": pieces[0]["symbol"], "symbols": symbols,
                        "strategy": ",".join(item["strategy"] for item in combination),
                        "pieces": pieces, "estimate": estimate, "piece_estimates": piece_estimates,
                        "replay_probability": replay_probability,
                        "learned_probability": learned_probability,
                        "window_results": combined_rows, "signals_used": signals,
                        "signals_omitted": [entry for item in combination for entry in item.get("signals_omitted", [])],
                        "shadow_probability": shadow, "shadow_signals": shadow_signals,
                        "advisor_recommendations": [item.get("advisor") for item in combination],
                        "reason": f"cartera de {piece_count} pieza(s), reparto {shares} %"}
                    alternatives.append(row)
                    evaluated_combinations.append({"symbols": symbols, "shares_pct": shares,
                        "piece_types": [piece["piece_type"] for piece in pieces],
                        "capital_used_usdt": round(sum(float(piece["capital_usdt"]) for piece in pieces), 4),
                        "probability_replay": replay_probability, "probability_adjusted": probability,
                        "median_progress_pct": estimate.get("median_progress_pct"),
                        "days_median": estimate["days_median"], "days_p10": estimate["days_p10"],
                        "days_p90": estimate["days_p90"],
                        "independent_windows": estimate["independent_windows"],
                        "worst_drawdown_pct": estimate["worst_drawdown_pct"],
                        "worst_loss_vs_capital_pct": estimate["worst_loss_vs_capital_pct"]})
        if not alternatives:
            raise ValueError("No hay estructuras del Advisor para comparar.")
        if use_learned:
            alternatives = rank_alternatives(alternatives, probability_key="learned_probability")
            for row in alternatives:
                row["estimate"]["probability"] = row.get("learned_probability", row["replay_probability"])
                row["estimate"]["probability_adjusted"] = row["estimate"]["probability"]
        else:
            alternatives = rank_alternatives(alternatives)
        shadow = rank_alternatives(alternatives, probability_key="shadow_probability")[0]
        learned_choice = rank_alternatives(alternatives, probability_key="learned_probability")[0]
        best = alternatives[0]
        pieces = best["pieces"]
        capital_used = sum(float(piece["capital_usdt"]) for piece in pieces)
        unused_capital = max(0.0, capital - capital_used)
        more_capital = next((item for item in alternatives
            if sum(float(piece["capital_usdt"]) for piece in item["pieces"]) > capital_used + 1e-6), None)
        all_replays_zero = all(float(item["estimate"].get("probability_replay", 0)) == 0
                               for item in alternatives)
        closest_progress = max((float(item["estimate"].get("median_progress_pct") or 0)
                                for item in alternatives), default=0.0)
        if all_replays_zero:
            selection_reason = (f"En el historial, ninguna alternativa alcanzó la meta en el plazo; "
                f"esta es la que más se acercó ({closest_progress:.1f}% de la meta en mediana). Desempate: "
                "mayor avance mediano, menor mediana de días y menor peor drawdown.")
        else:
            selection_reason = ("Mayor probabilidad histórica de alcanzar la meta; desempate por "
                "avance mediano, menor mediana de días y menor peor drawdown.")
        capital_reason = None
        if unused_capital > 1e-6:
            if more_capital is None:
                capital_reason = (f"Quedan {unused_capital:.2f} USDT libres: no hubo otra combinación "
                    "viable dentro de los límites de perfil, moneda y notional mínimo.")
            else:
                other_used = sum(float(piece["capital_usdt"]) for piece in more_capital["pieces"])
                capital_reason = (f"Quedan {unused_capital:.2f} USDT libres. Se evaluó una combinación "
                    f"de {other_used:.2f} USDT; su probabilidad histórica fue "
                    f"{100*float(more_capital['estimate'].get('probability_replay', 0)):.1f}% y su "
                    f"avance mediano {float(more_capital['estimate'].get('median_progress_pct') or 0):.1f}% "
                    "del objetivo. La selección sigue el criterio común, no el uso de capital por sí solo.")
        control_started = time.perf_counter()
        control_plan = self._control_plan(best, capital)
        if self.advisor is not None:
            try:
                control_advisor = self.advisor(pieces[0]["symbol"], capital,
                    float(cfg.get("min_margin_pct", .3)), int(cfg.get("max_days", 20)))
                control_plan.update({"advisor_recommendation": control_advisor,
                    "range_low": control_advisor.get("recommended_floor"),
                    "range_high": control_advisor.get("recommended_ceiling"),
                    "n_levels": control_advisor.get("suggested_grids", 4),
                    "smart_comparison": (control_advisor.get("simulations", {}).get("strategies", {}).get("smart"))})
            except Exception as exc:
                control_plan["warning"] = f"Advisor de control no disponible: {exc}"
        control_seconds = time.perf_counter() - control_started
        for index, item in enumerate(alternatives):
            item["alternative_id"] = f"{decision_id}:{index}"
        decision_choices = {
            "comparison_version": 2,
            "real": {"decision": "seleccionar_alternativa", "alternative_id": best.get("alternative_id"), "symbol": best["symbol"],
                "strategy": best["strategy"], "probability": best["estimate"].get("probability")},
            "scanner_advisor": {"decision": "seleccionar_alternativa", "alternative_id": shadow.get("alternative_id"), "symbol": shadow["symbol"],
                "strategy": shadow["strategy"], "probability": shadow.get("shadow_probability")},
            "learned": {"decision": "seleccionar_alternativa", "alternative_id": learned_choice.get("alternative_id"),
                "symbol": learned_choice["symbol"], "strategy": learned_choice["strategy"],
                "probability": learned_choice.get("learned_probability")}}
        selected_public = []
        for item in alternatives:
            if len(item.get("pieces", [])) == 1 or len(selected_public) < 30:
                selected_public.append(item)
        unique_comparisons = {}
        for item in advisor_comparisons:
            unique_comparisons.setdefault((item.get("symbol"), item.get("margin_target_pct")), item)
        advisor_comparisons = list(unique_comparisons.values())
        public_alternatives = [{key: value for key, value in item.items()
                                if key not in {"window_results", "_touch_observations"}}
                               for item in selected_public]
        plan = {"profile": profile, "decision_id": decision_id,
                "candidates": prepared, "sigma_bands": {"q33": q33, "q67": q67},
                "alternatives": public_alternatives, "combinations_evaluated": evaluated_combinations,
                "pieces": pieces, "estimated_time": best["estimate"],
                "piece_estimates": best.get("piece_estimates", []),
                "advisor_comparisons": advisor_comparisons,
                "timings_s": {"scanner": round(scan_seconds, 3), "candidate_filter": round(preparation_seconds, 3),
                    "advisor": round(self._analysis_timing["advisor_s"], 3),
                    "advisor_cache_hits": self._analysis_timing["advisor_cache_hits"],
                    "advisor_process_cache_hits": self._analysis_timing.get("advisor_process_cache_hits", 0),
                    "advisor_persistent_cache_hits": self._analysis_timing.get("advisor_persistent_cache_hits", 0),
                    "replays": round(self._analysis_timing["replay_s"], 3),
                    "replay_cache_hits": self._analysis_timing["replay_cache_hits"],
                    "touch_signal": round(self._analysis_timing["touch_signal_s"], 3),
                    "touch_signal_cache_hits": self._analysis_timing["touch_signal_cache_hits"],
                    "replay_process_cache_hits": self._analysis_timing.get("replay_process_cache_hits", 0),
                    "replay_persistent_cache_hits": self._analysis_timing.get("replay_persistent_cache_hits", 0),
                    "portfolio_selection": round(max(0.0, time.perf_counter() - portfolio_started - control_seconds
                        - self._analysis_timing["advisor_s"] - self._analysis_timing["replay_s"]
                        - self._analysis_timing["touch_signal_s"]), 3),
                    "control_advisor": round(control_seconds, 3)},
                "warnings": (["El replay no modela los préstamos de grids Smart; la estimación no incluye capital prestado."]
                    if any(piece["strategy"] == "smart" for piece in pieces) else []),
                "target_usdt": float(goal["target_usdt"]), "loss_max_usdt": capital * float(cfg.get("max_loss_pct", 0)) / 100,
                "deadline_days": int(cfg.get("max_days", 0)),
                "selection_reason": selection_reason,
                "capital_usage": {"initial_capital_usdt": capital, "used_usdt": capital_used,
                    "unused_usdt": unused_capital, "reason": capital_reason,
                    "best_higher_capital_alternative": None if more_capital is None else {
                        "symbols": more_capital["symbols"], "capital_used_usdt": sum(
                            float(piece["capital_usdt"]) for piece in more_capital["pieces"]),
                        "probability_replay": more_capital["estimate"].get("probability_replay"),
                        "median_progress_pct": more_capital["estimate"].get("median_progress_pct")}},
                "signals_used": best.get("signals_used", []),
                "signals_omitted": best.get("signals_omitted", []),
                "historical_evidence_note": best["estimate"].get("evidence_warning"),
                "shadow_decision": {"symbol": shadow["symbol"], "strategy": shadow["strategy"],
                    "probability": shadow["shadow_probability"], "signals": shadow["shadow_signals"]},
                "learning": {"enabled": use_learned, "weights": cfg.get("learned_signal_weights", {}),
                    "sources": cfg.get("learned_signal_sources", {}),
                    "version_ids": cfg.get("learned_weight_version_ids", [])},
                "decision_choices": decision_choices,
                "control_plan": control_plan,
                "probability_under_25_pct": best["estimate"]["low_likelihood"]}
        if record:
            self._ledger(goal["id"], "META_PLAN_EVALUATED", {"decision_id": decision_id,
                          "alternatives": public_alternatives,
                          "decision_choices": decision_choices,
                          "advisor_comparisons": advisor_comparisons,
                          "combinations_evaluated": evaluated_combinations,
                          "shadow_decision": plan["shadow_decision"],
                          "selected": {key: value for key, value in best.items()
                                       if key not in {"window_results", "_touch_observations"}},
                          "sigma_bands": plan["sigma_bands"]})
            self._persist_touch_observations(int(goal["id"]), decision_id, alternatives)
            self._persist_learning_samples(goal, decision_id, alternatives)
        return plan

    def _persist_touch_observations(self, meta_id, decision_id, alternatives):
        table = getattr(self.db, "meta_signal_observations", None)
        if table is None:
            return
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        values = []
        for alternative in alternatives:
            piece = (alternative.get("pieces") or [{}])[0]
            alt_id = alternative.get("alternative_id")
            for observation in alternative.get("_touch_observations", []):
                values.append({"meta_id": int(meta_id), "decision_id": str(decision_id),
                    "alternative_id": str(alt_id), "symbol": str(piece.get("symbol") or ""),
                    "piece_type": str(piece.get("piece_type") or "grid"),
                    "cell_index": int(observation["cell_index"]),
                    "window_start": str(observation["window_start"]),
                    "horizon": str(observation["horizon"]),
                    "buy_relative": float(observation["buy_relative"]),
                    "sell_relative": float(observation["sell_relative"]),
                    "stats_json": json.dumps(observation["stats"], sort_keys=True),
                    "created_at": now})
        if values:
            with self.db.engine.begin() as conn:
                conn.execute(table.insert(), values)

    @staticmethod
    def _learning_parameters(goal, alternative):
        cfg = dict(goal.get("config_snapshot") or {})
        pieces = [{key: value for key, value in piece.items()
                   if key in {"symbol", "strategy", "piece_type", "capital_usdt", "n_levels",
                              "floor", "ceiling", "range_low", "range_high", "mid_at_plan",
                              "margin_pct", "target_usdt"}}
                  for piece in alternative.get("pieces", [])]
        return {"profile": str(goal.get("profile", "RAPIDO")).upper(),
            "profile_days": int(cfg.get("max_days", 20)),
            "capital_usdt": float(goal.get("capital_initial", 0)),
            "target_usdt": float(goal.get("target_usdt", 0)),
            "max_loss_pct": float(cfg.get("max_loss_pct", 0)), "pieces": pieces}

    def _store_learning_sample(self, values):
        from sqlalchemy import select
        table = self.db.meta_learning_samples
        with self.db.engine.begin() as conn:
            exists = conn.execute(select(table.c.id).where(
                table.c.sample_key == values["sample_key"])).first()
            if exists:
                return False
            conn.execute(table.insert().values(**values))
        return True

    def _persist_learning_samples(self, goal, decision_id, alternatives):
        """Congela señales y geometría puntuales, sin copiar filas de replay futuras."""
        table = getattr(self.db, "meta_learning_samples", None)
        if table is None:
            return 0
        started_at = datetime.fromtimestamp(float(self.clock()), timezone.utc)
        created = 0
        for alternative in alternatives:
            alt_id = str(alternative.get("alternative_id") or "")
            if not alt_id:
                continue
            params = self._learning_parameters(goal, alternative)
            pieces = params["pieces"]
            if not pieces:
                continue
            sample_key = hashlib.sha256(f"live:{decision_id}:{alt_id}".encode()).hexdigest()
            signals = {"signals_used": alternative.get("signals_used", []),
                "touch_signal": alternative.get("touch_signal"),
                "shadow_signals": alternative.get("shadow_signals", [])}
            replay_raw = alternative.get("replay_probability")
            replay_windows = int((alternative.get("estimate") or {}).get("rolling_windows") or
                alternative.get("independent_windows") or 0)
            replay_smoothed = (((round(float(replay_raw) * replay_windows) + 1) /
                (replay_windows + 2)) if replay_raw is not None and replay_windows else None)
            values = {"sample_key": sample_key, "meta_id": int(goal["id"]),
                "decision_id": str(decision_id), "alternative_id": alt_id,
                "symbol": str(pieces[0].get("symbol") or "").upper(),
                "profile": params["profile"], "sample_at": started_at,
                "source_kind": "live", "base_probability": replay_raw,
                "base_probability_smoothed": replay_smoothed,
                "base_window_count": replay_windows,
                "base_independent_windows": int(alternative.get("independent_windows") or 0),
                "base_method_version": "rolling_7d_v2", "recalculate_base": False,
                "training_exclusion_reason": ("sin base suficiente" if
                    int(alternative.get("independent_windows") or 0) < 3 else None),
                "parameters_json": json.dumps(params, sort_keys=True, default=str),
                "signals_json": json.dumps(signals, sort_keys=True, default=str),
                "provenance_json": json.dumps({"source": "META_PLAN_EVALUATED",
                    "decision_id": decision_id, "alternative_id": alt_id,
                    "base_replay": {"raw_probability": replay_raw,
                        "smoothed_probability": replay_smoothed, "step_days": 7,
                        "window_count": replay_windows,
                        "independent_windows": int(alternative.get("independent_windows") or 0),
                        "method_version": "rolling_7d_v2"}}, sort_keys=True),
                "created_at": started_at}
            created += int(self._store_learning_sample(values))
        return created

    def _import_legacy_learning_samples(self):
        """Importa snapshots Meta anteriores al esquema 40d, de forma idempotente."""
        from sqlalchemy import select
        table = self.db.meta_learning_samples
        with self.db.engine.connect() as conn:
            rows = conn.execute(select(self.db.meta_ledger, self.db.meta_goals).join(
                self.db.meta_goals, self.db.meta_goals.c.id == self.db.meta_ledger.c.meta_id).where(
                self.db.meta_ledger.c.event_type == "META_PLAN_EVALUATED")).mappings().all()
        created = 0
        for row in rows:
            try:
                details = json.loads(row["details"] or "{}")
                decision_id = str(details.get("decision_id") or "")
                cfg = json.loads(row["config_snapshot"] or "{}")
                for alternative in details.get("alternatives", []):
                    alt_id = str(alternative.get("alternative_id") or "")
                    params = self._learning_parameters({"id": row["meta_id"],
                        "profile": row["profile"], "capital_initial": row["capital_initial"],
                        "target_usdt": row["target_usdt"], "config_snapshot": cfg}, alternative)
                    pieces = params["pieces"]
                    if not decision_id or not alt_id or not pieces:
                        continue
                    key = hashlib.sha256(f"live:{decision_id}:{alt_id}".encode()).hexdigest()
                    ts = row["ts"]
                    ts = ts.replace(tzinfo=timezone.utc) if ts.tzinfo is None else ts
                    replay_raw = alternative.get("replay_probability")
                    replay_windows = int((alternative.get("estimate") or {}).get("rolling_windows") or
                        alternative.get("independent_windows") or 0)
                    replay_smoothed = (((round(float(replay_raw) * replay_windows) + 1) /
                        (replay_windows + 2)) if replay_raw is not None and replay_windows else None)
                    values = {"sample_key": key, "meta_id": int(row["meta_id"]),
                        "decision_id": decision_id, "alternative_id": alt_id,
                        "symbol": str(pieces[0].get("symbol") or "").upper(),
                        "profile": params["profile"], "sample_at": ts,
                        "source_kind": "live", "base_probability": replay_raw,
                        "base_probability_smoothed": replay_smoothed,
                        "base_window_count": replay_windows,
                        "base_independent_windows": int(alternative.get("independent_windows") or 0),
                        "base_method_version": "rolling_7d_v2", "recalculate_base": False,
                        "training_exclusion_reason": ("sin base suficiente" if
                            int(alternative.get("independent_windows") or 0) < 3 else None),
                        "parameters_json": json.dumps(params, sort_keys=True, default=str),
                        "signals_json": json.dumps({"signals_used": alternative.get("signals_used", []),
                            "touch_signal": alternative.get("touch_signal"),
                            "shadow_signals": alternative.get("shadow_signals", [])}, sort_keys=True, default=str),
                        "provenance_json": json.dumps({"source": "legacy_meta_ledger",
                            "ledger_id": row["id"], "decision_id": decision_id,
                            "alternative_id": alt_id, "base_replay": {
                                "raw_probability": replay_raw, "smoothed_probability": replay_smoothed,
                                "step_days": 7, "window_count": replay_windows,
                                "independent_windows": int(alternative.get("independent_windows") or 0),
                                "method_version": "rolling_7d_v2"}}, sort_keys=True),
                        "created_at": datetime.now(timezone.utc)}
                    created += int(self._store_learning_sample(values))
            except (TypeError, ValueError, KeyError):
                continue
        return created

    def _learning_candles(self, symbol):
        import pandas as pd
        from grid.sim.data import CandleData
        raw = self.scanner.client.get_historical_klines(symbol, "5m", 180)
        frame = raw if isinstance(raw, pd.DataFrame) else pd.DataFrame(raw)
        if frame.empty:
            return None
        timestamps = pd.to_datetime(frame["timestamp"], utc=True).astype("int64").to_numpy() // 1_000_000_000
        return CandleData(timestamp=timestamps.astype("int64"),
            open=frame["open"].to_numpy(float), high=frame["high"].to_numpy(float),
            low=frame["low"].to_numpy(float), close=frame["close"].to_numpy(float), gaps=0)

    @staticmethod
    def _candle_inventory(candles):
        if candles is None or len(candles.timestamp) == 0:
            return None
        stamps = sorted(int(value) for value in candles.timestamp)
        deltas = [right - left for left, right in zip(stamps, stamps[1:]) if right > left]
        interval = int(statistics.median(deltas)) if deltas else None
        gaps = sum(1 for delta in deltas if interval and delta > interval * 1.5)
        iso = lambda value: datetime.fromtimestamp(value, timezone.utc).isoformat()
        return {"count": len(stamps), "first_at": iso(stamps[0]), "last_at": iso(stamps[-1]),
            "interval_seconds": interval, "gaps": gaps}

    def _learning_history_rows(self, symbol):
        from sqlalchemy import select
        with self.db.engine.connect() as conn:
            forecasts = conn.execute(select(self.db.vol_forecasts).where(
                self.db.vol_forecasts.c.symbol == symbol).order_by(
                    self.db.vol_forecasts.c.made_at)).mappings().all()
            predictions = conn.execute(select(self.db.predictions).where(
                self.db.predictions.c.symbol == symbol).order_by(
                    self.db.predictions.c.predicted_at)).mappings().all()
        return [dict(row) for row in forecasts], [dict(row) for row in predictions]

    def _persist_learning_labels(self, sample_id, labels, now):
        from sqlalchemy import select
        table = self.db.meta_learning_labels
        inserted = 0
        with self.db.engine.begin() as conn:
            for horizon, label in labels.items():
                if label.get("status") != "labeled":
                    continue
                exists = conn.execute(select(table.c.id).where(table.c.sample_id == int(sample_id),
                    table.c.horizon == str(horizon))).first()
                if exists:
                    continue
                payload = dict(label)
                payload.pop("status", None)
                conn.execute(table.insert().values(sample_id=int(sample_id), horizon=str(horizon),
                    label_json=json.dumps(payload, sort_keys=True), labeled_at=now))
                inserted += 1
        return inserted

    def _recalculate_learning_bases(self, samples, candles_by_symbol):
        """Actualiza bases históricas obsoletas sin borrar ni reemplazar muestras."""
        from grid.meta_learning import causal_replay_probability, REPLAY_METHOD_VERSION
        changed = 0
        replay_windows_new = 0
        for sample in samples:
            if sample.get("source_kind") != "historical_reconstructed":
                continue
            if not sample.get("recalculate_base") and sample.get("base_method_version") == REPLAY_METHOD_VERSION:
                continue
            params = json.loads(sample["parameters_json"])
            symbols = {str(piece.get("symbol") or "").upper() for piece in params.get("pieces", [])}
            market = {symbol: candles_by_symbol.get(symbol) for symbol in symbols}
            if not market or any(value is None for value in market.values()):
                continue
            at = sample["sample_at"]
            baseline = causal_replay_probability(market, at=at, parameters=params)
            replay_windows_new += int(baseline.get("simulated_windows") or 0)
            provenance = json.loads(sample["provenance_json"] or "{}")
            provenance["base_replay"] = baseline
            provenance["base_replay_method_version"] = REPLAY_METHOD_VERSION
            exclusion = baseline.get("training_exclusion_reason")
            with self.db.engine.begin() as conn:
                conn.execute(self.db.meta_learning_samples.update().where(
                    self.db.meta_learning_samples.c.id == int(sample["id"])).values(
                        base_probability=baseline.get("raw_probability"),
                        base_probability_smoothed=baseline.get("smoothed_probability"),
                        base_window_count=int(baseline.get("window_count") or 0),
                        base_independent_windows=int(baseline.get("independent_windows") or 0),
                        base_method_version=REPLAY_METHOD_VERSION, recalculate_base=False,
                        training_exclusion_reason=exclusion,
                        provenance_json=json.dumps(provenance, sort_keys=True)))
            changed += 1
        self._last_base_recalculation_windows = replay_windows_new
        return changed

    def _train_learning_weights(self, samples):
        """Ajusta versiones en sombra; no modifica pesos usados por el operador."""
        from sqlalchemy import select
        from grid.meta_learning import (effective_sample_rows, extract_learning_features,
            fit_signal_weights, temporal_learning_split, LEARNING_SIGNALS)
        with self.db.engine.connect() as conn:
            label_rows = conn.execute(select(self.db.meta_learning_labels)).mappings().all()
            setting_rows = conn.execute(select(self.db.meta_profile_settings)).mappings().all()
            previous = conn.execute(select(self.db.meta_learning_weight_versions).order_by(
                self.db.meta_learning_weight_versions.c.created_at)).mappings().all()
        labels = {(int(row["sample_id"]), str(row["horizon"])): json.loads(row["label_json"])
                  for row in label_rows}
        settings = {str(row["profile"]): json.loads(row["settings"] or "{}") for row in setting_rows}
        latest = {}
        for row in previous:
            latest[(row["profile"], row["source_kind"])] = json.loads(row["weights_json"] or "{}")
        saved = 0
        for profile in {str(row["profile"]).upper() for row in samples}:
            cfg = settings.get(profile, {})
            for source_kind in ("live", "historical_reconstructed"):
                rows = []
                for sample in samples:
                    if str(sample["profile"]).upper() != profile or sample["source_kind"] != source_kind:
                        continue
                    params = json.loads(sample["parameters_json"])
                    days = int(params.get("profile_days", 20))
                    row = dict(sample)
                    row["signals"] = json.loads(sample["signals_json"] or "{}")
                    weights = {}
                    for piece in params.get("pieces", []):
                        symbol = str(piece.get("symbol") or "").upper()
                        weights[symbol] = weights.get(symbol, 0.0) + float(piece.get("capital_usdt", 0))
                    row["feature_weights"] = weights
                    row["base_window_count"] = int(sample.get("base_window_count") or 0)
                    row["base_independent_windows"] = int(sample.get("base_independent_windows") or 0)
                    row["base_probability_smoothed"] = sample.get("base_probability_smoothed")
                    row["label"] = labels.get((int(sample["id"]), "72h"))
                    row["profile_label"] = labels.get((int(sample["id"]), f"{days}d"))
                    rows.append(row)
                training_rows, validation_rows = temporal_learning_split(rows)
                split_at = min((row["sample_at"] for row in validation_rows), default=None)
                training = [row for row in training_rows if row.get("label")]
                validation = [row for row in validation_rows if row.get("profile_label")]
                days = int(next((json.loads(row["parameters_json"]).get("profile_days", 20)
                    for row in rows), 20))
                training = effective_sample_rows(training, horizon_days=3)
                validation = effective_sample_rows(validation, horizon_days=days)
                from grid.meta_learning import DEFAULT_SIGNAL_WEIGHTS
                current = {key: float((latest.get((profile, source_kind)) or {}).get(
                    key, DEFAULT_SIGNAL_WEIGHTS[key]))
                    for key in LEARNING_SIGNALS}
                if (len(training) < int(cfg.get("learning_min_effective_samples", 30))
                        or len(validation) < 5):
                    result = {"status": "insufficient", "weights": current,
                        "signals": {key: {"weight": current[key], "reason": "sin evidencia suficiente",
                            "effective_samples": sum(1 for row in training if key in
                                extract_learning_features(row["signals"], row.get("feature_weights"))[1])}
                            for key in LEARNING_SIGNALS},
                        "reason": "sin entrenamiento y validación temporal suficientes",
                        "training_effective": len(training), "validation_effective": len(validation)}
                else:
                    result = fit_signal_weights(training, current,
                        min_effective=int(cfg.get("learning_min_effective_samples", 30)),
                        max_delta=float(cfg.get("learning_max_weight_delta", .05)),
                        max_abs_weight=float(cfg.get("learning_weight_max_abs", 1.0)),
                        logit_cap=float(cfg.get("signal_logit_cap", 1.0)),
                        regularization=float(cfg.get("learning_regularization", 1.0)),
                        alpha=float(cfg.get("learning_alpha", .05)),
                        validation_rows=[{**row, "label": row["profile_label"]} for row in validation])
                    result["training_effective"] = len(training)
                    result["validation_effective"] = len(validation)
                result["training_cutoff"] = str(split_at) if split_at is not None else None
                result["training_sample_ids"] = [int(row["id"]) for row in training]
                result["validation_sample_ids"] = [int(row["id"]) for row in validation]
                result["calibration_deciles"] = validation_calibration(
                    [{**row, "label": row.get("profile_label")} for row in validation],
                    result.get("weights", current), logit_cap=float(cfg.get("signal_logit_cap", 1.0)))
                # Registrar intentos y frenos por separado para cada origen.
                with self.db.engine.begin() as conn:
                    conn.execute(self.db.meta_learning_weight_versions.insert().values(
                        created_at=datetime.now(timezone.utc), profile=profile,
                        source_kind=source_kind, status=("updated" if result.get("improves") else
                            ("unchanged" if result.get("status") == "fitted" else result["status"])),
                        weights_json=json.dumps(result.get("weights", current), sort_keys=True),
                        summary_json=json.dumps(result, sort_keys=True, default=str)))
                saved += 1
        return saved

    def run_learning_daily(self):
        """Etiqueta en prioridad baja sin tomar ops_lock ni ejecutar órdenes."""
        from sqlalchemy import select
        if not bool(getattr(self.settings, "meta_mode_enabled", False)):
            return {"status": "disabled", "reason": "Modo Meta apagado"}
        if not self._learning_guard.acquire(blocking=False):
            return {"status": "skipped", "reason": "aprendizaje anterior sigue activo"}
        from grid.meta_learning import (replay_alternative_labels, reconstruct_historical_samples,
            stable_template)
        started = datetime.now(timezone.utc)
        run_monotonic = time.monotonic()
        timings = {"legacy_import_s": 0.0, "market_history_s": 0.0,
                   "historical_reconstruction_s": 0.0,
                   "label_simulation_s": 0.0, "persistence_s": 0.0,
                   "weight_training_s": 0.0, "yield_s": 0.0}
        counters = {"legacy_samples_imported": 0, "historical_samples_created": 0, "labels_72h": 0,
                    "labels_profile": 0, "samples_immature": 0, "symbols_failed": 0,
                    "replay_windows_new": 0, "replay_windows_cached": 0, "cuts_processed": 0}
        priority_lowered = False
        run_id = None
        try:
            try:
                if hasattr(os, "setpriority") and hasattr(os, "PRIO_PROCESS"):
                    worker_id = threading.get_native_id()
                    current = os.getpriority(os.PRIO_PROCESS, worker_id)
                    os.setpriority(os.PRIO_PROCESS, worker_id, min(19, current + 5))
                    priority_lowered = True
            except (AttributeError, OSError):
                pass
            with self.db.engine.begin() as conn:
                run_id = conn.execute(self.db.meta_learning_runs.insert().values(
                    started_at=started, status="RUNNING", summary_json="{}")).inserted_primary_key[0]
            t = time.perf_counter()
            counters["legacy_samples_imported"] = self._import_legacy_learning_samples()
            timings["legacy_import_s"] = time.perf_counter() - t
            with self.db.engine.connect() as conn:
                samples = conn.execute(select(self.db.meta_learning_samples)).mappings().all()
                labels = conn.execute(select(self.db.meta_learning_labels.c.sample_id,
                    self.db.meta_learning_labels.c.horizon)).all()
            existing = {(int(row[0]), str(row[1])) for row in labels}
            candles_by_symbol, sources = {}, {}
            t = time.perf_counter()
            for sample in samples:
                params = json.loads(sample["parameters_json"])
                for piece in params.get("pieces", []):
                    symbol = str(piece.get("symbol") or "").upper()
                    if symbol and symbol not in candles_by_symbol:
                        try:
                            candles_by_symbol[symbol] = self._learning_candles(symbol)
                            sources[symbol] = self._learning_history_rows(symbol)
                        except Exception:
                            counters["symbols_failed"] += 1
                            candles_by_symbol[symbol] = None
            timings["market_history_s"] = time.perf_counter() - t
            candle_inventory = {symbol: self._candle_inventory(candles)
                for symbol, candles in candles_by_symbol.items() if candles is not None}
            templates_by_id = {}
            for sample in sorted(samples, key=lambda row: row["sample_at"], reverse=True):
                if sample["source_kind"] != "live":
                    continue
                params = json.loads(sample["parameters_json"])
                template_id, canonical = stable_template(params,
                    meta_capital_usdt=params.get("capital_usdt"))
                if template_id not in templates_by_id:
                    templates_by_id[template_id] = {"template_id": template_id,
                        "sample_key": sample["sample_key"], "parameters": params,
                        "canonical": canonical, "sample_at": sample["sample_at"]}
            global_settings = {}
            previously_pending = []
            with self.db.engine.connect() as conn:
                setting = conn.execute(select(self.db.meta_profile_settings.c.settings).where(
                    self.db.meta_profile_settings.c.profile == "GLOBAL")).scalar_one_or_none()
                if setting:
                    global_settings = json.loads(setting)
                previous_run = conn.execute(select(self.db.meta_learning_runs.c.summary_json).where(
                    self.db.meta_learning_runs.c.id < int(run_id)).order_by(
                    self.db.meta_learning_runs.c.id.desc()).limit(1)).first()
                if previous_run:
                    previously_pending = json.loads(previous_run[0] or "{}").get("pending_template_ids", [])
            max_templates = max(1, int(global_settings.get("learning_max_templates_per_run", 30)))
            budget_seconds = max(1.0, float(global_settings.get("learning_time_budget_minutes", 20)) * 60)
            pending_set = set(previously_pending)
            ordered_templates = sorted(templates_by_id.values(), key=lambda row: (
                row["template_id"] not in pending_set, -row["sample_at"].timestamp()))
            selected_templates = ordered_templates[:max_templates]
            pending_ids = [row["template_id"] for row in ordered_templates[max_templates:]]
            if selected_templates:
                t = time.perf_counter()
                with self.db.engine.connect() as conn:
                    existing_cuts = {str(row[0]) for row in conn.execute(select(
                        self.db.meta_learning_samples.c.sample_key).where(
                        self.db.meta_learning_samples.c.source_kind == "historical_reconstructed"))}
                    template_ids = [row["template_id"] for row in selected_templates]
                    cached_rows = conn.execute(select(self.db.meta_learning_replay_cache.c.template_id,
                        self.db.meta_learning_replay_cache.c.window_start,
                        self.db.meta_learning_replay_cache.c.reached).where(
                        self.db.meta_learning_replay_cache.c.template_id.in_(template_ids))).all()
                replay_cache = {(str(row[0]), int(row[1])): bool(row[2]) for row in cached_rows}
                reconstruct_result = reconstruct_historical_samples(selected_templates,
                    candles_by_symbol={key: value for key, value in candles_by_symbol.items()
                                       if value is not None},
                    source_rows_by_symbol=sources, now=datetime.now(timezone.utc), lookback_days=180,
                    existing_cuts=existing_cuts, window_cache=replay_cache,
                    deadline=run_monotonic + budget_seconds, pause_callback=lambda: time.sleep(.01))
                reconstructed = reconstruct_result["samples"]
                counters["replay_windows_new"] += int(reconstruct_result.get("simulated_windows") or 0)
                counters["replay_windows_cached"] += int(reconstruct_result.get("cached_windows") or 0)
                counters["cuts_processed"] += int(reconstruct_result.get("processed_cuts") or 0)
                pending_ids.extend(reconstruct_result["pending_templates"])
                for item in reconstructed:
                    params = item["parameters"]
                    key = item["stable_sample_key"]
                    sample_at = datetime.fromtimestamp(int(item["sample_at"]), timezone.utc)
                    values = {"sample_key": key, "meta_id": None, "decision_id": None,
                        "alternative_id": None, "symbol": item["symbol"], "profile": item["profile"],
                        "sample_at": sample_at, "source_kind": "historical_reconstructed",
                        "base_probability": item.get("base_probability"),
                        "base_probability_smoothed": item.get("base_probability_smoothed"),
                        "base_window_count": int(item.get("base_window_count") or 0),
                        "base_independent_windows": int(item.get("base_independent_windows") or 0),
                        "base_method_version": item.get("base_method_version"),
                        "recalculate_base": False,
                        "training_exclusion_reason": item.get("training_exclusion_reason"),
                        "parameters_json": json.dumps(params, sort_keys=True, default=str),
                        "signals_json": json.dumps(item["signals"], sort_keys=True, default=str),
                        "provenance_json": json.dumps(item["provenance"], sort_keys=True),
                        "created_at": datetime.now(timezone.utc)}
                    counters["historical_samples_created"] += int(self._store_learning_sample(values))
                with self.db.engine.begin() as conn:
                    for (template_id, window_start), reached in replay_cache.items():
                        present = conn.execute(select(self.db.meta_learning_replay_cache.c.template_id).where(
                            self.db.meta_learning_replay_cache.c.template_id == template_id,
                            self.db.meta_learning_replay_cache.c.window_start == window_start)).first()
                        if present is None:
                            conn.execute(self.db.meta_learning_replay_cache.insert().values(
                                template_id=template_id, window_start=window_start, reached=bool(reached),
                                created_at=datetime.now(timezone.utc)))
                timings["historical_reconstruction_s"] = time.perf_counter() - t
                pending_ids = sorted(set(pending_ids))
                counters["pending_templates"] = len(pending_ids)
                with self.db.engine.connect() as conn:
                    samples = conn.execute(select(self.db.meta_learning_samples)).mappings().all()
                    labels = conn.execute(select(self.db.meta_learning_labels.c.sample_id,
                        self.db.meta_learning_labels.c.horizon)).all()
                existing = {(int(row[0]), str(row[1])) for row in labels}
            t = time.perf_counter()
            counters["bases_recalculated"] = self._recalculate_learning_bases(samples, candles_by_symbol)
            counters["replay_windows_new"] += int(getattr(self, "_last_base_recalculation_windows", 0))
            timings["base_recalculation_s"] = time.perf_counter() - t
            with self.db.engine.connect() as conn:
                samples = conn.execute(select(self.db.meta_learning_samples)).mappings().all()
            for sample in samples:
                params = json.loads(sample["parameters_json"])
                profile_horizon = f"{int(params.get('profile_days', 20))}d"
                needed = [horizon for horizon in ("72h", profile_horizon)
                          if (int(sample["id"]), horizon) not in existing]
                if not needed:
                    continue
                symbols = {str(piece.get("symbol") or "").upper() for piece in params.get("pieces", [])}
                market = {symbol: candles_by_symbol.get(symbol) for symbol in symbols}
                if any(value is None for value in market.values()):
                    counters["samples_immature"] += 1
                    continue
                start_s = int(sample["sample_at"].replace(tzinfo=timezone.utc).timestamp()
                    if sample["sample_at"].tzinfo is None else sample["sample_at"].timestamp())
                t = time.perf_counter()
                outcomes = replay_alternative_labels(market, start_ts=start_s,
                    parameters=params, include_horizons=needed)
                timings["label_simulation_s"] += time.perf_counter() - t
                fresh = {horizon: result for horizon, result in outcomes.items()
                         if (int(sample["id"]), horizon) not in existing}
                t = time.perf_counter()
                count = self._persist_learning_labels(sample["id"], fresh, datetime.now(timezone.utc))
                timings["persistence_s"] += time.perf_counter() - t
                counters["labels_72h"] += int("72h" in fresh and fresh["72h"].get("status") == "labeled")
                counters["labels_profile"] += sum(1 for horizon, label in fresh.items()
                    if horizon != "72h" and label.get("status") == "labeled")
            t = time.perf_counter()
            counters["weight_versions_saved"] = self._train_learning_weights(samples)
            timings["weight_training_s"] = time.perf_counter() - t
            learned_vs_real = {}
            for profile in ("PRUDENTE", "RAPIDO"):
                try:
                    decision_comparison = self.learning_status(profile)["decision_comparison"]
                    _weights, _sources, _details, current_version_ids = self._effective_learned_weights(profile)
                    learned_vs_real[profile] = {"metrics": decision_comparison,
                        "weight_version_ids": current_version_ids}
                    counters.setdefault("learned_worse_alerts", 0)
                    counters["learned_worse_alerts"] += int(self._alert_if_learned_worse(
                        profile, decision_comparison, current_version_ids))
                except Exception:
                    logging.getLogger(__name__).exception("No se pudo comparar decisiones aprendidas Meta")
            summary = {**counters, "pending_template_ids": pending_ids,
                "candle_inventory": candle_inventory,
                "candle_inventory_at": datetime.now(timezone.utc).isoformat(),
                "limits": {"max_templates": max_templates, "time_budget_minutes": budget_seconds / 60},
                "learned_vs_real": learned_vs_real,
                "timings_s": {key: round(value, 3) for key, value in timings.items()},
                "sample_count": len(samples), "symbols": len(candles_by_symbol),
                "priority": "baja" if priority_lowered else "baja_best_effort_no_os_thread_priority",
                "ops_lock": False}
            with self.db.engine.begin() as conn:
                conn.execute(self.db.meta_learning_runs.update().where(
                    self.db.meta_learning_runs.c.id == int(run_id)).values(
                        finished_at=datetime.now(timezone.utc), status="completed",
                        summary_json=json.dumps(summary, sort_keys=True)))
            return {"status": "completed", "run_id": run_id, **summary}
        except Exception as exc:
            summary = {**counters, "timings_s": {key: round(value, 3) for key, value in timings.items()},
                       "candle_inventory": locals().get("candle_inventory", {}),
                       "candle_inventory_at": datetime.now(timezone.utc).isoformat(),
                       "error": str(exc)}
            if run_id is not None:
                with self.db.engine.begin() as conn:
                    conn.execute(self.db.meta_learning_runs.update().where(
                        self.db.meta_learning_runs.c.id == int(run_id)).values(
                            finished_at=datetime.now(timezone.utc), status="failed",
                            summary_json=json.dumps(summary, sort_keys=True)))
            logging.getLogger(__name__).exception("Fallo en etiquetas diarias Meta")
            return {"status": "failed", "run_id": run_id, **summary}
        finally:
            self._learning_guard.release()

    def _model_snapshot(self, symbol):
        readiness = self.db.get_readiness(symbol) or {}
        query = getattr(self.db, "get_latest_vol_model_stats_forecast_at", None)
        latest = query(symbol, 24) if query else None
        stats_rows = getattr(self.db, "get_vol_model_stats_rows", lambda *_: [])(symbol, 24)
        verified_times = {str(row.get("forecast_at")) for row in stats_rows
                          if row.get("realized_logvol") is not None and row.get("verified_at") is not None}
        effective = len(verified_times) / 24
        forecasts = {}
        now = datetime.now(timezone.utc)
        for item in stats_rows:
            when = item.get("forecast_at")
            if when is None:
                continue
            when = when.replace(tzinfo=timezone.utc) if when.tzinfo is None else when
            age = (now - when).total_seconds()
            name = str(item.get("model_name"))
            if name not in forecasts or when > forecasts[name]["_when"]:
                forecasts[name] = {"sigma_24h": (math.exp(float(item["pred_logvol_cal"]))
                                   if item.get("pred_logvol_cal") is not None else None),
                                   "forecast_at": when.isoformat(), "age_seconds": age,
                                   "is_champion": bool(item.get("is_champion")), "_when": when}
        for item in forecasts.values():
            item.pop("_when", None)
            if item["age_seconds"] < 0 or item["age_seconds"] > 7200:
                item["used"] = False
                item["omission_reason"] = "forecast stale or future-dated"
            else:
                item["used"] = item.get("sigma_24h") is not None
        return {"readiness": readiness.get("state"), "effective_verifications": effective,
                "latest_forecast_at": None if latest is None else str(latest),
                "complete": bool(readiness.get("state") == "lista" and latest is not None),
                "volatility_forecasts": forecasts}

    def _model_ready(self, symbol, minimum=30):
        data = self._model_snapshot(symbol)
        try:
            count = float(data.get("effective_verifications") or 0)
        except (TypeError, ValueError):
            count = 0
        return data["complete"] and count >= float(minimum)

    def _symbol_filters(self, symbol):
        """Filtros reales del símbolo (tick, step, notional mínimo), para que el
        replay del operador Meta no junte líneas en monedas de precio muy bajo
        con el tick genérico 0,0001 del simulador (ver REPORTE_FASE40C.md,
        parte B1). Solo afecta el camino Meta; el Advisor normal sigue sin
        pasar `filters` y usa el valor por defecto de grid/sim/runner.py."""
        cached = self._symbol_filters_cache.get(symbol)
        if cached is not None:
            return cached
        raw = getattr(self.scanner.client, "client", self.scanner.client)
        info = raw.get_symbol_info(symbol)
        filters = SymbolFilters.from_symbol_info(info)
        self._symbol_filters_cache[symbol] = filters
        return filters

    def _evaluate(self, row, strategy, capital, target_usdt, low, high, n, cfg, *,
                  margin_pct=None):
        symbol = row["symbol"]
        try:
            symbol_filters = self._symbol_filters(symbol)
        except Exception:
            # Sin filtros reales se usa el tick genérico de grid/sim/runner.py
            # (comportamiento anterior); no se cae el análisis por esto.
            symbol_filters = None
        # Historial público del Scanner; la ventana sigue el plazo del perfil y avanza 7d.
        candle_key = (symbol, self.clock() // 86400)
        candles = self._candles_cache.get(candle_key)
        if candles is None:
            candles_raw = self.scanner.client.get_historical_klines(symbol, "5m", 180)
            from grid.sim.data import CandleData
            import pandas as pd
            frame = candles_raw if isinstance(candles_raw, pd.DataFrame) else pd.DataFrame(candles_raw)
            timestamps = pd.to_datetime(frame["timestamp"], utc=True).astype("int64").to_numpy() // 1_000_000_000
            candles = CandleData(timestamp=timestamps.astype("int64"), open=frame["open"].to_numpy(float),
                                 high=frame["high"].to_numpy(float), low=frame["low"].to_numpy(float),
                                 close=frame["close"].to_numpy(float), gaps=0)
            self._candles_cache[candle_key] = candles
        window_days = int(cfg.get("max_days", 20))
        book = row.get("book") or {}
        try:
            current_mid = (float(book.get("bid_price")) + float(book.get("ask_price"))) / 2
        except (TypeError, ValueError):
            current_mid = 0.0
        if current_mid <= 0:
            current_mid = math.sqrt(float(low) * float(high))
        # El rango relativo se cuantiza (B4 de 40c): la clave y la simulación usan
        # el mismo piso/techo cuantizado, así un movimiento intradía menor al paso
        # reutiliza el mismo resultado en vez de fallar la caché cada vez.
        low_ratio = self._quantize_relative_range(float(low) / current_mid)
        high_ratio = self._quantize_relative_range(float(high) / current_mid)
        low, high = low_ratio * current_mid, high_ratio * current_mid
        key = (symbol, self.clock() // 86400, strategy, round(target_usdt, 4), n,
               round(capital, 4), window_days, low_ratio, high_ratio,
               round(float(cfg.get("max_loss_pct", 0)), 4))
        cached = self._cache.get(key)
        self._analysis_timing.setdefault("replay_cache_hits", 0)
        if cached is None:
            cached = self._persistent_cache_get("replay", key)
            if cached is not None:
                self._analysis_timing["replay_cache_hits"] += 1
                self._analysis_timing["replay_persistent_cache_hits"] = self._analysis_timing.get("replay_persistent_cache_hits", 0) + 1
        else:
            self._analysis_timing["replay_cache_hits"] += 1
            self._analysis_timing["replay_process_cache_hits"] = self._analysis_timing.get("replay_process_cache_hits", 0) + 1
        replay_windows = None
        if cached is None:
            replay_started = time.perf_counter()
            windows = build_windows(candles, window_days, 7)
            replay_windows = windows
            rows, window_results, skipped_windows = [], [], []
            # El rango del plan está en precios de HOY. En cada ventana histórica se
            # reescala al precio de inicio de esa ventana (mismo ancho relativo), para
            # simular "abrir esta misma pieza en ese momento". Sin esto, una ventana
            # cuyo precio estaba fuera del rango actual rompe todo el análisis.
            book = row.get("book") or {}
            try:
                ref = (float(book.get("bid_price")) + float(book.get("ask_price"))) / 2
            except (TypeError, ValueError):
                ref = 0.0
            if strategy != "individual_bot" and not (float(low) < ref < float(high)):
                ref = math.sqrt(float(low) * float(high))
            if ref <= 0:
                ref = math.sqrt(float(low) * float(high))
            for window in windows:
                sample = _slice(candles, window["start_idx"], window["end_idx"])
                start_ts = datetime.fromtimestamp(int(window["start"]), timezone.utc).date().isoformat()
                scale = float(sample.open[0]) / ref
                w_low, w_high = float(low) * scale, float(high) * scale
                try:
                    result_row = self._replay_window(sample, start_ts, strategy, n, capital, target_usdt,
                                                     w_low, w_high, cfg, filters=symbol_filters)
                except GridConfigError as exc:
                    # Con el precio reescalado de una ventana vieja, el redondeo al tick
                    # puede juntar líneas. Esa ventana se omite y se cuenta; no se cae todo.
                    skipped_windows.append({"window_start": start_ts, "reason": str(exc)})
                    continue
                rows.append(result_row)
                window_results.append(result_row)
                if getattr(self, "_low_priority_precompute", False):
                    time.sleep(.001)
            cached = {"summary": summarize_replays(rows, independent_window_days=window_days, target_usdt=target_usdt),
                      "window_results": window_results}
            cached["summary"]["skipped_windows"] = len(skipped_windows)
            if skipped_windows:
                cached["summary"]["skipped_reason"] = skipped_windows[0]["reason"]
            cached["summary"]["loss_cut_windows"] = sum(bool(row.get("loss_cut")) for row in rows)
            if strategy == "individual_bot":
                cached["summary"]["bot_windows_bought"] = sum(bool(row.get("bought")) for row in rows)
                cached["summary"]["bot_windows_sold"] = sum(bool(row.get("sold")) for row in rows)
            self._cache[key] = cached
            self._persistent_cache_set("replay", key, cached)
            self._analysis_timing["replay_s"] += time.perf_counter() - replay_started
        try:
            signal_ref = (float(book.get("bid_price")) + float(book.get("ask_price"))) / 2
        except (TypeError, ValueError):
            signal_ref = 0.0
        if strategy != "individual_bot" and not (float(low) < signal_ref < float(high)):
            signal_ref = math.sqrt(float(low) * float(high))
        if signal_ref <= 0:
            signal_ref = math.sqrt(float(low) * float(high))
        touch_started = time.perf_counter()
        touch_signal, touch_observations = self._touch_signal_for(
            symbol, strategy, n, float(low), float(high), signal_ref, candles, window_days,
            windows=replay_windows)
        self._analysis_timing["touch_signal_s"] = self._analysis_timing.get("touch_signal_s", 0.0) + (
            time.perf_counter() - touch_started)
        cached = json.loads(json.dumps(cached))
        window_results = cached["window_results"]
        estimate = cached["summary"]
        piece_type = "individual_bot_grid" if strategy == "individual_bot" else "grid"
        piece_levels = max(4, n) if piece_type == "grid" else 1
        strategy_name = "simple" if piece_type == "individual_bot_grid" else strategy
        pieces = [{"symbol": symbol, "strategy": strategy_name,
                   "piece_type": piece_type,
                   "capital_usdt": capital, "n_levels": piece_levels, "floor": low, "ceiling": high,
                   "target_usdt": float(target_usdt),
                   "mid_at_plan": (float((row.get("book") or {}).get("bid_price", 0)) +
                                   float((row.get("book") or {}).get("ask_price", 0))) / 2 or
                                  (float(low) + float(high)) / 2,
                   "margin_pct": float(margin_pct if margin_pct is not None else cfg.get("min_margin_pct", .7)),
                   "loans_pct": float(cfg.get("loan_cap_pct", 0)) if strategy_name == "smart" else 0}]
        return {"symbol": symbol, "strategy": strategy_name, "pieces": pieces, "estimate": estimate,
                "scanner_score": row.get("score"), "signals_used": [],
                "touch_signal": touch_signal, "_touch_observations": touch_observations,
                "replay_probability": estimate["probability"], "window_results": window_results,
                "shadow_decision": {"scanner_score": row.get("score"), "advisor_strategy": strategy},
                "reason": "alternativa de replay histórico"}

    def _replay_window(self, sample, start_ts, strategy, n, capital, target_usdt, w_low, w_high, cfg, *,
                        filters=None):
        """Simula una ventana histórica; lanza GridConfigError si el rango no es simulable.

        `filters` son los filtros reales del símbolo (tick/step/notional); si
        faltan, cada simulador cae a su propio tick genérico (comportamiento
        anterior a la Fase 40c, parte B1)."""
        if strategy == "individual_bot":
            from grid.sim.meta_bot import run_meta_bot
            replay = run_meta_bot(sample, capital=capital, buy_price=w_low, sell_price=w_high,
                                  target_usdt=target_usdt, fee_pct=.1, filters=filters,
                                  max_loss_pct=cfg.get("max_loss_pct"),
                                  min_margin_pct=cfg.get("min_margin_pct", .3))
            result_row = {"window_start": start_ts, "reached": replay["reached"],
                          "days": replay["days"], "pnl_usdt": replay["pnl_usdt"],
                          "drawdown_pct": replay["drawdown_pct"],
                          "worst_loss_vs_capital_pct": replay["worst_loss_vs_capital_pct"], "step_days": 7,
                          "loss_cut": replay["loss_cut"], "bought": replay["bought"],
                          "sold": replay["sold"]}
        else:
            params = {"target_usdt": target_usdt, "target_basis": "cash"}
            simulation = run_simulation(sample, strategy=strategy, n=n, capital=capital,
                low=w_low, high=w_high, width_pct=None, params=params, fee_pct=.1,
                close_wait_enabled=True, adjust_enabled_global=False, stoploss_enabled=False,
                meta_max_loss_pct=cfg.get("max_loss_pct"), filters=filters)
            event = next((e for e in simulation["events"] if e.get("type") == "TARGET_REACHED"), None)
            loss_cut = any(e.get("type") == "META_LOSS_CUT" for e in simulation["events"])
            days = None if event is None else (int(event["ts"]) - int(sample.timestamp[0])) / 86400
            metrics = simulation["metrics"]
            result_row = {"window_start": start_ts, "reached": event is not None,
                          "days": days,
                          "pnl_usdt": (float(metrics.get("cash_final", capital)) - capital if loss_cut else
                              float(metrics.get("equity_final_after_repository_liquidation", capital)) - capital),
                          "drawdown_pct": max(0.0, float(metrics.get("max_drawdown_pct", 0))),
                          "worst_loss_vs_capital_pct": max(0.0, float(metrics.get("worst_loss_vs_capital_pct", 0))),
                          "step_days": 7, "loss_cut": loss_cut}
        return result_row

    def _touch_signal_for(self, symbol, strategy, n, low, high, ref, candles, window_days, *, windows=None):
        """Calcula estadísticas por celda y ventana, con precios reescalados."""
        horizons = {"4h": 4 * 12, "24h": 24 * 12, f"{int(window_days)}d": int(window_days) * 24 * 12}
        if strategy == "individual_bot":
            cells = [(low, high)]
        else:
            # Mismo espaciado que compute_lines, pero sin redondear al tick genérico de la
            # simulación (0.0001): en monedas de precio muy bajo ese redondeo junta las
            # líneas. Para contar toques no hace falta redondear.
            step = (float(high) - float(low)) / int(n)
            lines = [float(low) + step * i for i in range(int(n) + 1)]
            cells = list(zip(lines[:-1], lines[1:]))
        payload = {"version": 1, "symbol": symbol, "day": self.clock() // 86400,
            "strategy": strategy, "n": int(n), "window_days": int(window_days),
            "buy_sell_relative": [[round(float(x) / ref, 10), round(float(y) / ref, 10)] for x, y in cells],
            "horizons": horizons}
        process_key = ("touch_signal", json.dumps(payload, sort_keys=True))
        self._analysis_timing = getattr(self, "_analysis_timing", {})
        self._analysis_timing.setdefault("touch_signal_s", 0.0)
        self._analysis_timing.setdefault("touch_signal_cache_hits", 0)
        cached = self._cache.get(process_key)
        if cached is None:
            cached = self._persistent_cache_get("touch_signal", payload)
        if cached is not None:
            self._analysis_timing["touch_signal_cache_hits"] = self._analysis_timing.get(
                "touch_signal_cache_hits", 0) + 1
            return cached["summary"], cached["observations"]

        windows = list(windows) if windows is not None else build_windows(candles, int(window_days), 7)
        observations = []
        by_cell_horizon = {(cell_index, horizon): [] for cell_index in range(len(cells))
                           for horizon in horizons}
        for window in windows:
            sample = _slice(candles, window["start_idx"], window["end_idx"])
            scale = float(sample.open[0]) / float(ref)
            window_start = datetime.fromtimestamp(int(window["start"]), timezone.utc).date().isoformat()
            for cell_index, (buy, sell) in enumerate(cells):
                for horizon_name, horizon_candles in horizons.items():
                    stats = touch_stats(sample, buy * scale, sell * scale, horizon_candles)
                    rate = stats["sell_rate"]
                    stats["normalized_value"] = None if rate is None else 2 * float(rate) - 1
                    observation = {"cell_index": cell_index, "window_start": window_start,
                        "horizon": horizon_name, "buy_relative": float(buy) / float(ref),
                        "sell_relative": float(sell) / float(ref), "stats": stats}
                    observations.append(observation)
                    by_cell_horizon[(cell_index, horizon_name)].append(stats)

        summary_cells = []
        for cell_index, (buy, sell) in enumerate(cells):
            cell_horizons = {name: aggregate_touch_stats(by_cell_horizon[(cell_index, name)])
                             for name in horizons}
            for value in cell_horizons.values():
                rate = value["sell_rate"]
                value["normalized_value"] = None if rate is None else 2 * float(rate) - 1
                value.pop("sell_candles", None)
            summary_cells.append({"cell_index": cell_index, "buy_relative": float(buy) / ref,
                                  "sell_relative": float(sell) / ref, "horizons": cell_horizons})
        aggregate = {}
        for horizon_name in horizons:
            value = aggregate_touch_stats([cell["horizons"][horizon_name] for cell in summary_cells])
            rate = value["sell_rate"]
            value["normalized_value"] = None if rate is None else 2 * float(rate) - 1
            value.pop("sell_candles", None)
            aggregate[horizon_name] = value
        for observation in observations:
            observation["stats"].pop("sell_candles", None)
        result = {"summary": {"horizons": aggregate, "cells": summary_cells,
                              "weight": 0, "shadow_only": True},
                  "observations": observations}
        self._cache[process_key] = result
        self._persistent_cache_set("touch_signal", payload, result)
        return result["summary"], result["observations"]

    def _apply_intelligence(self, alternative, row, price, low, high, cfg, *, advisor_result=None):
        """Ajuste logit aditivo: logit(p_meta)=logit(p_replay)+sum(peso*señal)."""
        signals, omitted = [], []
        model = row.get("model_statistics") or self._model_snapshot(row["symbol"])
        usable_sigmas = [float(value["sigma_24h"]) for value in (model.get("volatility_forecasts") or {}).values()
                         if value.get("used") and value.get("sigma_24h") is not None]
        if usable_sigmas and row.get("sigma_24h_open"):
            forecast_sigma = float(np.median(usable_sigmas))
            realized = float(row["sigma_24h_open"])
            signals.append({"name": "volatility", "value": forecast_sigma,
                            "normalized": max(-1.0, min(1.0, math.log(max(1e-9, forecast_sigma / realized)))),
                            "models": model.get("volatility_forecasts")})
        else:
            stale = [name for name, value in (model.get("volatility_forecasts") or {}).items()
                     if not value.get("used")]
            omitted.append({"name": "volatility", "reason": "no hay pronóstico fresco de modelos activos",
                            "models_omitted": stale})
        try:
            params = dict(DEFAULT_SMART_PARAMS)
            params["min_free_cells"] = min(params["min_free_cells"], 1)
            sigma_for_break = float(np.median(usable_sigmas)) if usable_sigmas else float(row.get("sigma_24h_open") or 0)
            probability = break_prob(price, low, high, sigma_for_break, params)[0]
            signals.append({"name": "break_prob", "value": probability,
                            "sigma_24h": sigma_for_break,
                            "normalized": max(-1.0, min(1.0, 1 - 2 * float(probability)))})
        except Exception as exc:
            omitted.append({"name": "break_prob", "reason": str(exc)})
        prediction = self.db.get_latest_prediction(row["symbol"], "1h", "model_a_down")
        fresh_direction = False
        if prediction and prediction.get("predicted_at") is not None:
            predicted_at = prediction["predicted_at"]
            predicted_at = predicted_at.replace(tzinfo=timezone.utc) if predicted_at.tzinfo is None else predicted_at
            fresh_direction = 0 <= (datetime.now(timezone.utc) - predicted_at).total_seconds() <= 7200
        if fresh_direction and prediction.get("probability_up") is not None:
            p_up = float(prediction["probability_up"])
            direction = max(-1.0, min(1.0, (p_up - .5) / .1)) if p_up >= .60 else 0.0
            signals.append({"name": "direction", "value": p_up, "threshold": .60, "normalized": direction})
        else:
            omitted.append({"name": "direction", "reason": "predicción ausente o con más de 2 h"})
        score = row.get("score")
        if score is not None:
            signals.append({"name": "scanner", "value": score,
                            "normalized": max(-1.0, min(1.0, 2 * float(score) - 1))})
        else:
            omitted.append({"name": "scanner", "reason": "score ausente"})
        advisor_value = (advisor_result or {}).get("net_per_cycle_after_dust_usdt")
        if advisor_value is not None:
            advisor_value = float(advisor_value) / max(1.0, float(alternative["pieces"][0]["capital_usdt"])) * 100
            signals.append({"name": "advisor", "value": advisor_value,
                            "normalized": max(-1.0, min(1.0, float(advisor_value) / 2.0))})
        else:
            omitted.append({"name": "advisor", "reason": "recomendación sin margen neto"})
        try:
            from grid.meta_learning import causal_touch_buy_to_sell
            candle_key = (row["symbol"], self.clock() // 86400)
            candles = self._candles_cache.get(candle_key)
            piece = (alternative.get("pieces") or [{}])[0]
            touch = (causal_touch_buy_to_sell(at=self.clock(), candles=candles,
                buy_price=piece.get("floor", low), sell_price=piece.get("ceiling", high))
                if candles is not None else {"sell_rate": None})
            rate = touch.get("sell_rate")
            if rate is not None:
                signals.append({"name": "touch_buy_to_sell", "value": float(rate),
                    "normalized": max(-1.0, min(1.0, 2 * float(rate) - 1)),
                    "source": "velas previas", "lookback_days": 30})
            else:
                omitted.append({"name": "touch_buy_to_sell",
                    "reason": "sin tasa de venta causal disponible"})
        except (TypeError, ValueError, KeyError, IndexError) as exc:
            omitted.append({"name": "touch_buy_to_sell", "reason": str(exc)})
        weights = (cfg.get("signal_weights") or {})
        enough_evidence = bool(alternative["estimate"].get("evidence_sufficient"))
        logit_cap = float(cfg.get("signal_logit_cap", 1.0))
        adjusted = (signal_adjusted_probability(alternative["replay_probability"], signals, weights, logit_cap)
                    if enough_evidence else float(alternative["replay_probability"]))
        shadow_signals = [item for item in signals if item["name"] in {"scanner", "advisor"}]
        shadow_probability = (signal_adjusted_probability(alternative["replay_probability"],
            shadow_signals, weights, logit_cap) if enough_evidence else float(alternative["replay_probability"]))
        alternative["signals_used"] = signals
        alternative["signals_omitted"] = omitted
        alternative["estimate"]["probability_adjusted"] = adjusted
        alternative["estimate"]["probability_replay"] = alternative["replay_probability"]
        alternative["estimate"]["probability"] = adjusted
        alternative["estimate"]["signal_logit_adjustment_cap"] = logit_cap
        alternative["estimate"]["low_likelihood"] = adjusted < .25
        alternative["shadow_probability"] = shadow_probability
        alternative["shadow_signals"] = shadow_signals
        alternative["shadow_decision"] = {"probability": shadow_probability,
            "scanner_score": score, "advisor_net_edge_pct_per_cycle": advisor_value}

    def _control_plan(self, best, capital):
        return {"type": "normal_smart_advisor_control", "symbol": best["symbol"], "capital_usdt": capital,
                "strategy": "smart", "same_analysis_date": True, "opened": False,
                "range_low": best["pieces"][0]["floor"], "range_high": best["pieces"][0]["ceiling"],
                "n_levels": max(4, int(best["pieces"][0]["n_levels"]))}

    def compare_control(self, run_id):
        """Replay (sin abrir nada) del plan de control sobre las MISMAS fechas
        de una corrida ya terminada. Si la corrida sigue activa, solo devuelve
        el plan de control guardado (no hay fechas de cierre aún). El
        resultado se guarda en meta_runs.control_result_json para no repetir
        el replay en cada consulta."""
        from sqlalchemy import select
        with self.db.engine.connect() as conn:
            run = conn.execute(select(self.db.meta_runs).where(
                self.db.meta_runs.c.id == int(run_id))).mappings().first()
        if run is None:
            raise ValueError("Corrida no encontrada.")
        control_plan = json.loads(run.get("control_plan_json") or "null")
        if control_plan is None:
            return {"run_id": int(run_id), "control_plan": None, "control_result": None,
                    "note": "Esta corrida no guardó un plan de control."}
        if run.get("finished_at") is None:
            return {"run_id": int(run_id), "control_plan": control_plan, "control_result": None,
                    "note": "La corrida sigue activa; solo se muestra el plan de control."}
        cached = json.loads(run.get("control_result_json") or "null")
        if cached is not None:
            return {"run_id": int(run_id), "control_plan": control_plan, "control_result": cached}
        result = self._replay_control(control_plan, run["started_at"], run["finished_at"])
        with self.db.engine.begin() as conn:
            conn.execute(self.db.meta_runs.update().where(self.db.meta_runs.c.id == int(run_id)).values(
                control_result_json=json.dumps(result, sort_keys=True, default=str)))
        return {"run_id": int(run_id), "control_plan": control_plan, "control_result": result}

    def _replay_control(self, control_plan, started_at, finished_at):
        """Simula el plan de control (Smart normal, sin meta) desde started_at
        hasta finished_at con velas públicas reales de ese rango exacto -- no
        reescala precios ni busca ventanas: es la misma línea de tiempo real
        de la corrida que se está comparando."""
        import pandas as pd
        from grid.sim.data import CandleData
        symbol = control_plan["symbol"]
        started = started_at if started_at.tzinfo else started_at.replace(tzinfo=timezone.utc)
        finished = finished_at if finished_at.tzinfo else finished_at.replace(tzinfo=timezone.utc)
        if finished <= started:
            raise ValueError("La corrida no tiene un rango de fechas válido para comparar.")
        lookback_days = max(1, math.ceil((datetime.now(timezone.utc) - started).total_seconds() / 86400) + 1)
        candles_raw = self.scanner.client.get_historical_klines(symbol, "5m", lookback_days, start_time=started)
        frame = candles_raw if isinstance(candles_raw, pd.DataFrame) else pd.DataFrame(candles_raw)
        if frame.empty:
            raise ValueError("Sin velas históricas públicas para el plan de control.")
        timestamps = (pd.to_datetime(frame["timestamp"], utc=True).astype("int64").to_numpy() // 1_000_000_000)
        mask = timestamps <= int(finished.timestamp())
        if int(mask.sum()) < 2:
            raise ValueError("Sin velas suficientes dentro del rango de la corrida.")
        candles = CandleData(timestamp=timestamps[mask].astype("int64"),
                             open=frame["open"].to_numpy(float)[mask], high=frame["high"].to_numpy(float)[mask],
                             low=frame["low"].to_numpy(float)[mask], close=frame["close"].to_numpy(float)[mask],
                             gaps=0)
        try:
            symbol_filters = self._symbol_filters(symbol)
        except Exception:
            symbol_filters = None
        capital = float(control_plan["capital_usdt"])
        simulation = run_simulation(candles, strategy=control_plan.get("strategy", "smart"),
            n=max(4, int(control_plan["n_levels"])), capital=capital,
            low=float(control_plan["range_low"]), high=float(control_plan["range_high"]), width_pct=None,
            params=None, fee_pct=.1, close_wait_enabled=True, adjust_enabled_global=False,
            stoploss_enabled=False, filters=symbol_filters)
        metrics = simulation["metrics"]
        final_equity = float(metrics.get("equity_final_after_repository_liquidation", capital))
        pnl = final_equity - capital
        return {"symbol": symbol, "capital_usdt": capital, "pnl_usdt": pnl,
                "result_pct": (pnl / capital * 100) if capital else 0.0,
                "max_drawdown_pct": float(metrics.get("max_drawdown_pct", 0)),
                "worst_loss_vs_capital_pct": float(metrics.get("worst_loss_vs_capital_pct", 0)),
                "candles_used": int(mask.sum())}

    def start_plan(self, meta_id, plan_id, opener):
        with self._operation_lock:
            self._opener = opener
            return self._start_plan_locked(meta_id, plan_id, opener)

    def _start_plan_locked(self, meta_id, plan_id, opener):
        with self._lock:
            plan = self._plans.get(str(plan_id))
        if plan is None or int(plan.get("meta_id", -1)) != int(meta_id):
            raise ValueError("Plan no encontrado; solicite un análisis nuevo.")
        if self.clock() > plan["expires_at"]:
            raise ValueError("Plan vencido; solicite un análisis nuevo.")
        goal = self.core.get_goal(meta_id)
        cfg = goal.get("config_snapshot") or {}
        max_drift = float(cfg.get("plan_price_drift_pct", self.price_drift_pct))
        if self.engine is not None:
            for piece in plan.get("pieces", []):
                openability = self._testnet_openability(piece, goal)
                if not openability["allowed"]:
                    raise ValueError(f"Plan vencido operativamente: alternativa no abrible en Testnet: {openability['reason']}. Solicite un análisis nuevo.")
        for symbol, planned in self._plan_prices(plan).items():
            current = self._book_ticker(symbol)
            mid = (float(current["bid_price"]) + float(current["ask_price"])) / 2
            if abs(mid / planned - 1) * 100 > max_drift:
                raise ValueError(f"El precio de {symbol} se movió más de {max_drift:g}%; solicite un análisis nuevo.")
        self.core.start_goal(meta_id)
        run_id = self._active_run_id(meta_id)
        with self.db.engine.begin() as conn:
            conn.execute(self.db.meta_runs.update().where(self.db.meta_runs.c.id == run_id).values(
                control_plan_json=json.dumps(plan.get("control_plan"), sort_keys=True)))
        opened = []
        for piece in plan["pieces"]:
            try:
                if piece["piece_type"] == "individual_bot_grid":
                    result = self.engine.create_grid(piece["symbol"], piece["floor"], piece["ceiling"], 1,
                        capital=piece["capital_usdt"], strategy="simple", meta_id=int(meta_id),
                        meta_piece_type="individual_bot_grid")
                    result = {"grid_id": int(result["id"]), "symbol": piece["symbol"]}
                else:
                    result = opener(piece)
                grid_id = int(result["grid_id"])
                self.core.link_grid(meta_id, grid_id, piece["piece_type"])
                opened.append({**piece, "grid_id": grid_id})
                self._ledger(meta_id, "META_OPERATOR_OPEN", {"piece": piece, "grid_id": grid_id})
                self.alert("META_PIECE_OPENED", meta_id, {"symbol": piece["symbol"], "reason": "inicio del plan Meta"})
            except Exception as exc:
                self._ledger(meta_id, "META_OPERATOR_OPEN_FAILED", {"piece": piece, "error": str(exc)})
        if not opened:
            with self.db.engine.begin() as conn:
                conn.execute(self.db.meta_goals.update().where(self.db.meta_goals.c.id == int(meta_id)).values(status="DRAFT", started_at=None))
                conn.execute(self.db.meta_runs.update().where(self.db.meta_runs.c.id == run_id).values(status="NO_PIECES", finished_at=self.core._now()))
            raise ValueError("No se pudo abrir ninguna pieza; la meta volvió a DRAFT.")
        return {"meta": self.core.get_goal(meta_id), "opened": opened, "failed": len(plan["pieces"]) - len(opened)}

    def _plan_prices(self, plan):
        prices = {}
        pieces = list(plan.get("pieces", []))
        for alternative in plan.get("alternatives", []):
            pieces.extend(alternative.get("pieces", []))
        for piece in pieces:
            prices.setdefault(piece["symbol"], float(piece.get("mid_at_plan") or
                ((float(piece["floor"]) + float(piece["ceiling"])) / 2)))
        return prices

    def _active_run_id(self, meta_id):
        from sqlalchemy import select
        with self.db.engine.connect() as conn:
            row = conn.execute(select(self.db.meta_runs.c.id).where(self.db.meta_runs.c.meta_id == int(meta_id),
                self.db.meta_runs.c.status == "ACTIVE").order_by(self.db.meta_runs.c.id.desc())).first()
        return None if row is None else int(row[0])

    def review_once(self, meta_id):
        if not self._review_guard.acquire(blocking=False):
            result = {"decision": "no_hacer_nada", "reason": "la revisión anterior sigue en curso"}
            self._ledger(meta_id, "META_OPERATOR_REVIEW_SKIPPED", result)
            return result
        try:
            return self._review_once_locked(meta_id)
        finally:
            self._review_guard.release()

    @staticmethod
    def _review_choice_label(result):
        decision = str((result or {}).get("decision") or "")
        if decision == "no_hacer_nada": return "mantener"
        if decision == "reajustar_pieza": return "reajustar"
        if decision in {"retirar_con_ventas_vivas", "retirar_y_abrir", "cerrar_y_reasignar"}: return "rotar"
        if decision in {"cerrar_sin_reasignar", "cerrar"}: return "cerrar"
        if decision == "abrir_pieza_con_efectivo_libre": return "abrir"
        return decision or "mantener"

    def _active_review_choices(self, plan, result, meta_id=None):
        choices = json.loads(json.dumps((plan or {}).get("decision_choices") or {}))
        choices["comparison_version"] = 2
        real_label = self._review_choice_label(result)
        real_id = (result or {}).get("alternative_id")
        choices["real"] = {"decision": real_label, "alternative_id": real_id,
            "symbol": (result or {}).get("symbol"), "probability": (result or {}).get("probability")}
        grid_id = result.get("grid_id")
        grid = self.db.get_grid(int(grid_id)) if grid_id is not None else None
        current = next((row for row in (plan or {}).get("alternatives", []) if grid and
            row.get("symbol") == grid.get("symbol") and row.get("strategy") == grid.get("strategy") and
            len(row.get("pieces", [])) == 1), None)
        current_est = (current or {}).get("estimate") or {}
        meta_id = int(meta_id or result.get("meta_id") or 0)
        cfg = (self.core.get_goal(meta_id) or {}).get("config_snapshot") or {} if meta_id else {}
        now = float(self.clock())
        ledger = self._meta_ledger_rows(meta_id) if meta_id else []
        for name in ("scanner_advisor", "learned"):
            proposed = choices.get(name) or {}
            candidate = next((row for row in (plan or {}).get("alternatives", [])
                if row.get("alternative_id") == proposed.get("alternative_id")), None)
            is_actionable = bool(candidate and grid and proposed.get("alternative_id") and
                proposed.get("alternative_id") != current.get("alternative_id" if current else ""))
            desired = ("reajustar" if grid and candidate and candidate.get("symbol") == grid.get("symbol")
                and candidate.get("strategy") == grid.get("strategy") else "rotar")
            key = f"{name}:{int(grid_id or 0)}"
            prior_rows = [row for row in ledger if row.get("event_type") == "META_OPERATOR_COUNTERFACTUAL_STATE"
                and int(row.get("grid_id") or -1) == int(grid_id or -2)
                and (row.get("details") or {}).get("policy") == name]
            prior_details = [(row.get("details") or {}) for row in prior_rows]
            last_action = next((row.get("action_at") for row in reversed(prior_details)
                if row.get("action_at") is not None), None)
            recent_actions = [row for row in prior_details if row.get("action_at") is not None
                and now - float(row["action_at"]) <= 86400]
            choice = counterfactual_policy_decision(
                {"probability": current_est.get("probability_replay", current_est.get("probability", 0)),
                 "median_progress_pct": current_est.get("median_progress_pct")},
                ([{"probability": ((candidate or {}).get("learned_probability") if name == "learned" else
                        (candidate or {}).get("shadow_probability", (candidate or {}).get("estimate", {}).get("probability_replay"))),
                    "median_progress_pct": ((candidate or {}).get("estimate") or {}).get("median_progress_pct"),
                    "alternative_id": (candidate or {}).get("alternative_id"),
                    "alternative_key": self._alternative_key(candidate) if candidate else "",
                    "destination": (candidate or {}).get("alternative_id"), "action": desired,
                    "min_probability_delta_pp": cfg.get("rotation_min_probability_delta_pp", 5),
                    "probability_tie_tolerance_pp": cfg.get("rotation_probability_tie_tolerance_pp", 2),
                    "min_progress_delta_pct": cfg.get("rotation_min_progress_delta_pct", 10)}]
                    if is_actionable else []),
                prior_details, {"opened_at": (self._as_utc(grid.get("created_at")).timestamp()
                    if grid and self._as_utc(grid.get("created_at")) else None),
                    "last_action_at": last_action, "actions_24h": len(recent_actions)},
                required_reviews=int(cfg.get("rotation_persistence_reviews", 2)),
                cooldown_seconds=float(cfg.get("rotation_cooldown_hours", 1)) * 3600,
                max_actions_per_24h=int(cfg.get("max_rotations_per_24h", 2)), now=now)
            choice.update({"decision": choice["action"], "probability": proposed.get("probability"),
                "alternative_id": choice.get("destination"), "symbol": proposed.get("symbol"),
                "counterfactual": True})
            choices[name] = choice
            if grid_id is not None:
                self._ledger(meta_id, "META_OPERATOR_COUNTERFACTUAL_STATE",
                    {"policy": name, "alternative_key": choice.get("alternative_key"),
                     "material": choice.get("material"), "streak": choice.get("streak"),
                     "decision": choice["action"], "reason": choice["reason"],
                     "action_at": now if choice["action"] != "mantener" else None}, grid_id=int(grid_id))
        return choices

    def _review_once_locked(self, meta_id):
        goal = self.core.get_goal(meta_id)
        value = self.core.valuation(meta_id)
        if goal["status"] != "ACTIVE" or value.get("status") != "KNOWN":
            self._reset_rotation_streaks(meta_id, "meta no activa o valor desconocido")
            result = {"decision": "no_hacer_nada", "reason": "meta no activa o valoración DESCONOCIDO"}
            self._ledger(meta_id, "META_OPERATOR_NOOP", result)
            self._ledger(meta_id, "META_OPERATOR_REVIEW_DECISION", {
                "decision": result, "shadow_decision": None, "valuation": value,
                "decision_choices": {"comparison_version": 2,
                    "real": {"decision": "mantener", "alternative_id": None},
                    "scanner_advisor": {"decision": "mantener", "alternative_id": None},
                    "learned": {"decision": "mantener", "alternative_id": None}}})
            return result
        cfg = goal.get("config_snapshot") or {}
        if float(value["result_pct"]) <= -float(cfg.get("warning_loss_pct", 0)):
            self._reset_rotation_streaks(meta_id, "bloqueo 75% del limite de perdida")
            result = {"decision": "no_hacer_nada", "reason": "bloqueo 75% del límite de pérdida"}
            self._record_review(meta_id, result, value, None)
            return result
        # Scanner/Advisor/replays can take minutes. They must not hold the
        # monitor lock because MetaCore's one-minute loss guard uses it too.
        try:
            plan = self._build_plan(goal)
        except Exception as exc:
            self._reset_rotation_streaks(meta_id, f"analisis no disponible: {exc}")
            result = {"decision": "no_hacer_nada", "reason": f"análisis de revisión no disponible: {exc}"}
            self._record_review(meta_id, result, value, None)
            return result
        with self._operation_lock:
            current_goal = self.core.get_goal(meta_id)
            current_value = self.core.valuation(meta_id)
            if current_goal["status"] != "ACTIVE" or current_value.get("status") != "KNOWN":
                self._reset_rotation_streaks(meta_id, "meta cambio durante analisis")
                result = {"decision": "no_hacer_nada", "reason": "meta dejó de estar activa o valor DESCONOCIDO durante el análisis"}
            elif float(current_value["result_pct"]) <= -float((current_goal.get("config_snapshot") or {}).get("warning_loss_pct", 0)):
                self._reset_rotation_streaks(meta_id, "bloqueo 75% durante analisis")
                result = {"decision": "no_hacer_nada", "reason": "bloqueo 75% del límite de pérdida durante el análisis"}
            elif self._plan_price_moved(plan, current_goal):
                self._reset_rotation_streaks(meta_id, "precio cambio durante analisis")
                result = {"decision": "no_hacer_nada", "reason": "precio cambió durante el análisis; recalcular en la siguiente revisión"}
            else:
                result = self._review_allocations(current_goal, current_value, plan=plan)
            value = current_value
        self._ledger(meta_id, "META_OPERATOR_REVIEW_DECISION", {
            "decision": result, "shadow_decision": getattr(self, "_last_review_shadow", None),
            "valuation": value, "touch_decision_id": (plan or {}).get("decision_id"),
                "decision_choices": {**self._active_review_choices(plan, result, meta_id),
                "real_result": result, "learned_weights_enabled": bool(
                    (((plan or {}).get("learning") or {}).get("enabled"))),
                "learned_weight_version_ids": ((plan or {}).get("learning") or {}).get("version_ids", []),
                "comparison_version": 2},
            "touch_alternatives": [{"alternative_id": item.get("alternative_id"),
                "symbol": item.get("symbol"), "touch_signal": item.get("touch_signal")}
                for item in (plan or {}).get("alternatives", [])]})
        if result.get("decision") == "no_hacer_nada":
            self._ledger(meta_id, "META_OPERATOR_NOOP", {**result, "valuation": value})
        return result

    def _record_review(self, meta_id, result, value, shadow, plan=None):
        self._ledger(meta_id, "META_OPERATOR_REVIEW_DECISION", {
            "decision": result, "shadow_decision": shadow, "valuation": value,
            "decision_choices": self._active_review_choices(plan, result, meta_id),
            "comparison_version": 2})
        self._ledger(meta_id, "META_OPERATOR_NOOP", {**result, "valuation": value})

    def _plan_price_moved(self, plan, goal):
        max_drift = float((goal.get("config_snapshot") or {}).get("plan_price_drift_pct", self.price_drift_pct))
        try:
            for symbol, planned in self._plan_prices(plan).items():
                book = self._book_ticker(symbol)
                mid = (float(book["bid_price"]) + float(book["ask_price"])) / 2
                if planned <= 0 or abs(mid / planned - 1) * 100 > max_drift:
                    return True
        except Exception:
            return True
        return False

    def _meta_ledger_rows(self, meta_id):
        reader = getattr(self.core, "ledger", None)
        if callable(reader):
            try:
                return list(reader(int(meta_id)))
            except Exception:
                return []
        rows = []
        for args, kwargs in getattr(self.core, "events", []):
            if len(args) >= 4 and int(args[0]) == int(meta_id):
                rows.append({"event_type": args[2], "details": args[3] or {},
                    "grid_id": kwargs.get("grid_id"), "ts": None})
        return rows

    @staticmethod
    def _as_utc(value):
        if value is None:
            return None
        if isinstance(value, str):
            try:
                value = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                return None
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)

    @staticmethod
    def _alternative_key(alternative):
        piece = (alternative.get("pieces") or [{}])[0]
        numeric = lambda key: round(float(piece[key]), 4) if piece.get(key) is not None else ""
        return "|".join(map(str, (piece.get("symbol", ""), piece.get("piece_type", ""),
            piece.get("strategy", ""), numeric("floor"), numeric("ceiling"),
            piece.get("n_levels", ""), numeric("capital_usdt"))))

    def _rotation_gate(self, goal, grid, link, alternative, current_alternative, plan):
        """Aplicar materialidad, dos revisiones persistidas y frenos temporales antes de rotar."""
        cfg = goal.get("config_snapshot") or {}
        if isinstance(cfg, str):
            cfg = json.loads(cfg)
        new_est = alternative.get("estimate") or {}
        old_est = current_alternative.get("estimate") if current_alternative else None
        if old_est is None:
            return {"allowed": False, "reason": "no hay estimación comparable de la pieza actual"}
        probability_delta = None
        progress_delta = None
        if old_est is not None:
            probability_delta = (float(new_est.get("probability_replay", new_est.get("probability", 0)) or 0)
                - float(old_est.get("probability_replay", old_est.get("probability", 0)) or 0)) * 100
            old_progress = float(old_est.get("median_progress_pct") or 0)
            new_progress = float(new_est.get("median_progress_pct") or 0)
            progress_delta = ((new_progress / old_progress - 1) * 100 if old_progress > 0
                              else (100.0 if new_progress > 0 else 0.0))
        material = bool(old_est is not None and (
            probability_delta >= float(cfg.get("rotation_min_probability_delta_pp", 5.0)) or
            (abs(probability_delta) <= float(cfg.get("rotation_probability_tie_tolerance_pp", 2.0)) and
             progress_delta >= float(cfg.get("rotation_min_progress_delta_pct", 10.0)))))
        key = self._alternative_key(alternative)
        rows = self._meta_ledger_rows(goal["id"])
        evaluations = [row for row in rows if row.get("event_type") == "META_OPERATOR_ROTATION_EVALUATED"
            and int(row.get("grid_id") or -1) == int(grid["id"])]
        review_id = str(plan.get("decision_id") or uuid.uuid4().hex)
        prior = evaluations[-1] if evaluations else None
        prior_details = prior.get("details") or {} if prior else {}
        streak = (int(prior_details.get("streak") or 0) + 1
            if prior and prior_details.get("alternative_key") == key and prior_details.get("material")
                and prior_details.get("review_id") != review_id and material else 1 if material else 0)
        self._ledger(goal["id"], "META_OPERATOR_ROTATION_EVALUATED", {
            "review_id": review_id, "alternative_key": key, "material": material,
            "streak": streak, "probability_improvement_pp": probability_delta,
            "progress_improvement_pct": progress_delta,
            "alternative": alternative, "current": current_alternative,
            "shadow_decision": plan.get("shadow_decision")}, grid_id=int(grid["id"]))
        required_reviews = max(2, int(cfg.get("rotation_persistence_reviews", 2)))
        now = datetime.fromtimestamp(self.clock(), timezone.utc)
        opened_at = self._as_utc(link.get("created_at") or grid.get("created_at"))
        cooldown = timedelta(hours=float(cfg.get("rotation_cooldown_hours", 1.0)))
        rotations = []
        for row in rows:
            if row.get("event_type") != "META_OPERATOR_ROTATION":
                continue
            stamp = self._as_utc(row.get("ts"))
            if stamp and now - stamp <= timedelta(hours=24):
                rotations.append(row)
        last_piece_rotation = [self._as_utc(row.get("ts")) for row in rotations
            if int(row.get("grid_id") or -1) == int(grid["id"]) and self._as_utc(row.get("ts"))]
        # Árbitro puro compartido con las políticas contrafactuales.
        prior_streak = []
        for row in reversed(evaluations):
            details = row.get("details") or {}
            if details.get("alternative_key") != key or not details.get("material"):
                break
            if details.get("review_id") != review_id:
                prior_streak.append(details)
        policy = counterfactual_policy_decision(
            {"probability": (old_est or {}).get("probability_replay", (old_est or {}).get("probability", 0)),
             "median_progress_pct": (old_est or {}).get("median_progress_pct")},
            [{"probability": new_est.get("probability_replay", new_est.get("probability", 0)),
              "median_progress_pct": new_est.get("median_progress_pct"), "alternative_key": key,
              "destination": alternative.get("alternative_id"), "action": "rotar",
              "min_probability_delta_pp": cfg.get("rotation_min_probability_delta_pp", 5.0),
              "probability_tie_tolerance_pp": cfg.get("rotation_probability_tie_tolerance_pp", 2.0),
              "min_progress_delta_pct": cfg.get("rotation_min_progress_delta_pct", 10.0)}],
            prior_streak, {"opened_at": opened_at.timestamp() if opened_at else None,
                "last_action_at": max((stamp.timestamp() for stamp in last_piece_rotation), default=None),
                "actions_24h": len(rotations)}, required_reviews=required_reviews,
            cooldown_seconds=cooldown.total_seconds(),
            max_actions_per_24h=int(cfg.get("max_rotations_per_24h", 2)), now=now.timestamp())
        if policy["action"] == "mantener":
            return {"allowed": False, "reason": policy["reason"], "policy": policy}
        return {"allowed": True, "reason": None, "streak": streak,
            "probability_improvement_pp": probability_delta,
            "progress_improvement_pct": progress_delta, "policy": policy}

    def _reset_rotation_streaks(self, meta_id, reason):
        try:
            links = self.core._owned_grids(int(meta_id))
        except Exception:
            return
        review_id = f"reset:{uuid.uuid4().hex}"
        for link in links:
            self._ledger(meta_id, "META_OPERATOR_ROTATION_EVALUATED", {
                "review_id": review_id, "alternative_key": None, "material": False,
                "streak": 0, "reason": reason}, grid_id=int(link["grid_id"]))

    def _review_allocations(self, goal, valuation, *, plan=None):
        """Compara alternativa y pieza activa; toda rotación liquida por el cierre verificado."""
        links = self.core._owned_grids(int(goal["id"]))
        try:
            plan = plan if plan is not None else self._build_plan(goal)
        except Exception as exc:
            self._last_review_shadow = None
            return {"decision": "no_hacer_nada", "reason": f"análisis de revisión no disponible: {exc}"}
        self._last_review_shadow = plan.get("shadow_decision")
        best = next((item for item in plan["alternatives"]
                     if len(item.get("pieces", [])) == 1
                     and item["estimate"].get("evidence_sufficient")), None)
        if best is None:
            self._reset_rotation_streaks(goal["id"], "evidencia independiente insuficiente")
            return {"decision": "no_hacer_nada", "reason": "evidencia independiente insuficiente"}
        scan_rows = {row.get("symbol"): row for row in plan["candidates"]}
        for link in links:
            grid = self.db.get_grid(int(link["grid_id"]))
            if grid is None or grid.get("status") not in {"ACTIVE", "PAUSED"}:
                continue
            levels = self.db.get_grid_levels(int(grid["id"]))
            has_inventory = any(float(level.get("held_qty") or 0) > 0 for level in levels)
            book = self.engine.exchange.get_book_ticker(grid["symbol"])
            bid = float(book.get("bid_price") or 0)
            key = (int(goal["id"]), int(grid["id"]))
            above = bid > float(grid["range_high"])
            outside_now = bid < float(grid["range_low"]) or above
            self._outside_range_count[key] = self._outside_range_count.get(key, 0) + 1 if outside_now else 0
            scan_row = scan_rows.get(grid["symbol"], {})
            hard_fail = not bool(scan_row) or not bool(scan_row.get("scanner_eligible"))
            self._above_ceiling_count[key] = self._above_ceiling_count.get(key, 0) + 1 if above else 0
            self._hard_filter_misses[key] = self._hard_filter_misses.get(key, 0) + 1 if hard_fail else 0
            # Dos revisiones fuera habilitan la simulacion de recentrado; no fuerzan
            # por si solas cerrar/reabrir si mantener sigue siendo mejor.
            must_withdraw = self._hard_filter_misses[key] >= 2
            current_alternative = next((item for item in plan["alternatives"]
                if item["symbol"] == grid["symbol"] and item["strategy"] == grid["strategy"]
                and len(item.get("pieces", [])) == 1), None)
            is_alternative = best["symbol"] != grid["symbol"] or best["strategy"] != grid["strategy"]
            rotation_gate = self._rotation_gate(goal, grid, link, best, current_alternative, plan)
            reconfiguration = self._consider_piece_reconfiguration(
                goal, grid, levels, bid, plan, link, key)
            if reconfiguration is not None:
                return reconfiguration
            loss = 0.0
            close_fee = 0.0
            if has_inventory and is_alternative:
                loss = sum(max(0.0, float(level.get("entry_price") or level["price"]) * float(level["held_qty"]) -
                    float(level["held_qty"]) * bid * .999) for level in levels if float(level.get("held_qty") or 0) > 0)
                close_fee = sum(float(level.get("held_qty") or 0) * bid * .001
                                for level in levels if float(level.get("held_qty") or 0) > 0)
            expected = float(best["estimate"]["probability"]) * float(goal["target_usdt"])
            reopen_fee = float(grid["capital_total"]) * .001
            acceptable = loss_reallocation_allowed(expected, loss, close_fee, reopen_fee)
            current_p90 = math.inf if current_alternative is None else float(
                current_alternative["estimate"].get("days_p90") or math.inf)
            time_faster = (best["estimate"].get("days_median") is not None and
                (best["estimate"].get("days_p90") or math.inf) < current_p90)
            reason = ("fallo de filtros duros del Scanner en dos revisiones" if self._hard_filter_misses[key] >= 2 else
                      "precio sobre techo en dos revisiones" if self._above_ceiling_count[key] >= 2 else
                      "alternativa estimada más rápida y ganancia esperada cubre pérdida y comisiones")
            if must_withdraw and has_inventory and (self._hard_filter_misses[key] >= 2 or
                                                     self._above_ceiling_count[key] >= 2):
                openability = self._testnet_openability(best["pieces"][0], goal,
                    replacing_grid_id=int(grid["id"]), include_held_proceeds=False)
                if not openability["allowed"]:
                    blocked = f"alternativa no abrible en Testnet: {openability['reason']}"
                    self._ledger(goal["id"], "META_OPERATOR_ROTATION_REJECTED", {
                        "reason": blocked, "alternative": best, "openability": openability}, grid_id=int(grid["id"]))
                    return {"decision": "no_hacer_nada", "reason": blocked, "grid_id": int(grid["id"])}
                if not rotation_gate["allowed"]:
                    return {"decision": "no_hacer_nada", "reason": rotation_gate["reason"],
                        "grid_id": int(grid["id"]), "rotation_gate": rotation_gate}
                self.engine.cancel_grid_orders(int(grid["id"]), finalize=False, buy_only=True)
                self._ledger(goal["id"], "META_OPERATOR_WITHDRAW", {"reason": reason,
                    "scanner": scan_row, "price_bid": bid,
                    "decision": "cancelar_compras_conservar_ventas",
                    "alternative": best, "testnet_openability": openability}, grid_id=int(grid["id"]))
                self.alert("META_OPERATOR_WITHDRAW", goal["id"], {"symbol": grid["symbol"], "reason": reason})
                self._ledger(goal["id"], "META_OPERATOR_ROTATION", {"reason": reason,
                    "alternative": best, "testnet_openability": openability}, grid_id=int(grid["id"]))
                return {"decision": "retirar_con_ventas_vivas", "reason": reason,
                        "alternative_id": best.get("alternative_id"), "symbol": best.get("symbol"),
                        "grid_id": int(grid["id"]), "capital_reusable": False}
            if must_withdraw and not has_inventory:
                replacement = best
                if self._above_ceiling_count[key] >= 2 and scan_row.get("eligible"):
                    replacement = self._recenter_alternative(best, grid, bid)
                openability = self._testnet_openability(replacement["pieces"][0], goal,
                    replacing_grid_id=int(grid["id"]))
                if not openability["allowed"]:
                    blocked = f"alternativa no abrible en Testnet: {openability['reason']}"
                    self._ledger(goal["id"], "META_OPERATOR_ROTATION_REJECTED", {
                        "reason": blocked, "alternative": replacement, "openability": openability}, grid_id=int(grid["id"]))
                    return {"decision": "no_hacer_nada", "reason": blocked, "grid_id": int(grid["id"])}
                if not rotation_gate["allowed"]:
                    return {"decision": "no_hacer_nada", "reason": rotation_gate["reason"],
                        "grid_id": int(grid["id"]), "rotation_gate": rotation_gate}
                close = self.engine.close_grid(int(grid["id"]), "cancel", close_reason="META_OPERATOR_WITHDRAW")
                if close.get("status") != "CLOSED":
                    return {"decision": "no_hacer_nada", "reason": "el retiro verificado quedó pendiente",
                            "grid_id": int(grid["id"]), "close": close}
                self._ledger(goal["id"], "META_OPERATOR_ROTATION", {"reason": reason,
                    "alternative": replacement, "testnet_openability": openability}, grid_id=int(grid["id"]))
                self._ledger(goal["id"], "META_OPERATOR_WITHDRAW", {"reason": reason,
                    "scanner": scan_row, "price_bid": bid}, grid_id=int(grid["id"]))
                self.alert("META_OPERATOR_WITHDRAW", goal["id"], {"symbol": grid["symbol"], "reason": reason})
                try:
                    opened = self._open_replacement(goal, replacement, float(grid["capital_total"]))
                except Exception as exc:
                    self._ledger(goal["id"], "META_OPERATOR_OPEN_FAILED", {"reason": reason,
                        "piece": replacement.get("pieces", [None])[0], "error": str(exc)}, grid_id=int(grid["id"]))
                    return {"decision": "retirada_sin_reapertura", "reason": reason,
                            "error": str(exc), "closed_grid_id": int(grid["id"])}
                return {"decision": "retirar_y_abrir", "reason": reason,
                        "alternative_id": replacement.get("alternative_id"), "symbol": replacement.get("symbol"),
                        "closed_grid_id": int(grid["id"]),
                        "opened": opened}
            if has_inventory and is_alternative and time_faster and acceptable:
                openability = self._testnet_openability(best["pieces"][0], goal,
                    replacing_grid_id=int(grid["id"]))
                if not openability["allowed"]:
                    blocked = f"alternativa no abrible en Testnet: {openability['reason']}"
                    self._ledger(goal["id"], "META_OPERATOR_ROTATION_REJECTED", {
                        "reason": blocked, "alternative": best, "openability": openability}, grid_id=int(grid["id"]))
                    return {"decision": "no_hacer_nada", "reason": blocked, "grid_id": int(grid["id"])}
                if not rotation_gate["allowed"]:
                    return {"decision": "no_hacer_nada", "reason": rotation_gate["reason"],
                        "grid_id": int(grid["id"]), "rotation_gate": rotation_gate}
                close = self.engine.close_grid(int(grid["id"]), "liquidate", close_reason="META_OPERATOR_ROTATION")
                if close.get("status") != "CLOSED":
                    return {"decision": "no_hacer_nada", "reason": "cierre verificado pendiente",
                            "grid_id": int(grid["id"]), "close": close}
                actual_allowed, actual_costs = actual_close_reallocation_allowed(expected, close, reopen_fee)
                self._ledger(goal["id"], "META_OPERATOR_ROTATION", {"reason": reason,
                    "alternative": best, "testnet_openability": openability,
                    "rotation_gate": rotation_gate, "actual_costs": actual_costs}, grid_id=int(grid["id"]))
                self._ledger(goal["id"], "META_OPERATOR_CLOSE", {"reason": reason,
                    "estimated_gain_usdt": expected, "estimated_realized_loss_usdt": loss,
                    "estimated_close_fee_usdt": close_fee, "reopen_fee_usdt": reopen_fee,
                    "actual_costs": actual_costs, "reallocation_allowed_after_verified_close": actual_allowed,
                    "alternative": best}, grid_id=int(grid["id"]))
                self.alert("META_OPERATOR_CLOSE", goal["id"], {"symbol": grid["symbol"], "reason": reason})
                if not actual_allowed:
                    self._ledger(goal["id"], "META_OPERATOR_ROTATION_REJECTED", {
                        "reason": "la ganancia estimada no compensa el resultado liquidado y la reapertura",
                        "actual_costs": actual_costs, "expected_gain_usdt": expected,
                    }, grid_id=int(grid["id"]))
                    return {"decision": "cerrar_sin_reasignar", "reason": reason,
                            "alternative_id": best.get("alternative_id"), "symbol": best.get("symbol"),
                            "actual_costs": actual_costs, "expected_gain_usdt": expected}
                try:
                    opened = self._open_replacement(goal, best, float(grid["capital_total"]))
                except Exception as exc:
                    self._ledger(goal["id"], "META_OPERATOR_OPEN_FAILED", {"reason": reason,
                        "piece": best.get("pieces", [None])[0], "error": str(exc)}, grid_id=int(grid["id"]))
                    return {"decision": "cierre_sin_reapertura", "reason": reason,
                            "error": str(exc), "closed_grid_id": int(grid["id"])}
                return {"decision": "cerrar_y_reasignar", "reason": reason,
                        "alternative_id": best.get("alternative_id"), "symbol": best.get("symbol"), "loss_usdt": loss,
                        "expected_gain_usdt": expected, "close_fee_usdt": close_fee,
                        "reopen_fee_usdt": reopen_fee, "opened": opened}
        added = self._add_piece_from_free_cash(goal, links, plan, valuation=valuation)
        if added is not None:
            return added
        return {"decision": "no_hacer_nada", "reason": "ninguna alternativa supera los límites de reasignación",
                "alternatives": plan["alternatives"], "signals_used": plan.get("signals_used"),
                "shadow_decision": plan.get("shadow_decision")}

    def _consider_piece_reconfiguration(self, goal, grid, levels, bid, plan, link, key):
        """Reajusta una pieza Meta solo cuando la simulacion supera materialidad."""
        cfg = goal.get("config_snapshot") or {}
        piece_type = link.get("piece_type", "grid")
        is_bot = piece_type == "individual_bot_grid"
        if is_bot and (any(float(row.get("held_qty") or 0) > 0 for row in levels)
                or any(row.get("state") == "DONE" or int(row.get("cycles_completed") or 0) > 0
                       for row in levels)):
            return None  # Conserva intacta la venta viva del bot comprado.
        width = float(grid["range_high"]) - float(grid["range_low"])
        step = width / max(1, int(grid.get("n_levels") or 1))
        outside = (self._outside_range_count.get(key, 0) >= 2
            and (bid > float(grid["range_high"]) + step or bid < float(grid["range_low"]) - step))
        near_edge = min(abs(bid - float(grid["range_low"])),
                        abs(float(grid["range_high"]) - bid)) <= step
        if not (outside or near_edge):
            return None
        candidates = [row for row in plan.get("alternatives", [])
            if row.get("symbol") == grid["symbol"] and row.get("strategy") == grid["strategy"]
            and row.get("estimate", {}).get("evidence_sufficient") and row.get("pieces")]
        candidates = [row for row in candidates if (row["pieces"][0].get("piece_type") ==
            ("individual_bot_grid" if is_bot else "grid"))]
        if len(candidates) < 2:
            return None
        current = min(candidates, key=lambda row: abs(float(row["pieces"][0]["floor"])
            - float(grid["range_low"])) + abs(float(row["pieces"][0]["ceiling"])
            - float(grid["range_high"])))
        old_est = current["estimate"]
        tie = float(cfg.get("reconfig_probability_tie_tolerance_pp", 2.0))
        material_candidates = []
        for alternative in candidates:
            estimate = alternative["estimate"]
            probability_delta = (float(estimate.get("probability") or 0)
                - float(old_est.get("probability") or 0)) * 100
            previous_progress = float(old_est.get("median_progress_pct") or 0)
            next_progress = float(estimate.get("median_progress_pct") or 0)
            progress_improvement = ((next_progress / previous_progress - 1) * 100
                if previous_progress > 0 else (100.0 if next_progress > 0 else 0.0))
            if (probability_delta >= float(cfg.get("reconfig_min_probability_delta_pp", 5.0))
                    or (abs(probability_delta) <= tie and progress_improvement >=
                        float(cfg.get("reconfig_min_progress_delta_pct", 10.0)))):
                material_candidates.append((alternative, probability_delta, progress_improvement))
        if not material_candidates:
            return None
        candidate, prob_delta, progress_delta = max(material_candidates,
            key=lambda row: (float(row[0]["estimate"].get("probability") or 0),
                             float(row[0]["estimate"].get("median_progress_pct") or 0)))
        now = datetime.fromtimestamp(self.clock(), timezone.utc)
        recent, evaluations = [], []
        for row in self._meta_ledger_rows(int(goal["id"])):
            stamp = self._as_utc(row.get("ts"))
            if stamp is None or now - stamp > timedelta(hours=24):
                continue
            if int(row.get("grid_id") or -1) != int(grid["id"]):
                continue
            if row.get("event_type") == "META_OPERATOR_RECONFIGURE":
                recent.append(row)
            elif row.get("event_type") == "META_OPERATOR_RECONFIGURE_EVALUATED":
                evaluations.append(row.get("details") or {})
        key = self._alternative_key(candidate)
        selected = candidate["estimate"]
        policy = counterfactual_policy_decision(
            {"probability": old_est.get("probability_replay", old_est.get("probability", 0)),
             "median_progress_pct": old_est.get("median_progress_pct")},
            [{"probability": selected.get("probability_replay", selected.get("probability", 0)),
              "median_progress_pct": selected.get("median_progress_pct"), "alternative_key": key,
              "destination": candidate.get("alternative_id"), "action": "reajustar",
              "min_probability_delta_pp": cfg.get("reconfig_min_probability_delta_pp", 5),
              "probability_tie_tolerance_pp": cfg.get("reconfig_probability_tie_tolerance_pp", 2),
              "min_progress_delta_pct": cfg.get("reconfig_min_progress_delta_pct", 10)}],
            evaluations, {"last_action_at": max((stamp.timestamp() for stamp in
                (self._as_utc(row.get("ts")) for row in recent) if stamp), default=None),
                "actions_24h": len(recent)}, required_reviews=int(cfg.get("reconfig_persistence_reviews", 2)),
            cooldown_seconds=float(cfg.get("reconfig_cooldown_hours", 1)) * 3600,
            max_actions_per_24h=int(cfg.get("max_reconfigurations_per_24h", 4)), now=now.timestamp())
        self._ledger(goal["id"], "META_OPERATOR_RECONFIGURE_EVALUATED", {
            "alternative_key": key, "material": policy["material"], "streak": policy["streak"],
            "decision": policy["action"], "reason": policy["reason"],
            "probability_improvement_pp": policy["probability_improvement_pp"],
            "progress_improvement_pct": policy["progress_improvement_pct"]}, grid_id=int(grid["id"]))
        if policy["action"] != "reajustar":
            return None
        prob_delta = policy["probability_improvement_pp"]
        progress_delta = policy["progress_improvement_pct"]
        piece = candidate["pieces"][0]
        reason = ("precio fuera dos revisiones y desplazado mas de un nivel" if outside
                  else "precio a un nivel o menos del piso o techo")
        current_capital = float(grid.get("capital_total") or piece.get("capital_usdt") or 0)
        target_capital = float(piece.get("capital_usdt") or current_capital)
        if not is_bot and abs(target_capital - current_capital) > 1e-8:
            links = self.core._owned_grids(int(goal["id"]))
            used_by_symbol, used_total = {}, 0.0
            for owned in links:
                other = self.db.get_grid(int(owned["grid_id"]))
                if other is not None and other.get("status") in {"OPENING", "ACTIVE", "PAUSED", "CLOSING", "HOLDING"}:
                    amount = float(other.get("capital_total") or 0)
                    used_total += amount
                    used_by_symbol[other["symbol"]] = used_by_symbol.get(other["symbol"], 0.0) + amount
            free_cash = max(0.0, float(goal["capital_initial"]) - used_total)
            other_coin_capital = max(0.0, used_by_symbol.get(grid["symbol"], 0.0)
                - current_capital)
            coin_cap = float(goal["capital_initial"]) * float(cfg.get("capital_per_coin_pct", 100)) / 100
            if (target_capital > float(grid["capital_total"]) + free_cash + 1e-8
                    or target_capital + other_coin_capital > coin_cap + 1e-8):
                return None
        if is_bot:
            outcome = self.engine.reposition_meta_bot(int(grid["id"]), piece["floor"],
                piece["ceiling"], reason=reason)
        else:
            outcome = self.engine.adjust_grid(int(grid["id"]), piece["floor"],
                piece["ceiling"], int(piece["n_levels"]), reason="meta_operator_reconfigure",
                details={"reason": reason, "alternative_id": candidate.get("alternative_id")},
                new_capital_total=target_capital)
        if not outcome.get("ok") or not outcome.get("changed", outcome.get("ok")):
            return None
        details = {"decision": "reajustar_pieza", "piece_type": piece_type,
            "alternative_id": candidate.get("alternative_id"), "symbol": piece.get("symbol"),
            "old": {"floor": grid["range_low"], "ceiling": grid["range_high"], "n_levels": grid["n_levels"]},
            "new": {"floor": piece["floor"], "ceiling": piece["ceiling"],
                    "n_levels": piece.get("n_levels", 1)},
            "reason": reason, "alternative_key": key, "policy": policy,
            "estimated_probability_improvement_pp": prob_delta,
            "estimated_progress_improvement_pct": progress_delta,
            "alternatives_evaluated": [{"alternative_id": row.get("alternative_id"),
                "floor": row["pieces"][0].get("floor"), "ceiling": row["pieces"][0].get("ceiling"),
                "n_levels": row["pieces"][0].get("n_levels"),
                "probability": row["estimate"].get("probability"),
                "median_progress_pct": row["estimate"].get("median_progress_pct")} for row in candidates],
            "shadow_decision": plan.get("shadow_decision"), "execution": outcome}
        self._ledger(goal["id"], "META_OPERATOR_RECONFIGURE", details, grid_id=int(grid["id"]))
        return details

    def _add_piece_from_free_cash(self, goal, links, plan, *, valuation=None):
        cfg = goal.get("config_snapshot") or {}
        initial = float(goal["capital_initial"])
        active, used_by_symbol = [], {}
        for link in links:
            grid = self.db.get_grid(int(link["grid_id"]))
            if grid is None or grid.get("status") not in {"OPENING", "ACTIVE", "PAUSED", "CLOSING", "HOLDING"}:
                continue
            active.append((link, grid))
            used_by_symbol[grid["symbol"]] = used_by_symbol.get(grid["symbol"], 0.0) + float(grid["capital_total"])
        used = sum(float(grid["capital_total"]) for _, grid in active)
        allocatable = max(0.0, initial - used)
        if valuation is None and callable(getattr(self.core, "valuation", None)):
            try:
                valuation = self.core.valuation(int(goal["id"]))
            except Exception:
                valuation = None
        attributable_cash = (float(valuation.get("cash_usdt")) if valuation is not None
                             and valuation.get("cash_usdt") is not None else allocatable)
        free = max(0.0, min(allocatable, attributable_cash))
        grids = sum(link["piece_type"] == "grid" for link, _ in active)
        bots = len(active) - grids
        if free < 5.5 or (grids >= int(cfg.get("max_grids", 0)) and bots >= int(cfg.get("max_bots", 0))):
            return None
        for alternative in plan.get("alternatives", []):
            pieces = alternative.get("pieces", [])
            if len(pieces) != 1:
                continue
            piece = dict(pieces[0])
            kind = piece["piece_type"]
            if kind == "grid" and grids >= int(cfg.get("max_grids", 0)):
                continue
            if kind == "individual_bot_grid" and bots >= int(cfg.get("max_bots", 0)):
                continue
            coin_cap = initial * float(cfg.get("capital_per_coin_pct", 0)) / 100
            available_coin = max(0.0, coin_cap - used_by_symbol.get(piece["symbol"], 0.0))
            needed = float(piece["capital_usdt"])
            if needed < 5.5 or needed > free + 1e-9 or needed > available_coin + 1e-9:
                continue
            openability = self._testnet_openability(piece, goal)
            if not openability["allowed"]:
                self._ledger(goal["id"], "META_OPERATOR_OPEN_REJECTED", {
                    "reason": f"alternativa no abrible en Testnet: {openability['reason']}",
                    "piece": piece, "testnet_openability": openability})
                continue
            opened = self._open_replacement(goal, alternative, needed)
            decision = {"decision": "abrir_pieza_con_efectivo_libre",
                "alternative_id": alternative.get("alternative_id"), "symbol": piece.get("symbol"),
                "reason": "revisión de 15 minutos; hay capital atribuible libre",
                "free_before_usdt": free, "attributable_cash_usdt": attributable_cash,
                "allocated_reserve_free_usdt": allocatable, "piece": opened,
                "profile_limits": {"capital_per_coin_pct": cfg.get("capital_per_coin_pct"),
                    "max_grids": cfg.get("max_grids"), "max_bots": cfg.get("max_bots")}}
            self._ledger(goal["id"], "META_OPERATOR_OPEN", decision, grid_id=opened["grid_id"])
            self.alert("META_OPERATOR_OPEN", goal["id"], {"symbol": piece["symbol"], "reason": decision["reason"]})
            return decision
        return None

    def _open_replacement(self, goal, alternative, capital):
        piece = dict(alternative["pieces"][0])
        piece["capital_usdt"] = min(float(capital), float(goal["capital_initial"]) *
                                     float((goal.get("config_snapshot") or {}).get("capital_per_coin_pct", 100)) / 100)
        if piece["capital_usdt"] < 5.5:
            raise ValueError("capital libre inferior al mínimo de una pieza")
        if piece["piece_type"] == "individual_bot_grid":
            grid = self.engine.create_grid(piece["symbol"], piece["floor"], piece["ceiling"], 1,
                capital=piece["capital_usdt"], strategy="simple", meta_id=int(goal["id"]),
                meta_piece_type="individual_bot_grid")
            grid_id = int(grid["id"])
        elif self._opener is not None:
            result = self._opener(piece)
            grid_id = int(result["grid_id"])
        else:
            if piece["strategy"] == "smart":
                from grid.meta import smart_grid_params_for_goal
                params = smart_grid_params_for_goal(goal)
            else:
                params = None
            grid = self.engine.create_grid(piece["symbol"], piece["floor"], piece["ceiling"],
                piece["n_levels"], capital=piece["capital_usdt"], strategy=piece["strategy"],
                params=params, meta_id=int(goal["id"]))
            grid_id = int(grid["id"])
        self.core.link_grid(int(goal["id"]), grid_id, piece["piece_type"])
        self._ledger(goal["id"], "META_OPERATOR_OPEN", {"piece": piece, "grid_id": grid_id},)
        self.alert("META_OPERATOR_OPEN", goal["id"], {"symbol": piece["symbol"], "reason": alternative["reason"]})
        return {**piece, "grid_id": grid_id}

    @staticmethod
    def _recenter_alternative(alternative, grid, price):
        result = json.loads(json.dumps(alternative))
        piece = result["pieces"][0]
        width_ratio = (float(grid["range_high"]) - float(grid["range_low"])) / float(grid["open_price"])
        piece["symbol"] = grid["symbol"]
        piece["strategy"] = grid["strategy"]
        piece["floor"] = price * (1 - width_ratio / 2)
        piece["ceiling"] = price * (1 + width_ratio / 2)
        piece["mid_at_plan"] = price
        piece["piece_type"] = "grid" if int(grid["n_levels"]) >= 4 else "individual_bot_grid"
        piece["n_levels"] = int(grid["n_levels"]) if piece["piece_type"] == "grid" else 1
        result["symbol"] = grid["symbol"]
        result["strategy"] = grid["strategy"]
        result["pieces"] = [piece]
        return result

    def review_active(self):
        """Revisa una sola meta activa; el watchdog 60 s sigue siendo de MetaCore."""
        from sqlalchemy import select
        with self.db.engine.connect() as conn:
            row = conn.execute(select(self.db.meta_goals.c.id).where(
                self.db.meta_goals.c.status.in_(["ACTIVE", "PIECES_BLOCKED"])).order_by(
                    self.db.meta_goals.c.id)).first()
        return None if row is None else self.review_once(int(row[0]))

    def precompute_daily(self):
        """Precarga Advisor/replay de perfiles sin tomar el lock del monitor."""
        if not bool(getattr(self.settings, "meta_mode_enabled", False)):
            return {"status": "disabled", "reason": "Modo Meta apagado"}
        if not self._precompute_guard.acquire(blocking=False):
            return {"status": "skipped", "reason": "precálculo anterior sigue activo"}
        started = time.perf_counter()
        logger = logging.getLogger(__name__)
        try:
            # Renuncia CPU cuando la plataforma expone prioridad por hilo; el job
            # usa un solo worker y no puede exceder el límite de dos procesos.
            try:
                if hasattr(os, "setpriority") and hasattr(os, "PRIO_PROCESS"):
                    worker_id = threading.get_native_id()
                    current = os.getpriority(os.PRIO_PROCESS, worker_id)
                    os.setpriority(os.PRIO_PROCESS, worker_id, min(19, current + 5))
            except (AttributeError, OSError):
                pass
            worker = MetaOperator(self.core, self.db, self.scanner, self.engine, self.settings,
                advisor=self.advisor, alert=self.alert, clock=self.clock,
                price_drift_pct=self.price_drift_pct, cache_enabled=True, cache_writes=True)
            worker._low_priority_precompute = True
            before = 0
            try:
                from sqlalchemy import func, select
                with self.db.engine.connect() as conn:
                    before = conn.execute(select(func.count()).select_from(
                        self.db.meta_analysis_cache)).scalar_one()
            except Exception:
                pass
            configs = self.core._profile_settings()
            bases = {(100.0, 10.0)}
            try:
                from sqlalchemy import select
                with self.db.engine.connect() as conn:
                    rows = conn.execute(select(self.db.meta_goals.c.capital_initial,
                        self.db.meta_goals.c.target_usdt).where(self.db.meta_goals.c.status.in_(
                            ["DRAFT", "ACTIVE", "PIECES_BLOCKED"]))).all()
                if rows:
                    bases.update((float(row[0]), float(row[1])) for row in rows)
            except Exception:
                pass
            profile_results = []
            for capital, target in sorted(bases):
                for profile in ("PRUDENTE", "RAPIDO"):
                    if not bool(getattr(self.settings, "meta_mode_enabled", False)):
                        return {"status": "disabled", "reason": "Modo Meta apagado durante precálculo"}
                    cfg = dict(configs.get(profile, {}))
                    goal = {"id": 0, "capital_initial": capital, "target_usdt": target,
                        "target_input_type": "USDT", "profile": profile, "config_snapshot": cfg}
                    try:
                        worker._build_plan(goal, record=False)
                        profile_results.append({"capital_usdt": capital, "target_usdt": target,
                            "profile": profile, "status": "completed"})
                    except ValueError as exc:
                        # Un perfil sin candidatas no debe impedir calentar el otro.
                        message = str(exc)
                        status = ("no_candidates" if ("Ninguna moneda cumple" in message
                            or "No hay estructuras del Advisor" in message) else "failed")
                        profile_results.append({"capital_usdt": capital, "target_usdt": target,
                            "profile": profile, "status": status, "reason": message})
                    except Exception as exc:
                        profile_results.append({"capital_usdt": capital, "target_usdt": target,
                            "profile": profile, "status": "failed", "reason": str(exc)})
                    time.sleep(.05)
            after = before
            try:
                with self.db.engine.connect() as conn:
                    after = conn.execute(select(func.count()).select_from(
                        self.db.meta_analysis_cache)).scalar_one()
            except Exception:
                pass
            result = {"status": ("completed" if all(row["status"] == "completed"
                        for row in profile_results) else "partial"),
                      "profiles": profile_results,
                      "duration_s": round(time.perf_counter() - started, 3),
                      "cache_entries_before": int(before), "cache_entries_after": int(after),
                      "cache_entries_added": max(0, int(after) - int(before)), "workers": 1}
            logger.info("Meta daily precompute finished duration_s=%.3f cache_entries_before=%d cache_entries_after=%d",
                        result["duration_s"], result["cache_entries_before"], result["cache_entries_after"])
            return result
        except Exception:
            logger.exception("Falló el precálculo diario de Meta después de %.3f s", time.perf_counter() - started)
            return {"status": "failed", "duration_s": round(time.perf_counter() - started, 3)}
        finally:
            self._precompute_guard.release()

    def start(self):
        self.scheduler.add_job(self.review_active, "interval", minutes=15, id="meta_operator_review",
                               replace_existing=True, max_instances=2, coalesce=True)
        self.scheduler.add_job(self.precompute_daily, "cron", hour=8, minute=0, timezone="UTC",
                               id="meta_daily_precompute", replace_existing=True,
                               max_instances=1, coalesce=True)
        self.scheduler.add_job(self.run_learning_daily, "cron", hour=8, minute=30, timezone="UTC",
                               id="meta_learning_labels_daily", replace_existing=True,
                               max_instances=1, coalesce=True)
        if not self.scheduler.running:
            self.scheduler.start()

    def stop(self):
        if self.scheduler.running:
            self.scheduler.shutdown(wait=True)

