"""Adaptador que conserva el comportamiento actual del operador Meta."""
from grid.engines.base import DecisionEngine
import threading

V1_SHADOW_BUDGET_SECONDS = 30.0


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
        completed = threading.Event()
        state = {}
        def calculate():
            try:
                state["plan"] = operator._build_plan(goal, record=False)
            except Exception as exc:
                state["error"] = f"{type(exc).__name__}: {exc}"
            finally:
                completed.set()
        threading.Thread(target=calculate, daemon=True, name="meta-v1-shadow").start()
        if not completed.wait(V1_SHADOW_BUDGET_SECONDS):
            return {"decision": "sin_tiempo", "reason": "el análisis v1 en sombra superó 30 s",
                "source": "v1_plan_read_only"}
        if "error" in state:
            return {"decision": "error", "reason": state["error"],
                "source": "v1_plan_read_only"}
        v1_plan = state.get("plan") or {}
        choices = (v1_plan or {}).get("decision_choices") or {}
        choice = choices.get("real") or choices.get("scanner_advisor") or {}
        selected_id = choice.get("alternative_id")
        selected = next((row for row in v1_plan.get("alternatives", [])
            if row.get("alternative_id") == selected_id), None)
        return {"decision": choice.get("decision", "mantener"),
            "reason": choice.get("reason"), "alternative_id": choice.get("alternative_id"),
            "source": "v1_plan_read_only", "pieces": (selected or {}).get("pieces", []),
            "selected_alternative": selected}
