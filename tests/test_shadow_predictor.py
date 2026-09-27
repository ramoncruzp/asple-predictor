import json
from datetime import timezone

import numpy as np
import pandas as pd
import pytest

from database.db_manager import DBManager
from models.shadow_predictor import ShadowPredictor


class FakeModel:
    def __init__(self, probability):
        self.probability = probability

    def predict(self, frame):
        candle_open = pd.Timestamp.now(tz="UTC").floor("h") - pd.Timedelta(hours=1)
        return {
            "probability_up": self.probability,
            "signal": "BAJISTA",
            "confidence": "baja",
            "timestamp": candle_open,
        }


def synthetic_frame():
    rng = np.random.default_rng(761)
    close = 100 + np.cumsum(rng.normal(0, 0.1, 200))
    timestamps = pd.date_range("2026-01-01", periods=200, freq="1h", tz="UTC")
    return pd.DataFrame({
        "timestamp": timestamps, "close_time": timestamps + pd.Timedelta(hours=1) - pd.Timedelta(milliseconds=1),
        "open": close, "high": close + 0.2, "low": close - 0.2, "close": close,
        "volume": rng.uniform(10, 100, 200),
    })


@pytest.mark.parametrize(("probability", "expected"), [(0.61, "ALCISTA"), (0.60, "NEUTRAL"), (0.30, "NEUTRAL")])
def test_shadow_predict_only_emits_bullish_or_neutral(probability, expected):
    result = ShadowPredictor(FakeModel(probability), None).predict("XRPUSDT", "1h", synthetic_frame())
    assert result["consensus_signal"] == expected
    assert result["signal"] != "BAJISTA"
    assert result["mode"] == "shadow"


def test_shadow_predict_and_save_persists_exactly_one_model_a_row():
    db = DBManager("sqlite:///:memory:")
    try:
        result = ShadowPredictor(FakeModel(0.61), db).predict_and_save(
            "XRPUSDT", "1h", synthetic_frame()
        )
        rows = db.get_recent_predictions(limit=10)
        assert len(rows) == 1
        assert rows[0]["model_name"] == "model_a"
        assert rows[0]["signal"] == "ALCISTA"
        row = rows[0]
        candle_open = result["candle_open"]
        predicted_at = row["predicted_at"]
        verify_at = row["verify_at"]
        if predicted_at.tzinfo is None:
            predicted_at = predicted_at.replace(tzinfo=timezone.utc)
        if verify_at.tzinfo is None:
            verify_at = verify_at.replace(tzinfo=timezone.utc)
        assert predicted_at >= candle_open + pd.Timedelta(hours=1)
        assert verify_at - predicted_at == pd.Timedelta(hours=4)
        assert db.has_prediction_since(
            "XRPUSDT", "1h", "model_a",
            candle_open + pd.Timedelta(hours=1) - pd.Timedelta(milliseconds=1),
        ) is True
        market_condition = json.loads(row["market_condition"])
        assert market_condition["candle_open"] == candle_open.isoformat()
    finally:
        db.engine.dispose()
