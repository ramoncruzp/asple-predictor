"""Explainable grid scan and guarded Testnet grid opening API."""
from __future__ import annotations

import hmac
import ipaddress
from decimal import Decimal
from typing import Literal

import numpy as np
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator

from grid.levels import GridConfigError, compute_lines, plan_cells
from grid.policy import validate_params
from grid.scan_service import EXECUTION_WARNING
from grid.structure import suggest_structure
from grid.guards import sell_level_conflicts, sell_level_conflict_message

router = APIRouter()


def _mid(market: dict) -> Decimal:
    return (Decimal(str(market["bid"])) + Decimal(str(market["ask"]))) / Decimal(2)


class ScanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbols: list[str] | None = Field(default=None, max_length=100)
    capital: Decimal = Field(gt=0)
    strategy: Literal["simple", "smart"] = "simple"
    with_sim: StrictBool = False


class OpenRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbol: str = Field(min_length=5, max_length=20)
    strategy: Literal["simple", "smart"] = "simple"
    capital: Decimal = Field(gt=0)
    from_scan: StrictBool = False
    range_low: Decimal | None = Field(default=None, gt=0)
    range_high: Decimal | None = Field(default=None, gt=0)
    n_levels: int | None = Field(default=None, ge=4, le=60)
    target_pct: Decimal | None = Field(default=None, gt=0, le=100)
    target_usdt: Decimal | None = Field(default=None, gt=0)
    target_basis: Literal["cash", "equity"] = "cash"
    max_days: Decimal | None = Field(default=None, gt=0)
    params: dict | None = None
    dry_run: StrictBool = True
    confirm: StrictBool = False

    @model_validator(mode="after")
    def valid_range(self):
        if self.range_low is not None and self.range_high is not None and self.range_low >= self.range_high:
            raise ValueError("range_low debe ser menor que range_high")
        if self.target_pct is not None and self.target_usdt is not None:
            raise ValueError("elige target_pct o target_usdt, no ambos")
        return self


from api.auth import authorize as _authorize

def _scan_snapshot(request: Request, symbol: str) -> dict | None:
    last = getattr(request.app.state.grid_scan_service, "last_scan", None)
    if not last:
        return None
    return next((row for row in last["results"] if row.get("symbol") == symbol and row.get("eligible")), None)


def _reject(db, status: int, message: str, symbol: str, body: OpenRequest):
    try:
        db.add_grid_event(run_id=None, source="CLI", event_type="GRID_OPEN_REJECTED",
            reason=message, details={"who": "api", "symbol": symbol, "strategy": body.strategy,
                "capital": str(body.capital), "params": body.params or {}, "dry_run": body.dry_run})
    except Exception:
        pass
    raise HTTPException(status, message)


@router.post("/scan")
def scan(request: Request, body: ScanRequest):
    _authorize(request)
    try:
        service = request.app.state.grid_scan_service
        result = service.scan(
            symbols=body.symbols, capital=body.capital, strategy=body.strategy, with_sim=body.with_sim)
        fee_pct = float(getattr(getattr(service, "settings", None), "scanner_fee_pct",
            getattr(request.app.state.settings, "scanner_fee_pct", 0.1)))
        return {**result, "results": [{**row, "fee_pct": fee_pct} for row in result.get("results", [])]}
    except Exception as exc:
        raise HTTPException(503, f"No se pudo completar el scan: {exc}") from exc


