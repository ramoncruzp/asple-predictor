from __future__ import annotations

import threading
import time
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routes import intelligence
from database.db_manager import DBManager
from grid.intelligence_snapshot import IntelligenceSnapshotService


def test_intelligence_serves_a_persisted_snapshot_while_refresh_runs(tmp_path):
    db = DBManager(f"sqlite:///{tmp_path / 'snapshot.sqlite'}")
    started, release = threading.Event(), threading.Event()
    calls = []

    def build():
        calls.append(1)
        started.set()
        release.wait(2)
        return {"updated_at": "2026-10-29T00:00:00+00:00", "timings_ms": {},
            "readiness_switches": {}, "grid_intelligence_readiness": {},
            "grid_intelligence": {"grids": []}, "regressions": {"profiles": {}}}

    service = IntelligenceSnapshotService(db, build)
    settings = SimpleNamespace(grid_api_token="snapshot-token")
    app = FastAPI()
    app.include_router(intelligence.router, prefix="/api/intelligence")
    app.state.db, app.state.settings, app.state.intelligence_snapshot = db, settings, service
    client = TestClient(app)

    before = time.perf_counter()
    first = client.get("/api/intelligence", headers={"X-API-Token": "snapshot-token"})
    assert time.perf_counter() - before < 2
    assert first.status_code == 200 and first.json()["status"] == "calculando"
    assert started.wait(1)
    assert service.request_refresh() == {"status": "calculando", "shared": True}
    release.set()
    assert service.wait(2)
    assert len(calls) == 1

    restored = IntelligenceSnapshotService(db, lambda: (_ for _ in ()).throw(AssertionError("no recalcular")))
    app.state.intelligence_snapshot = restored
    before = time.perf_counter()
    response = client.get("/api/intelligence", headers={"X-API-Token": "snapshot-token"})
    assert time.perf_counter() - before < 2
    assert response.status_code == 200
    assert response.json()["status"] == "ready"
    assert response.json()["computed_at"]
    assert restored.snapshot["grid_intelligence"] == {"grids": []}


