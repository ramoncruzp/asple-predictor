"""Tests for api/routes/grid_status.py (Fase 17A): read-only GET endpoints."""
from __future__ import annotations

import asyncio
import json as json_module
from datetime import datetime, timezone
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from urllib.parse import urlencode

import pytest
from fastapi import FastAPI

from api.routes import grid_status, grids
from database.db_manager import DBManager
from grid.engine import GridEngine
from tests.grid_fakes import FakeExchange

NOW = datetime(2026, 10, 2, tzinfo=timezone.utc)


class Response:
    def __init__(self, status, body):
        self.status_code, self.body = status, body
    def json(self):
        return self.body


class LocalClient:
    """Minimal ASGI client supporting GET and POST, loopback by default."""
    def __init__(self, app, host="127.0.0.1"):
        self.app, self.host = app, host

    def _call(self, method, path, *, query=None, json=None, headers=None):
        payload = b"" if json is None else json_module.dumps(json).encode()
        query_string = urlencode(query or {}).encode()
        messages, sent = [], False

        async def receive():
            nonlocal sent
            if not sent:
                sent = True
                return {"type": "http.request", "body": payload, "more_body": False}
            return {"type": "http.disconnect"}

        async def send(message):
            messages.append(message)

        hdrs = [(key.lower().encode(), value.encode()) for key, value in (headers or {}).items()]
        if json is not None:
            hdrs.append((b"content-type", b"application/json"))

        async def call():
            await self.app({
                "type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"},
                "http_version": "1.1", "method": method, "scheme": "http", "path": path,
                "raw_path": path.encode(), "query_string": query_string, "root_path": "",
                "headers": hdrs, "client": (self.host, 50000), "server": ("127.0.0.1", 80),
            }, receive, send)

        asyncio.run(call())
        status = next(m["status"] for m in messages if m["type"] == "http.response.start")
        raw = b"".join(m.get("body", b"") for m in messages if m["type"] == "http.response.body")
        return Response(status, json_module.loads(raw) if raw else None)

    def get(self, path, **kwargs):
        return self._call("GET", path, **kwargs)

    def post(self, path, **kwargs):
        return self._call("POST", path, **kwargs)


def make_app(tmp_path, *, token="", testnet_client="default"):
    db = DBManager(f"sqlite:///{tmp_path}/status_api.db")
    db.add_or_reactivate_coin("XRPUSDT")
    db.add_or_reactivate_coin("ETHUSDT")
    exchange = FakeExchange() if testnet_client == "default" else testnet_client
    settings = SimpleNamespace(
        max_grids_simultaneos=5, usdt_por_grid=100, capital_max_por_nivel_pct=.30,
        grid_min_step_pct=.003, grid_api_token=token, scanner_timeout_seconds=60,
        scanner_fee_pct=.1, scanner_min_spacing_pct=.8, grid_monitor_gap_minutes=20,
    )
    engine = GridEngine(db, exchange, settings) if exchange is not None else None
    api = FastAPI()
    api.include_router(grids.router, prefix="/api/grids")
    api.include_router(grid_status.router)
    api.state.db, api.state.settings = db, settings
    api.state.grid_engine, api.state.testnet_client = engine, exchange
    return LocalClient(api), db, exchange, engine


def _counts(db):
    with db.engine.connect() as conn:
        from sqlalchemy import select, func
        return {
            "grids": conn.execute(select(func.count()).select_from(db.grids)).scalar_one(),
            "grid_levels": conn.execute(select(func.count()).select_from(db.grid_levels)).scalar_one(),
            "grid_events": conn.execute(select(func.count()).select_from(db.grid_events)).scalar_one(),
        }


