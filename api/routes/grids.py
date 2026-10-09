"""Explainable grid scan and guarded Testnet grid opening API."""
from __future__ import annotations

import hmac
import ipaddress
import math
import random
import secrets
import statistics
import uuid
from decimal import Decimal
from datetime import datetime, timedelta, timezone
from typing import Literal

import numpy as np
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator

from grid.levels import GridConfigError, compute_lines, plan_cells
from grid.policy import validate_params
from grid.loan_cohorts import assign_loan_creation_defaults as _assign_loan_creation_defaults
from grid.loan_cohorts import create_grid_with_loan_cohort, create_loan_pair
from grid.scan_service import EXECUTION_WARNING
from grid.structure import (evaluate_cell_margins, minimum_cell_for_dust_limit,
                            minimum_cell_threshold, minimum_cell_warning,
                            functional_cell_threshold, functional_cell_warning,
                            evaluate_levels, evaluate_preview_position, suggest_structure)
from grid.guards import sell_level_conflicts, sell_level_conflict_message

router = APIRouter()

_GRID_STATUSES = {"OPENING", "ACTIVE", "PAUSED", "CLOSING", "HOLDING",
                  "CLOSED", "CANCELLED", "FAILED", "ERROR"}
_LOAN_STATUSES = ("OPEN", "REPAID", "TRANSFERRED", "CANCELLED", "PENDING")


def _all_grids(db):
    return [row for status in _GRID_STATUSES for row in db.list_grids_by_status({status})]


def _bootstrap_cohort_comparison(cohort_values, control_values, *, seed=20260908, draws=2000,
                                 excluded_cohort=0, excluded_control=0, cohort_loans_created=0):
    n_cohort, n_control = len(cohort_values), len(control_values)
    result = {"diff_pct_per_day": None, "ci_low": None, "ci_high": None,
              "n_cohort": n_cohort, "n_control": n_control,
              "excluded_cohort": excluded_cohort, "excluded_control": excluded_control,
              "conclusive": False, "reason": "muestra insuficiente",
              "small_sample": n_cohort < 10 or n_control < 10}
    if not n_cohort or not n_control:
        return result
    cohort_mean = sum(cohort_values) / n_cohort
    control_mean = sum(control_values) / n_control
    result["diff_pct_per_day"] = cohort_mean - control_mean
    rng = random.Random(seed)
    boot_differences = []
    for _ in range(draws):
        sampled_cohort = sum(cohort_values[rng.randrange(n_cohort)] for _ in range(n_cohort)) / n_cohort
        sampled_control = sum(control_values[rng.randrange(n_control)] for _ in range(n_control)) / n_control
        boot_differences.append(sampled_cohort - sampled_control)
    boot_differences.sort()
    def percentile(fraction):
        position = (len(boot_differences) - 1) * fraction
        lower = int(position)
        upper = min(lower + 1, len(boot_differences) - 1)
        weight = position - lower
        return boot_differences[lower] * (1 - weight) + boot_differences[upper] * weight
    result["ci_low"], result["ci_high"] = percentile(0.025), percentile(0.975)
    if n_cohort < 10 or n_control < 10:
        result["reason"] = "muestra insuficiente"
        return result
    if cohort_loans_created <= 0:
        result["reason"] = "sin préstamos creados"
        return result
    if result["ci_low"] <= 0 <= result["ci_high"]:
        result["reason"] = "el IC incluye 0"
        return result
    result["conclusive"] = True
    result["reason"] = None
    return result


def _dt_utc(value):
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _loan_fields(params: dict | None) -> dict:
    params = params or {}
    return {"loans_group": params.get("loans_group"),
            "loans_enabled": params.get("loans_enabled", False),
            "loan_lender_max_pct": params.get("loan_lender_max_pct")}


def _loan_open_view(loan: dict, now: datetime) -> dict:
    created = _dt_utc(loan.get("created_at"))
    age = max(0.0, (now - created).total_seconds() / 3600) if created else None
    lender = loan.get("lender_idx")
    return {"id": int(loan["id"]), "borrower_idx": int(loan["borrower_idx"]),
            "lender_idx": None if lender is None else int(lender),
            "lender_source": "reserva" if lender is None else "nivel",
            "amount_usdt": float(loan.get("amount") or 0), "age_hours": age,
            "created_at": created.isoformat() if created else None}


def _mid(market: dict) -> Decimal:
    return (Decimal(str(market["bid"])) + Decimal(str(market["ask"]))) / Decimal(2)


def _sigma_open(request, market: dict) -> dict:
    closes = market["klines_1h"]["close"].astype(float).to_numpy()
    sigma = float(np.std(np.diff(np.log(closes)), ddof=1) * np.sqrt(24)) if len(closes) > 2 else 0.0
    days = int(getattr(request.app.state.settings, "scanner_history_days", 30))
    return {"value": sigma, "source": "realized", "window": f"{days}d"}


def _sigma_surfaces(request, symbol: str, sigma_open: dict) -> dict:
    days = int(getattr(request.app.state.settings, "scanner_history_days", 30))
    realized = {**sigma_open, "reason": None if days >= 30 else "la ventana configurada es menor de 30 d\u00EDas"}
    provider = getattr(request.app.state, "vol_provider", None)
    if provider is None or not hasattr(provider, "sigma_surface_context"):
        return {"realized_30d": {"value": None if days < 30 else sigma_open.get("value"),
                                 "source": "realizada", "window": "30 d",
                                 "reason": realized["reason"] or "proveedor de volatilidad no disponible"},
                "champion_24h": {"value": None, "source": "campe\u00F3n", "window": "24 h",
                                 "reason": "proveedor de volatilidad no disponible"},
                "champion_monitor_h": {"value": None, "source": "campe\u00F3n", "window": "4 h",
                                       "reason": "proveedor de volatilidad no disponible"},
                "monitor_h": 4}
    return provider.sigma_surface_context(symbol, realized, monitor_h=4)


