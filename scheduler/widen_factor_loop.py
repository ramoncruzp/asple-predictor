"""Daily light-weight persistence and optional guarded application of widening suggestions."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from config.models_config import (
    VOL_ARTIFACT_DIR, VOL_CHAMPIONS, VOL_HORIZONS, VOL_MODELS, VOL_SYMBOL,
    VOL_WIDEN_AUTO, VOL_WIDEN_DISAGREEMENT_PCT, VOL_WIDEN_K_ACTIVE,
)
from models.volatility.widen_factor import calculate_widen_factor


def _val_report(horizon):
    try:
        path = Path(VOL_ARTIFACT_DIR) / "consensus_xrp.json"
        report = json.loads(path.read_text(encoding="utf-8"))
        return report.get("report", {}).get("horizons", {}).get(str(horizon), {})
    except (OSError, json.JSONDecodeError):
        return {}


def compute_horizon(db, horizon, now=None, force=False):
    now = now or datetime.now(timezone.utc)
    report = _val_report(horizon)
    models = report.get("eligible_models", [])
    weights = report.get("weights", {})
    champion = VOL_CHAMPIONS[int(horizon)]
    calibrated = report.get("calibration_models", {}).get(champion, {}).get("calibrated", {})
    mse, bias = calibrated.get("mse_log"), calibrated.get("bias_log")
    sigma_ref = (max(0.0, float(mse) - float(bias) ** 2) ** .5
                 if mse is not None and bias is not None else None)
    if not models or not weights or sigma_ref is None or sigma_ref <= 0:
        return None
    previous = db.get_widen_factor_history(VOL_SYMBOL, horizon, limit=100)
    last = db.get_latest_widen_factor(VOL_SYMBOL, horizon)
    if not force:
        last_at = last.get("computed_at") if last else None
        if last_at is not None and last_at.tzinfo is None:
            last_at = last_at.replace(tzinfo=timezone.utc)
        count_query = getattr(db, "count_new_widen_verifications", None)
        new_mature = (count_query(VOL_SYMBOL, horizon, champion, last_at, now)
                      if count_query else 0)
        if new_mature < 24:
            return None
    rows = db.get_widen_factor_rows(VOL_SYMBOL, horizon, now)
    active = db.get_widen_active_values(
        VOL_SYMBOL, horizon, VOL_WIDEN_K_ACTIVE, VOL_WIDEN_DISAGREEMENT_PCT,
    )
    from models.volatility.model_stats import adaptive_weight_history
    adaptive = adaptive_weight_history(rows, VOL_MODELS, weights, now)
    current_weights = adaptive.get("P", weights) if adaptive.get("source") == "vivo" else weights
    current_models = adaptive.get("eligible", []) if adaptive.get("source") == "vivo" else models
    suggestion = calculate_widen_factor(
        rows, horizon, champion, current_weights, current_models, sigma_ref, now,
        previous=previous, k_active=active["k_active"],
        disagreement_pct_active=active["disagreement_pct_active"],
    )
    db.save_widen_factor_record({key: value for key, value in suggestion.items()
        if key in db.vol_widen_suggestions.c and key != "id"} | {"kind": "suggestion"})
    if VOL_WIDEN_AUTO:
        _maybe_auto_apply(db, horizon, suggestion, now, active)
    return suggestion


def _maybe_auto_apply(db, horizon, suggestion, now, active):
    if (suggestion.get("status") != "disponible" or suggestion.get("k_raw", 1.0) < 1.0
            or suggestion.get("k_stress_smoothed") is None):
        return False
    history = db.get_widen_factor_history(VOL_SYMBOL, horizon, limit=4)
    if len(history) < 4 or any(row.get("status") != "disponible" for row in history):
        return False
    values = [row.get("k_stress_smoothed") for row in history]
    if any(value is None for value in values) or max(values) - min(values) > .10:
        return False
    audit_rows = [row for row in db.get_widen_factor_audit_history(VOL_SYMBOL, horizon, limit=100)
                  if row.get("kind") == "auto_apply"]
    if audit_rows:
        last_at = audit_rows[-1]["computed_at"]
        if last_at.tzinfo is None:
            last_at = last_at.replace(tzinfo=timezone.utc)
        if now - last_at < timedelta(days=7):
            return False
    before = active["k_active"]
    desired = float(suggestion["k_stress_smoothed"])
    after = min(2.0, max(1.0, before + min(.10, max(-.10, desired - before))))
    if after == before:
        return False
    db.save_widen_factor_record({
        "symbol": VOL_SYMBOL, "horizon_h": int(horizon), "computed_at": now,
        "kind": "auto_apply", "n": suggestion["n"],
        "n_effective": suggestion["n_effective"], "k_active": after,
        "disagreement_pct_active": active["disagreement_pct_active"],
        "status": "aplicado", "audit_actor": "system:auto",
        "audit_before": json.dumps({"k_active": before}, sort_keys=True),
        "audit_after": json.dumps({"k_active": after}, sort_keys=True),
    })
    return True


class WidenFactorLoop:
    def __init__(self, db_manager, scheduler=None, hour_utc=3):
        from apscheduler.schedulers.background import BackgroundScheduler
        self.db_manager = db_manager
        self.scheduler = scheduler or BackgroundScheduler()
        self.hour_utc = int(hour_utc)
        self.logger = logging.getLogger(__name__)

    def run_cycle(self):
        for horizon in VOL_HORIZONS:
            try:
                result = compute_horizon(self.db_manager, horizon)
                if result:
                    self.logger.info("Widen suggestion horizon=%sh status=%s n=%d", horizon,
                                     result["status"], result["n"])
            except Exception:
                self.logger.exception("Widen-factor calculation failed for horizon=%s", horizon)

    def start(self):
        self.scheduler.add_job(self.run_cycle, "cron", hour=self.hour_utc, minute=15,
                               timezone="UTC", id="vol_widen_factor_daily", replace_existing=True)
        self.scheduler.start()

    def stop(self):
        if self.scheduler.running:
            self.scheduler.shutdown(wait=True)
