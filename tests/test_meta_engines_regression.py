import copy
from datetime import datetime, timezone

import pytest

from grid.meta_operator import MetaOperator


@pytest.mark.parametrize("decision", [
    "no_hacer_nada", "reajustar_pieza", "retirar_y_abrir",
    "abrir_pieza_con_efectivo_libre",
])
def test_v1_review_keeps_plan_result_and_orders_for_existing_decision_branches(decision):
    class Core:
        def __init__(self): self.events = []
        def get_goal(self, _meta_id): return goal
        def valuation(self, _meta_id):
            return {"status": "KNOWN", "result_pct": 0, "cash_usdt": 40}
        def _ledger(self, *args, **kwargs): self.events.append((args, kwargs))
        def ledger(self, _meta_id): return []

    goal = {"id": 9, "status": "ACTIVE", "profile": "PRUDENTE",
        "config_snapshot": {"decision_engine": "v1", "warning_loss_pct": 6}}
    base_plan = {"profile": "PRUDENTE", "decision_id": "replay-43", "pieces": [],
        "candidates": [{"symbol": "XRPUSDT", "eligible": True}],
        "alternatives": [{"alternative_id": "replay-43:0", "symbol": "XRPUSDT",
            "strategy": "simple", "pieces": [{"symbol": "XRPUSDT", "piece_type": "grid",
                "strategy": "simple", "capital_usdt": 40, "floor": 90, "ceiling": 110}],
            "estimate": {"probability": .6, "evidence_sufficient": True}}],
        "current_pieces": {}, "decision_choices": {"comparison_version": 2,
            "scanner_advisor": {"decision": "mantener", "reason": "sin mejora material"},
            "learned": {"decision": "mantener"}}, "learning": {"enabled": False}}
    valuation = {"status": "KNOWN", "result_pct": 0, "cash_usdt": 40}

    def run_through_legacy_calls():
        core = Core()
        sent_orders = []
        operator = MetaOperator(core, None, None, None, None, clock=lambda: 1_800_000_000)
        operator._sleep = lambda _seconds: None
        operator._build_plan = lambda _goal, **_kwargs: copy.deepcopy(base_plan)
        operator._plan_price_moved = lambda _plan, _goal: False
        operator._touch_bot_shadow_proposals = lambda *_args: []
        operator._safe_active_review_choices = lambda plan, _result, _meta_id: copy.deepcopy(plan["decision_choices"])
        def review(_goal, _valuation, *, plan=None):
            sent_orders.append(decision if decision != "no_hacer_nada" else None)
            return {"decision": decision, "reason": "fixture de rama",
                "alternative_id": "replay-43:0"}
        operator._review_allocations = review
        legacy_plan = operator._build_plan(goal, plan_kind="revision",
            allocation_free_cash=valuation["cash_usdt"])
        legacy_decision = operator._review_allocations(goal, valuation, plan=legacy_plan)
        legacy_orders = list(sent_orders)
        sent_orders.clear()
        result = operator._review_once_locked(goal["id"])
        event = next(args[0][3] for args in core.events if args[0][2] == "META_OPERATOR_REVIEW_DECISION")
        assert event["engine"] == {"name": "v1", "version": "1"}
        assert event["engine_shadow"]["name"] == "v2"
        return legacy_plan, legacy_decision, legacy_orders, list(sent_orders), result, event

    legacy_plan, legacy_decision, legacy_orders, routed_orders, routed_result, event = run_through_legacy_calls()
    assert {k: v for k, v in legacy_plan.items()} == {k: v for k, v in base_plan.items()}
    assert routed_result == legacy_decision
    assert routed_orders == legacy_orders == ([None] if decision == "no_hacer_nada" else [decision])
    assert event["decision"]["decision"] == decision
    assert event["decision_choices"]["scanner_advisor"]["decision"] == "mantener"


def test_engine_missing_from_legacy_goal_defaults_to_v1_and_start_rejects_mismatch():
    class Core:
        def __init__(self): self.started = False
        def get_goal(self, _meta_id): return {"config_snapshot": {"decision_engine": "v2"}}
        def start_goal(self, _meta_id): self.started = True

    core = Core()
    operator = MetaOperator(core, None, None, None, None, clock=lambda: 100)
    assert operator._engine_for_goal({"config_snapshot": {}}).name == "v1"
    operator._plans["plan"] = {"meta_id": 1, "expires_at": 200,
        "engine": {"name": "v1", "version": "1"}, "pieces": []}
    with pytest.raises(ValueError, match="El plan fue calculado con otro motor; vuelve a analizar"):
        operator.start_plan(1, "plan", lambda _piece: None)
    assert core.started is False


