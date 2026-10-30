"""API endpoints for the tradable coin registry."""
from __future__ import annotations

import time
import threading
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from math import exp, sqrt
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from config.models_config import ACTIVE_SYMBOL, VOL_SYMBOL, vol_champions
from models.coin_onboarding import OnboardingArtifactsSaving, coin_is_ready, readiness_public
from data.binance_client import BinanceClient

router = APIRouter()

_AVAILABLE_CACHE: dict[str, Any] = {"symbols": None, "fetched_at": 0.0}
_AVAILABLE_TTL_SECONDS = 3600.0
_VOLATILITY_CACHE_TTL_SECONDS = 300.0
_VOLATILITY_CACHE_MAX_SYMBOLS = 32
_VOLATILITY_CACHE: OrderedDict[tuple[str, int], tuple[float, dict[str, Any]]] = OrderedDict()
_VOLATILITY_CANDLES_CACHE: OrderedDict[str, tuple[float, Any]] = OrderedDict()
_VOLATILITY_CACHE_LOCK = threading.RLock()


class CoinCreate(BaseModel):
    symbol: str
    notes: str | None = None


def _normalize(symbol: str) -> str:
    try:
        return BinanceClient._binance_symbol(symbol)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/available")
def available(request: Request):
    client = request.app.state.client
    now = time.monotonic()
    cached = _AVAILABLE_CACHE["symbols"]
    if cached is not None and now - _AVAILABLE_CACHE["fetched_at"] < _AVAILABLE_TTL_SECONDS:
        return cached
    try:
        pairs = client.get_supported_symbols()
    except Exception as exc:
        if cached is not None:
            return cached
        raise HTTPException(
            status_code=503, detail="Binance no respondió al listar símbolos disponibles."
        ) from exc
    symbols = sorted({pair.replace("/", "") for pair in pairs})
    _AVAILABLE_CACHE["symbols"] = symbols
    _AVAILABLE_CACHE["fetched_at"] = now
    return symbols


@router.post("", status_code=201)
def create_coin(request: Request, body: CoinCreate):
    db, client = request.app.state.db, request.app.state.client
    symbol = _normalize(body.symbol)
    try:
        status = client.get_symbol_status(symbol)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=f"Binance no respondió: {exc}") from exc
    if status is None:
        raise HTTPException(status_code=404, detail=f"El símbolo {symbol} no existe en Binance.")
    if status != "TRADING":
        raise HTTPException(
            status_code=422, detail=f"El símbolo {symbol} no está en estado TRADING (status={status})."
        )
    testnet = getattr(request.app.state, "testnet_client", None)
    if testnet is None:
        raise HTTPException(status_code=503, detail="No hay cliente Testnet para validar el símbolo.")
    try:
        testnet_info = testnet.get_symbol_info(symbol)
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Testnet no respondió al validar el símbolo.") from exc
    if not testnet_info:
        raise HTTPException(status_code=422, detail=f"{symbol} existe en Binance pero no en Testnet.")
    if testnet_info.get("status") != "TRADING":
        raise HTTPException(status_code=422, detail=f"{symbol} existe en Binance pero no está disponible para operar en Testnet.")
    existing = db.get_coin(symbol)
    if existing is not None and existing["active"] == 1:
        raise HTTPException(status_code=409, detail=f"{symbol} ya está activo.")
    coin=db.add_or_reactivate_coin(symbol,body.notes)
    if symbol!=VOL_SYMBOL and not coin_is_ready(db,getattr(request.app.state,"vol_registry",None),symbol):
        if db.get_readiness(symbol) is None:db.set_readiness(symbol,"pendiente",stage_detail="pendiente",progress_pct=0.0)
        service=getattr(request.app.state,"coin_onboarding_service",None)
        if service is not None:service.enqueue(symbol)
    return {**coin,"readiness":readiness_public(db.get_readiness(symbol))}


