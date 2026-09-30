from __future__ import annotations

from decimal import Decimal
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from grid.engine import GridEngine
from grid.monitor import GridMonitor
from database.db_manager import DBManager
from tests.test_grid_engine import create, make_engine


def test_engine_event_sink_receives_events_and_sink_failure_is_isolated(caplog):
    engine, db, exchange = make_engine(fee_rate="0", fee_asset="USDT")
    events = []
    engine.event_sink = events.append
    grid = create(engine)
    assert any(row["event_type"] == "BUY_PLACED" and row["grid_id"] == grid["id"] for row in events)

    engine.event_sink = lambda event: (_ for _ in ()).throw(RuntimeError("sink unavailable"))
    engine._emit("TEST_EVENT", grid["id"], 0, details={"value": 1})
    assert "event sink failed" in caplog.text


def test_sync_closing_settles_fills_without_rearming_buy():
    engine, db, exchange = make_engine(fee_rate="0", fee_asset="USDT")
    grid = create(engine)
    db.update_grid(grid["id"], status="CLOSING")
    buy = exchange.get_open_orders("XRPUSDT")[-1]
    exchange.fill(buy["order_id"])
    engine.sync_closing(grid["id"])
    level = db.get_grid_levels(grid["id"])[2]
    sell_id = level["order_id"]
    exchange.fill(sell_id)
    before = len(exchange.create_calls)
    result = engine.sync_closing(grid["id"])
    level = db.get_grid_levels(grid["id"])[2]
    assert result["cycles_completed"] == 1
    assert level["state"] == "DONE"
    assert len(exchange.create_calls) == before


def test_repository_sync_sells_only_and_closes_when_last_cell_is_done():
    engine, db, exchange = make_engine(fee_rate="0", fee_asset="USDT")
    grid = create(engine)
    levels = db.get_grid_levels(grid["id"])
    for row in levels:
        db.update_level(grid["id"], row["level_idx"], state="ERROR", order_id=None, client_order_id=None)
    level = levels[2]
    sell = exchange.place_order(
        "XRPUSDT", "SELL", Decimal("1.0"), Decimal("102.0"), client_order_id="g1L2S0",
    )
    db.update_grid(grid["id"], status="HOLDING")
    db.update_level(grid["id"], 2, state="SELL_OPEN", held_qty=1.0,
                    client_order_id="g1L2S0", order_id=sell["order_id"])
    exchange.fill(sell["order_id"])
    before_calls = len(exchange.create_calls)
    result = engine.sync_repository(grid["id"])
    assert result["cycles_completed"] == 1
    assert db.get_grid_levels(grid["id"])[2]["state"] == "DONE"
    assert db.get_grid(grid["id"])["status"] == "CLOSED"
    assert len(exchange.create_calls) == before_calls


def monitor_settings():
    return SimpleNamespace(
        usdt_por_grid=1000.0, max_grids_simultaneos=5,
        capital_max_por_nivel_pct=0.30, grid_min_step_pct=0.003,
        grid_monitor_interval=900, grid_monitor_gap_minutes=20, grid_monitor_enabled=True,
    )


def test_monitor_run_records_heartbeat_snapshots_without_noise():
    engine, db, exchange = make_engine(fee_rate="0", fee_asset="USDT")
    copied = create(engine)
    monitor = GridMonitor(db, exchange, engine, monitor_settings())
    result = monitor.run_once("SCHEDULED")
    assert result["status"] == "OK" and result["grids_checked"] == 1
    assert result["events_written"] == 0
    assert db.get_monitor_run(result["run_id"])["status"] == "OK"
    snapshots = db.list_grid_snapshots(grid_id=copied["id"], run_id=result["run_id"])
    assert len(snapshots) == 6
    assert sum(row["level_idx"] is None for row in snapshots) == 1


def test_monitor_isolates_grid_sync_failure_and_keeps_other_grids_running():
    engine, db, exchange = make_engine(fee_rate="0", fee_asset="USDT")
    # Seed legacy duplicate grids directly: the production create_grid guard now
    # prevents creating this invalid same-symbol/same-strategy setup.
    grids = [db.create_grid_with_levels(
        {"symbol": "XRPUSDT", "range_low": 90, "range_high": 110, "n_levels": 5,
         "capital_total": 1000, "status": "ACTIVE", "environment": "testnet"},
        [{"level_idx": i, "price": 90 + i, "sell_price": 91 + i, "capital": 200, "state": "IDLE"}
         for i in range(5)],
    ) for _ in range(3)]
    seen = []
    def sync(grid_id):
        seen.append(grid_id)
        if grid_id == grids[0]["id"]:
            raise RuntimeError("one grid failed")
        return {}
    engine.sync_grid = sync
    monitor = GridMonitor(db, exchange, engine, monitor_settings())
    result = monitor.run_once("SCHEDULED")
    assert result["status"] == "PARTIAL"
    assert result["grids_checked"] == 3 and result["grids_failed"] == 1
    assert len(seen) == 3
    assert len(db.list_grid_events(event_type="SYNC_FAILED")) == 1