def _margin_guard(request, low, high, n, capital, mid, filters):
    settings = request.app.state.settings
    fee = float(getattr(settings, "scanner_fee_pct", .1))
    minimum = float(getattr(settings, "grid_min_margin_after_fees_pct", getattr(settings, "grid_min_net_margin_pct", 0.7)))
    width_pct = float((Decimal(str(high)) - Decimal(str(low))) / Decimal(str(mid)) * 100)
    min_cell = max(Decimal(str(getattr(filters, "min_notional", 5))) * Decimal("1.1"), Decimal("5"))
    evaluation = evaluate_levels(int(n), width_pct, Decimal(str(capital)), Decimal(str(mid)), filters,
                                 fee, float(getattr(settings, "scanner_min_spacing_pct", .8)), min_cell)
    gross = float(evaluation["edge_gross_pct"])
    net = float(evaluation["net_edge_pct"])
    dust_warning = float(evaluation["dust_pct"]) > gross * .5
    reasons = []
    if not evaluation["cell_ok"]: reasons.append("cell_below_minimum")
    if gross < minimum: reasons.append("margin_after_fees_below_minimum")
    return {"minimum_pct": minimum, "actual_pct": gross, "edge_gross_pct": gross,
            "net_after_dust_pct": net, "dust_estimate_pct": float(evaluation["dust_pct"]),
            "dust_warning": dust_warning,
            "dust_warning_message": ("El polvo estimado es alto para esta celda; sube el capital por celda o reduce niveles. Es un tope pesimista, aún no medido en Testnet." if dust_warning else None),
            "reasons": reasons,
            "allowed": bool(evaluation["cell_ok"] and gross >= minimum),
            "cell_ok": bool(evaluation["cell_ok"])}


def _preview_testnet_price(engine, symbol, low, high, public_price):
    unavailable = "No se pudo leer el precio de Testnet; la apertura puede fallar."
    try:
        if engine is None:
            raise RuntimeError("Testnet exchange unavailable")
        book = engine.exchange.get_book_ticker(symbol)
        bid = Decimal(str(book["bid_price"]))
        ask = Decimal(str(book["ask_price"]))
    except Exception:
        return None, None, {"allowed": None, "reason": unavailable}
    empty_sides = []
    if bid <= 0:
        empty_sides.append("compras")
    if ask <= 0:
        empty_sides.append("ventas")
    if empty_sides:
        side_text = " y ".join(empty_sides)
        reason = (f"El libro de Testnet de {symbol} no tiene {side_text} en este momento "
                  f"(bid {bid} / ask {ask}); la apertura fallar\u00eda. "
                  "Prueba con una moneda m\u00e1s l\u00edquida o reintenta.")
        return None, False, {"allowed": False, "reason": reason}
    price = (bid + ask) / Decimal(2)
    in_range = low < price < high
    if in_range:
        return price, True, {"allowed": True, "reason": None}
    reason = (f"Precio Testnet {price} fuera del rango [{low} – {high}]; "
              f"precio público {public_price}.")
    return price, False, {"allowed": False, "reason": reason}


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


class PairOpenRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbol: str = Field(min_length=5, max_length=20)
    capital: Decimal = Field(gt=0)
    range_low: Decimal = Field(gt=0)
    range_high: Decimal = Field(gt=0)
    n_levels: int = Field(ge=4, le=60)
    params: dict | None = None
    target_pct: Decimal | None = Field(default=None, gt=0, le=100)
    target_usdt: Decimal | None = Field(default=None, gt=0)
    target_basis: Literal["cash", "equity"] = "cash"
    max_days: Decimal | None = Field(default=None, gt=0)
    dry_run: StrictBool = True
    confirm: StrictBool = False
    pair_seed: int | None = None
    pair_id: str | None = None
    factor: Literal["loans", "idle_shrink", "capital_shrink"] = "loans"

    @model_validator(mode="after")
    def valid_pair_range(self):
        if self.range_low >= self.range_high:
            raise ValueError("range_low debe ser menor que range_high")
        if self.target_pct is not None and self.target_usdt is not None:
            raise ValueError("elige target_pct o target_usdt, no ambos")
        return self


from api.auth import authorize as _authorize
from models.coin_onboarding import coin_is_ready

def _scan_snapshot(request: Request, symbol: str) -> dict | None:
    last = getattr(request.app.state.grid_scan_service, "last_scan", None)
    if not last:
        return None
    return next((row for row in last["results"] if row.get("symbol") == symbol and row.get("eligible")), None)


def _reject(db, status: int, message: str, symbol: str, body: OpenRequest,
            testnet_snapshot: dict | None = None):
    try:
        details = {"who": "api", "symbol": symbol, "strategy": body.strategy,
            "capital": str(body.capital), "params": body.params or {}, "dry_run": body.dry_run,
            "range_low": str(body.range_low) if body.range_low is not None else None,
            "range_high": str(body.range_high) if body.range_high is not None else None,
            "n_levels": body.n_levels}
        if testnet_snapshot is not None:
            details.update({key: str(value) if value is not None else None
                            for key, value in testnet_snapshot.items()})
        db.add_grid_event(run_id=None, source="CLI", event_type="GRID_OPEN_REJECTED",
            reason=message, details=details)
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
    return _open_grid(request, body)


def _persist_pair_metadata(db, result: dict, pair_metadata: dict):
    grid_id = int(result["id"])
    db.merge_grid_params(grid_id, pair_metadata, allowed=frozenset(pair_metadata))
    effective = dict((db.get_grid(grid_id) or {}).get("params") or {})
    return effective, db.get_grid(grid_id) or result


