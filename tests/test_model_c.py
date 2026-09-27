from datetime import datetime, timezone

import numpy as np
import pandas as pd

from config.models_config import TARGET_HORIZON_CANDLES
from models.model_c_prophet import ModelC


class FakeXGB:
    def __init__(self):
        self.received = None

    def predict_proba(self, features):
        self.received = features.copy()
        return np.array([[0.4, 0.6]])


def make_ohlcv():
    now = datetime.now(timezone.utc)
    latest_closed_open = pd.Timestamp(now).floor("4h") - pd.Timedelta(hours=4)
    timestamps = pd.date_range(end=latest_closed_open, periods=200, freq="4h")
    rng = np.random.default_rng(8621)
    close = 100 + np.cumsum(rng.normal(0, 0.2, size=200))
    df = pd.DataFrame(
        {
            "timestamp": timestamps,
            "open": close - 0.05,
            "high": close + 0.2,
            "low": close - 0.2,
            "close": close,
            "volume": rng.uniform(10, 100, size=200),
        }
    )
    return df


def compute_residuals(frame, _prophet_model):
    result = frame.copy()
    residual = np.arange(len(frame), dtype=float)
    result["residual_pct"] = residual / 1000.0
    result["residual_pct_rolling_5"] = pd.Series(residual / 1000.0).rolling(5).mean().to_numpy()
    result["prophet_trend_rel"] = 0.01
    return result


def test_predict_uses_latest_features_even_without_target(monkeypatch):
    model = ModelC()
    model.prophet_model = object()
    model.xgb_model = FakeXGB()
    df = make_ohlcv()

    monkeypatch.setattr(model, "_future_prophet_trend", lambda _df: 0.01)
    monkeypatch.setattr(model, "_compute_residuals", compute_residuals)

    result = model.predict(df)

    assert len(model.xgb_model.received) == 1
    assert model.xgb_model.received["residual_pct"].iloc[0] == (len(df) - 1) / 1000.0
    assert pd.Timestamp(result["timestamp"]).tz_convert("UTC") == pd.Timestamp(
        df["timestamp"].iloc[-1]
    ).tz_convert("UTC")
    assert result["signal"] == "ALCISTA"


def test_prepare_hybrid_data_keeps_last_rows_only_without_target_requirement(
    monkeypatch,
):
    model = ModelC()
    df = make_ohlcv()
    monkeypatch.setattr(model, "_compute_residuals", compute_residuals)

    h_train = model._prepare_hybrid_data(df, object(), require_target=True)
    h_pred = model._prepare_hybrid_data(df, object(), require_target=False)

    assert h_pred["timestamp"].iloc[-1] == df["timestamp"].iloc[-1]
    assert h_train["timestamp"].iloc[-1] == df["timestamp"].iloc[
        -1 - TARGET_HORIZON_CANDLES
    ]
    assert len(h_pred) - len(h_train) == TARGET_HORIZON_CANDLES
