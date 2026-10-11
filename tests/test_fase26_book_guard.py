"""Fase 26 - guard against one-sided Testnet order books.

Covers F2 (monitor), F3 (engine open rejection), F4 (scanner eligibility) and
F5 (control_service / grid_status / grid_ctl) with the shared fake exchange.
"""
from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import pytest

from grid.engine import GridConfigError
from grid.monitor import GridMonitor
from tests.test_grid_engine import create, make_engine
from tests.test_grid_monitor import monitor_settings
from tests.test_grids_api import app as _api_app, payload as _api_payload
from tests.test_grid_scan_service import Client as _ScanClient, setup as _scan_setup
from tests.test_grid_control_api import FakeControlExchange, build_client as _build_control_client
from tests.test_grid_status_api import make_app as _status_make_app
from scripts import grid_ctl
from tests.test_grid_ctl_open import _context as _ctl_context


def _smart_grid_with_sigma(sigma=0.02):
    engine, db, exchange = make_engine(fee_rate="0", fee_asset="USDT")

    class Provider:
        last_reason = None

        def get(self, symbol, horizon_h=24):
            return SimpleNamespace(sigma_24h=sigma, source="realized", fallback=False)

    provider = Provider()
    engine.vol_provider = provider
    grid = engine.create_grid("XRPUSDT", 90, 110, 5, strategy="smart", params={"horizon_h": 4})
    return engine, db, exchange, provider, grid


# -- F6.2: incident regression -----------------------------------------------

def test_one_sided_book_does_not_trigger_false_out_of_range_close():
    engine, db, exchange, provider, grid = _smart_grid_with_sigma()
    # Real incident shape, scaled to this fixture's tick size: bid is INSIDE the
    # range (90-110) but ask=0. The old code averaged (90+0)/2=45, which is
    # below range_low=90, and closed the grid as "out_of_range".
    exchange.bid, exchange.ask = Decimal("90.5"), Decimal("0")
    monitor = GridMonitor(db, exchange, engine, monitor_settings(), vol_provider=provider)
    result = monitor.run_once()
    assert result["status"] == "OK"
    assert db.get_grid(grid["id"])["status"] == "ACTIVE"
    assert db.list_grid_events(grid_id=grid["id"], event_type="GRID_AUTO_CLOSE") == []
    one_sided = db.list_grid_events(event_type="BOOK_ONE_SIDED")
    assert len(one_sided) == 1
    assert one_sided[0]["grid_id"] is None
    assert one_sided[0]["details"]["symbol"] == "XRPUSDT"
    assert one_sided[0]["details"]["bid"] == "90.5"
    assert one_sided[0]["details"]["ask"] == "0"


# -- F6.3: cooldown -----------------------------------------------------------

def test_one_sided_book_event_has_60_minute_cooldown():
    from datetime import datetime, timedelta, timezone
    engine, db, exchange, provider, grid = _smart_grid_with_sigma()
    exchange.bid, exchange.ask = Decimal("90.5"), Decimal("0")
    now = datetime(2026, 10, 11, 12, 0, 0, tzinfo=timezone.utc)
    monitor = GridMonitor(db, exchange, engine, monitor_settings(),
                           vol_provider=provider, clock=lambda: now)
    monitor.run_once()
    monitor.run_once()
    assert len(db.list_grid_events(event_type="BOOK_ONE_SIDED")) == 1
    now = now + timedelta(minutes=59)
    monitor.run_once()
    assert len(db.list_grid_events(event_type="BOOK_ONE_SIDED")) == 1
    now = now + timedelta(minutes=2)
    monitor.run_once()
    assert len(db.list_grid_events(event_type="BOOK_ONE_SIDED")) == 2


# -- F6.4: normal book unaffected --------------------------------------------

def test_normal_book_still_closes_grid_when_genuinely_out_of_range():
    engine, db, exchange, provider, grid = _smart_grid_with_sigma()
    exchange.bid, exchange.ask = Decimal("85"), Decimal("85.01")
    monitor = GridMonitor(db, exchange, engine, monitor_settings(), vol_provider=provider)
    result = monitor.run_once()
    assert result["status"] == "OK"
    assert db.list_grid_events(event_type="BOOK_ONE_SIDED") == []
    assert db.list_grid_events(grid_id=grid["id"], event_type="GRID_AUTO_CLOSE")


