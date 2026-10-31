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
                "days_to_target_p50": 15, "days_to_target_p90": 18}),
            json.dumps({"factible": True, "motivos": [], "clave_orden": [-.35, 15, -1.4]})))


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
    assert plan["pieces"][0]["capital_usdt"] == 70


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