def _open_grid(request: Request, body: OpenRequest, *, pair_metadata: dict | None = None,
               pair_created_callback=None):
    _authorize(request)
    symbol = body.symbol.strip().upper().replace("/", "")
    if not symbol.endswith("USDT") or len(symbol) <= 4:
        db = request.app.state.db
        _reject(db, 422, "symbol debe ser un par USDT válido.", symbol, body)
    db, engine, client = request.app.state.db, request.app.state.grid_engine, request.app.state.testnet_client
    coin = db.get_coin(symbol)
    if not coin or int(coin.get("active", 0)) != 1:
        _reject(db, 422, "El símbolo debe estar activo en Coin Registry.", symbol, body)
    if not coin_is_ready(db,getattr(request.app.state,"vol_registry",None),symbol):
        state=(db.get_readiness(symbol) or {}).get("state") or "pendiente"
        _reject(db,409,f"La moneda {symbol} aún no está lista: {state}. Espera a que termine la preparación.",symbol,body)
    maximum = int(request.app.state.settings.max_grids_simultaneos)
    if db.count_open_grids() >= maximum:
        _reject(db, 409, f"Se alcanzó max_grids_simultaneos ({maximum}).", symbol, body)
    scan_row = _scan_snapshot(request, symbol) if body.from_scan else None
    if body.from_scan and scan_row is None:
        _reject(db, 422, "No hay resultado elegible reciente del scan para este símbolo.", symbol, body)
    raw_params = dict(body.params or {})
    control_every_n = int(getattr(request.app.state.settings, "loans_control_every_n", 3))
    params = (_assign_loan_creation_defaults(db, body.strategy, raw_params, control_every_n)
              if body.dry_run else raw_params)
    if body.strategy == "smart":
        horizon = params.get("horizon_h", 4)
        if isinstance(horizon, bool) or not isinstance(horizon, (int, float)) or horizon not in {1, 2, 4, 24}:
            _reject(db, 422, "horizon_h debe ser 1, 2, 4 o 24", symbol, body)
        params["horizon_h"] = int(horizon)
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
        if body.strategy == "simple" and set(params) - {
            "max_days", "compound_enabled", "compound_ratio", "compound_max_growth_pct"
        }:
            _reject(db, 422, "El motor simple solo admite plazo e interés compuesto; use strategy=smart para otros parámetros.", symbol, body)
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
            sigma_open = _sigma_open(request, market)
            sigma = sigma_open["value"]
            mid = _mid(market)
            structure = suggest_structure(sigma, body.capital, mid,
                filters, float(request.app.state.settings.scanner_fee_pct),
                min_spacing_pct=float(request.app.state.settings.scanner_min_spacing_pct),
                min_cell_usdt=functional_cell_threshold(filters, body.strategy,
                    max(filters.min_notional * Decimal("1.1"), Decimal("5.5"))),
                min_margin_after_fees_pct=float(getattr(request.app.state.settings,
                    "grid_min_margin_after_fees_pct", getattr(request.app.state.settings,
                    "grid_min_net_margin_pct", .7))))
            low = body.range_low or structure["range_low"]
            high = body.range_high or structure["range_high"]
            n = body.n_levels or structure["n_levels"]
            if not structure["feasible"] and (body.range_low is None or body.range_high is None or body.n_levels is None):
                raise ValueError("No hay estructura factible; proporcione rango y niveles manualmente.")
            lines = compute_lines(low, high, n, filters)
            margin_guard = _margin_guard(request, low, high, n, body.capital, mid, filters)
            cells = plan_cells(lines, body.capital,
                {"bid_price": Decimal(str(market["bid"])), "ask_price": Decimal(str(market["ask"])),
                 "avg_price": mid}, filters,
                request.app.state.settings)
            cell_rows, cell_metrics_summary = evaluate_cell_margins(
                cells, filters, getattr(request.app.state.settings, "scanner_fee_pct", .1))
            minimum_cell = functional_cell_threshold(filters, body.strategy,
                max(filters.min_notional * Decimal("1.1"), Decimal("5.5")))
            functional_minimum = functional_cell_threshold(filters, "smart")
            dust_min_cell = minimum_cell_for_dust_limit(filters, mid)
            active_orders = sum(cell.initial_state in {"BUY_OPEN", "SELL_OPEN"} for cell in cells)
            position = evaluate_preview_position(mid, lines[0], lines[-1])
            testnet_price, testnet_in_range, testnet_price_guard = _preview_testnet_price(
                engine, symbol, lines[0], lines[-1], mid)
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
                "n_levels": n, "levels": [str(value) for value in lines], "current_price": str(mid),
                "testnet_price": str(testnet_price) if testnet_price is not None else None,
                "testnet_in_range": testnet_in_range, "testnet_price_guard": testnet_price_guard,
                "initial_order_count": active_orders, **position,
                "suggested_structure": structure, "sigma_open": sigma_open,
                "sigma_surfaces": _sigma_surfaces(request, symbol, sigma_open),
                "min_cell_warning": minimum_cell_warning(body.capital, n, minimum_cell),
                "functional_cell_warning": functional_cell_warning(body.capital, n, functional_minimum, body.strategy),
                "dust_target_pct": "0.1", "dust_min_cell_usdt": str(dust_min_cell),
                "margin_guard": margin_guard,
                "cell_usdt": str(body.capital / Decimal(n)), "filters": filters.__dict__,
                "cells": cell_rows, "cell_metrics_summary": cell_metrics_summary,
                "params": params, **_loan_fields(params),
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
        market, filters = request.app.state.grid_scan_service._market(
            symbol, float(body.capital), request.app.state.grid_scan_service.clock() +
            float(request.app.state.settings.scanner_timeout_seconds))
        sigma_open = _sigma_open(request, market)
        margin_guard = _margin_guard(request, body.range_low, body.range_high, body.n_levels,
                                     body.capital, _mid(market), filters)
    except Exception as exc:
        _reject(db, 503, f"No se pudo comprobar el margen mínimo tras comisiones en el mercado público: {type(exc).__name__}.", symbol, body)
    if not margin_guard["allowed"]:
        _reject(db, 422, f"Apertura bloqueada: {'; '.join(margin_guard['reasons'])}; margen tras comisiones {margin_guard['actual_pct']:.3f}% (m\u00ednimo {margin_guard['minimum_pct']:.3f}%) o celda bajo el m\u00ednimo del exchange.", symbol, body)
    try:
        book = engine.exchange.get_book_ticker(symbol)
        testnet_bid = Decimal(str(book["bid_price"]))
        testnet_ask = Decimal(str(book["ask_price"]))
    except Exception as exc:
        _reject(db, 503, f"No se pudo leer el libro de Testnet antes de abrir ({type(exc).__name__}).", symbol, body)
    empty_sides = []
    if testnet_bid <= 0:
        empty_sides.append("compras")
    if testnet_ask <= 0:
        empty_sides.append("ventas")
    testnet_mid = ((testnet_bid + testnet_ask) / Decimal(2)
                   if not empty_sides else None)
    testnet_snapshot = {"bid": testnet_bid, "ask": testnet_ask, "mid": testnet_mid}
    if empty_sides:
        sides = " y ".join(empty_sides)
        _reject(db, 422,
            f"Apertura bloqueada: el libro de Testnet de {symbol} no tiene {sides} "
            f"(bid {testnet_bid}, ask {testnet_ask}, mid no calculado); "
            f"el libro est\u00e1 vac\u00edo; rango {body.range_low} a {body.range_high}.",
            symbol, body, testnet_snapshot)
    if not (body.range_low < testnet_mid < body.range_high):
        _reject(db, 422,
            f"Apertura bloqueada: precio medio de Testnet fuera del rango "
            f"(bid {testnet_bid}, ask {testnet_ask}, mid {testnet_mid}, "
            f"rango {body.range_low} a {body.range_high}).",
            symbol, body, testnet_snapshot)
    try:
        if pair_metadata is None:
            effective, result = create_grid_with_loan_cohort(
                db, body.strategy, params, control_every_n,
                lambda assigned: engine.create_grid(
                    symbol, body.range_low, body.range_high, body.n_levels,
                    capital=body.capital, strategy=body.strategy, params=assigned or None),
                explicit_params=raw_params,
            )
        else:
            result = engine.create_grid(
                symbol, body.range_low, body.range_high, body.n_levels,
                capital=body.capital, strategy="smart", params=params or None)
            if pair_created_callback is not None:
                pair_created_callback(int(result["id"]))
            effective, result = _persist_pair_metadata(db, result, pair_metadata)
        db.add_grid_event(run_id=None, source="CLI", event_type="GRID_OPEN_API", grid_id=int(result["id"]),
            details={"who": "api", "strategy": body.strategy, "capital": str(body.capital),
                     "params": effective, "scan_snapshot": scan_row, "sigma_open": sigma_open,
                     **_loan_fields(effective), **(pair_metadata or {})})
        return {"status": result.get("status"), "grid_id": result.get("id"), "symbol": symbol,
                **_loan_fields(effective), **(pair_metadata or {}), "warning": EXECUTION_WARNING}
    except GridConfigError as exc:
        message = str(exc).casefold()
        status = 409 if ("maximum simultaneous" in message or "already exists" in message
                         or "sell level conflict" in message) else 422
        _reject(db, status, str(exc), symbol, body, testnet_snapshot)
    except Exception as exc:
        _reject(db, 422, f"No se pudo abrir el grid: {exc}", symbol, body, testnet_snapshot)


