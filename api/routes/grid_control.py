"""Confirmed, Testnet-only grid control endpoints."""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictFloat

from api.routes.grids import _authorize
from grid.control_service import run_action, _lock, _acquire_lock

class AuditedValidationRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()

        async def handler(request: Request):
            try:
                return await original(request)
            except RequestValidationError:
                _authorize(request)
                grid_id = request.path_params.get("grid_id")
                db = getattr(request.app.state, "db", None)
                if grid_id is not None and db is not None and db.get_grid(int(grid_id)) is not None:
                    action = str(request.path_params.get("action") or request.scope.get("endpoint").__name__)
                    db.add_grid_event(run_id=None, source="CLI", grid_id=int(grid_id),
                        event_type="GRID_ACTION_REJECTED", reason=action,
                        details={"who":"api", "action":action, "reason":"request validation failed"})
                raise

        return handler


router = APIRouter(route_class=AuditedValidationRoute)


class ActionBase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dry_run: StrictBool = True
    confirm: StrictBool = False


class PauseBody(ActionBase):
    reason: str | None = Field(default=None, max_length=120)


class EmptyBody(ActionBase):
    pass


class CloseBody(ActionBase):
    mode: Literal["cancel", "liquidate", "repository", "profit_repository"]
    confirm_text: str | None = None


class AdjustBody(ActionBase):
    new_low: float = Field(gt=0)
    new_high: float = Field(gt=0)
    n: int | None = Field(default=None, ge=4, le=60)


class ParamsBody(ActionBase):
    # Business constraints run in control_service so rejected values can be audited.
    target_pct: float | None = None
    target_usdt: float | None = None
    target_basis: str | None = None
    max_days: float | None = None
    dust_sweep_threshold_pct: float | None = None
    compound_enabled: StrictBool | None = None
    compound_ratio: StrictFloat | None = None
    compound_max_growth_pct: StrictFloat | None = None


def _dispatch(request: Request, grid_id: int, action: str, body: BaseModel):
    _authorize(request)
    return run_action(request, grid_id, action,
                      body.model_dump(exclude_unset=True))


@router.post("/{grid_id}/pause")
def pause(request: Request, grid_id: int, body: PauseBody): return _dispatch(request, grid_id, "pause", body)


@router.post("/{grid_id}/resume")
def resume(request: Request, grid_id: int, body: EmptyBody): return _dispatch(request, grid_id, "resume", body)


@router.post("/{grid_id}/close")
def close(request: Request, grid_id: int, body: CloseBody): return _dispatch(request, grid_id, "close", body)


@router.post("/{grid_id}/adjust")
def adjust(request: Request, grid_id: int, body: AdjustBody): return _dispatch(request, grid_id, "adjust", body)


@router.post("/{grid_id}/sweep-dust")
def sweep_dust(request: Request, grid_id: int, body: EmptyBody): return _dispatch(request, grid_id, "sweep-dust", body)


@router.post("/{grid_id}/params")
def update_params(request: Request, grid_id: int, body: ParamsBody): return _dispatch(request, grid_id, "params", body)


@router.post("/{grid_id}/loans/disable")
def disable_grid_loans(request: Request, grid_id: int, body: EmptyBody):
    _authorize(request)
    db = getattr(request.app.state, "db", None)
    engine = getattr(request.app.state, "grid_engine", None)
    client = getattr(request.app.state, "testnet_client", None)
    if client is None or getattr(getattr(client, "client", None), "testnet", None) is not True or engine is None:
        raise HTTPException(503, "Grid Testnet no disponible o no confirmado.")
    lock = _lock(request)
    _acquire_lock(lock, grid_id, "disable-loans")
    try:
        grid = db.get_grid(int(grid_id))
        if grid is None:
            raise HTTPException(404, "Grid no existe.")
        params = dict(grid.get("params") or {})
        if grid.get("strategy") != "smart" or params.get("loans_enabled") is not True:
            raise HTTPException(409, "El grid Smart no tiene préstamos activos.")
        if str(grid.get("status", "")).upper() not in {"ACTIVE", "PAUSED", "HOLDING"}:
            raise HTTPException(409, "El estado actual no permite apagar préstamos.")
        loans = db.list_grid_loans(int(grid_id))
        open_loans = [row for row in loans if str(row.get("status", "")).upper() == "OPEN"]
        pending = [row for row in loans if str(row.get("status", "")).upper() == "PENDING"]
        plan = {"action": "disable-loans", "grid_id": int(grid_id),
                "loans_open_count": len(open_loans),
                "loans_open_amount_usdt": sum(float(row.get("amount") or 0) for row in open_loans),
                "pending_count": len(pending), "loans_enabled_after": False,
                "settlement": "transferencia contable de los préstamos OPEN"}
        if body.dry_run and body.confirm or not body.dry_run and not body.confirm:
            raise HTTPException(422, "Ejecutar requiere dry_run=false y confirm=true.")
        if pending:
            raise HTTPException(409, "Hay préstamos PENDING; resuelve la saga antes de apagar préstamos.")
        if body.dry_run:
            return {"dry_run": True, "plan": plan}
        transferred = engine.transfer_loans(int(grid_id), "loans_disabled")
        remaining = [row for row in db.list_grid_loans(int(grid_id))
                     if str(row.get("status", "")).upper() == "OPEN"]
        if remaining:
            raise HTTPException(409, "No se pudieron transferir todos los préstamos OPEN.")
        db.merge_grid_params(int(grid_id), {"loans_enabled": False},
                             allowed=frozenset({"loans_enabled"}))
        db.add_grid_event(run_id=None, source="CLI", grid_id=int(grid_id),
            event_type="GRID_ACTION_API", reason="disable-loans",
            details={"who": "api", "action": "disable-loans", "loans_transferred": transferred,
                     "loans_enabled_after": False, "dry_run": False})
        return {"dry_run": False, "result": {"ok": True, "loans_transferred": transferred},
                "loans_enabled": False, "loans_group": params.get("loans_group")}
    finally:
        lock.release()