def test_monitor_detects_run_gap_and_interrupts_stale_startup_runs():
    db = DBManager("sqlite:///:memory:")
    first = db.start_monitor_run("SCHEDULED")
    old = datetime.now(timezone.utc) - timedelta(minutes=26)
    with db.engine.begin() as conn:
        conn.execute(db.monitor_runs.update().where(db.monitor_runs.c.id == first["id"]).values(started_at=old, status="RUNNING"))
    engine, _, exchange = make_engine()
    monitor = GridMonitor(db, exchange, engine, monitor_settings())
    result = monitor.run_once("STARTUP")
    assert db.get_monitor_run(first["id"])["status"] == "INTERRUPTED"
    gap = db.list_grid_events(event_type="RUN_GAP")[0]
    assert gap["details"]["minutes"] >= 26
    assert gap["details"]["previous_run_id"] == first["id"]
    assert db.get_monitor_run(result["run_id"])["trigger"] == "STARTUP"


def test_monitor_market_read_failure_keeps_snapshots_with_null_mid(caplog):
    engine, db, exchange = make_engine()
    grid = db.create_grid_with_levels(
        {"symbol": "XRPUSDT", "range_low": 90, "range_high": 110, "n_levels": 1,
         "capital_total": 20, "status": "ACTIVE", "environment": "testnet", "open_price": 100},
        [{"level_idx": 0, "price": 99, "sell_price": 101, "capital": 20, "state": "IDLE"}],
    )
    exchange.get_book_ticker = lambda symbol: (_ for _ in ()).throw(RuntimeError("ticker unavailable"))
    engine.sync_grid = lambda grid_id: {}
    result = GridMonitor(db, exchange, engine, monitor_settings()).run_once("SCHEDULED")
    rows = db.list_grid_snapshots(grid_id=grid["id"], run_id=result["run_id"])
    assert len(rows) == 2 and all(row["market_mid"] is None for row in rows)
    assert "grid snapshot mid unavailable" in caplog.text


def test_monitor_persists_fill_events_with_run_and_level_identity_and_unrealized_snapshot():
    engine, db, exchange = make_engine(fee_rate="0", fee_asset="USDT")
    grid = create(engine)
    buy = exchange.get_open_orders("XRPUSDT")[-1]
    exchange.fill(buy["order_id"])
    monitor = GridMonitor(db, exchange, engine, monitor_settings())
    run = monitor.run_once("SCHEDULED")
    events = db.list_grid_events(grid_id=grid["id"])
    buy_event = next(event for event in events if event["event_type"] == "BUY_FILLED")
    sell_event = next(event for event in events if event["event_type"] == "SELL_PLACED")
    for event in (buy_event, sell_event):
        assert event["run_id"] == run["run_id"]
        assert event["grid_id"] == grid["id"] and event["level_idx"] == 2
        assert event["client_order_id"] and event["order_id"] is not None
    snapshots = db.list_grid_snapshots(grid_id=grid["id"], run_id=run["run_id"])
    level = next(row for row in snapshots if row["level_idx"] == 2)
    assert level["level_state"] == "SELL_OPEN"
    assert level["unrealized_pnl"] == pytest.approx((level["market_mid"] - level["buy_price"]) * level["held_qty"])
    summary = next(row for row in snapshots if row["level_idx"] is None)
    assert summary["open_orders_db"] == 3
    assert summary["inventory_value_usdt"] == pytest.approx(level["market_mid"] * level["held_qty"])


def test_monitor_warns_when_closing_grid_is_stale():
    engine, db, exchange = make_engine()
    grid = create(engine)
    old = datetime.now(timezone.utc) - timedelta(minutes=40)
    db.update_grid(grid["id"], status="CLOSING", created_at=old)
    monitor = GridMonitor(db, exchange, engine, monitor_settings())
    run = monitor.run_once("SCHEDULED")
    event = db.list_grid_events(grid_id=grid["id"], event_type="CLOSE_PENDING")[0]
    assert event["run_id"] == run["run_id"]
    assert event["details"]["age_minutes"] >= 40


def test_monitor_does_not_sync_closing_grid_but_keeps_its_snapshot():
    engine, db, exchange = make_engine()
    grid = create(engine)
    db.update_grid(grid["id"], status="CLOSING")
    engine.sync_grid = lambda _grid_id: (_ for _ in ()).throw(AssertionError("CLOSING grid synchronized"))
    engine.sync_repository = lambda _grid_id: (_ for _ in ()).throw(AssertionError("CLOSING grid synchronized as repository"))

    result = GridMonitor(db, exchange, engine, monitor_settings()).run_once("SCHEDULED")

    assert result["status"] == "OK" and result["grids_checked"] == 1
    snapshots = db.list_grid_snapshots(grid_id=grid["id"], run_id=result["run_id"])
    assert len(snapshots) == 6
    assert all(row["grid_status"] == "CLOSING" for row in snapshots)