_PAIR_OFFSETS_PCT = (0.10, 0.15, 0.20, 0.30)


def _pair_arm_body(body: PairOpenRequest, arm: str, low: Decimal, high: Decimal,
                   *, dry_run: bool, confirm: bool, factor: str = "loans") -> OpenRequest:
    params = dict(body.params or {})
    params.pop("loans_group", None)
    if factor == "loans":
        params["loans_enabled"] = arm == "pair_loans"
        params["adjust_idle_shrink"] = False
        if arm == "pair_loans":
            params.update({"loan_topup_pct": 70.0, "loan_lender_max_pct": 70.0})
    elif factor == "idle_shrink":
        params["loans_enabled"] = False
        params["adjust_idle_shrink"] = arm == "pair_idle_on"
    elif factor == "capital_shrink":
        params["loans_enabled"] = False
        params["adjust_idle_shrink"] = False
        params["adjust_shrink_n"] = arm == "pair_shrink_on"
    return OpenRequest(
        symbol=body.symbol, strategy="smart", capital=body.capital,
        range_low=low, range_high=high, n_levels=body.n_levels,
        params=params, target_pct=body.target_pct, target_usdt=body.target_usdt,
        target_basis=body.target_basis, max_days=body.max_days,
        dry_run=dry_run, confirm=confirm,
    )


def _pair_arms(factor: str) -> tuple[str, str]:
    return {"loans": ("pair_loans", "pair_control"),
            "idle_shrink": ("pair_idle_on", "pair_idle_off"),
            "capital_shrink": ("pair_shrink_on", "pair_shrink_off")}[factor]


def _build_pair_plans(request: Request, body: PairOpenRequest, first_arm: str):
    factor = body.factor
    first = _open_grid(request, _pair_arm_body(
        body, first_arm, body.range_low, body.range_high, dry_run=True, confirm=False, factor=factor))
    arms = _pair_arms(factor)
    second_arm = arms[1] if first_arm == arms[0] else arms[0]
    conflicts = []
    chosen = None
    second_plan = None
    for offset_pct in _PAIR_OFFSETS_PCT:
        offset = Decimal(str(offset_pct)) / Decimal(100)
        low, high = body.range_low * (1 + offset), body.range_high * (1 + offset)
        try:
            candidate = _open_grid(request, _pair_arm_body(
                body, second_arm, low, high, dry_run=True, confirm=False, factor=factor))
        except HTTPException as exc:
            if exc.status_code == 409 and "sell level conflict" in str(exc.detail).casefold():
                continue
            raise
        tick = first["filters"]["tick_size"]
        tolerance = getattr(request.app.state.settings, "same_coin_sell_tolerance_pct", 0.05)
        conflicts = sell_level_conflicts(
            [cell["sell_price"] for cell in candidate.get("cells", [])],
            [{"sell_price": cell["sell_price"], "state": "SELL_OPEN"}
             for cell in first.get("cells", [])], tick, tolerance)
        if not conflicts:
            chosen, second_plan = offset_pct, candidate
            break
    if chosen is None:
        raise HTTPException(422, "Ningún desplazamiento de 0,10 % a 0,30 % evita el conflicto de ventas.")
    return first_arm, second_arm, first, second_plan, chosen


def _mark_pair_orphan(db, grid_id: int, pair_id: str, reason: str):
    db.merge_grid_params(int(grid_id), {"pair_id": pair_id, "pair_status": "orphan"},
                         allowed=frozenset({"pair_id", "pair_status"}))
    db.add_grid_event(run_id=None, source="CLI", event_type="PAIR_ORPHAN", grid_id=int(grid_id),
                      reason=reason, details={"pair_id": pair_id, "grid_id": int(grid_id)})


