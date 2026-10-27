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


class LearningLimits(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_templates_per_run: int = Field(ge=1, le=500)
    time_budget_minutes: float = Field(ge=1, le=240)


class LearningSwitch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    profile: str
    enabled: StrictBool
    confirm: StrictBool = False
    actor: str = Field(min_length=1, max_length=80)


class AllocationSwitch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    profile: str
    enabled: StrictBool
    confirm: StrictBool = False
    actor: str = Field(min_length=1, max_length=80)


class StartPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    plan_id: str = Field(min_length=16, max_length=64)


def _operator(request: Request):
    _authorize(request)
    operator = getattr(request.app.state, "meta_operator", None)
    if operator is None:
        raise HTTPException(503, "Operador Modo Meta no disponible.")
    return operator


@router.get("/config")
def get_config(request: Request):
    return _core(request).config()


@router.put("/config/profile")
def update_profile(request: Request, body: ProfileSettings):
    return {"profile": body.profile.upper(), "settings": _call(_core(request).set_config, body.profile, body.values)}


@router.put("/config/max-active")
def update_max_active(request: Request, body: MaxActive):
    return {"max_active": _call(_core(request).set_max_active, body.value)}


@router.put("/config/learning-limits")
def update_learning_limits(request: Request, body: LearningLimits):
    _authorize(request)
    return _call(_core(request).set_learning_limits,
        max_templates_per_run=body.max_templates_per_run,
        time_budget_minutes=body.time_budget_minutes)


@router.get("/learning")
def learning_status(request: Request, profile: str = "RAPIDO"):
    return _operator(request).learning_status(profile)


@router.put("/learning/switch")
def update_learning_switch(request: Request, body: LearningSwitch):
    return _call(_operator(request).set_learning_switch, body.profile, body.enabled,
        confirm=body.confirm, actor=body.actor)


@router.put("/allocation/switch")
def update_allocation_switch(request: Request, body: AllocationSwitch):
    return _call(_operator(request).set_extended_allocation_switch, body.profile, body.enabled,
        confirm=body.confirm, actor=body.actor)


@router.get("")
def list_goals(request: Request):
    return {"metas": _core(request).list_goals()}


@router.get("/piece-links")
def piece_links(request: Request):
    """Para la pantalla Grids: qué grid pertenece a qué meta, sin exponer
    toda la meta. No es un sub-recurso de {meta_id} porque abarca todas."""
    return {"links": _core(request).list_active_piece_links()}


@router.post("")
def create_goal(request: Request, body: GoalCreate):
    return _call(_core(request).create_goal, body.capital_usdt, body.target_value,
                 body.target_type, body.profile)


@router.get("/{meta_id}")
def get_goal(request: Request, meta_id: int):
    return _call(_core(request).get_goal, meta_id)


@router.post("/{meta_id}/discard")
def discard_goal(request: Request, meta_id: int):
    return _call(_core(request).discard_goal, meta_id)


@router.get("/{meta_id}/runs")
def list_runs(request: Request, meta_id: int):
    return {"runs": _call(_core(request).list_runs, meta_id)}


@router.post("/{meta_id}/runs/{run_id}/control-comparison")
def control_comparison(request: Request, meta_id: int, run_id: int):
    runs = _call(_core(request).list_runs, meta_id)
    if not any(int(row["id"]) == int(run_id) for row in runs):
        raise HTTPException(404, "La corrida no pertenece a esta meta.")
    return _call(_operator(request).compare_control, run_id)


@router.post("/{meta_id}/analysis")
def analyze_goal(request: Request, meta_id: int):
    return _call(_operator(request).analyze, meta_id)


@router.get("/{meta_id}/analysis/{job_id}")
def analysis_status(request: Request, meta_id: int, job_id: str):
    result = _call(_operator(request).job, job_id)
    if int(result.get("meta_id", -1)) != int(meta_id):
        raise HTTPException(404, "Análisis no encontrado.")
    return result


@router.post("/{meta_id}/start")
def start_goal(request: Request, meta_id: int, body: StartPlan):
    core = _core(request)
    try:
        core._enabled_testnet()
        core.start()
    except Exception as exc:
        raise HTTPException(503, f"No se pudo iniciar vigilancia Meta: {exc}") from exc
    from api.routes.grids import OpenRequest, _open_grid
    def open_piece(piece):
        goal = core.get_goal(meta_id)
        if piece["strategy"] == "smart":
            from grid.meta import smart_grid_params_for_goal
            params = smart_grid_params_for_goal(goal, target_usdt=piece.get("target_usdt"))
        else:
            params = None
        return _open_grid(request, OpenRequest(
            symbol=piece["symbol"], strategy=piece["strategy"], capital=piece["capital_usdt"],
            range_low=piece["floor"], range_high=piece["ceiling"], n_levels=piece["n_levels"],
            params=params,
            dry_run=False, confirm=True,
        ), meta_id=meta_id)
    return _call(_operator(request).start_plan, meta_id, body.plan_id, open_piece)


@router.post("/{meta_id}/pieces")
def link_piece(request: Request, meta_id: int, body: PieceLink):
    return _call(_core(request).link_grid, meta_id, body.grid_id, body.piece_type)


@router.get("/{meta_id}/valuation")
def valuation(request: Request, meta_id: int):
    return _call(_core(request).valuation, meta_id)


@router.get("/{meta_id}/ledger")
def ledger(request: Request, meta_id: int, page: int = 1, page_size: int = 50,
           include_valuation: bool = False):
    return _call(_core(request).ledger_page, meta_id, page=page, page_size=page_size,
                 include_valuation=include_valuation)


@router.post("/{meta_id}/cancel-plan")
def cancellation_plan(request: Request, meta_id: int):
    return _call(_core(request).cancellation_plan, meta_id)


@router.post("/{meta_id}/cancel")
def cancel(request: Request, meta_id: int, body: CancelRequest):
    return _call(_core(request).cancel, meta_id, body.mode, body.confirm, body.plan_id)
