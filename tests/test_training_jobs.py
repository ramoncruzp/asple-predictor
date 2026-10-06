import os
import shutil
import signal
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastapi.responses import JSONResponse
from fastapi import HTTPException

from api.auth import authorize
from api.routes.model_training import router
from database.db_manager import DBManager
from models.training_jobs import ActiveTrainingJob, TrainingJobService, artifact_trained_at

FAKE_TRAINING_SCRIPT = "import time; print('PROGRESS:descargando', flush=True); time.sleep(.35); print('PROGRESS:entrenando:model_b', flush=True); print('PROGRESS:guardando', flush=True)"


def _service(tmp_path, *, code=None, pid_alive=None, kill_tree=None):
    db_path = tmp_path / "jobs.sqlite"
    db = DBManager(f"sqlite:///{db_path}")
    script = code or FAKE_TRAINING_SCRIPT
    service = TrainingJobService(
        db, root=tmp_path,
        command_factory=lambda _job: [sys.executable, "-u", "-c", script],
        pid_alive=pid_alive, kill_tree=kill_tree,
    )
    return db, service


def _client(service, *, token="", auth=False):
    app = FastAPI()
    app.include_router(router, prefix="/api/models")
    app.state.training_job_service = service
    app.state.settings = SimpleNamespace(grid_api_token=token)
    if auth:
        @app.middleware("http")
        async def protect_training_routes(request, call_next):
            if request.url.path.startswith("/api/"):
                try:
                    authorize(request)
                except HTTPException as exc:
                    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
            return await call_next(request)
    return TestClient(app)


def _body(**values):
    result = {"symbol": "XRPUSDT", "interval": "1h", "models": ["b"], "days": 730, "confirm": True}
    result.update(values)
    return result


def test_existing_sqlite_database_gets_only_training_jobs_table_and_endpoints_work(tmp_path):
    path = tmp_path / "old.sqlite"
    first = DBManager(f"sqlite:///{path}")
    with first.engine.begin() as conn:
        conn.exec_driver_sql("DROP TABLE training_jobs")
    first.engine.dispose()
    db, service = _service_for_path(path, tmp_path)
    with db.engine.connect() as conn:
        columns = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info('training_jobs')")}
    assert columns == {
        "id", "symbol", "interval", "models", "days", "status", "phase", "created_at",
        "started_at", "finished_at", "pid", "exit_code", "log_path", "error", "confirm_reset_evaluation",
    }
    client = _client(service)
    response = client.post("/api/models/train", json=_body())
    assert response.status_code == 201
    job_id = response.json()["job"]["id"]
    assert client.get("/api/models/train/jobs?limit=5").status_code == 200
    assert client.get("/api/models/train/status").json()["job"]["id"] == job_id
    finished = service.wait_for_job(job_id)
    assert finished["status"] == "listo"
    assert finished["phase"] == "listo"
    assert "PROGRESS:entrenando:model_b" in Path(finished["log_path"]).read_text(encoding="utf-8")
    assert "--force" not in service._command(finished)
    db.engine.dispose()


def _service_for_path(path, root):
    db = DBManager(f"sqlite:///{path}")
    service = TrainingJobService(
        db, root=root,
        command_factory=lambda _job: [sys.executable, "-u", "-c", FAKE_TRAINING_SCRIPT],
    )
    return db, service


def test_two_post_requests_are_serialized_with_201_then_409(tmp_path):
    db, service = _service(tmp_path)
    client = _client(service)
    first = client.post("/api/models/train", json=_body())
    second = client.post("/api/models/train", json=_body())
    assert first.status_code == 201
    assert second.status_code == 409
    service.wait_for_job(first.json()["job"]["id"])
    db.engine.dispose()


def test_two_threads_cannot_create_two_active_jobs(tmp_path):
    db, service = _service(tmp_path)
    client = _client(service)
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: client.post("/api/models/train", json=_body()), range(2)))
    assert sorted(response.status_code for response in responses) == [201, 409]
    accepted = next(response for response in responses if response.status_code == 201)
    service.wait_for_job(accepted.json()["job"]["id"])
    db.engine.dispose()