@router.post("/pair")
def open_loan_pair(request: Request, body: PairOpenRequest):
    _authorize(request)
    if body.dry_run and body.confirm:
        raise HTTPException(422, "confirm solo se acepta junto con dry_run=false.")
    if not body.dry_run and not body.confirm:
        raise HTTPException(422, "confirm=true es obligatorio para ejecutar una apertura de par.")
    db = request.app.state.db
    factor = body.factor
    if factor == "idle_shrink" and not getattr(request.app.state.settings,
                                                "adjust_idle_shrink_enabled", False):
        raise HTTPException(422, "idle_shrink est bloqué par el interruptor global apagado.")
    maximum = int(request.app.state.settings.max_grids_simultaneos)
    if maximum - int(db.count_open_grids()) < 2:
        raise HTTPException(409, "El par necesita al menos dos cupos libres de max_grids_simultaneos.")
    symbol = body.symbol.strip().upper().replace("/", "")
    if not symbol.endswith("USDT") or len(symbol) <= 4:
        raise HTTPException(422, "symbol debe ser un par USDT válido.")
    if not db.get_coin(symbol) or int(db.get_coin(symbol).get("active", 0)) != 1:
        raise HTTPException(422, "El símbolo debe estar activo en Coin Registry.")
    if not coin_is_ready(db, getattr(request.app.state, "vol_registry", None), symbol):
        state = (db.get_readiness(symbol) or {}).get("state") or "pendiente"
        raise HTTPException(409, f"La moneda {symbol} aún no está lista: {state}.")
    try:
        _, filters = request.app.state.grid_scan_service._market(
            symbol, float(body.capital), request.app.state.grid_scan_service.clock() +
            float(request.app.state.settings.scanner_timeout_seconds))
    except Exception as exc:
        raise HTTPException(503, f"No se pudieron comprobar los filtros del símbolo: {exc}") from exc
    floor = functional_cell_threshold(filters, "smart")
    if body.capital / Decimal(body.n_levels) < floor:
        raise HTTPException(422, f"Capital por celda inferior al piso funcional Smart ({floor} USDT).")

    pair_seed = body.pair_seed if body.pair_seed is not None else secrets.randbits(64)
    pair_arms = _pair_arms(factor)
    first_arm = random.Random(pair_seed).choice(pair_arms)
    first_arm, second_arm, first_plan, second_plan, selected_offset = _build_pair_plans(
        request, body, first_arm)
    pair_id = body.pair_id or str(uuid.uuid4())
    if body.dry_run:
        return {"dry_run": True, "pair_id": pair_id, "pair_seed": pair_seed, "factor": factor,
                "pair_first_arm": first_arm, "pair_offset_pct": selected_offset,
                "arms": [{"arm": first_arm, "plan": first_plan},
                         {"arm": second_arm, "plan": second_plan}],
                "warning": EXECUTION_WARNING}
    created = []

    def create_pair():
        if maximum - int(db.count_open_grids()) < 2:
            raise HTTPException(409, "El par necesita al menos dos cupos libres.")
        first_metadata = {"pair_id": pair_id, "pair_seed": pair_seed, "pair_factor": factor,
                          "pair_arm": first_arm, "pair_first_arm": first_arm,
                          "pair_offset_pct": 0.0, "loans_group": first_arm if factor == "loans" else "manual"}
        first_body = _pair_arm_body(body, first_arm, body.range_low, body.range_high,
                                    dry_run=False, confirm=True, factor=factor)
        first_result = _open_grid(request, first_body, pair_metadata=first_metadata,
                                  pair_created_callback=created.append)
        offset_list = [value for value in _PAIR_OFFSETS_PCT if value >= selected_offset]
        last_error = None
        for offset_pct in offset_list:
            offset = Decimal(str(offset_pct)) / Decimal(100)
            second_body = _pair_arm_body(
                body, second_arm, body.range_low * (1 + offset), body.range_high * (1 + offset),
                dry_run=False, confirm=True, factor=factor)
            second_metadata = {"pair_id": pair_id, "pair_seed": pair_seed, "pair_factor": factor,
                               "pair_arm": second_arm, "pair_first_arm": first_arm,
                               "pair_offset_pct": offset_pct,
                               "loans_group": second_arm if factor == "loans" else "manual"}
            try:
                second_result = _open_grid(request, second_body, pair_metadata=second_metadata,
                                           pair_created_callback=created.append)
                return {"dry_run": False, "pair_id": pair_id, "pair_seed": pair_seed, "factor": factor,
                        "pair_first_arm": first_arm, "pair_offset_pct": offset_pct,
                        "grids": [first_result, second_result], "warning": EXECUTION_WARNING}
            except HTTPException as exc:
                if exc.status_code == 409 and "sell level conflict" in str(exc.detail).casefold():
                    last_error = exc
                    continue
                raise
        raise HTTPException(422, f"Ningún desplazamiento disponible evita el conflicto: {last_error.detail if last_error else 'sin detalle'}")

    try:
        return create_loan_pair(db, create_pair)
    except HTTPException as exc:
        if created:
            orphan_errors = []
            for grid_id in created:
                try:
                    _mark_pair_orphan(db, grid_id, pair_id, str(exc.detail))
                except Exception as mark_error:
                    orphan_errors.append(f"grid_id={grid_id}: {mark_error}")
            ids = ",".join(str(grid_id) for grid_id in created)
            audit_note = f"; errores al registrar huérfano: {'; '.join(orphan_errors)}" if orphan_errors else ""
            raise HTTPException(409, f"PAIR_ORPHAN pair_id={pair_id}; grid_id(s)={ids}; segundo brazo falló: {exc.detail}{audit_note}") from exc
        raise
    except Exception as exc:
        if created:
            orphan_errors = []
            for grid_id in created:
                try:
                    _mark_pair_orphan(db, grid_id, pair_id, str(exc))
                except Exception as mark_error:
                    orphan_errors.append(f"grid_id={grid_id}: {mark_error}")
            ids = ",".join(str(grid_id) for grid_id in created)
            audit_note = f"; errores al registrar huérfano: {'; '.join(orphan_errors)}" if orphan_errors else ""
            raise HTTPException(409, f"PAIR_ORPHAN pair_id={pair_id}; grid_id(s)={ids}; segundo brazo falló: {exc}{audit_note}") from exc
        raise HTTPException(503, f"No se pudo adquirir el candado para abrir el par: {exc}") from exc


