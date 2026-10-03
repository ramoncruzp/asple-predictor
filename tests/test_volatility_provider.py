from __future__ import annotations

from datetime import datetime, timedelta, timezone
from math import exp, sqrt
from types import SimpleNamespace

import pytest

from grid.volatility_provider import VolatilityProvider


NOW = datetime(2026, 9, 29, 12, tzinfo=timezone.utc)


class FakeDB:
    def __init__(self, rows=None, error=None):
        self.rows = rows or []
        self.error = error

    def get_latest_vol_forecasts(self, symbol):
        if self.error:
            raise self.error
        return self.rows


def forecast_row(*, horizon=24, model="NexoHAR", made=None, forecast=None, value=-5.0, champion=True):
    return {
        "symbol": "XRPUSDT", "horizon_h": horizon, "model_name": model,
        "forecast_at": forecast or NOW - timedelta(minutes=10),
        "made_at": made or NOW - timedelta(minutes=5),
        "pred_logvol_cal": value, "is_champion": champion,
    }


def test_provider_converts_calibrated_hourly_logvol_to_24h_fraction_and_regime():
    db = FakeDB([forecast_row(value=-5.0)])
    provider = VolatilityProvider(db, manifest={"regime_percentiles_24h": {"p33": 0.001, "p66": 0.02}}, clock=lambda: NOW)

    view = provider.get("xrp/usdt")

    assert view is not None
    assert view.sigma_24h == pytest.approx(exp(-5.0) * sqrt(24))
    assert view.stale is False
    assert view.regime == "NORMAL"
    assert view.as_of == NOW - timedelta(minutes=10)


def test_provider_returns_none_for_stale_forecast():
    db = FakeDB([forecast_row(forecast=NOW - timedelta(hours=2, seconds=1))])
    provider = VolatilityProvider(db, clock=lambda: NOW)
    assert provider.get("XRPUSDT") is None
    assert provider.last_reason == "stale"


def test_provider_returns_none_for_unsupported_symbol_or_missing_champion():
    provider = VolatilityProvider(FakeDB([forecast_row()]), clock=lambda: NOW)
    assert provider.get("BTCUSDT") is None
    assert provider.last_reason == "data_client_unavailable"
    assert VolatilityProvider(FakeDB([]), clock=lambda: NOW).get("XRPUSDT") is None


def test_provider_catches_and_logs_database_exception(caplog):
    provider = VolatilityProvider(FakeDB(error=RuntimeError("database unavailable")), clock=lambda: NOW)
    assert provider.get("XRPUSDT") is None
    assert provider.last_reason == "exception"
    assert "volatility lookup failed" in caplog.text


class FakeMarketData:
    def __init__(self, closes):
        self.closes = closes
        self.calls = 0

    def get_historical_klines(self, symbol, interval, lookback_days):
        self.calls += 1
        return SimpleNamespace(__getitem__=None) if False else CloseFrame(self.closes)


class CloseFrame:
    def __init__(self, closes): self.closes = closes
    def __getitem__(self, name):
        assert name == "close"
        return SimpleNamespace(tolist=lambda: self.closes)


def _closes(amplitude):
    import math
    values = [100.0]
    for index in range(168):
        values.append(values[-1] * math.exp(amplitude if index % 2 else -amplitude))
    return values


def test_realized_sigma_orders_agitated_above_calm_and_requires_100_bars():
    calm = VolatilityProvider(FakeDB(), clock=lambda: NOW,
                              data_client=FakeMarketData(_closes(.001))).get("BTCUSDT")
    agitated = VolatilityProvider(FakeDB(), clock=lambda: NOW,
                                  data_client=FakeMarketData(_closes(.02))).get("ETHUSDT")
    assert calm.source == agitated.source == "realized"
    assert agitated.sigma_24h > calm.sigma_24h
    client = FakeMarketData(_closes(.01)[:80])
    provider = VolatilityProvider(FakeDB(), clock=lambda: NOW, data_client=client)
    assert provider.get("SOLUSDT") is None
    assert provider.last_reason == "insufficient_bars"


def test_realized_sigma_cache_observes_30_minute_ttl():
    now = [NOW]
    client = FakeMarketData(_closes(.01))
    provider = VolatilityProvider(FakeDB(), clock=lambda: now[0], data_client=client)
    first = provider.get("BTCUSDT")
    assert provider.get("BTCUSDT") is first and client.calls == 1
    now[0] += timedelta(minutes=31)
    provider.get("BTCUSDT")
    assert client.calls == 2


def test_realized_failure_cache_retries_after_90_seconds():
    now = [NOW]
    class Recovering:
        calls = 0
        def get_historical_klines(self, *_args, **_kwargs):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("temporary")
            return CloseFrame(_closes(.01))
    client = Recovering()
    provider = VolatilityProvider(FakeDB(), clock=lambda: now[0], data_client=client)
    assert provider.get("BTCUSDT") is None
    now[0] += timedelta(seconds=89)
    assert provider.get("BTCUSDT") is None and client.calls == 1
    now[0] += timedelta(seconds=2)
    assert provider.get("BTCUSDT") is not None and client.calls == 2


def test_realized_cache_is_safe_for_concurrent_callers():
    from concurrent.futures import ThreadPoolExecutor
    client = FakeMarketData(_closes(.01))
    provider = VolatilityProvider(FakeDB(), clock=lambda: NOW, data_client=client)
    with ThreadPoolExecutor(max_workers=8) as pool:
        views = list(pool.map(lambda _index: provider.get("BTCUSDT"), range(24)))
    assert all(view is not None for view in views)
    assert client.calls == 1
