from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest
from fastapi import FastAPI

from api.routes import grid_advisor, volatility as volatility_route
from config.models_config import VOL_CHAMPIONS
from grid.sim.runner import FILTERS
from grid.policy import DEFAULT_SMART_PARAMS, break_prob
from grid.sim.data import ewma_sigma_24h
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
        self.frame_5m = self.frame.copy()
        self.frame_5m["timestamp"] = pd.date_range(
            self.frame.timestamp.iloc[0], periods=len(self.frame), freq="5min", tz="UTC")

    def get_historical_klines(self, symbol, interval, lookback_days):
        return self.frame_5m if interval == "5m" else self.frame


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
    assert low["capital"] == 1000 and high["capital"] == 1000
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
    assert "Historia disponible para simular" in result["simulations"]["window_warning"]
    assert "90 pedidos" in result["simulations"]["window_warning"]
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
    assert result["range_risk"]["source"].startswith("pron\u00f3stico campe\u00f3n ADAUSDT 24 h")
    assert result["vol_selection"] == "provisional"
    assert result["volatility_advisory"]["k_active"] == 1.0
    assert result["volatility_advisory"]["range_widened"] is False
    assert advisory_symbols == ["ADAUSDT"]


def test_ada_advisor_uses_neutral_factor_without_xrp_widen_data():
    result = make_client().get("/api/grid/recommend", query={"symbol":"ADAUSDT","capital":1000,"risk":"low","days":90}).body
    advisory = result["volatility_advisory"]
    assert advisory["widen_not_calculated"] is True and advisory["k_active"] == 1.0
    assert advisory["widen_n"] is None and advisory["widen_n_effective"] is None
    assert advisory["disagreement_pct_active"] is None and advisory["disagreement_threshold_suggested"] is None
    assert advisory["k_suggested"] is None


def test_advisor_simulation_resets_nonzero_index_and_reports_short_history(monkeypatch):
    frame = candles().iloc[1:]
    monkeypatch.setattr(grid_advisor, "_simulation_window", lambda *_: (frame, {"sim_start":"2026-08-22T00:00:00+00:00", "sim_days":46.8, "window_warning":None}))
    result = make_client().get("/api/grid/recommend", query={"symbol":"ADAUSDT","capital":1000,"risk":"low","days":90}).body
    for strategy in ("simple", "smart"):
        row=result["simulations"]["strategies"][strategy]
        assert "KeyError: 0" not in row.get("unavailable", "")
        assert "Historia disponible para simular 46,8 d\u00edas de 90 pedidos" in row["window_warning"]
        assert "el precio entr\u00f3 al rango" in row["window_warning"]


def test_5m_simulation_uses_warmed_daily_sigma_and_monitor_cadence(monkeypatch):
    client = make_client()
    rng = np.random.default_rng(207)
    returns = rng.normal(0, .01 / np.sqrt(288), 9000)
    closes = 100 * np.exp(np.cumsum(returns))
    stamps = pd.date_range("2026-01-01", periods=len(closes), freq="5min", tz="UTC")
    frame_5m = pd.DataFrame({"timestamp": stamps, "open": closes,
        "high": closes * 1.001, "low": closes * .999, "close": closes})
    client.app.state.client.frame_5m = frame_5m
    client.app.state.settings.grid_monitor_interval = 900
    captured = []

    def fake_run(candles, **kwargs):
        captured.append((candles, kwargs))
        return {"metrics": {"cycles_completed": 0}}

    monkeypatch.setattr(grid_advisor, "run_simulation", fake_run)
    response = client.get("/api/grid/recommend", query={"symbol": "ADAUSDT", "days": 90,
        "risk": "low", "range_mode": "estructural"}).body
    assert response["simulations"]["resolution"] == "5m"
    assert response["simulations"]["resync_minutes"] == 15
    assert len(captured) == 2
    candles, kwargs = captured[0]
    sigma_values = kwargs["sigma_values"]
    assert kwargs["resync_candles"] == 3
    assert len(sigma_values) == len(candles.close)
    assert np.all(np.diff(candles.timestamp) == 300)
    assert np.isfinite(sigma_values).all()
    assert sigma_values[0] > 0
    assert float(np.median(sigma_values)) == pytest.approx(.01, abs=.005)
    theoretical = ewma_sigma_24h(closes, halflife_h=72)
    assert float(np.median(theoretical[1000:])) == pytest.approx(.01, abs=.005)


