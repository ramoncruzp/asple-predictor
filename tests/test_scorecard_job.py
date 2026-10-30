import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from api.routes import metas
from database.db_manager import DBManager
from grid.meta_operator import MetaOperator
from scheduler.scorecard_job import ScorecardJob


class SchedulerFake:
    def __init__(self):
        self.job = None
        self.running = False

    def add_job(self, fn, trigger, **kwargs):
        self.job = (fn, trigger, kwargs)

    def start(self):
        self.running = True

    def shutdown(self, wait=True):
        self.running = False


def setup(tmp_path):
    db = DBManager(f"sqlite:///{tmp_path / 'meta-scorecard.sqlite'}")
    settings = SimpleNamespace(grid_api_token="scorecard-token")
    return db, MetaOperator(None, db, None, None, settings)


def test_weekly_job_default_off_checks_setting_and_registers_schedule(tmp_path):
    db, operator = setup(tmp_path)
    assert operator.scorecard_weekly_status()["enabled"] is False
    calls, scheduler = [], SchedulerFake()
    job = ScorecardJob(db, tmp_path / "market_data.db", scheduler=scheduler,
        calculate=lambda *a, **kw: calls.append((a, kw)) or {"status": "completed"},
        write_report=lambda *_: None)
    assert job.run_if_enabled()["status"] == "apagado"
    assert not calls
    job.start()
    fn, trigger, options = scheduler.job
    assert trigger == "cron" and options["day_of_week"] == "sun"
    assert (options["hour"], options["minute"], options["max_instances"], options["coalesce"]) == (6, 20, 1, True)
    job.stop()


def test_weekly_job_enabled_calculates_and_switch_event_is_audited(tmp_path):
    db, operator = setup(tmp_path)
    calls, reports = [], []
    job = ScorecardJob(db, tmp_path / "synthetic-market.db",
        calculate=lambda *args, **kwargs: calls.append((args, kwargs)) or {"run_id": 5},
        write_report=lambda result, path: reports.append((result, path)))
    with pytest.raises(ValueError, match="Confirma el cambio"):
        operator.set_scorecard_weekly_enabled(True, confirm=False, actor="Ramón")
    operator.set_scorecard_weekly_enabled(True, confirm=True, actor="Ramón")
    status = operator.scorecard_weekly_status()
    assert status["enabled"] is True and status["actor"] == "Ramón"
    result = job.run_if_enabled()
    assert result["status"] == "completed" and calls and reports
    with db.engine.connect() as conn:
        events = conn.execute(select(db.scorecard_switch_events)).mappings().all()
        raw = conn.execute(select(db.meta_profile_settings.c.settings).where(
            db.meta_profile_settings.c.profile == "GLOBAL")).scalar_one()
    assert len(events) == 1 and events[0]["enabled"] and events[0]["actor"] == "Ramón"
    assert json.loads(raw)["scorecard_weekly_enabled"] is True


def test_weekly_switch_api_requires_token_and_confirmation(tmp_path):
    db, operator = setup(tmp_path)
    app = FastAPI()
    app.include_router(metas.router, prefix="/api/metas")
    app.state.settings = SimpleNamespace(grid_api_token="scorecard-token")
    app.state.meta_operator = operator
    client = TestClient(app)
    assert client.get("/api/metas/scorecard-weekly").status_code == 403
    headers = {"X-API-Token": "scorecard-token"}
    assert client.get("/api/metas/scorecard-weekly", headers=headers).json()["enabled"] is False
    denied = client.put("/api/metas/scorecard-weekly/switch", headers=headers,
        json={"enabled": True, "confirm": False, "actor": "Prueba"})
    assert denied.status_code == 409
    assert client.put("/api/metas/scorecard-weekly/switch", headers=headers,
        json={"enabled": True, "confirm": True, "actor": "Prueba"}).json()["enabled"] is True
