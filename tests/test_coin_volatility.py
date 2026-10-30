from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import numpy as np
import pandas as pd
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routes import coins


def test_volatility_endpoint_computes_range_and_percentile_without_database(monkeypatch):
    now = datetime(2026, 10, 29, 12, tzinfo=timezone.utc)
    timestamps = pd.date_range(end=now, periods=181 * 24, freq="h", tz="UTC")
    close = np.full(len(timestamps), 100.0)
    # El rango diario constante hace que el percentil sea determinista.
    frame = pd.DataFrame({"timestamp": timestamps, "open": close, "high": 102.0,
                          "low": 98.0, "close": close})

    class PublicClient:
        def get_historical_klines(self, symbol, interval, lookback_days):
            assert (symbol, interval, lookback_days) == ("XRPUSDT", "1h", 180)
            return frame.copy()

    app = FastAPI()
    app.include_router(coins.router, prefix="/api/coins")
    app.state.client = PublicClient()
    app.state.vol_loop = None
    monkeypatch.setattr(coins, "_volatility_now", lambda: now)
    coins._VOLATILITY_CACHE.clear()
    coins._VOLATILITY_CANDLES_CACHE.clear()
    result = TestClient(app).get("/api/coins/XRPUSDT/volatility?days=7")
    assert result.status_code == 200, result.text
    payload = result.json()
    assert payload["range_usdt"] == 4.0
    assert payload["range_pct"] == 4 / 98 * 100
    assert payload["daily_range_avg_pct"] == 4 / 98 * 100
    assert payload["current_range_percentile_180d"] == 100.0
    assert payload["maximum"]["price"] == 102.0
    assert payload["minimum"]["price"] == 98.0
    assert len(payload["daily_ranges"]) == 8
    assert TestClient(app).get("/api/coins/XRPUSDT/volatility?days=3").status_code == 422
