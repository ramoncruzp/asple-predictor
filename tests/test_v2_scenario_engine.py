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
from grid.engines.shadow_guard import ShadowSideEffectError, shadow_execution_context
from grid.engine import GridEngine
from grid.meta import MetaCore
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


def _cache_row(path, *, as_of=None, status="ok", error=None):
    stamp = as_of or datetime.now(timezone.utc).isoformat()
    run_details = dict(error or {})
    run_details.setdefault("symbol_stats", {"JUPUSDT": {"price": .25,
        "sigma_24h": .03}})
    with sqlite3.connect(path) as conn:
        conn.execute("INSERT INTO scenario_eval_runs(run_id,as_of,goal_id,profile,horizon_days,"
            "n_scenarios,status,error) VALUES('cache-1',?,9,'RAPIDO',20,1000,?,?)",
            (stamp, status, json.dumps(run_details)))
        conn.execute("INSERT INTO scenario_eval_results VALUES(?,?,?,?,?,?,?)", (
            "cache-1", "piece-1", "JUPUSDT", "simple",
            json.dumps({"low": .20, "high": .30, "n": 6, "capital": 70}),
            json.dumps({"n": 1000, "pnl_mean_usdt": 1.4, "p_target": .35,
                "p_loss": .01, "p_loss_ci95": [0, .02], "pnl_mean_ci95": [1.2, 1.6],
                "cut_slippage_mean_usdt": 0, "cut_slippage_p90_usdt": 0,
                "p_inventory": .27,
                "days_to_target_p50": 15, "days_to_target_p90": 18}),
            json.dumps({"factible": True, "motivos": [], "clave_orden": [-.35, 15, -1.4]})))


def _plan_operator(db, prices):
    return SimpleNamespace(db=db, engine=None,
        _book_ticker=lambda symbol: {"bid_price": prices[symbol],
            "ask_price": prices[symbol]},
        _validate_v2_plan_limits=lambda *_args, **_kwargs: None)


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

    plan = engine.plan(_plan_operator(db, {"JUPUSDT": .25}), goal, plan_kind="analisis")

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
    starts = []
    operator.core = SimpleNamespace(get_goal=lambda _goal_id: goal,
        start_goal=lambda _goal_id: starts.append(True))
    operator._plans = {"quiet-plan": {**plan, "plan_id": "quiet-plan", "meta_id": 9,
        "expires_at": 200, "engine": {"name": "v2"}}}
    result = operator._start_plan_locked(9, "quiet-plan", None)
    assert result["status"] == "ACTIVE"
    assert result["opened"] == []
    assert starts == [True]


def test_plan_v2_respeta_tope_por_moneda_y_agrupa_tabla(engine_databases, monkeypatch):
    db, _market_path = engine_databases
    cached = {"run_id": "r", "as_of": datetime.now(timezone.utc).isoformat(),
        "age_seconds": 0, "status": "ok", "horizon_days": 20,
        "discarded": {}, "skipped_symbols": {}, "trajectory_file": "synthetic.npz",
        "symbol_stats": {"ZROUSDT": {"price": 2.1, "sigma_24h": .04,
            "generated_count": 4, "evaluated_count": 3,
            "discarded": {"filtro": 1}}},
        "candidates": [
            {"candidate_key": "zro100", "symbol": "ZROUSDT", "strategy": "simple",
                "params": {"low": 1.7, "high": 2.3, "n": 6, "capital": 100},
                "metrics": {"n": 10, "p_target": .5, "pnl_mean_usdt": 2,
                    "p_loss": 0, "p_loss_ci95": [0, .01], "pnl_mean_ci95": [1, 3],
                    "cut_slippage_mean_usdt": 0, "days_to_target_p50": 7},
                "objective": {"factible": True, "motivos": [],
                    "clave_orden": [-.5, 7, -2]}},
            {"candidate_key": "zro70", "symbol": "ZROUSDT", "strategy": "simple",
                "params": {"low": 1.7, "high": 2.3, "n": 6, "capital": 70},
                "metrics": {"n": 10, "p_target": .4, "pnl_mean_usdt": 1,
                    "p_loss": 0, "p_loss_ci95": [0, .01], "pnl_mean_ci95": [.5, 1.5],
                    "cut_slippage_mean_usdt": 0, "days_to_target_p50": 8},
                "objective": {"factible": True, "motivos": [],
                    "clave_orden": [-.4, 8, -1]}}]}
    monkeypatch.setattr(V2ScenarioEngine, "_load_cache", lambda *_args, **_kwargs: cached)
    monkeypatch.setattr("grid.engines.v2_escenarios.greedy_portfolio", lambda *_args, **_kwargs: {
        "added": [{"candidate_key": "zro70"}], "steps": [],
        "metrics": {"p_target": .4}, "not_added_reasons": [
            {"symbol": "PEPEUSDT", "strategy": "simple", "reason": "no sube P(meta)"}]})
    plan = V2ScenarioEngine().plan(_plan_operator(db, {"ZROUSDT": 2.1}),
        {"id": 9, "status": "DRAFT", "profile": "RAPIDO", "capital_initial": 100,
            "target_usdt": 3}, plan_kind="analisis")

    assert [piece["capital_usdt"] for piece in plan["pieces"]] == [70]
    assert len(plan["candidates"]) == 1
    assert plan["candidates"][0]["generated_count"] == 4
    assert plan["candidates"][0]["evaluated_count"] == 3
    assert plan["candidates"][0]["discarded_count"] == 1
    assert plan["pieces"][0]["mid_at_plan"] == 2.1
    assert plan["pieces"][0]["price_source"] == "book"
    assert plan["pieces"][0]["price_at"]
    assert plan["alternatives"][0]["price_source"] == "book"
    assert plan["loss_max_usdt"] == 18
    assert plan["v2"]["cartera_no_agregadas"][0]["symbol"] == "PEPEUSDT"