@router.get("/loans/summary")
def loans_summary(request: Request):
    _authorize(request)
    db = request.app.state.db
    now = datetime.now(timezone.utc)
    groups = {name: {"group": name, "grid_count": 0, "realized_pnl_usdt": 0.0,
                     "pnl_per_open_day_usdt": None, "pnl_pct_capital": None,
                     "pnl_pct_capital_per_day": None, "cycles_completed": 0,
                     "commissions_usdt": 0.0, "loans_created": 0,
                     "loans_repaid": 0, "loans_transferred": 0,
                     "_pnl_known": True, "_open_days": 0.0, "_days_known": True,
                     "_capital": 0.0, "_capital_known": True}
              for name in ("loans", "loans_v2", "control", "manual", "sin_grupo")}
    grid_rows = []
    cohort_samples = {name: [] for name in ("loans", "loans_v2", "control")}
    cohort_excluded = {name: 0 for name in cohort_samples}
    active_statuses = {"OPENING", "ACTIVE", "PAUSED", "CLOSING", "HOLDING"}
    for grid in _all_grids(db):
        if grid.get("strategy") != "smart":
            continue
        params = grid.get("params") if isinstance(grid.get("params"), dict) else {}
        group_name = params.get("loans_group")
        group_name = group_name if group_name in {"loans", "loans_v2", "control", "manual"} else "sin_grupo"
        group = groups[group_name]
        group["grid_count"] += 1
        levels = db.get_grid_levels(int(grid["id"]))
        pnl_values = [row.get("pnl") for row in levels]
        grid_pnl_known = not any(value is None for value in pnl_values)
        grid_pnl = sum(float(value) for value in pnl_values) if grid_pnl_known else None
        if any(value is None for value in pnl_values):
            group["_pnl_known"] = False
        else:
            group["realized_pnl_usdt"] += sum(float(value) for value in pnl_values)
        capital = float(grid.get("capital_total") or 0)
        if capital > 0:
            group["_capital"] += capital
        else:
            group["_capital_known"] = False
        group["cycles_completed"] += sum(int(row.get("cycles_completed") or 0) for row in levels)
        fee_values = [row.get("fee_paid") for row in levels]
        group["commissions_usdt"] += sum(float(value or 0) for value in fee_values)
        created = _dt_utc(grid.get("created_at"))
        ended = now if str(grid.get("status", "")).upper() in active_statuses else _dt_utc(grid.get("closed_at"))
        if created is None or ended is None:
            group["_days_known"] = False
            grid_days = None
        else:
            actual_open_days = (ended - created).total_seconds() / 86400
            grid_days = max(1.0, actual_open_days)
            group["_open_days"] += grid_days
        if group_name in cohort_samples:
            if (grid_pnl_known and capital > 0 and created is not None and ended is not None
                    and actual_open_days > 0):
                cohort_samples[group_name].append(grid_pnl / capital / grid_days * 100)
            else:
                cohort_excluded[group_name] += 1
        grid_rows.append({
            "grid_id": int(grid["id"]), "symbol": grid.get("symbol"), "group": group_name,
            "capital_usdt": capital, "realized_pnl_usdt": grid_pnl,
            "pnl_pct_capital": (grid_pnl / capital * 100
                                 if grid_pnl_known and capital > 0 else None),
            "pnl_pct_capital_per_day": (grid_pnl / capital * 100 / grid_days
                                         if grid_pnl_known and capital > 0 and grid_days else None),
        })
        loans = db.list_grid_loans(int(grid["id"]))
        group["loans_created"] += len(loans)
        group["loans_repaid"] += sum(row.get("status") == "REPAID" for row in loans)
        group["loans_transferred"] += sum(row.get("status") == "TRANSFERRED" for row in loans)
    for group in groups.values():
        if not group.pop("_pnl_known"):
            group["realized_pnl_usdt"] = None
            group["pnl_per_open_day_usdt"] = None
            group["pnl_pct_capital"] = None
            group["pnl_pct_capital_per_day"] = None
        else:
            if group["_capital_known"] and group["_capital"] > 0:
                group["pnl_pct_capital"] = group["realized_pnl_usdt"] / group["_capital"] * 100
            if group["_days_known"] and group["_open_days"] > 0:
                group["pnl_per_open_day_usdt"] = group["realized_pnl_usdt"] / group["_open_days"]
                if group["pnl_pct_capital"] is not None:
                    group["pnl_pct_capital_per_day"] = group["pnl_pct_capital"] / group["_open_days"]
        group.pop("_days_known")
        group.pop("_open_days")
        group.pop("_capital")
        group.pop("_capital_known")
    comparisons = {
        "loans_vs_control": _bootstrap_cohort_comparison(
            cohort_samples["loans"], cohort_samples["control"], seed=20260908,
            excluded_cohort=cohort_excluded["loans"], excluded_control=cohort_excluded["control"],
            cohort_loans_created=groups["loans"]["loans_created"]),
        "loans_v2_vs_control": _bootstrap_cohort_comparison(
            cohort_samples["loans_v2"], cohort_samples["control"], seed=20260909,
            excluded_cohort=cohort_excluded["loans_v2"], excluded_control=cohort_excluded["control"],
            cohort_loans_created=groups["loans_v2"]["loans_created"]),
    }
    comparison_note = ("P&L realizado por capital y d\u00edas abiertos con m\u00ednimo de 1 d\u00eda; no ajusta tama\u00f1o de celda ni moneda. "
        "Observacional: el grupo control se asigna en cada 3.er grid. Los grids excluidos por "
        "P&L desconocido, capital no v\u00e1lido o duraci\u00f3n no positiva se cuentan en cada comparaci\u00f3n.")
    return {"groups": list(groups.values()), "grids": grid_rows,
            "cohort_comparisons": comparisons, "comparison_note": comparison_note,
            "note": "Muestra pequeña y mercado distinto por grid: es una guía, no una conclusión."}