class CapturingScheduler:
    running = False

    def __init__(self):
        self.jobs = []

    def add_job(self, function, trigger, **kwargs):
        self.jobs.append({"function": function, "trigger": trigger, **kwargs})

    def start(self):
        self.running = True

    def shutdown(self, wait=True):
        self.running = False


def test_monitor_start_schedules_interval_and_nonblocking_startup_job():
    scheduler = CapturingScheduler()
    engine, db, exchange = make_engine()
    monitor = GridMonitor(db, exchange, engine, monitor_settings(), scheduler=scheduler)
    monitor.start()
    interval, startup = scheduler.jobs
    assert interval["trigger"] == "interval" and interval["seconds"] == 900
    assert interval["max_instances"] == 1 and interval["coalesce"] is True
    assert startup["trigger"] == "date" and startup["args"] == ["STARTUP"]
    assert startup["misfire_grace_time"] == 300
    monitor.stop()
    assert scheduler.running is False


def test_monitor_snapshot_failure_isolated_per_grid():
    engine, db, exchange = make_engine(fee_rate="0", fee_asset="USDT")
    # Seed legacy duplicate grids directly; see the guard test in 15B-1A.
    grids = [db.create_grid_with_levels(
        {"symbol": "XRPUSDT", "range_low": 90, "range_high": 110, "n_levels": 5,
         "capital_total": 1000, "status": "ACTIVE", "environment": "testnet"},
        [{"level_idx": i, "price": 90 + i, "sell_price": 91 + i, "capital": 200, "state": "IDLE"}
         for i in range(5)],
    ) for _ in range(3)]
    original_add_snapshots = db.add_snapshots
    failed_id = grids[0]["id"]
    def fail_one_grid(rows):
        if rows[0]["grid_id"] == failed_id:
            raise RuntimeError("snapshot database unavailable")
        return original_add_snapshots(rows)
    db.add_snapshots = fail_one_grid
    monitor = GridMonitor(db, exchange, engine, monitor_settings())

    result = monitor.run_once("SCHEDULED")

    assert result["status"] == "PARTIAL"
    assert result["grids_checked"] == 3 and result["grids_failed"] == 1
    assert len(db.list_grid_snapshots(grid_id=grids[1]["id"], run_id=result["run_id"])) == 6
    assert len(db.list_grid_snapshots(grid_id=grids[2]["id"], run_id=result["run_id"])) == 6
    assert len(db.list_grid_events(grid_id=failed_id, event_type="SNAPSHOT_FAILED")) == 1


def test_app_lifespan_starts_without_monitor_when_testnet_credentials_missing(tmp_path, monkeypatch, caplog):
    from types import SimpleNamespace
    from api import main as main_module

    settings = SimpleNamespace(
        binance_api_key="tu_api_key_aqui", binance_api_secret="tu_api_secret_aqui",
        testnet_api_key="tu_testnet_api_key_aqui", testnet_api_secret="tu_testnet_secret_aqui",
        database_url="sqlite:///:memory:", log_level="ERROR", grid_monitor_enabled=True,
        grid_monitor_interval=900, grid_monitor_gap_minutes=20,
        usdt_por_grid=100, max_grids_simultaneos=5,
        capital_max_por_nivel_pct=0.30, grid_min_step_pct=0.003,
    )
    class DummyDB:
        def seed_coin_if_missing(self, *args):
            pass
    class DummyModel:
        def load(self, *args):
            pass
    class DummyLoop:
        def __init__(self, *args, **kwargs):
            self.models = {}
        def start(self):
            pass
        def stop(self):
            pass
    monkeypatch.setattr(main_module, "Settings", lambda: settings)
    monkeypatch.setattr(main_module, "BinanceClient", lambda *args: object())
    monkeypatch.setattr(main_module, "DBManager", lambda *args: DummyDB())
    monkeypatch.setattr(main_module, "ModelA", DummyModel)
    monkeypatch.setattr(main_module, "ShadowPredictor", lambda *args: object())
    monkeypatch.setattr(main_module, "PredictionLoop", DummyLoop)
    monkeypatch.setattr(main_module, "VerificationLoop", DummyLoop)
    monkeypatch.setattr(main_module, "VolLoop", DummyLoop)
    monkeypatch.setattr(main_module, "VOL_ARTIFACT_DIR", tmp_path / "missing-vol")

    async def run_lifespan():
        app = SimpleNamespace(state=SimpleNamespace())
        async with main_module.lifespan(app):
            assert app.state.grid_monitor is None
    import asyncio
    asyncio.run(run_lifespan())
    assert "Grid monitor disabled: faltan credenciales" in caplog.text
