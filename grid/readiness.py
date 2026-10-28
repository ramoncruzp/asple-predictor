"""Criterios comunes de evidencia para interruptores de inteligencia.

Este módulo solo describe evidencia. Nunca modifica interruptores ni decisiones.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Iterable

MIN_MATURE_CASES = 30
BOOTSTRAP_RESAMPLES = 2_000
BOOTSTRAP_SEED = 41_027
CONFIDENCE_LEVEL = 0.95

STATE_COLLECTING = "Recolectando datos"
STATE_EVALUATING = "Evaluando"
STATE_READY = "Listo para encender"
STATE_NOT_RECOMMENDED = "No conviene"


def estimate_ready_at(n: int, required: int, sample_times: Iterable[datetime], *,
                      now: datetime | None = None) -> str | None:
    """Proyecta la fecha con el ritmo observado en los últimos tres días.

    Devuelve None si no hay una tasa positiva que se pueda estimar.
    """
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    cutoff = now - timedelta(days=3)
    recent = []
    for value in sample_times:
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        if cutoff <= value <= now:
            recent.append(value)
    if not recent or n >= required:
        return now.isoformat() if n >= required else None
    elapsed_days = max((now - min(recent)).total_seconds() / 86400, 1 / 24)
    rate_per_day = len(recent) / elapsed_days
    if rate_per_day <= 0:
        return None
    days_left = max(0, required - n) / rate_per_day
    return (now + timedelta(days=days_left)).isoformat()


def evaluate_readiness(*, n: int, criteria: list[dict], adverse_significant: bool = False,
                       sample_times: Iterable[datetime] = (), required: int = MIN_MATURE_CASES,
                       now: datetime | None = None, summary: str | None = None) -> dict:
    """Construye el contrato común para una señal y un perfil."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    n = max(0, int(n))
    required = max(1, int(required))
    criteria_rows = [{"text": str(row["text"]), "ok": bool(row.get("ok")),
                      "value": row.get("value")} for row in criteria]
    if n < required:
        state = STATE_COLLECTING
    elif adverse_significant:
        state = STATE_NOT_RECOMMENDED
    elif all(row["ok"] for row in criteria_rows):
        state = STATE_READY
    else:
        state = STATE_EVALUATING
    return {
        "state": state,
        "n": n,
        "n_required": required,
        "progress_pct": min(100.0, 100.0 * n / required),
        "criteria": criteria_rows,
        "estimated_ready_at": estimate_ready_at(n, required, sample_times, now=now),
        "evaluated_at": now.isoformat(),
        "summary": summary or state,
    }


def paired_bootstrap_mean_ci(values: Iterable[float], *, seed: int = BOOTSTRAP_SEED,
                             resamples: int = BOOTSTRAP_RESAMPLES) -> list[float] | None:
    """Percentile CI for a paired per-case difference; deterministic by default."""
    import random

    rows = [float(value) for value in values]
    if len(rows) < 2:
        return None
    rng = random.Random(seed)
    size = len(rows)
    means = []
    for _ in range(max(1, int(resamples))):
        means.append(sum(rows[rng.randrange(size)] for _ in range(size)) / size)
    means.sort()
    low = max(0, int((1 - CONFIDENCE_LEVEL) / 2 * len(means)))
    high = min(len(means) - 1, int((1 + CONFIDENCE_LEVEL) / 2 * len(means)))
    return [means[low], means[high]]


def probability_comparison(actual: Iterable[tuple[float, int]],
                           candidate: Iterable[tuple[float, int]]) -> dict:
    """Compara probabilidades emparejadas; diferencia negativa favorece candidate."""
    import math

    left = list(actual)
    right = list(candidate)
    paired = [(a, b) for a, b in zip(left, right) if a[1] == b[1]]
    def losses(pair):
        p = max(1e-12, min(1 - 1e-12, float(pair[0])))
        y = int(bool(pair[1]))
        return -(y * math.log(p) + (1 - y) * math.log(1 - p)), (p - y) ** 2
    if not paired:
        return {"n": 0, "actual_log_loss": None, "candidate_log_loss": None,
                "actual_brier": None, "candidate_brier": None, "log_loss_delta_ci95": None}
    actual_losses = [losses(a) for a, _ in paired]
    candidate_losses = [losses(b) for _, b in paired]
    ci = paired_bootstrap_mean_ci([b[0] - a[0] for a, b in zip(actual_losses, candidate_losses)])
    return {"n": len(paired),
        "actual_log_loss": sum(x[0] for x in actual_losses) / len(paired),
        "candidate_log_loss": sum(x[0] for x in candidate_losses) / len(paired),
        "actual_brier": sum(x[1] for x in actual_losses) / len(paired),
        "candidate_brier": sum(x[1] for x in candidate_losses) / len(paired),
        "log_loss_delta_ci95": ci}
