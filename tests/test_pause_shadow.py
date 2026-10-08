from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routes.grids import router
from database.db_manager import DBManager
from grid.monitor import GridMonitor


class _ShadowDB:
    def __init__(self, fail=False):
        self.rows = []
        self.fail = fail

    def add_pause_shadow_observation(self, values):
        if self.fail:
            raise RuntimeError("storage unavailable")
        self.rows.append(values)


def _monitor(db):
    return GridMonitor(db, object(), object(), SimpleNamespace(grid_monitor_interval=900))


def test_shadow_record_keeps_real_pause_and_adjust_actions_unchanged():
    db = _ShadowDB()
    monitor = _monitor(db)
    grid = {"id": 1, "symbol": "XRPUSDT", "range_low": 99.0, "range_high": 101.0, "params": {}}
    for action, expected in (("PAUSE", 1), ("ADJUST", 0)):
        decision = SimpleNamespace(action=action, reasons=("test",))
        before = (decision.action, decision.reasons)
        monitor._record_pause_shadow(grid, 7, datetime.now(timezone.utc), 100.0, 0.3, 0.3, decision.action)
        assert (decision.action, decision.reasons) == before
        assert db.rows[-1]["paused_actual"] == expected
        assert db.rows[-1]["would_pause_24h"] == 1
        assert db.rows[-1]["break_prob_4h"] is not None


def test_shadow_write_failure_is_logged_and_does_not_raise(caplog):
    db = _ShadowDB(fail=True)
    monitor = _monitor(db)
    decision = SimpleNamespace(action="PAUSE", reasons=("test",))
    monitor._record_pause_shadow(
        {"id": 4, "symbol": "XRPUSDT", "range_low": 99, "range_high": 101, "params": {}},
        1, datetime.now(timezone.utc), 100, 0.3, 0.3, decision.action,
    )
    assert decision.action == "PAUSE"
    assert "pause-shadow observation failed" in caplog.text
    assert "storage unavailable" in caplog.text


def _observation(db, grid, ts, price, actual, shadow):
    db.add_pause_shadow_observation({
        "run_id": 1, "grid_id": grid, "ts": ts, "symbol": "XRPUSDT",
        "break_prob_4h": 0.2, "would_pause_4h": 1,
        "break_prob_24h": 0.3, "would_pause_24h": shadow,
        "paused_actual": actual, "price": price, "range_low": 99.0, "range_high": 101.0,
    })


def test_summary_excludes_run_gap_windows_and_endpoint_is_read_only(tmp_path):
    path = tmp_path / "shadow.sqlite"
    db = DBManager(f"sqlite:///{path}")
    origin = datetime(2026, 1, 1, tzinfo=timezone.utc)
    _observation(db, 1, origin, 100, 0, 0)
    _observation(db, 1, origin + timedelta(hours=4), 104, 1, 1)
    _observation(db, 1, origin + timedelta(hours=24), 104, 1, 1)
    db.add_grid_event(run_id=2, source="MONITOR", event_type="RUN_GAP", ts=origin + timedelta(hours=2))

    clean_origin = origin + timedelta(days=2)
    _observation(db, 2, clean_origin, 100, 0, 0)
    _observation(db, 2, clean_origin + timedelta(hours=4), 103, 0, 0)
    _observation(db, 2, clean_origin + timedelta(hours=24), 103, 0, 0)
    app = FastAPI()
    app.include_router(router, prefix="/api/grids")
    app.state.db = db
    app.state.settings = SimpleNamespace(grid_api_token="test-token")
    with TestClient(app) as client:
        response = client.get("/api/grids/pause-shadow/summary", headers={"X-API-Token": "test-token"})
    assert response.status_code == 200
    data = response.json()
    assert data["4h"]["excluded_run_gap_windows"] == 1
    assert data["4h"]["actual_paused"]["not_paused"]["n"] == 1
    assert data["4h"]["actual_paused"]["not_paused"]["exits_over_1_9_pct"] == 1
    assert data["4h"]["reason"] == "muestra insuficiente"
    assert "no emite veredicto" in data["24h"]["criterion"]
    db.engine.dispose()


def test_pause_shadow_schema_is_additive_and_nullable_idempotently(tmp_path):
    path = tmp_path / "shadow-migration.sqlite"
    first = DBManager(f"sqlite:///{path}")
    with first.engine.connect() as conn:
        columns = conn.exec_driver_sql("PRAGMA table_info('pause_shadow_observations')").fetchall()
    first.engine.dispose()
    second = DBManager(f"sqlite:///{path}")
    with second.engine.connect() as conn:
        again = conn.exec_driver_sql("PRAGMA table_info('pause_shadow_observations')").fetchall()
    assert [row[1] for row in columns] == [row[1] for row in again]
    assert all(row[3] == 0 for row in columns if row[1] != "id")
    second.engine.dispose()
