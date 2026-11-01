import json
import sqlite3
import threading
import threading
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from data.market_store import CandleStore
from database.db_manager import DBManager
from grid.engines.base import EngineUnavailable
from grid.engines.v2_escenarios import V2ScenarioEngine
from grid.engines.v1_replay import V1ReplayEngine
from grid.meta_operator import MetaOperator


@pytest.fixture
def engine_databases(tmp_path, monkeypatch):
    app_path = tmp_path / "app.sqlite"
    market_path = tmp_path / "market.sqlite"
    db = DBManager(f"sqlite:///{app_path.as_posix()}")
    CandleStore(market_path)
    import data.market_store
    monkeypatch.setattr(data.market_store, "market_db_path", lambda: market_path)
    with db.engine.begin() as conn:
        raw = conn.execute(select(db.meta_profile_settings.c.settings).where(
            db.meta_profile_settings.c.profile == "GLOBAL")).scalar_one()
        settings = json.loads(raw)
        settings.update({"scenario_cache_enabled": True, "v2_engine_enabled": True})
        conn.execute(db.meta_profile_settings.update().where(
            db.meta_profile_settings.c.profile == "GLOBAL").values(
                settings=json.dumps(settings)))
    yield db, market_path
    db.engine.dispose()


def _cache_row(path, *, as_of=None, status="ok"):
    stamp = as_of or datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(path) as conn:
        conn.execute("INSERT INTO scenario_eval_runs(run_id,as_of,goal_id,profile,horizon_days,"
            "n_scenarios,status) VALUES('cache-1',?,9,'RAPIDO',20,1000,?)", (stamp, status))
        conn.execute("INSERT INTO scenario_eval_results VALUES(?,?,?,?,?,?,?)", (
            "cache-1", "piece-1", "JUPUSDT", "simple",
            json.dumps({"low": .20, "high": .30, "n": 6, "capital": 70}),
            json.dumps({"n": 1000, "pnl_mean_usdt": 1.4, "p_target": .35,
                "p_loss": .01, "p_loss_ci95": [0, .02], "pnl_mean_ci95": [1.2, 1.6],
                "cut_slippage_mean_usdt": 0, "cut_slippage_p90_usdt": 0,
                "p_inventory": .27,
                "days_to_target_p50": 15, "days_to_target_p90": 18}),
            json.dumps({"factible": True, "motivos": [], "clave_orden": [-.35, 15, -1.4]})))


def _meta_three_live_piece_fixture(db):
    from datetime import datetime, timezone
    from types import SimpleNamespace

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with db.engine.begin() as conn:
        conn.execute(db.meta_goals.insert().values(id=9, capital_initial=100,
            target_usdt=3, target_input_type="USDT", target_input_value=3,
            profile="RAPIDO", status="ACTIVE", environment="testnet",
            created_at=now, config_snapshot=json.dumps({"decision_engine": "v1"})))
        for grid_id, status, level_state, held_qty in (
                (12, "CLOSED", "DONE", 0), (13, "PAUSED", "SELL_OPEN", 5)):
            conn.execute(db.grids.insert().values(id=grid_id, symbol="JUPUSDT",
                range_low=.20, range_high=.30, n_levels=6, capital_total=50,
                reserve=0, status=status, created_at=now, environment="testnet",
                strategy="smart", params="{}", dust_qty="0"))
            conn.execute(db.grid_levels.insert().values(grid_id=grid_id, level_idx=0,
                price=.21, sell_price=.22, capital=50, state=level_state,
                held_qty=held_qty, cycles_completed=0, pnl=0, fee_paid=0,
                updated_at=now))
            run_id = conn.execute(db.meta_runs.insert().values(meta_id=9,
                status="ACTIVE", started_at=now, realized_extra_usdt=0)).inserted_primary_key[0]
            conn.execute(db.meta_piece_links.insert().values(meta_id=9, run_id=run_id,
                grid_id=grid_id, piece_type="grid", origin_grid_id=grid_id,
                created_at=now))

    def owned(_goal_id):
        with db.engine.connect() as conn:
            return [dict(row) for row in conn.execute(select(db.meta_piece_links).where(
                db.meta_piece_links.c.meta_id == 9,
                db.meta_piece_links.c.released_at.is_(None))).mappings().all()]

    return SimpleNamespace(db=db, _owned_grids=owned)


