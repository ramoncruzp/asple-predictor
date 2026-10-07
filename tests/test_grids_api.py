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
import api.routes.grids as grids_api
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
        scanner_timeout_seconds=60, scanner_fee_pct=.1, scanner_min_spacing_pct=.8,
        grid_min_net_margin_pct=0.0)
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
    assert response.json()["testnet_in_range"] is True
    assert response.json()["testnet_price_guard"]["allowed"] is True
    assert exchange.create_calls == []
    assert client.post("/api/grids/open", json=payload(dry_run=False)).status_code == 422
    client.app.state.grid_engine = None
    client.app.state.testnet_client = None
    unavailable = client.post("/api/grids/open", json=payload())
    assert unavailable.status_code == 200
    assert unavailable.json()["testnet_price"] is None
    assert unavailable.json()["testnet_in_range"] is None
    assert unavailable.json()["testnet_price_guard"] == {
        "allowed": None,
        "reason": "No se pudo leer el precio de Testnet; la apertura puede fallar.",
    }


def test_open_still_rejects_margin_target_as_an_extra_field(tmp_path):
    client, _, exchange = app(tmp_path)

    response = client.post("/api/grids/open", json=payload(margin_target_pct=.7))

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert isinstance(detail, list)
    assert detail[0]["loc"][-1] == "margin_target_pct"
    assert detail[0]["type"] == "extra_forbidden"
    assert exchange.create_calls == []


def test_dry_run_testnet_book_price_guard_uses_only_the_read_book_method(tmp_path):
    client, _, exchange = app(tmp_path)
    read_calls = []
    get_book_ticker = exchange.get_book_ticker
    exchange.get_book_ticker = lambda symbol: (read_calls.append(symbol), get_book_ticker(symbol))[1]

    response = client.post("/api/grids/open", json=payload())

    assert response.status_code == 200, response.text
    body = response.json()
    assert Decimal(body["current_price"]) == Decimal("100.0")
    assert Decimal(body["testnet_price"]) == Decimal("100.005")
    assert body["testnet_in_range"] is True
    assert body["testnet_price_guard"] == {"allowed": True, "reason": None}
    assert read_calls == ["XRPUSDT"]
    assert exchange.create_calls == []


def test_dry_run_reports_testnet_outside_range_with_both_prices(tmp_path):
    client, _, exchange = app(tmp_path)
    exchange.move_price("120", "121", "120.5")

    response = client.post("/api/grids/open", json=payload())

    assert response.status_code == 200, response.text
    body = response.json()
    assert Decimal(body["current_price"]) == Decimal("100.0")
    assert Decimal(body["testnet_price"]) == Decimal("120.5")
    assert body["testnet_in_range"] is False
    assert body["testnet_price_guard"]["allowed"] is False
    assert "120.5" in body["testnet_price_guard"]["reason"]
    assert "100.0" in body["testnet_price_guard"]["reason"]
    assert exchange.create_calls == []


def test_dry_run_blocks_testnet_book_with_empty_ask_side(tmp_path):
    client, _, exchange = app(tmp_path)
    exchange.get_book_ticker = lambda _symbol: {
        "bid_price": "0.00000443", "ask_price": "0", "ask_qty": "0"
    }

    response = client.post("/api/grids/open", json=payload())

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["testnet_in_range"] is False
    assert body["testnet_price"] is None
    assert body["testnet_price_guard"]["allowed"] is False
    assert body["testnet_price_guard"]["reason"] == (
        "El libro de Testnet de XRPUSDT no tiene ventas en este momento "
        "(bid 0.00000443 / ask 0); la apertura fallar\u00eda. "
        "Prueba con una moneda m\u00e1s l\u00edquida o reintenta."
    )
    assert exchange.create_calls == []


