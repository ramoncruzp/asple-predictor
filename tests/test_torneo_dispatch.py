from threading import RLock
from types import SimpleNamespace


def test_revision_de_ronda_invoca_solo_el_competidor_asignado(monkeypatch):
    import grid.engines as engines
    from grid.meta_operator import MetaOperator

    calls = []

    class Spy:
        def __init__(self, name):
            self.name, self.version = name, "spy"

        def plan(self, _operator, goal, **_kwargs):
            calls.append((goal["config_snapshot"]["competidor"], self.name, "plan"))
            return {"pieces": [], "alternatives": [], "decision_id": None}

        def review(self, _operator, goal, _value, plan):
            calls.append((goal["config_snapshot"]["competidor"], self.name, "review"))
            return {"decision": "no_hacer_nada", "reason": "spy"}

        def shadow_review(self, *_args):
            raise AssertionError("la sombra no se debe ejecutar en esta prueba")

        def _complete_cazador_initial_inventory(self, *_args):
            calls.append(("cazador", "cazador", "completion"))
            return None

        def _review_cazador(self, _operator, goal, _value, _at):
            calls.append((goal["config_snapshot"]["competidor"], "cazador", "review"))
            return {"decision": "no_hacer_nada", "cazador": {"accion": "ninguna"}}

    spies = {name: Spy(name) for name in ("v1", "v2")}
    monkeypatch.setattr(engines, "ENGINES", spies)
    goals = {index: {"id": index, "status": "ACTIVE", "profile": "RAPIDO",
        "capital_initial": 50, "config_snapshot": {"competidor": competitor,
            "decision_engine": "v2" if competitor == "v2" else "v1",
            "warning_loss_pct": 13.5}}
        for index, competitor in enumerate(("v1", "v2", "cazador", "escalera", "control"), 1)}
    operator = object.__new__(MetaOperator)
    operator.core = SimpleNamespace(get_goal=lambda meta_id: goals[int(meta_id)])
    operator._operation_lock = RLock()
    operator.clock = lambda: 100000
    operator._valuation_with_retry = lambda _meta_id: ({"status": "KNOWN",
        "cash_coherent": True, "result_pct": 0, "cash_usdt": 50}, [])
    operator._reset_rotation_streaks = lambda *_args: None
    operator._record_cazador_shadow = lambda *_args: {}
    operator._record_review = lambda *_args, **_kwargs: None
    operator._touch_bot_shadow_proposals = lambda *_args: []
    operator._plan_price_moved = lambda *_args: False
    operator._run_engine_shadow = lambda *_args, **_kwargs: {"deferred": True}
    operator._persist_engine_shadow_decision = lambda *_args: None
    operator._safe_active_review_choices = lambda *_args: {}
    operator._ledger = lambda *_args, **_kwargs: None
    operator._alert_engine_failure = lambda *_args: None
    operator.cazador_status = lambda: (_ for _ in ()).throw(
        AssertionError("v2 del torneo no debe consultar ni invocar Cazador"))

    def plan_tournament(goal, competitor, **_kwargs):
        calls.append((competitor, competitor, "plan"))
        return {"competitor": competitor, "pieces": [], "alternatives": [], "decision_id": None}

    def review_tournament(goal, _value, plan):
        calls.append((goal["config_snapshot"]["competidor"], plan["competitor"], "review"))
        return {"decision": "no_hacer_nada", "reason": "spy"}

    operator._plan_tournament_policy = plan_tournament
    operator._review_tournament_policy = review_tournament

    for meta_id in goals:
        operator._review_once_locked(meta_id)

    expected = {
        "v1": [("v1", "v1", "plan"), ("v1", "v1", "review")],
        "v2": [("v2", "v2", "plan"), ("v2", "v2", "review")],
        "cazador": [("cazador", "cazador", "completion"), ("cazador", "cazador", "review")],
        "escalera": [("escalera", "escalera", "plan"), ("escalera", "escalera", "review")],
        "control": [("control", "control", "plan"), ("control", "control", "review")],
    }
    for competitor, expected_calls in expected.items():
        assert [call for call in calls if call[0] == competitor] == expected_calls
