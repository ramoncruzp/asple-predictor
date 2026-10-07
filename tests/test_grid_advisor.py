from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest
from fastapi import FastAPI

from api.routes import grid_advisor, volatility as volatility_route
from config.models_config import VOL_CHAMPIONS
from grid.sim.runner import FILTERS
from grid.policy import DEFAULT_SMART_PARAMS, break_prob
from tests.test_grid_status_api import LocalClient


def candles():
    x = np.arange(420, dtype=float)
    close = 100 + 4 * np.sin(x / 8) + .02 * x
    return pd.DataFrame({"close": close, "high": close + .8, "low": close - .8,
                         "open": close - .1, "volume": np.full(len(x), 1000),
                         "timestamp": pd.date_range("2026-09-01", periods=len(x), freq="h", tz="UTC")})


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
        "risk": "low", "days": 90, "margin_target_pct": .7, "range_mode": "estructural"}).body
    high = client.get("/api/grid/recommend", query={"symbol": "ADAUSDT", "capital": 1000,
        "risk": "high", "days": 90, "margin_target_pct": .5, "range_mode": "estructural"}).body
    assert high["recommended_floor"] < low["recommended_floor"]
    assert low["risk"]["max_range_pct"] == 25
    assert high["risk"]["max_range_pct"] == 70
    assert low["margin_target_pct"] == .7
    assert "dust_estimate_pct" in low and "net_margin_pct" in low
    assert low["prediction_signal"] is None
    assert low["range_preference_note"] is None
    expected_break, _, _, _ = break_prob(low["current_price"], low["recommended_floor"],
        low["recommended_ceiling"], low["range_risk"]["sigma_24h"], {**DEFAULT_SMART_PARAMS, "horizon_h": 24})
    assert low["pause_risk"]["break_prob"] == pytest.approx(expected_break)
    assert low["pause_risk"]["pause_enter_prob"] == .10
    assert low["pause_risk"]["would_be_pausable"] == (expected_break > .10)
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


@pytest.mark.parametrize("requested,validated,expected", [
    ("champion", False, "campeon"), ("champion", True, "campeon"),
    ("consensus", False, "consenso"), ("consensus", True, "consenso"),
    ("auto", False, "campeon"), ("auto", True, "consenso"),
])
def test_vol_source_resolution_honors_mode_and_validation(requested, validated, expected):
    forecast = {"stale": False, "champion": VOL_CHAMPIONS[24], "move_1sigma_pct": 3.0,
                "consensus": {"sigma_pct": 4.0, "validation_status_live":
                    "validated" if validated else "en_evaluacion", "confidence": "alta"}}
    selected = grid_advisor._resolve_volatility_source("XRPUSDT", forecast, requested)
    assert selected["effective"] == expected
    assert selected["sigma_24h"] == pytest.approx(.04 if expected == "consenso" else .03)


def _forecast24(confidence="media", sigma_pct=4.0, stale=False, bias=False):
    return {"symbol": "XRPUSDT", "forecasts": [{"horizon_h": 24, "champion": VOL_CHAMPIONS[24],
        "move_1sigma_pct": 3.0, "stale": stale, "consensus": {"sigma_pct": sigma_pct,
        "confidence": confidence, "validation_status_live": "en_evaluacion",
        "eligible_models": ["NexoHAR"], "dispersion_iqr": .2}}]}


def _stats_response(bias=False, bias_mean=.2, n=10):
    return {"horizons": [{"models": [{"model_name": "NexoHAR", "n_verificadas": n,
        "bias_alert": bias, "all": {"bias_mean": bias_mean}}]}]}


