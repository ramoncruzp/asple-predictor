from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from grid import monitor as monitor_module
from grid.monitor import GridMonitor
from grid.policy import PolicyDecision
from tests.test_grid_engine import create, make_engine
from tests.test_grid_monitor import monitor_settings


class QuietVolatility:
    last_reason = None

    def get(self, symbol, horizon_h=24):
        return SimpleNamespace(sigma_24h=0.05)


def test_monitor_syncs_then_processes_enabled_smart_grid_loans(monkeypatch):
    engine, db, exchange = make_engine(fee_rate="0", fee_asset="USDT")
    grid = create(engine, strategy="smart", params={"loans_enabled": True})
    settings = monitor_settings()
    settings.grid_policy_enabled = True
    calls = []
    original_sync = engine.sync_grid

    def sync(grid_id):
        calls.append("sync")
        return original_sync(grid_id)

    def process(grid_id, now=None):
        calls.append(("loans", grid_id, now))
        return {"created": 0}

    monkeypatch.setattr(engine, "sync_grid", sync)
    monkeypatch.setattr(engine, "process_grid_loans", process)
    now = datetime(2026, 9, 30, tzinfo=timezone.utc)
    monitor = GridMonitor(db, exchange, engine, settings, clock=lambda: now,
                          vol_provider=QuietVolatility())
    result = monitor.run_once("SCHEDULED")
    assert result["status"] == "OK"
    assert calls[0] == "sync"
    assert calls[1] == ("loans", grid["id"], now)


@pytest.mark.parametrize("status,strategy,policy_enabled", [
    ("PAUSED", "smart", True), ("ACTIVE", "simple", True), ("ACTIVE", "smart", False),
])
def test_monitor_never_processes_loans_when_grid_or_policy_is_ineligible(
    monkeypatch, status, strategy, policy_enabled,
):
    engine, db, exchange = make_engine(fee_rate="0", fee_asset="USDT")
    grid = create(engine, strategy="smart", params={"loans_enabled": True})
    if strategy == "simple":
        db.update_grid(grid["id"], strategy="simple", params=None)
    if status == "PAUSED":
        db.update_grid(grid["id"], status="PAUSED")
    settings = monitor_settings()
    settings.grid_policy_enabled = policy_enabled
    calls = []
    monkeypatch.setattr(engine, "process_grid_loans", lambda *args, **kwargs: calls.append(args))
    monitor = GridMonitor(db, exchange, engine, settings, vol_provider=QuietVolatility())
    monitor.run_once("SCHEDULED")
    assert calls == []


def test_monitor_skips_loans_on_the_same_pass_as_a_successful_adjust(monkeypatch):
    engine, db, exchange = make_engine(fee_rate="0", fee_asset="USDT")
    grid = create(engine, strategy="smart", params={"loans_enabled": True, "adjust_enabled": True})
    settings = monitor_settings()
    settings.grid_policy_enabled = True
    calls = []
    monkeypatch.setattr(monitor_module, "adjust_decision", lambda *args, **kwargs: PolicyDecision(
        "ADJUST", (), {"range_low": 95.0, "range_high": 105.0, "n_levels": 5},
    ))
    monkeypatch.setattr(engine, "preview_adjust", lambda *args, **kwargs: {"ok": True})
    monkeypatch.setattr(engine, "adjust_grid", lambda *args, **kwargs: {"ok": True, "changed": True})
    monkeypatch.setattr(engine, "process_grid_loans", lambda *args, **kwargs: calls.append(args))
    monitor = GridMonitor(db, exchange, engine, settings, vol_provider=QuietVolatility())
    result = monitor.run_once("SCHEDULED")
    assert result["status"] == "OK"
    assert calls == []


def test_monitor_isolates_loan_failure_to_partial_grid_run(monkeypatch):
    engine, db, exchange = make_engine(fee_rate="0", fee_asset="USDT")
    grid = create(engine, strategy="smart", params={"loans_enabled": True})
    settings = monitor_settings()
    settings.grid_policy_enabled = True

    def fail(*args, **kwargs):
        raise RuntimeError("loan storage unavailable")

    monkeypatch.setattr(engine, "process_grid_loans", fail)
    monitor = GridMonitor(db, exchange, engine, settings, vol_provider=QuietVolatility())
    result = monitor.run_once("SCHEDULED")
    assert result["status"] == "PARTIAL" and result["grids_failed"] == 1
    events = db.list_grid_events(grid_id=grid["id"], event_type="POLICY_ACTION_FAILED")
    assert events and events[0]["details"]["action"] == "LOANS"