@router.post("/open")
def open_grid(request: Request, body: OpenRequest):
    _authorize(request)
    symbol = body.symbol.strip().upper().replace("/", "")
    if not symbol.endswith("USDT") or len(symbol) <= 4:
        db = request.app.state.db
        _reject(db, 422, "symbol debe ser un par USDT válido.", symbol, body)
    db, engine, client = request.app.state.db, request.app.state.grid_engine, request.app.state.testnet_client
    coin = db.get_coin(symbol)
    if not coin or int(coin.get("active", 0)) != 1:
        _reject(db, 422, "El símbolo debe estar activo en Coin Registry.", symbol, body)
    maximum = int(request.app.state.settings.max_grids_simultaneos)
    if db.count_open_grids() >= maximum:
        _reject(db, 409, f"Se alcanzó max_grids_simultaneos ({maximum}).", symbol, body)
    scan_row = _scan_snapshot(request, symbol) if body.from_scan else None
    if body.from_scan and scan_row is None:
        _reject(db, 422, "No hay resultado elegible reciente del scan para este símbolo.", symbol, body)
    params = dict(body.params or {})
    if body.target_pct is not None:
        params["target_pct"] = float(body.target_pct)
    if body.target_usdt is not None:
        params["target_usdt"] = float(body.target_usdt)
    if body.target_pct is not None or body.target_usdt is not None:
        params["target_basis"] = body.target_basis
    if body.max_days is not None:
        params["max_days"] = float(body.max_days)
    if body.strategy == "simple" and (body.target_pct is not None or body.target_usdt is not None):
        _reject(db, 422, "Los objetivos de cierre requieren strategy=smart en el motor actual.", symbol, body)
    if body.strategy == "smart":
        provider = getattr(request.app.state, "vol_provider", None)
        if provider is None or provider.get(symbol) is None:
            _reject(db, 422, f"smart requiere sigma disponible para {symbol}.", symbol, body)
    if params:
        try:
            validate_params(params, body.n_levels or 10)
        except (ValueError, TypeError) as exc:
            _reject(db, 422, f"Parámetros de grid inválidos: {exc}", symbol, body)
        if body.strategy == "simple" and set(params) != {"max_days"}:
            _reject(db, 422, "El motor simple solo admite max_days; use strategy=smart para otros parámetros.", symbol, body)
    if body.dry_run and body.confirm:
        _reject(db, 422, "confirm solo se acepta junto con dry_run=false.", symbol, body)
    if not body.dry_run and not body.confirm:
        _reject(db, 422, "confirm=true es obligatorio para ejecutar una apertura.", symbol, body)
    if not body.dry_run and any(value is None for value in (body.range_low, body.range_high, body.n_levels)):
        _reject(db, 422, "ejecuta primero dry_run y reenvía rango y niveles.", symbol, body)
    if body.dry_run:
        try:
            market, filters = request.app.state.grid_scan_service._market(
                symbol, float(body.capital), request.app.state.grid_scan_service.clock() +
                float(request.app.state.settings.scanner_timeout_seconds))
            closes = market["klines_1h"]["close"].astype(float).tolist()
            sigma = float(np.std(np.diff(np.log(closes)), ddof=1) * np.sqrt(24)) if len(closes) > 2 else 0
            mid = _mid(market)
            structure = suggest_structure(sigma, body.capital, mid,
                filters, float(request.app.state.settings.scanner_fee_pct),
                min_spacing_pct=float(request.app.state.settings.scanner_min_spacing_pct),
                min_cell_usdt=max(filters.min_notional * Decimal("1.1"), Decimal("5.5")))
            low = body.range_low or structure["range_low"]
            high = body.range_high or structure["range_high"]
            n = body.n_levels or structure["n_levels"]
            if not structure["feasible"] and (body.range_low is None or body.range_high is None or body.n_levels is None):
                raise ValueError("No hay estructura factible; proporcione rango y niveles manualmente.")
            lines = compute_lines(low, high, n, filters)
            cells = plan_cells(lines, body.capital,
                {"bid_price": Decimal(str(market["bid"])), "ask_price": Decimal(str(market["ask"])),
                 "avg_price": mid}, filters,
                request.app.state.settings)
            existing_levels = []
            for status in ("OPENING", "ACTIVE", "PAUSED", "CLOSING", "HOLDING"):
                for existing in db.list_grids_by_status({status}):
                    if existing.get("symbol") == symbol:
                        existing_levels.extend(db.get_grid_levels(int(existing["id"])))
            conflicts = sell_level_conflicts(
                [cell.sell_price for cell in cells], existing_levels, filters.tick_size,
                getattr(request.app.state.settings, "same_coin_sell_tolerance_pct", 0.05),
            )
            if conflicts:
                _reject(db, 409, sell_level_conflict_message(conflicts), symbol, body)
            return {"dry_run": True, "symbol": symbol, "strategy": body.strategy,
                "capital": str(body.capital), "range_low": str(lines[0]), "range_high": str(lines[-1]),
                "n_levels": n, "levels": [str(value) for value in lines],
                "suggested_structure": structure,
                "cell_usdt": str(body.capital / Decimal(n)), "filters": filters.__dict__,
                "cells": [{"level_idx": cell.level_idx, "buy_price": str(cell.buy_price),
                           "sell_price": str(cell.sell_price), "capital": str(cell.capital),
                           "quantity": str(cell.qty), "initial_state": cell.initial_state}
                          for cell in cells],
                "params": params,
                "guards": {"registry_active": True, "symbol_slot_available": True,
                           "max_grids_simultaneos": maximum, "target_params_valid": True,
                           "order_filters_valid": True, "free_balance_checked": False,
                           "exchange_will_be_called": False}, "data_source": "Binance public spot market data (read-only)",
                "warning": EXECUTION_WARNING}
        except HTTPException:
            raise
        except Exception as exc:
            _reject(db, 422, f"No se pudo construir el plan: {exc}", symbol, body)
    if engine is None or client is None:
        _reject(db, 503, "Grid Engine/Testnet no está disponible; no se abrirán grids.", symbol, body)
    testnet_flag = getattr(client, "testnet", getattr(getattr(client, "client", None), "testnet", False))
    if client is None or testnet_flag is not True:
        _reject(db, 503, "Apertura rechazada: el cliente de ejecución no confirma Testnet.", symbol, body)
    try:
        effective = {**params}
        result = engine.create_grid(symbol, body.range_low, body.range_high, body.n_levels,
            capital=body.capital, strategy=body.strategy, params=effective or None)
        db.add_grid_event(run_id=None, source="CLI", event_type="GRID_OPEN_API", grid_id=int(result["id"]),
            details={"who": "api", "strategy": body.strategy, "capital": str(body.capital),
                     "params": effective, "scan_snapshot": scan_row})
        return {"status": result.get("status"), "grid_id": result.get("id"), "symbol": symbol,
                "warning": EXECUTION_WARNING}
    except GridConfigError as exc:
        message = str(exc).casefold()
        status = 409 if ("maximum simultaneous" in message or "already exists" in message
                         or "sell level conflict" in message) else 422
        _reject(db, status, str(exc), symbol, body)
    except Exception as exc:
        _reject(db, 422, f"No se pudo abrir el grid: {exc}", symbol, body)