def test_list_and_detail_return_distinct_data_for_two_grids_same_symbol(tmp_path):
    client, db, exchange, engine = make_app(tmp_path)
    first = engine.create_grid("XRPUSDT", Decimal(90), Decimal(110), 5, capital=1000, strategy="simple")
    second = engine.create_grid("XRPUSDT", Decimal(95), Decimal(105), 4, capital=500,
                                strategy="smart", params={"compound_enabled": False})

    listing = client.get("/api/grids")
    assert listing.status_code == 200
    body = listing.json()
    ids = {row["id"] for row in body["grids"]}
    assert {first["id"], second["id"]} <= ids
    assert body["same_symbol_warning"] is not None and "mismo símbolo" in body["same_symbol_warning"]

    detail_first = client.get(f"/api/grids/{first['id']}").json()
    detail_second = client.get(f"/api/grids/{second['id']}").json()
    assert detail_first["summary"]["capital_total_usdt"] == pytest.approx(1000.0)
    assert detail_second["summary"]["capital_total_usdt"] == pytest.approx(500.0)
    assert detail_first["summary"]["id"] != detail_second["summary"]["id"]


def test_detail_404_for_nonexistent_grid(tmp_path):
    client, *_ = make_app(tmp_path)
    response = client.get("/api/grids/999999")
    assert response.status_code == 404


def test_only_get_allowed_on_read_routes(tmp_path):
    client, db, exchange, engine = make_app(tmp_path)
    grid = engine.create_grid("XRPUSDT", Decimal(90), Decimal(110), 5, capital=1000)
    assert client.post("/api/grids").status_code == 405
    assert client.post(f"/api/grids/{grid['id']}").status_code == 405
    assert client.post("/api/monitor/status").status_code == 405


def test_existing_scan_and_open_routes_unaffected_by_new_router(tmp_path):
    client, db, exchange, engine = make_app(tmp_path)

    class ScanService:
        def scan(self, **kwargs):
            return {"results": [], "data_source": "Binance public spot market data (read-only)",
                    "warning": "aviso"}

    client.app.state.grid_scan_service = ScanService()
    response = client.post("/api/grids/scan", json={"capital": 100})
    assert response.status_code == 200
    assert response.json()["data_source"].startswith("Binance public")


def test_get_scan_and_open_never_return_a_grid(tmp_path):
    client, *_ = make_app(tmp_path)
    for path in ("/api/grids/scan", "/api/grids/open"):
        response = client.get(path)
        assert response.status_code in (404, 405, 422), (path, response.status_code)
        if response.status_code == 422:
            # Must be a path-parsing error (grid_id expects int), not a grid payload.
            assert "grid_id" in json_module.dumps(response.body)


def test_security_token_required_when_configured(tmp_path):
    client, db, exchange, engine = make_app(tmp_path, token="secret")
    grid = engine.create_grid("XRPUSDT", Decimal(90), Decimal(110), 5, capital=1000)
    assert client.get("/api/grids").status_code == 403
    assert client.get(f"/api/grids/{grid['id']}").status_code == 403
    ok = client.get("/api/grids", headers={"X-API-Token": "secret"})
    assert ok.status_code == 200


def test_security_loopback_allowed_without_token_remote_rejected(tmp_path):
    client, db, exchange, engine = make_app(tmp_path, token="")
    assert client.get("/api/grids").status_code == 200
    remote = LocalClient(client.app, host="198.51.100.8")
    assert remote.get("/api/grids").status_code == 403


def test_list_and_detail_do_not_write_to_the_database(tmp_path):
    client, db, exchange, engine = make_app(tmp_path)
    grid = engine.create_grid("XRPUSDT", Decimal(90), Decimal(110), 5, capital=1000)
    before = _counts(db)
    client.get("/api/grids")
    client.get(f"/api/grids/{grid['id']}")
    client.get(f"/api/grids/{grid['id']}/operations")
    client.get(f"/api/grids/{grid['id']}/events")
    client.get(f"/api/grids/{grid['id']}/daily")
    client.get(f"/api/grids/{grid['id']}/equity")
    client.get("/api/monitor/status")
    after = _counts(db)
    assert before == after


def test_price_as_of_present_when_testnet_client_available(tmp_path):
    client, db, exchange, engine = make_app(tmp_path)
    grid = engine.create_grid("XRPUSDT", Decimal(90), Decimal(110), 5, capital=1000)
    detail = client.get(f"/api/grids/{grid['id']}").json()
    assert detail["summary"]["price"] is not None
    assert detail["summary"]["price_as_of"] is not None