def test_advisor_widens_only_low_dispersion_and_bias_is_informational(monkeypatch):
    monkeypatch.setattr(grid_advisor, "VOL_SOURCE", "consensus")
    def forecast_for_xrp(request, symbol):
        if symbol != "XRPUSDT":
            raise RuntimeError("no forecast")
        return _forecast24("baja")
    monkeypatch.setattr(volatility_route, "forecast", forecast_for_xrp)
    monkeypatch.setattr(volatility_route, "model_stats", lambda *a, **k: _stats_response(True))
    low = make_client().get("/api/grid/recommend", query={"symbol": "XRPUSDT", "range_mode": "estructural"}).body
    assert low["vol_source_effective"] == "consenso"
    assert low["vol_source_requested"] == "consensus"
    assert low["volatility_advisory"]["range_widened"] is True
    legacy_base = make_client().get("/api/grid/recommend", query={"symbol":"ADAUSDT", "range_mode":"estructural"}).body
    assert low["recommended_floor"] == pytest.approx(
        low["current_price"] - (low["current_price"] - legacy_base["recommended_floor"]) * 1.25)
    assert low["recommended_ceiling"] == pytest.approx(
        low["current_price"] + (legacy_base["recommended_ceiling"] - low["current_price"]) * 1.25)
    assert low["range_risk"]["sigma_24h"] == pytest.approx(.05)
    assert low["range_centered"]["sigma_widened"] is True
    assert low["range_centered"]["sigma_widen_factor"] == pytest.approx(1.25)
    assert low["volatility_advisory"]["bias_alerts"] == [{"model_name":"NexoHAR","direction":"sobreestima"}]
    assert low["volatility_advisory"]["accumulating_models"] == [
        {"model_name":"NexoHAR","n_verificadas":10,"n_min":30}]
    assert low["volatility_advisory"]["show_comparison"] is True
    assert low["range_risk"]["horizons"]["24"]["vol_source_effective"] == "consenso"
    low_sigma = low["range_risk"]["sigma_24h"]
    monkeypatch.setattr(volatility_route, "model_stats", lambda *a, **k: _stats_response(False))
    no_bias = make_client().get("/api/grid/recommend", query={"symbol": "XRPUSDT", "range_mode":"estructural"}).body
    assert no_bias["range_risk"]["sigma_24h"] == pytest.approx(low_sigma)

    monkeypatch.setattr(volatility_route, "forecast", lambda request, symbol: _forecast24("media"))
    medium = make_client().get("/api/grid/recommend", query={"symbol": "XRPUSDT", "range_mode":"estructural"}).body
    assert medium["volatility_advisory"]["range_widened"] is False
    assert medium["range_risk"]["sigma_24h"] == pytest.approx(.04)
    assert medium["range_centered"]["sigma_widened"] is False
    assert medium["range_centered"]["sigma_widen_factor"] is None


def test_non_xrp_and_stale_forecasts_keep_realized_volatility(monkeypatch):
    payload = _forecast24("baja")["forecasts"][0]
    assert grid_advisor._resolve_volatility_source("ADAUSDT", payload, "consensus")["effective"] == "consenso"
    assert grid_advisor._resolve_volatility_source("XRPUSDT", {**payload, "stale": True}, "consensus")["effective"] == "realizada"
    monkeypatch.setattr(grid_advisor, "VOL_SOURCE", "consensus")
    monkeypatch.setattr(volatility_route, "forecast", lambda request, symbol: _forecast24("alta", stale=True))
    stale = make_client().get("/api/grid/recommend", query={"symbol": "XRPUSDT"}).body
    assert stale["vol_source_effective"] == "realizada"
    assert "obsoleto" in stale["volatility_advisory"]["reason"]


def test_champion_mode_keeps_existing_advisor_range_contract(monkeypatch):
    monkeypatch.setattr(grid_advisor, "VOL_SOURCE", "champion")
    monkeypatch.setattr(volatility_route, "forecast", lambda request, symbol: _forecast24("media", sigma_pct=5))
    monkeypatch.setattr(volatility_route, "model_stats", lambda *a, **k: _stats_response(False))
    result = make_client().get("/api/grid/recommend", query={"symbol": "XRPUSDT"}).body
    old_contract = {"recommended_floor", "recommended_ceiling", "suggested_grids", "capital_per_grid",
                    "spacing_pct", "target_met", "range_risk", "risk", "simulations"}
    assert old_contract <= result.keys()
    assert result["vol_source_effective"] == "campeon"
    assert result["range_risk"]["sigma_24h"] == pytest.approx(.03)
    assert result["volatility_advisory"]["range_widened"] is False


