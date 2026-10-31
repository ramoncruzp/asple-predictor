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


def test_job_fija_hilos_y_no_usa_rlimit_ni_preexec(cache_databases):
    db, market_path = cache_databases
    _set_enabled(db, True)
    captured = {}

    def run(args, **kwargs):
        captured.update(kwargs)
        captured["args"] = args
        return subprocess.CompletedProcess(args, 0, "ok", "")

    result = ScenarioCacheJob(db, market_path, run=run).run_if_enabled()

    assert result["status"] == "completed"
    assert captured["env"]["OPENBLAS_NUM_THREADS"] == "1"
    assert captured["env"]["OMP_NUM_THREADS"] == "1"
    assert captured["env"]["MALLOC_ARENA_MAX"] == "2"
    assert "preexec_fn" not in captured
    assert "RLIMIT_AS" not in open("scheduler/scenario_cache_job.py", encoding="utf-8").read()


def test_rss_superior_al_limite_marca_corrida_memoria(cache_databases, tmp_path):
    db, market_path = cache_databases
    _set_enabled(db, True)
    with sqlite3.connect(market_path) as conn:
        conn.execute("INSERT INTO scenario_eval_runs(run_id,as_of,profile,horizon_days,"
            "n_scenarios,status) VALUES('run-memory','2026-10-30T00:00:00+00:00',"
            "'RAPIDO',20,1000,'running')")
    child = tmp_path / "slow_child.py"
    child.write_text("import time; time.sleep(10)\n", encoding="utf-8", newline="\n")
    job = ScenarioCacheJob(db, market_path, script_path=child,
        rss_reader=lambda _pid: 601 * 1024 * 1024)

    result = job.run_if_enabled()

    assert result["status"] == "memoria"
    with sqlite3.connect(market_path) as conn:
        status = conn.execute("SELECT status FROM scenario_eval_runs "
            "WHERE run_id='run-memory'").fetchone()[0]
    assert status == "memoria"


def test_contexto_meta_calcula_efectivo_valor_inventario_y_objetivo(cache_databases):
    db, _market_path = cache_databases
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with db.engine.begin() as conn:
        goal_id = conn.execute(db.meta_goals.insert().values(
            capital_initial=100, target_usdt=10, target_input_type="USDT",
            target_input_value=10, profile="RAPIDO", status="ACTIVE",
            environment="testnet", created_at=now, started_at=now,
            config_snapshot=json.dumps({"max_loss_pct": 18}))).inserted_primary_key[0]
    grid = db.create_grid_with_levels({"symbol": "XRPUSDT", "range_low": .8,
        "range_high": 1.2, "n_levels": 6, "capital_total": 40,
        "status": "active", "environment": "testnet"}, [{"level_idx": 0,
        "price": 1, "capital": 40, "sell_price": 1.1,
        "entry_price": 1, "held_qty": 40, "state": "HOLDING"}])
    with db.engine.begin() as conn:
        run_id = conn.execute(db.meta_runs.insert().values(meta_id=goal_id,
            status="ACTIVE", started_at=now, realized_extra_usdt=0)).inserted_primary_key[0]
        conn.execute(db.meta_piece_links.insert().values(meta_id=goal_id,
            run_id=run_id, grid_id=grid["id"], piece_type="grid", created_at=now))
    db.add_grid_event(run_id=grid.get("run_id"), source="MONITOR",
        event_type="BUY_FILLED", grid_id=grid["id"], price=1,
        details={"quote_qty": 40, "fee_usdt": .04})

    context = cache_script._contexts(db.engine.url.database, {"XRPUSDT": 1})[0]

    assert context["free_cash"] == pytest.approx(59.96)
    assert context["capital"] == pytest.approx(99.92004)
    assert context["target"] == pytest.approx(10)
    assert context["horizon"] == 20
    assert context["balance_approximate"] is False


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
    invalid_piece = {"candidate_key": "simple-invalid", "symbol": "JUPUSDT",
        "strategy": "simple", "capital": 25.0, "n": 10, "low": .2, "high": .3}
    monkeypatch.setattr(cache_script, "_read_market_inputs",
        lambda _store: ({"JUPUSDT": object()}, {"JUPUSDT": .25},
            {"JUPUSDT": .04}, {}))
    monkeypatch.setattr(cache_script, "build_candidates",
        lambda **_kwargs: {"candidates": [piece, invalid_piece, {"strategy": "usdt_quieto"}],
            "discarded": {}})
    monkeypatch.setattr(cache_script, "build_smart_candidates", lambda _rows: [])
    monkeypatch.setattr(cache_script, "rank_alternatives_v2",
        lambda rows, **_kwargs: [{"piece": row.get("piece", row),
            "objetivo": {"factible": True, "motivos": [], "clave_orden": [0, 0, 0]}}
            for row in rows])

    def evaluate(pieces, **_kwargs):
        if any(candidate["candidate_key"] == "simple-invalid" for candidate in pieces):
            raise ValueError("level 5 net sell does not meet quantity/notional filters")
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
    assert result["runs"][0]["evaluated_count"] == 1
    assert result["runs"][0]["discarded"]["level 5 net sell does not meet quantity/notional filters"] == 1
    with sqlite3.connect(market_path) as conn:
        rows = conn.execute("SELECT strategy FROM scenario_eval_results ORDER BY strategy").fetchall()
        status, error = conn.execute("SELECT status,error FROM scenario_eval_runs").fetchone()
    assert rows == [("simple",), ("usdt_quieto",)]
    assert status == "ok"
    assert json.loads(error)["evaluated_count"] == 1
    assert json.loads(error)["discarded"]["level 5 net sell does not meet quantity/notional filters"] == 1


