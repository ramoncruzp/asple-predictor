"""Tests for the coin registry API (no network access)."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from urllib.parse import urlsplit

import pytest
from fastapi import FastAPI

from api.routes import coins as coins_module
from config.models_config import ACTIVE_SYMBOL
from database.db_manager import DBManager
from scheduler.prediction_loop import PredictionLoop


class FakeMarketClient:
    def __init__(self, statuses=None, stats=None, supported=None):
        self.statuses = statuses or {}
        self.stats = stats or {}
        self.supported = supported if supported is not None else []
        self.stats_calls = 0
        self.supported_calls = 0

    def get_symbol_status(self, symbol):
        if symbol not in self.statuses:
            return None
        value = self.statuses[symbol]
        if value == "ERROR":
            raise RuntimeError("Binance no respondió")
        return value

    def get_24h_stats(self, symbol):
        self.stats_calls += 1
        if symbol not in self.stats:
            raise RuntimeError("sin datos")
        return self.stats[symbol]

    def get_supported_symbols(self):
        self.supported_calls += 1
        return self.supported


class FakeTestnetClient:
    def __init__(self, statuses=None):
        self.statuses = statuses or {}

    def get_symbol_info(self, symbol):
        status = self.statuses.get(symbol)
        return {"symbol": symbol, "status": status} if status else None


def make_app(fake_client, tmp_path):
    app = FastAPI()
    app.include_router(coins_module.router, prefix="/api/coins")
    app.state.db = DBManager(f"sqlite:///{tmp_path}/test.db")
    app.state.client = fake_client
    app.state.testnet_client = FakeTestnetClient(fake_client.statuses)
    coins_module._AVAILABLE_CACHE["symbols"] = None
    coins_module._AVAILABLE_CACHE["fetched_at"] = 0.0
    return app


def _request(app, method, path, body=None):
    async def run():
        messages = []
        request_sent = False
        payload = b"" if body is None else json.dumps(body).encode()

        async def receive():
            nonlocal request_sent
            if not request_sent:
                request_sent = True
                return {"type": "http.request", "body": payload, "more_body": False}
            return {"type": "http.disconnect"}

        async def send(message):
            messages.append(message)

        headers = [(b"content-type", b"application/json")] if body is not None else []
        parsed_path = urlsplit(path)
        await app(
            {
                "type": "http",
                "asgi": {"version": "3.0", "spec_version": "2.3"},
                "http_version": "1.1",
                "method": method,
                "scheme": "http",
                "path": parsed_path.path,
                "raw_path": parsed_path.path.encode(),
                "query_string": parsed_path.query.encode(),
                "root_path": "",
                "headers": headers,
                "client": ("testclient", 50000),
                "server": ("testserver", 80),
            },
            receive,
            send,
        )
        status = next(m["status"] for m in messages if m["type"] == "http.response.start")
        raw_body = b"".join(m.get("body", b"") for m in messages if m["type"] == "http.response.body")
        parsed = json.loads(raw_body) if raw_body else None
        return status, parsed

    return asyncio.run(run())


def post(app, path, body):
    return _request(app, "POST", path, body)


def delete(app, path):
    return _request(app, "DELETE", path)


def get(app, path):
    return _request(app, "GET", path)


def test_create_all_preserves_existing_prediction(tmp_path):
    db = DBManager(f"sqlite:///{tmp_path}/preexisting.db")
    db.save_prediction({
        "symbol": "XRPUSDT", "interval": "1h", "model_name": "model_a",
        "predicted_at": datetime.now(timezone.utc), "probability_up": 0.6,
        "signal": "ALCISTA", "confidence": "alta", "price_at_prediction": 1.0,
    })
    db2 = DBManager(f"sqlite:///{tmp_path}/preexisting.db")
    rows = db2.get_recent_predictions("XRPUSDT")
    assert len(rows) == 1


def test_post_valid_returns_201(tmp_path):
    fake = FakeMarketClient(statuses={"SOLUSDT": "TRADING"})
    app = make_app(fake, tmp_path)

    status, data = post(app, "/api/coins", {"symbol": "solusdt"})

    assert status == 201
    assert data["symbol"] == "SOLUSDT"
    assert data["active"] == 1
    assert data["added_at"] is not None


def test_post_nonexistent_returns_404_without_row(tmp_path):
    fake = FakeMarketClient(statuses={})
    app = make_app(fake, tmp_path)

    status, data = post(app, "/api/coins", {"symbol": "ABCUSDT"})

    assert status == 404
    assert app.state.db.get_coin("ABCUSDT") is None


def test_post_rejects_symbol_missing_from_testnet(tmp_path):
    fake = FakeMarketClient(statuses={"SOLUSDT": "TRADING"})
    app = make_app(fake, tmp_path)
    app.state.testnet_client = FakeTestnetClient()
    status, data = post(app, "/api/coins", {"symbol": "SOLUSDT"})
    assert status == 422
    assert "existe en Binance pero no en Testnet" in data["detail"]
    assert app.state.db.get_coin("SOLUSDT") is None


def test_post_upstream_runtime_error_returns_503_without_row(tmp_path):
    fake = FakeMarketClient(statuses={"SOLUSDT": "ERROR"})
    app = make_app(fake, tmp_path)

    status, data = post(app, "/api/coins", {"symbol": "SOLUSDT"})

    assert status == 503
    assert app.state.db.get_coin("SOLUSDT") is None


def test_post_non_usdt_pair_returns_422(tmp_path):
    fake = FakeMarketClient()
    app = make_app(fake, tmp_path)

    status, data = post(app, "/api/coins", {"symbol": "ETHBTC"})

    assert status == 422


def test_post_duplicate_active_returns_409(tmp_path):
    fake = FakeMarketClient(statuses={"SOLUSDT": "TRADING"})
    app = make_app(fake, tmp_path)
    post(app, "/api/coins", {"symbol": "SOLUSDT"})

    status, data = post(app, "/api/coins", {"symbol": "SOLUSDT"})

    assert status == 409


def test_post_symbol_with_break_status_returns_422(tmp_path):
    fake = FakeMarketClient(statuses={"SOLUSDT": "BREAK"})
    app = make_app(fake, tmp_path)

    status, data = post(app, "/api/coins", {"symbol": "SOLUSDT"})

    assert status == 422


def test_delete_deactivates_and_preserves_predictions(tmp_path):
    fake = FakeMarketClient(statuses={"SOLUSDT": "TRADING"})
    app = make_app(fake, tmp_path)
    post(app, "/api/coins", {"symbol": "SOLUSDT"})
    app.state.db.save_prediction({
        "symbol": "SOLUSDT", "interval": "1h", "model_name": "model_a",
        "predicted_at": datetime.now(timezone.utc), "probability_up": 0.6,
        "signal": "ALCISTA", "confidence": "alta", "price_at_prediction": 1.0,
    })
    before = len(app.state.db.get_recent_predictions("SOLUSDT"))

    status, data = delete(app, "/api/coins/SOLUSDT")

    after = len(app.state.db.get_recent_predictions("SOLUSDT"))
    assert status == 200
    assert app.state.db.get_coin("SOLUSDT")["active"] == 0
    assert before == after


def test_delete_active_predictor_symbol_returns_409(tmp_path):
    fake = FakeMarketClient()
    app = make_app(fake, tmp_path)
    app.state.db.seed_coin_if_missing(ACTIVE_SYMBOL, "seed")

    status, data = delete(app, f"/api/coins/{ACTIVE_SYMBOL}")

    assert status == 409
    assert app.state.db.get_coin(ACTIVE_SYMBOL)["active"] == 1


def test_delete_nonexistent_returns_404(tmp_path):
    fake = FakeMarketClient()
    app = make_app(fake, tmp_path)

    status, data = delete(app, "/api/coins/SOLUSDT")

    assert status == 404


def test_delete_then_post_reactivates_with_same_added_at(tmp_path):
    fake = FakeMarketClient(statuses={"SOLUSDT": "TRADING"})
    app = make_app(fake, tmp_path)
    _, created = post(app, "/api/coins", {"symbol": "SOLUSDT"})
    delete(app, "/api/coins/SOLUSDT")

    status, reactivated = post(app, "/api/coins", {"symbol": "SOLUSDT"})

    assert status == 201
    assert reactivated["added_at"] == created["added_at"]
    assert reactivated["active"] == 1


def test_get_lists_only_active_with_fake_stats(tmp_path):
    fake = FakeMarketClient(
        statuses={"SOLUSDT": "TRADING", "ADAUSDT": "TRADING"},
        stats={"SOLUSDT": {"price": 150.0, "volume_24h_quote": 1000.0, "change_pct_24h": 2.5}},
    )
    app = make_app(fake, tmp_path)
    post(app, "/api/coins", {"symbol": "SOLUSDT"})
    post(app, "/api/coins", {"symbol": "ADAUSDT"})
    delete(app, "/api/coins/ADAUSDT")

    status, data = get(app, "/api/coins")

    assert status == 200
    assert len(data) == 1
    assert data[0]["symbol"] == "SOLUSDT"
    assert data[0]["price"] == 150.0
    assert data[0]["volume_24h_quote"] == 1000.0
    assert data[0]["change_pct_24h"] == 2.5
    assert data[0]["active"] is True
    assert data[0]["open_grid_id"] is None


def test_get_can_include_inactive_coins(tmp_path):
    fake = FakeMarketClient(statuses={"SOLUSDT": "TRADING"})
    app = make_app(fake, tmp_path)
    post(app, "/api/coins", {"symbol": "SOLUSDT"})
    delete(app, "/api/coins/SOLUSDT")
    status, data = get(app, "/api/coins?include_inactive=true")
    assert status == 200
    assert data[0]["symbol"] == "SOLUSDT"
    assert data[0]["active"] is False


def test_get_with_stats_failure_returns_null_fields(tmp_path):
    fake = FakeMarketClient(statuses={"SOLUSDT": "TRADING"}, stats={})
    app = make_app(fake, tmp_path)
    post(app, "/api/coins", {"symbol": "SOLUSDT"})

    status, data = get(app, "/api/coins")

    assert status == 200
    assert data[0]["price"] is None
    assert data[0]["volume_24h_quote"] is None
    assert data[0]["change_pct_24h"] is None


def test_available_normalizes_sorts_and_caches(tmp_path):
    fake = FakeMarketClient(supported=["ETH/USDT", "BTC/USDT"])
    app = make_app(fake, tmp_path)

    status1, data1 = get(app, "/api/coins/available")
    status2, data2 = get(app, "/api/coins/available")

    assert status1 == 200
    assert data1 == ["BTCUSDT", "ETHUSDT"]
    assert data2 == ["BTCUSDT", "ETHUSDT"]
    assert fake.supported_calls == 1


def test_seed_coin_if_missing_is_idempotent_and_does_not_reactivate(tmp_path):
    db = DBManager(f"sqlite:///{tmp_path}/seed.db")

    db.seed_coin_if_missing("XRPUSDT", "seed note")
    first = db.get_coin("XRPUSDT")
    db.deactivate_coin("XRPUSDT")
    db.seed_coin_if_missing("XRPUSDT", "seed note again")
    second = db.get_coin("XRPUSDT")

    assert first["active"] == 1
    assert second["active"] == 0
    assert second["added_at"] == first["added_at"]


def test_prediction_loop_only_monitors_xrp_after_registering_new_coin(tmp_path):
    fake = FakeMarketClient(statuses={"ETHUSDT": "TRADING"})
    app = make_app(fake, tmp_path)

    post(app, "/api/coins", {"symbol": "ETHUSDT"})

    assert PredictionLoop.SYMBOLS_TO_MONITOR == ["XRPUSDT"]