def _paired_loan_summary(db, *, factor="loans", now=None, draws=2000, seed=20261008):
    now = now or datetime.now(timezone.utc)
    expected_arms = _pair_arms(factor)
    treated_arm, control_arm = expected_arms
    pairs = {}
    for grid in _all_grids(db):
        params = grid.get("params") if isinstance(grid.get("params"), dict) else {}
        pair_id = params.get("pair_id")
        if pair_id and params.get("pair_factor", "loans") == factor:
            pairs.setdefault(str(pair_id), []).append(grid)

    rows, excluded, observations = [], [], []
    duration_ratio_flagged = 0
    treated_values = []
    terminal = {"CLOSED", "CANCELLED", "FAILED", "ERROR"}
    for pair_id, grids in sorted(pairs.items()):
        arms = {str((grid.get("params") or {}).get("pair_arm")): grid for grid in grids}
        reason = None
        if any((grid.get("params") or {}).get("pair_status") == "orphan" for grid in grids):
            reason = "par huérfano"
        elif len(grids) != 2 or set(arms) != set(expected_arms):
            reason = "brazos incompletos o duplicados"
        elif any(str(grid.get("status", "")).upper() not in terminal for grid in grids):
            reason = "brazos no cerrados"

        metrics = {}
        durations = {}
        loans_created = 0
        if reason is None:
            for arm_name, grid in arms.items():
                levels = db.get_grid_levels(int(grid["id"]))
                pnl_values = [row.get("pnl") for row in levels]
                capital = float(grid.get("capital_total") or 0)
                created, ended = _dt_utc(grid.get("created_at")), _dt_utc(grid.get("closed_at"))
                if any(value is None for value in pnl_values):
                    reason = "P&L desconocido"
                    break
                if capital <= 0:
                    reason = "capital no válido"
                    break
                if created is None or ended is None:
                    reason = "fechas ausentes"
                    break
                duration_days = (ended - created).total_seconds() / 86400
                if duration_days <= 0:
                    reason = "duración no positiva"
                    break
                durations[arm_name] = duration_days
                metrics[arm_name] = sum(float(value) for value in pnl_values) / capital / max(1.0, duration_days) * 100
                if arm_name == treated_arm and factor == "loans":
                    loans_created = len(db.list_grid_loans(int(grid["id"])))

        treated = loans_created > 0 if factor == "loans" else False
        if reason is None and factor != "loans":
            list_events = getattr(db, "list_grid_events", None)
            if callable(list_events):
                source = "IDLE_SHRINK" if factor == "idle_shrink" else "CAPITAL_SHRINK"
                treated = any(event.get("event_type") == "GRID_ADJUSTED"
                    and (event.get("details") or {}).get("source") == source
                    for event in list_events(grid_id=int(arms[treated_arm]["id"]), limit=10000))
        row = {"pair_id": pair_id,
               "symbol": grids[0].get("symbol") if grids else None,
               "pair_loans_status": arms.get(treated_arm, {}).get("status"),
               "pair_control_status": arms.get(control_arm, {}).get("status"),
               "loans_pct_per_day": metrics.get("pair_loans"),
               "control_pct_per_day": metrics.get("pair_control"),
               "d_i": (metrics[treated_arm] - metrics[control_arm] if reason is None else None),
               "treated": treated,
               "excluded_reason": reason}
        if factor != "loans":
            row.update({"pair_factor": factor, "treated_arm": treated_arm, "control_arm": control_arm,
                        "treated_pct_per_day": metrics.get(treated_arm),
                        "control_pct_per_day": metrics.get(control_arm)})
        rows.append(row)
        if reason is not None:
            excluded.append({"pair_id": pair_id, "reason": reason})
            continue
        difference = row["d_i"]
        observations.append(difference)
        if treated:
            treated_values.append(difference)
        if max(durations.values()) > 2 * min(durations.values()):
            duration_ratio_flagged += 1

    n = len(observations)
    mean_d = sum(observations) / n if n else None
    sd_d = statistics.stdev(observations) if n >= 2 else None
    t_paired = (mean_d / (sd_d / math.sqrt(n)) if n >= 4 and sd_d and sd_d > 0 else None)
    ci_low = ci_high = None
    if n:
        rng = random.Random(seed)
        boot = sorted(sum(observations[rng.randrange(n)] for _ in range(n)) / n for _ in range(draws))
        def percentile(fraction):
            pos = (len(boot) - 1) * fraction
            low = int(pos)
            high = min(low + 1, len(boot) - 1)
            return boot[low] * (1 - (pos - low)) + boot[high] * (pos - low)
        ci_low, ci_high = percentile(.025), percentile(.975)
    treated_n = len(treated_values)
    reason = []
    if n < 15:
        reason.append("muestra insuficiente (n<15)")
    if ci_low is None or ci_low <= 0 <= ci_high:
        reason.append("el IC incluye 0")
    if not n or treated_n / n < 0.5:
        reason.append("pocos pares con aplicaciones tratadas" if factor != "loans"
                      else "pocos pares con préstamos reales")
    return {
        **({"factor": factor} if factor != "loans" else {}),
        "pairs": rows, "n_pairs": n, "mean_d": mean_d, "sd_d": sd_d,
        "t_paired": t_paired, "ci_low": ci_low, "ci_high": ci_high,
        "wins": sum(value > 0 for value in observations),
        "treated_pairs": treated_n,
        "mean_d_treated": sum(treated_values) / treated_n if treated_n else None,
        "n_treated": treated_n,
        "duration_ratio_flagged": duration_ratio_flagged,
        "detectable_effect_80pct": 2.8 * sd_d / math.sqrt(n) if n >= 5 and sd_d is not None else None,
        "excluded_pairs": excluded, "orphan_pairs": sum(item["reason"] == "par huérfano" for item in excluded),
        "conclusive": not reason, "reason": reason,
        "note": ("Observacional dentro del par; los brazos difieren en un desplazamiento de rango de ≤ 0,30 %; "
                 "el P&L es realizado; no corrige por tamaño de celda."),
    }


@router.get("/loans/pairs/summary")
def loans_pairs_summary(request: Request, factor: Literal["loans", "idle_shrink", "capital_shrink"] = "loans"):
    _authorize(request)
    return _paired_loan_summary(request.app.state.db, factor=factor)


@router.get("/pause-shadow/summary")
def pause_shadow_summary(request: Request):
    _authorize(request)
    return request.app.state.db.get_pause_shadow_summary()


def _adjust_verdict(summary: dict) -> dict:
    low, high, n = summary.get("ci_low"), summary.get("ci_high"), int(summary.get("n_pairs") or 0)
    if n >= 20 and high is not None and high <= 0:
        verdict = "apagar definitivamente"
    elif n >= 20 and low is not None and low > 0:
        verdict = "candidato a activar por defecto (decide Ramón)"
    else:
        verdict = "en prueba"
    return {"n_pairs": n, "ci_low": low, "ci_high": high, "verdict": verdict}


