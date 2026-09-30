"""Persistence and queries for predictions and verification outcomes."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import numpy as np
from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer, MetaData, String, Table, Text, UniqueConstraint, create_engine, exists, func, select, text
from config.models_config import TARGET_HORIZON_CANDLES, TARGET_UP_THRESHOLD
from config.models_config import (
    SHADOW_KILL_MIN_LIFT_PTS,
    SHADOW_KILL_MIN_MEAN_RETURN,
    SHADOW_KILL_MIN_SIGNALS,
)


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
            Column("was_correct", Integer, nullable=True), Column("why_correct", String), Column("why_wrong", String))
        self.conditions = Table("model_accuracy_by_condition", self.metadata,
            Column("id", Integer, primary_key=True), Column("model_name", String, nullable=False), Column("condition_name", String, nullable=False),
            Column("total_predictions", Integer, default=0), Column("correct_predictions", Integer, default=0), Column("accuracy", Float, default=0.0), Column("last_updated", DateTime(timezone=True)))
        self.vol_forecasts = Table(
            "vol_forecasts", self.metadata,
            Column("id", Integer, primary_key=True),
            Column("symbol", String, nullable=False),
            Column("horizon_h", Integer, nullable=False),
            Column("model_name", String, nullable=False),
            Column("forecast_at", DateTime(timezone=True), nullable=False),
            Column("made_at", DateTime(timezone=True), nullable=False),
            Column("pred_logvol_raw", Float, nullable=False),
            Column("pred_logvol_cal", Float, nullable=False),
            Column("var_factor", Float, nullable=False),
            Column("is_champion", Integer, nullable=False),
            Column("realized_logvol", Float),
            Column("verified_at", DateTime(timezone=True)),
            UniqueConstraint(
                "symbol", "horizon_h", "model_name", "forecast_at",
                name="uq_vol_forecasts_identity",
            ),
        )
        self.coins_registry = Table(
            "coins_registry", self.metadata,
            Column("symbol", String, primary_key=True),
            Column("active", Integer, nullable=False, default=1),
            Column("added_at", DateTime(timezone=True), nullable=False),
            Column("notes", String, nullable=True),
        )
        self.grids = Table(
            "grids", self.metadata,
            Column("id", Integer, primary_key=True, autoincrement=True),
            Column("symbol", String, nullable=False),
            Column("range_low", Float, nullable=False),
            Column("range_high", Float, nullable=False),
            Column("n_levels", Integer, nullable=False),
            Column("capital_total", Float, nullable=False),
            Column("status", String, nullable=False),
            Column("created_at", DateTime(timezone=True), nullable=False),
            Column("environment", String, nullable=False, default="testnet"),
            Column("open_price", Float),
            Column("closed_at", DateTime),
            Column("fail_reason", String),
            Column("strategy", String, nullable=False, default="simple", server_default="simple"),
            Column("params", Text),
        )
        self.grid_levels = Table(
            "grid_levels", self.metadata,
            Column("grid_id", Integer, ForeignKey("grids.id"), primary_key=True),
            Column("level_idx", Integer, primary_key=True),
            Column("price", Float, nullable=False),
            Column("capital", Float, nullable=False),
            Column("order_id", Integer),
            Column("state", String, nullable=False),
            Column("cycles_completed", Integer, nullable=False, default=0),
            Column("pnl", Float, nullable=False, default=0.0),
            Column("sell_price", Float),
            Column("entry_price", Float),
            Column("bought_at", DateTime),
            Column("stop_loss_pct", Float),
            Column("held_qty", Float, nullable=False, default=0.0),
            Column("client_order_id", String),
            Column("buy_client_order_id", String),
            Column("fee_paid", Float, nullable=False, default=0.0),
            Column("updated_at", DateTime, nullable=False),
        )
        self.monitor_runs = Table(
            "monitor_runs", self.metadata,
            Column("id", Integer, primary_key=True, autoincrement=True),
            Column("started_at", DateTime(timezone=True), nullable=False),
            Column("finished_at", DateTime(timezone=True)),
            Column("trigger", String, nullable=False),
            Column("status", String, nullable=False),
            Column("grids_checked", Integer, nullable=False, default=0),
            Column("grids_failed", Integer, nullable=False, default=0),
            Column("events_written", Integer, nullable=False, default=0),
            Column("duration_ms", Integer), Column("note", String),
        )
        self.grid_events = Table(
            "grid_events", self.metadata,
            Column("id", Integer, primary_key=True, autoincrement=True),
            Column("run_id", Integer), Column("source", String, nullable=False),
            Column("ts", DateTime(timezone=True), nullable=False),
            Column("grid_id", Integer), Column("level_idx", Integer),
            Column("client_order_id", String), Column("order_id", Integer),
            Column("event_type", String, nullable=False), Column("reason", String),
            Column("price", Float), Column("details", Text),
        )
        self.grid_snapshots = Table(
            "grid_snapshots", self.metadata,
            Column("id", Integer, primary_key=True, autoincrement=True),
            Column("run_id", Integer, nullable=False), Column("ts", DateTime(timezone=True), nullable=False),
            Column("grid_id", Integer, nullable=False), Column("level_idx", Integer),
            Column("symbol", String, nullable=False), Column("grid_status", String, nullable=False),
            Column("level_state", String), Column("buy_price", Float), Column("sell_price", Float),
            Column("held_qty", Float), Column("cycles_completed", Integer), Column("pnl_realized", Float),
            Column("fee_paid", Float), Column("market_mid", Float), Column("unrealized_pnl", Float),
            Column("open_orders_db", Integer), Column("inventory_value_usdt", Float),
            Column("in_repository", Integer, nullable=False, default=0),
            Column("origin_grid_id", Integer), Column("origin_level_idx", Integer), Column("age_hours", Float),
            Column("break_prob", Float), Column("sigma_24h", Float),
            Column("trapped_capital_pct", Float), Column("free_cells", Integer),
        )
        self.metadata.create_all(self.engine)
        self._migrate_grid_columns()
        with self.engine.begin() as conn:
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_predictions_symbol_model ON predictions(symbol, model_name)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_predictions_verify_at ON predictions(verify_at)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_outcomes_prediction_id ON outcomes(prediction_id)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_grid_levels_grid_id ON grid_levels(grid_id)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_grids_symbol_status ON grids(symbol, status)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_monitor_runs_started_at ON monitor_runs(started_at)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_grid_events_grid_ts ON grid_events(grid_id, ts)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_grid_events_type_ts ON grid_events(event_type, ts)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_grid_snapshots_grid_ts ON grid_snapshots(grid_id, ts)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_grid_snapshots_run_id ON grid_snapshots(run_id)"))

    def _migrate_grid_columns(self) -> None:
        """Idempotently add the approved 15B columns to existing SQLite databases."""
        if self.engine.dialect.name != "sqlite":
            return
        additions = {
            "grids": {"strategy": "VARCHAR NOT NULL DEFAULT 'simple'", "params": "TEXT"},
            "grid_levels": {
                "entry_price": "FLOAT", "bought_at": "DATETIME", "stop_loss_pct": "FLOAT",
                "buy_client_order_id": "VARCHAR",
            },
            "grid_snapshots": {
                "break_prob": "FLOAT", "sigma_24h": "FLOAT",
                "trapped_capital_pct": "FLOAT", "free_cells": "INTEGER",
            },
        }
        with self.engine.begin() as conn:
            for table, columns in additions.items():
                existing = {row["name"] for row in conn.execute(text(f"PRAGMA table_info({table})")).mappings()}
                for name, sql_type in columns.items():
                    if name not in existing:
                        conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {sql_type}"))
            conn.execute(text("UPDATE grids SET strategy='simple' WHERE strategy IS NULL OR strategy=''"))
            conn.execute(text("UPDATE grids SET strategy='repository' WHERE status='HOLDING'"))

    @staticmethod
    def _json(value: Any) -> str | None:
        return None if value is None else json.dumps(value, default=str)

    def _utc_now(self) -> datetime:
        now = datetime.now(timezone.utc)
        return now.replace(tzinfo=None) if self.engine.dialect.name == "sqlite" else now

    def save_prediction(self, prediction_dict: dict[str, Any]) -> str:
        prediction_id = str(prediction_dict.get("prediction_id") or uuid.uuid4())
        predicted_at = prediction_dict.get("predicted_at") or datetime.now(timezone.utc)
        verify_at = prediction_dict.get("verify_at")
        if verify_at is None:
            interval = str(prediction_dict.get("interval", "4h"))
            hours = int(interval[:-1]) if interval.endswith("h") else 24
            verify_at = predicted_at + timedelta(hours=hours * int(prediction_dict.get("verification_delay_candles", TARGET_HORIZON_CANDLES)))
        values = {"prediction_id": prediction_id, "symbol": prediction_dict["symbol"], "interval": prediction_dict["interval"],
                  "model_name": prediction_dict["model_name"], "predicted_at": predicted_at, "verify_at": verify_at,
                  "probability_up": float(prediction_dict["probability_up"]), "signal": prediction_dict["signal"], "confidence": prediction_dict["confidence"],
                  "features_snapshot": self._json(prediction_dict.get("features_snapshot")), "price_at_prediction": float(prediction_dict["price_at_prediction"]),
                  "market_condition": self._json(prediction_dict.get("market_condition"))}
        with self.engine.begin() as conn:
            conn.execute(self.predictions.insert().values(**values))
        return prediction_id

    def has_prediction_since(
        self, symbol: str, interval: str, model_name: str, since_dt: datetime
    ) -> bool:
        if since_dt.tzinfo is None:
            since_utc = since_dt.replace(tzinfo=timezone.utc)
        else:
            since_utc = since_dt.astimezone(timezone.utc)
        if self.engine.dialect.name == "sqlite":
            since_utc = since_utc.replace(tzinfo=None)
        stmt = select(exists().where(
            self.predictions.c.symbol == symbol,
            self.predictions.c.interval == interval,
            self.predictions.c.model_name == model_name,
            self.predictions.c.predicted_at >= since_utc,
        ))
        with self.engine.connect() as conn:
            return bool(conn.execute(stmt).scalar_one())

    def save_outcome(self, prediction_id: str, price_at_verification: float) -> dict:
        with self.engine.begin() as conn:
            p = conn.execute(select(self.predictions).where(self.predictions.c.prediction_id == prediction_id)).mappings().one()
            change = (float(price_at_verification) - p["price_at_prediction"]) / p["price_at_prediction"] * 100
            actual_label = int(change / 100 > TARGET_UP_THRESHOLD)
            actual = "UP" if actual_label == 1 else "NOT_UP"
            correct = (int(actual_label == 1) if p["signal"] == "ALCISTA" else
                       int(actual_label == 0) if p["signal"] == "BAJISTA" else None)
            outcome = {"prediction_id": prediction_id, "verified_at": datetime.now(timezone.utc), "price_at_verification": float(price_at_verification),
                       "price_change_pct": change, "actual_direction": actual, "was_correct": correct,
                       "why_correct": self._json({"signal": p["signal"]}) if correct is not None and correct else None,
                       "why_wrong": self._json({"signal": p["signal"], "actual_direction": actual}) if correct is not None and not correct else None}
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
            func.count(self.outcomes.c.was_correct).label("evaluated"),
            func.coalesce(func.sum(self.outcomes.c.was_correct), 0).label("correct"),
        ).select_from(
            self.predictions.outerjoin(self.outcomes, self.predictions.c.prediction_id == self.outcomes.c.prediction_id)
        ).where(self.predictions.c.model_name == model_name, self.predictions.c.predicted_at >= cutoff)
        with self.engine.connect() as conn:
            row = conn.execute(stmt).mappings().one()
        total, verified, evaluated, correct = int(row["total"]), int(row["verified"]), int(row["evaluated"]), int(row["correct"])
        return {"model_name": model_name, "total_predictions": total, "verified_predictions": verified,
                "evaluated_predictions": evaluated, "correct_predictions": correct, "accuracy": correct / evaluated if evaluated else None,
                "last_n_days": last_n_days}

    def get_battle_stats(self, model_name: str) -> dict:
        """Return all-time battle counts, including pending predictions."""
        stmt = select(
            func.count(self.predictions.c.id).label("total"),
            func.count(self.outcomes.c.id).label("verified"),
            func.count(self.outcomes.c.was_correct).label("evaluated"),
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
        evaluated = int(row["evaluated"])
        correct = int(row["correct"])
        return {
            "model_name": model_name,
            "total_predictions": total,
            "verified_count": verified,
            "evaluated_count": evaluated,
            "neutral_count": verified - evaluated,
            "correct_count": correct,
            "pending_count": total - verified,
            "accuracy": correct / evaluated if evaluated else None,
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
            total = verified = evaluated = correct = 0
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
                        was_correct = outcomes[prediction.prediction_id]
                        if was_correct is not None:
                            evaluated += 1
                            correct += int(was_correct)
            result.append({"model_name": model_name, "condition_name": condition_name,
                           "total_predictions": total, "verified_predictions": verified,
                           "evaluated_predictions": evaluated, "correct_predictions": correct,
                           "accuracy": correct / evaluated if evaluated else None})
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

    def get_shadow_stats(self, model_name: str, symbol: str, interval: str) -> dict:
        stmt = select(
            self.predictions.c.predicted_at,
            self.predictions.c.signal,
            self.outcomes.c.was_correct,
            self.outcomes.c.actual_direction,
            self.outcomes.c.price_change_pct,
        ).select_from(
            self.predictions.join(
                self.outcomes,
                self.predictions.c.prediction_id == self.outcomes.c.prediction_id,
            )
        ).where(
            self.predictions.c.model_name == model_name,
            self.predictions.c.symbol == symbol,
            self.predictions.c.interval == interval,
        ).order_by(self.predictions.c.predicted_at)
        with self.engine.connect() as conn:
            rows = conn.execute(stmt).mappings().all()

        verified = list(rows)
        all_returns = [float(row["price_change_pct"]) / 100 for row in verified]
        base_rate = (
            sum(row["actual_direction"] == "UP" for row in verified) / len(verified)
            if verified else None
        )
        signals = [row for row in verified if row["signal"] == "ALCISTA"]
        nonoverlap = []
        last_selected_at = None
        try:
            interval_hours = int(interval[:-1]) if interval.endswith("h") else 24
        except (TypeError, ValueError):
            interval_hours = 24
        separation = timedelta(hours=TARGET_HORIZON_CANDLES * interval_hours)
        for row in signals:
            predicted_at = row["predicted_at"]
            if predicted_at.tzinfo is None:
                predicted_at = predicted_at.replace(tzinfo=timezone.utc)
            else:
                predicted_at = predicted_at.astimezone(timezone.utc)
            if last_selected_at is None or predicted_at >= last_selected_at + separation:
                nonoverlap.append(row)
                last_selected_at = predicted_at

        def mean_return(items):
            return (sum(float(row["price_change_pct"]) / 100 for row in items) / len(items)) if items else None

        def precision(items):
            evaluated = [row for row in items if row["was_correct"] is not None]
            return (sum(int(row["was_correct"]) for row in evaluated) / len(evaluated)) if evaluated else None

        nonoverlap_count = len(nonoverlap)
        if nonoverlap_count < SHADOW_KILL_MIN_SIGNALS:
            kill_status = "pending"
        elif (
            base_rate is None
            or (precision(nonoverlap) or 0.0) < base_rate + SHADOW_KILL_MIN_LIFT_PTS
            or (mean_return(nonoverlap) or 0.0) < SHADOW_KILL_MIN_MEAN_RETURN
        ):
            kill_status = "fail"
        else:
            kill_status = "pass"

        return {
            "model_name": model_name,
            "symbol": symbol,
            "interval": interval,
            "n_verified_total": len(verified),
            "base_rate": base_rate,
            "mean_return_all": sum(all_returns) / len(all_returns) if all_returns else None,
            "n_signals": len(signals),
            "precision": precision(signals),
            "mean_return": mean_return(signals),
            "n_nonoverlap": nonoverlap_count,
            "precision_nonoverlap": precision(nonoverlap),
            "mean_return_nonoverlap": mean_return(nonoverlap),
            "kill_status": kill_status,
            "kill_min_signals": SHADOW_KILL_MIN_SIGNALS,
            "kill_min_lift_pts": SHADOW_KILL_MIN_LIFT_PTS,
            "kill_min_mean_return": SHADOW_KILL_MIN_MEAN_RETURN,
        }

    @staticmethod
    def _utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def save_vol_forecasts(self, rows: list[dict]) -> int:
        """Insert forecasts idempotently; repeated startup cycles are harmless."""
        if not rows:
            return 0
        values = [{
            "symbol": row["symbol"],
            "horizon_h": int(row["horizon_h"]),
            "model_name": row["model_name"],
            "forecast_at": self._utc(row["forecast_at"]),
            "made_at": self._utc(row["made_at"]),
            "pred_logvol_raw": float(row["pred_logvol_raw"]),
            "pred_logvol_cal": float(row["pred_logvol_cal"]),
            "var_factor": float(row["var_factor"]),
            "is_champion": int(bool(row["is_champion"])),
        } for row in rows]
        with self.engine.begin() as conn:
            if self.engine.dialect.name == "sqlite":
                from sqlalchemy.dialects.sqlite import insert as dialect_insert
                statement = dialect_insert(self.vol_forecasts).values(values).on_conflict_do_nothing(
                    index_elements=["symbol", "horizon_h", "model_name", "forecast_at"]
                )
            elif self.engine.dialect.name == "postgresql":
                from sqlalchemy.dialects.postgresql import insert as dialect_insert
                statement = dialect_insert(self.vol_forecasts).values(values).on_conflict_do_nothing(
                    index_elements=["symbol", "horizon_h", "model_name", "forecast_at"]
                )
            else:
                statement = self.vol_forecasts.insert().values(values)
            result = conn.execute(statement)
            return int(result.rowcount or 0)

    def get_pending_vol_verifications(self, now: datetime) -> list[dict]:
        """Return matured forecasts awaiting their realized-volatility label."""
        now_utc = self._utc(now)
        statement = select(self.vol_forecasts).where(
            self.vol_forecasts.c.realized_logvol.is_(None),
            self.vol_forecasts.c.verified_at.is_(None),
        ).order_by(self.vol_forecasts.c.forecast_at)
        with self.engine.connect() as conn:
            rows = [dict(row) for row in conn.execute(statement).mappings().all()]
        return [
            row for row in rows
            if self._utc(row["forecast_at"]) + timedelta(hours=int(row["horizon_h"])) <= now_utc
        ]

    def save_vol_realized(self, forecast_id: int, realized_logvol: float) -> bool:
        now = datetime.now(timezone.utc)
        if self.engine.dialect.name == "sqlite":
            now = now.replace(tzinfo=None)
        statement = self.vol_forecasts.update().where(
            self.vol_forecasts.c.id == int(forecast_id),
            self.vol_forecasts.c.realized_logvol.is_(None),
        ).values(realized_logvol=float(realized_logvol), verified_at=now)
        with self.engine.begin() as conn:
            return bool(conn.execute(statement).rowcount)

    def get_vol_battle(self, symbol: str, horizon_h: int) -> list[dict]:
        statement = select(self.vol_forecasts).where(
            self.vol_forecasts.c.symbol == symbol,
            self.vol_forecasts.c.horizon_h == int(horizon_h),
        ).order_by(self.vol_forecasts.c.made_at)
        with self.engine.connect() as conn:
            rows = [dict(row) for row in conn.execute(statement).mappings().all()]
        by_model: dict[str, list[dict]] = {}
        for row in rows:
            by_model.setdefault(row["model_name"], []).append(row)
        result = []
        for model_name, model_rows in by_model.items():
            verified = [row for row in model_rows if row["realized_logvol"] is not None]
            y = np.asarray([row["realized_logvol"] for row in verified], dtype="float64")
            pred = np.asarray([row["pred_logvol_cal"] for row in verified], dtype="float64")
            mse = float(np.mean(np.square(y - pred))) if len(y) else None
            sst = float(np.square(y - y.mean()).sum()) if len(y) else 0.0
            r2 = (
                1.0 - float(np.square(y - pred).sum()) / sst
                if len(y) and sst > 0.0 else None
            )
            result.append({
                "model_name": model_name,
                "n_verified": len(verified),
                "r2_live": r2,
                "mse_live": mse,
                "is_champion": bool(model_rows[-1]["is_champion"]),
            })
        return result

    def get_vol_history(
        self, symbol: str, horizon_h: int, model_name: str, limit: int = 200
    ) -> list[dict]:
        statement = select(
            self.vol_forecasts.c.forecast_at,
            self.vol_forecasts.c.pred_logvol_cal,
            self.vol_forecasts.c.realized_logvol,
        ).where(
            self.vol_forecasts.c.symbol == symbol,
            self.vol_forecasts.c.horizon_h == int(horizon_h),
            self.vol_forecasts.c.model_name == model_name,
        ).order_by(self.vol_forecasts.c.forecast_at.desc()).limit(int(limit))
        with self.engine.connect() as conn:
            rows = [dict(row) for row in conn.execute(statement).mappings().all()]
        rows.reverse()
        return [{
            "forecast_at": row["forecast_at"],
            "pred_vol_pct": float(np.exp(row["pred_logvol_cal"]) * 100.0),
            "realized_vol_pct": (
                float(np.exp(row["realized_logvol"]) * 100.0)
                if row["realized_logvol"] is not None else None
            ),
        } for row in rows]

    def get_latest_vol_forecasts(self, symbol: str) -> list[dict]:
        latest_at = select(func.max(self.vol_forecasts.c.forecast_at)).where(
            self.vol_forecasts.c.symbol == symbol
        ).scalar_subquery()
        statement = select(self.vol_forecasts).where(
            self.vol_forecasts.c.symbol == symbol,
            self.vol_forecasts.c.forecast_at == latest_at,
        ).order_by(self.vol_forecasts.c.horizon_h, self.vol_forecasts.c.model_name)
        with self.engine.connect() as conn:
            return [dict(row) for row in conn.execute(statement).mappings().all()]

    def get_coin(self, symbol: str) -> dict | None:
        statement = select(self.coins_registry).where(self.coins_registry.c.symbol == symbol)
        with self.engine.connect() as conn:
            row = conn.execute(statement).mappings().first()
        return dict(row) if row else None

    def get_active_coins(self) -> list[dict]:
        statement = select(self.coins_registry).where(
            self.coins_registry.c.active == 1
        ).order_by(self.coins_registry.c.added_at)
        with self.engine.connect() as conn:
            return [dict(row) for row in conn.execute(statement).mappings().all()]

    def add_or_reactivate_coin(self, symbol: str, notes: str | None = None) -> dict:
        now = self._utc(datetime.now(timezone.utc))
        if self.engine.dialect.name == "sqlite":
            now = now.replace(tzinfo=None)
        with self.engine.begin() as conn:
            existing = conn.execute(
                select(self.coins_registry).where(self.coins_registry.c.symbol == symbol)
            ).mappings().first()
            if existing is None:
                conn.execute(self.coins_registry.insert().values(
                    symbol=symbol, active=1, added_at=now, notes=notes,
                ))
            else:
                values: dict[str, Any] = {"active": 1}
                if notes is not None:
                    values["notes"] = notes
                conn.execute(
                    self.coins_registry.update()
                    .where(self.coins_registry.c.symbol == symbol)
                    .values(**values)
                )
        return self.get_coin(symbol)

    def deactivate_coin(self, symbol: str) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                self.coins_registry.update()
                .where(self.coins_registry.c.symbol == symbol)
                .values(active=0)
            )

    def seed_coin_if_missing(self, symbol: str, notes: str | None = None) -> None:
        now = self._utc(datetime.now(timezone.utc))
        if self.engine.dialect.name == "sqlite":
            now = now.replace(tzinfo=None)
        with self.engine.begin() as conn:
            existing = conn.execute(
                select(self.coins_registry).where(self.coins_registry.c.symbol == symbol)
            ).mappings().first()
            if existing is None:
                conn.execute(self.coins_registry.insert().values(
                    symbol=symbol, active=1, added_at=now, notes=notes,
                ))

    def create_grid_with_levels(self, grid: dict[str, Any], levels: list[dict[str, Any]]) -> dict:
        now = datetime.now(timezone.utc)
        if self.engine.dialect.name == "sqlite":
            now = now.replace(tzinfo=None)
        grid_values = {
            "symbol": grid["symbol"],
            "range_low": float(grid["range_low"]),
            "range_high": float(grid["range_high"]),
            "n_levels": int(grid["n_levels"]),
            "capital_total": float(grid["capital_total"]),
            "status": grid.get("status", "OPENING"),
            "created_at": grid.get("created_at", now),
            "environment": grid.get("environment", "testnet"),
            "open_price": grid.get("open_price"),
            "closed_at": grid.get("closed_at"),
            "fail_reason": grid.get("fail_reason"),
            "strategy": str(grid.get("strategy", "simple")),
            "params": self._json(grid.get("params")),
        }
        with self.engine.begin() as conn:
            result = conn.execute(self.grids.insert().values(**grid_values))
            grid_id = result.inserted_primary_key[0]
            level_rows = []
            for index, level in enumerate(levels):
                if "sell_price" not in level:
                    raise KeyError("sell_price")
                level_rows.append({
                    "grid_id": grid_id,
                    "level_idx": int(level.get("level_idx", index)),
                    "price": float(level["price"]),
                    "capital": float(level["capital"]),
                    "order_id": level.get("order_id"),
                    "state": level.get("state", level.get("initial_state", "IDLE")),
                    "cycles_completed": int(level.get("cycles_completed", 0)),
                    "pnl": float(level.get("pnl", 0.0)),
                    "sell_price": None if "sell_price" not in level else (
                        None if level["sell_price"] is None else float(level["sell_price"])
                    ),
                    "entry_price": level.get("entry_price"),
                    "bought_at": level.get("bought_at"),
                    "stop_loss_pct": level.get("stop_loss_pct"),
                    "held_qty": float(level.get("held_qty", 0.0)),
                    "client_order_id": level.get("client_order_id"),
                    "buy_client_order_id": level.get("buy_client_order_id"),
                    "fee_paid": float(level.get("fee_paid", 0.0)),
                    "updated_at": level.get("updated_at", now),
                })
            if level_rows:
                conn.execute(self.grid_levels.insert(), level_rows)
        return self.get_grid(grid_id)

    def get_grid(self, grid_id: int) -> dict | None:
        with self.engine.connect() as conn:
            row = conn.execute(select(self.grids).where(self.grids.c.id == grid_id)).mappings().first()
        if not row:
            return None
        result = dict(row)
        if result.get("params") is not None:
            try:
                result["params"] = json.loads(result["params"])
            except (TypeError, json.JSONDecodeError):
                pass
        return result

    def get_grid_levels(self, grid_id: int) -> list[dict]:
        statement = select(self.grid_levels).where(
            self.grid_levels.c.grid_id == grid_id
        ).order_by(self.grid_levels.c.level_idx)
        with self.engine.connect() as conn:
            return [dict(row) for row in conn.execute(statement).mappings().all()]

    def set_level_fields(self, grid_id: int, level_idx: int, **fields: Any) -> dict | None:
        allowed = {"sell_price", "entry_price", "bought_at", "stop_loss_pct", "buy_client_order_id"}
        if not fields or not set(fields) <= allowed:
            raise ValueError("invalid grid level fields")
        return self.update_level(grid_id, level_idx, **fields)

    @staticmethod
    def _decode_json_fields(row: dict, fields: tuple[str, ...]) -> dict:
        for field in fields:
            if row.get(field) is not None:
                try:
                    row[field] = json.loads(row[field])
                except (TypeError, json.JSONDecodeError):
                    pass
        return row

    def start_monitor_run(self, trigger: str) -> dict:
        trigger = str(trigger).upper()
        if trigger not in {"SCHEDULED", "STARTUP"}:
            raise ValueError("trigger must be SCHEDULED or STARTUP")
        values = {
            "started_at": self._utc_now(), "trigger": trigger, "status": "RUNNING",
            "grids_checked": 0, "grids_failed": 0, "events_written": 0,
        }
        with self.engine.begin() as conn:
            run_id = conn.execute(self.monitor_runs.insert().values(**values)).inserted_primary_key[0]
        return self.get_monitor_run(int(run_id))

    def get_monitor_run(self, run_id: int) -> dict | None:
        with self.engine.connect() as conn:
            row = conn.execute(select(self.monitor_runs).where(self.monitor_runs.c.id == run_id)).mappings().first()
        return dict(row) if row else None

    def finish_monitor_run(self, run_id: int, **fields: Any) -> dict | None:
        allowed = {"status", "grids_checked", "grids_failed", "events_written", "duration_ms", "note"}
        if not fields or not set(fields) <= allowed:
            raise ValueError("invalid monitor run update fields")
        values = {**fields, "finished_at": self._utc_now()}
        with self.engine.begin() as conn:
            conn.execute(self.monitor_runs.update().where(self.monitor_runs.c.id == run_id).values(**values))
        return self.get_monitor_run(run_id)

    def get_last_monitor_run(self, exclude_id: int | None = None) -> dict | None:
        statement = select(self.monitor_runs)
        if exclude_id is not None:
            statement = statement.where(self.monitor_runs.c.id != exclude_id)
        statement = statement.order_by(self.monitor_runs.c.id.desc()).limit(1)
        with self.engine.connect() as conn:
            row = conn.execute(statement).mappings().first()
        return dict(row) if row else None

    def mark_stale_runs_interrupted(self, before_id: int) -> int:
        now = self._utc_now()
        with self.engine.begin() as conn:
            result = conn.execute(
                self.monitor_runs.update().where(
                    self.monitor_runs.c.id < int(before_id),
                    self.monitor_runs.c.status == "RUNNING",
                ).values(status="INTERRUPTED", finished_at=now, note="interrupted by a later STARTUP run")
            )
        return int(result.rowcount or 0)

    def add_grid_event(
        self, *, run_id: int | None, source: str, event_type: str, grid_id: int | None = None,
        level_idx: int | None = None, client_order_id: str | None = None,
        order_id: int | None = None, reason: str | None = None, price: float | None = None,
        details: Any = None, ts: datetime | None = None,
    ) -> dict:
        source = str(source).upper()
        if source not in {"MONITOR", "CLI"}:
            raise ValueError("source must be MONITOR or CLI")
        values = {
            "run_id": run_id, "source": source, "ts": ts or self._utc_now(),
            "grid_id": grid_id, "level_idx": level_idx, "client_order_id": client_order_id,
            "order_id": order_id, "event_type": str(event_type), "reason": reason,
            "price": None if price is None else float(price), "details": self._json(details),
        }
        with self.engine.begin() as conn:
            event_id = conn.execute(self.grid_events.insert().values(**values)).inserted_primary_key[0]
        with self.engine.connect() as conn:
            row = conn.execute(select(self.grid_events).where(self.grid_events.c.id == event_id)).mappings().one()
        return self._decode_json_fields(dict(row), ("details",))

    def list_grid_events(
        self, grid_id: int | None = None, event_type: str | None = None, limit: int = 100,
    ) -> list[dict]:
        statement = select(self.grid_events)
        if grid_id is not None:
            statement = statement.where(self.grid_events.c.grid_id == grid_id)
        if event_type is not None:
            statement = statement.where(self.grid_events.c.event_type == event_type)
        statement = statement.order_by(self.grid_events.c.ts.desc(), self.grid_events.c.id.desc()).limit(max(0, int(limit)))
        with self.engine.connect() as conn:
            rows = conn.execute(statement).mappings().all()
        return [self._decode_json_fields(dict(row), ("details",)) for row in rows]

    def get_last_event(self, grid_id: int, event_type: str) -> dict | None:
        statement = select(self.grid_events).where(
            self.grid_events.c.grid_id == int(grid_id),
            self.grid_events.c.event_type == str(event_type),
        ).order_by(self.grid_events.c.ts.desc(), self.grid_events.c.id.desc()).limit(1)
        with self.engine.connect() as conn:
            row = conn.execute(statement).mappings().first()
        return self._decode_json_fields(dict(row), ("details",)) if row else None

    def add_snapshots(self, rows: list[dict[str, Any]]) -> int:
        if not rows:
            return 0
        now = self._utc_now()
        values = [{**row, "ts": row.get("ts") or now} for row in rows]
        with self.engine.begin() as conn:
            conn.execute(self.grid_snapshots.insert(), values)
        return len(values)

    def list_grid_snapshots(
        self, grid_id: int | None = None, run_id: int | None = None, limit: int = 1000,
    ) -> list[dict]:
        statement = select(self.grid_snapshots)
        if grid_id is not None:
            statement = statement.where(self.grid_snapshots.c.grid_id == grid_id)
        if run_id is not None:
            statement = statement.where(self.grid_snapshots.c.run_id == run_id)
        statement = statement.order_by(self.grid_snapshots.c.ts.desc(), self.grid_snapshots.c.id.desc()).limit(max(0, int(limit)))
        with self.engine.connect() as conn:
            return [dict(row) for row in conn.execute(statement).mappings().all()]

    def list_grids_by_status(self, statuses: set[str] | list[str] | tuple[str, ...]) -> list[dict]:
        normalized = sorted({str(status).upper() for status in statuses})
        if not normalized:
            return []
        statement = select(self.grids).where(self.grids.c.status.in_(normalized)).order_by(self.grids.c.id)
        with self.engine.connect() as conn:
            rows = [dict(row) for row in conn.execute(statement).mappings().all()]
        return [self._decode_json_fields(row, ("params",)) for row in rows]

    def transition_grid_status(self, grid_id: int, from_statuses: set[str] | list[str], to_status: str) -> bool:
        expected = sorted({str(status).upper() for status in from_statuses})
        if not expected:
            return False
        with self.engine.begin() as conn:
            result = conn.execute(
                self.grids.update().where(
                    self.grids.c.id == grid_id, self.grids.c.status.in_(expected),
                ).values(status=str(to_status).upper())
            )
        return bool(result.rowcount)

    def move_level_to_grid(
        self, from_grid_id: int, from_level_idx: int, to_grid_id: int,
        new_level_idx: int, conn: Any = None,
    ) -> dict:
        def move(connection):
            row = connection.execute(select(self.grid_levels).where(
                self.grid_levels.c.grid_id == from_grid_id,
                self.grid_levels.c.level_idx == from_level_idx,
            )).mappings().first()
            if row is None:
                raise ValueError(f"grid level {from_grid_id}/{from_level_idx} does not exist")
            values = {**dict(row), "grid_id": to_grid_id, "level_idx": new_level_idx}
            connection.execute(self.grid_levels.delete().where(
                self.grid_levels.c.grid_id == from_grid_id,
                self.grid_levels.c.level_idx == from_level_idx,
            ))
            connection.execute(self.grid_levels.insert().values(**values))
            return values

        if conn is not None:
            return move(conn)
        with self.engine.begin() as connection:
            return move(connection)

    def update_grid(self, grid_id: int, **fields: Any) -> dict | None:
        allowed = {column.name for column in self.grids.columns} - {"id"}
        if not fields or not set(fields) <= allowed:
            raise ValueError("invalid grid update fields")
        with self.engine.begin() as conn:
            conn.execute(self.grids.update().where(self.grids.c.id == grid_id).values(**fields))
        return self.get_grid(grid_id)

    def update_level(self, grid_id: int, level_idx: int, **fields: Any) -> dict | None:
        allowed = {column.name for column in self.grid_levels.columns} - {"grid_id", "level_idx"}
        if not set(fields) <= allowed:
            raise ValueError("invalid grid level update fields")
        now = datetime.now(timezone.utc)
        if self.engine.dialect.name == "sqlite":
            now = now.replace(tzinfo=None)
        values = {**fields, "updated_at": now}
        with self.engine.begin() as conn:
            conn.execute(
                self.grid_levels.update().where(
                    self.grid_levels.c.grid_id == grid_id,
                    self.grid_levels.c.level_idx == level_idx,
                ).values(**values)
            )
        return next((row for row in self.get_grid_levels(grid_id) if row["level_idx"] == level_idx), None)

    def add_grid_level(self, grid_id: int, level: dict[str, Any]) -> dict:
        now = datetime.now(timezone.utc)
        if self.engine.dialect.name == "sqlite":
            now = now.replace(tzinfo=None)
        values = {
            "grid_id": int(grid_id), "level_idx": int(level["level_idx"]),
            "price": float(level["price"]), "capital": float(level["capital"]),
            "order_id": level.get("order_id"), "state": level.get("state", "IDLE"),
            "cycles_completed": int(level.get("cycles_completed", 0)),
            "pnl": float(level.get("pnl", 0)), "sell_price": level.get("sell_price"),
            "entry_price": level.get("entry_price"), "bought_at": level.get("bought_at"),
            "stop_loss_pct": level.get("stop_loss_pct"), "held_qty": float(level.get("held_qty", 0)),
            "client_order_id": level.get("client_order_id"),
            "buy_client_order_id": level.get("buy_client_order_id"),
            "fee_paid": float(level.get("fee_paid", 0)), "updated_at": now,
        }
        with self.engine.begin() as conn:
            conn.execute(self.grid_levels.insert().values(**values))
        return next(row for row in self.get_grid_levels(grid_id) if row["level_idx"] == values["level_idx"])

    def count_open_grids(self) -> int:
        statement = select(func.count()).select_from(self.grids).where(
            self.grids.c.status.in_(("OPENING", "ACTIVE", "PAUSED", "CLOSING"))
        )
        with self.engine.connect() as conn:
            return int(conn.execute(statement).scalar_one())

    def has_open_grid(self, symbol: str) -> bool:
        statement = select(exists().where(
            self.grids.c.symbol == symbol,
            self.grids.c.status.in_(("OPENING", "ACTIVE", "PAUSED", "CLOSING", "HOLDING")),
        ))
        with self.engine.connect() as conn:
            return bool(conn.execute(statement).scalar_one())

    def list_open_grids(self) -> list[dict]:
        statement = select(self.grids).where(
            self.grids.c.status.in_(("OPENING", "ACTIVE", "PAUSED", "CLOSING"))
        ).order_by(self.grids.c.id)
        with self.engine.connect() as conn:
            return [dict(row) for row in conn.execute(statement).mappings().all()]