def test_dry_run_continues_when_testnet_book_cannot_be_read(tmp_path):
    client, _, exchange = app(tmp_path)
    exchange.get_book_ticker = lambda _symbol: (_ for _ in ()).throw(RuntimeError("offline fake"))

    response = client.post("/api/grids/open", json=payload())

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["testnet_price"] is None
    assert body["testnet_in_range"] is None
    assert body["testnet_price_guard"]["allowed"] is None
    assert "No se pudo leer el precio de Testnet" in body["testnet_price_guard"]["reason"]
    assert exchange.create_calls == []


def test_dry_run_reports_server_order_count_distances_and_cell_margins(tmp_path):
    client, _, exchange = app(tmp_path)
    response = client.post("/api/grids/open", json=payload())
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["initial_order_count"] == sum(
        cell["initial_state"] in {"BUY_OPEN", "SELL_OPEN"} for cell in body["cells"])
    assert 0 < body["initial_order_count"] < len(body["cells"])
    assert body["price_in_range"] is True
    assert Decimal(body["distance_to_floor_pct"]) > 0
    assert Decimal(body["distance_to_ceiling_pct"]) > 0
    assert body["min_cell_warning"] is None
    assert Decimal(body["dust_target_pct"]) == Decimal("0.1")
    assert Decimal(body["dust_min_cell_usdt"]) == exchange.filters.step_size * Decimal("100") / Decimal("0.001")
    rows = body["cells"]
    assert len({row["net_margin_pct"] for row in rows}) > 1
    for row in rows:
        buy, sell = Decimal(row["buy_price"]), Decimal(row["sell_price"])
        capital = Decimal(row["capital"])
        gross = Decimal(row["gross_margin_pct"])
        net = Decimal(row["net_margin_pct"])
        dust_pct = exchange.filters.step_size * buy / capital * Decimal(100)
        assert gross == (sell / buy - 1) * Decimal(100) - Decimal("0.2")
        assert net == gross - dust_pct
        assert Decimal(row["gross_margin_usdt"]) == capital * gross / 100
        assert Decimal(row["net_margin_usdt"]) == capital * net / 100
    summary = body["cell_metrics_summary"]
    assert Decimal(summary["gross_margin_usdt"]) == sum(Decimal(r["gross_margin_usdt"]) for r in rows)
    assert Decimal(summary["net_margin_usdt"]) == sum(Decimal(r["net_margin_usdt"]) for r in rows)
    assert Decimal(summary["net_margin_pct"]) == Decimal(summary["net_margin_usdt"]) / Decimal(summary["capital_usdt"]) * 100


def test_preview_position_reports_signed_outside_distances():
    from grid.structure import evaluate_preview_position
    result = evaluate_preview_position("120", "90", "110")
    assert result["price_in_range"] is False
    assert result["distance_to_floor_pct"] == Decimal("25")
    assert result["distance_to_ceiling_pct"] == Decimal("-8.333333333333333333333333333")


def test_minimum_margin_after_fees_is_reported_in_preview_and_blocks_execution(tmp_path):
    client, _, exchange = app(tmp_path)
    client.app.state.settings.grid_min_net_margin_pct=.7
    narrow = payload(range_low="99", range_high="101", n_levels=5)
    preview = client.post("/api/grids/open", json=narrow)
    assert preview.status_code == 200, preview.text
    guard = preview.json()["margin_guard"]
    assert guard["minimum_pct"] == .7 and guard["allowed"] is False
    result = client.post("/api/grids/open", json=payload(range_low="99", range_high="101",
        n_levels=5, dry_run=False, confirm=True))
    assert result.status_code == 422
    assert "mínimo 0.700%" in result.json()["detail"]
    assert exchange.create_calls == []


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
    body = payload(dry_run=False, confirm=True, from_scan=True, capital=10000)
    opened = client.post("/api/grids/open", json=body)
    assert opened.status_code == 200, opened.text
    event = db.list_grid_events(grid_id=opened.json()["grid_id"], event_type="GRID_OPEN_API")[0]
    assert event["details"]["who"] == "api"
    assert event["details"]["scan_snapshot"]["reasons"] == ["spread dentro del umbral"]
    duplicate = client.post("/api/grids/open", json=body)
    assert duplicate.status_code == 409


