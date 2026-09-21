"""Persistence and queries for predictions and verification outcomes."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import Column, DateTime, Float, Integer, MetaData, String, Table, create_engine, func, select, text


class DBManager:
    def __init__(self, db_url: str):
        self.engine = create_engine(db_url, future=True)
        self.metadata = MetaData()
        self.predictions = Table("predictions", self.metadata,
            Column("id", Integer, primary_key=True), Column("prediction_id", String, unique=True, nullable=False),
            Column("symbol", String, nullable=False), Column("interval", String, nullable=False),
            Column("model_name", String, nullable=False), Column("predicted_at", DateTime(timezone=True), nullable=False),
            Column("verify_at", DateTime(timezone=True), nullable=False), Column("probability_up", Float, nullable=False),
            Column("signal", String, nullable=False), Column("confidence", String, nullable=False),
            Column("features_snapshot", String), Column("price_at_prediction", Float, nullable=False), Column("market_condition", String))
        self.outcomes = Table("outcomes", self.metadata,
            Column("id", Integer, primary_key=True), Column("prediction_id", String, nullable=False),
            Column("verified_at", DateTime(timezone=True), nullable=False), Column("price_at_verification", Float, nullable=False),
            Column("price_change_pct", Float, nullable=False), Column("actual_direction", String, nullable=False),
            Column("was_correct", Integer, nullable=False), Column("why_correct", String), Column("why_wrong", String))
        self.conditions = Table("model_accuracy_by_condition", self.metadata,
            Column("id", Integer, primary_key=True), Column("model_name", String, nullable=False), Column("condition_name", String, nullable=False),
            Column("total_predictions", Integer, default=0), Column("correct_predictions", Integer, default=0), Column("accuracy", Float, default=0.0), Column("last_updated", DateTime(timezone=True)))
        self.metadata.create_all(self.engine)
        with self.engine.begin() as conn:
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_predictions_symbol_model ON predictions(symbol, model_name)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_predictions_verify_at ON predictions(verify_at)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_outcomes_prediction_id ON outcomes(prediction_id)"))

    @staticmethod
    def _json(value: Any) -> str | None:
        return None if value is None else json.dumps(value, default=str)

    def save_prediction(self, prediction_dict: dict[str, Any]) -> str:
        prediction_id = str(prediction_dict.get("prediction_id") or uuid.uuid4())
        predicted_at = prediction_dict.get("predicted_at") or datetime.now(timezone.utc)
        verify_at = prediction_dict.get("verify_at")
        if verify_at is None:
            interval = str(prediction_dict.get("interval", "4h"))
            hours = int(interval[:-1]) if interval.endswith("h") else 24
            verify_at = predicted_at + timedelta(hours=hours * int(prediction_dict.get("verification_delay_candles", 4)))
        values = {"prediction_id": prediction_id, "symbol": prediction_dict["symbol"], "interval": prediction_dict["interval"],
                  "model_name": prediction_dict["model_name"], "predicted_at": predicted_at, "verify_at": verify_at,
                  "probability_up": float(prediction_dict["probability_up"]), "signal": prediction_dict["signal"], "confidence": prediction_dict["confidence"],
                  "features_snapshot": self._json(prediction_dict.get("features_snapshot")), "price_at_prediction": float(prediction_dict["price_at_prediction"]),
                  "market_condition": self._json(prediction_dict.get("market_condition"))}
        with self.engine.begin() as conn:
            conn.execute(self.predictions.insert().values(**values))
        return prediction_id

    def save_outcome(self, prediction_id: str, price_at_verification: float) -> dict:
        with self.engine.begin() as conn:
            p = conn.execute(select(self.predictions).where(self.predictions.c.prediction_id == prediction_id)).mappings().one()
            change = (float(price_at_verification) - p["price_at_prediction"]) / p["price_at_prediction"] * 100
            actual = "UP" if change > 0 else "DOWN"
            expected = "UP" if p["signal"] == "ALCISTA" else "DOWN"
            correct = int(actual == expected)
            outcome = {"prediction_id": prediction_id, "verified_at": datetime.now(timezone.utc), "price_at_verification": float(price_at_verification),
                       "price_change_pct": change, "actual_direction": actual, "was_correct": correct,
                       "why_correct": self._json({"signal": p["signal"]}) if correct else None,
                       "why_wrong": self._json({"signal": p["signal"], "actual_direction": actual}) if not correct else None}
            conn.execute(self.outcomes.insert().values(**outcome))
        return outcome

    def get_pending_verifications(self) -> list[dict]:
        now = datetime.now(timezone.utc)
        stmt = select(self.predictions).where(self.predictions.c.verify_at <= now).where(~self.predictions.c.prediction_id.in_(select(self.outcomes.c.prediction_id)))
        with self.engine.connect() as conn:
            return [dict(row) for row in conn.execute(stmt).mappings().all()]

    def get_accuracy_by_model(self, model_name: str, last_n_days: int = 30) -> dict:
        cutoff = datetime.now(timezone.utc) - timedelta(days=last_n_days)
        stmt = select(
            func.count(self.predictions.c.id).label("total"),
            func.count(self.outcomes.c.id).label("verified"),
            func.coalesce(func.sum(self.outcomes.c.was_correct), 0).label("correct"),
        ).select_from(
            self.predictions.outerjoin(self.outcomes, self.predictions.c.prediction_id == self.outcomes.c.prediction_id)
        ).where(self.predictions.c.model_name == model_name, self.predictions.c.predicted_at >= cutoff)
        with self.engine.connect() as conn:
            row = conn.execute(stmt).mappings().one()
        total, verified, correct = int(row["total"]), int(row["verified"]), int(row["correct"])
        return {"model_name": model_name, "total_predictions": total, "verified_predictions": verified,
                "correct_predictions": correct, "accuracy": correct / verified if verified else None,
                "last_n_days": last_n_days}

    def get_battle_stats(self, model_name: str) -> dict:
        """Return all-time battle counts, including pending predictions."""
        stmt = select(
            func.count(self.predictions.c.id).label("total"),
            func.count(self.outcomes.c.id).label("verified"),
            func.coalesce(func.sum(self.outcomes.c.was_correct), 0).label("correct"),
        ).select_from(
            self.predictions.outerjoin(
                self.outcomes,
                self.predictions.c.prediction_id == self.outcomes.c.prediction_id,
            )
        ).where(self.predictions.c.model_name == model_name)
        with self.engine.connect() as conn:
            row = conn.execute(stmt).mappings().one()
        total = int(row["total"])
        verified = int(row["verified"])
        correct = int(row["correct"])
        return {
            "model_name": model_name,
            "total_predictions": total,
            "verified_count": verified,
            "correct_count": correct,
            "pending_count": total - verified,
            "accuracy": correct / verified if verified else None,
        }

    def get_accuracy_by_condition(self, model_name: str) -> list[dict]:
        condition_rules = {
            "rsi_oversold": lambda features, market: float(features.get("rsi_14", 100)) < 30,
            "high_volume": lambda features, market: float(features.get("vol_ratio", 0)) > 1.5,
            "strong_trend": lambda features, market: abs(float(features.get("ema_cross", 0))) == 1,
            "ranging_market": lambda features, market: market.get("trend") == "ranging",
            "post_macd_cross": lambda features, market: bool(features.get("macd_cross", False)),
        }
        with self.engine.connect() as conn:
            predictions = conn.execute(select(self.predictions.c.prediction_id, self.predictions.c.features_snapshot, self.predictions.c.market_condition).where(self.predictions.c.model_name == model_name)).mappings().all()
            outcomes = {row.prediction_id: row.was_correct for row in conn.execute(select(self.outcomes.c.prediction_id, self.outcomes.c.was_correct)).all()}
        result = []
        for condition_name, rule in condition_rules.items():
            total = verified = correct = 0
            for prediction in predictions:
                try:
                    features = json.loads(prediction.features_snapshot or "{}")
                    market = json.loads(prediction.market_condition or "{}")
                except json.JSONDecodeError:
                    features, market = {}, {}
                if rule(features, market):
                    total += 1
                    if prediction.prediction_id in outcomes:
                        verified += 1
                        correct += int(outcomes[prediction.prediction_id])
            result.append({"model_name": model_name, "condition_name": condition_name,
                           "total_predictions": total, "verified_predictions": verified,
                           "correct_predictions": correct, "accuracy": correct / verified if verified else None})
        return result

    def get_recent_predictions(self, symbol: str | None = None, limit: int = 50) -> list[dict]:
        stmt = select(self.predictions).order_by(self.predictions.c.predicted_at.desc()).limit(limit)
        if symbol:
            stmt = stmt.where(self.predictions.c.symbol == symbol)
        with self.engine.connect() as conn:
            return [dict(row) for row in conn.execute(stmt).mappings().all()]

    def get_predictions_with_outcomes(self, symbol=None, interval=None, model_name=None, days=None, limit=50):
        stmt = select(self.predictions, self.outcomes).select_from(
            self.predictions.outerjoin(self.outcomes, self.predictions.c.prediction_id == self.outcomes.c.prediction_id)
        ).order_by(self.predictions.c.predicted_at.desc()).limit(limit)
        if symbol: stmt = stmt.where(self.predictions.c.symbol == symbol)
        if interval: stmt = stmt.where(self.predictions.c.interval == interval)
        if model_name: stmt = stmt.where(self.predictions.c.model_name == model_name)
        if days: stmt = stmt.where(self.predictions.c.predicted_at >= datetime.now(timezone.utc) - timedelta(days=days))
        with self.engine.connect() as conn:
            rows = []
            for row in conn.execute(stmt).mappings():
                item = dict(row)
                item["is_verified"] = item.get("verified_at") is not None
                item["was_correct"] = item.get("was_correct") if item["is_verified"] else None
                rows.append(item)
            return rows
