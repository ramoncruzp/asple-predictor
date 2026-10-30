"""Esqueleto del motor por escenarios; permanece deshabilitado."""
from grid.engines.base import DecisionEngine, EngineUnavailable


class V2ScenarioEngine(DecisionEngine):
    name = "v2"
    version = "2-skeleton"
    available = False
    _reason = "motor v2 no disponible todavía"

    def plan(self, operator, goal, *, plan_kind, allocation_free_cash=None, record=True):
        raise EngineUnavailable(self._reason)

    def review(self, operator, goal, valuation, plan):
        raise EngineUnavailable(self._reason)

    def shadow_review(self, operator, goal, valuation, plan):
        return {"decision": "no_disponible", "reason": self._reason}
