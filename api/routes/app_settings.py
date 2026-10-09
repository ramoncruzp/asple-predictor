"""Allowlisted, audited application-level switch overrides."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, StrictBool

from api.routes.grids import _authorize
from config.settings import APP_FLAG_DEFINITIONS, app_flag_origin

router = APIRouter()


class FlagChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str
    value: StrictBool
    dry_run: StrictBool = True
    confirm: StrictBool = False


def _flags(request: Request) -> list[dict]:
    settings = request.app.state.settings
    overrides = {row["key"]: row["value"] for row in request.app.state.db.list_app_flag_overrides()
                 if row.get("key") in APP_FLAG_DEFINITIONS and type(row.get("value")) is bool}
    return [{
        "key": key,
        "label": definition["label"],
        "description": definition["description"],
        "current": bool(overrides[key] if key in overrides else getattr(settings, key)),
        "default": definition["default"],
        "origin": app_flag_origin(settings, key, overrides),
        "requires_restart": definition["requires_restart"],
    } for key, definition in APP_FLAG_DEFINITIONS.items()]


@router.get("/flags")
def list_flags(request: Request):
    _authorize(request)
    return {"flags": _flags(request)}


@router.post("/flags")
def change_flag(request: Request, body: FlagChange):
    _authorize(request)
    definition = APP_FLAG_DEFINITIONS.get(body.key)
    if definition is None:
        raise HTTPException(422, "Interruptor no permitido.")
    if body.dry_run and body.confirm:
        raise HTTPException(422, "confirm solo se acepta junto con dry_run=false.")
    if body.dry_run:
        current = next(row for row in _flags(request) if row["key"] == body.key)
        return {"dry_run": True, "plan": {"key": body.key, "previous": current["current"],
                "value": body.value, "origin": current["origin"],
                "requires_restart": definition["requires_restart"]}}
    if body.value is True and body.confirm is not True:
        raise HTTPException(422, "Encender requiere dry_run=false y confirm=true.")
    settings = request.app.state.settings
    current = bool(getattr(settings, body.key))
    overrides = {row["key"]: row["value"] for row in request.app.state.db.list_app_flag_overrides()
                 if row.get("key") in APP_FLAG_DEFINITIONS and type(row.get("value")) is bool}
    origin = app_flag_origin(settings, body.key, overrides)
    stored = request.app.state.db.set_app_flag_override(
        body.key, body.value, previous=current, origin=origin)
    setattr(settings, body.key, body.value)
    flag = next(row for row in _flags(request) if row["key"] == body.key)
    return {"dry_run": False, "changed": current != body.value, "flag": flag,
            "updated_at": stored["updated_at"].isoformat()}
