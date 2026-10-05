"""API endpoints for calibrated live realized-volatility forecasts."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from math import exp, isfinite, log, sqrt
from pathlib import Path
import threading
import time

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from config.models_config import (
    VOL_ARTIFACT_DIR, VOL_CHAMPIONS, VOL_HORIZONS, VOL_LIVE_MIN_VERIFIED, VOL_MODELS, VOL_SYMBOL,
    VOL_WIDEN_AUTO, VOL_WIDEN_DISAGREEMENT_PCT, VOL_WIDEN_K_ACTIVE,
)
from models.volatility.consensus import dispersion_confidence, weighted_logvol
from models.volatility.model_stats import (
    N_MIN, calculate_model_stats, forward_consensus_metrics,
)

router = APIRouter()
_MODEL_STATS_CACHE_TTL_SECONDS = 30


class WidenApplyRequest(BaseModel):
    horizon_h: int
    confirm: bool = False
    k: float | None = None
    disagreement_pct: float | None = None


def _model_stats_rows(request: Request, symbol: str, horizon: int):
    db = request.app.state.db
    latest_query = getattr(db, "get_latest_vol_model_stats_forecast_at", None)
    latest = latest_query(symbol, horizon) if latest_query else None
    state = request.app.state
    if not hasattr(state, "vol_model_stats_cache"):
        state.vol_model_stats_cache = {}
        state.vol_model_stats_cache_lock = threading.Lock()
    key = (symbol, int(horizon))
    now_mono = time.monotonic()
    with state.vol_model_stats_cache_lock:
        entry = state.vol_model_stats_cache.get(key)
        if entry and entry["latest"] == latest and now_mono - entry["cached_at"] < _MODEL_STATS_CACHE_TTL_SECONDS:
            return entry["rows"]
    bounded_query = getattr(db, "get_vol_model_stats_rows", None)
    bounded_rows = bounded_query(symbol, horizon) if bounded_query else []
    query = getattr(db, "get_vol_model_stats_dispersion_rows", None)
    rows = query(symbol, horizon) if query else bounded_rows
    with state.vol_model_stats_cache_lock:
        state.vol_model_stats_cache[key] = {"latest": latest, "cached_at": now_mono, "rows": rows}
    return rows


def _vol_state(request: Request):
    predictor = getattr(request.app.state, "vol_predictor", None)
    loop = getattr(request.app.state, "vol_loop", None)
    if predictor is None or loop is None:
        raise HTTPException(
            status_code=503,
            detail="Volatilidad no disponible: faltan manifest o modelos entrenados.",
        )
    return predictor, loop


def _utc(value) -> datetime:
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _consensus_for_horizon(request: Request, symbol: str, horizon: int, rows: list[dict], price: float):
    if symbol != VOL_SYMBOL:
        return None
    path = Path(VOL_ARTIFACT_DIR) / "consensus_xrp.json"
    if not path.is_file():
        return {"consensus": None, "reason": "consensus_xrp.json no disponible"}
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"consensus": None, "reason": "no se pudo leer consensus_xrp.json"}
    horizon_report = report.get("report", {}).get("horizons", {}).get(str(horizon), {})
    eligible = horizon_report.get("eligible_models", [])
    weights = horizon_report.get("weights", {})
    if not eligible or not weights:
        return {"consensus": None, "reason": "no hay modelos elegibles en VAL"}
    horizon_rows = [item for item in rows if int(item["horizon_h"]) == horizon]
    by_model = {
        item["model_name"]: item for item in horizon_rows
        if item.get("pred_logvol_cal") is not None
    }
    if not by_model:
        return {"consensus": None, "reason": "no hay predicciones recientes para el horizonte"}
    missing = [name for name in eligible if name not in by_model]
    if missing:
        return {"consensus": None, "reason": "faltan pronósticos recientes de modelos elegibles"}
    oldest_eligible = min(_utc(by_model[name]["forecast_at"]) for name in eligible)
    if datetime.now(timezone.utc) - oldest_eligible > timedelta(hours=2):
        return {"consensus": None, "reason": "las predicciones de volatilidad están obsoletas"}
    factor_info = horizon_report.get("ensemble_calibration_val", {}).get("P", {})
    history_rows = _model_stats_rows(request, symbol, horizon)
    adaptive = calculate_model_stats(
        history_rows, VOL_MODELS, VOL_CHAMPIONS,
        _sigma_refs(horizon_report), weights, datetime.now(timezone.utc),
        horizon_report.get("eligible_models", VOL_MODELS),
    )
    forward = forward_consensus_metrics(
        history_rows, VOL_MODELS, VOL_CHAMPIONS, weights, datetime.now(timezone.utc),
        _ensemble_sigma_ref(horizon_report),
    )
    adaptive = adaptive["adaptive"]
    adaptive["validation_status_live"] = forward["validation_status_live"]
    selected_weights = adaptive.get("P", weights) if adaptive.get("source") == "vivo" else weights
    selected_models = adaptive.get("eligible", []) if adaptive.get("source") == "vivo" else eligible
    missing_selected = [name for name in selected_models if name not in by_model]
    if missing_selected:
        return {"consensus": None, "reason": "faltan pronósticos recientes para los modelos elegibles"}
    selected_times = [_utc(by_model[name]["forecast_at"]) for name in selected_models]
    if selected_times and datetime.now(timezone.utc) - min(selected_times) > timedelta(hours=2):
        return {"consensus": None, "reason": "las predicciones de volatilidad están obsoletas"}
    values = {name: float(by_model[name]["pred_logvol_cal"]) for name in selected_models}
    if factor_info.get("applied") and factor_info.get("factor"):
        # P is a variance calibration factor; log-vol is log standard deviation.
        for name in values:
            values[name] += 0.5 * log(float(factor_info["factor"]))
    if not values or sum(selected_weights.get(name, 0.0) for name in values) <= 0:
        return {"consensus": None, "reason": "faltan pronósticos de modelos elegibles"}
    logvol = weighted_logvol(values, selected_weights)
    if logvol is None:
        return {"consensus": None, "reason": "no se pudo combinar los pronósticos"}
    dispersion = dispersion_confidence(values, selected_models)
    sigma = exp(logvol) * sqrt(horizon)
    status_live = "validated" if adaptive.get("validation_status_live") == "validated" else "en_evaluacion"
    status_19a = horizon_report.get("ensemble_results_test", {}).get("P", {}).get(
        "validation_status", "not_validated"
    )
    return {"consensus": {
        "method": "weighted",
        "sigma_pct": sigma * 100.0,
        "range_1sigma": [price * exp(-sigma), price * exp(sigma)],
        "range_2sigma": [price * exp(-2.0 * sigma), price * exp(2.0 * sigma)],
        "dispersion_iqr": dispersion["dispersion_iqr"],
        "confidence": dispersion["confidence"],
        "validation_status": status_19a,
        "validation_status_live": status_live,
        "fuente_pesos": adaptive.get("source", "val"),
        "eligible_models": selected_models,
        "weights": selected_weights,
    }}


def _load_horizon_report(horizon: int):
    path = Path(VOL_ARTIFACT_DIR) / "consensus_xrp.json"
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return report.get("report", {}).get("horizons", {}).get(str(horizon), {})


def _sigma_refs(horizon_report: dict) -> dict[str, float]:
    refs = {}
    for name, model in horizon_report.get("calibration_models", {}).items():
        calibrated = model.get("calibrated", {})
        mse, bias = calibrated.get("mse_log"), calibrated.get("bias_log")
        if mse is not None and bias is not None:
            refs[name] = sqrt(max(0.0, float(mse) - float(bias) ** 2))
    return refs


def _ensemble_sigma_ref(horizon_report: dict) -> float | None:
    calibrated = horizon_report.get("ensemble_calibration_val", {}).get("P", {}).get("calibrated", {})
    mse, bias = calibrated.get("mse_log"), calibrated.get("bias_log")
    return sqrt(max(0.0, float(mse) - float(bias) ** 2)) if mse is not None and bias is not None else None


@router.get("/forecast")
def forecast(request: Request, symbol: str = VOL_SYMBOL):
    predictor, loop = _vol_state(request)
    symbol = symbol.strip().upper().replace("/", "")
    if symbol != predictor.symbol:
        raise HTTPException(status_code=404, detail=f"No hay artefactos de volatilidad para {symbol}")
    rows = request.app.state.db.get_latest_vol_forecasts(symbol)
    champion_rows = {
        int(row["horizon_h"]): row for row in rows if bool(row["is_champion"])
    }
    price = loop.latest.get("price")
    if price is None or not champion_rows:
        raise HTTPException(status_code=503, detail="Aún no hay pronósticos de volatilidad disponibles.")
    price = float(price)
    now = datetime.now(timezone.utc)
    outputs = []
    for horizon in VOL_HORIZONS:
        row = champion_rows.get(horizon)
        if row is None:
            continue
        forecast_at = _utc(row["forecast_at"])
        made_at = _utc(row["made_at"])
        sigma_per_hour = exp(float(row["pred_logvol_cal"]))
        sigma_h = sigma_per_hour * sqrt(horizon)
        item = {
            "horizon_h": horizon,
            "champion": VOL_CHAMPIONS[horizon],
            "forecast_at": forecast_at,
            "made_at": made_at,
            "vol_per_hour": sigma_per_hour,
            "move_1sigma_pct": sigma_h * 100.0,
            "range_1sigma": [price * exp(-sigma_h), price * exp(sigma_h)],
            "range_2sigma": [price * exp(-2.0 * sigma_h), price * exp(2.0 * sigma_h)],
            "stale": now - forecast_at > timedelta(hours=2),
        }
        consensus = _consensus_for_horizon(request, symbol, horizon, rows, price)
        if consensus:
            item.update(consensus)
        outputs.append(item)
    if not outputs:
        raise HTTPException(status_code=503, detail="No hay campeones con pronóstico guardado.")
    pcts = predictor.manifest.get("regime_percentiles_24h", {})
    champion_24 = champion_rows.get(24)
    regime = None
    if champion_24 is not None and pcts.get("p33") is not None and pcts.get("p66") is not None:
        vol_24 = exp(float(champion_24["pred_logvol_cal"]))
        regime = (
            "CALMA" if vol_24 < float(pcts["p33"])
            else "NORMAL" if vol_24 < float(pcts["p66"])
            else "AGITADO"
        )
    return {
        "symbol": symbol,
        "price": price,
        "regime": regime,
        "regime_percentiles_24h": pcts,
        "forecasts": outputs,
    }


@router.get("/model-stats")
def model_stats(
    request: Request,
    symbol: str = VOL_SYMBOL,
    horizon: int | None = Query(None, ge=1, le=24),
):
    predictor, _loop = _vol_state(request)
    symbol = symbol.strip().upper().replace("/", "")
    if symbol != predictor.symbol:
        raise HTTPException(status_code=404, detail=f"No hay estadísticas de volatilidad para {symbol}")
    horizons = [horizon] if horizon is not None else VOL_HORIZONS
    if any(value not in VOL_HORIZONS for value in horizons):
        raise HTTPException(status_code=404, detail="Horizonte de volatilidad no disponible")
    result = []
    now = datetime.now(timezone.utc)
    for current_horizon in horizons:
        report = _load_horizon_report(current_horizon) or {}
        val_weights = report.get("weights", {})
        rows = _model_stats_rows(request, symbol, current_horizon)
        aggregate_query = getattr(request.app.state.db, "get_vol_model_stats_aggregates", None)
        sigma_refs = _sigma_refs(report)
        aggregates = (aggregate_query(symbol, current_horizon, sigma_refs, now)
                      if aggregate_query else None)
        stats = calculate_model_stats(
            rows, VOL_MODELS, VOL_CHAMPIONS, sigma_refs, val_weights, now,
            report.get("eligible_models", VOL_MODELS), aggregates,
        )
        stats["forward"] = forward_consensus_metrics(
            rows, VOL_MODELS, VOL_CHAMPIONS, val_weights, now, _ensemble_sigma_ref(report),
        )
        stats["n"] = stats["forward"].get("n", 0)
        stats["n_efectivas"] = stats["forward"].get("n_efectivas", 0)
        stats["validation_status_live"] = stats["forward"]["validation_status_live"]
        adaptive = stats["adaptive"]
        for model in stats["models"]:
            name = model["model_name"]
            model["peso_actual"] = (
                adaptive.get("P", {}).get(name, 0.0)
                if model["estado"] == "activo" and name in adaptive.get("eligible", [])
                and adaptive.get("source") == "vivo" else 0.0
            )
            model["peso_respaldo_val"] = adaptive.get("P", {}).get(name, 0.0) if adaptive.get("source") == "val" else None
            model["peso_p2"] = adaptive.get("P2", {}).get(name, 0.0)
            model["fuente_pesos"] = adaptive.get("source", "val")
            model["razon_peso"] = (
                "acumulando: requiere 30 verificaciones y MSE no superior a Persistence"
                if model["estado"] != "activo" or name not in adaptive.get("eligible", [])
                or adaptive.get("source") != "vivo"
                else None
            )
        stats["horizon_h"] = current_horizon
        stats["symbol"] = symbol
        stats["n_min"] = N_MIN
        stats["widen_factor"] = _widen_horizon_payload(request, current_horizon)
        stats["adaptive"]["validation_status_live"] = stats["forward"]["validation_status_live"]
        result.append(stats)
    return {"symbol": symbol, "horizons": result}


def _widen_horizon_payload(request, horizon):
    db = request.app.state.db
    active_query = getattr(db, "get_widen_active_values", None)
    active = (active_query(VOL_SYMBOL, horizon, VOL_WIDEN_K_ACTIVE, VOL_WIDEN_DISAGREEMENT_PCT)
              if active_query else {"k_active": VOL_WIDEN_K_ACTIVE,
                                    "disagreement_pct_active": VOL_WIDEN_DISAGREEMENT_PCT})
    latest_query = getattr(db, "get_latest_widen_factor", None)
    latest = latest_query(VOL_SYMBOL, horizon) if latest_query else None
    history_query = getattr(db, "get_widen_factor_history", None)
    history = history_query(VOL_SYMBOL, horizon, limit=5) if history_query else []
    if latest is None:
        latest = {"symbol": VOL_SYMBOL, "horizon_h": horizon, "n": 0,
                  "n_effective": 0.0, "status": "acumulando", "progress_pct": 0.0,
                  "disagreement_status": "acumulando", "disagreement_progress": 0.0,
                  "k_stress_smoothed": None, "k_global": None, "k_stress_raw": None,
                  "k_raw": None, "bias_log": None, "vol_scale_suggested": None,
                  "overestimate_message": None,
                  "ci_low": None, "ci_high": None, "ci_width": None,
                  "k_global_ci_low": None, "k_global_ci_high": None,
                  "k_global_ci_width": None, "days_estimated": None,
                  "disagreement_threshold_suggested": None}
    return {**latest, **active, "history": history}


@router.get("/widen-factor")
def get_widen_factor(request: Request, symbol: str = VOL_SYMBOL):
    symbol = symbol.strip().upper().replace("/", "")
    if symbol != VOL_SYMBOL:
        raise HTTPException(status_code=404, detail="Símbolo de ampliación no disponible")
    return {"symbol": symbol, "auto_enabled": VOL_WIDEN_AUTO,
            "horizons": [_widen_horizon_payload(request, h) for h in VOL_HORIZONS]}


@router.post("/widen-factor/compute")
def compute_widen_factor_now(request: Request):
    from scheduler.widen_factor_loop import compute_horizon
    results = []
    for horizon in VOL_HORIZONS:
        try:
            result = compute_horizon(request.app.state.db, horizon, force=True)
            if result is not None:
                results.append(result)
        except Exception:
            import logging
            logging.getLogger(__name__).exception("Manual widen-factor computation failed: horizon=%s", horizon)
            raise HTTPException(status_code=503, detail=f"No se pudo calcular horizonte {horizon} h")
    return {"computed": len(results), "horizons": results}


@router.post("/widen-factor/apply")
def apply_widen_factor(request: Request, body: WidenApplyRequest):
    from datetime import datetime, timezone
    if not body.confirm:
        raise HTTPException(status_code=422, detail="Se requiere confirm=true")
    if body.horizon_h not in VOL_HORIZONS:
        raise HTTPException(status_code=422, detail="Horizonte no permitido")
    if body.k is None and body.disagreement_pct is None:
        raise HTTPException(status_code=422, detail="Indica k o disagreement_pct")
    if body.k is not None and (not isfinite(body.k) or not 1.0 <= body.k <= 2.0):
        raise HTTPException(status_code=422, detail="k debe estar entre 1,00 y 2,00")
    if body.disagreement_pct is not None and (not isfinite(body.disagreement_pct)
                                              or not 0.0 < body.disagreement_pct <= 100.0):
        raise HTTPException(status_code=422, detail="disagreement_pct debe estar entre 0 y 100")
    db = request.app.state.db
    latest = db.get_latest_widen_factor(VOL_SYMBOL, body.horizon_h)
    if latest is None:
        raise HTTPException(status_code=409, detail="Aún no hay una sugerencia calculada")
    if body.k is not None and latest.get("status") != "disponible":
        raise HTTPException(status_code=409, detail="El factor solo se aplica cuando el estado es disponible")
    if body.k is not None and latest.get("k_raw") is not None and float(latest["k_raw"]) < 1.0:
        raise HTTPException(status_code=409, detail="El modelo sobreestima la volatilidad; no se sugiere ampliar")
    if body.disagreement_pct is not None and latest.get("disagreement_status") != "disponible":
        raise HTTPException(status_code=409, detail="El umbral requiere al menos 200 instantes")
    active = db.get_widen_active_values(
        VOL_SYMBOL, body.horizon_h, VOL_WIDEN_K_ACTIVE, VOL_WIDEN_DISAGREEMENT_PCT,
    )
    before = dict(active)
    after = {
        "k_active": float(body.k if body.k is not None else active["k_active"]),
        "disagreement_pct_active": float(body.disagreement_pct if body.disagreement_pct is not None
                                          else active["disagreement_pct_active"]),
    }
    host = request.client.host if request.client else "unknown"
    db.save_widen_factor_record({
        "symbol": VOL_SYMBOL, "horizon_h": body.horizon_h,
        "computed_at": datetime.now(timezone.utc), "kind": "apply",
        "n": int(latest.get("n") or 0), "n_effective": float(latest.get("n_effective") or 0),
        **after, "status": "aplicado", "audit_actor": f"api-client:{host}",
        "audit_before": json.dumps(before, sort_keys=True),
        "audit_after": json.dumps(after, sort_keys=True),
    })
    return {"applied": True, "horizon_h": body.horizon_h, "before": before, "after": after}


@router.get("/battle")
def battle(
    request: Request,
    symbol: str = VOL_SYMBOL,
    horizon: int = Query(4, ge=1, le=24),
):
    predictor, _loop = _vol_state(request)
    symbol = symbol.strip().upper().replace("/", "")
    if symbol != predictor.symbol or horizon not in VOL_HORIZONS:
        raise HTTPException(status_code=404, detail="Símbolo u horizonte de volatilidad no disponible")
    manifest_models = predictor.manifest.get("horizons", {}).get(str(horizon), {})
    live_models = {
        item["model_name"]: item
        for item in request.app.state.db.get_vol_battle(symbol, horizon)
    }
    result = []
    for model_name in VOL_MODELS:
        live = live_models.get(model_name, {})
        n_verified = int(live.get("n_verified", 0))
        metric = manifest_models.get(model_name, {})
        enough = n_verified >= VOL_LIVE_MIN_VERIFIED
        result.append({
            "model_name": model_name,
            "r2_cal": metric.get("r2_cal"),
            "qlike_cal": metric.get("qlike_cal"),
            "n_verified": n_verified,
            "r2_live": live.get("r2_live") if enough else None,
            "mse_live": live.get("mse_live") if enough else None,
            "is_champion": bool(live.get("is_champion", VOL_CHAMPIONS.get(horizon) == model_name)),
        })
    return {"symbol": symbol, "horizon_h": horizon, "models": result}


@router.get("/history")
def history(
    request: Request,
    symbol: str = VOL_SYMBOL,
    horizon: int = Query(4, ge=1, le=24),
    model: str = Query("GBM"),
    limit: int = Query(200, ge=1, le=1000),
):
    predictor, _loop = _vol_state(request)
    symbol = symbol.strip().upper().replace("/", "")
    if symbol != predictor.symbol or horizon not in VOL_HORIZONS or model not in VOL_MODELS:
        raise HTTPException(status_code=404, detail="Símbolo, horizonte o modelo de volatilidad no disponible")
    return request.app.state.db.get_vol_history(symbol, horizon, model, limit)