def _meta_three_current_cache(path, *, as_of=None):
    stamp = as_of or datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(path) as conn:
        conn.execute("INSERT INTO scenario_eval_runs(run_id,as_of,goal_id,profile,horizon_days,"
            "n_scenarios,status) VALUES('meta-three-current',?,9,'RAPIDO',20,1000,'ok')", (stamp,))
        for grid_id, pnl in ((12, 2.0), (13, .5)):
            conn.execute("INSERT INTO scenario_eval_results VALUES(?,?,?,?,?,?,?)", (
                "meta-three-current", f"mantener-{grid_id}", "JUPUSDT", "smart",
                json.dumps({"low": .20, "high": .30, "n": 6, "capital": 50,
                    "grid_id": grid_id, "is_current": True}),
                json.dumps({"n": 1000, "pnl_mean_usdt": pnl, "p_target": .12,
                    "p_loss": .03, "p_loss_ci95": [.02, .04],
                    "pnl_mean_ci95": [pnl - .1, pnl + .1],
                    "cut_slippage_mean_usdt": .04, "cut_slippage_p90_usdt": .1,
                    "p_inventory": .2, "days_to_target_p50": 12,
                    "days_to_target_p90": 20}),
                json.dumps({"factible": True, "motivos": [],
                    "clave_orden": [-.12, 12, -pnl]})))


def test_cache_y_v2_comparten_piezas_vivas_de_meta_tres(engine_databases):
    from scripts.escenarios_cache import _active_grid_pieces

    db, market_path = engine_databases
    core = _meta_three_live_piece_fixture(db)
    assert [int(link["grid_id"]) for link in
        __import__("grid.meta_live_pieces", fromlist=["live_meta_grid_links"])
        .live_meta_grid_links(core, 9)] == [13]
    _meta_three_current_cache(market_path)
    pieces = _active_grid_pieces(db.engine.url.database, 9,
        prices={"JUPUSDT": .25}, filters={})
    assert [piece["grid_id"] for piece in pieces] == [13]

    operator = SimpleNamespace(db=db, core=core)
    goal = {"id": 9, "profile": "RAPIDO", "capital_initial": 100,
        "target_usdt": 3}
    plan = V2ScenarioEngine().plan(operator, goal, plan_kind="revision")
    assert list(plan["current_pieces"]) == ["13"]
    result = V2ScenarioEngine().review(operator, goal,
        {"pieces": [{"grid_id": 13, "symbol": "JUPUSDT", "levels": []}]}, plan)
    assert result["decision"] == "mantener"
    assert result.get("grid_id") is None


def test_sombra_v2_usa_solo_cache_y_expone_metricas_de_mantener(engine_databases):
    db, market_path = engine_databases
    core = _meta_three_live_piece_fixture(db)
    _meta_three_current_cache(market_path)
    with db.engine.begin() as conn:
        raw = conn.execute(select(db.meta_profile_settings.c.settings).where(
            db.meta_profile_settings.c.profile == "GLOBAL")).scalar_one()
        settings = json.loads(raw)
        settings.update({"scenario_cache_enabled": True, "v2_engine_enabled": False})
        conn.execute(db.meta_profile_settings.update().where(
            db.meta_profile_settings.c.profile == "GLOBAL").values(
                settings=json.dumps(settings)))
    operator = SimpleNamespace(db=db, core=core)
    shadow = V2ScenarioEngine().shadow_review(operator,
        {"id": 9, "profile": "RAPIDO", "capital_initial": 100, "target_usdt": 3},
        {"pieces": [{"grid_id": 13, "symbol": "JUPUSDT", "levels": []}]}, None)
    assert shadow["decision"] == "mantener"
    assert shadow["v2_metrics"]["p_loss"] == .03
    assert shadow["v2_metrics"]["cut_slippage_mean_usdt"] == .04
    assert shadow["v2_metrics"]["p_inventory"] == .2

    with db.engine.begin() as conn:
        settings["scenario_cache_enabled"] = False
        conn.execute(db.meta_profile_settings.update().where(
            db.meta_profile_settings.c.profile == "GLOBAL").values(
                settings=json.dumps(settings)))
    unavailable = V2ScenarioEngine().shadow_review(operator,
        {"id": 9, "profile": "RAPIDO", "capital_initial": 100, "target_usdt": 3},
        {}, None)
    assert unavailable["decision"] == "no_disponible"