def _start_plan_fixture(engine_databases, *, engine_name, prices, alternative=True):
    db, _market_path = engine_databases
    calls = []
    goal = {"id": 9, "profile": "RAPIDO", "capital_initial": 100,
        "status": "DRAFT", "config_snapshot": {"decision_engine": engine_name,
            "plan_price_drift_pct": 1}}
    piece = {"symbol": "ZROUSDT", "strategy": "simple", "piece_type": "grid",
        "capital_usdt": 70, "floor": 1.7, "ceiling": 2.3, "n_levels": 6,
        "mid_at_plan": 2.1, "price_source": "scenario_cache",
        "price_at": "2026-11-01T00:00:00+00:00"}
    plan = {"plan_id": "plan", "meta_id": 9, "expires_at": 200,
        "engine": {"name": engine_name}, "pieces": [piece], "control_plan": {},
        "alternatives": ([{"symbol": "PEPEUSDT", "pieces": [{"symbol": "PEPEUSDT",
            "floor": .0009, "ceiling": .0011, "mid_at_plan": .001,
            "capital_usdt": 30}]}] if alternative else [])}
    operator = object.__new__(MetaOperator)
    operator._lock = threading.RLock()
    operator._plans = {"plan": plan}
    operator.clock = lambda: 100
    operator.price_drift_pct = 1
    operator.core = SimpleNamespace(get_goal=lambda _id: goal,
        _owned_grids=lambda _id: [], _available_usdt=lambda _id: (100, 0),
        start_goal=lambda _id: calls.append("start"),
        link_grid=lambda _id, grid_id, _kind: calls.append(("link", grid_id)),
        _now=lambda: datetime.now(timezone.utc).replace(tzinfo=None))
    operator.db = db
    operator.engine = None
    operator.scanner = SimpleNamespace(client=SimpleNamespace(get_book_ticker=lambda symbol: {
        "bid_price": prices[symbol], "ask_price": prices[symbol]}))
    operator._validate_v2_plan_limits = lambda *_args, **_kwargs: None
    operator._active_run_id = lambda _id: None
    operator._ledger = lambda *_args, **_kwargs: None
    operator.alert = lambda *_args, **_kwargs: None
    result = operator._start_plan_locked(9, "plan", lambda _piece: {"grid_id": 77})
    return result, calls


def test_start_plan_v2_ignora_deriva_de_alternativas_y_abre_solo_pieza_del_plan(
        engine_databases):
    result, calls = _start_plan_fixture(engine_databases, engine_name="v2",
        prices={"ZROUSDT": 2.1, "PEPEUSDT": .00105})
    assert [row["symbol"] for row in result["opened"]] == ["ZROUSDT"]
    assert ("link", 77) in calls
    assert "start" in calls


def test_start_plan_v2_rechaza_deriva_de_su_pieza(engine_databases):
    db, _market_path = engine_databases
    goal = {"id": 9, "profile": "RAPIDO", "capital_initial": 100,
        "status": "DRAFT", "config_snapshot": {"decision_engine": "v2",
            "plan_price_drift_pct": 1}}
    operator = object.__new__(MetaOperator)
    operator._lock = threading.RLock()
    operator._plans = {"plan": {"plan_id": "plan", "meta_id": 9,
        "expires_at": 200, "engine": {"name": "v2"}, "pieces": [{
            "symbol": "ZROUSDT", "strategy": "simple", "piece_type": "grid",
            "capital_usdt": 70, "floor": 1.7, "ceiling": 2.3, "n_levels": 6,
            "mid_at_plan": 2.1}]}}
    operator.clock = lambda: 100
    operator.price_drift_pct = 1
    operator.core = SimpleNamespace(get_goal=lambda _id: goal,
        _available_usdt=lambda _id: (100, 0), start_goal=lambda _id: pytest.fail("no debe iniciar"))
    operator.db = db
    operator.engine = None
    operator.scanner = SimpleNamespace(client=SimpleNamespace(get_book_ticker=lambda _symbol: {
        "bid_price": 2.13, "ask_price": 2.13}))
    operator._validate_v2_plan_limits = lambda *_args, **_kwargs: None
    with pytest.raises(ValueError, match="ZROUSDT se movi\u00f3 m\u00e1s de 1%"):
        operator._start_plan_locked(9, "plan", lambda _piece: pytest.fail("no debe abrir"))


def test_start_plan_v1_sigue_validando_alternativas_y_su_deriva(engine_databases):
    db, _market_path = engine_databases
    goal = {"id": 9, "profile": "RAPIDO", "capital_initial": 100,
        "status": "DRAFT", "config_snapshot": {"decision_engine": "v1",
            "plan_price_drift_pct": 1}}
    operator = object.__new__(MetaOperator)
    operator._lock = threading.RLock()
    operator._plans = {"plan": {"plan_id": "plan", "meta_id": 9,
        "expires_at": 200, "engine": {"name": "v1"}, "pieces": [{
            "symbol": "ZROUSDT", "strategy": "simple", "piece_type": "grid",
            "capital_usdt": 70, "floor": 1.7, "ceiling": 2.3, "n_levels": 6,
            "mid_at_plan": 2.1}], "alternatives": [{"pieces": [{
                "symbol": "PEPEUSDT", "floor": .0009, "ceiling": .0011,
                "mid_at_plan": .001}]}]}}
    operator.clock = lambda: 100
    operator.price_drift_pct = 1
    operator.core = SimpleNamespace(get_goal=lambda _id: goal,
        _available_usdt=lambda _id: (100, 0), start_goal=lambda _id: pytest.fail("no debe iniciar"))
    operator.db = db
    operator.engine = None
    operator.scanner = SimpleNamespace(client=SimpleNamespace(get_book_ticker=lambda symbol: {
        "bid_price": 2.1 if symbol == "ZROUSDT" else .00105,
        "ask_price": 2.1 if symbol == "ZROUSDT" else .00105}))
    with pytest.raises(ValueError, match="PEPEUSDT se movi\u00f3 m\u00e1s de 1%"):
        operator._start_plan_locked(9, "plan", lambda _piece: pytest.fail("no debe abrir"))


