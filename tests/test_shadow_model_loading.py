import json
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from database.db_manager import DBManager
from models.shadow_loader import load_optional_shadow_models
from models.shadow_predictor import ShadowPredictor


class LoaderModel:
    def __init__(self, should_fail=False):
        self.should_fail = should_fail

    def load(self, path):
        if self.should_fail:
            raise ValueError("stub load failure")


def test_optional_models_missing_or_failing_do_not_block_loading_the_other(tmp_path):
    (tmp_path / "model_b_xrp_1h.pt").write_bytes(b"stub")
    (tmp_path / "model_c_xrp_1h.joblib").write_bytes(b"stub")
    loaded, report = load_optional_shadow_models(
        tmp_path,
        model_factories={
            "model_b": lambda: LoaderModel(),
            "model_c": lambda: LoaderModel(should_fail=True),
        },
    )
    assert set(loaded) == {"model_b"}
    assert report["model_b"]["available"] is True
    assert report["model_b"]["duration_ms"] >= 0
    assert report["model_b"]["rss_delta_bytes"] is None
    assert report["model_c"]["available"] is False
    assert "stub load failure" in report["model_c"]["reason"]

    missing, missing_report = load_optional_shadow_models(tmp_path / "absent")
    assert missing == {}
    assert missing_report["model_b"]["available"] is False
    assert "FileNotFoundError" in missing_report["model_b"]["reason"]


class StubPredictionModel:
    def __init__(self, signal, probability):
        self.signal, self.probability = signal, probability

    def predict(self, frame):
        return {
            "signal": self.signal,
            "probability_up": self.probability,
            "confidence": "media",
            "timestamp": frame["timestamp"].iloc[-1],
        }


class ModelAStub(StubPredictionModel):
    def predict(self, frame):
        return {**super().predict(frame), "timestamp": frame["timestamp"].iloc[-1]}


def test_b_and_c_shadow_predictions_are_saved_individually_outside_consensus():
    rng = np.random.default_rng(2026)
    close = 100 + np.cumsum(rng.normal(0, 0.1, 200))
    timestamps = pd.date_range("2026-01-01", periods=200, freq="1h", tz="UTC")
    frame = pd.DataFrame({
        "timestamp": timestamps,
        "close_time": timestamps + pd.Timedelta(hours=1) - pd.Timedelta(milliseconds=1),
        "open": close, "high": close + 0.2, "low": close - 0.2,
        "close": close, "volume": rng.uniform(10, 100, 200),
    })
    db = DBManager("sqlite:///:memory:")
    try:
        db.get_champion_vol_forecast_before = lambda symbol, horizon, as_of: {
            "forecast_at": as_of - timedelta(minutes=5),
            "model_name": "GBM",
            "pred_logvol_cal": 0.01,
        }
        predictor = ShadowPredictor(
            ModelAStub("ALCISTA", 0.62), db,
            shadow_models={
                "model_b": StubPredictionModel("BAJISTA", 0.41),
                "model_c": StubPredictionModel("NEUTRAL", 0.50),
            },
            shadow_thresholds={"model_b": 0.55, "model_c": 0.55},
            validation_status={"model_b": "not_validated", "model_c": "not_validated"},
        )
        result = predictor.predict_and_save("XRPUSDT", "1h", frame)
        rows = db.get_recent_predictions(limit=10)
        by_name = {row["model_name"]: row for row in rows}
        assert set(by_name) == {"model_a", "model_b", "model_c"}
        assert result["weights"] == {"model_a": 1.0}
        assert by_name["model_b"]["probability_up"] == 0.41
        assert by_name["model_c"]["probability_up"] == 0.50
        assert json.loads(by_name["model_b"]["market_condition"])["threshold"] == 0.55
        assert json.loads(by_name["model_c"]["market_condition"])["consensus_included"] is False
        snapshot = json.loads(by_name["model_a"]["market_condition"])["volatility_4h"]
        assert snapshot["champion"] == "GBM"
        snapshot_at = pd.Timestamp(snapshot["forecast_at"])
        prediction_at = pd.Timestamp(by_name["model_a"]["predicted_at"])
        snapshot_at = snapshot_at.tz_localize("UTC") if snapshot_at.tzinfo is None else snapshot_at
        prediction_at = prediction_at.tz_localize("UTC") if prediction_at.tzinfo is None else prediction_at
        assert snapshot_at < prediction_at
        assert len(snapshot["range_1sigma"]) == len(snapshot["range_2sigma"]) == 2

        verified_price = sum(snapshot["range_1sigma"]) / 2
        with db.engine.begin() as connection:
            connection.execute(db.outcomes.insert().values(
                prediction_id=by_name["model_a"]["prediction_id"],
                verified_at=datetime.now(timezone.utc),
                price_at_verification=verified_price,
                price_change_pct=0.0,
                actual_direction="UP",
                was_correct=1,
            ))
        coverage = db.get_volatility_coverage("XRPUSDT", "1h")
        assert coverage["n"] == 1
        assert coverage["inside_1sigma"] == 1
        assert coverage["sample_sufficient"] is False
    finally:
        db.engine.dispose()


class FalseLoaderModel:
    def load(self, path):
        return False

def test_model_b_false_load_reports_missing_pytorch_precisely(tmp_path):
    (tmp_path / "model_b_xrp_1h.pt").write_bytes(b"stub")
    _, report = load_optional_shadow_models(tmp_path, model_factories={"model_b": FalseLoaderModel})
    assert report["model_b"]["available"] is False
    assert report["model_b"]["reason"] == "RuntimeError: PyTorch (torch) no est\u00e1 disponible en este entorno"

def test_model_b_version_incompatibility_is_not_swallowed(tmp_path):
    (tmp_path / "model_b_xrp_1h.pt").write_bytes(b"stub")
    class VersionErrorModel:
        def load(self, path):
            raise ValueError("artifact version incompatible")
    _, report = load_optional_shadow_models(tmp_path, model_factories={"model_b": VersionErrorModel})
    assert report["model_b"]["available"] is False
    assert report["model_b"]["reason"] == "ValueError: artifact version incompatible"
