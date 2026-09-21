"""Weighted parallel ensemble with optional Model D."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any

import numpy as np
from data.feature_engineer import FeatureEngineer

try:
    from models.model_d_tft import ModelD
    model_d_available = True
except (ImportError, OSError, Exception) as e:
    print(f"[Ensemble] Model D no disponible: {e}")
    ModelD = None
    model_d_available = False
    # Limpiar módulos contaminados para no bloquear imports posteriores.
    import sys
    mods_to_remove = [
        k for k in list(sys.modules.keys())
        if k.startswith("torch")
        or k.startswith("pytorch_forecasting")
        or "model_d" in k
    ]
    for k in mods_to_remove:
        del sys.modules[k]


class EnsemblePredictor:
    def __init__(self, model_a, model_b, model_c, db_manager, model_d=None):
        self.models = {"model_a": model_a, "model_b": model_b, "model_c": model_c}
        if model_d is not None:
            self.models["model_d"] = model_d
        self.db_manager = db_manager

    @staticmethod
    def _accuracy(model) -> float:
        info = model.get_model_info()
        return float(info.get("accuracy_acumulada") or 0.0)

    @staticmethod
    def _direction(result: dict[str, Any]) -> str:
        return result.get("signal", "NEUTRAL")

    def predict(self, symbol: str, interval: str, df) -> dict:
        def run(item):
            name, model = item
            return name, model.predict(df)

        with ThreadPoolExecutor(max_workers=len(self.models), thread_name_prefix="ensemble") as executor:
            results = dict(executor.map(run, self.models.items()))
        weights = {name: 1.0 / len(self.models) for name in self.models}
        for name, model in self.models.items():
            if self._accuracy(model) > 0.60:
                weights[name] *= 1.20
        total = sum(weights.values())
        weights = {name: value / total for name, value in weights.items()}
        probability = sum(weights[name] * float(results[name]["probability_up"]) for name in weights)
        signals = {name: self._direction(results[name]) for name in results}
        bullish = sum(signal == "ALCISTA" for signal in signals.values())
        bearish = sum(signal == "BAJISTA" for signal in signals.values())
        majority_signal, agreement = ("ALCISTA", bullish) if bullish >= bearish else ("BAJISTA", bearish)
        if agreement < 2 or 0.45 <= probability <= 0.55:
            signal, confidence = "NEUTRAL", "baja"
        elif agreement == len(self.models) and (probability >= 0.60 or probability <= 0.40):
            signal, confidence = majority_signal, "muy_alta"
        elif probability >= 0.60 or probability <= 0.40:
            signal, confidence = majority_signal, "alta"
        else:
            signal, confidence = "NEUTRAL", "baja"
        return {"symbol": symbol, "interval": interval, "consensus_probability_up": float(probability),
                "consensus_signal": signal, "consensus_confidence": confidence, "agreement_count": agreement,
                **results,
                "weights": weights, "timestamp": datetime.now(timezone.utc)}

    def predict_and_save(self, symbol: str, interval: str, df) -> dict:
        consensus = self.predict(symbol, interval, df)
        price = float(df["close"].iloc[-1])
        feature_snapshot = {}
        try:
            latest_features = FeatureEngineer().compute_features(df).iloc[-1]
            feature_snapshot = {name: float(value) for name, value in latest_features.items()
                                if name != "target" and isinstance(value, (int, float, np.number)) and np.isfinite(value)}
        except (KeyError, TypeError, ValueError):
            feature_snapshot = {}
        for name in self.models:
            result = consensus[name]
            self.db_manager.save_prediction({"symbol": symbol, "interval": interval, "model_name": name,
                "predicted_at": consensus["timestamp"], "probability_up": result["probability_up"],
                "signal": result["signal"], "confidence": result["confidence"], "features_snapshot": feature_snapshot,
                "price_at_prediction": price, "market_condition": {"ensemble_signal": consensus["consensus_signal"]}})
        self.db_manager.save_prediction({"symbol": symbol, "interval": interval, "model_name": "ensemble",
            "predicted_at": consensus["timestamp"], "probability_up": consensus["consensus_probability_up"],
            "signal": consensus["consensus_signal"], "confidence": consensus["consensus_confidence"],
            "features_snapshot": {"agreement_count": consensus["agreement_count"]}, "price_at_prediction": price,
            "market_condition": {"weights": consensus["weights"]}})
        return consensus
