from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from grid.monitor import GridMonitor
from tests.test_grid_engine import make_engine
from tests.test_grid_monitor import monitor_settings


class Vol:
    def __init__(self, sigma):
        self.sigma = sigma
        self.last_reason = "stale" if sigma is None else None

    def get(self, symbol, horizon_h=24):
        return None if self.sigma is None else SimpleNamespace(sigma_24h=self.sigma, stale=False)


def _smart_grid(engine, params=None):
    # These 15B-1 tests exercise pause/resume behavior in isolation; 15B-2
    # adjustment has an explicit opt-out so the original policy assertions
    # remain focused on their intended action.
    effective = {"adjust_enabled": False, **(params or {})}
    return engine.create_grid("XRPUSDT", 90, 110, 5, capital=1000,
                              strategy="smart", params=effective)


def test_smart_grid_auto_pauses_and_resumes_with_hysteresis():
    engine, db, exchange = make_engine(fee_rate="0", fee_asset="USDT")
    grid = _smart_grid(engine)
    vol = Vol(0.5)
    monitor = GridMonitor(db, exchange, engine, monitor_settings(), vol_provider=vol)
    first = monitor.run_once()
    assert db.get_grid(grid["id"])["status"] == "PAUSED"
    paused_event = db.get_last_event(grid["id"], "GRID_PAUSED")
    assert paused_event["source"] == "MONITOR"
    assert "break_prob" in paused_event["details"]["reasons"]
    vol.sigma = 0.001
    second = monitor.run_once()
    assert db.get_grid(grid["id"])["status"] == "ACTIVE"
    assert db.get_last_event(grid["id"], "GRID_RESUMED")["source"] == "MONITOR"
    assert first["status"] == second["status"] == "OK"


def test_break_probability_pause_is_not_resumed_without_fresh_volatility():
    engine, db, exchange = make_engine(fee_rate="0", fee_asset="USDT")
    grid = _smart_grid(engine)
    vol = Vol(0.5)
    monitor = GridMonitor(db, exchange, engine, monitor_settings(), vol_provider=vol)
    monitor.run_once()
    vol.sigma = None
    monitor.run_once()
    assert db.get_grid(grid["id"])["status"] == "PAUSED"
    assert db.get_last_event(grid["id"], "GRID_RESUMED") is None
    assert db.list_grid_events(grid_id=grid["id"], event_type="VOL_UNAVAILABLE")


def test_pause_for_non_volatility_reason_can_resume_without_volatility():
    engine, db, exchange = make_engine(fee_rate="0", fee_asset="USDT")
    grid = _smart_grid(engine)
    db.update_grid(grid["id"], status="PAUSED")
    db.add_grid_event(run_id=None, source="CLI", grid_id=grid["id"], event_type="GRID_PAUSED",
                      reason="trapped_capital_pct", details={"reasons": ["trapped_capital_pct"]})
    vol = Vol(None)
    monitor = GridMonitor(db, exchange, engine, monitor_settings(), vol_provider=vol)
    monitor.run_once()
    assert db.get_grid(grid["id"])["status"] == "ACTIVE"


def test_smart_out_of_range_auto_close_goes_to_repository_and_simple_is_untouched():
    engine, db, exchange = make_engine(fee_rate="0", fee_asset="USDT")
    smart = _smart_grid(engine)
    exchange.move_price(120, 120.01, 120)
    monitor = GridMonitor(db, exchange, engine, monitor_settings(), vol_provider=Vol(None))
    monitor.run_once()
    assert db.get_grid(smart["id"])["status"] == "CLOSED"
    event = db.get_last_event(smart["id"], "GRID_AUTO_CLOSE")
    assert event and "out_of_range" in event["details"]["reasons"]
    assert event["source"] == "MONITOR"


def test_policy_disabled_does_not_auto_close_smart_grid():
    engine, db, exchange = make_engine(fee_rate="0", fee_asset="USDT")
    grid = _smart_grid(engine)
    exchange.move_price(120, 120.01, 120)
    settings = monitor_settings()
    settings.grid_policy_enabled = False
    GridMonitor(db, exchange, engine, settings, vol_provider=Vol(None)).run_once()
    assert db.get_grid(grid["id"])["status"] == "ACTIVE"
    assert db.get_last_event(grid["id"], "GRID_AUTO_CLOSE") is None


def test_monitor_retries_monitor_initiated_close_and_keeps_summary_metrics():
    engine, db, exchange = make_engine(fee_rate="0", fee_asset="USDT")
    grid = _smart_grid(engine)
    db.update_grid(grid["id"], status="CLOSING")
    db.add_grid_event(run_id=None, source="MONITOR", grid_id=grid["id"],
                      event_type="GRID_CLOSE_STARTED", details={"mode": "repository"})
    monitor = GridMonitor(db, exchange, engine, monitor_settings(), vol_provider=Vol(0.001))
    run = monitor.run_once()
    assert db.get_grid(grid["id"])["status"] == "CLOSED"
    summaries = [row for row in db.list_grid_snapshots(grid_id=grid["id"], run_id=run["run_id"])
                 if row["level_idx"] is None]
    assert summaries and set(("break_prob", "sigma_24h", "trapped_capital_pct", "free_cells")) <= summaries[0].keys()


def test_vol_unavailable_event_is_rate_limited_to_six_hours():
    engine, db, exchange = make_engine(fee_rate="0", fee_asset="USDT")
    grid = _smart_grid(engine)
    now = [datetime.now(timezone.utc)]
    monitor = GridMonitor(db, exchange, engine, monitor_settings(), clock=lambda: now[0], vol_provider=Vol(None))
    monitor.run_once()
    now[0] += timedelta(hours=1)
    monitor.run_once()
    assert len(db.list_grid_events(grid_id=grid["id"], event_type="VOL_UNAVAILABLE")) == 1
    now[0] += timedelta(hours=6)
    monitor.run_once()
    assert len(db.list_grid_events(grid_id=grid["id"], event_type="VOL_UNAVAILABLE")) == 2
