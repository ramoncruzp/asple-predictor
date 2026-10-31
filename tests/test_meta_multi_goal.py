from datetime import datetime, timezone
import json
import sqlite3
from types import SimpleNamespace

from database.db_manager import DBManager
from data.market_store import CandleStore
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


def test_dos_metas_v1_v2_revisan_con_cache_fresca_y_exchange_falso(tmp_path, monkeypatch):
    import data.market_store

    db = DBManager(f"sqlite:///{tmp_path / 'two-engines.sqlite'}")
    market_path = tmp_path / "two-engines-market.sqlite"
    CandleStore(market_path)
    monkeypatch.setattr(data.market_store, "market_db_path", lambda: market_path)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    settings = SimpleNamespace(meta_mode_enabled=True, meta_max_active=1,
        alerts_enabled=False)
    core = MetaCore(db, FakeExchange(), settings=settings, clock=lambda: now)
    core.set_max_active(2)
    with db.engine.begin() as conn:
        global_row = conn.execute(db.meta_profile_settings.select().where(
            db.meta_profile_settings.c.profile == "GLOBAL")).mappings().one()
        global_settings = json.loads(global_row["settings"])
        global_settings.update({"scenario_cache_enabled": True, "v2_engine_enabled": True})
        conn.execute(db.meta_profile_settings.update().where(
            db.meta_profile_settings.c.profile == "GLOBAL").values(
                settings=json.dumps(global_settings)))
        rapid_row = conn.execute(db.meta_profile_settings.select().where(
            db.meta_profile_settings.c.profile == "RAPIDO")).mappings().one()
        rapid_settings = json.loads(rapid_row["settings"])
        rapid_settings["decision_engine"] = "v2"
        conn.execute(db.meta_profile_settings.update().where(
            db.meta_profile_settings.c.profile == "RAPIDO").values(
                settings=json.dumps(rapid_settings)))
    v2_goal = core.create_goal(100, 10, profile="RAPIDO")
    v1_goal = core.create_goal(100, 10, profile="PRUDENTE")
    core.start_goal(v2_goal["id"])
    core.start_goal(v1_goal["id"])
    for goal in (v2_goal, v1_goal):
        with sqlite3.connect(market_path) as conn:
            run_id = f"fresh-{goal['id']}"
            conn.execute("INSERT INTO scenario_eval_runs(run_id,as_of,goal_id,profile,horizon_days,"
                "n_scenarios,status) VALUES(?,?,?,?,20,1000,'ok')", (run_id,
                    now.replace(tzinfo=timezone.utc).isoformat(), goal["id"], goal["profile"]))
            conn.execute("INSERT INTO scenario_eval_results VALUES(?,?,?,?,?,?,?)", (
                run_id, f"candidate-{goal['id']}", "JUPUSDT", "simple",
                json.dumps({"low": .2, "high": .3, "n": 6, "capital": 50}),
                json.dumps({"n": 1000, "pnl_mean_usdt": 1, "p_target": .3,
                    "p_loss": .02, "p_loss_ci95": [.01, .03],
                    "pnl_mean_ci95": [.8, 1.2], "cut_slippage_mean_usdt": 0,
                    "cut_slippage_p90_usdt": 0, "p_inventory": .1}),
                json.dumps({"factible": True, "motivos": [], "clave_orden": [-.3, 15, -1]})))

    operator = MetaOperator(core, db, None, None, settings,
        clock=lambda: now.replace(tzinfo=timezone.utc).timestamp())
    operator._build_plan = lambda _goal, **_kwargs: {"decision_id": "v1-plan",
        "profile": "PRUDENTE", "alternatives": [], "pieces": [], "candidates": [],
        "decision_choices": {"comparison_version": 2}}
    operator._touch_bot_shadow_proposals = lambda *_args: []
    operator._plan_price_moved = lambda *_args: False
    operator._safe_active_review_choices = lambda *_args: {"comparison_version": 2}
    operator._review_allocations = lambda *_args, **_kwargs: {
        "decision": "no_hacer_nada", "reason": "fixture multi-meta"}
    operator._run_engine_shadow = lambda *_args: {"name": "sombra", "decision": "mantener"}

    result = operator.review_active()

    assert [item["meta_id"] for item in result["reviewed"]] == [v2_goal["id"], v1_goal["id"]]
    with db.engine.connect() as conn:
        rows = conn.execute(db.meta_ledger.select().where(
            db.meta_ledger.c.event_type == "META_OPERATOR_REVIEW_DECISION").order_by(
                db.meta_ledger.c.meta_id)).mappings().all()
        shadow_rows = conn.execute(db.engine_shadow_decisions.select().order_by(
            db.engine_shadow_decisions.c.goal_id)).mappings().all()
    details = {int(row["meta_id"]): json.loads(row["details"]) for row in rows}
    assert details[v2_goal["id"]]["engine"]["name"] == "v2"
    assert details[v1_goal["id"]]["engine"]["name"] == "v1"
    assert details[v1_goal["id"]]["decision"]["decision"] == "no_hacer_nada"
    assert shadow_rows[0]["active_engine"] == "v2"
    assert shadow_rows[0]["cache_run_id"] == f"fresh-{v2_goal['id']}"


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
