from __future__ import annotations

from decimal import Decimal
import asyncio
import json as json_module
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest
from fastapi import FastAPI

from api.routes.grids import router
from database.db_manager import DBManager
from grid.engine import GridEngine
from grid.levels import GridConfigError
from grid.scan_service import GridScanService
from tests.grid_fakes import FakeExchange, fake_symbol_info


class Public:
    def get_symbol_status(self, symbol): return "TRADING"
    def get_24h_stats(self, symbol): return {"volume_24h_quote": 5_000_000}
    def get_symbol_info(self, symbol): return fake_symbol_info()
    def get_book_ticker(self, symbol): return {"bid_price": 99.99, "ask_price": 100.01}
    def get_historical_klines(self, symbol, interval, days):
        count = 30 * 24 * (12 if interval == "5m" else 1)
        i = np.arange(count)
        return pd.DataFrame({"close": 100 + 3 * np.sin(i / (55 if interval == "5m" else 4))})


class Exchange(FakeExchange):
    def __init__(self):
        super().__init__()
        self.client = SimpleNamespace(testnet=True)
        self.testnet = True


class ScanService:
    def __init__(self):
        self.public = Public()
        self.last_scan = None
        self.clock = lambda: 0
        self.settings = SimpleNamespace(scanner_timeout_seconds=60, scanner_fee_pct=.1,
                                        scanner_min_spacing_pct=.8)

    def scan(self, **kwargs):
        result = {"results": [], "data_source": "Binance public spot market data (read-only)",
                  "warning": "Los precios de ejecución en Testnet difieren del mercado público."}
        self.last_scan = result
        return result

    def _market(self, symbol, capital, deadline):
        from data.exchange_filters import SymbolFilters
        market = {"bid": 99.99, "ask": 100.01, "klines_1h": self.public.get_historical_klines(symbol, "1h", 30)}
        return market, SymbolFilters.from_symbol_info(fake_symbol_info())


def app(tmp_path, *, token="", maximum=5, balance="100000"):
    db = DBManager(f"sqlite:///{tmp_path}/api.db")
    db.add_or_reactivate_coin("XRPUSDT")
    exchange = Exchange()
    exchange.free_usdt = Decimal(balance)
    settings = SimpleNamespace(max_grids_simultaneos=maximum, usdt_por_grid=100,
        capital_max_por_nivel_pct=.30, grid_min_step_pct=.003, grid_api_token=token,
        scanner_timeout_seconds=60, scanner_fee_pct=.1, scanner_min_spacing_pct=.8)
    engine = GridEngine(db, exchange, settings)
    service = ScanService()
    api = FastAPI()
    api.include_router(router, prefix="/api/grids")
    api.state.db, api.state.settings = db, settings
    api.state.grid_engine, api.state.testnet_client = engine, exchange
    api.state.vol_provider = SimpleNamespace(get=lambda symbol: SimpleNamespace(sigma_24h=.01))
    api.state.grid_scan_service = service
    return LocalClient(api), db, exchange


class Response:
    def __init__(self, status, body):
        self.status_code, self.body = status, body
        self.text = json_module.dumps(body)
    def json(self): return self.body


class LocalClient:
    """Tiny ASGI test client using only the standard library and loopback scope."""
    def __init__(self, app, host="127.0.0.1"): self.app, self.host = app, host
    def post(self, path, json=None, headers=None):
        payload = b"" if json is None else json_module.dumps(json).encode()
        messages, sent = [], False
        async def receive():
            nonlocal sent
            if not sent:
                sent = True
                return {"type": "http.request", "body": payload, "more_body": False}
            return {"type": "http.disconnect"}
        async def send(message): messages.append(message)
        hdrs = [(b"content-type", b"application/json")]
        hdrs += [(key.lower().encode(), value.encode()) for key, value in (headers or {}).items()]
        async def call():
            await self.app({"type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"},
                "http_version": "1.1", "method": "POST", "scheme": "http", "path": path,
                "raw_path": path.encode(), "query_string": b"", "root_path": "", "headers": hdrs,
                "client": (self.host, 50000), "server": ("127.0.0.1", 80)}, receive, send)
        asyncio.run(call())
        status = next(message["status"] for message in messages if message["type"] == "http.response.start")
        raw = b"".join(message.get("body", b"") for message in messages if message["type"] == "http.response.body")
        return Response(status, json_module.loads(raw) if raw else None)


def payload(**updates):
    value = {"symbol": "XRPUSDT", "strategy": "simple", "capital": 1000,
             "range_low": 90, "range_high": 110, "n_levels": 5}
    value.update(updates)
    return value


