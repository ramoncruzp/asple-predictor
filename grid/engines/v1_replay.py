"""Adaptador que conserva el comportamiento actual del operador Meta."""
from grid.engines.base import DecisionEngine


class V1ReplayEngine(DecisionEngine):
    name = "v1"
    version = "1"
    available = True

    def plan(self, operator, goal, *, plan_kind, allocation_free_cash=None, record=True):
        if plan_kind == "analisis" and allocation_free_cash is None and record:
            return operator._build_plan(goal)
        return operator._build_plan(goal, plan_kind=plan_kind,
            allocation_free_cash=allocation_free_cash, record=record)

    def review(self, operator, goal, valuation, plan):
        return operator._review_allocations(goal, valuation, plan=plan)

    def shadow_review(self, operator, goal, valuation, plan):
        # Al ser sombra del motor v2, calcula la alternativa v1 sin persistir
        # el plan ni ejecutar acciones del operador.
        v1_plan = operator._build_plan(goal, record=False)
        choices = (v1_plan or {}).get("decision_choices") or {}
        choice = choices.get("real") or choices.get("scanner_advisor") or {}
        return {"decision": choice.get("decision", "mantener"),
            "reason": choice.get("reason"), "alternative_id": choice.get("alternative_id"),
            "source": "v1_plan_read_only"}
