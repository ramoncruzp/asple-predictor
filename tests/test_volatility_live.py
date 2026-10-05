from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from math import exp, sqrt
from types import SimpleNamespace

import joblib
import numpy as np
import pandas as pd
import pytest
from fastapi import HTTPException

from api.routes.volatility import forecast as api_forecast
from api.routes.volatility import history as api_history
from api.routes.volatility import battle as api_battle
from config.models_config import VOL_CHAMPIONS, VOL_HORIZONS, VOL_MODELS
from database.db_manager import DBManager
from data.volatility import aggregate_intraday_to_hourly
from models.volatility.live import VolPredictor
from scheduler.vol_loop import VolLoop
from scripts.train_vol_models import train_volatility_models


def synthetic_ohlcv(hours=700, seed=910):
    rng = np.random.default_rng(seed)
    returns = rng.normal(0.0, 0.002, hours)
    close = 100.0 * np.exp(np.cumsum(returns))
    open_ = np.r_[100.0, close[:-1]]
    timestamps = pd.date_range("2025-01-01", periods=hours, freq="h", tz="UTC")
    return pd.DataFrame({
        "timestamp": timestamps,
        "close_time": timestamps + pd.Timedelta(hours=1) - pd.Timedelta(milliseconds=1),
        "open": open_, "high": np.maximum(open_, close) * 1.001,
        "low": np.minimum(open_, close) * 0.999, "close": close,
        "volume": rng.uniform(10.0, 100.0, hours),
    })


def synthetic_5m_from_hourly(hourly):
    timestamps = []
    open_values = []
    close_values = []
    for index, row in hourly.iterrows():
        previous = float(row["open"])
        end = float(row["close"])
        for step in range(12):
            next_close = previous * np.exp(np.log(end / float(row["open"])) / 12.0)
            timestamps.append(pd.Timestamp(row["timestamp"]) + pd.Timedelta(minutes=5 * step))
            open_values.append(previous)
            close_values.append(next_close)
            previous = next_close
    close_values[-1] = float(hourly["close"].iloc[-1])
    open_values = np.asarray(open_values)
    close_values = np.asarray(close_values)
    return pd.DataFrame({
        "timestamp": timestamps,
        "open": open_values,
        "high": np.maximum(open_values, close_values) * 1.0001,
        "low": np.minimum(open_values, close_values) * 0.9999,
        "close": close_values,
        "volume": 1.0,
    })


class ConstantLogVolModel:
    def predict(self, frame):
        return pd.Series(-5.0, index=frame.index)


def test_training_produces_artifact_and_manifest_on_synthetic_data(tmp_path):
    candles = synthetic_ohlcv(700)
    intraday = aggregate_intraday_to_hourly(synthetic_5m_from_hourly(candles))
    manifest = train_volatility_models(
        candles,
        intraday,
        artifact_dir=tmp_path,
        horizons=[1],
        model_names=["Persistence"],
    )

    artifact = tmp_path / "Persistence_1h.joblib"
    manifest_path = tmp_path / "manifest_xrp.json"
    assert artifact.is_file()
    assert manifest_path.is_file()
    assert joblib.load(artifact)["var_factor"] > 0.0
    loaded = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert loaded["horizons"]["1"]["Persistence"]["var_factor"] > 0.0
    assert loaded["horizons"]["1"]["Persistence"]["r2_cal"] is not None
    assert "p33" in manifest["regime_percentiles_24h"]
    assert "p66" in manifest["regime_percentiles_24h"]


