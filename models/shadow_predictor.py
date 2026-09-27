"""Single-model shadow predictor for the validated Model A artifact."""
from __future__ import annotations

from datetime import datetime, timezone

import numpy as np

from config.models_config import SHADOW_MODEL_NAME, SHADOW_THRESHOLD
from data.feature_engineer import FeatureEngineer


class ShadowPredictor:
    def __init__(self, model_a, db_manager, threshold=SHADOW_THRESHOLD):
        self.models = {"model_a": model_a}
        self.primary_model_name = SHADOW_MODEL_NAME
        self.model_a = model_a
        self.db_manager = db_manager
        self.threshold = float(threshold)

    def predict(self, symbol: str, interval: str, df) -> dict:
        raw = dict(self.model_a.predict(df))
        probability = float(raw["probability_up"])
        signal = "ALCISTA" if probability > self.threshold else "NEUTRAL"
        confidence = "alta" if probability > 0.70 else "media" if probability > self.threshold else "baja"
        raw["signal"] = signal
        raw["confidence"] = confidence
        candle_open = raw.get("timestamp")
        if candle_open is not None and hasattr(candle_open, "to_pydatetime"):
            candle_open = candle_open.to_pydatetime()
        if candle_open is not None:
            if candle_open.tzinfo is None:
                candle_open = candle_open.replace(tzinfo=timezone.utc)
            else:
                candle_open = candle_open.astimezone(timezone.utc)
        prediction_time = datetime.now(timezone.utc)
        return {
            "symbol": symbol, "interval": interval,
            "probability_up": probability, "signal": signal, "confidence": confidence,
            "consensus_probability_up": probability, "consensus_signal": signal,
            "consensus_confidence": confidence, "agreement_count": 1,
            "weights": {SHADOW_MODEL_NAME: 1.0}, SHADOW_MODEL_NAME: raw,
            "timestamp": prediction_time, "candle_open": candle_open,
            "threshold": self.threshold, "mode": "shadow",
        }

    def predict_and_save(self, symbol: str, interval: str, df) -> dict:
        result = self.predict(symbol, interval, df)
        feature_snapshot = {}
        try:
            latest = FeatureEngineer().compute_features(df).iloc[-1]
            feature_snapshot = {
                name: float(value) for name, value in latest.items()
                if name != "target" and isinstance(value, (int, float, np.number)) and np.isfinite(value)
            }
        except (KeyError, TypeError, ValueError):
            feature_snapshot = {}
        self.db_manager.save_prediction({
            "symbol": symbol, "interval": interval, "model_name": SHADOW_MODEL_NAME,
            "predicted_at": result["timestamp"], "probability_up": result["probability_up"],
            "signal": result["signal"], "confidence": result["confidence"],
            "features_snapshot": feature_snapshot, "price_at_prediction": float(df["close"].iloc[-1]),
            "market_condition": {
                "mode": "shadow",
                "threshold": self.threshold,
                "candle_open": result["candle_open"].isoformat() if result["candle_open"] else None,
            },
        })
        return result
