"""API endpoints for calibrated live realized-volatility forecasts."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from math import exp, sqrt

from fastapi import APIRouter, HTTPException, Query, Request

from config.models_config import (
    VOL_CHAMPIONS, VOL_HORIZONS, VOL_LIVE_MIN_VERIFIED, VOL_MODELS, VOL_SYMBOL,
)

router = APIRouter()


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
        outputs.append({
            "horizon_h": horizon,
            "champion": VOL_CHAMPIONS[horizon],
            "forecast_at": forecast_at,
            "made_at": made_at,
            "vol_per_hour": sigma_per_hour,
            "move_1sigma_pct": sigma_h * 100.0,
            "range_1sigma": [price * exp(-sigma_h), price * exp(sigma_h)],
            "range_2sigma": [price * exp(-2.0 * sigma_h), price * exp(2.0 * sigma_h)],
            "stale": now - forecast_at > timedelta(hours=2),
        })
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
