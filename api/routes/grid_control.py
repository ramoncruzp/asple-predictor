"""Confirmed, Testnet-only grid control endpoints."""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field, StrictBool

from api.routes.grids import _authorize
from grid.control_service import run_action

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
