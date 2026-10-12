from decimal import Decimal
from types import SimpleNamespace

from grid.monitor import GridMonitor
from tests.test_grid_engine import create, make_engine
from tests.test_grid_monitor import monitor_settings


def _monitor_with_held_cell(stoploss_enabled):
    engine, db, exchange = make_engine(fee_rate="0", fee_asset="USDT")
    grid = create(engine)
    buy = next(order for order in exchange.get_open_orders("XRPUSDT") if order["side"] == "BUY")
    exchange.fill(buy["order_id"])
    engine.sync_grid(grid["id"])
    cell = next(row for row in db.get_grid_levels(grid["id"]) if row["state"] == "SELL_OPEN")
    db.update_level(grid["id"], cell["level_idx"], entry_price=110, stop_loss_pct=5)
    market = {"mid": 104.0}

    def book(_symbol):
        mid = Decimal(str(market["mid"]))
        return {"bid_price": mid, "ask_price": mid + Decimal("0.01"),
                "bid_qty": Decimal("100"), "ask_qty": Decimal("100")}

    exchange.get_book_ticker = book
    settings = monitor_settings()
    settings.stoploss_enabled = stoploss_enabled
    provider = SimpleNamespace(get=lambda *_args, **_kwargs:
        SimpleNamespace(sigma_24h=.02, source="model", fallback=False))
    monitor = GridMonitor(db, exchange, engine, settings, vol_provider=provider)
    engine.sync_grid = lambda *_args, **_kwargs: {"ok": True}
    return monitor, engine, db, grid, cell, market


def test_enabled_stoploss_calls_cell_action_once_at_threshold():
    monitor, engine, db, grid, cell, _market = _monitor_with_held_cell(True)
    calls = []
    engine.stoploss_cell = lambda *args, **kwargs: (calls.append((args, kwargs)) or {"ok": True})

    result = monitor.run_once()

    assert result["status"] == "OK"
    assert len(calls) == 1
    assert calls[0][0][:3] == (grid["id"], cell["level_idx"], "stop_loss_pct")
    assert db.list_grid_events(grid_id=grid["id"], event_type="STOPLOSS_SUSPENDED") == []


def test_disabled_stoploss_is_suspended_once_per_episode():
    monitor, engine, db, grid, cell, _market = _monitor_with_held_cell(False)
    calls = []
    engine.stoploss_cell = lambda *args, **kwargs: (calls.append((args, kwargs)) or {"ok": True})

    for _ in range(3):
        result = monitor.run_once()
        assert result["status"] == "OK"

    events = db.list_grid_events(grid_id=grid["id"], event_type="STOPLOSS_SUSPENDED")
    assert calls == []
    assert len(events) == 1
    assert events[0]["reason"] == "interruptor_apagado"
    assert events[0]["details"] == {
        "level_idx": cell["level_idx"], "entry_price": 110.0,
        "stop_loss_pct": 5.0, "mid": 104.005, "would_have_sold_at": 104.005,
    }


def test_disabled_stoploss_recovery_starts_a_new_episode():
    monitor, engine, db, grid, _cell, market = _monitor_with_held_cell(False)
    calls = []
    engine.stoploss_cell = lambda *args, **kwargs: (calls.append((args, kwargs)) or {"ok": True})

    monitor.run_once()
    market["mid"] = 106.0
    monitor.run_once()
    market["mid"] = 104.0
    monitor.run_once()

    events = db.list_grid_events(grid_id=grid["id"], event_type="STOPLOSS_SUSPENDED")
    assert calls == []
    assert len(events) == 2