@router.post("/{symbol}/prepare", status_code=202)
def prepare_coin(request: Request, symbol: str):
    db = request.app.state.db
    symbol = _normalize(symbol)
    coin = db.get_coin(symbol)
    if coin is None or int(coin.get("active", 0)) != 1:
        raise HTTPException(404, detail=f"{symbol} no está activo en Coin Registry.")
    if symbol == VOL_SYMBOL:
        raise HTTPException(409, detail="XRPUSDT usa la preparación heredada.")
    service = getattr(request.app.state, "coin_onboarding_service", None)
    if service is None:
        raise HTTPException(503, detail="Servicio de preparación no disponible.")
    if service.is_training_active():
        raise HTTPException(409, detail="Hay un entrenamiento en curso; la preparación queda pendiente.")
    row = db.get_readiness(symbol)
    active = row and row.get("state") in {"descargando", "entrenando", "consensuando"}
    if active or service.in_progress(symbol) or coin_is_ready(db, None, symbol):
        raise HTTPException(409, detail="La moneda ya está en preparación o lista.")
    if row and row.get("state") not in {"pendiente", "error", "datos_insuficientes"}:
        raise HTTPException(409, detail="El estado actual no permite reintentar.")
    service.prepare(symbol)
    return {"symbol": symbol, "readiness": readiness_public(db.get_readiness(symbol))}


@router.get("/{symbol}/readiness")
def get_coin_readiness(request: Request, symbol: str):
    db = request.app.state.db
    symbol = _normalize(symbol)
    if db.get_coin(symbol) is None:
        raise HTTPException(404, detail=f"{symbol} no está registrado.")
    return {
        "symbol": symbol,
        "readiness": readiness_public(db.get_readiness(symbol)),
        "ready": coin_is_ready(db, getattr(request.app.state, "vol_registry", None), symbol),
    }


@router.get("/{symbol}/volatility")
def coin_volatility(request: Request, symbol: str, days: int = 7):
    """Resume velas públicas y estadísticas realizadas sin consultar la BD."""
    symbol = _normalize(symbol)
    if days not in {1, 7, 14, 30}:
        raise HTTPException(422, detail="days debe ser uno de: 1, 7, 14, 30")
    now = _volatility_now()
    cache_key = (symbol, int(days))
    mono = time.monotonic()
    with _VOLATILITY_CACHE_LOCK:
        cached = _VOLATILITY_CACHE.get(cache_key)
        if cached and mono - cached[0] < _VOLATILITY_CACHE_TTL_SECONDS:
            _VOLATILITY_CACHE.move_to_end(cache_key)
            return cached[1]
    try:
        candles = _volatility_hourly_candles(request, symbol, mono)
        result = _build_coin_volatility(candles, symbol, days, now,
            getattr(request.app.state, "vol_loop", None))
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(503, detail=f"No se pudieron obtener velas públicas para {symbol}.") from exc
    with _VOLATILITY_CACHE_LOCK:
        _VOLATILITY_CACHE[cache_key] = (time.monotonic(), result)
        _VOLATILITY_CACHE.move_to_end(cache_key)
        while len(_VOLATILITY_CACHE) > _VOLATILITY_CACHE_MAX_SYMBOLS * 4:
            _VOLATILITY_CACHE.popitem(last=False)
    return result


def _volatility_now():
    return datetime.now(timezone.utc)


def _volatility_hourly_candles(request: Request, symbol: str, mono: float):
    with _VOLATILITY_CACHE_LOCK:
        cached = _VOLATILITY_CANDLES_CACHE.get(symbol)
        if cached and mono - cached[0] < _VOLATILITY_CACHE_TTL_SECONDS:
            _VOLATILITY_CANDLES_CACHE.move_to_end(symbol)
            return cached[1]
    frame = request.app.state.client.get_historical_klines(symbol, "1h", 180)
    if not hasattr(frame, "columns"):
        import pandas as pd
        frame = pd.DataFrame(frame)
    required = {"timestamp", "open", "high", "low", "close"}
    if not required.issubset(frame.columns):
        raise ValueError("las velas deben contener timestamp y OHLC")
    frame = frame.loc[:, ["timestamp", "open", "high", "low", "close"]].copy()
    import pandas as pd
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True, errors="coerce")
    for name in ("open", "high", "low", "close"):
        frame[name] = pd.to_numeric(frame[name], errors="coerce")
    frame = frame.dropna().sort_values("timestamp").drop_duplicates("timestamp", keep="last")
    if frame.empty or (frame[["open", "high", "low", "close"]] <= 0).any().any():
        raise ValueError("no hay velas válidas para calcular volatilidad")
    with _VOLATILITY_CACHE_LOCK:
        _VOLATILITY_CANDLES_CACHE[symbol] = (time.monotonic(), frame)
        _VOLATILITY_CANDLES_CACHE.move_to_end(symbol)
        while len(_VOLATILITY_CANDLES_CACHE) > _VOLATILITY_CACHE_MAX_SYMBOLS:
            _VOLATILITY_CANDLES_CACHE.popitem(last=False)
    return frame