def test_plan_bot_v2_se_reancla_a_libro_y_arranca_dentro_del_tres_por_ciento(
        engine_databases, monkeypatch):
    db, _market_path = engine_databases
    cached = {"run_id": "r", "as_of": datetime.now(timezone.utc).isoformat(),
        "age_seconds": 0, "status": "ok", "horizon_days": 20,
        "discarded": {}, "skipped_symbols": {}, "trajectory_file": None,
        "symbol_stats": {"ZROUSDT": {"price": 1.904, "sigma_24h": .03}},
        "candidates": [{"candidate_key": "bot", "symbol": "ZROUSDT",
            "strategy": "bot", "params": {"buy_price": 1.84688,
                "sell_price": 1.93256, "buy_offset_pct": -3.0,
                "sell_offset_pct": 1.5, "capital": 70, "n": 1},
            "metrics": {"n": 1000, "p_target": .737, "pnl_mean_usdt": 1.2,
                "p_loss": .01, "p_loss_ci95": [0, .02],
                "cut_slippage_mean_usdt": .1, "cut_slippage_p90_usdt": .2,
                "p_inventory": .99, "days_to_target_p50": 10},
            "objective": {"factible": True, "motivos": [],
                "clave_orden": [-.737, 10, -1.2]}}]}
    monkeypatch.setattr(V2ScenarioEngine, "_load_cache", lambda *_args, **_kwargs: cached)
    candidate_piece = {"symbol": "ZROUSDT", "strategy": "bot", "capital": 70,
        "buy_price": 1.84688, "sell_price": 1.93256, "buy_offset_pct": -3.0,
        "sell_offset_pct": 1.5, "candidate_key": "bot"}
    metrics = cached["candidates"][0]["metrics"]
    monkeypatch.setattr("grid.engines.v2_escenarios.rank_alternatives_v2", lambda *_args,
        **_kwargs: [{"candidate_key": "bot", "piece": candidate_piece,
            "metrics": metrics, "objetivo": {"factible": True, "motivos": []}}])
    checked = []
    book = {"ZROUSDT": 1.88}
    goal = {"id": 9, "profile": "RAPIDO", "capital_initial": 100,
        "target_usdt": 3, "status": "DRAFT",
        "config_snapshot": {"decision_engine": "v2", "plan_price_drift_pct": 1}}
    operator = object.__new__(MetaOperator)
    operator.db = db
    operator.engine = SimpleNamespace(create_grid=lambda *_args, **_kwargs: {"id": 91})
    operator.price_drift_pct = 1
    operator.clock = lambda: 1_800_000_000
    operator._book_ticker = lambda symbol: {"bid_price": book[symbol],
        "ask_price": book[symbol]}
    operator._validate_v2_plan_limits = lambda _goal, value: checked.append(
        ("limits", [dict(piece) for piece in value["pieces"]]))
    operator._testnet_openability = lambda piece, _goal: checked.append(
        ("filters", dict(piece))) or {"allowed": True}
    plan = V2ScenarioEngine().plan(operator, goal, plan_kind="analisis")
    assert len(plan["pieces"]) == 1
    assert plan["pieces"][0]["floor"] == pytest.approx(1.88 * .97)
    assert plan["pieces"][0]["ceiling"] == pytest.approx(1.88 * 1.015)
    assert plan["pieces"][0]["mid_at_plan"] == 1.88
    assert plan["pieces"][0]["price_source"] == "book"
    assert plan["pieces"][0]["price_at"]
    assert [row[0] for row in checked] == ["limits", "filters"]

    operator._lock = threading.RLock()
    operator._operation_lock = threading.RLock()
    operator._plans = {"book-plan": {**plan, "plan_id": "book-plan", "meta_id": 9,
        "expires_at": 2_000_000_000, "engine": {"name": "v2"}}}
    opened = []
    operator.core = SimpleNamespace(get_goal=lambda _id: goal,
        start_goal=lambda _id: None, link_grid=lambda _id, grid_id, _kind: opened.append(grid_id),
        _now=lambda: datetime.now(timezone.utc).replace(tzinfo=None))
    operator._active_run_id = lambda _id: 1
    operator._ledger = lambda *_args, **_kwargs: None
    operator.alert = lambda *_args, **_kwargs: None
    result = operator.start_plan(9, "book-plan", lambda _piece: {"grid_id": 91})
    assert [piece["symbol"] for piece in result["opened"]] == ["ZROUSDT"]
    assert opened == [91]


