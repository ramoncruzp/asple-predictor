from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routes import grids as grids_api
from database.db_manager import DBManager


def test_level_adjust_summary_counts_sources_omissions_and_marks_pnl_descriptive(monkeypatch):
    now = datetime(2026, 10, 8, tzinfo=timezone.utc)
    grid = {"id": 7, "symbol": "ADAUSDT", "strategy": "smart", "status": "ACTIVE",
            "n_levels": 5, "params": {"adjust_idle_shrink": True}}
    events = [
        {"event_type": "GRID_ADJUSTED", "ts": now, "details": {"source": "IDLE_SHRINK",
            "old": {"n": 8}, "new": {"n": 6}}},
        {"event_type": "GRID_ADJUSTED", "ts": now, "details": {"source": "CAPITAL_SHRINK",
            "old": {"n": 8}, "new": {"n": 7}}},
        {"event_type": "ADJUST_BLOCKED", "reason": "capital_per_cell_below_min_notional_margin",
            "details": {}},
        {"event_type": "IDLE_SHRINK_EVAL", "details": {"omission_reason": "idle_frac_min"}},
    ]
    snapshots = [
        {"ts": now - timedelta(hours=24), "pnl_realized": 3, "unrealized_pnl": 2},
        {"ts": now + timedelta(hours=24), "pnl_realized": 4, "unrealized_pnl": 2},
        {"ts": now - timedelta(hours=72), "pnl_realized": 1, "unrealized_pnl": 0},
        {"ts": now + timedelta(hours=72), "pnl_realized": 6, "unrealized_pnl": 0},
    ]
    class DB:
        def list_grids_by_status(self, _statuses): return [grid]
        def list_grid_events(self, *, grid_id=None, limit=10000):
            return list(reversed(events)) if event_type_order(events) else events
        def list_grid_snapshots(self, *, grid_id, limit): return snapshots
    def event_type_order(rows): return bool(rows)
    monkeypatch.setattr(grids_api, "_authorize", lambda _request: None)
    monkeypatch.setattr(grids_api, "_paired_loan_summary", lambda db, factor: {
        "factor": factor, "n_pairs": 0, "ci_low": None, "ci_high": None, "pairs": [],
        "excluded_pairs": [], "treated_pairs": 0})
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(
        db=DB(), settings=SimpleNamespace(adjust_idle_shrink_enabled=False))))
    result = grids_api.level_adjust_summary(request)
    row = result["grids"][0]
    assert result["idle_shrink_enabled"] is False
    assert row["adjustments"]["IDLE_SHRINK"] == 1
    assert row["adjustments"]["CAPITAL_SHRINK"] == 1
    assert row["adjust_blocked"]["capital_per_cell_below_min_notional_margin"] == 1
    assert row["idle_omissions"] == {"idle_frac_min": 1}
    assert row["idle_blocked_reason"] == "interruptor_global_apagado"
    assert row["pnl_after_adjustments"][0]["label"] == "descriptivo, no causal"
    assert row["pnl_after_adjustments"][0]["pnl_24h_before"] == 5
    assert row["pnl_after_adjustments"][0]["pnl_24h_after"] == 6
    assert result["verdicts"]["idle_shrink"]["verdict"] == "en prueba"


def test_adjust_verdict_requires_twenty_pairs_and_uses_interval_sign():
    assert grids_api._adjust_verdict({"n_pairs": 19, "ci_low": 1, "ci_high": 2})["verdict"] == "en prueba"
    assert grids_api._adjust_verdict({"n_pairs": 20, "ci_low": -2, "ci_high": 0})["verdict"] == "apagar definitivamente"
    assert grids_api._adjust_verdict({"n_pairs": 20, "ci_low": 0.1, "ci_high": 2})["verdict"].startswith("candidato")


def test_level_adjust_summary_route_is_read_only_and_returns_default_switch(tmp_path):
    api = FastAPI()
    api.include_router(grids_api.router, prefix="/api/grids")
    api.state.db = DBManager(f"sqlite:///{tmp_path / 'level-adjust.db'}")
    api.state.settings = SimpleNamespace(grid_api_token="local-test", adjust_idle_shrink_enabled=False)
    response = TestClient(api).get("/api/grids/level-adjust/summary",
        headers={"X-API-Token": "local-test"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["idle_shrink_enabled"] is False
    assert payload["verdicts"]["idle_shrink"]["verdict"] == "en prueba"