def test_created_goal_keeps_profile_engine_snapshot_after_profile_setting_changes(tmp_path):
    import json
    from types import SimpleNamespace
    from sqlalchemy import select
    from database.db_manager import DBManager
    from grid.meta import MetaCore

    class Testnet:
        testnet = True

    db = DBManager(f"sqlite:///{tmp_path / 'engine-snapshot.sqlite'}")
    core = MetaCore(db, Testnet(), settings=SimpleNamespace(
        meta_mode_enabled=True, meta_max_active=1, alerts_enabled=False))
    goal = core.create_goal(100, 10, profile="PRUDENTE")
    assert goal["config_snapshot"]["decision_engine"] == "v1"
    with db.engine.begin() as conn:
        row = conn.execute(select(db.meta_profile_settings.c.settings).where(
            db.meta_profile_settings.c.profile == "PRUDENTE")).scalar_one()
        settings = json.loads(row)
        settings["decision_engine"] = "v2"
        conn.execute(db.meta_profile_settings.update().where(
            db.meta_profile_settings.c.profile == "PRUDENTE").values(settings=json.dumps(settings)))
    assert core.get_goal(goal["id"])["config_snapshot"]["decision_engine"] == "v1"


def test_active_engine_failure_is_noop_and_alert_is_limited_to_once_per_hour():
    alerts = []
    class Core:
        def __init__(self): self.events = []
        def get_goal(self, _meta_id): return {"id": 3, "status": "ACTIVE",
            "config_snapshot": {"decision_engine": "v1", "warning_loss_pct": 6}}
        def valuation(self, _meta_id): return {"status": "KNOWN", "result_pct": 0}
        def _ledger(self, *args, **kwargs): self.events.append((args, kwargs))
        def ledger(self, _meta_id): return []

    now = [1_800_000_000]
    core = Core()
    operator = MetaOperator(core, None, None, None, None, alert=lambda *args: alerts.append(args),
        clock=lambda: now[0])
    operator._sleep = lambda _seconds: None
    operator._build_plan = lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("fallo plan"))
    operator._safe_active_review_choices = lambda *_args: {"comparison_version": 2}
    first = operator.review_once(3)
    now[0] += 120
    second = operator.review_once(3)
    assert first["decision"] == second["decision"] == "no_hacer_nada"
    assert "motor v1 falló" in first["reason"]
    assert alerts == [("META_DECISION_ENGINE_FAILURE", 3, {"engine": "v1", "error": "RuntimeError: fallo plan"})]
    assert not any(row[0][2] in {"META_OPERATOR_OPEN", "META_OPERATOR_CLOSE", "META_OPERATOR_RECONFIGURE"}
        for row in core.events)


def test_shadow_exception_is_recorded_without_changing_v1_decision(monkeypatch):
    from grid.engines import get_engine

    class Core:
        def __init__(self): self.events = []
        def get_goal(self, _meta_id): return {"id": 4, "status": "ACTIVE",
            "config_snapshot": {"decision_engine": "v1", "warning_loss_pct": 6}}
        def valuation(self, _meta_id): return {"status": "KNOWN", "result_pct": 0}
        def _ledger(self, *args, **kwargs): self.events.append((args, kwargs))
        def ledger(self, _meta_id): return []

    core = Core()
    operator = MetaOperator(core, None, None, None, None, clock=lambda: 1_800_000_000)
    operator._sleep = lambda _seconds: None
    operator._build_plan = lambda *_args, **_kwargs: {"decision_id": "p", "alternatives": [],
        "candidates": [], "pieces": [], "decision_choices": {}}
    operator._plan_price_moved = lambda *_args: False
    operator._touch_bot_shadow_proposals = lambda *_args: []
    operator._review_allocations = lambda *_args, **_kwargs: {"decision": "no_hacer_nada", "reason": "sin cambio"}
    operator._safe_active_review_choices = lambda *_args: {"comparison_version": 2}
    monkeypatch.setattr(get_engine("v2"), "shadow_review",
        lambda *_args: (_ for _ in ()).throw(ValueError("fallo sombra")))
    result = operator.review_once(4)
    event = next(args[0][3] for args in core.events if args[0][2] == "META_OPERATOR_REVIEW_DECISION")
    assert result == {"decision": "no_hacer_nada", "reason": "sin cambio"}
    assert event["engine_shadow"]["error"] == "ValueError: fallo sombra"