def test_scan_authentication_and_fixed_market_warning(tmp_path):
    client, _, _ = app(tmp_path, token="local-secret")
    assert client.post("/api/grids/scan", json={"capital": 100}).status_code == 403
    response = client.post("/api/grids/scan", headers={"X-API-Token": "local-secret"},
                           json={"capital": 100})
    assert response.status_code == 200
    assert response.json()["data_source"].startswith("Binance public")
    assert "Testnet difieren" in response.json()["warning"]
    public_path = tmp_path / "public"
    public_path.mkdir()
    local, _, _ = app(public_path)
    remote = LocalClient(local.app, host="198.51.100.8")
    assert remote.post("/api/grids/scan", json={"capital": 100}).status_code == 403


def test_dry_run_builds_plan_without_exchange_orders_and_confirm_is_required(tmp_path):
    client, _, exchange = app(tmp_path)
    response = client.post("/api/grids/open", json=payload())
    assert response.status_code == 200, response.text
    assert response.json()["dry_run"] is True
    assert response.json()["guards"]["exchange_will_be_called"] is False
    assert exchange.create_calls == []
    assert client.post("/api/grids/open", json=payload(dry_run=False)).status_code == 422
    client.app.state.grid_engine = None
    client.app.state.testnet_client = None
    assert client.post("/api/grids/open", json=payload()).status_code == 200


def test_executing_open_requires_confirm_emits_audit_and_rejects_second_symbol_grid(tmp_path):
    client, db, _ = app(tmp_path)
    client.app.state.grid_scan_service.last_scan = {"results": [{"symbol": "XRPUSDT", "eligible": True,
        "score": .8, "reasons": ["spread dentro del umbral"]}]}
    missing_confirmation = client.post("/api/grids/open", json=payload(dry_run=False, confirm=False))
    assert missing_confirmation.status_code == 422
    missing_plan = client.post("/api/grids/open", json=payload(dry_run=False, confirm=True,
        range_low=None, range_high=None, n_levels=None))
    assert missing_plan.status_code == 422
    assert missing_plan.json()["detail"] == "ejecuta primero dry_run y reenvía rango y niveles."
    body = payload(dry_run=False, confirm=True, from_scan=True)
    opened = client.post("/api/grids/open", json=body)
    assert opened.status_code == 200, opened.text
    event = db.list_grid_events(grid_id=opened.json()["grid_id"], event_type="GRID_OPEN_API")[0]
    assert event["details"]["who"] == "api"
    assert event["details"]["scan_snapshot"]["reasons"] == ["spread dentro del umbral"]
    duplicate = client.post("/api/grids/open", json=body)
    assert duplicate.status_code == 409


def test_grid_cap_balance_and_schema_validation_return_clear_status(tmp_path):
    client, _, _ = app(tmp_path, maximum=0)
    assert client.post("/api/grids/open", json=payload()).status_code == 409
    client, _, _ = app(tmp_path, balance="0")
    response = client.post("/api/grids/open", json=payload(dry_run=False, confirm=True))
    assert response.status_code == 422 and "balance" in response.json()["detail"]
    invalid = client.post("/api/grids/open", json=payload(unexpected=True))
    assert invalid.status_code == 422


def test_token_failure_and_invalid_target_params_are_rejected(tmp_path):
    client, _, _ = app(tmp_path, token="secret")
    assert client.post("/api/grids/open", json=payload()).status_code == 403
    response = client.post("/api/grids/open", headers={"X-API-Token": "secret"},
        json=payload(target_pct=101))
    assert response.status_code == 422
    simple_params = client.post("/api/grids/open", headers={"X-API-Token": "secret"},
        json=payload(params={"pause_enter_prob": 0.1}))
    assert simple_params.status_code == 422


def test_smart_target_params_are_validated_and_passed_to_existing_engine(tmp_path):
    client, db, _ = app(tmp_path)
    response = client.post("/api/grids/open", json=payload(strategy="smart", target_pct=5,
        dry_run=False, confirm=True))
    assert response.status_code == 200, response.text
    event = db.list_grid_events(grid_id=response.json()["grid_id"], event_type="GRID_OPEN_API")[0]
    assert event["details"]["params"]["target_pct"] == 5


def test_smart_open_rejects_missing_sigma_in_dry_run_and_confirm(tmp_path):
    client, _db, _exchange = app(tmp_path)
    client.app.state.vol_provider = SimpleNamespace(get=lambda symbol: None)
    preview = client.post("/api/grids/open", json=payload(strategy="smart"))
    confirm = client.post("/api/grids/open", json=payload(strategy="smart", dry_run=False, confirm=True))
    assert preview.status_code == 422 and "sigma disponible" in preview.json()["detail"]
    assert confirm.status_code == 422 and "sigma disponible" in confirm.json()["detail"]


