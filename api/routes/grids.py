"""Explainable grid scan and guarded Testnet grid opening API."""
from __future__ import annotations

import hmac
import ipaddress
import random
from decimal import Decimal
from datetime import datetime, timezone
from typing import Literal

import numpy as np
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator

from grid.levels import GridConfigError, compute_lines, plan_cells
from grid.policy import validate_params
from grid.loan_cohorts import assign_loan_creation_defaults as _assign_loan_creation_defaults
from grid.loan_cohorts import create_grid_with_loan_cohort
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
        effective, result = create_grid_with_loan_cohort(
            db, body.strategy, params, control_every_n,
            lambda assigned: engine.create_grid(
                symbol, body.range_low, body.range_high, body.n_levels,
                capital=body.capital, strategy=body.strategy, params=assigned or None),
            explicit_params=raw_params,
        )
        db.add_grid_event(run_id=None, source="CLI", event_type="GRID_OPEN_API", grid_id=int(result["id"]),
            details={"who": "api", "strategy": body.strategy, "capital": str(body.capital),
                     "params": effective, "scan_snapshot": scan_row, "sigma_open": sigma_open,
                     **_loan_fields(effective)})
        return {"status": result.get("status"), "grid_id": result.get("id"), "symbol": symbol,
                **_loan_fields(effective), "warning": EXECUTION_WARNING}
    except GridConfigError as exc:
        message = str(exc).casefold()
        status = 409 if ("maximum simultaneous" in message or "already exists" in message
                         or "sell level conflict" in message) else 422
        _reject(db, status, str(exc), symbol, body, testnet_snapshot)
    except Exception as exc:
        _reject(db, 422, f"No se pudo abrir el grid: {exc}", symbol, body, testnet_snapshot)


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
            "counts": counts,
            "total_amount_lent_usdt": sum(float(row.get("amount") or 0) for row in loans
                                           if str(row.get("status", "")).upper() in lent_statuses),
            "average_repaid_open_hours": (sum(repaid_hours) / len(repaid_hours)
                                          if repaid_hours else None),
            "open_loans": [_loan_open_view(row, now) for row in loans
                           if str(row.get("status", "")).upper() == "OPEN"]}
