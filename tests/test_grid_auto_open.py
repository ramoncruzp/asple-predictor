from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

from grid.auto_open import GridAutoOpen


def row(symbol):
    return {"symbol": symbol, "eligible": True, "score": .9,
        "suggested_structure": {"feasible": True, "range_low": Decimal("90"),
            "range_high": Decimal("110"), "n_levels": 5}}


class DB:
    def __init__(self, maximum=5):
        self.events, self.opened, self.maximum = [], set(), maximum

    def list_grid_events(self, **kwargs): return list(self.events)
    def count_open_grids(self): return len(self.opened)
    def has_open_grid(self, symbol): return symbol in self.opened
    def add_grid_event(self, **event):
        result = {**event, "ts": datetime.now(timezone.utc)}
        self.events.append(result)
        return result


class Scanner:
    def __init__(self): self.calls = 0
    def scan(self, **kwargs):
        self.calls += 1
        return {"results": [row("XRPUSDT"), row("ETHUSDT")]}


class Engine:
    def __init__(self, db): self.db, self.calls = db, []
    def create_grid(self, symbol, *args, **kwargs):
        self.calls.append((symbol, kwargs))
        self.db.opened.add(symbol)
        return {"id": len(self.calls), "status": "ACTIVE"}


class Testnet:
    client = SimpleNamespace(testnet=True)


def settings(**overrides):
    values = dict(scanner_auto_open=True, scanner_auto_open_interval_hours=6,
        scanner_auto_open_max_per_run=1, scanner_auto_open_min_score=.6,
        scanner_auto_open_daily_cap=2, scanner_auto_open_strategy="simple",
        scanner_auto_open_target_pct=None, scanner_auto_open_max_days=None,
        usdt_por_grid=100, max_grids_simultaneos=5)
    values.update(overrides)
    return SimpleNamespace(**values)


def setup(cfg=None, client=None, db=None):
    cfg = cfg or settings()
    db = db or DB()
    scanner, engine = Scanner(), Engine(db)
    service = GridAutoOpen(scanner, db, engine, client or Testnet(), cfg,
                           settings_factory=lambda: cfg)
    return service, scanner, engine, db


def test_auto_open_is_disabled_by_default_and_hot_switch_is_read_each_run():
    cfg = settings(scanner_auto_open=False)
    service, scanner, engine, _ = setup(cfg)
    assert service.run_once()["disabled"] is True
    assert scanner.calls == 0 and engine.calls == []
    cfg.scanner_auto_open = True
    assert len(service.run_once()["opened"]) == 1
    service.stop()


def test_caps_and_max_grids_are_respected_and_slot_is_idempotent():
    cfg = settings(scanner_auto_open_max_per_run=2, scanner_auto_open_daily_cap=1,
                   max_grids_simultaneos=1)
    service, scanner, engine, db = setup(cfg)
    result = service.run_once()
    assert len(result["opened"]) == 1
    assert len(engine.calls) == 1
    replay = service.run_once()
    assert replay.get("duplicate_slot") is not None
    assert len(engine.calls) == 1
    assert sum(event["event_type"] == "AUTO_OPEN" for event in db.events) == 1
    assert db.events[-1]["details"]["who"] == "auto"


def test_daily_cap_counts_persisted_completions_and_non_testnet_is_rejected():
    db = DB()
    db.add_grid_event(run_id=None, source="CLI", event_type="AUTO_OPEN", ts=datetime.now(timezone.utc),
                      details={"phase": "COMPLETED", "slot": -1})
    service, scanner, engine, _ = setup(settings(scanner_auto_open_daily_cap=1), db=db)
    assert service.run_once()["daily_cap"] == 1
    assert engine.calls == []
    service.stop()

    service, scanner, engine, _ = setup(client=SimpleNamespace(client=SimpleNamespace(testnet=False)))
    assert "Testnet" in service.run_once()["error"]
    assert scanner.calls == 0 and engine.calls == []
    service.stop()
