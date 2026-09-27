from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from database.db_manager import DBManager
from scheduler.prediction_loop import PredictionLoop


class FakeClient:
    def __init__(self, last_candle_closed):
        self.last_candle_closed = last_candle_closed
        self.frame = None

    def get_historical_klines(self, symbol, interval, lookback_days):
        now = datetime.now(timezone.utc)
        end = now - timedelta(hours=1 if self.last_candle_closed else 0)
        timestamps = pd.date_range(end=end, periods=210, freq="1h")
        self.frame = pd.DataFrame(
            {
                "timestamp": timestamps,
                "close_time": timestamps + pd.Timedelta(hours=1),
                "open": np.arange(210, dtype=float) + 100,
                "high": np.arange(210, dtype=float) + 101,
                "low": np.arange(210, dtype=float) + 99,
                "close": np.arange(210, dtype=float) + 100.5,
                "volume": np.full(210, 10.0),
            }
        )
        return self.frame


class FakeEnsemble:
    models = {"model_a": object()}
    primary_model_name = "model_a"

    def __init__(self):
        self.frames = []
        self.prediction_frames = []

    def predict(self, symbol, interval, df):
        self.prediction_frames.append(df.copy())
        return {"consensus_signal": "NEUTRAL", "source": "predict"}

    def predict_and_save(self, symbol, interval, df):
        self.frames.append(df.copy())
        return {"consensus_signal": "NEUTRAL"}


@pytest.fixture
def db():
    manager = DBManager("sqlite:///:memory:")
    yield manager
    manager.engine.dispose()


@pytest.mark.parametrize("last_candle_closed", [False, True])
def test_prediction_cycle_uses_only_closed_candles(last_candle_closed):
    client = FakeClient(last_candle_closed)
    ensemble = FakeEnsemble()
    loop = PredictionLoop(client, ensemble)

    loop.run_prediction_cycle()

    assert len(ensemble.frames) == 1
    actual_last_open = ensemble.frames[0].iloc[-1]["timestamp"]
    source_last_open = client.frame.iloc[-1]["timestamp"]
    assert (actual_last_open == source_last_open) is last_candle_closed


def test_has_prediction_since_uses_pair_model_and_utc_time(db):
    since = datetime.now(timezone.utc) - timedelta(hours=1)

    assert db.has_prediction_since("XRPUSDT", "1h", "model_a", since) is False

    db.save_prediction(
        {
            "prediction_id": "recent-ensemble",
            "symbol": "XRPUSDT",
            "interval": "1h",
            "model_name": "model_a",
            "predicted_at": datetime.now(timezone.utc),
            "probability_up": 0.5,
            "signal": "NEUTRAL",
            "confidence": "baja",
            "price_at_prediction": 100.0,
        }
    )

    assert db.has_prediction_since("XRPUSDT", "1h", "model_a", since) is True


def test_startup_cycle_skips_existing_prediction_for_latest_closed_candle(db):
    client = FakeClient(last_candle_closed=True)
    ensemble = FakeEnsemble()
    last_closed_close = client.get_historical_klines(
        "XRPUSDT", "1h", lookback_days=60
    )["close_time"].iloc[-1]
    db.save_prediction(
        {
            "prediction_id": "already-predicted",
            "symbol": "XRPUSDT",
            "interval": "1h",
            "model_name": "model_a",
            "predicted_at": last_closed_close + pd.Timedelta(minutes=1),
            "probability_up": 0.5,
            "signal": "NEUTRAL",
            "confidence": "baja",
            "price_at_prediction": 100.0,
        }
    )
    loop = PredictionLoop(client, ensemble, db_manager=db)

    loop.run_startup_cycle()

    assert ensemble.frames == []
    assert len(ensemble.prediction_frames) == 1
    assert loop.latest[("XRPUSDT", "1h")] == {"consensus_signal": "NEUTRAL", "source": "predict"}


def test_startup_cycle_does_not_skip_for_prediction_before_latest_candle_close(db):
    now = datetime.now(timezone.utc)
    current_open = pd.Timestamp(now).floor("1h")
    closed_open = current_open - pd.Timedelta(hours=1)
    frame = pd.DataFrame(
        {
            "timestamp": [closed_open, current_open],
            "close_time": [
                current_open - pd.Timedelta(milliseconds=1),
                current_open + pd.Timedelta(hours=1) - pd.Timedelta(milliseconds=1),
            ],
            "open": [100.0, 101.0],
            "high": [101.0, 102.0],
            "low": [99.0, 100.0],
            "close": [100.5, 101.5],
            "volume": [10.0, 10.0],
        }
    )
    assert frame["close_time"].iloc[0] <= now < frame["close_time"].iloc[1]

    class RegressionClient:
        def get_historical_klines(self, symbol, interval, lookback_days):
            return frame

    ensemble = FakeEnsemble()
    db.save_prediction(
        {
            "prediction_id": "previous-candle-prediction",
            "symbol": "XRPUSDT",
            "interval": "1h",
            "model_name": "model_a",
            "predicted_at": closed_open + pd.Timedelta(minutes=1),
            "probability_up": 0.5,
            "signal": "NEUTRAL",
            "confidence": "baja",
            "price_at_prediction": 100.0,
        }
    )
    loop = PredictionLoop(RegressionClient(), ensemble, db_manager=db)

    loop.run_startup_cycle()

    assert len(ensemble.frames) == 1
    assert ensemble.frames[0].iloc[-1]["timestamp"] == closed_open
    assert current_open not in ensemble.frames[0]["timestamp"].values
class CapturingScheduler:
    running = False

    def add_job(self, function, trigger, **kwargs):
        self.job = {"function": function, "trigger": trigger, **kwargs}

    def start(self):
        self.running = True


def test_prediction_loop_uses_hourly_utc_cron():
    scheduler = CapturingScheduler()
    loop = PredictionLoop(FakeClient(last_candle_closed=True), FakeEnsemble(), scheduler=scheduler)

    loop.start()

    assert scheduler.job["trigger"] == "cron"
    assert scheduler.job["hour"] == "*"
    assert scheduler.job["minute"] == 1
    assert scheduler.job["timezone"] == "UTC"