def test_plan_usa_corrida_vigente_sin_recalcular(engine_databases):
    db, market_path = engine_databases
    _cache_row(market_path)
    engine = V2ScenarioEngine()
    goal = {"id": 9, "profile": "RAPIDO", "capital_initial": 100, "target_usdt": 3}

    plan = engine.plan(SimpleNamespace(db=db), goal, plan_kind="analisis")

    assert plan["engine"] == {"name": "v2", "version": engine.version}
    assert plan["cache"]["run_id"] == "cache-1"
    assert plan["pieces"][0]["symbol"] == "JUPUSDT"
    assert plan["pieces"][0]["floor"] == .20
    assert plan["v2"]["p_target"] == .35
    assert plan["v2"]["p_loss"] == .01
    assert plan["v2"]["p_inventory"] == .27
    assert plan["estimated_time"]["p_inventory"] == .27
    assert plan["pieces"][0]["capital_usdt"] == 70


def test_plan_v2_con_dias_ausentes_es_json_estricto_y_respeta_ranking(
        engine_databases):
    from research.scenarios.objective import rank_alternatives_v2

    ordered = rank_alternatives_v2([
        {"candidate_key": "sin_dias", "piece": {"strategy": "simple"},
            "metrics": {"pnl_mean_usdt": 1, "p_target": .35, "p_loss_ci95": [.2, .4],
                "cut_slippage_mean_usdt": 0, "days_to_target_p50": None}},
        {"candidate_key": "con_dias", "piece": {"strategy": "simple"},
            "metrics": {"pnl_mean_usdt": 1, "p_target": .20, "p_loss_ci95": [.2, .4],
                "cut_slippage_mean_usdt": 0, "days_to_target_p50": 1}},
    ], perfil="RAPIDO", capital_meta=100)
    assert [row.get("candidate_key") or row["piece"]["strategy"] for row in ordered] == [
        "usdt_quieto", "sin_dias", "con_dias"]

    db, market_path = engine_databases
    _cache_row(market_path)
    with sqlite3.connect(market_path) as conn:
        conn.execute("UPDATE scenario_eval_results SET metrics_json=?",
            (json.dumps({"n": 1000, "pnl_mean_usdt": 1.4, "p_target": .35,
                "p_loss": .3, "p_loss_ci95": [.2, .4],
                "cut_slippage_mean_usdt": 0, "days_to_target_p50": None}),))
    goal = {"id": 9, "profile": "RAPIDO", "capital_initial": 100, "target_usdt": 3}
    engine = V2ScenarioEngine()
    plan = engine.plan(SimpleNamespace(db=db), goal, plan_kind="analisis")

    encoded = json.dumps(plan, allow_nan=False)
    assert plan["v2"]["decision"] == "mantener_usdt"
    assert plan["alternatives"][0]["objective"]["clave_orden"][1] is None
    assert json.loads(encoded)["alternatives"][0]["objective"]["clave_orden"][1] is None


