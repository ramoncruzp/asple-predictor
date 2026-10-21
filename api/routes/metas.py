"""API del núcleo Modo Meta; no contiene decisiones de operador ni UI."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, StrictBool

from api.routes.grids import _authorize

router = APIRouter()


def _core(request: Request):
    _authorize(request)
    core = getattr(request.app.state, "meta_core", None)
    if core is None:
        raise HTTPException(503, "Núcleo Modo Meta no disponible.")
    return core


def _call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc


class GoalCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    capital_usdt: float = Field(gt=0)
    target_value: float = Field(gt=0)
    target_type: str = "USDT"
    profile: str = "PRUDENTE"


class PieceLink(BaseModel):
    model_config = ConfigDict(extra="forbid")
    grid_id: int = Field(gt=0)
    piece_type: str = "grid"


class CancelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: str
    confirm: StrictBool = False
    plan_id: str | None = None


class ProfileSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    profile: str
    values: dict[str, Any]


class MaxActive(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: int = Field(ge=1, le=10)


@router.get("/config")
def get_config(request: Request):
    return _core(request).config()


@router.put("/config/profile")
def update_profile(request: Request, body: ProfileSettings):
    return {"profile": body.profile.upper(), "settings": _call(_core(request).set_config, body.profile, body.values)}


@router.put("/config/max-active")
def update_max_active(request: Request, body: MaxActive):
    return {"max_active": _call(_core(request).set_max_active, body.value)}


@router.get("")
def list_goals(request: Request):
    return {"metas": _core(request).list_goals()}


@router.post("")
def create_goal(request: Request, body: GoalCreate):
    return _call(_core(request).create_goal, body.capital_usdt, body.target_value,
                 body.target_type, body.profile)


@router.get("/{meta_id}")
def get_goal(request: Request, meta_id: int):
    return _call(_core(request).get_goal, meta_id)


@router.post("/{meta_id}/start")
def start_goal(request: Request, meta_id: int):
    core = _core(request)
    try:
        core._enabled_testnet()
        core.start()
    except Exception as exc:
        raise HTTPException(503, f"No se pudo iniciar vigilancia Meta: {exc}") from exc
    return _call(core.start_goal, meta_id)


@router.post("/{meta_id}/pieces")
def link_piece(request: Request, meta_id: int, body: PieceLink):
    return _call(_core(request).link_grid, meta_id, body.grid_id, body.piece_type)


@router.get("/{meta_id}/valuation")
def valuation(request: Request, meta_id: int):
    return _call(_core(request).valuation, meta_id)


@router.get("/{meta_id}/ledger")
def ledger(request: Request, meta_id: int):
    return {"events": _call(_core(request).ledger, meta_id)}


@router.post("/{meta_id}/cancel-plan")
def cancellation_plan(request: Request, meta_id: int):
    return _call(_core(request).cancellation_plan, meta_id)


@router.post("/{meta_id}/cancel")
def cancel(request: Request, meta_id: int, body: CancelRequest):
    return _call(_core(request).cancel, meta_id, body.mode, body.confirm, body.plan_id)