def test_confirm_rejects_empty_testnet_ask_before_engine_and_logs_snapshot(tmp_path):
    client, db, exchange = app(tmp_path)
    exchange.get_book_ticker = lambda _symbol: {
        "bid_price": "0.00000443", "ask_price": "0", "ask_qty": "0"
    }
    engine = client.app.state.grid_engine
    create_grid = engine.create_grid
    create_calls = []
    engine.create_grid = lambda *args, **kwargs: (create_calls.append((args, kwargs)),
        create_grid(*args, **kwargs))[1]

    response = client.post("/api/grids/open", json=payload(dry_run=False, confirm=True,
        range_low=90, range_high=110, n_levels=5))

    assert response.status_code == 422
    assert "no tiene ventas" in response.json()["detail"]
    assert "bid 0.00000443" in response.json()["detail"]
    assert "ask 0" in response.json()["detail"]
    assert create_calls == []
    events = db.list_grid_events(event_type="GRID_OPEN_REJECTED")
    details = events[-1]["details"]
    assert details["range_low"] == "90"
    assert details["range_high"] == "110"
    assert details["n_levels"] == 5
    assert details["bid"] == "0.00000443"
    assert details["ask"] == "0"
    assert details["mid"] is None


def test_confirm_rejects_testnet_mid_out_of_range_and_logs_mid_snapshot(tmp_path):
    client, db, exchange = app(tmp_path)
    exchange.move_price("120", "121", "120.5")
    engine = client.app.state.grid_engine
    create_grid = engine.create_grid
    create_calls = []
    engine.create_grid = lambda *args, **kwargs: (create_calls.append((args, kwargs)),
        create_grid(*args, **kwargs))[1]

    response = client.post("/api/grids/open", json=payload(dry_run=False, confirm=True))

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert "fuera del rango" in detail
    assert "bid 120" in detail and "ask 121" in detail and "mid 120.5" in detail
    assert "rango 90 a 110" in detail
    assert create_calls == []
    details = db.list_grid_events(event_type="GRID_OPEN_REJECTED")[-1]["details"]
    assert details["range_low"] == "90"
    assert details["range_high"] == "110"
    assert details["n_levels"] == 5
    assert details["bid"] == "120"
    assert details["ask"] == "121"
    assert details["mid"] == "120.5"


def test_grid_cap_balance_and_schema_validation_return_clear_status(tmp_path):
    client, _, _ = app(tmp_path, maximum=0)
    assert client.post("/api/grids/open", json=payload()).status_code == 409
    client, _, _ = app(tmp_path, balance="0")
    response = client.post("/api/grids/open", json=payload(dry_run=False, confirm=True, capital=10000))
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
        dry_run=False, confirm=True, capital=10000))
    assert response.status_code == 200, response.text
    event = db.list_grid_events(grid_id=response.json()["grid_id"], event_type="GRID_OPEN_API")[0]
    assert event["details"]["params"]["target_pct"] == 5


def test_simple_open_accepts_compound_params_and_persists_them(tmp_path):
    client, db, _exchange = app(tmp_path)
    params = {"compound_enabled": True, "compound_ratio": 0.5,
              "compound_max_growth_pct": 25.0}
    preview = client.post("/api/grids/open", json=payload(params=params))
    assert preview.status_code == 200, preview.text
    assert preview.json()["params"] == params
    opened = client.post("/api/grids/open", json=payload(params=params,
        dry_run=False, confirm=True, capital=10000))
    assert opened.status_code == 200, opened.text
    grid = db.get_grid(opened.json()["grid_id"])
    assert grid["strategy"] == "simple"
    assert grid["params"]["compound_enabled"] is True
    assert grid["params"]["compound_ratio"] == 0.5
    assert grid["params"]["compound_max_growth_pct"] == 25.0


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
    plan_mid = (Decimal(plan["range_low"]) + Decimal(plan["range_high"])) / Decimal(2)
    recorder.exchange = SimpleNamespace(get_book_ticker=lambda _symbol: {
        "bid_price": plan_mid - Decimal("0.00000001"),
        "ask_price": plan_mid + Decimal("0.00000001"),
    })
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


