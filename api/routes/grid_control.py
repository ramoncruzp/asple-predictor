"""Confirmed, Testnet-only grid control endpoints."""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field, StrictBool

from api.routes.grids import _authorize
from grid.control_service import run_action

router = APIRouter()


class ActionBase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dry_run: StrictBool = True
    confirm: StrictBool = False


class PauseBody(ActionBase):
    reason: str | None = Field(default=None, max_length=120)


class EmptyBody(ActionBase):
    pass


class CloseBody(ActionBase):
    mode: Literal["cancel", "liquidate", "repository"]
    confirm_text: str | None = None


class AdjustBody(ActionBase):
    new_low: float = Field(gt=0)
    new_high: float = Field(gt=0)
    n: int | None = Field(default=None, ge=4, le=60)


class ParamsBody(ActionBase):
    target_pct: float | None = Field(default=None, gt=0, le=100)
    target_usdt: float | None = Field(default=None, gt=0)
    target_basis: Literal["cash", "equity"] | None = None
    max_days: float | None = Field(default=None, gt=0)
    dust_sweep_threshold_pct: float | None = Field(default=None, ge=0)


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
