"""Interfaz experimental de escenarios, sin conexión al operador v1."""

from research.scenarios.evaluate import evaluate_history, evaluate_pieces
from research.scenarios.objective import objective_limits, objective_v2, rank_alternatives_v2

__all__ = [
    "evaluate_history",
    "evaluate_pieces",
    "objective_limits",
    "objective_v2",
    "rank_alternatives_v2",
]
