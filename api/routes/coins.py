"""API endpoints for the tradable coin registry."""
from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from config.models_config import ACTIVE_SYMBOL
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
    existing = db.get_coin(symbol)
    if existing is not None and existing["active"] == 1:
        raise HTTPException(status_code=409, detail=f"{symbol} ya está activo.")
    return db.add_or_reactivate_coin(symbol, body.notes)


@router.delete("/{symbol}")
def remove_coin(request: Request, symbol: str):
    db = request.app.state.db
    symbol = _normalize(symbol)
    if symbol == ACTIVE_SYMBOL:
        raise HTTPException(
            status_code=409,
            detail="XRPUSDT es el símbolo activo del predictor; cambiarlo es una decisión aparte.",
        )
    existing = db.get_coin(symbol)
    if existing is None or existing["active"] == 0:
        raise HTTPException(status_code=404, detail=f"{symbol} no existe o ya está inactivo.")
    _assert_no_open_grid(symbol)
    db.deactivate_coin(symbol)
    return {"symbol": symbol, "active": False}


def _assert_no_open_grid(symbol: str) -> None:
    # Fase 14 (Grid Engine): cuando exista la tabla de grids, lanzar 409 si hay un grid
    # abierto para este símbolo. Hoy no hay nada que chequear; no inventar tabla.
    return None


@router.get("")
def list_coins(request: Request):
    db, client = request.app.state.db, request.app.state.client
    coins = db.get_active_coins()
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
        })
    return result