def _build_coin_volatility(candles, symbol: str, days: int, now: datetime, vol_loop):
    import numpy as np
    import pandas as pd

    cutoff = pd.Timestamp(now - timedelta(days=180))
    recent = candles.loc[(candles["timestamp"] >= cutoff) &
        (candles["timestamp"] <= pd.Timestamp(now))].copy()
    if len(recent) < 25:
        raise ValueError("historial horario insuficiente")
    period_start = pd.Timestamp(now - timedelta(days=days))
    period = recent.loc[recent["timestamp"] >= period_start].copy()
    if period.empty:
        raise ValueError("no hay velas dentro del período solicitado")
    first, last = float(period.iloc[0]["close"]), float(period.iloc[-1]["close"])
    high_row = period.loc[period["high"].idxmax()]
    low_row = period.loc[period["low"].idxmin()]
    high, low = float(high_row["high"]), float(low_row["low"])
    if low <= 0:
        raise ValueError("el mínimo del período debe ser positivo")
    change_pct = (last / first - 1) * 100
    range_usdt = high - low
    range_pct = range_usdt / low * 100

    period["day"] = period["timestamp"].dt.floor("D")
    daily = period.groupby("day", sort=True).agg(high=("high", "max"), low=("low", "min"))
    daily = daily.loc[daily["low"] > 0]
    daily["range_usdt"] = daily["high"] - daily["low"]
    daily["range_pct"] = daily["range_usdt"] / daily["low"] * 100
    if daily.empty:
        raise ValueError("no hay rangos diarios calculables")
    widest_day = daily["range_pct"].idxmax()

    history = recent.copy()
    history["day"] = history["timestamp"].dt.floor("D")
    daily_history = history.groupby("day", sort=True).agg(high=("high", "max"), low=("low", "min"))
    daily_history = daily_history.loc[daily_history["low"] > 0]
    history_ranges = ((daily_history["high"] - daily_history["low"]) /
        daily_history["low"] * 100).to_numpy(dtype=float)
    today_key = pd.Timestamp(now).floor("D")
    current_range = float(daily.loc[today_key, "range_pct"]) if today_key in daily.index else float(daily["range_pct"].iloc[-1])
    percentile = float(np.count_nonzero(history_ranges <= current_range) / len(history_ranges) * 100)

    hourly_close = recent["close"].to_numpy(dtype=float)
    log_returns = np.diff(np.log(hourly_close))
    if len(log_returns) < 24:
        sigma_24h = None
    else:
        sigma_24h = float(np.std(log_returns[-24:], ddof=1) * sqrt(24) * 100)
    period_close = period["close"].to_numpy(dtype=float)
    period_returns = np.diff(np.log(period_close))
    sigma_annualized = (float(np.std(period_returns, ddof=1) * sqrt(24 * 365) * 100)
        if len(period_returns) > 1 else None)

    latest = getattr(vol_loop, "latest_by_symbol", {}) if vol_loop is not None else {}
    forecast_rows = (latest.get(symbol) or {}).get("forecasts") or [] if isinstance(latest, dict) else []
    forecast_24h = next((row for row in forecast_rows if int(row.get("horizon_h") or 0) == 24
        and row.get("pred_logvol_cal") is not None), None)
    forecast_sigma = (float(exp(float(forecast_24h["pred_logvol_cal"])) * sqrt(24) * 100)
        if forecast_24h else None)

    price_rows = [{"t": int(row.timestamp.timestamp()), "close": float(row.close)}
        for row in period[["timestamp", "close"]].itertuples(index=False)]
    daily_rows = [{"date": stamp.strftime("%Y-%m-%d"), "range_usdt": float(row.range_usdt),
        "range_pct": float(row.range_pct)} for stamp, row in daily.iterrows()]
    return {"symbol": symbol, "days": int(days), "from": period_start.isoformat(),
        "to": now.isoformat(), "price_initial": first, "price_final": last,
        "change_pct": float(change_pct), "maximum": {"price": high,
            "at": pd.Timestamp(high_row["timestamp"]).isoformat(), "t": int(pd.Timestamp(high_row["timestamp"]).timestamp())},
        "minimum": {"price": low, "at": pd.Timestamp(low_row["timestamp"]).isoformat(), "t": int(pd.Timestamp(low_row["timestamp"]).timestamp())},
        "range_usdt": float(range_usdt), "range_pct": float(range_pct),
        "daily_range_avg_pct": float(daily["range_pct"].mean()),
        "widest_day": {"date": widest_day.strftime("%Y-%m-%d"),
            "range_pct": float(daily.loc[widest_day, "range_pct"])},
        "sigma_24h_realized_pct": sigma_24h, "sigma_period_annualized_pct": sigma_annualized,
        "current_range_percentile_180d": percentile,
        "sigma_24h_forecast_pct": forecast_sigma,
        "price_series": price_rows, "daily_ranges": daily_rows}