def test_plan_v2_rechaza_cache_con_movimiento_superior_al_tres_por_ciento(
        engine_databases, monkeypatch):
    db, _market_path = engine_databases
    cached = {"run_id": "r", "as_of": datetime.now(timezone.utc).isoformat(),
        "age_seconds": 0, "status": "ok", "horizon_days": 20,
        "discarded": {}, "skipped_symbols": {}, "trajectory_file": None,
        "symbol_stats": {"ZROUSDT": {"price": 1.904, "sigma_24h": .03}},
        "candidates": [{"candidate_key": "bot", "symbol": "ZROUSDT",
            "strategy": "bot", "params": {"buy_price": 1.84688,
                "sell_price": 1.93256, "buy_offset_pct": -3.0,
                "sell_offset_pct": 1.5, "capital": 70, "n": 1},
            "metrics": {"n": 1000, "p_target": .737, "pnl_mean_usdt": 1.2,
                "p_loss": .01, "p_loss_ci95": [0, .02],
                "cut_slippage_mean_usdt": .1, "cut_slippage_p90_usdt": .2,
                "p_inventory": .99, "days_to_target_p50": 10},
            "objective": {"factible": True, "motivos": [],
                "clave_orden": [-.737, 10, -1.2]}}]}
    monkeypatch.setattr(V2ScenarioEngine, "_load_cache", lambda *_args, **_kwargs: cached)
    candidate_piece = {"symbol": "ZROUSDT", "strategy": "bot", "capital": 70,
        "buy_price": 1.84688, "sell_price": 1.93256, "buy_offset_pct": -3.0,
        "sell_offset_pct": 1.5, "candidate_key": "bot"}
    monkeypatch.setattr("grid.engines.v2_escenarios.rank_alternatives_v2", lambda *_args,
        **_kwargs: [{"candidate_key": "bot", "piece": candidate_piece,
            "metrics": cached["candidates"][0]["metrics"],
            "objetivo": {"factible": True, "motivos": []}}])
    operator = SimpleNamespace(db=db, _book_ticker=lambda _symbol: {
        "bid_price": 1.82, "ask_price": 1.82})

    with pytest.raises(EngineUnavailable, match="caché está desactualizada para ZROUSDT.*-4.4%"):
        V2ScenarioEngine().plan(operator, {"id": 9, "profile": "RAPIDO",
            "capital_initial": 100, "target_usdt": 3, "status": "DRAFT"},
            plan_kind="analisis")


def test_reanclaje_grid_sigma_conserva_ancho_relativo(engine_databases):
    db, _market_path = engine_databases
    piece = {"symbol": "ZROUSDT", "floor": 80.0, "ceiling": 120.0}
    alternative = {"pieces": [piece]}
    candidate = {"piece": {"strategy": "simple", "width_sigma": .5,
        "low": 80.0, "high": 120.0}}
    operator = SimpleNamespace(_book_ticker=lambda _symbol: {
        "bid_price": 99.0, "ask_price": 99.0})
    V2ScenarioEngine._reanchor_piece(operator, {}, {"horizon_days": 16,
        "symbol_stats": {"ZROUSDT": {"price": 100.0, "sigma_24h": .02}}},
        alternative, candidate)
    assert piece["floor"] == pytest.approx(99.0 * __import__("math").exp(-.04))
    assert piece["ceiling"] == pytest.approx(99.0 * __import__("math").exp(.04))


def test_start_plan_v2_rechaza_exceso_antes_de_tocar_exchange(engine_databases):
    db, _market_path = engine_databases
    calls = []
    goal = {"id": 9, "profile": "RAPIDO", "capital_initial": 100,
        "status": "DRAFT", "config_snapshot": {"decision_engine": "v2"}}
    operator = object.__new__(MetaOperator)
    operator._lock = threading.RLock()
    operator._plans = {"bad": {"plan_id": "bad", "meta_id": 9,
        "expires_at": 200, "engine": {"name": "v2"},
        "pieces": [{"symbol": "ZROUSDT", "strategy": "simple", "piece_type": "grid",
            "capital_usdt": 100, "floor": 1.8, "ceiling": 2.2, "n_levels": 6}]}}
    operator.clock = lambda: 100
    operator.price_drift_pct = 5
    operator.core = SimpleNamespace(get_goal=lambda _id: goal,
        _owned_grids=lambda _id: [], _available_usdt=lambda _id: (100, 0),
        start_goal=lambda _id: calls.append("start"))
    operator.db = db
    operator.engine = None
    operator._plan_prices = lambda _plan: {}
    operator._active_run_id = lambda _id: 1
    operator._ledger = lambda *_args, **_kwargs: calls.append("ledger")
    operator.alert = lambda *_args, **_kwargs: calls.append("alert")

    with pytest.raises(ValueError, match="límite de capital por moneda"):
        operator._start_plan_locked(9, "bad", lambda _piece: calls.append("open"))
    assert calls == []


def test_start_plan_v2_abre_y_liga_pieza_dentro_del_tope(engine_databases):
    db, _market_path = engine_databases
    linked = []
    goal = {"id": 9, "profile": "RAPIDO", "capital_initial": 100,
        "status": "DRAFT", "config_snapshot": {"decision_engine": "v2"}}
    operator = object.__new__(MetaOperator)
    operator._lock = threading.RLock()
    operator._plans = {"good": {"plan_id": "good", "meta_id": 9,
        "expires_at": 200, "engine": {"name": "v2"}, "control_plan": {},
        "pieces": [{"symbol": "ZROUSDT", "strategy": "simple", "piece_type": "grid",
            "capital_usdt": 70, "floor": 1.8, "ceiling": 2.2, "n_levels": 6}]}}
    operator.clock = lambda: 100
    operator.price_drift_pct = 5
    operator.core = SimpleNamespace(get_goal=lambda _id: goal,
        _owned_grids=lambda _id: [], _available_usdt=lambda _id: (100, 0),
        start_goal=lambda _id: None, link_grid=lambda _id, grid_id, _kind: linked.append(grid_id),
        _now=lambda: datetime.now(timezone.utc).replace(tzinfo=None))
    operator.db = db
    operator.engine = None
    operator._plan_prices = lambda _plan: {}
    operator._active_run_id = lambda _id: 1
    operator._ledger = lambda *_args, **_kwargs: None
    operator.alert = lambda *_args, **_kwargs: None

    result = operator._start_plan_locked(9, "good", lambda _piece: {"grid_id": 77})

    assert linked == [77]
    assert result["opened"][0]["capital_usdt"] == 70


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
    operator, goal, valuation, plan = _review_fixture()
    plan["exploration_enabled"] = True
    engine = V2ScenarioEngine()
    engine._execute_rotation_v2 = lambda _operator, _goal, action: calls.append(action) or {
        "decision": "cerrar_y_reasignar"}

    result = engine.review(operator, goal, valuation, plan)

    assert len(calls) == 1
    assert calls[0]["alternative"]["alternative_id"] == "change"
    assert result["decision"] == "cerrar_y_reasignar"
    assert result["comparison_v2"]["probability_improvement_pp"] == pytest.approx(10)