def test_endpoint_analisis_v2_responde_200_con_plan_sin_inf(engine_databases):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from api.routes import metas

    db, market_path = engine_databases
    _cache_row(market_path)
    with sqlite3.connect(market_path) as conn:
        conn.execute("UPDATE scenario_eval_results SET metrics_json=?",
            (json.dumps({"n": 1000, "pnl_mean_usdt": 1.4, "p_target": .35,
                "p_loss": .3, "p_loss_ci95": [.2, .4],
                "cut_slippage_mean_usdt": 0, "days_to_target_p50": None}),))
    goal = {"id": 9, "profile": "RAPIDO", "capital_initial": 100, "target_usdt": 3}
    plan = V2ScenarioEngine().plan(SimpleNamespace(db=db), goal, plan_kind="analisis")
    app = FastAPI()
    app.include_router(metas.router, prefix="/api/metas")
    app.state.settings = SimpleNamespace(grid_api_token="local-v2-test")
    app.state.meta_operator = SimpleNamespace(analyze=lambda _meta_id: plan)

    response = TestClient(app, headers={"X-API-Token": "local-v2-test"}).post(
        "/api/metas/9/analysis")

    assert response.status_code == 200, response.text
    assert response.json()["alternatives"][0]["objective"]["clave_orden"][1] is None


def test_plan_usdt_quieto_no_abre_piezas_y_start_plan_lo_acepta(engine_databases):
    db, market_path = engine_databases
    _cache_row(market_path)
    with sqlite3.connect(market_path) as conn:
        conn.execute("UPDATE scenario_eval_results SET metrics_json=?,objective_json=?",
            (json.dumps({"n": 1000, "pnl_mean_usdt": -5, "p_target": .1,
                "p_loss": .5, "p_loss_ci95": [.4, .6], "cut_slippage_mean_usdt": 0}),
             json.dumps({"factible": False, "motivos": ["prueba"],
                "clave_orden": [0, 0, 0]})))
    engine = V2ScenarioEngine()
    goal = {"id": 9, "profile": "RAPIDO", "capital_initial": 100, "target_usdt": 3,
        "config_snapshot": {"decision_engine": "v2"}}
    plan = engine.plan(SimpleNamespace(db=db), goal, plan_kind="analisis")
    assert plan["pieces"] == []
    assert plan["v2"]["decision"] == "mantener_usdt"

    operator = object.__new__(MetaOperator)
    operator._lock = threading.RLock()
    operator.clock = lambda: 100.0
    operator.core = SimpleNamespace(get_goal=lambda _goal_id: goal)
    operator._plans = {"quiet-plan": {**plan, "plan_id": "quiet-plan", "meta_id": 9,
        "expires_at": 200, "engine": {"name": "v2"}}}
    result = operator._start_plan_locked(9, "quiet-plan", None)
    assert result["status"] == "USDT_QUIETO"
    assert result["opened"] == []


def _review_fixture(*, alt_probability=.5, alt_pnl=.8, current_probability=.4,
        current_pnl=.5, alt_result=None):
    current_piece = {"grid_id": 1, "symbol": "JUPUSDT", "strategy": "simple",
        "floor": .20, "ceiling": .30, "capital_usdt": 70, "n_levels": 6,
        "piece_type": "grid"}
    best_piece = {"symbol": "XRPUSDT", "strategy": "simple", "floor": .5,
        "ceiling": .7, "capital_usdt": 70, "n_levels": 6, "piece_type": "grid"}
    current_est = {"probability": current_probability, "pnl_mean_usdt": current_pnl,
        "pnl_mean_ci95": [current_pnl - .2, current_pnl + .2]}
    current = {"alternative_id": "keep", "symbol": "JUPUSDT", "strategy": "simple",
        "pieces": [current_piece], "estimate": current_est,
        "cache_candidate_key": "mantener:1", "is_current": True,
        "objective": {"factible": True}}
    best = {"alternative_id": "change", "symbol": "XRPUSDT", "strategy": "simple",
        "pieces": [best_piece], "estimate": {"probability": alt_probability,
            "pnl_mean_usdt": alt_pnl, "evidence_sufficient": True},
        "cache_candidate_key": "candidate", "is_current": False,
        "objective": {"factible": True}}
    plan = {"alternatives": [best, current], "current_pieces": {"1": {
        "piece": current_piece, "estimate": current_est}}, "cache": {"run_id": "run-1"}}
    operator = SimpleNamespace(core=SimpleNamespace(_owned_grids=lambda _goal_id: [
        {"grid_id": 1}]), _review_allocations=lambda _goal, _value, plan=None:
        alt_result or {"decision": "cerrar_y_reasignar"})
    return operator, {"id": 2, "profile": "RAPIDO", "target_usdt": 3}, {
        "pieces": [{"grid_id": 1, "symbol": "JUPUSDT", "levels": []}]}, plan


