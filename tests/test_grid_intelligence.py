from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import numpy as np
import pandas as pd
import pytest

from sqlalchemy import select
from fastapi import FastAPI
from fastapi.testclient import TestClient

from database.db_manager import DBManager
from grid.grid_intelligence import GridIntelligenceJob, recommend_grid_action, retained_inventory_metrics
from api.routes.intelligence import _grid_intelligence_readiness_alerts
from api.routes import grids as grids_api
from grid.sim.runner import FILTERS


class FakeScheduler:
    running = False
    jobs = []

    def add_job(self, func, trigger, **kwargs):
        self.jobs.append((func, trigger, kwargs))

    def start(self):
        self.running = True


def test_hourly_shadow_job_has_its_own_schedule_and_lock(tmp_path):
    db = DBManager(f"sqlite:///{tmp_path / 'grid-intel.sqlite'}")
    scheduler = FakeScheduler()
    job = GridIntelligenceJob(db, None, None, scheduler=scheduler)
    job.start()
    shadow = next(job for job in scheduler.jobs if job[2]["id"] == "grid_intelligence_shadow")
    labels = next(job for job in scheduler.jobs if job[2]["id"] == "grid_intelligence_labels")
    assert shadow[1] == "cron"
    assert shadow[2]["minute"] == 7
    assert shadow[2]["max_instances"] == 1
    assert labels[2]["hour"] == 9
    assert job._grid_intel_guard.acquire(blocking=False)
    try:
        assert job.run_once() == {"status": "skipped", "reason": "ejecución horaria anterior activa"}
    finally:
        job._grid_intel_guard.release()


def test_shadow_recommendation_requires_gain_and_drawdown_gates():
    rows = [
        {"action": "mantener", "expected_gain_7d": 0.4, "drawdown": 4.0},
        {"action": "mover", "expected_gain_7d": 0.69, "drawdown": 4.5},
        {"action": "n_plus_one", "expected_gain_7d": 0.8, "drawdown": 5.1},
    ]
    selected, _ = recommend_grid_action(rows, 100)
    assert selected["action"] == "mantener"  # 0.29 USDT is below the 0.30 USDT gate.
    rows[1]["expected_gain_7d"] = .71
    selected, _ = recommend_grid_action(rows, 100)
    assert selected["action"] == "mover"


def test_retained_inventory_is_valued_at_bid_net_fee_for_gain_and_drawdown():
    result = retained_inventory_metrics([0, 300], [10, 8],
        [{"timestamp": 0}, {"timestamp": 300}], [80, 80],
        [{"held_qty": 2, "entry_cost": 20, "entry_fee_usdt": 0}], 80)
    assert result["gain"] == pytest.approx(80 + 2 * 8 * .999 - 100)
    assert result["drawdown"] == pytest.approx((100 - (80 + 2 * 8 * .999)) / 100 * 100)


def test_synthetic_shadow_evaluates_candidates_without_mutating_grid():
    class DB:
        def get_grid_levels(self, _grid_id):
            return [{"level_idx": idx, "capital": 25, "capital_base": 25, "held_qty": 0,
                "price": 90 + idx * 5, "sell_price": 95 + idx * 5,
                "state": "IDLE", "order_id": None} for idx in range(4)]

    class Market:
        def get_historical_klines(self, symbol, interval, lookback):
            stamps = pd.date_range("2026-01-01", periods=7 * 24 * 12 + 1, freq="5min", tz="UTC")
            price = np.full(len(stamps), 100.0)
            return pd.DataFrame({"timestamp": stamps, "open": price, "high": price + .5,
                "low": price - .5, "close": price})

    now = datetime(2026, 1, 8, tzinfo=timezone.utc)
    job = GridIntelligenceJob(DB(), Market(), lambda _symbol: FILTERS, clock=lambda: now)
    grid = {"id": 1, "symbol": "XRPUSDT", "strategy": "simple", "status": "ACTIVE",
        "range_low": 90, "range_high": 110, "n_levels": 4, "capital_total": 100, "params": {}}
    summary = {"timings_s": {"candles": 0, "advisor": 0, "replay": 0, "persistence": 0},
        "candidates_evaluated": 0, "cache_hits": 0}
    result = job._evaluate_grid(grid, __import__("time").perf_counter(), summary)
    assert result is not None
    assert any(row["action"] == "mantener" and row.get("expected_gain_7d") is not None
        for row in result["candidates"])
    assert any(row["action"].startswith("agregar_bot_") for row in result["candidates"])
    assert result["signals"]["simulation_scope"].startswith("curva total:")
    assert summary["candidates_evaluated"] > 0