def test_revision_v2_con_interruptor_exploracion_no_castiga_perdida_realizada():
    operator, goal, valuation, plan = _review_fixture()
    valuation["pieces"][0]["levels"] = [{"held_qty": 100, "entry_price": 1.0}]
    valuation["symbol_valuations"] = {"JUPUSDT": {"best_bid": .8}}
    calls = []
    engine = V2ScenarioEngine()
    engine._execute_rotation_v2 = lambda *_args: calls.append(True) or {"decision": "rotar_v2"}
    plan["exploration_enabled"] = True

    result = engine.review(operator, goal, valuation, plan)

    assert result["decision"] == "rotar_v2"
    assert calls == [True]


def test_revision_v2_agrega_cartera_voraz_con_frenos_del_operador():
    operator, goal, valuation, plan = _review_fixture(
        alt_probability=.4, current_probability=.4)
    added = []
    operator._add_piece_from_free_cash = lambda _goal, links, candidate_plan, valuation=None: (
        added.append((links, candidate_plan, valuation)) or {
            "decision": "abrir_pieza_con_efectivo_libre"})
    alternative = plan["alternatives"][0]
    alternative["estimate"]["probability"] = .4
    plan["portfolio"] = {"added": [{"candidate_key": "candidate"}],
        "steps": [{"candidate_key": "candidate", "p_target_delta_pp": 5}]}

    result = V2ScenarioEngine().review(operator, goal, valuation, plan)

    assert result["decision"] == "abrir_pieza_con_efectivo_libre"
    assert len(added) == 1
    assert added[0][1]["alternatives"][0]["alternative_id"] == "change"


def _shadow_action_plan(action):
    current_piece = {"grid_id": 13, "symbol": "JUPUSDT", "strategy": "smart",
        "floor": .20, "ceiling": .30, "capital_usdt": 50, "n_levels": 6,
        "piece_type": "grid"}
    alternative_piece = {"symbol": "XRPUSDT", "strategy": "simple", "floor": .5,
        "ceiling": .7, "capital_usdt": 40, "n_levels": 6, "piece_type": "grid"}
    current_estimate = {"probability": .4, "pnl_mean_usdt": .5,
        "pnl_mean_ci95": [.3, .7]}
    current = {"alternative_id": "keep", "symbol": "JUPUSDT", "strategy": "smart",
        "pieces": [current_piece], "estimate": current_estimate,
        "cache_candidate_key": "keep-13", "is_current": True}
    alt_probability = .6 if action == "cambiar" else .4
    alternative = {"alternative_id": "alternative", "symbol": "XRPUSDT",
        "strategy": "simple", "pieces": [alternative_piece],
        "estimate": {"probability": alt_probability, "pnl_mean_usdt": 1.5,
            "pnl_mean_ci95": [1.2, 1.8], "evidence_sufficient": True},
        "objective": {"factible": True}, "cache_candidate_key": "alternative",
        "is_current": False}
    return {"alternatives": [alternative, current], "current_pieces": {"13": {
        "piece": current_piece, "estimate": current_estimate}},
        "portfolio": ({"added": [{"candidate_key": "alternative"}],
            "steps": [{"candidate_key": "alternative", "p_target_delta_pp": 10}]}
            if action == "agregar" else {}), "cache": {"run_id": "shadow-cache"}}


@pytest.mark.parametrize("action", ["cambiar", "agregar"])
def test_sombra_v2_de_meta_v1_no_actua_y_solo_persiste_sombra_y_bitacora(
        engine_databases, monkeypatch, action):
    db, _market_path = engine_databases
    core = _meta_three_live_piece_fixture(db)
    goal = {"id": 9, "profile": "RAPIDO", "capital_initial": 100,
        "target_usdt": 3, "status": "ACTIVE",
        "config_snapshot": {"decision_engine": "v1"}}
    valuation = {"pieces": [{"grid_id": 13, "symbol": "JUPUSDT", "levels": []}],
        "symbol_valuations": {"JUPUSDT": {"best_bid": .25}}}
    plan = _shadow_action_plan(action)
    plan["exploration_enabled"] = True
    monkeypatch.setattr(V2ScenarioEngine, "plan",
        lambda _self, _operator, _goal, **_kwargs: plan)
    class CountingExchange:
        def __init__(self):
            self.calls = []
        def place_order(self, *args, **kwargs):
            self.calls.append(("place_order", args, kwargs))
        def cancel_order(self, *args, **kwargs):
            self.calls.append(("cancel_order", args, kwargs))
    exchange = CountingExchange()
    mutations = []
    operator = SimpleNamespace(core=core, db=db,
        _review_allocations=lambda *_args, **_kwargs: (
            exchange.cancel_order("JUPUSDT", 13) or mutations.append("rotar")),
        _add_piece_from_free_cash=lambda *_args, **_kwargs: (
            exchange.place_order("XRPUSDT", "BUY") or mutations.append("agregar")))
    before = _shadow_db_counts(db)

    shadow = MetaOperator._run_engine_shadow(operator, goal, valuation, {},
        SimpleNamespace(name="v1"))

    assert shadow["decision"] == action
    assert shadow["result"]["proposed_action"]["type"] == (
        "rotar" if action == "cambiar" else "agregar")
    assert exchange.calls == [] and mutations == []
    assert _shadow_db_counts(db) == before

    MetaOperator._persist_engine_shadow_decision(operator, goal,
        SimpleNamespace(name="v1"), {"decision": "mantener"},
        {"name": "v2", "decision": action, "result": shadow["result"]}, {})
    with db.engine.begin() as conn:
        conn.execute(db.meta_ledger.insert().values(meta_id=9,
            ts=datetime.now(timezone.utc).replace(tzinfo=None),
            event_type="META_OPERATOR_REVIEW_DECISION",
            details=json.dumps({"decision": {"decision": "mantener"},
                "shadow_decision": shadow})))
    after = _shadow_db_counts(db)
    assert after["grids"] == before["grids"]
    assert after["grid_levels"] == before["grid_levels"]
    assert after["meta_piece_links"] == before["meta_piece_links"]
    assert after["orders"] == before["orders"]
    assert after["meta_analysis_cache"] == before["meta_analysis_cache"]
    assert after["engine_shadow_decisions"] == before["engine_shadow_decisions"] + 1
    assert after["meta_ledger"] == before["meta_ledger"] + 1