def test_advisor_uses_persisted_factor_and_adaptive_stress_trigger(monkeypatch):
    monkeypatch.setattr(grid_advisor, "VOL_SOURCE", "consensus")
    monkeypatch.setattr(volatility_route, "forecast", lambda request, symbol: _forecast24("media"))
    monkeypatch.setattr(volatility_route, "model_stats", lambda *a, **k: _stats_response(False))
    client = make_client()
    client.app.state.db = SimpleNamespace(
        get_widen_active_values=lambda *a: {"k_active": 1.5, "disagreement_pct_active": 40.0},
        get_latest_widen_factor=lambda *a: {"dispersion_n": 200, "stress_threshold": .15,
            "status": "acumulando", "k_stress_smoothed": 1.3, "n": 20,
            "n_effective": 2, "progress_pct": 10},
    )
    result = client.get("/api/grid/recommend", query={"symbol": "XRPUSDT"}).body
    advisory = result["volatility_advisory"]
    assert advisory["widen_adaptive_trigger"] is True
    assert advisory["range_widened"] is True
    assert advisory["k_active"] == 1.5
    assert result["range_risk"]["sigma_24h"] == pytest.approx(.06)
    assert result["range_centered"]["sigma_widened"] is True
    assert result["range_centered"]["sigma_widen_factor"] == pytest.approx(1.5)
    assert advisory["show_comparison"] is False


def test_centered_range_profile_touch_targets_symmetry_and_cap():
    targets = {"low": .50, "medium": .30, "high": .15}
    for risk, target in targets.items():
        centered = grid_advisor._centered_range(100, .02, risk)
        assert centered["touch_probability_each_side_72h"] == pytest.approx(target, abs=.02)
        assert np.log(100 / centered["floor"]) == pytest.approx(np.log(centered["ceiling"] / 100))
        assert centered["floor"] < 100 < centered["ceiling"]
    capped = grid_advisor._centered_range(100, .5, "low")
    assert capped["limited_by_profile"] is True
    assert capped["range_pct"] == pytest.approx(25.0)
    assert capped["floor"] * capped["ceiling"] == pytest.approx(10000)
    assert grid_advisor._centered_range(100, 0, "medium") is None


def test_advisor_defaults_centered_and_keeps_structural_range_available():
    result = make_client().get("/api/grid/recommend", query={"symbol": "ADAUSDT"}).body
    assert result["range_mode"] == result["recommended_range"] == "centrado"
    assert result["range_centered"]["floor"] == pytest.approx(result["recommended_floor"])
    assert result["range_centered"]["ceiling"] == pytest.approx(result["recommended_ceiling"])
    assert result["range_centered"]["sigma_widened"] is False
    assert result["range_centered"]["sigma_widen_factor"] is None
    assert "1 ATR" in result["range_preference_note"]
    assert result["range_structural"]["floor"] < result["current_price"] < result["range_structural"]["ceiling"]
    assert result["range_risk"]["horizons"]["72"]["touch_floor"] == pytest.approx(.30, abs=.02)
    assert result["range_risk"]["horizons"]["72"]["touch_ceiling"] == pytest.approx(.30, abs=.02)
    assert result["simulations"]["sim_start"] is not None
    assert result["simulations"]["sim_days"] < 30
    assert "Ventana corta" in result["simulations"]["window_warning"]
    structural = make_client().get("/api/grid/recommend", query={
        "symbol": "ADAUSDT", "range_mode": "estructural"}).body
    assert structural["range_mode"] == "estructural"
    assert structural["recommended_floor"] == structural["range_structural"]["floor"]
    assert structural["recommended_ceiling"] == structural["range_structural"]["ceiling"]
    assert structural["range_preference_note"] is None


