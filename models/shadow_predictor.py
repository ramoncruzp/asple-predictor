"""Single-model shadow predictor for the validated Model A artifact."""
from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import logging
from math import exp, sqrt

from config.models_config import SHADOW_MODEL_NAME, SHADOW_THRESHOLD
from data.feature_engineer import FeatureEngineer


class ShadowPredictor:
    def __init__(self, model_a, db_manager, threshold=SHADOW_THRESHOLD,
                 shadow_models=None, shadow_thresholds=None, validation_status=None):
        self.models = {"model_a": model_a}
        self.shadow_models = dict(shadow_models or {})
        self.shadow_thresholds = dict(shadow_thresholds or {})
        self.validation_status = dict(validation_status or {})
        self.primary_model_name = SHADOW_MODEL_NAME
        self.model_a = model_a
        self.db_manager = db_manager
        self.threshold = float(threshold)

    def _volatility_snapshot(self, symbol, price, prediction_time):
        if self.db_manager is None or not hasattr(self.db_manager, "get_champion_vol_forecast_before"):
            return None
        forecast = self.db_manager.get_champion_vol_forecast_before(symbol, 4, prediction_time)
        if not forecast:
            return None
        sigma = exp(float(forecast["pred_logvol_cal"])) * sqrt(4)
        reference = float(price)
        return {
            "horizon_h": 4,
            "champion": forecast["model_name"],
            "forecast_at": forecast["forecast_at"].isoformat(),
            "reference_price": reference,
            "move_1sigma_pct": sigma * 100.0,
            "range_1sigma": [reference * exp(-sigma), reference * exp(sigma)],
            "range_2sigma": [reference * exp(-2 * sigma), reference * exp(2 * sigma)],
        }

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
        volatility_snapshot = self._volatility_snapshot(
            symbol, float(df["close"].iloc[-1]), result["timestamp"]
        )
        self.db_manager.save_prediction({
            "symbol": symbol, "interval": interval, "model_name": SHADOW_MODEL_NAME,
            "predicted_at": result["timestamp"], "probability_up": result["probability_up"],
            "signal": result["signal"], "confidence": result["confidence"],
            "features_snapshot": feature_snapshot, "price_at_prediction": float(df["close"].iloc[-1]),
            "market_condition": {
                "mode": "shadow",
                "threshold": self.threshold,
                "candle_open": result["candle_open"].isoformat() if result["candle_open"] else None,
                "volatility_4h": volatility_snapshot,
            },
        })
        for name, model in self.shadow_models.items():
            try:
                shadow = dict(model.predict(df))
                probability = float(shadow["probability_up"])
                signal = str(shadow.get("signal") or "NEUTRAL")
                predicted_at = shadow.get("timestamp") or result["timestamp"]
                if isinstance(predicted_at, str):
                    predicted_at = datetime.fromisoformat(predicted_at.replace("Z", "+00:00"))
                if hasattr(predicted_at, "to_pydatetime"):
                    predicted_at = predicted_at.to_pydatetime()
                if not isinstance(predicted_at, datetime):
                    predicted_at = result["timestamp"]
                if predicted_at.tzinfo is None:
                    predicted_at = predicted_at.replace(tzinfo=timezone.utc)
                else:
                    predicted_at = predicted_at.astimezone(timezone.utc)
                volatility_snapshot = self._volatility_snapshot(
                    symbol, float(df["close"].iloc[-1]), predicted_at
                )
                self.db_manager.save_prediction({
                    "symbol": symbol, "interval": interval, "model_name": name,
                    "predicted_at": predicted_at, "probability_up": probability,
                    "signal": signal, "confidence": shadow.get("confidence", "baja"),
                    "features_snapshot": feature_snapshot,
                    "price_at_prediction": float(df["close"].iloc[-1]),
                    "market_condition": {
                        "mode": "shadow",
                        "threshold": self.shadow_thresholds.get(name),
                        "validation_status": self.validation_status.get(name, "not_validated"),
                        "consensus_included": False,
                        "volatility_4h": volatility_snapshot,
                    },
                })
            except Exception:
                logging.getLogger(__name__).exception("Shadow prediction failed for %s", name)
        return result