@pytest.mark.parametrize("body", [
    _body(confirm=False),
    _body(symbol="BTCUSDT"),
    _body(interval="4h"),
    _body(models=[]),
    _body(models=["b", "b"]),
    _body(models=["d"]),
    _body(models=["a"]),
    _body(days=364),
    _body(days=731),
])
def test_training_request_rejects_unsupported_or_unconfirmed_inputs(tmp_path, body):
    db, service = _service(tmp_path)
    response = _client(service).post("/api/models/train", json=body)
    assert response.status_code == 422
    db.engine.dispose()


def test_model_a_requires_reinforced_confirmation_then_is_accepted(tmp_path):
    db, service = _service(tmp_path)
    client = _client(service)
    denied = client.post("/api/models/train", json=_body(models=["a"]))
    accepted = client.post("/api/models/train", json=_body(models=["a"], confirm_reset_evaluation=True))
    assert denied.status_code == 422
    assert accepted.status_code == 201
    service.wait_for_job(accepted.json()["job"]["id"])
    db.engine.dispose()


def test_recovery_marks_dead_pid_interrupted(tmp_path):
    db, service = _service(tmp_path, pid_alive=lambda _pid: False)
    table = db.training_jobs
    now = db._utc_now()
    with db.engine.begin() as conn:
        row_id = conn.execute(table.insert().values(
            symbol="XRPUSDT", interval="1h", models="b", days=730, status="running",
            phase="entrenando", created_at=now, started_at=now, pid=987654321,
        )).inserted_primary_key[0]
    assert service.recover_interrupted() == [row_id]
    assert service.get(row_id)["status"] == "interrumpido"
    db.engine.dispose()


def test_cancel_cleans_job_temps_and_leaves_existing_artifact_unchanged(tmp_path):
    saved = tmp_path / "models" / "saved"
    saved.mkdir(parents=True)
    artifact = saved / "model_b_xrp_1h.pt"
    artifact.write_bytes(b"old-artifact")
    temporary = saved / "model_b_xrp_1h.pt.tmp"
    code = (
        "from pathlib import Path; import time; "
        f"Path({str(temporary)!r}).write_bytes(b'partial'); "
        "print('PROGRESS:entrenando:model_b',flush=True); time.sleep(30)"
    )
    db, service = _service(tmp_path, code=code, kill_tree=lambda pid: os.kill(pid, signal.SIGTERM))
    response = _client(service).post("/api/models/train", json=_body())
    job = response.json()["job"]
    deadline = time.monotonic() + 3
    while not temporary.exists() and time.monotonic() < deadline:
        time.sleep(.02)
    assert temporary.exists()
    cancelled = _client(service).post(f"/api/models/train/{job['id']}/cancel")
    assert cancelled.status_code == 200
    assert cancelled.json()["job"]["status"] == "cancelado"
    assert not temporary.exists()
    assert artifact.read_bytes() == b"old-artifact"
    db.engine.dispose()


def test_pid_detection_method_is_declared():
    from models.training_jobs import IS_WINDOWS, PID_CHECK_METHOD
    assert PID_CHECK_METHOD == ("psutil.pid_exists + create_time" if IS_WINDOWS and __import__("models.training_jobs", fromlist=["_psutil"])._psutil is not None
                                else "Windows without psutil: always false" if IS_WINDOWS
                                else "os.kill(pid, 0) (POSIX)")


def _sleeping_child():
    return subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])


def test_pid_check_true_and_creation_time_guard_do_not_terminate_child(monkeypatch):
    from datetime import datetime, timedelta
    import models.training_jobs as jobs

    child = _sleeping_child()
    created = time.time()

    class FakeProcess:
        def create_time(self):
            return created

    class FakePsutil:
        class NoSuchProcess(Exception): pass
        class ZombieProcess(Exception): pass
        class AccessDenied(Exception): pass
        def pid_exists(self, pid):
            return pid == child.pid and child.poll() is None
        def Process(self, _pid):
            return FakeProcess()

    try:
        monkeypatch.setattr(jobs, "IS_WINDOWS", True)
        monkeypatch.setattr(jobs, "_psutil", FakePsutil())
        started_at = datetime.fromtimestamp(created, timezone.utc)
        assert jobs._is_pid_alive(child.pid) is True
        assert child.poll() is None
        assert jobs._is_pid_alive(child.pid, started_at) is True
        assert child.poll() is None
        assert jobs._is_pid_alive(child.pid, started_at - timedelta(seconds=60)) is False
        assert child.poll() is None
        assert jobs._is_pid_alive(9876543210, started_at) is False
    finally:
        if child.poll() is None:
            child.terminate()
            child.wait(timeout=5)


