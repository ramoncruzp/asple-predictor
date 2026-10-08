import json
import multiprocessing
import sqlite3
import time
from types import SimpleNamespace

from grid.loan_cohorts import create_grid_with_loan_cohort


class _SQLiteDB:
    def __init__(self, path):
        self.path = str(path)
        self.engine = SimpleNamespace(url=f"sqlite:///{self.path}")

    def list_grids_by_status(self, statuses):
        if "ACTIVE" not in statuses:
            return []
        with sqlite3.connect(self.path, timeout=10) as conn:
            rows = conn.execute("SELECT strategy, params FROM grids WHERE status = 'ACTIVE'").fetchall()
        return [{"strategy": strategy, "status": "ACTIVE", "params": json.loads(params)}
                for strategy, params in rows]


def _open_in_process(path, index, barrier, results):
    db = _SQLiteDB(path)
    barrier.wait(timeout=20)
    def create(params):
        time.sleep(0.08)
        with sqlite3.connect(path, timeout=10) as conn:
            conn.execute("INSERT INTO grids(status, strategy, params) VALUES('ACTIVE', 'smart', ?)",
                         (json.dumps(params),))
        return params["loans_group"]
    from grid.loan_cohorts import _lock_file_path
    _effective, group = create_grid_with_loan_cohort(db, "smart", {}, 3, create)
    results.put((index, group, str(_lock_file_path(db))))


def test_eight_processes_share_control_every_third_creation(tmp_path):
    path = tmp_path / "cohorts.sqlite"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE grids(status TEXT, strategy TEXT, params TEXT)")
    ctx = multiprocessing.get_context("spawn")
    barrier, results = ctx.Barrier(8), ctx.Queue()
    workers = [ctx.Process(target=_open_in_process, args=(str(path), index, barrier, results))
               for index in range(8)]
    for worker in workers: worker.start()
    for worker in workers:
        worker.join(30)
        assert worker.exitcode == 0
    completed = [results.get(timeout=2) for _ in workers]
    groups = [item[1] for item in completed]
    assert len({item[2] for item in completed}) == 1
    assert groups.count("control") == 2
    assert groups.count("loans_v2") == 6


def test_creation_exception_releases_cohort_lock():
    class DB:
        engine = SimpleNamespace(url="sqlite:///lock-release-test")
        def list_grids_by_status(self, _statuses): return []
    db = DB()
    try:
        create_grid_with_loan_cohort(db, "smart", {}, 3, lambda _params: (_ for _ in ()).throw(RuntimeError("stop")))
    except RuntimeError as exc:
        assert str(exc) == "stop"
    else:
        raise AssertionError("creation exception did not propagate")
    effective, result = create_grid_with_loan_cohort(db, "smart", {}, 3, lambda params: params["loans_group"])
    assert effective["loans_group"] == result == "loans_v2"


def test_process_lock_timeout_does_not_create_grid(tmp_path, monkeypatch):
    import grid.loan_cohorts as cohorts
    assert cohorts._PROCESS_LOCK_TIMEOUT_SECONDS >= 120.0
    class DB:
        engine = SimpleNamespace(url=f"sqlite:///{tmp_path / 'timeout.sqlite'}")
        def list_grids_by_status(self, _statuses): return []
    monkeypatch.setattr(cohorts, "_PROCESS_LOCK_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(cohorts, "_PROCESS_LOCK_RETRY_SECONDS", 0.001)
    monkeypatch.setattr(cohorts, "_try_os_lock", lambda _handle: (_ for _ in ()).throw(BlockingIOError("busy")))
    created = []
    try:
        cohorts.create_grid_with_loan_cohort(DB(), "smart", {}, 3, lambda params: created.append(params))
    except TimeoutError as exc:
        assert "candado de cohortes" in str(exc)
    else:
        raise AssertionError("lock timeout did not surface")
    assert created == []


def test_lock_file_open_failure_warns_and_falls_back(tmp_path, monkeypatch, caplog):
    import grid.loan_cohorts as cohorts
    class DB:
        engine = SimpleNamespace(url=f"sqlite:///{tmp_path / 'fallback.sqlite'}")
        def list_grids_by_status(self, _statuses): return []
    def fail_open(_path): raise OSError("no temp permission")
    monkeypatch.setattr(cohorts, "_open_lock_file", fail_open)
    with caplog.at_level("WARNING", logger="grid.loan_cohorts"):
        effective, result = cohorts.create_grid_with_loan_cohort(
            DB(), "smart", {}, 3, lambda params: params["loans_group"])
    assert effective["loans_group"] == result == "loans_v2"
    assert "candado de cohortes entre procesos" in caplog.text