def test_disabled_grid_intelligence_switch_never_calls_apply_callback(tmp_path):
    db = DBManager(f"sqlite:///{tmp_path / 'grid-intel-disabled.sqlite'}")
    now = datetime.now(timezone.utc)
    with db.engine.begin() as conn:
        grid_id = conn.execute(db.grids.insert().values(symbol="XRPUSDT", range_low=.4,
            range_high=.6, n_levels=4, capital_total=100, reserve=0, status="ACTIVE",
            created_at=now, environment="testnet", strategy="simple", params="{}")).inserted_primary_key[0]
    calls = []
    job = GridIntelligenceJob(db, None, None, clock=lambda: now,
        apply_callback=lambda *args: calls.append(args) or {"ok": True})
    result = job._apply_if_eligible(db.get_grid(grid_id), {"action": "cambiar_n_5",
        "candidate": {"geometry": {"low": .4, "high": .6, "n": 5}}})
    assert result is None
    assert calls == []
    db.engine.dispose()


def test_grid_readiness_alert_snapshot_uses_grid_and_action_key():
    snapshot = _grid_intelligence_readiness_alerts({"grids": [{"grid_id": 7,
        "mature_labels": {"readiness_by_action": {"cambiar_n_5": {"state": "Listo para encender"}}}}]})
    assert snapshot == {"grid_7_cambiar_n_5": {"state": "Listo para encender"}}


def test_recommendations_are_persisted_as_shadow_only(tmp_path):
    db = DBManager(f"sqlite:///{tmp_path / 'grid-intel-persist.sqlite'}")
    now = datetime.now(timezone.utc)
    with db.engine.begin() as conn:
        grid_id = conn.execute(db.grids.insert().values(symbol="XRPUSDT", range_low=.4,
            range_high=.6, n_levels=4, capital_total=100, reserve=0, status="ACTIVE",
            created_at=now, environment="testnet", strategy="simple", params="{}")).inserted_primary_key[0]
    job = GridIntelligenceJob(db, None, None, clock=lambda: now)
    job._persist({"id": grid_id, "symbol": "XRPUSDT", "strategy": "simple"}, {
        "action": "mantener", "candidate": {"action": "mantener", "expected_gain_7d": .2,
            "drawdown": 1.0}, "baseline": {"action": "mantener", "expected_gain_7d": .2,
            "drawdown": 1.0}, "candidates": [], "reason": "sin alternativa material",
        "signals": {"free_cells": 4}})
    with db.engine.connect() as conn:
        row = conn.execute(select(db.grid_intel_recommendations)).mappings().one()
    assert row["would_apply"] is False
    assert row["action"] == "mantener"
    assert row["method_version"] == "grid_shadow_42a_v1"
    db.engine.dispose()


def test_apply_gate_needs_two_identical_hourly_recommendations(tmp_path):
    db = DBManager(f"sqlite:///{tmp_path / 'grid-intel-gate.sqlite'}")
    now = datetime.now(timezone.utc)
    with db.engine.begin() as conn:
        grid_id = conn.execute(db.grids.insert().values(symbol="XRPUSDT", range_low=.4,
            range_high=.6, n_levels=4, capital_total=100, reserve=0, status="ACTIVE",
            created_at=now, environment="testnet", strategy="simple",
            params='{"intel_apply_enabled":true}')).inserted_primary_key[0]
        for age in (30, 90):
            rec_id = conn.execute(db.grid_intel_recommendations.insert().values(grid_id=grid_id,
                symbol="XRPUSDT", strategy="simple", created_at=now - timedelta(minutes=age),
                action="cambiar_n_5", candidate_json="{}", baseline_json="{}",
                signals_json="{}", method_version="test", would_apply=False)).inserted_primary_key[0]
            conn.execute(db.grid_intel_labels.insert().values(recommendation_id=rec_id,
                labeled_at=now, status="mature", label_json='{"gain_difference_usdt":1}'))
        for index in range(28):
            rec_id = conn.execute(db.grid_intel_recommendations.insert().values(grid_id=grid_id,
                symbol="XRPUSDT", strategy="simple", created_at=now - timedelta(days=8, minutes=index),
                action="cambiar_n_5", candidate_json="{}", baseline_json="{}",
                signals_json="{}", method_version="test", would_apply=False)).inserted_primary_key[0]
            conn.execute(db.grid_intel_labels.insert().values(recommendation_id=rec_id,
                labeled_at=now, status="mature", label_json='{"gain_difference_usdt":1}'))
    called = []
    job = GridIntelligenceJob(db, None, None, clock=lambda: now,
        apply_callback=lambda grid, action, candidate: called.append(action) or {"ok": True},
        valuation_checker=lambda grid: True)
    result = job._apply_if_eligible(db.get_grid(grid_id), {"action": "cambiar_n_5",
        "candidate": {"geometry": {"low": .4, "high": .6, "n": 5}}})
    assert result["status"] == "applied"
    assert called == ["cambiar_n_5"]
    assert db.list_grid_events(grid_id=grid_id, limit=10)[0]["event_type"] == "GRID_INTEL_APPLIED"
    db.engine.dispose()