def test_revision_v2_mantiene_si_no_supera_umbral():
    operator, goal, valuation, plan = _review_fixture(alt_probability=.43)

    result = V2ScenarioEngine().review(operator, goal, valuation, plan)

    assert result["decision"] == "mantener"
    assert "alternativa" in result["reason"]


def test_revision_v2_cambia_solo_tras_comparacion_neta_y_delega_frenos():
    calls = []
    operator, goal, valuation, plan = _review_fixture(
        alt_result={"decision": "no_hacer_nada", "reason": "cooldown vigente"})
    operator._review_allocations = lambda _goal, _value, plan=None: calls.append(plan) or {
        "decision": "no_hacer_nada", "reason": "cooldown vigente"}

    result = V2ScenarioEngine().review(operator, goal, valuation, plan)

    assert len(calls) == 1
    assert calls[0]["alternatives"][0]["alternative_id"] == "change"
    assert result["decision"] == "no_hacer_nada"
    assert result["reason"] == "cooldown vigente"
    assert result["comparison_v2"]["probability_improvement_pp"] == pytest.approx(10)


def test_revision_v2_no_cambia_si_perdida_y_comisiones_superan_beneficio():
    operator, goal, valuation, plan = _review_fixture()
    valuation["pieces"][0]["levels"] = [{"held_qty": 100, "entry_price": 1.0}]
    valuation["symbol_valuations"] = {"JUPUSDT": {"best_bid": .8}}
    calls = []
    operator._review_allocations = lambda *_args, **_kwargs: calls.append(True)

    result = V2ScenarioEngine().review(operator, goal, valuation, plan)

    assert result["decision"] == "mantener"
    assert calls == []


def test_motor_no_usa_cache_vencida_ni_fallback(engine_databases):
    db, market_path = engine_databases
    stale = datetime(2020, 1, 1, tzinfo=timezone.utc).isoformat()
    _cache_row(market_path, as_of=stale)
    engine = V2ScenarioEngine()

    with pytest.raises(EngineUnavailable, match="vencida"):
        engine.plan(SimpleNamespace(db=db), {"id": 9, "profile": "RAPIDO", "capital_initial": 100,
            "target_usdt": 3}, plan_kind="analisis")


def test_v2_acepta_cache_parcial_reciente(engine_databases):
    db, market_path = engine_databases
    _cache_row(market_path, status="parcial")

    cache = V2ScenarioEngine._load_cache(SimpleNamespace(db=db),
        {"id": 9, "profile": "RAPIDO"})

    assert cache["status"] == "parcial"
    assert cache["candidates"]


def test_motor_apagado_falla_explicito(engine_databases):
    db, market_path = engine_databases
    with db.engine.begin() as conn:
        raw = conn.execute(select(db.meta_profile_settings.c.settings).where(
            db.meta_profile_settings.c.profile == "GLOBAL")).scalar_one()
        settings = json.loads(raw)
        settings["v2_engine_enabled"] = False
        conn.execute(db.meta_profile_settings.update().where(
            db.meta_profile_settings.c.profile == "GLOBAL").values(
                settings=json.dumps(settings)))
    engine = V2ScenarioEngine()

    with pytest.raises(EngineUnavailable, match="apagado"):
        engine.plan(SimpleNamespace(db=db), {"id": 9, "profile": "RAPIDO", "capital_initial": 100,
            "target_usdt": 3}, plan_kind="analisis")


