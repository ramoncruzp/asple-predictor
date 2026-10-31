from datetime import datetime, timezone
from types import SimpleNamespace

from database.db_manager import DBManager
from grid.meta import MetaCore
from grid.meta_operator import MetaOperator


class FakeExchange:
    testnet = True

    def get_balance(self, _asset=None):
        return {"USDT": {"free": 1000, "locked": 0}}


def _active_operator(tmp_path, count=2):
    db = DBManager(f"sqlite:///{tmp_path / 'multi-goal.sqlite'}")
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    settings = SimpleNamespace(meta_mode_enabled=True, meta_max_active=1,
        alerts_enabled=False)
    core = MetaCore(db, FakeExchange(), settings=settings, clock=lambda: now)
    core.set_max_active(count)
    goals = []
    for _ in range(count):
        goal = core.create_goal(100, 10)
        core.start_goal(goal["id"])
        goals.append(goal["id"])
    operator = MetaOperator(core, db, None, None, settings, clock=lambda: now.timestamp())
    return db, core, operator, goals


def test_review_active_revisa_todas_y_un_error_no_detiene_la_siguiente(tmp_path):
    _db, core, operator, goals = _active_operator(tmp_path)
    seen = []

    def review(meta_id):
        seen.append(meta_id)
        if meta_id == goals[0]:
            raise RuntimeError("error sintético de revisión")
        return {"decision": "no_hacer_nada", "meta_id": meta_id}

    operator.review_once = review
    result = operator.review_active()

    assert seen == goals
    assert [row["meta_id"] for row in result["reviewed"]] == [goals[1]]
    assert result["errors"] == [{"meta_id": goals[0],
        "error": "RuntimeError: error sintético de revisión"}]
    assert core.ledger(goals[0])[-1]["event_type"] == "META_REVIEW_FAILED"


def test_review_active_preserva_resultado_legacy_con_una_meta(tmp_path):
    _db, _core, operator, goals = _active_operator(tmp_path, count=1)
    expected = {"decision": "no_hacer_nada", "reason": "sin cambios"}
    operator.review_once = lambda _meta_id: expected

    assert operator.review_active() is expected
    assert goals == [operator.core.list_goals()[0]["id"]]


def test_review_active_reanuda_primero_la_meta_omitida_por_presupuesto(tmp_path):
    _db, _core, operator, goals = _active_operator(tmp_path, count=3)
    operator.review_cycle_budget_s = 0
    first_seen = []
    operator.review_once = lambda meta_id: first_seen.append(meta_id) or {
        "decision": "no_hacer_nada"}

    first = operator.review_active()
    assert first["reviewed"][0]["meta_id"] == goals[0]
    assert first["skipped_budget"] == goals[1:]

    second_seen = []
    operator.review_once = lambda meta_id: second_seen.append(meta_id) or {
        "decision": "no_hacer_nada"}
    operator.review_active()
    assert second_seen[0] in goals[1:]