def test_public_binance_string_book_shape_scans_dry_runs_and_reuses_plan(tmp_path):
    class Raw:
        def get_symbol_info(self, symbol):
            info = fake_symbol_info()
            info["filters"] = [
                {"filterType": "PRICE_FILTER", "minPrice": "0.00000001", "maxPrice": "1", "tickSize": "0.00000001"},
                {"filterType": "LOT_SIZE", "minQty": "1", "maxQty": "10000000000", "stepSize": "1"},
                {"filterType": "NOTIONAL", "minNotional": "5", "applyMinToMarket": True},
                {"filterType": "MAX_NUM_ORDERS", "maxNumOrders": 200},
            ]
            return info
        def get_orderbook_ticker(self, symbol):
            return {"bidPrice": "0.00001000", "askPrice": "0.00001001"}

    class PublicClient:
        def __init__(self): self.client = Raw()
        def get_symbol_status(self, symbol): return "TRADING"
        def get_24h_stats(self, symbol): return {"volume_24h_quote": 5_000_000}
        def get_historical_klines(self, symbol, interval, days):
            frame = Public().get_historical_klines(symbol, interval, days)
            frame["close"] = .00001 + .0000005 * np.sin(np.arange(len(frame)) / 12)
            frame["open"] = frame["high"] = frame["low"] = frame["close"]
            return frame

    client, db, exchange = app(tmp_path)
    service = GridScanService(db, PublicClient(), client.app.state.settings, sleep=lambda _: None)
    client.app.state.grid_scan_service = service
    scanned = client.post("/api/grids/scan", json={"symbols": ["XRPUSDT"], "capital": 1000})
    assert scanned.status_code == 200
    assert len([row for row in scanned.json()["results"] if row.get("eligible")]) == 1, scanned.text
    planned = client.post("/api/grids/open", json=payload(range_low=None, range_high=None, n_levels=None))
    assert planned.status_code == 200, planned.text
    plan = planned.json()
    class RecordingEngine:
        def create_grid(self, symbol, low, high, levels, *, capital, strategy, params):
            row = db.create_grid_with_levels({"symbol": symbol, "range_low": low,
                "range_high": high, "n_levels": levels, "capital_total": capital,
                "status": "ACTIVE", "environment": "testnet", "strategy": strategy,
                "params": params}, [])
            self.call = (symbol, low, high, levels)
            return {"id": row["id"], "status": "ACTIVE"}
    recorder = RecordingEngine()
    client.app.state.grid_engine = recorder
    opened = client.post("/api/grids/open", json=payload(dry_run=False, confirm=True,
        range_low=plan["range_low"], range_high=plan["range_high"], n_levels=plan["n_levels"]))
    assert opened.status_code == 200, opened.text
    record = db.get_grid(opened.json()["grid_id"])
    assert Decimal(str(record["range_low"])) == Decimal(plan["range_low"])
    assert Decimal(str(record["range_high"])) == Decimal(plan["range_high"])
    assert int(record["n_levels"]) == plan["n_levels"]
    assert recorder.call == ("XRPUSDT", Decimal(plan["range_low"]),
        Decimal(plan["range_high"]), plan["n_levels"])


def test_engine_error_texts_for_api_status_mapping_are_stable(tmp_path):
    client, db, exchange = app(tmp_path)
    settings = SimpleNamespace(usdt_por_grid=100, max_grids_simultaneos=5,
        capital_max_por_nivel_pct=.30, grid_min_step_pct=.003)
    engine = GridEngine(db, exchange, settings)
    engine.create_grid("XRPUSDT", Decimal(90), Decimal(110), 5, capital=1000)
    with pytest.raises(GridConfigError, match="^sell level conflict:"):
        engine.create_grid("XRPUSDT", Decimal(90), Decimal(110), 5, capital=1000)
    db2 = DBManager(f"sqlite:///{tmp_path / 'cap.db'}")
    db2.add_or_reactivate_coin("XRPUSDT")
    capped = GridEngine(db2, exchange, SimpleNamespace(usdt_por_grid=100,
        max_grids_simultaneos=0, capital_max_por_nivel_pct=.30, grid_min_step_pct=.003))
    with pytest.raises(GridConfigError, match="^maximum simultaneous open grids reached$"):
        capped.create_grid("XRPUSDT", Decimal(90), Decimal(110), 5, capital=1000)


def test_fastapi_application_registers_both_grid_routes():
    from api.main import app as production_app
    paths = {route.path for route in production_app.routes}
    assert "/api/grids/scan" in paths
    assert "/api/grids/open" in paths
