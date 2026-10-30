"""Registro de motores de decisión de Meta."""
from grid.engines.base import DecisionEngine, EngineUnavailable
from grid.engines.v1_replay import V1ReplayEngine
from grid.engines.v2_escenarios import V2ScenarioEngine

DEFAULT_ENGINE = "v1"
ENGINES = {"v1": V1ReplayEngine(), "v2": V2ScenarioEngine()}


def get_engine(name):
    return ENGINES.get(str(name or "").strip().lower(), ENGINES[DEFAULT_ENGINE])


__all__ = ["DEFAULT_ENGINE", "ENGINES", "DecisionEngine", "EngineUnavailable", "get_engine"]
