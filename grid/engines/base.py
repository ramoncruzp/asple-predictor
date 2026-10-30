"""Contrato común para motores de decisión de Modo Meta."""
from __future__ import annotations


class EngineUnavailable(RuntimeError):
    """El motor está registrado, pero todavía no se puede ejecutar."""


class DecisionEngine:
    name: str
    version: str
    available: bool

    def plan(self, operator, goal, *, plan_kind, allocation_free_cash=None, record=True):
        raise NotImplementedError

    def review(self, operator, goal, valuation, plan):
        raise NotImplementedError

    def shadow_review(self, operator, goal, valuation, plan):
        """Devuelve una evaluación informativa sin ejecutar acciones."""
        raise NotImplementedError
