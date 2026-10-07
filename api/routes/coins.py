"""API endpoints for the tradable coin registry."""
from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from config.models_config import ACTIVE_SYMBOL, VOL_SYMBOL
from models.coin_onboarding import OnboardingArtifactsSaving, coin_is_ready, readiness_public
from data.binance_client import BinanceClient

router = APIRouter()

_AVAILABLE_CACHE: dict[str, Any] = {"symbols": None, "fetched_at": 0.0}
_AVAILABLE_TTL_SECONDS = 3600.0


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
    open_grids = {row["symbol"]: row for row in db.list_open_grids()}
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
            "readiness": readiness_public(db.get_readiness(symbol)),
            "ready": coin_is_ready(db,getattr(request.app.state,"vol_registry",None),symbol),
            "volatility_model": _champion_name(db, symbol),
        })
    return result


def _champion_name(db: Any, symbol: str) -> str | None:
    try:
        rows = db.get_vol_battle(symbol, 4)
        champions = [row for row in rows if row.get("is_champion")]
        return champions[0].get("model_name") if champions else "realizada" if rows else None
    except Exception:
        return None
