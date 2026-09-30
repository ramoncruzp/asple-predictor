from __future__ import annotations

from datetime import datetime, timedelta, timezone
from math import exp, sqrt

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
    assert provider.last_reason == "unsupported_symbol"
    assert VolatilityProvider(FakeDB([]), clock=lambda: NOW).get("XRPUSDT") is None


def test_provider_catches_and_logs_database_exception(caplog):
    provider = VolatilityProvider(FakeDB(error=RuntimeError("database unavailable")), clock=lambda: NOW)
    assert provider.get("XRPUSDT") is None
    assert provider.last_reason == "exception"
    assert "volatility lookup failed" in caplog.text