def test_windows_without_psutil_never_kills_child_and_warns(monkeypatch, caplog):
    import models.training_jobs as jobs

    child = _sleeping_child()
    try:
        monkeypatch.setattr(jobs, "IS_WINDOWS", True)
        monkeypatch.setattr(jobs, "_psutil", None)
        assert jobs._is_pid_alive(child.pid) is False
        assert child.poll() is None
        assert "sin psutil" in caplog.text
    finally:
        if child.poll() is None:
            child.terminate()
            child.wait(timeout=5)


def test_real_psutil_pid_check_keeps_child_alive_when_installed():
    try:
        import psutil
    except ImportError:
        pytest.skip("psutil está declarado en requirements.txt, pero no está instalado en este entorno")
    import models.training_jobs as jobs

    child = _sleeping_child()
    try:
        started = datetime.fromtimestamp(psutil.Process(child.pid).create_time(), timezone.utc)
        assert jobs._is_pid_alive(child.pid, started) is True
        assert child.poll() is None
    finally:
        if child.poll() is None:
            child.terminate()
            child.wait(timeout=5)


def test_nonexistent_pid_is_not_alive():
    from models.training_jobs import _is_pid_alive
    assert _is_pid_alive(9876543210) is False


def test_overflowing_posix_pid_is_reported_dead(monkeypatch):
    import models.training_jobs as training_jobs

    monkeypatch.setattr(training_jobs, "IS_WINDOWS", False)
    monkeypatch.setattr(training_jobs.os, "kill", lambda pid, sig: (_ for _ in ()).throw(OverflowError("pid out of range")))
    assert training_jobs._is_pid_alive(9876543210) is False


def test_training_endpoint_uses_real_authorize_middleware(tmp_path):
    db, service = _service(tmp_path)
    client = _client(service, token="configured-test-token", auth=True)
    denied = client.post("/api/models/train", json=_body())
    accepted = client.post("/api/models/train", headers={"X-API-Token": "configured-test-token"}, json=_body())
    assert denied.status_code == 403
    assert accepted.status_code == 201
    service.wait_for_job(accepted.json()["job"]["id"])
    db.engine.dispose()


def test_artifact_fallback_uses_model_specific_file_mtime(tmp_path):
    artifact = tmp_path / "models" / "saved" / "model_b_xrp_1h.pt"
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b"artifact")
    known = 1_790_000_000
    os.utime(artifact, (known, known))
    actual = artifact_trained_at({"trained_at": "old-run", "models": {"model_a": {"trained_at": "a-run"}}},
                                "model_b", root=tmp_path)
    expected = datetime.fromtimestamp(known, timezone.utc).isoformat()
    assert actual == expected
    assert artifact_trained_at({"trained_at": "old-run", "models": {"model_a": {"trained_at": "a-run"}}},
                               "model_c", root=tmp_path) is None
    assert artifact_trained_at({"trained_at": "old-run", "models": {"model_a": {"trained_at": "a-run"}}},
                               "model_a", root=tmp_path) == "a-run"