def test_switch_requires_actor_and_mature_positive_evidence(tmp_path):
    db = DBManager(f"sqlite:///{tmp_path / 'grid-intel-switch.sqlite'}")
    now = datetime.now(timezone.utc)
    with db.engine.begin() as conn:
        grid_id = conn.execute(db.grids.insert().values(symbol="XRPUSDT", range_low=.4,
            range_high=.6, n_levels=4, capital_total=100, reserve=0, status="ACTIVE",
            created_at=now, environment="testnet", strategy="simple", params="{}")).inserted_primary_key[0]
    app = FastAPI()
    app.include_router(grids_api.router, prefix="/api/grids")
    app.state.settings, app.state.db = SimpleNamespace(grid_api_token="test-token"), db
    client = TestClient(app)
    headers = {"X-API-Token": "test-token"}
    assert client.put(f"/api/grids/{grid_id}/intelligence-switch",
        json={"enabled": True, "actor": "Ramón", "confirm": True}, headers=headers).status_code == 409
    response = client.put(f"/api/grids/{grid_id}/intelligence-switch",
        json={"enabled": False, "actor": "Ramón", "confirm": True}, headers=headers)
    assert response.status_code == 200
    assert response.json()["intel_apply_enabled"] is False
    assert db.get_grid(grid_id)["params"]["intel_apply_actor"] == "Ramón"
    db.engine.dispose()


def test_seven_day_forward_label_compares_maintain_and_recommended_branch(tmp_path):
    db = DBManager(f"sqlite:///{tmp_path / 'grid-intel-label.sqlite'}")
    now = datetime.now(timezone.utc)
    created = now - timedelta(days=8)
    with db.engine.begin() as conn:
        grid_id = conn.execute(db.grids.insert().values(symbol="XRPUSDT", range_low=.9,
            range_high=1.1, n_levels=4, capital_total=100, reserve=0, status="ACTIVE",
            created_at=created, environment="testnet", strategy="simple", params="{}")).inserted_primary_key[0]
        geometry = {"low": .9, "high": 1.1, "n": 4, "affected_capital": 100}
        conn.execute(db.grid_intel_recommendations.insert().values(grid_id=grid_id,
            symbol="XRPUSDT", strategy="simple", created_at=created, action="mantener",
            candidate_json='{"action":"mantener","geometry":'+__import__('json').dumps(geometry)+',"affected_capital":100,"all_candidates":[]}',
            baseline_json=__import__('json').dumps({"action":"mantener","geometry":geometry,"affected_capital":100}),
            signals_json="{}", method_version="test", would_apply=False))
    class Market:
        def get_historical_klines(self, symbol, interval, lookback, start_time=None):
            stamps = pd.date_range(created, periods=7 * 24 * 12 + 1, freq="5min", tz="UTC")
            return pd.DataFrame({"timestamp": stamps, "open": np.ones(len(stamps)),
                "high": np.full(len(stamps), 1.01), "low": np.full(len(stamps), .99),
                "close": np.ones(len(stamps))})
    job = GridIntelligenceJob(db, Market(), lambda _symbol: FILTERS, clock=lambda: now)
    result = job.label_mature_recommendations()
    assert result["labeled"] == 1
    with db.engine.connect() as conn:
        label = conn.execute(select(db.grid_intel_labels)).mappings().one()
    payload = __import__('json').loads(label["label_json"])
    assert label["status"] == "mature"
    assert payload["gain_difference_usdt"] == 0
    assert "inventario retenido" in payload["scope"]
    db.engine.dispose()
