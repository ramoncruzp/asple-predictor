from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest
from fastapi import FastAPI

from api.routes import grid_advisor
from grid.sim.runner import FILTERS
from tests.test_grid_status_api import LocalClient


def candles():
    x = np.arange(420, dtype=float)
    close = 100 + 4 * np.sin(x / 8) + .02 * x
    return pd.DataFrame({"close": close, "high": close + .8, "low": close - .8,
                         "open": close - .1, "volume": np.full(len(x), 1000)})


class Market:
    def __init__(self):
        self.frame = candles()

    def get_historical_klines(self, symbol, interval, lookback_days):
        return self.frame


def make_client():
    frame = candles()
    app = FastAPI()
    app.include_router(grid_advisor.router, prefix="/api/grid")
    app.state.client = Market()
    app.state.settings = SimpleNamespace(scanner_fee_pct=.1, scanner_min_spacing_pct=.8,
                                         scanner_timeout_seconds=10)
    class Scan:
        settings = app.state.settings
        def clock(self): return 1
        def _market(self, symbol, capital, deadline):
            mid = float(frame.close.iloc[-1])
            return ({"bid": mid - .01, "ask": mid + .01, "klines_1h": frame}, FILTERS)
    app.state.grid_scan_service = Scan()
    app.state.prediction_loop = SimpleNamespace(latest={})
    return LocalClient(app)


def test_recommendation_uses_editable_margin_and_named_risk_limits():
    client = make_client()
    low = client.get("/api/grid/recommend", query={"symbol": "ADAUSDT", "capital": 1000,
        "risk": "low", "days": 90, "margin_target_pct": .7}).body
    high = client.get("/api/grid/recommend", query={"symbol": "ADAUSDT", "capital": 1000,
        "risk": "high", "days": 90, "margin_target_pct": .5}).body
    assert high["recommended_floor"] < low["recommended_floor"]
    assert low["risk"]["max_range_pct"] == 25
    assert high["risk"]["max_range_pct"] == 70
    assert low["margin_target_pct"] == .7
    assert "dust_estimate_pct" in low and "net_margin_pct" in low
    assert low["prediction_signal"] is None
    assert low["range_position_warning"] == "El precio está cerca del techo: casi todo el capital quedaría en compras"
    assert low["simulations"]["label"] == "histórico, no promesa de resultado"
    assert set(low["simulations"]["strategies"]) == {"simple", "smart"}


def test_ada_100_usdt_advisor_target_uses_gross_margin_and_keeps_dust_informational(monkeypatch):
    def evaluated(n, width_pct, capital, mid, filters, fee_pct, min_spacing_pct, min_cell_usdt):
        gross = .742 if n == 18 else .69
        return {"n": n, "spacing_pct": .942 if n == 18 else .89,
            "cell_usdt": capital / n, "dust_pct": .46,
            "edge_gross_pct": gross, "net_edge_pct": gross - .46,
            "cell_ok": True, "spacing_ok": gross - .46 >= min_spacing_pct}

    monkeypatch.setattr(grid_advisor, "evaluate_levels", evaluated)
    result = make_client().get("/api/grid/recommend", query={"symbol": "ADAUSDT",
        "capital": 100, "risk": "low", "days": 90, "margin_target_pct": .7}).body

    assert result["suggested_grids"] == 18
    assert result["target_met"] is True
    assert result["edge_gross_pct"] == pytest.approx(.742)
    assert result["net_margin_pct"] == pytest.approx(.282)
    assert result["net_per_cycle_usdt"] == pytest.approx(100 / 18 * .742 / 100)
    assert result["net_per_cycle_after_dust_usdt"] == pytest.approx(100 / 18 * .282 / 100)
    assert result["estimated_cycles_to_target"] == 17


def test_realized_volatility_reports_two_sigma_band_for_any_symbol():
    result = make_client().get("/api/grid/volatility", query={"symbol": "ADAUSDT", "days": 30}).body
    assert result["source"].startswith("volatilidad realizada")
    assert result["samples"] == 419
    assert result["range_2sigma"][0] < result["price"] < result["range_2sigma"][1]
