"""Verification and condition analysis for prediction outcomes."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from sqlalchemy import text


class LearningEngine:
    def __init__(self, db_manager, binance_client):
        self.db_manager = db_manager
        self.binance_client = binance_client

    def verify_pending_predictions(self) -> int:
        verified = 0
        for prediction in self.db_manager.get_pending_verifications():
            symbol = prediction["symbol"]
            if "/" not in symbol and symbol.endswith("USDT"):
                symbol = f"{symbol[:-4]}/USDT"
            price = self.binance_client.get_current_price(symbol)["price"]
            self.db_manager.save_outcome(prediction["prediction_id"], float(price))
            verified += 1
        return verified

    @staticmethod
    def _snapshot(prediction):
        value = prediction.get("features_snapshot")
        if isinstance(value, str):
            try:
                return json.loads(value)
            except json.JSONDecodeError:
                return {}
        return value or {}

    def analyze_failure(self, prediction, outcome) -> dict:
        features = self._snapshot(prediction)
        reasons = []
        if float(features.get("rsi_14", 50)) > 70:
            reasons.append("RSI>70 ignorado")
        if float(features.get("vol_ratio", 1)) < 1:
            reasons.append("volumen caía")
        return {"prediction_id": prediction.get("prediction_id"), "reasons": reasons or ["condición no identificada"], "features": features}

    def analyze_success(self, prediction, outcome) -> dict:
        features = self._snapshot(prediction)
        correlated = [key for key in ("rsi_14", "adx", "vol_ratio", "ema_cross", "prophet_trend") if key in features]
        return {"prediction_id": prediction.get("prediction_id"), "correlated_features": correlated, "features": features}

    def update_condition_accuracy(self, model_name: str):
        conditions = {"rsi_oversold": lambda f: float(f.get("rsi_14", 50)) < 30,
                      "high_volume": lambda f: float(f.get("vol_ratio", 0)) > 1.5,
                      "strong_trend": lambda f: abs(float(f.get("ema_cross", 0))) == 1}
        results = []
        for name, predicate in conditions.items():
            total = correct = 0
            for prediction in self.db_manager.get_recent_predictions(limit=10000):
                if prediction["model_name"] != model_name or not predicate(self._snapshot(prediction)):
                    continue
                with self.db_manager.engine.connect() as conn:
                    outcome = conn.execute(text("SELECT was_correct FROM outcomes WHERE prediction_id=:id"), {"id": prediction["prediction_id"]}).fetchone()
                if outcome:
                    total += 1
                    correct += int(outcome[0])
            values = {"model_name": model_name, "condition_name": name, "total_predictions": total, "correct_predictions": correct,
                      "accuracy": correct / total if total else 0.0, "last_updated": datetime.now(timezone.utc)}
            with self.db_manager.engine.begin() as conn:
                conn.execute(text("DELETE FROM model_accuracy_by_condition WHERE model_name=:m AND condition_name=:c"), {"m": model_name, "c": name})
                conn.execute(self.db_manager.conditions.insert().values(**values))
            results.append(values)
        return results

    def should_retrain(self, model_name: str) -> bool:
        with self.db_manager.engine.connect() as conn:
            count = conn.execute(text("SELECT COUNT(*) FROM outcomes o JOIN predictions p ON p.prediction_id=o.prediction_id WHERE p.model_name=:m"), {"m": model_name}).scalar_one()
        return int(count) >= 168