def test_margin_guard_blocks_only_cell_floor_or_gross_threshold_and_reports_dust():
    from api.routes.grids import _margin_guard
    from data.exchange_filters import SymbolFilters
    from tests.grid_fakes import fake_symbol_info
    from dataclasses import replace
    settings=SimpleNamespace(scanner_fee_pct=.1,scanner_min_spacing_pct=.8,
        grid_min_margin_after_fees_pct=.7)
    request=SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(settings=settings)))
    filters=replace(SymbolFilters.from_symbol_info(fake_symbol_info()),step_size=Decimal(".01"),
                    min_notional=Decimal("5"))
    # 17.118% / 18 = .951%; gross after fees .751% passes although dust lowers it.
    passing=_margin_guard(request, Decimal(".99049"), Decimal("1.16167"), 18,
                          Decimal("100"), Decimal("1"), filters)
    assert passing["actual_pct"] == pytest.approx(.751)
    assert passing["allowed"] is True and passing["net_after_dust_pct"] > 0
    denied=_margin_guard(request, Decimal(".993"), Decimal("1.119"), 18,
                         Decimal("100"), Decimal("1"), filters)
    assert denied["actual_pct"] == pytest.approx(.5)
    assert denied["allowed"] is False
    dusty_filters=replace(filters,step_size=Decimal(".05"))
    dusty=_margin_guard(request, Decimal(".99049"), Decimal("1.16167"), 18,
                        Decimal("100"), Decimal("1"), dusty_filters)
    assert dusty["dust_warning"] is True
    assert dusty["net_after_dust_pct"] <= 0
    assert dusty["allowed"] is True
    assert "estimated_net_after_dust_nonpositive" not in dusty["reasons"]
    assert dusty["dust_estimate_pct"] > 0


def test_open_dry_run_and_fake_execution_allow_negative_dust_net_when_gross_passes(tmp_path):
    from dataclasses import replace

    client, _, exchange = app(tmp_path)
    filters = replace(exchange.filters, step_size=Decimal("0.5"), min_qty=Decimal("0.5"))
    exchange.filters = filters
    symbol_info = fake_symbol_info()
    for item in symbol_info["filters"]:
        if item["filterType"] == "LOT_SIZE":
            item["stepSize"] = "0.5"
            item["minQty"] = "0.5"
    exchange.get_symbol_info = lambda symbol: symbol_info
    client.app.state.settings.grid_min_margin_after_fees_pct = .7
    client.app.state.grid_scan_service._market = lambda *args: (
        {"bid": Decimal("99.99"), "ask": Decimal("100.01"),
         "klines_1h": client.app.state.grid_scan_service.public.get_historical_klines("XRPUSDT", "1h", 30)},
        filters)
    body = payload(capital=1000)
    preview = client.post("/api/grids/open", json=body)
    assert preview.status_code == 200, preview.text
    guard = preview.json()["margin_guard"]
    assert guard["edge_gross_pct"] >= .7
    assert guard["net_after_dust_pct"] <= 0
    assert guard["allowed"] is True and guard["dust_warning"] is True
    opened = client.post("/api/grids/open", json=payload(capital=1000,
        dry_run=False, confirm=True))
    assert opened.status_code == 200, opened.text
    assert exchange.create_calls


def test_open_rejects_nonready_coin_and_records_same_rejection(monkeypatch,tmp_path):
    client,db,_=app(tmp_path)
    db.add_or_reactivate_coin("ADAUSDT")
    monkeypatch.setattr(grids_api,"coin_is_ready",lambda *_:False)
    response=client.post("/api/grids/open",json=payload(symbol="ADAUSDT"))
    assert response.status_code==409
    assert "La moneda ADAUSDT aún no está lista: pendiente" in response.json()["detail"]
    events=db.list_grid_events(event_type="GRID_OPEN_REJECTED")
    assert events and "ADAUSDT" in events[-1]["reason"]