def _snapshot_pnl_by_time(db, grid_id: int) -> list[tuple[datetime, float]]:
    rows = db.list_grid_snapshots(grid_id=grid_id, limit=10000)
    grouped: dict[datetime, float] = {}
    for row in rows:
        ts = _dt_utc(row.get("ts"))
        if ts is None:
            continue
        grouped[ts] = grouped.get(ts, 0.0) + float(row.get("pnl_realized") or 0) + float(row.get("unrealized_pnl") or 0)
    return sorted(grouped.items())


@router.get("/level-adjust/summary")
def level_adjust_summary(request: Request):
    _authorize(request)
    db = request.app.state.db
    idle_pairs = _paired_loan_summary(db, factor="idle_shrink")
    capital_pairs = _paired_loan_summary(db, factor="capital_shrink")
    grids = []
    for grid in _all_grids(db):
        if grid.get("strategy") != "smart":
            continue
        grid_id = int(grid["id"])
        events = list(reversed(db.list_grid_events(grid_id=grid_id, limit=10000)))
        adjustments = [event for event in events if event.get("event_type") == "GRID_ADJUSTED"]
        counts = {source: 0 for source in ("MONITOR", "CAPITAL_SHRINK", "IDLE_SHRINK")}
        applications = []
        for event in adjustments:
            details = event.get("details") or {}
            source = str(details.get("source") or "MONITOR").upper()
            counts[source] = counts.get(source, 0) + 1
            ts = _dt_utc(event.get("ts"))
            snapshots = _snapshot_pnl_by_time(db, grid_id) if ts else []
            def at_offset(hours, before):
                target = ts + timedelta(hours=hours if not before else -hours)
                candidates = [value for stamp, value in snapshots
                              if (stamp <= target if before else stamp >= target)]
                return candidates[-1] if before and candidates else candidates[0] if candidates else None
            old_new = details.get("new") or {}
            old = details.get("old") or {}
            applications.append({"source": source, "ts": ts.isoformat() if ts else None,
                "n_from": old.get("n"), "n_to": old_new.get("n"),
                "pnl_24h_before": at_offset(24, True), "pnl_24h_after": at_offset(24, False),
                "pnl_72h_before": at_offset(72, True), "pnl_72h_after": at_offset(72, False),
                "label": "descriptivo, no causal"})
        blocked: dict[str, int] = {}
        for event in events:
            if event.get("event_type") == "ADJUST_BLOCKED":
                reason = str(event.get("reason") or (event.get("details") or {}).get("blocked_reason") or "sin motivo")
                blocked[reason] = blocked.get(reason, 0) + 1
        idle_evals = [event for event in events if event.get("event_type") == "IDLE_SHRINK_EVAL"]
        omitted: dict[str, int] = {}
        initial_n = ((adjustments[0].get("details") or {}).get("old") or {}).get("n") if adjustments else grid.get("n_levels")
        trajectory = [{"ts": str(grid.get("created_at")), "n": initial_n, "source": "OPEN"}]
        trajectory.extend({"ts": event.get("ts").isoformat() if isinstance(event.get("ts"), datetime)
                           else str(event.get("ts")),
                           "n": (event.get("details") or {}).get("new", {}).get("n"),
                           "source": (event.get("details") or {}).get("source", "MONITOR")}
                          for event in adjustments)
        for event in idle_evals:
            details = event.get("details") or {}
            if details.get("omission_reason"):
                reason = str(details["omission_reason"])
                omitted[reason] = omitted.get(reason, 0) + 1
        grids.append({"grid_id": grid_id, "symbol": grid.get("symbol"),
            "adjustments": counts, "adjust_blocked": blocked,
            "idle_evaluations": len(idle_evals), "idle_omissions": omitted,
            "idle_blocked_reason": ("interruptor_global_apagado"
                if (grid.get("params") or {}).get("adjust_idle_shrink") is True
                and not getattr(request.app.state.settings, "adjust_idle_shrink_enabled", False) else None),
            "idle_applied": counts.get("IDLE_SHRINK", 0), "n_trajectory": trajectory,
            "pnl_after_adjustments": applications})
    settings = request.app.state.settings
    return {"idle_shrink_enabled": bool(getattr(settings, "adjust_idle_shrink_enabled", False)),
        "idle_shrink_default": False, "grids": grids,
        "verdicts": {"idle_shrink": _adjust_verdict(idle_pairs),
                     "capital_shrink": _adjust_verdict(capital_pairs)},
        "pairs": {"idle_shrink": idle_pairs, "capital_shrink": capital_pairs},
        "note": "La regla queda apagada por defecto; sin evidencia de mejora en simulación. "
                "Las comparaciones descriptivas no son causales."}


@router.get("/{grid_id}/loans")
def grid_loans(request: Request, grid_id: int):
    _authorize(request)
    db = request.app.state.db
    grid = db.get_grid(int(grid_id))
    if grid is None:
        raise HTTPException(404, f"Grid {grid_id} no existe.")
    params = grid.get("params") if isinstance(grid.get("params"), dict) else {}
    loans = db.list_grid_loans(int(grid_id))
    counts = {status: sum(str(row.get("status", "")).upper() == status for row in loans)
              for status in _LOAN_STATUSES}
    now = datetime.now(timezone.utc)
    repaid_hours = []
    for row in loans:
        if str(row.get("status", "")).upper() != "REPAID":
            continue
        created, closed = _dt_utc(row.get("created_at")), _dt_utc(row.get("closed_at"))
        if created is not None and closed is not None:
            repaid_hours.append(max(0.0, (closed - created).total_seconds() / 3600))
    lent_statuses = {"OPEN", "REPAID", "TRANSFERRED"}
    return {"grid_id": int(grid_id), **_loan_fields(params),
            "idle_shrink_enabled": params.get("adjust_idle_shrink") is True,
            "idle_shrink_global_enabled": bool(getattr(request.app.state.settings,
                                                        "adjust_idle_shrink_enabled", False)),
            "counts": counts,
            "total_amount_lent_usdt": sum(float(row.get("amount") or 0) for row in loans
                                           if str(row.get("status", "")).upper() in lent_statuses),
            "average_repaid_open_hours": (sum(repaid_hours) / len(repaid_hours)
                                          if repaid_hours else None),
            "open_loans": [_loan_open_view(row, now) for row in loans
                           if str(row.get("status", "")).upper() == "OPEN"]}