@router.post("/{symbol}/prepare/cancel")
def cancel_coin_preparation(request: Request, symbol: str):
    symbol = _normalize(symbol)
    service = getattr(request.app.state, "coin_onboarding_service", None)
    if service is None:
        raise HTTPException(503, detail="Servicio de preparación no disponible.")
    try:
        cancelled = service.cancel(symbol)
    except OnboardingArtifactsSaving as exc:
        raise HTTPException(409, detail=str(exc)) from exc
    if not cancelled:
        raise HTTPException(409, detail="No hay una preparación activa para cancelar.")
    db = request.app.state.db
    return {"symbol": symbol, "readiness": readiness_public(db.get_readiness(symbol))}

@router.delete("/{symbol}")
def remove_coin(request: Request, symbol: str):
    db = request.app.state.db
    symbol = _normalize(symbol)
    if symbol == ACTIVE_SYMBOL:
        raise HTTPException(
            status_code=409,
            detail="XRPUSDT es el símbolo activo del predictor; cambiarlo es una decisión aparte.",
        )
    existing=db.get_coin(symbol)
    service=getattr(request.app.state,"coin_onboarding_service",None)
    if service is not None and service.in_progress(symbol):
        raise HTTPException(status_code=409,detail=f"No se puede desactivar {symbol} mientras se prepara la volatilidad.")
    if existing is None or existing["active"] == 0:
        raise HTTPException(status_code=404, detail=f"{symbol} no existe o ya está inactivo.")
    _assert_no_open_grid(db, symbol)
    db.deactivate_coin(symbol)
    return {"symbol": symbol, "active": False}


def _assert_no_open_grid(db: Any, symbol: str) -> None:
    if db.has_open_grid(symbol):
        raise HTTPException(
            status_code=409,
            detail=f"No se puede desactivar {symbol} mientras tenga un grid abierto.",
        )

@router.get("")
def list_coins(request: Request):
    db, client = request.app.state.db, request.app.state.client
    include_inactive = request.query_params.get("include_inactive", "false").lower() == "true"
    coins = db.get_all_coins() if include_inactive else db.get_active_coins()
    open_grid_rows = db.list_open_grids()
    open_grids = {row["symbol"]: row for row in open_grid_rows}
    active_ranges: dict[str, list[dict[str, float]]] = {}
    for grid in open_grid_rows:
        if str(grid.get("status", "")).upper() != "ACTIVE":
            continue
        try:
            floor, ceiling = float(grid["range_low"]), float(grid["range_high"])
        except (TypeError, ValueError):
            continue
        active_ranges.setdefault(grid["symbol"], []).append({"floor": floor, "ceiling": ceiling})
    result = []
    for coin in coins:
        symbol = coin["symbol"]
        price = volume = change = None
        try:
            stats = client.get_24h_stats(symbol)
            price = stats["price"]
            volume = stats["volume_24h_quote"]
            change = stats["change_pct_24h"]
        except Exception:
            pass
        ready = coin_is_ready(db,getattr(request.app.state,"vol_registry",None),symbol)
        result.append({
            "symbol": symbol,
            "added_at": coin["added_at"],
            "notes": coin["notes"],
            "is_predictor_symbol": symbol == ACTIVE_SYMBOL,
            "price": price,
            "volume_24h_quote": volume,
            "change_pct_24h": change,
            "active": bool(coin["active"]),
            "open_grid_id": open_grids.get(symbol, {}).get("id"),
            "open_grid_ranges": active_ranges.get(symbol, []),
            "readiness": readiness_public(db.get_readiness(symbol)),
            "ready": ready,
            "volatility_model": _champion_name(db, symbol, ready=ready),
        })
    return result


def _champion_name(db: Any, symbol: str, *, ready: bool = True) -> str | None:
    if not ready:
        return None
    try:
        champions, provisional = vol_champions(symbol)
        name = champions.get(4)
        if not name:
            return None
        return f"{name} (provisional)" if provisional else name
    except Exception:
        return None
