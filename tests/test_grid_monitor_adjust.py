from types import SimpleNamespace
from datetime import datetime, timedelta, timezone

from grid.monitor import GridMonitor
from tests.test_grid_engine import make_engine
from tests.test_grid_monitor import monitor_settings


class Vol:
    sigma_24h = 0.01
    last_reason = None

    def get(self, _symbol, horizon_h=24):
        return SimpleNamespace(sigma_24h=self.sigma_24h, stale=False)


def test_monitor_adjusts_smart_grid_and_leaves_simple_grid_unchanged():
    engine, db, exchange = make_engine(fee_rate="0", fee_asset="USDT")
    smart = engine.create_grid("XRPUSDT", 90, 110, 5, capital=1000,
                               strategy="smart", params={"horizon_h": 24, "pause_enter_prob": 0.99})
    simple = engine.create_grid("XRPUSDT", 95, 105, 4, capital=1000)
    exchange.move_price(109.49, 109.5, 109.495)
    run = GridMonitor(db, exchange, engine, monitor_settings(), vol_provider=Vol()).run_once()
    assert run["status"] == "OK"
    assert db.get_grid(smart["id"])["range_low"] != 90
    assert db.get_grid(simple["id"])["range_low"] == 95
    assert db.get_last_event(smart["id"], "GRID_ADJUSTED")["source"] == "MONITOR"


def test_monitor_adjust_failure_is_partial_and_records_failure():
    engine, db, exchange = make_engine(fee_rate="0", fee_asset="USDT")
    smart = engine.create_grid("XRPUSDT", 90, 110, 5, capital=1000,
                               strategy="smart", params={"horizon_h": 24, "pause_enter_prob": 0.99})
    exchange.move_price(90.5, 90.51, 90.505)
    engine.adjust_grid = lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("injected"))
    run = GridMonitor(db, exchange, engine, monitor_settings(), vol_provider=Vol()).run_once()
    assert run["status"] == "PARTIAL"
    failure = db.get_last_event(smart["id"], "POLICY_ACTION_FAILED")
    assert failure and failure["details"]["action"] == "ADJUST"


def test_monitor_adjust_cooldown_and_block_event_rate_limit():
    engine, db, exchange = make_engine(fee_rate="0", fee_asset="USDT")
    smart = engine.create_grid("XRPUSDT", 90, 110, 5, capital=1000,
                               strategy="smart", params={"horizon_h": 24, "pause_enter_prob": 0.99})
    exchange.move_price(90.5, 90.51, 90.505)
    now = [datetime.now(timezone.utc)]
    monitor = GridMonitor(db, exchange, engine, monitor_settings(), clock=lambda: now[0], vol_provider=Vol())
    assert monitor.run_once()["status"] == "OK"
    first_range = db.get_grid(smart["id"])["range_low"]
    upper = db.get_grid(smart["id"])["range_high"]
    exchange.move_price(upper * 0.999 - 0.005, upper * 0.999, upper * 0.999 - 0.0025)
    now[0] += timedelta(hours=1)
    monitor.run_once()
    blocked = db.list_grid_events(grid_id=smart["id"], event_type="ADJUST_BLOCKED")
    assert len(blocked) == 1 and blocked[0]["reason"] == "cooldown"
    assert db.get_grid(smart["id"])["range_low"] == first_range
    now[0] += timedelta(hours=1)
    monitor.run_once()
    assert len(db.list_grid_events(grid_id=smart["id"], event_type="ADJUST_BLOCKED")) == 1