@pytest.mark.parametrize(
    ("floor_factor", "ceiling_factor", "expected"),
    [(.90, 1.01, "El precio está cerca del techo: casi todo el capital quedaría en compras"),
     (.99, 1.10, "El precio está cerca del piso: casi todo el capital quedaría en ventas"),
     (.97, 1.03, None)],
    ids=["cerca-techo", "cerca-piso", "sin-aviso"],
)
def test_recommended_range_position_warning_is_separate_from_preference_note(
        monkeypatch, floor_factor, ceiling_factor, expected):
    def centered(price, sigma_24h, risk):
        floor, ceiling = price * floor_factor, price * ceiling_factor
        return {"floor": floor, "ceiling": ceiling,
            "range_pct": (ceiling - floor) / price * 100,
            "limited_by_profile": False}

    monkeypatch.setattr(grid_advisor, "_centered_range", centered)
    result = make_client().get("/api/grid/recommend", query={"symbol": "ADAUSDT"}).body
    assert result["range_position_warning"] == expected
    assert result["range_preference_note"] is not None
    assert "1 ATR" in result["range_preference_note"]


def test_historical_simulation_starts_at_first_strictly_in_range_candle():
    frame = pd.DataFrame({"close": [9.0, 10.0, 11.0, 12.0],
        "timestamp": pd.date_range("2026-10-01", periods=4, freq="h", tz="UTC")})
    selected, meta = grid_advisor._simulation_window(frame, 10, 12, 90)
    assert selected.index.tolist() == [2, 3]
    assert meta["sim_start"] == frame.timestamp.iloc[2].isoformat()
    assert meta["sim_days"] == pytest.approx(1 / 24)
    assert "menos de 30 días" in meta["window_warning"]
    unavailable, meta = grid_advisor._simulation_window(frame, 13, 14, 90)
    assert unavailable is None
    assert "últimos 90 días nunca estuvo dentro" in meta["unavailable"]
    assert meta["sim_start"] is None and meta["sim_days"] == 0


def test_advisor_recommendation_rejects_coin_not_ready(monkeypatch):
    from fastapi import FastAPI, HTTPException
    from starlette.requests import Request
    class DB:
        def get_coin(self,symbol):return {"symbol":symbol,"active":1}
        def get_readiness(self,symbol):return {"state":"entrenando"}
    app=FastAPI();app.state.db=DB()
    request=Request({"type":"http","method":"GET","path":"/api/grid/recommend",
        "headers":[],"query_string":b"","server":("test",80),"client":("127.0.0.1",1),"scheme":"http","app":app})
    monkeypatch.setattr(grid_advisor,"coin_is_ready",lambda *_:False)
    with pytest.raises(HTTPException) as exc:
        grid_advisor.recommend(request,symbol="ADAUSDT")
    assert exc.value.status_code==409 and "ADAUSDT" in exc.value.detail and "entrenando" in exc.value.detail


def test_ready_ada_advisor_uses_its_model_without_xrp_widening(monkeypatch):
    monkeypatch.setattr(volatility_route, "forecast", lambda request, symbol: _forecast24("baja"))
    monkeypatch.setattr(grid_advisor, "vol_champions", lambda _symbol: (VOL_CHAMPIONS, True))
    advisory_symbols = []
    monkeypatch.setattr(grid_advisor, "_model_volatility_advisories",
        lambda *args: (advisory_symbols.append(args[-1]) or ([], [])))
    monkeypatch.setattr(grid_advisor, "_widen_runtime_settings", lambda *args: (_ for _ in ()).throw(AssertionError("XRP widening queried for ADA")))
    result = make_client().get("/api/grid/recommend", query={"symbol": "ADAUSDT", "range_mode": "estructural"}).body
    assert result["vol_source_effective"] == "campeon"
    assert result["vol_selection"] == "provisional"
    assert result["volatility_advisory"]["k_active"] == 1.0
    assert result["volatility_advisory"]["range_widened"] is False
    assert advisory_symbols == ["ADAUSDT"]