def _shadow_db_counts(db):
    with db.engine.connect() as conn:
        counts = {name: conn.exec_driver_sql(f'SELECT COUNT(*) FROM "{name}"').scalar_one()
            for name in ("grids", "grid_levels", "meta_piece_links", "grid_events",
                "meta_analysis_cache", "engine_shadow_decisions", "meta_ledger")}
        counts["orders"] = conn.exec_driver_sql(
            "SELECT COUNT(*) FROM grid_levels WHERE order_id IS NOT NULL").scalar_one()
    return counts


@pytest.mark.parametrize("action", ["cambiar", "agregar"])
def test_revision_activa_v2_ejecuta_dentro_del_candado(action, monkeypatch):
    goal = {"id": 9, "profile": "RAPIDO", "capital_initial": 100,
        "target_usdt": 3, "status": "ACTIVE", "result_pct": 0,
        "config_snapshot": {"decision_engine": "v2", "warning_loss_pct": 20}}
    valuation = {"status": "KNOWN", "result_pct": 0, "cash_usdt": 40,
        "pieces": [{"grid_id": 13, "symbol": "JUPUSDT", "levels": []}],
        "symbol_valuations": {"JUPUSDT": {"best_bid": .25}}}
    plan = _shadow_action_plan(action)
    plan["exploration_enabled"] = True
    state = {"held": False, "seen": [], "ledger": [], "now": 1000.0}

    class OperationLock:
        def __enter__(self):
            state["held"] = True
            return self
        def __exit__(self, *_args):
            state["held"] = False

    operator = object.__new__(MetaOperator)
    operator._operation_lock = OperationLock()
    operator.core = SimpleNamespace(get_goal=lambda _goal_id: goal,
        _owned_grids=lambda _goal_id: [{"grid_id": 13}])
    engine = SimpleNamespace(name="v2", version="test",
        plan=lambda *_args, **_kwargs: plan)
    operator._engine_for_goal = lambda _goal: engine
    operator._valuation_with_retry = lambda _goal_id: (valuation, [])
    operator._plan_price_moved = lambda *_args: False
    operator._touch_bot_shadow_proposals = lambda *_args: []
    operator._reset_rotation_streaks = lambda *_args: None
    operator._run_engine_shadow = lambda *_args, **_kwargs: {"name": "v1", "decision": "mantener"}
    operator._persist_engine_shadow_decision = lambda *_args: None
    operator._safe_active_review_choices = lambda *_args: {"real": {}}
    operator._ledger = lambda *_args, **_kwargs: None
    def record(_meta_id, event_type, details, *, grid_id=None):
        state["ledger"].append({"event_type": event_type, "details": details,
            "grid_id": grid_id})
    operator._ledger = record
    operator._meta_ledger_rows = lambda _meta_id: list(state["ledger"])
    operator.clock = lambda: state["now"]
    operator._validate_v2_plan_limits = lambda *_args, **_kwargs: None
    def execute(*_args, **_kwargs):
        state["seen"].append(state["held"])
        return {"decision": "ejecutada"}
    operator._review_allocations = lambda *_args, **_kwargs: pytest.fail(
        "v2 no debe entrar a los frenos v1")
    operator._add_piece_from_free_cash = execute
    monkeypatch.setattr(V2ScenarioEngine, "_execute_rotation_v2",
        staticmethod(lambda _operator, _goal, _action: execute()))
    engine.review = lambda _operator, _goal, _valuation, plan=None: (
        V2ScenarioEngine().review(_operator, _goal, _valuation, plan))

    first = MetaOperator._review_once_locked(operator, 9)
    assert first["decision"] == "mantener"
    state["now"] += 901
    result = MetaOperator._review_once_locked(operator, 9)

    assert result["decision"] == "ejecutada"
    assert state["seen"] == [True]


def test_guardas_lanzan_error_si_sombra_intenta_ejecutar():
    engine = object.__new__(GridEngine)
    core = object.__new__(MetaCore)

    with shadow_execution_context():
        with pytest.raises(ShadowSideEffectError, match="shadow_violation"):
            engine.create_grid("XRPUSDT", 1, 2, 4)
        with pytest.raises(ShadowSideEffectError, match="shadow_violation"):
            engine.close_grid(1, "cancel")
        with pytest.raises(ShadowSideEffectError, match="shadow_violation"):
            engine.cancel_grid_orders(1)
        with pytest.raises(ShadowSideEffectError, match="shadow_violation"):
            engine._send_limit("XRPUSDT", "BUY", 1, 1, "shadow", None, 1)
        with pytest.raises(ShadowSideEffectError, match="shadow_violation"):
            engine._cancel_order("XRPUSDT", 1)
        with pytest.raises(ShadowSideEffectError, match="shadow_violation"):
            core.start_goal(1)
        with pytest.raises(ShadowSideEffectError, match="shadow_violation"):
            core.link_grid(1, 1)
        with pytest.raises(ShadowSideEffectError, match="shadow_violation"):
            core.cancel(1, "liquidate", confirm=True)
        with pytest.raises(ShadowSideEffectError, match="shadow_violation"):
            MetaOperator._review_allocations(object.__new__(MetaOperator), {}, {})
        with pytest.raises(ShadowSideEffectError, match="shadow_violation"):
            MetaOperator._persist_engine_shadow_decision(
                object.__new__(MetaOperator), {}, {}, {}, {}, {})


