import json
import sqlite3
import subprocess
import sys

import pytest
from sqlalchemy import select

from data.market_store import CandleStore
from database.db_manager import DBManager
from scheduler.scenario_cache_job import ScenarioCacheJob
import scripts.escenarios_cache as cache_script


@pytest.fixture
def cache_databases(tmp_path):
    app_path = tmp_path / "app.sqlite"
    market_path = tmp_path / "market.sqlite"
    db = DBManager(f"sqlite:///{app_path.as_posix()}")
    CandleStore(market_path)
    yield db, market_path
    db.engine.dispose()


def _set_enabled(db, enabled):
    with db.engine.begin() as conn:
        raw = conn.execute(select(db.meta_profile_settings.c.settings).where(
            db.meta_profile_settings.c.profile == "GLOBAL")).scalar_one()
        values = json.loads(raw)
        values["scenario_cache_enabled"] = enabled
        conn.execute(db.meta_profile_settings.update().where(
            db.meta_profile_settings.c.profile == "GLOBAL").values(
                settings=json.dumps(values)))


def test_job_apagado_no_inicia_subproceso(cache_databases):
    db, market_path = cache_databases
    calls = []
    job = ScenarioCacheJob(db, market_path, run=lambda *args, **kwargs: calls.append(args))

    result = job.run_if_enabled()

    assert result["status"] == "disabled"
    assert calls == []


def test_timeout_queda_registrado_en_la_corrida(cache_databases):
    db, market_path = cache_databases
    _set_enabled(db, True)
    with sqlite3.connect(market_path) as conn:
        conn.execute("INSERT INTO scenario_eval_runs(run_id,as_of,profile,horizon_days,"
            "n_scenarios,status) VALUES('run-timeout','2026-10-30T00:00:00+00:00',"
            "'RAPIDO',20,1000,'running')")

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], kwargs["timeout"])

    job = ScenarioCacheJob(db, market_path, run=timeout)
    result = job.run_if_enabled()

    assert result["status"] == "timeout"
    with sqlite3.connect(market_path) as conn:
        status = conn.execute("SELECT status FROM scenario_eval_runs WHERE run_id='run-timeout'").fetchone()[0]
    assert status == "timeout"


def test_importar_job_no_importa_numpy_ni_evaluador_en_servicio():
    code = ("import sys; import scheduler.scenario_cache_job; "
        "assert 'numpy' not in sys.modules; "
        "assert 'research.scenarios.evaluate' not in sys.modules")
    result = subprocess.run([sys.executable, "-c", code], capture_output=True,
        text=True, check=False)
    assert result.returncode == 0, result.stderr


def test_corrida_completa_persiste_candidatos_y_usdt_quieto(cache_databases, tmp_path,
        monkeypatch):
    db, market_path = cache_databases
    with db.engine.begin() as conn:
        row = conn.execute(select(db.meta_profile_settings.c.settings).where(
            db.meta_profile_settings.c.profile == "PRUDENTE")).scalar_one()
        values = json.loads(row)
        values["decision_engine"] = "v2"
        conn.execute(db.meta_profile_settings.update().where(
            db.meta_profile_settings.c.profile == "PRUDENTE").values(
                settings=json.dumps(values)))
    piece = {"candidate_key": "simple-test", "symbol": "JUPUSDT",
        "strategy": "simple", "capital": 50.0, "n": 6,
        "low": .2, "high": .3}
    monkeypatch.setattr(cache_script, "_read_market_inputs",
        lambda _store: ({"JUPUSDT": object()}, {"JUPUSDT": .25},
            {"JUPUSDT": .04}, {}))
    monkeypatch.setattr(cache_script, "build_candidates",
        lambda **_kwargs: {"candidates": [piece, {"strategy": "usdt_quieto"}],
            "discarded": {}})
    monkeypatch.setattr(cache_script, "build_smart_candidates", lambda _rows: [])
    monkeypatch.setattr(cache_script, "rank_alternatives_v2",
        lambda rows, **_kwargs: [{"piece": row.get("piece", row),
            "objetivo": {"factible": True, "motivos": [], "clave_orden": [0, 0, 0]}}
            for row in rows])

    def evaluate(pieces, **_kwargs):
        return {"pieces": [{"piece": candidate, "metrics": {"n": 1000,
            "pnl_mean_usdt": 1.2, "p_target": .4, "p_loss": .1,
            "p_loss_ci95": [.08, .12], "cvar_5_usdt": -2,
            "cut_slippage_mean_usdt": .1, "cut_slippage_p90_usdt": .3}}
            for candidate in pieces], "skipped_symbols": {}}

    result = cache_script.run_cache(db.engine.url.database, market_path,
        library_dir=tmp_path, evaluator=evaluate,
        now=__import__("datetime").datetime(2026, 10, 30, tzinfo=__import__(
            "datetime").timezone.utc))

    assert result["contexts"] == 1
    assert result["runs"][0]["status"] == "ok"
    with sqlite3.connect(market_path) as conn:
        rows = conn.execute("SELECT strategy FROM scenario_eval_results ORDER BY strategy").fetchall()
        status = conn.execute("SELECT status FROM scenario_eval_runs").fetchone()[0]
    assert rows == [("simple",), ("usdt_quieto",)]
    assert status == "ok"