def test_vol_predictor_uses_last_complete_hour_and_ignores_incomplete_tail(tmp_path):
    horizons = [1]
    model_names = ["Persistence"]
    (tmp_path / "manifest_xrp.json").write_text(json.dumps({
        "symbol": "XRPUSDT", "horizons": {"1": {"Persistence": {"var_factor": 1.2}}},
        "champions": {"1": "Persistence"},
    }), encoding="utf-8")
    joblib.dump({"model": ConstantLogVolModel(), "var_factor": 1.2}, tmp_path / "Persistence_1h.joblib")
    predictor = VolPredictor(tmp_path, horizons=horizons, model_names=model_names)
    candles_1h = synthetic_ohlcv(190)
    rows = len(candles_1h) * 12 - 4
    timestamps_5m = pd.date_range(candles_1h["timestamp"].iloc[0], periods=rows, freq="5min", tz="UTC")
    close = 100.0 * np.exp(np.arange(rows) * 0.00001)
    open_ = np.r_[100.0, close[:-1]]
    candles_5m = pd.DataFrame({
        "timestamp": timestamps_5m, "close_time": timestamps_5m + pd.Timedelta(minutes=5) - pd.Timedelta(milliseconds=1),
        "open": open_, "high": np.maximum(open_, close) * 1.0001,
        "low": np.minimum(open_, close) * 0.9999, "close": close, "volume": 1.0,
    })
    now = candles_1h["close_time"].iloc[-1].to_pydatetime() + timedelta(seconds=1)

    predictions = predictor.predict_latest(candles_1h, candles_5m, now=now)

    assert len(predictions) == 1
    expected_forecast_at = candles_1h["close_time"].iloc[-2].to_pydatetime()
    assert predictions[0]["forecast_at"] == expected_forecast_at
    assert np.isclose(predictions[0]["pred_logvol_cal"], -5.0 + 0.5 * np.log(1.2))


def test_vol_forecasts_unique_identity_makes_saves_idempotent():
    db = DBManager("sqlite:///:memory:")
    now = datetime.now(timezone.utc)
    row = {
        "symbol": "XRPUSDT", "horizon_h": 4, "model_name": "GBM",
        "forecast_at": now - timedelta(hours=4), "made_at": now,
        "pred_logvol_raw": -5.0, "pred_logvol_cal": -4.9,
        "var_factor": 1.2, "is_champion": True,
    }

    assert db.save_vol_forecasts([row]) == 1
    assert db.save_vol_forecasts([row]) == 0
    assert len(db.get_latest_vol_forecasts("XRPUSDT")) == 1
    db.engine.dispose()


def test_realization_uses_next_h_complete_hours_or_remains_pending():
    timestamps = pd.date_range("2025-01-01", periods=8 * 12, freq="5min", tz="UTC")
    close = 100.0 * np.exp(np.arange(len(timestamps)) * 0.0001)
    open_ = np.r_[100.0, close[:-1]]
    five_minute = pd.DataFrame({
        "timestamp": timestamps, "open": open_, "high": np.maximum(open_, close) * 1.001,
        "low": np.minimum(open_, close) * 0.999, "close": close, "volume": 1.0,
    })
    hourly_intraday = aggregate_intraday_to_hourly(five_minute)
    forecast_at = hourly_intraday["close_time"].iloc[2].to_pydatetime()
    forecast = {"forecast_at": forecast_at, "horizon_h": 2}
    latest_close = hourly_intraday["close_time"].iloc[4].to_pydatetime()
    expected_var = hourly_intraday["rv_intra"].iloc[3:5].mean()

    actual = VolLoop.realized_for_forecast(forecast, hourly_intraday, latest_close)
    missing_one = hourly_intraday.loc[hourly_intraday["timestamp"] != hourly_intraday["timestamp"].iloc[4]]
    pending = VolLoop.realized_for_forecast(forecast, missing_one, latest_close)

    assert np.isclose(actual, 0.5 * np.log(expected_var + 1e-12))
    assert pending is None


def _request_with_state(**state_values):
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(**state_values)))


def test_api_forecast_calculates_sigma_ranges_regime_and_stale():
    now = datetime.now(timezone.utc)
    rows = []
    for horizon, champion in VOL_CHAMPIONS.items():
        rows.append({
            "horizon_h": horizon, "model_name": champion, "is_champion": True,
            "forecast_at": now - timedelta(hours=3), "made_at": now - timedelta(hours=3),
            "pred_logvol_cal": float(np.log(0.8)),
        })

    class FakeDB:
        def get_latest_vol_forecasts(self, symbol):
            return rows

    predictor = SimpleNamespace(
        symbol="XRPUSDT", manifest={"regime_percentiles_24h": {"p33": 0.4, "p66": 0.7}}
    )
    loop = SimpleNamespace(latest={"price": 100.0})
    request = _request_with_state(vol_predictor=predictor, vol_loop=loop, db=FakeDB())

    result = api_forecast(request)

    first = result["forecasts"][0]
    sigma_h = 0.8 * sqrt(1)
    assert np.isclose(first["move_1sigma_pct"], sigma_h * 100.0)
    np.testing.assert_allclose(first["range_1sigma"], [100.0 * exp(-sigma_h), 100.0 * exp(sigma_h)])
    np.testing.assert_allclose(first["range_2sigma"], [100.0 * exp(-2 * sigma_h), 100.0 * exp(2 * sigma_h)])
    assert first["stale"] is True
    assert result["regime"] == "AGITADO"


