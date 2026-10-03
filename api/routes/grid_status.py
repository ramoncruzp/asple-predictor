"""Read-only API for the Fase 17A grid status screen. GET only, no writes.

Reuses ``_authorize`` from ``api.routes.grids`` (imported, not copied) for the
same X-API-Token/loopback rule. Registered under the same ``/api/grids`` prefix
as that module's ``POST /scan`` and ``POST /open`` -- no path here collides
with those because every route is GET and every path segment after the prefix
is either empty, ``/{grid_id}`` (an int, so "scan"/"open" fail to parse and
FastAPI answers 422, never routing into a grid lookup) or a two-segment path
like ``/{grid_id}/operations``.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import threading
import time

from fastapi import APIRouter, HTTPException, Query, Request

from api.routes.grids import _authorize
from grid import status_view as sv

# A single router with absolute paths (rather than two routers needing two
# separate prefixes) so api/main.py only needs one app.include_router(...)
# line, per this phase's scope rule.
router = APIRouter()

REPOSITORY_AND_OPEN_STATUSES = sv.OPEN_STATUSES | {"HOLDING"}
_PRICE_CACHE_TTL = 5.0


def _price_cache_for(request: Request) -> tuple[dict[str, tuple[float, float, datetime]], threading.Lock]:
    state = request.app.state
    cache_state = getattr(state, "grid_status_price_cache", None)
    if cache_state is None:
        cache_state = {"cache": {}, "lock": threading.Lock()}
        state.grid_status_price_cache = cache_state
    return cache_state["cache"], cache_state["lock"]


def _price_for_symbol(request: Request, symbol: str) -> tuple[float | None, datetime | None]:
    """Current mid price from the same source the monitor uses (Testnet book ticker)."""
    client = getattr(request.app.state, "testnet_client", None)
    if client is None:
        return None, None
    now_mono = time.monotonic()
    cache, cache_lock = _price_cache_for(request)
    with cache_lock:
        cached = cache.get(symbol)
        if cached and now_mono - cached[0] < _PRICE_CACHE_TTL:
            return cached[1], cached[2]
    try:
        book = client.get_book_ticker(symbol)
        from decimal import Decimal
        mid = float((Decimal(str(book["bid_price"])) + Decimal(str(book["ask_price"]))) / Decimal(2))
        as_of = datetime.now(timezone.utc)
        with cache_lock:
            cache[symbol] = (now_mono, mid, as_of)
            for key, value in list(cache.items()):
                if now_mono - value[0] >= _PRICE_CACHE_TTL:
                    cache.pop(key, None)
        return mid, as_of
    except Exception:
        return None, None


def _grid_or_404(db, grid_id: int) -> dict:
    grid = db.get_grid(grid_id)
    if grid is None:
        raise HTTPException(404, f"Grid {grid_id} no existe.")
    return grid


def _range_cutoff(range_param: str, now: datetime) -> datetime:
    value = str(range_param or "30d").strip().lower()
    try:
        if value.endswith("d"):
            return now - timedelta(days=float(value[:-1]))
        if value.endswith("m"):
            return now - timedelta(days=float(value[:-1]) * 30.0)
        if value.endswith("h"):
            return now - timedelta(hours=float(value[:-1]))
    except ValueError:
        pass
    return now - timedelta(days=30)


def _build_summary(request: Request, grid: dict, levels: list[dict], now: datetime) -> dict:
    fee_pct = float(request.app.state.settings.scanner_fee_pct)
    price, price_as_of = _price_for_symbol(request, grid["symbol"])
    return sv.grid_summary(grid, levels, price, now=now, fee_pct=fee_pct, price_as_of=price_as_of)


@router.get("/api/grids")
def list_grids(request: Request, status: str | None = Query(default=None)):
    _authorize(request)
    db = request.app.state.db
    now = datetime.now(timezone.utc)
    if status:
        wanted = {part.strip().upper() for part in status.split(",") if part.strip()}
        grids = db.list_grids_by_status(wanted)
    else:
        grids = db.list_grids_by_status(REPOSITORY_AND_OPEN_STATUSES)
    summaries = [_build_summary(request, grid, db.get_grid_levels(int(grid["id"])), now) for grid in grids]
    client = getattr(request.app.state, "testnet_client", None)
    free_usdt, unavailable_reason = None, None
    if client is None:
        unavailable_reason = "cliente Testnet no disponible"
    else:
        try:
            balance = client.get_balance("USDT")
            free_usdt = balance.get("USDT", {}).get("free")
        except Exception as exc:
            unavailable_reason = f"no se pudo leer el saldo de la cuenta en Testnet: {exc}"
    totals = sv.account_totals(summaries, free_usdt, unavailable_reason)
    return {
        "grids": summaries, "totals": totals,
        "same_symbol_warning": sv.same_symbol_warning(summaries),
    }


@router.get("/api/grids/{grid_id}")
def grid_detail(request: Request, grid_id: int):
    _authorize(request)
    db = request.app.state.db
    grid = _grid_or_404(db, grid_id)
    levels = db.get_grid_levels(grid_id)
    now = datetime.now(timezone.utc)
    summary = _build_summary(request, grid, levels, now)
    cells = sv.cells_view(levels, summary["price"], float(request.app.state.settings.scanner_fee_pct),
                          grid_paused=(grid["status"] == "PAUSED"))
    return {"summary": summary, "cells": cells, "config": grid.get("params") or {},
            "range_low": grid.get("range_low"), "range_high": grid.get("range_high"),
            "n_levels": grid.get("n_levels"), "environment": grid.get("environment")}


@router.get("/api/grids/{grid_id}/operations")
def grid_operations(request: Request, grid_id: int, limit: int = Query(default=50, ge=1, le=500)):
    _authorize(request)
    db = request.app.state.db
    _grid_or_404(db, grid_id)
    events = db.list_grid_events(grid_id=grid_id, limit=5000)
    levels = db.get_grid_levels(grid_id)
    return {"operations": sv.operations_view(events, limit=limit, levels=levels)}


@router.get("/api/grids/{grid_id}/events")
def grid_events(request: Request, grid_id: int, limit: int = Query(default=50, ge=1, le=500)):
    _authorize(request)
    db = request.app.state.db
    _grid_or_404(db, grid_id)
    events = db.list_grid_events(grid_id=grid_id, limit=limit)
    return {"events": sv.events_view(events)}


@router.get("/api/grids/{grid_id}/daily")
def grid_daily(request: Request, grid_id: int, range: str = Query(default="30d")):
    _authorize(request)
    db = request.app.state.db
    grid = _grid_or_404(db, grid_id)
    levels = db.get_grid_levels(grid_id)
    now = datetime.now(timezone.utc)
    cutoff = _range_cutoff(range, now)
    created_at = grid.get("created_at")
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    else:
        created_at = created_at.astimezone(timezone.utc)
    covers_lifetime = cutoff <= created_at
    relevant_types = ("SELL_FILLED", "CELL_STOPLOSS", "CELL_LIQUIDATED")
    fetched = []
    truncated = False
    for event_type in relevant_types:
        batch = db.list_grid_events(grid_id=grid_id, event_type=event_type, limit=5001)
        truncated = truncated or len(batch) > 5000
        fetched.extend(batch[:5000])
    fetched.sort(key=lambda event: (event["ts"], event.get("id", 0)), reverse=True)
    truncated = truncated or len(fetched) > 5000
    events = [event for event in fetched[:5000]
              if event["ts"].replace(tzinfo=event["ts"].tzinfo or timezone.utc) >= cutoff]
    net_realized = sum((float(level.get("pnl") or 0) for level in levels), 0.0)
    return sv.daily_profit_view(events, net_realized_usdt=net_realized, truncated=truncated,
        reconcile_available=covers_lifetime and not truncated)


@router.get("/api/grids/{grid_id}/equity")
def grid_equity(request: Request, grid_id: int, range: str = Query(default="30d")):
    _authorize(request)
    db = request.app.state.db
    _grid_or_404(db, grid_id)
    now = datetime.now(timezone.utc)
    cutoff = _range_cutoff(range, now)
    snapshots = [row for row in db.list_grid_snapshots(grid_id=grid_id, limit=20000)
                 if row["ts"].replace(tzinfo=row["ts"].tzinfo or timezone.utc) >= cutoff]
    return sv.equity_curve_view(snapshots)


@router.get("/api/monitor/status")
def monitor_status(request: Request):
    _authorize(request)
    db = request.app.state.db
    last_run = db.get_last_monitor_run()
    return {
        "last_run": last_run,
        "testnet_reset_suspected": bool(getattr(getattr(request.app.state, "grid_monitor", None),
                                                  "testnet_reset_suspected", False)),
        "testnet_reset_since": getattr(getattr(request.app.state, "grid_monitor", None),
                                        "testnet_reset_since", None),
        "gap_minutes_configured": int(request.app.state.settings.grid_monitor_gap_minutes),
        "contract_version": None,
        "contract_version_unavailable_reason": (
            "no existe un concepto de version de contrato en el codigo actual "
            "(ver DISCREPANCIAS_FASE17A.md)"
        ),
    }