def test_corrida_running_antigua_se_marca_abandonada(cache_databases):
    from datetime import datetime, timedelta, timezone

    _db, market_path = cache_databases
    old = (datetime.now(timezone.utc) - timedelta(minutes=31)).isoformat()
    with sqlite3.connect(market_path) as conn:
        conn.execute("INSERT INTO scenario_eval_runs(run_id,as_of,profile,horizon_days,"
            "n_scenarios,status) VALUES('run-antigua',?,'RAPIDO',20,1000,'running')",
            (old,))

    result = cache_script.run_cache(_db.engine.url.database, market_path,
        evaluator=lambda *_args, **_kwargs: {"pieces": []})

    assert result["contexts"] == 0
    with sqlite3.connect(market_path) as conn:
        status = conn.execute("SELECT status FROM scenario_eval_runs "
            "WHERE run_id='run-antigua'").fetchone()[0]
    assert status == "abandonada"


def test_vigilante_distingue_memoria_y_tiempo(monkeypatch):
    monkeypatch.setattr(cache_script, "_rss_current_mb", lambda: 51)
    with pytest.raises(cache_script.CacheLimitExceeded) as memory:
        cache_script._check_limits(0, 50, None)
    assert memory.value.status == "memoria"

    monkeypatch.setattr(cache_script, "_rss_current_mb", lambda: 1)
    with pytest.raises(cache_script.CacheLimitExceeded) as timeout:
        cache_script._check_limits(0, None, .001)
    assert timeout.value.status == "timeout"


@pytest.mark.parametrize(("limit_kwargs", "expected_status"), [
    ({"max_rss_mb": 0, "max_seconds": None}, "memoria"),
    ({"max_rss_mb": None, "max_seconds": 1}, "timeout"),
])
def test_limite_de_corrida_persiste_resultados_y_status(cache_databases, tmp_path,
        monkeypatch, limit_kwargs, expected_status):
    db, market_path = cache_databases
    with db.engine.begin() as conn:
        raw = conn.execute(select(db.meta_profile_settings.c.settings).where(
            db.meta_profile_settings.c.profile == "PRUDENTE")).scalar_one()
        settings = json.loads(raw)
        settings["decision_engine"] = "v2"
        conn.execute(db.meta_profile_settings.update().where(
            db.meta_profile_settings.c.profile == "PRUDENTE").values(
                settings=json.dumps(settings)))
    piece = {"candidate_key": "partial", "symbol": "JUPUSDT",
        "strategy": "simple", "capital": 50, "n": 6, "low": .2, "high": .3}
    monkeypatch.setattr(cache_script, "_read_market_inputs",
        lambda _store: ({"JUPUSDT": object()}, {"JUPUSDT": .25}, {"JUPUSDT": .04}, {}))
    monkeypatch.setattr(cache_script, "build_candidates",
        lambda **_kwargs: {"candidates": [piece, {"strategy": "usdt_quieto"}],
            "discarded": {}, "invalid_count": 0})
    monkeypatch.setattr(cache_script, "build_smart_candidates", lambda _rows: [])
    monkeypatch.setattr(cache_script, "rank_alternatives_v2",
        lambda rows, **_kwargs: [{"piece": row.get("piece", row),
            "objetivo": {"factible": True, "motivos": [], "clave_orden": [0, 0, 0]}}
            for row in rows])
    def evaluator(pieces, **_kwargs):
        if expected_status == "timeout":
            import time
            time.sleep(3)
        return {"pieces": [{"piece": row, "metrics": {"n": 1000,
            "pnl_mean_usdt": .1, "p_target": .2, "p_loss": .1,
            "p_loss_ci95": [.05, .15], "cut_slippage_mean_usdt": 0,
            "cut_slippage_p90_usdt": 0}} for row in pieces]}

    result = cache_script.run_cache(db.engine.url.database, market_path,
        library_dir=tmp_path, evaluator=evaluator, **limit_kwargs)

    run_id = result["runs"][0]["run_id"]
    assert result["runs"][0]["status"] == expected_status
    with sqlite3.connect(market_path) as conn:
        status, error, count = conn.execute("SELECT status,error,(SELECT COUNT(*) FROM "
            "scenario_eval_results WHERE run_id=scenario_eval_runs.run_id) "
            "FROM scenario_eval_runs WHERE run_id=?", (run_id,)).fetchone()
    assert status == expected_status
    assert count == (1 if expected_status == "memoria" else 0)
    details = json.loads(error)
    assert details["generated_count"] == 1
    assert details["evaluated_count"] == count


@pytest.mark.parametrize(("status", "exit_code"), [("memoria", 3), ("timeout", 4)])
def test_main_devuelve_codigo_de_salida_del_limite(monkeypatch, capsys, status, exit_code):
    monkeypatch.setattr(cache_script, "run_cache", lambda *_args, **_kwargs: {
        "contexts": 1, "runs": [{"status": status}], "seconds": 1.0, "rss_mb": 50.0})

    result = cache_script.main(["--app-db", "temporal.sqlite", "--market-db", "market.sqlite"])

    assert result == exit_code
    assert f"estado={status}" in capsys.readouterr().out