def test_api_forecast_adds_weighted_consensus_without_changing_champion_fields(tmp_path, monkeypatch):
    import json
    import api.routes.volatility as volatility_route

    monkeypatch.setattr(volatility_route, "VOL_ARTIFACT_DIR", tmp_path)
    now = datetime.now(timezone.utc)
    rows = []
    for horizon, champion in VOL_CHAMPIONS.items():
        for model_name in VOL_MODELS:
            rows.append({
                "horizon_h": horizon, "model_name": model_name,
                "is_champion": model_name == champion,
                "forecast_at": now, "made_at": now,
                "pred_logvol_cal": float(np.log(0.01 + 0.001 * VOL_MODELS.index(model_name))),
            })
    weights = {name: (1.0 if name == "EWMA" else 0.0) for name in VOL_MODELS}
    report = {
        "report": {"horizons": {
            str(horizon): {
                "eligible_models": ["EWMA"], "weights": weights,
                "ensemble_results_test": {"P": {"validation_status": "not_validated"}},
            } for horizon in VOL_HORIZONS
        }}
    }
    (tmp_path / "consensus_xrp.json").write_text(json.dumps(report), encoding="utf-8")

    class FakeDB:
        def get_latest_vol_forecasts(self, symbol):
            return rows

    predictor = SimpleNamespace(symbol="XRPUSDT", manifest={"regime_percentiles_24h": {}})
    request = _request_with_state(
        vol_predictor=predictor, vol_loop=SimpleNamespace(latest={"price": 100.0}), db=FakeDB()
    )
    result = api_forecast(request)
    first = result["forecasts"][0]
    expected_sigma = 0.011 * sqrt(first["horizon_h"])
    assert first["consensus"]["method"] == "weighted"
    assert np.isclose(first["consensus"]["sigma_pct"], expected_sigma * 100)
    np.testing.assert_allclose(
        first["consensus"]["range_1sigma"],
        [100 * exp(-expected_sigma), 100 * exp(expected_sigma)],
    )
    assert first["consensus"]["eligible_models"] == ["EWMA"]
    assert first["consensus"]["weights"] == weights
    assert {"horizon_h", "champion", "forecast_at", "made_at", "vol_per_hour",
            "move_1sigma_pct", "range_1sigma", "range_2sigma", "stale"} <= first.keys()


def test_api_forecast_returns_null_consensus_when_file_missing_and_unchanged_for_other_symbol(tmp_path, monkeypatch):
    import api.routes.volatility as volatility_route

    monkeypatch.setattr(volatility_route, "VOL_ARTIFACT_DIR", tmp_path)
    now = datetime.now(timezone.utc)
    rows = [{
        "horizon_h": horizon, "model_name": champion, "is_champion": True,
        "forecast_at": now, "made_at": now, "pred_logvol_cal": float(np.log(0.01)),
    } for horizon, champion in VOL_CHAMPIONS.items()]

    class FakeDB:
        def get_latest_vol_forecasts(self, symbol):
            return rows

    request = _request_with_state(
        vol_predictor=SimpleNamespace(symbol="XRPUSDT", manifest={}),
        vol_loop=SimpleNamespace(latest={"price": 100.0}), db=FakeDB(),
    )
    result = api_forecast(request)
    assert result["forecasts"][0]["consensus"] is None
    assert "no disponible" in result["forecasts"][0]["reason"]
    with pytest.raises(HTTPException) as error:
        api_forecast(request, symbol="ADAUSDT")
    assert error.value.status_code == 404


