"""Regresiones de revisión Meta sin red ni base real (Fase 41b-11)."""
import ast
import copy
import subprocess
from datetime import datetime, timezone
from types import MethodType

import pytest

from grid.meta_operator import MetaOperator


def review_setup(*, current_present=True):
    class Core:
        def __init__(self):
            self.events = []

        def get_goal(self, _meta_id):
            return {"id": 3, "status": "ACTIVE", "config_snapshot": {"warning_loss_pct": 13.5}}

        def valuation(self, _meta_id):
            return {"status": "KNOWN", "result_pct": -1, "cash_usdt": 30}

        def ledger(self, _meta_id):
            return self.events

        def _ledger(self, meta_id, run_id, event_type, details, **kwargs):
            self.events.append({"event_type": event_type, "details": copy.deepcopy(details),
                "grid_id": kwargs.get("grid_id"), "ts": datetime.fromtimestamp(1_800_000_000, timezone.utc)})

    class DB:
        def get_grid(self, _grid_id):
            return {"id": 12, "symbol": "JUPUSDT", "strategy": "smart", "status": "ACTIVE",
                "created_at": datetime.fromtimestamp(1_800_000_000 - 7200, timezone.utc)}

    def alternative(alt_id, symbol, strategy, probability):
        return {"alternative_id": alt_id, "symbol": symbol, "strategy": strategy,
            "estimate": {"probability": probability, "median_progress_pct": 20},
            "shadow_probability": probability, "learned_probability": probability,
            "pieces": [{"symbol": symbol, "strategy": strategy, "piece_type": "grid",
                "floor": 90, "ceiling": 110, "n_levels": 4, "capital_usdt": 70}]}

    current = alternative("actual", "JUPUSDT", "smart", .5)
    proposed = alternative("alternativa", "XRPUSDT", "simple", .6)
    plan = {"decision_id": "revision-1", "alternatives": ([current] if current_present else []) + [proposed],
        "candidates": [{"symbol": "JUPUSDT", "excluded_reason":
            None if current_present else "alternativa no abrible en Testnet: conflicto con niveles de venta vivos"}],
        "decision_choices": {name: {"alternative_id": "alternativa", "symbol": "XRPUSDT", "probability": .6}
            for name in ("scanner_advisor", "learned")}}
    result = {"decision": "no_hacer_nada", "grid_id": 12, "reason": "mantener la pieza"}
    core = Core()
    operator = MetaOperator(core, DB(), None, None, None, clock=lambda: 1_800_000_000)
    operator._build_plan = lambda *_args, **_kwargs: copy.deepcopy(plan)
    operator._plan_price_moved = lambda *_args: False
    operator._review_allocations = lambda *_args, **_kwargs: copy.deepcopy(result)
    return operator, core, plan, result


def test_review_records_real_decision_when_current_alternative_is_missing():
    operator, core, plan, expected = review_setup(current_present=False)
    assert operator.review_once(3) == expected
    review = next(event["details"] for event in core.events
        if event["event_type"] == "META_OPERATOR_REVIEW_DECISION")
    assert review["decision"] == expected
    assert review["decision_choices"]["current_alternative_missing"] is True
    assert review["decision_choices"]["current_alternative_missing_reason"] == plan["candidates"][0]["excluded_reason"]
    assert any(event["event_type"] == "META_OPERATOR_NOOP" for event in core.events)
    assert "decision_choices_error" not in review


@pytest.mark.parametrize(("path", "decision"), [("review_once", "no_hacer_nada"),
    ("review_once", "reajustar_pieza"), ("record_review", "no_hacer_nada")])
def test_comparison_failure_preserves_real_decision_and_logs_traceback(path, decision, caplog):
    operator, core, plan, expected = review_setup()
    expected["decision"] = decision
    operator._review_allocations = lambda *_args, **_kwargs: expected

    def fail(*_args):
        raise ValueError("comparación inválida")

    operator._active_review_choices = fail
    if path == "review_once":
        assert operator.review_once(3) == expected
    else:
        # Esta ruta registra los NOOP de bloqueos y errores de análisis.
        operator._record_review(3, expected, core.valuation(3), None, plan)
    review = next(event["details"] for event in core.events
        if event["event_type"] == "META_OPERATOR_REVIEW_DECISION")
    assert review["decision"] == expected
    assert review["decision_choices"]["real"]["decision"] == operator._review_choice_label(expected)
    assert review["decision_choices_error"] == "ValueError: comparación inválida"
    noops = [event["details"] for event in core.events if event["event_type"] == "META_OPERATOR_NOOP"]
    assert bool(noops) == (decision == "no_hacer_nada")
    if noops:
        assert noops[0]["decision_choices_error"] == review["decision_choices_error"]
    assert any(record.exc_info for record in caplog.records if "comparación" in record.message)


def test_normal_review_matches_dd10892_comparison_and_real_decision():
    """Referencia local fijada al commit solicitado; no importa el módulo antiguo."""
    import grid.meta_operator as module

    source = subprocess.check_output(["git", "show", "dd10892:grid/meta_operator.py"]).decode("utf-8")
    cls = next(node for node in ast.parse(source).body if isinstance(node, ast.ClassDef)
        and node.name == "MetaOperator")
    method = next(node for node in cls.body if isinstance(node, ast.FunctionDef)
        and node.name == "_active_review_choices")
    namespace = dict(vars(module))
    exec(compile(ast.Module(body=[method], type_ignores=[]), "dd10892:_active_review_choices", "exec"), namespace)
    actual, actual_core, _, _ = review_setup()
    reference, reference_core, _, _ = review_setup()
    reference._active_review_choices = MethodType(namespace["_active_review_choices"], reference)
    for _ in range(2):
        assert actual.review_once(3) == reference.review_once(3)
        assert actual_core.events == reference_core.events


def test_rotation_gate_with_no_current_alternative_keeps_existing_guard():
    operator, core, plan, _ = review_setup(current_present=False)
    grid = operator.db.get_grid(12)
    gate = operator._rotation_gate(core.get_goal(3), grid, {}, plan["alternatives"][0], None, plan)
    assert gate == {"allowed": False, "reason": "no hay estimación comparable de la pieza actual"}
