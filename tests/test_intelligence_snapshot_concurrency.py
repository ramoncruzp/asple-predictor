from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routes import intelligence
from database.db_manager import DBManager
from grid.intelligence_snapshot import IntelligenceSnapshotService
from grid.meta_operator import MetaOperator

def test_simultaneous_api_reads_share_one_refresh_and_recalculate_returns_202(tmp_path):
    db = DBManager(f"sqlite:///{tmp_path / 'snapshot-single-flight.sqlite'}")
    started, release = threading.Event(), threading.Event()
    calls = []

    def build():
        calls.append(1)
        started.set()
        release.wait(3)
        return {"timings_ms": {}, "grid_intelligence": {"grids": []}}

    service = IntelligenceSnapshotService(db, build)
    settings = SimpleNamespace(grid_api_token="snapshot-token")
    app = FastAPI()
    app.include_router(intelligence.router, prefix="/api/intelligence")
    app.state.db, app.state.settings, app.state.intelligence_snapshot = db, settings, service
    headers = {"X-API-Token": "snapshot-token"}

    def get_summary(_):
        with TestClient(app) as client:
            return client.get("/api/intelligence", headers=headers).json()["status"]

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(get_summary, range(2)))
        assert results == ["calculando", "calculando"]
        assert started.wait(1)
        with TestClient(app) as client:
            response = client.post("/api/intelligence/recalculate", headers=headers)
        assert response.status_code == 202
        assert response.json()["shared"] is True
        assert len(calls) == 1
    finally:
        release.set()
        assert service.wait(3)



def test_intelligence_snapshot_matches_direct_calculation(tmp_path):
    db = DBManager(f"sqlite:///{tmp_path / 'intelligence-parity.sqlite'}")
    settings = SimpleNamespace(grid_api_token='parity-secret', meta_mode_enabled=False)
    operator = MetaOperator(SimpleNamespace(), db, None, None, settings)
    app = FastAPI()
    app.include_router(intelligence.router, prefix='/api/intelligence')
    app.state.settings, app.state.db, app.state.meta_operator = settings, db, operator
    client = TestClient(app)
    headers = {'X-API-Token': 'parity-secret'}
    direct = client.get('/api/intelligence?page=1&page_size=50', headers=headers).json()
    service = IntelligenceSnapshotService(db, lambda: direct)
    app.state.intelligence_snapshot = service
    service.request_refresh()
    assert service.wait(3)
    cached = client.get('/api/intelligence', headers=headers).json()
    assert cached['status'] == 'ready'
    assert cached['refresh_status'] == 'listo'
    assert cached['computed_at']
    for key, value in direct.items():
        if key == 'timings_ms':
            assert {name: duration for name, duration in cached[key].items() if name != 'snapshot_total'} == value
        else:
            assert cached[key] == value