def test_api_forecast_returns_null_consensus_when_eligible_predictions_are_stale(tmp_path, monkeypatch):
    import api.routes.volatility as volatility_route

    monkeypatch.setattr(volatility_route, "VOL_ARTIFACT_DIR", tmp_path)
    names = VOL_MODELS
    (tmp_path / "consensus_xrp.json").write_text(json.dumps({
        "report": {"horizons": {
            str(horizon): {
                "eligible_models": ["EWMA"],
                "weights": {name: (1.0 if name == "EWMA" else 0.0) for name in names},
                "ensemble_results_test": {"P": {"validation_status": "not_validated"}},
            } for horizon in VOL_HORIZONS
        }}
    }), encoding="utf-8")
    old = datetime.now(timezone.utc) - timedelta(hours=3)
    rows = [{
        "horizon_h": horizon, "model_name": model, "is_champion": model == VOL_CHAMPIONS[horizon],
        "forecast_at": old, "made_at": old, "pred_logvol_cal": float(np.log(0.01)),
    } for horizon in VOL_HORIZONS for model in names]

    class FakeDB:
        def get_latest_vol_forecasts(self, symbol):
            return rows

    request = _request_with_state(
        vol_predictor=SimpleNamespace(symbol="XRPUSDT", manifest={}),
        vol_loop=SimpleNamespace(latest={"price": 100.0}), db=FakeDB(),
    )
    first = api_forecast(request)["forecasts"][0]
    assert first["consensus"] is None
    assert "obsoletas" in first["reason"]


def test_api_battle_hides_live_metrics_until_minimum_and_history_delegates():
    class FakeDB:
        def get_vol_battle(self, symbol, horizon):
            return [{"model_name": "GBM", "n_verified": 2, "r2_live": 0.5, "mse_live": 0.1, "is_champion": True}]

        def get_vol_history(self, symbol, horizon, model, limit):
            return [{"model": model, "limit": limit}]

    predictor = SimpleNamespace(
        symbol="XRPUSDT", manifest={"horizons": {"4": {"GBM": {"r2_cal": 0.6, "qlike_cal": 0.2}}}}
    )
    request = _request_with_state(vol_predictor=predictor, vol_loop=object(), db=FakeDB())

    battle = api_battle(request, horizon=4)
    gbm = next(item for item in battle["models"] if item["model_name"] == "GBM")

    assert gbm["r2_cal"] == 0.6 and gbm["qlike_cal"] == 0.2
    assert gbm["n_verified"] == 2 and gbm["r2_live"] is None and gbm["mse_live"] is None
    assert api_history(request, horizon=4, model="GBM", limit=17) == [{"model": "GBM", "limit": 17}]


def test_api_returns_503_when_volatility_manifest_is_unavailable():
    request = _request_with_state(vol_predictor=None, vol_loop=None)
    with pytest.raises(HTTPException) as error:
        api_forecast(request)
    assert error.value.status_code == 503
    assert "manifest" in error.value.detail


def test_frontend_volatility_contract_fields():
    """Keep the three V2b frontend endpoint payloads aligned with their consumers."""
    now = datetime.now(timezone.utc)
    forecast_rows = [{
        "horizon_h": horizon, "model_name": champion, "is_champion": True,
        "forecast_at": now, "made_at": now, "pred_logvol_cal": float(np.log(0.01)),
    } for horizon, champion in VOL_CHAMPIONS.items()]

    class FakeDB:
        def get_latest_vol_forecasts(self, symbol):
            return forecast_rows

        def get_vol_battle(self, symbol, horizon):
            return []

        def get_vol_history(self, symbol, horizon, model, limit):
            return [{"forecast_at": now, "pred_vol_pct": 1.0, "realized_vol_pct": None}]

    predictor = SimpleNamespace(
        symbol="XRPUSDT",
        manifest={"regime_percentiles_24h": {"p33": 0.005, "p66": 0.02}, "horizons": {"4": {}}},
    )
    request = _request_with_state(
        vol_predictor=predictor, vol_loop=SimpleNamespace(latest={"price": 1.0}), db=FakeDB()
    )

    forecast_result = api_forecast(request)
    assert {"symbol", "price", "regime", "forecasts"} <= forecast_result.keys()
    forecast_fields = {
        "horizon_h", "champion", "forecast_at", "made_at", "move_1sigma_pct",
        "range_1sigma", "range_2sigma", "stale",
    }
    assert forecast_fields <= forecast_result["forecasts"][0].keys()

    battle_result = api_battle(request, horizon=4)
    assert {"symbol", "horizon_h", "models"} <= battle_result.keys()
    battle_fields = {"model_name", "r2_cal", "qlike_cal", "n_verified", "r2_live", "is_champion"}
    assert battle_fields <= battle_result["models"][0].keys()

    history_result = api_history(request, horizon=4, model="GBM", limit=200)
    assert {"forecast_at", "pred_vol_pct", "realized_vol_pct"} <= history_result[0].keys()
