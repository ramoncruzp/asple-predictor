"""Authenticated API routes for user-triggered direction-model training."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, StrictBool, StrictInt, model_validator

from config.models_config import ACTIVE_INTERVAL, ACTIVE_SYMBOL
from models.training_jobs import ActiveTrainingJob, TRAINING_MAX_DAYS, TRAINING_MIN_DAYS

router = APIRouter()


class TrainingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbol: str
    interval: str
    models: list[str]
    days: StrictInt
    confirm: StrictBool = False
    confirm_reset_evaluation: StrictBool = False

    @model_validator(mode="after")
    def validate_training(self):
        self.symbol = self.symbol.strip().upper().replace("/", "")
        self.interval = self.interval.strip().lower()
        if self.confirm is not True:
            raise ValueError("Se requiere confirm=true")
        if self.symbol != ACTIVE_SYMBOL or self.interval != ACTIVE_INTERVAL:
            raise ValueError("Sin modelos de dirección habilitados para esa combinación; llegará con 20B-2")
        if not self.models or len(set(self.models)) != len(self.models) or any(name not in {"a", "b", "c"} for name in self.models):
            raise ValueError("models debe ser una lista no vacía y sin repetidos de a, b o c")
        if "a" in self.models and self.confirm_reset_evaluation is not True:
            raise ValueError("Model A requiere confirm_reset_evaluation=true para reiniciar su evaluación en vivo")
        if not TRAINING_MIN_DAYS <= self.days <= TRAINING_MAX_DAYS:
            raise ValueError(f"days debe estar entre {TRAINING_MIN_DAYS} y {TRAINING_MAX_DAYS}")
        return self


@router.post("/train", status_code=201)
def create_training(body: TrainingRequest, request: Request):
    service = request.app.state.training_job_service
    try:
        return {"job": service.create_job(
            symbol=body.symbol, interval=body.interval, models=body.models, days=body.days,
            confirm_reset_evaluation=body.confirm_reset_evaluation,
        )}
    except ActiveTrainingJob as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/train/status")
def training_status(request: Request):
    return {"job": request.app.state.training_job_service.active_or_latest()}


@router.get("/train/jobs")
def training_jobs(request: Request, limit: int = Query(10, ge=1, le=50)):
    return {"jobs": request.app.state.training_job_service.list_jobs(limit)}


@router.post("/train/{job_id}/cancel")
def cancel_training(job_id: int, request: Request):
    result = request.app.state.training_job_service.cancel(job_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Trabajo de entrenamiento no encontrado")
    if result is False:
        raise HTTPException(status_code=409, detail="El trabajo ya no está activo")
    return {"job": result}