def test_grid_list_deduplicates_and_caches_market_price_for_same_symbol(tmp_path):
    client, db, exchange, engine = make_app(tmp_path)
    engine.create_grid("XRPUSDT", Decimal(90), Decimal(110), 5, capital=1000, strategy="simple")
    engine.create_grid("XRPUSDT", Decimal(95), Decimal(105), 4, capital=500,
                       strategy="smart", params={"compound_enabled": False})
    original = exchange.get_book_ticker
    calls = []
    def counted(symbol):
        calls.append(symbol)
        return original(symbol)
    exchange.get_book_ticker = counted
    response = client.get("/api/grids")
    assert response.status_code == 200
    assert len([row for row in response.json()["grids"] if row["symbol"] == "XRPUSDT"]) == 2
    assert calls == ["XRPUSDT"]


def test_price_cache_is_isolated_between_fastapi_app_instances(tmp_path):
    first_exchange, second_exchange = FakeExchange(), FakeExchange()
    second_exchange.bid = Decimal("105")
    second_exchange.ask = Decimal("105.01")
    second_exchange.avg = Decimal("105")
    first_path, second_path = tmp_path / "first", tmp_path / "second"
    first_path.mkdir(); second_path.mkdir()
    first_client, _, _, first_engine = make_app(first_path, testnet_client=first_exchange)
    second_client, _, _, second_engine = make_app(second_path, testnet_client=second_exchange)
    first_grid = first_engine.create_grid("XRPUSDT", Decimal(90), Decimal(110), 5, capital=1000)
    second_grid = second_engine.create_grid("XRPUSDT", Decimal(90), Decimal(110), 5, capital=1000)
    first_price = first_client.get(f"/api/grids/{first_grid['id']}").json()["summary"]["price"]
    second_price = second_client.get(f"/api/grids/{second_grid['id']}").json()["summary"]["price"]
    assert first_price == pytest.approx(100.005)
    assert second_price == pytest.approx(105.005)
    assert first_client.app.state.grid_status_price_cache is not second_client.app.state.grid_status_price_cache


def test_daily_api_includes_market_loss_and_returns_unattributed_reconciliation(tmp_path):
    client, db, _, engine = make_app(tmp_path)
    grid = engine.create_grid("XRPUSDT", Decimal(90), Decimal(110), 5, capital=1000)
    db.update_level(grid["id"], 0, pnl=-6)
    for event_type, details in [
        ("SELL_FILLED", {"cycle_pnl": "2"}),
        ("CELL_STOPLOSS", {"realized_pnl": "-5"}),
    ]:
        db.add_grid_event(run_id=None, source="CLI", event_type=event_type,
            grid_id=grid["id"], level_idx=0, details=details, ts=NOW)
    response = client.get(f"/api/grids/{grid['id']}/daily")
    assert response.status_code == 200
    body = response.json()
    assert body["daily"][0]["net_pnl_usdt"] == pytest.approx(-3)
    assert body["daily"][0]["stoploss"] == pytest.approx(-5)
    assert body["unattributed_usdt"] == pytest.approx(-3)
    assert body["unattributed_note"]
    assert body["truncated"] is False