# -- F6.5: opening is rejected before any order/row -------------------------

def test_create_grid_rejects_one_sided_book_before_any_order_or_row():
    engine, db, exchange = make_engine(fee_rate="0", fee_asset="USDT")
    exchange.bid, exchange.ask = Decimal("95"), Decimal("0")
    before_count = len(db.list_grids_by_status({"ACTIVE", "OPENING", "PAUSED", "CLOSING", "HOLDING",
                                                 "CLOSED", "CANCELLED", "FAILED", "ERROR"}))
    with pytest.raises(GridConfigError, match="libro unilateral"):
        create(engine)
    assert exchange.create_calls == []
    after_count = len(db.list_grids_by_status({"ACTIVE", "OPENING", "PAUSED", "CLOSING", "HOLDING",
                                                "CLOSED", "CANCELLED", "FAILED", "ERROR"}))
    assert after_count == before_count


def test_dry_run_blocks_a_crossed_testnet_book(tmp_path):
    """A crossed book (ask < bid, both positive) is not caught by the old
    bid<=0/ask<=0 checks in api/routes/grids.py -- book_mid() closes that gap."""
    client, _, exchange = _api_app(tmp_path)
    exchange.get_book_ticker = lambda _symbol: {"bid_price": "100.5", "ask_price": "100.0"}
    response = client.post("/api/grids/open", json=_api_payload())
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["testnet_price"] is None
    assert body["testnet_price_guard"]["allowed"] is False
    assert "libro cruzado" in body["testnet_price_guard"]["reason"]
    assert exchange.create_calls == []


def test_confirmed_open_rejects_a_crossed_testnet_book_without_creating_grid(tmp_path):
    client, db, exchange = _api_app(tmp_path)
    exchange.get_book_ticker = lambda _symbol: {"bid_price": "100.5", "ask_price": "100.0"}
    response = client.post("/api/grids/open", json=_api_payload(dry_run=False, confirm=True))
    assert response.status_code == 422
    assert "libro cruzado" in response.json()["detail"]
    assert exchange.create_calls == []
    details = db.list_grid_events(event_type="GRID_OPEN_REJECTED")[-1]["details"]
    assert details["mid"] is None


# -- F6.6: scanner reports a one-sided public book as ineligible ------------

def test_scanner_marks_one_sided_public_book_as_ineligible_not_as_a_generic_error(tmp_path):
    client = _ScanClient()
    client.get_book_ticker = lambda symbol: {"bid_price": 0, "ask_price": 100.01}
    service, _ = _scan_setup(tmp_path, client)
    result = service.scan()
    row = next(item for item in result["results"] if item["symbol"] == "XRPUSDT")
    assert "error" not in row
    assert row["eligible"] is False
    assert "libro_unilateral" in row["reasons"]
    assert any(gate["name"] == "book_two_sided" and not gate["passed"]
               for gate in row["hard_filters"])


# -- F6.7: closing with a one-sided book never raises -----------------------

def test_close_preview_with_one_sided_book_does_not_raise(tmp_path):
    class OneSidedExchange(FakeControlExchange):
        def get_book_ticker(self, symbol):
            return {"bid_price": 0, "ask_price": 100}

    client, db, _, engine = _build_control_client(tmp_path, client=OneSidedExchange())
    response = client.post("/api/grids/1/close",
                            json={"mode": "repository", "dry_run": True, "confirm": False})
    assert response.status_code == 200, response.body
    assert response.body["plan"]["bid_used"] is None
    assert response.body["plan"]["held_market_value_usdt"] is None


def test_grid_status_api_reports_price_unavailable_for_one_sided_book(tmp_path):
    client, db, exchange, engine = _status_make_app(tmp_path)
    grid = engine.create_grid("XRPUSDT", Decimal(90), Decimal(110), 5, capital=1000)
    exchange.bid, exchange.ask = Decimal("0"), Decimal("100.01")
    detail = client.get(f"/api/grids/{grid['id']}").json()
    assert detail["summary"]["price"] is None


