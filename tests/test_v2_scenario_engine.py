import json
import sqlite3
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
                "p_loss": .01, "p_loss_ci95": [0, .02], "days_to_target_p90": 18}),
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