def test_sombra_v2_convierte_violacion_en_no_disponible(monkeypatch):
    engine = V2ScenarioEngine()
    operator = SimpleNamespace(core=SimpleNamespace(_owned_grids=lambda _id: []),
        _review_allocations=lambda *_args, **_kwargs:
            MetaOperator._review_allocations(object.__new__(MetaOperator), {}, {}))
    monkeypatch.setattr(engine, "plan", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(engine, "decide", lambda *_args, **_kwargs:
        operator._review_allocations({}, {}))

    result = engine.shadow_review(operator, {"id": 9}, {}, {})

    assert result["decision"] == "no_disponible"
    assert result["shadow_violation"] is True


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
    _cache_row(market_path, status="parcial", error={"pass1_coverage": {
        "expected_symbols": ["JUPUSDT"], "covered_symbols": ["JUPUSDT"], "complete": True}})

    cache = V2ScenarioEngine._load_cache(SimpleNamespace(db=db),
        {"id": 9, "profile": "RAPIDO"})

    assert cache["status"] == "parcial"
    assert cache["candidates"]


def test_v2_rechaza_cache_parcial_sin_cobertura_de_pasada_1(engine_databases):
    db, market_path = engine_databases
    _cache_row(market_path, status="parcial", error={"pass1_coverage": {
        "expected_symbols": ["JUPUSDT", "WIFUSDT"], "covered_symbols": ["JUPUSDT"],
        "complete": False}})

    with pytest.raises(EngineUnavailable, match="pasada 1 no cubrió"):
        V2ScenarioEngine._load_cache(SimpleNamespace(db=db),
            {"id": 9, "profile": "RAPIDO"})


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


def test_meta_v2_con_sombra_v1_no_escribe_cache_persistente(engine_databases):
    db, _market_path = engine_databases
    operator = object.__new__(MetaOperator)
    operator.db = db
    operator.cache_enabled = True
    operator.cache_writes = True
    exchange_calls = []
    operator.exchange = SimpleNamespace(
        place_order=lambda *args, **kwargs: exchange_calls.append(("place", args, kwargs)),
        cancel_order=lambda *args, **kwargs: exchange_calls.append(("cancel", args, kwargs)))
    before = _shadow_db_counts(db)

    def build(_goal, **kwargs):
        assert kwargs["record"] is False
        operator._persistent_cache_set("advisor", {"synthetic": "shadow"},
            {"result": "no persistir"})
        return {"decision_choices": {"real": {"decision": "mantener"}}}
    operator._build_plan = build

    result = MetaOperator._run_engine_shadow(operator,
        {"id": 9, "profile": "RAPIDO", "capital_initial": 100}, {}, {},
        SimpleNamespace(name="v2"))

    assert result["decision"] == "mantener"
    assert exchange_calls == []
    assert _shadow_db_counts(db) == before


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


def test_interruptor_exploracion_por_defecto_encendido_solo_con_testnet(engine_databases):
    db, _market_path = engine_databases
    operator = object.__new__(MetaOperator)
    operator.db = db
    operator.core = SimpleNamespace(settings=SimpleNamespace(
        testnet_api_key="clave-configurada"))
    status = MetaOperator.v2_exploration_status(operator)
    assert status["enabled"] is True
    assert status["defaulted"] is True
    assert status["environment"] == "testnet"
    MetaOperator.set_v2_exploration_enabled(operator, False, confirm=True, actor="prueba")
    assert MetaOperator.v2_exploration_status(operator)["enabled"] is False


def test_frenos_v2_persistencia_cooldown_y_limite_diario():
    now = {"value": 5000.0}
    rows = []
    operator = SimpleNamespace(clock=lambda: now["value"],
        _meta_ledger_rows=lambda _goal_id: list(rows),
        _ledger=lambda _goal_id, event_type, details, **_kwargs: rows.append({
            "event_type": event_type, "details": details}))
    brake = V2ScenarioEngine._v2_brake(operator, {"id": 3},
        action_key="rotar:8:candidate", subject="8", grid_id=8)
    assert "dos revisiones" in brake
    now["value"] += 901
    brake = V2ScenarioEngine._v2_brake(operator, {"id": 3},
        action_key="rotar:8:candidate", subject="8", grid_id=8)
    assert brake is None
    rows.append({"event_type": "META_V2_ACTION", "details": {
        "subject": "8", "review_at_s": now["value"] - 100}})
    now["value"] += 901
    brake = V2ScenarioEngine._v2_brake(operator, {"id": 3},
        action_key="rotar:8:candidate", subject="8", grid_id=8)
    assert "6 horas" in brake
    rows.extend([{"event_type": "META_V2_ACTION", "details": {
        "subject": "otro", "review_at_s": now["value"] - 500}},
        {"event_type": "META_V2_ACTION", "details": {
        "subject": "otro2", "review_at_s": now["value"] - 600}}])
    rows.append({"event_type": "META_V2_REVIEW", "details": {
        "action_key": "rotar:8:candidate", "review_at_s": now["value"] - 901}})
    now["value"] += 901
    brake = V2ScenarioEngine._v2_brake(operator, {"id": 3},
        action_key="rotar:8:candidate", subject="8", grid_id=8)
    assert "dos cambios" in brake


def test_bitacora_compacta_conserva_auditoria_v2():
    comparison = {"modo": "exploracion", "keep": {"symbol": "ZROUSDT"},
        "alternative": {"symbol": "ZROUSDT", "estimate": {"p_loss": .02}}}
    compact = MetaOperator._compact_review_result({"decision": "rotar_v2",
        "modo": "exploracion", "reason": "mejora esperada",
        "comparison_v2": comparison, "freno": None})
    assert compact["modo"] == "exploracion"
    assert compact["comparison_v2"] == comparison


def test_meta_sintetica_v2_explora_reconvierte_bot_y_aplica_frenos(monkeypatch):
    from threading import RLock
    now = {"value": 10_000.0}
    ledger = []
    calls = []
    goal = {"id": 77, "profile": "RAPIDO", "capital_initial": 100,
        "target_usdt": 10, "status": "ACTIVE",
        "config_snapshot": {"decision_engine": "v2", "warning_loss_pct": 20}}
    keep_piece = {"grid_id": 18, "symbol": "ZROUSDT", "strategy": "bot",
        "floor": 1.58, "ceiling": 1.87, "capital_usdt": 70, "n_levels": 1,
        "piece_type": "individual_bot_grid"}
    convert_piece = {"symbol": "ZROUSDT", "strategy": "simple",
        "floor": 1.42, "ceiling": 1.91, "capital_usdt": 70, "n_levels": 8,
        "piece_type": "grid", "reconversion": True}
    keep_est = {"probability": .01, "pnl_mean_usdt": -8,
        "pnl_mean_ci95": [-9, -7], "evidence_sufficient": True}
    alt_est = {"probability": .02, "pnl_mean_usdt": 1.2,
        "pnl_mean_ci95": [.8, 1.6], "p_loss": .01,
        "p_loss_ci95": [0, .02], "cut_slippage_expected_usdt": .1,
        "p_target": .02, "evidence_sufficient": True}
    keep = {"alternative_id": "keep-18", "symbol": "ZROUSDT",
        "strategy": "bot", "pieces": [keep_piece], "estimate": keep_est,
        "cache_candidate_key": "keep-18", "is_current": True,
        "objective": {"factible": True}}
    candidate = {"alternative_id": "reconvert-18", "symbol": "ZROUSDT",
        "strategy": "simple", "pieces": [convert_piece], "estimate": alt_est,
        "metrics": {**alt_est, "cycles_expected": 2.4},
        "cache_candidate_key": "reconvert-18", "is_current": False,
        "objective": {"factible": False, "motivos": ["piso de EV"]}}
    plan = {"alternatives": [candidate, keep], "current_pieces": {"18": {
        "piece": keep_piece, "estimate": keep_est, "alternative_id": "keep-18"}},
        "exploration_enabled": True, "profile_config": {"p_limit_cap": 15,
            "cut_slippage_expected_max_pct": .5}, "cache": {"run_id": "synthetic"}}
    class Exchange:
        def close_grid(self, grid_id, mode, **kwargs):
            calls.append(("close", grid_id, mode))
            return {"status": "CLOSED", "realized_pnl_usdt": -8}
    operator = SimpleNamespace(core=SimpleNamespace(_owned_grids=lambda _id: [
            {"grid_id": 18, "piece_type": "individual_bot_grid"}]),
        db=SimpleNamespace(get_grid=lambda _id: {"id": 18, "symbol": "ZROUSDT",
            "status": "ACTIVE", "capital_total": 70},
            get_grid_levels=lambda _id: [{"held_qty": 39.12}]),
        engine=Exchange(), _operation_lock=RLock(), clock=lambda: now["value"],
        _meta_ledger_rows=lambda _id: list(ledger),
        _ledger=lambda _id, event, details, **kwargs: ledger.append({
            "event_type": event, "details": details, "grid_id": kwargs.get("grid_id")}),
        _validate_v2_plan_limits=lambda *_args, **_kwargs: calls.append(("validate",)),
        _testnet_openability=lambda *_args, **_kwargs: {"allowed": True},
        _open_replacement=lambda _goal, _alternative, _capital: (
            calls.append(("open", "ZROUSDT", 8)) or {"grid_id": 19}))
    engine = V2ScenarioEngine()
    decision = engine.decide(operator, goal, {"pieces": [{"grid_id": 18,
        "symbol": "ZROUSDT", "levels": [{"held_qty": 39.12, "entry_price": 1.789}],
        "symbol_valuations": {}}]}, plan)
    assert decision["decision"] == "cambiar"
    assert decision["modo"] == "exploracion"
    assert decision["proposed_action"]["alternative"]["alternative_id"] == "reconvert-18"
    first = engine.review(operator, goal, {}, plan)
    assert first["decision"] == "mantener" and not calls
    now["value"] += 901
    second = engine.review(operator, goal, {"pieces": [{"grid_id": 18,
        "symbol": "ZROUSDT", "levels": [{"held_qty": 39.12, "entry_price": 1.789}],
        "symbol_valuations": {}}]}, plan)
    assert second["decision"] == "rotar_v2"
    assert [row[0] for row in calls if row[0] in {"close", "open"}] == ["close", "open"]
    now["value"] += 901
    third = engine.review(operator, goal, {}, plan)
    assert third["decision"] == "mantener" and "6 horas" in third["reason"]
    assert [row[0] for row in calls if row[0] == "close"] == ["close"]


def test_interruptor_exploracion_apagado_conserva_no_cambiar():
    operator, goal, valuation, plan = _review_fixture(alt_probability=.02,
        current_probability=.01, alt_pnl=1.2, current_pnl=-8)
    valuation["pieces"][0]["levels"] = [{"held_qty": 39.12, "entry_price": 1.789}]
    valuation["symbol_valuations"] = {"ZROUSDT": {"best_bid": 1.63}}
    plan["exploration_enabled"] = False
    result = V2ScenarioEngine().decide(operator, goal, valuation, plan)
    assert result["decision"] == "mantener"


def test_interruptor_exploracion_apagado_delega_en_comportamiento_previo():
    operator, goal, valuation, plan = _review_fixture()
    calls = []
    operator._review_allocations = lambda _goal, _value, plan=None: (
        calls.append(plan) or {"decision": "cerrar_y_reasignar"})
    result = V2ScenarioEngine().review(operator, goal, valuation, plan)
    assert result["decision"] == "cerrar_y_reasignar"
    assert len(calls) == 1