# -- extra: engine.adjust_grid / preview_adjust / profit_repository close ---

def test_adjust_grid_rejects_one_sided_book_without_moving_cells():
    engine, db, exchange = make_engine(fee_rate="0", fee_asset="USDT")
    grid = create(engine)
    before = db.get_grid_levels(grid["id"])
    exchange.bid, exchange.ask = Decimal("0"), Decimal("100.01")
    result = engine.adjust_grid(grid["id"], 95, 105)
    assert result == {"ok": False, "reason": "libro_unilateral"}
    assert db.get_grid_levels(grid["id"]) == before


def test_preview_adjust_rejects_one_sided_book():
    engine, db, exchange = make_engine(fee_rate="0", fee_asset="USDT")
    grid = create(engine)
    exchange.bid, exchange.ask = Decimal("0"), Decimal("100.01")
    result = engine.preview_adjust(grid["id"], 95, 105)
    assert result == {"ok": False, "reason": "libro_unilateral"}


def test_close_profit_repository_fails_gracefully_with_one_sided_book():
    engine, db, exchange = make_engine(fee_rate="0", fee_asset="USDT")
    grid = create(engine)
    exchange.bid, exchange.ask = Decimal("0"), Decimal("100.01")
    result = engine.close_grid(grid["id"], "profit_repository")
    assert result["status"] == "CLOSING"
    assert any("libro unilateral" in str(error.get("reason", "")) for error in result["errors"])
    assert db.get_grid(grid["id"])["status"] == "CLOSING"


def test_status_view_labels_book_one_sided_as_warning():
    from grid.status_view import EVENT_LABELS, SEVERITY_BY_EVENT
    assert EVENT_LABELS["BOOK_ONE_SIDED"] == "Libro de órdenes unilateral"
    assert SEVERITY_BY_EVENT["BOOK_ONE_SIDED"] == "warning"


def test_grid_ctl_sweep_dust_rejects_one_sided_book(monkeypatch, capsys):
    context = _ctl_context()
    grid = context["engine"].create_grid("XRPUSDT", 90, 110, 5, capital=1000)
    context["exchange"].bid = Decimal("0")
    monkeypatch.setattr(grid_ctl, "build_context", lambda: context)
    assert grid_ctl.main(["sweep-dust", "--grid", str(grid["id"])]) == 1
    assert "libro unilateral" in capsys.readouterr().err


def test_grid_ctl_open_dry_run_reports_one_sided_testnet_book(monkeypatch, capsys):
    context = _ctl_context()
    context["exchange"].ask = Decimal("0")
    monkeypatch.setattr(grid_ctl, "build_context", lambda: context)
    args = ["open", "--symbol", "XRPUSDT", "--low", "90", "--high", "110", "--n", "5",
            "--capital", "1000", "--strategy", "smart", "--dry-run"]
    assert grid_ctl.main(args) == 0
    result = __import__("json").loads(capsys.readouterr().out)
    assert result["testnet_book_mid"] is None
    assert "libro unilateral" in result["testnet_book_warning"]


def test_sync_and_fills_continue_for_a_grid_whose_book_is_one_sided():
    """A one-sided book must skip price decisions, not sync/fills, for that grid."""
    engine, db, exchange = make_engine(fee_rate="0", fee_asset="USDT")
    grid = create(engine)  # simple grid, XRPUSDT, places BUY_OPEN levels below bid
    buy_level = next(row for row in db.get_grid_levels(grid["id"]) if row["state"] == "BUY_OPEN")
    exchange.fill(buy_level["order_id"])
    exchange.bid, exchange.ask = Decimal("0"), Decimal("0")
    monitor = GridMonitor(db, exchange, engine, monitor_settings())
    result = monitor.run_once()
    assert result["status"] == "OK"
    assert db.list_grid_events(grid_id=grid["id"], event_type="SYNC_FAILED") == []
    refreshed = next(row for row in db.get_grid_levels(grid["id"])
                      if int(row["level_idx"]) == int(buy_level["level_idx"]))
    assert refreshed["state"] == "SELL_OPEN"  # the fill was still synced and a sell placed
    assert db.list_grid_events(event_type="BOOK_ONE_SIDED")
