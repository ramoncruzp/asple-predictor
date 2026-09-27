import asyncio
import json
from datetime import datetime, timezone

import numpy as np
import pandas as pd
from fastapi import FastAPI

from api.routes.predictions import router


class FakeClient:
    def get_historical_klines(self, symbol, interval, lookback_days):
        rng = np.random.default_rng(7341)
        timestamps = pd.date_range(
            end=datetime.now(timezone.utc), periods=300, freq="1h", tz="UTC"
        )
        close = 100 + np.cumsum(rng.normal(0, 0.2, size=300))
        return pd.DataFrame(
            {
                "timestamp": timestamps,
                "open": close - 0.05,
                "high": close + 0.2,
                "low": close - 0.2,
                "close": close,
                "volume": rng.uniform(10, 100, size=300),
            }
        )


class FakeEnsemble:
    def __init__(self):
        self.predict_calls = 0
        self.predict_and_save_calls = 0

    def predict(self, symbol, interval, df):
        self.predict_calls += 1
        return {
            "consensus_signal": "ALCISTA",
            "consensus_probability_up": 0.7,
            "agreement_count": 2,
            "weights": {"model_a": 0.5, "model_b": 0.5},
        }

    def predict_and_save(self, symbol, interval, df):
        self.predict_and_save_calls += 1
        raise AssertionError("predict_and_save no debe llamarse desde el endpoint")


class FakeLoop:
    def __init__(self, latest=None):
        self.latest = latest or {}


def make_client(latest=None):
    app = FastAPI()
    app.include_router(router, prefix="/api/predictions")
    app.state.client = FakeClient()
    app.state.ensemble = FakeEnsemble()
    app.state.prediction_loop = FakeLoop(latest)
    return app


def get(app, path, query_string=b""):
    async def request():
        messages = []
        request_sent = False

        async def receive():
            nonlocal request_sent
            if not request_sent:
                request_sent = True
                return {"type": "http.request", "body": b"", "more_body": False}
            return {"type": "http.disconnect"}

        async def send(message):
            messages.append(message)

        await app(
            {
                "type": "http",
                "asgi": {"version": "3.0", "spec_version": "2.3"},
                "http_version": "1.1",
                "method": "GET",
                "scheme": "http",
                "path": path,
                "raw_path": path.encode(),
                "query_string": query_string,
                "root_path": "",
                "headers": [],
                "client": ("testclient", 50000),
                "server": ("testserver", 80),
            },
            receive,
            send,
        )
        body = b"".join(message.get("body", b"") for message in messages)
        return json.loads(body)

    return asyncio.run(request())


def test_consensus_without_cached_prediction_uses_predict_only():
    app = make_client()

    first = get(app, "/api/predictions/consensus")
    second = get(app, "/api/predictions/consensus")

    assert first["persisted"] is False
    assert second["persisted"] is False
    assert app.state.ensemble.predict_calls == 2
    assert app.state.ensemble.predict_and_save_calls == 0


def test_consensus_uses_cached_result_without_mutating_it():
    cached = {
        "consensus_signal": "ALCISTA",
        "consensus_probability_up": 0.7,
        "agreement_count": 2,
        "weights": {"model_a": 0.5, "model_b": 0.5},
    }
    app = make_client({("XRPUSDT", "1h"): cached})

    response = get(app, "/api/predictions/consensus")

    assert response["persisted"] is True
    assert "candles" not in cached
    assert "indicators" not in cached
    assert "persisted" not in cached
    assert app.state.ensemble.predict_calls == 0
    assert app.state.ensemble.predict_and_save_calls == 0


def test_consensus_for_inactive_pair_does_not_call_predictor():
    app = make_client()

    response = get(app, "/api/predictions/consensus", b"symbol=BTCUSDT&interval=1h")

    assert response["model_available"] is False
    assert response["consensus_signal"] is None
    assert response["consensus_probability_up"] is None
    assert response["persisted"] is False
    assert app.state.ensemble.predict_calls == 0
    assert app.state.ensemble.predict_and_save_calls == 0
