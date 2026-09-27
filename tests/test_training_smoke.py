from __future__ import annotations

import joblib
import numpy as np
import pandas as pd
import pytest
from xgboost import XGBClassifier

from models.model_a_xgboost import ModelA
from models.model_c_prophet import ModelC


def synthetic_ohlcv(rows: int = 3000) -> pd.DataFrame:
    rng = np.random.default_rng(41027)
    close = 100.0 + np.cumsum(rng.normal(0.0, 0.35, size=rows))
    return pd.DataFrame({
        "timestamp": pd.date_range("2024-01-01", periods=rows, freq="h", tz="UTC"),
        "open": close - 0.03,
        "high": close + rng.uniform(0.05, 0.25, size=rows),
        "low": close - rng.uniform(0.05, 0.25, size=rows),
        "close": close,
        "volume": rng.uniform(10.0, 100.0, size=rows),
    })


class FakeProphet:
    def fit(self, frame):
        self.level = float(frame["y"].iloc[-1])
        self.fit_rows = len(frame)
        return self

    def predict(self, frame):
        return pd.DataFrame({
            "yhat": np.full(len(frame), self.level),
            "trend": np.full(len(frame), self.level),
        })

    def make_future_dataframe(self, periods, freq, include_history):
        return pd.DataFrame({"ds": pd.date_range("2024-01-01", periods=periods + 1, freq=freq)})


def _assert_metrics(metrics):
    assert metrics["n_train"] > 0
    assert metrics["n_val"] > 0
    assert metrics["n_test"] > 0
    assert metrics["final_fit_rows"] > 0


def test_model_a_train_save_load_predict_and_artifact_version(tmp_path):
    frame = synthetic_ohlcv()
    model = ModelA()
    model._new_classifier = lambda scale: XGBClassifier(
        n_estimators=12, max_depth=3, learning_rate=0.1, eval_metric="logloss",
        random_state=42, scale_pos_weight=scale, n_jobs=1,
    )
    metrics = model.train(frame)
    _assert_metrics(metrics)
    artifact = tmp_path / "model_a.joblib"
    model.save(str(artifact))

    loaded = ModelA()
    loaded.load(str(artifact))
    prediction = loaded.predict(frame.tail(300).reset_index(drop=True))
    assert prediction["signal"] in {"ALCISTA", "BAJISTA", "NEUTRAL"}

    incompatible = tmp_path / "old_features.joblib"
    joblib.dump({"feature_set_version": "old", "feature_names": loaded.feature_names}, incompatible)
    with pytest.raises(ValueError, match="Feature set incompatible"):
        ModelA().load(str(incompatible))


def test_model_c_train_save_load_predict(monkeypatch, tmp_path):
    monkeypatch.setattr(ModelC, "_new_prophet", staticmethod(FakeProphet))
    frame = synthetic_ohlcv()
    model = ModelC()
    model._new_xgb = lambda scale: XGBClassifier(
        n_estimators=12, max_depth=3, learning_rate=0.1, eval_metric="logloss",
        random_state=42, scale_pos_weight=scale, n_jobs=1,
    )
    metrics = model.train(frame)
    _assert_metrics(metrics)
    assert metrics["evaluation_note"] == (
        "prophet residuals in-sample on train, out-of-sample on val/test"
    )
    artifact = tmp_path / "model_c.joblib"
    model.save(str(artifact))

    loaded = ModelC()
    loaded.load(str(artifact))
    prediction = loaded.predict(frame.tail(300).reset_index(drop=True))
    assert prediction["signal"] in {"ALCISTA", "BAJISTA", "NEUTRAL"}


def test_model_b_train_save_load_predict(tmp_path):
    pytest.importorskip("torch")
    from models.model_b_gru import ModelB

    model = ModelB()
    model.MAX_EPOCHS = 3
    model.EARLY_STOPPING_PATIENCE = 2
    model.HIDDEN_SIZE = 8
    model.NUM_LAYERS = 1
    model.BATCH_SIZE = 128
    frame = synthetic_ohlcv()
    metrics = model.train(frame)
    _assert_metrics(metrics)
    artifact = tmp_path / "model_b.pt"
    model.save(str(artifact))

    loaded = ModelB()
    loaded.HIDDEN_SIZE = model.HIDDEN_SIZE
    loaded.NUM_LAYERS = model.NUM_LAYERS
    loaded.load(str(artifact))
    prediction = loaded.predict(frame.tail(300).reset_index(drop=True))
    assert prediction["signal"] in {"ALCISTA", "BAJISTA", "NEUTRAL"}