def test_naive_job_timestamps_are_utc_and_elapsed_is_local_timezone_safe(tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js no está instalado")
    db, service = _service(tmp_path)
    started = datetime(2026, 10, 6, 12, 0, 0)
    row = service._row({"started_at": started})
    assert row["started_at"].endswith("+00:00")
    env = dict(os.environ, TZ="America/Santo_Domingo", STARTED_AT=row["started_at"])
    harness = r"""const fs=require('fs'),vm=require('vm');global.window={APP:{}};global.document={addEventListener(){},getElementById(){return null}};vm.runInThisContext(fs.readFileSync('frontend/models.js','utf8'));const now=Date.parse('2026-10-06T12:01:30Z');process.stdout.write(String(window.ModelsPageTest.trainingElapsedSeconds(process.env.STARTED_AT,now)));"""
    result = subprocess.run([node, "-e", harness], cwd=Path(__file__).resolve().parents[1], env=env,
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert result.stdout == "90"
    db.engine.dispose()


def test_cancel_race_cannot_be_overwritten_by_monitor_error_twenty_times(tmp_path):
    db, service = _service(tmp_path, kill_tree=lambda _pid: setattr(fake_process, "returncode", 1))

    class FakeProcess:
        pid = 424242
        returncode = None

        def __init__(self):
            import threading
            self.started = threading.Event()

        def poll(self):
            self.started.set()
            return self.returncode

        def wait(self, timeout=None):
            return self.returncode

    for _ in range(20):
        fake_process = FakeProcess()
        table = db.training_jobs
        with db.engine.begin() as conn:
            job_id = conn.execute(table.insert().values(
                symbol="XRPUSDT", interval="1h", models="b", days=730, status="running",
                phase="entrenando", created_at=db._utc_now(), started_at=db._utc_now(),
                pid=fake_process.pid,
            )).inserted_primary_key[0]
        log_path = tmp_path / f"race-{job_id}.log"
        log_handle = log_path.open("wb")
        service._processes[job_id] = fake_process
        monitor = __import__("threading").Thread(target=service._monitor,
                args=(job_id, fake_process, log_handle, log_path), daemon=True)
        monitor.start()
        assert fake_process.started.wait(1)
        assert service.cancel(job_id)["status"] == "cancelado"
        monitor.join(timeout=2)
        assert not monitor.is_alive()
        assert service.get(job_id)["status"] == "cancelado"
    db.engine.dispose()


@pytest.mark.skipif(os.name == "nt", reason="Cubre la implementación de grupo POSIX; Windows se verifica manualmente")
def test_real_posix_kill_tree_stops_parent_and_child(tmp_path):
    try:
        import psutil
    except ImportError:
        pytest.skip("psutil no está instalado para comprobar procesos descendientes")

    code = "import subprocess,sys,time; subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); time.sleep(60)"
    parent = subprocess.Popen([sys.executable, "-c", code], start_new_session=True)
    processes = [psutil.Process(parent.pid)]
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        try:
            children = psutil.Process(parent.pid).children(recursive=True)
        except psutil.NoSuchProcess:
            children = []
        if children:
            processes.extend(children)
            break
        time.sleep(.02)
    assert len(processes) == 2
    assert TrainingJobService._kill_tree(parent.pid)
    gone, alive = psutil.wait_procs(processes, timeout=5)
    parent.wait(timeout=5)
    assert len(gone) == 2 and not alive


def test_recovery_reattaches_live_pid_and_cancellation_works(tmp_path):
    state = {"alive": True}
    db, service = _service(tmp_path, pid_alive=lambda _pid: state["alive"],
                           kill_tree=lambda _pid: state.update(alive=False))
    table = db.training_jobs
    log_path = tmp_path / "logs" / "training" / "job_orphan.log"
    log_path.parent.mkdir(parents=True)
    log_path.write_text("PROGRESS:entrenando:model_b\nMétricas guardadas: manifest.json\n", encoding="utf-8")
    with db.engine.begin() as conn:
        job_id = conn.execute(table.insert().values(
            symbol="XRPUSDT", interval="1h", models="b", days=730, status="running",
            phase="entrenando", created_at=db._utc_now(), started_at=db._utc_now(), pid=98765,
            log_path=str(log_path),
        )).inserted_primary_key[0]
    assert service.recover_interrupted() == []
    assert service.get(job_id)["status"] == "running"
    state["alive"] = False
    finished = service.wait_for_job(job_id, timeout=2)
    assert finished["status"] == "listo"

    state["alive"] = True
    with db.engine.begin() as conn:
        cancel_id = conn.execute(table.insert().values(
            symbol="XRPUSDT", interval="1h", models="b", days=730, status="running",
            phase="entrenando", created_at=db._utc_now(), started_at=db._utc_now(), pid=98766,
            log_path=str(log_path),
        )).inserted_primary_key[0]
    service.recover_interrupted()
    cancelled = service.cancel(cancel_id)
    assert cancelled["status"] == "cancelado"
    time.sleep(.15)
    assert service.get(cancel_id)["status"] == "cancelado"
    db.engine.dispose()