def test_daily_range_reconciliation_requires_full_grid_lifetime(tmp_path):
    client, db, _, engine = make_app(tmp_path)
    grid = engine.create_grid("XRPUSDT", Decimal(90), Decimal(110), 5, capital=1000)
    created = datetime.now(timezone.utc) - timedelta(days=10)
    db.update_grid(grid["id"], created_at=created.replace(tzinfo=None))
    db.update_level(grid["id"], 0, pnl=-5)
    db.add_grid_event(run_id=None, source="CLI", event_type="SELL_FILLED", grid_id=grid["id"],
        level_idx=0, details={"cycle_pnl": "4"}, ts=(created + timedelta(days=1)).replace(tzinfo=None))
    db.add_grid_event(run_id=None, source="CLI", event_type="CELL_STOPLOSS", grid_id=grid["id"],
        level_idx=0, details={"realized_pnl": "-6"}, ts=(datetime.now(timezone.utc) - timedelta(days=1)).replace(tzinfo=None))

    partial = client.get(f"/api/grids/{grid['id']}/daily", query={"range": "7d"}).json()
    assert partial["unattributed_usdt"] is None
    assert partial["unattributed_note"] == "conciliación solo disponible cuando el rango cubre toda la vida del grid"
    assert partial["net_realized_usdt"] == pytest.approx(-5)
    assert partial["net_realized_label"] == "total de vida del grid"

    full = client.get(f"/api/grids/{grid['id']}/daily", query={"range": "30d"}).json()
    assert full["unattributed_usdt"] == pytest.approx(-3)
    assert full["unattributed_note"] == "polvo barrido, ajustes o eventos fuera de la ventana consultada"
    assert sum(row["net_pnl_usdt"] for row in full["daily"]) == pytest.approx(-2)


def test_daily_api_disables_reconciliation_when_event_query_truncates(tmp_path, monkeypatch):
    client, db, _, engine = make_app(tmp_path)
    grid = engine.create_grid("XRPUSDT", Decimal(90), Decimal(110), 5, capital=1000)
    db.update_grid(grid["id"], created_at=(datetime.now(timezone.utc) - timedelta(days=3)).replace(tzinfo=None))
    original = db.list_grid_events
    def truncated_events(*, grid_id=None, event_type=None, limit=100):
        if event_type == "SELL_FILLED":
            return [{"event_type": event_type, "grid_id": grid_id, "id": idx,
                     "ts": datetime.now(timezone.utc), "details": {"cycle_pnl": "1"}}
                    for idx in range(5001)]
        return original(grid_id=grid_id, event_type=event_type, limit=limit)
    monkeypatch.setattr(db, "list_grid_events", truncated_events)
    body = client.get(f"/api/grids/{grid['id']}/daily", query={"range": "12m"}).json()
    assert body["truncated"] is True
    assert body["unattributed_usdt"] is None
    assert body["unattributed_note"] == "conciliación solo disponible cuando el rango cubre toda la vida del grid"


def test_price_null_with_unavailable_reason_when_no_testnet_client(tmp_path):
    client, db, exchange, engine = make_app(tmp_path)
    client.app.state.testnet_client = None
    grid = engine.create_grid("XRPUSDT", Decimal(90), Decimal(110), 5, capital=1000)
    detail = client.get(f"/api/grids/{grid['id']}").json()
    assert detail["summary"]["price"] is None
    listing = client.get("/api/grids").json()
    assert listing["totals"]["free_usdt"] is None
    assert listing["totals"]["free_usdt_unavailable_reason"] is not None


def test_operations_events_daily_equity_endpoints_respond(tmp_path):
    client, db, exchange, engine = make_app(tmp_path)
    grid = engine.create_grid("XRPUSDT", Decimal(90), Decimal(110), 5, capital=1000)
    grid_id = grid["id"]
    assert client.get(f"/api/grids/{grid_id}/operations").status_code == 200
    assert client.get(f"/api/grids/{grid_id}/events").status_code == 200
    assert client.get(f"/api/grids/{grid_id}/daily").status_code == 200
    assert client.get(f"/api/grids/{grid_id}/equity").status_code == 200


def test_monitor_status_endpoint(tmp_path):
    client, db, exchange, engine = make_app(tmp_path)
    response = client.get("/api/monitor/status")
    assert response.status_code == 200
    body = response.json()
    assert "last_run" in body
    assert body["contract_version"] is None
    assert body["contract_version_unavailable_reason"]


def test_fastapi_application_registers_grid_status_routes():
    from api.main import app as production_app
    paths = {route.path for route in production_app.routes}
    assert "/api/grids" in paths
    assert "/api/grids/{grid_id}" in paths
    assert "/api/monitor/status" in paths
    assert "/api/grids/scan" in paths
    assert "/api/grids/open" in paths