def test_sombra_v1_solicita_plan_sin_persistir():
    calls = []
    operator = SimpleNamespace(_build_plan=lambda goal, **kwargs: calls.append(kwargs) or {
        "decision_choices": {"real": {"decision": "seleccionar_alternativa",
            "alternative_id": "v1:0"}}})

    result = V1ReplayEngine().shadow_review(operator, {"id": 9}, {}, {})

    assert calls == [{"record": False}]
    assert result["source"] == "v1_plan_read_only"
    assert result["alternative_id"] == "v1:0"


def test_sombra_v1_respeta_presupuesto_de_30_segundos(monkeypatch):
    monkeypatch.setattr("grid.engines.v1_replay.V1_SHADOW_BUDGET_SECONDS", 0)
    release = threading.Event()
    operator = SimpleNamespace(_build_plan=lambda *_args, **_kwargs: release.wait(1))

    result = V1ReplayEngine().shadow_review(operator, {"id": 9}, {}, {})
    release.set()

    assert result["decision"] == "sin_tiempo"


def test_disponibilidad_publicada_depende_de_interruptores_y_cache(engine_databases):
    db, market_path = engine_databases
    operator = object.__new__(MetaOperator)
    operator.db = db
    with db.engine.begin() as conn:
        raw = conn.execute(select(db.meta_profile_settings.c.settings).where(
            db.meta_profile_settings.c.profile == "GLOBAL")).scalar_one()
        settings = json.loads(raw)
        settings["v2_engine_enabled"] = False
        conn.execute(db.meta_profile_settings.update().where(
            db.meta_profile_settings.c.profile == "GLOBAL").values(
                settings=json.dumps(settings)))
    assert operator.v2_engine_status()["available"] is False

    with db.engine.begin() as conn:
        raw = conn.execute(select(db.meta_profile_settings.c.settings).where(
            db.meta_profile_settings.c.profile == "GLOBAL")).scalar_one()
        settings = json.loads(raw)
        settings.update({"v2_engine_enabled": True, "scenario_cache_enabled": True})
        conn.execute(db.meta_profile_settings.update().where(
            db.meta_profile_settings.c.profile == "GLOBAL").values(
                settings=json.dumps(settings)))
    with sqlite3.connect(market_path) as conn:
        conn.execute("INSERT INTO scenario_eval_runs(run_id,as_of,profile,horizon_days,"
            "n_scenarios,status) VALUES('fresh',?,'RAPIDO',20,1000,'ok')",
            (datetime.now(timezone.utc).isoformat(),))
    assert operator.v2_engine_status()["available"] is True


def test_engine_shadow_decision_se_persiste_en_tabla_temporal(engine_databases):
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    db, _market_path = engine_databases
    with db.engine.begin() as conn:
        goal_id = conn.execute(db.meta_goals.insert().values(
            capital_initial=100, target_usdt=3, target_input_type="USDT",
            target_input_value=3, profile="RAPIDO", status="ACTIVE",
            environment="testnet", created_at=now, config_snapshot="{}")).inserted_primary_key[0]
    operator = MetaOperator(SimpleNamespace(db=db), db, None, None, None)

    assert operator._persist_engine_shadow_decision({"id": goal_id},
        SimpleNamespace(name="v2"), {"decision": "mantener"},
        {"name": "v1", "decision": "agregar", "result": {
            "alternative_id": "alt-1"}}, {"cache": {"run_id": "cache-1"}})

    with db.engine.connect() as conn:
        row = conn.execute(select(db.engine_shadow_decisions)).mappings().one()
    assert row["active_engine"] == "v2"
    assert row["shadow_engine"] == "v1"
    assert row["cache_run_id"] == "cache-1"
    assert json.loads(row["active_decision_json"])["decision"] == "mantener"
    assert json.loads(row["shadow_decision_json"])["result"]["alternative_id"] == "alt-1"
    assert row["shadow_outcome_json"] is None
    history = operator.engine_shadow_history(limit=10)
    assert history["events"][0]["active_decision"]["decision"] == "mantener"
    assert history["events"][0]["shadow_outcome"] is None