def test_5m_download_failure_falls_back_to_hourly_simulation(monkeypatch):
    client = make_client()
    original = client.app.state.client.get_historical_klines

    def fail_5m(symbol, interval, lookback_days):
        if interval == "5m":
            raise RuntimeError("simulated 5m download failure")
        return original(symbol, interval, lookback_days)

    client.app.state.client.get_historical_klines = fail_5m
    captured = []

    def fake_run(candles, **kwargs):
        captured.append(kwargs)
        return {"metrics": {"cycles_completed": 0}}

    monkeypatch.setattr(grid_advisor, "run_simulation", fake_run)
    response = client.get("/api/grid/recommend", query={"symbol": "ADAUSDT", "days": 90,
        "risk": "low", "range_mode": "estructural"}).body
    assert response["simulations"]["resolution"] == "1h"
    assert "simulación aproximada: velas de 1 h" in response["simulations"]["window_warning"]
    assert response["simulations"]["resync_minutes"] == 180
    assert captured and all(kwargs["resync_candles"] == 3 for kwargs in captured)
    assert all(kwargs["sigma_values"] is None for kwargs in captured)


def test_advisor_simulation_passes_candle_data_for_ada_and_xrp(monkeypatch):
    import sys
    import threading
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from data.binance_client import BinanceClient
    from grid.sim.data import CandleData
    from grid.sim.runner import run_simulation as actual_run_simulation

    rows = []
    for index in range(420):
        close = 70.0 if index < 30 else 100 + 4 * np.sin((index - 30) / 8) + .02 * (index - 30)
        stamp_ms = int(pd.Timestamp("2026-09-01T00:00:00Z").timestamp() * 1000) + index * 3_600_000
        rows.append([stamp_ms, str(close - .1), str(close + .8), str(close - .8),
                     str(close), "1000", stamp_ms + 3_599_999, "100000", 10, "500", "50000"])
    frame = BinanceClient._klines_to_dataframe(rows)
    assert str(frame.timestamp.dtype) == "datetime64[ns, UTC]"
    assert isinstance(frame.index, pd.RangeIndex)
    received = []

    def spy(frame, event, arg):
        if event == "call" and frame.f_code is actual_run_simulation.__code__:
            received.append(frame.f_locals["candles"])

    monkeypatch.setattr(volatility_route, "forecast", lambda *_args, **_kwargs: {"forecasts": []})
    app = FastAPI()
    app.include_router(grid_advisor.router, prefix="/api/grid")
    app.state.client = type("HistoricalClient", (), {"get_historical_klines": lambda self, *_a, **_kw: frame})()
    app.state.settings = SimpleNamespace(scanner_fee_pct=.1, scanner_timeout_seconds=10)
    app.state.grid_scan_service = None
    app.state.prediction_loop = SimpleNamespace(latest={})
    observed = {}
    previous_sys_profile, previous_thread_profile = sys.getprofile(), threading.getprofile()
    sys.setprofile(spy)
    threading.setprofile(spy)
    try:
        with TestClient(app) as client:
            for symbol in ("ADAUSDT", "XRPUSDT"):
                response = client.get("/api/grid/recommend", params={"symbol": symbol, "days": 10, "risk": "low"})
                assert response.status_code == 200, response.text
                observed[symbol] = response.json()["simulations"]["strategies"]
    finally:
        sys.setprofile(previous_sys_profile)
        threading.setprofile(previous_thread_profile)

    assert len(received) == 4
    assert all(isinstance(candles, CandleData) for candles in received), [type(item).__name__ for item in received]
    assert all(candles.timestamp.dtype == np.int64 for candles in received)
    assert all(candles.open.dtype == np.float64 and candles.high.dtype == np.float64
               and candles.low.dtype == np.float64 and candles.close.dtype == np.float64 for candles in received)
    assert all(int(candles.timestamp[0]) > int(frame.timestamp.iloc[0].timestamp()) for candles in received)
    for symbol in ("ADAUSDT", "XRPUSDT"):
        for strategy in ("simple", "smart"):
            row = observed[symbol][strategy]
            assert "unavailable" not in row, f"{symbol} {strategy}: {row}"
            assert isinstance(row.get("pnl_total_net_usdt"), (int, float))
            assert isinstance(row.get("max_drawdown_pct"), (int, float))
            assert isinstance(row.get("cycles_completed"), int)
