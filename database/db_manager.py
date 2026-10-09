"""Persistence and queries for predictions and verification outcomes."""
from __future__ import annotations

import json
import time
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import numpy as np
from sqlalchemy import Column, DateTime, Float, ForeignKey, Index, Integer, MetaData, String, Table, Text, UniqueConstraint, case, create_engine, event, exists, func, or_, select, text
from sqlalchemy.exc import OperationalError
from config.models_config import TARGET_HORIZON_CANDLES, TARGET_UP_THRESHOLD
from config.models_config import (
    SHADOW_KILL_MIN_LIFT_PTS,
    SHADOW_KILL_MIN_MEAN_RETURN,
    SHADOW_KILL_MIN_SIGNALS,
    VOL_HORIZONS, VOL_SYMBOL, VOL_WIDEN_DISAGREEMENT_PCT, VOL_WIDEN_K_ACTIVE,
)


class DBManager:
    def __init__(self, db_url: str):
        self.engine = create_engine(db_url, future=True)
        if (self.engine.dialect.name == "sqlite"
                and self.engine.url.database not in {None, "", ":memory:"}):
            @event.listens_for(self.engine, "connect")
            def _configure_sqlite_connection(connection, _record):
                cursor = connection.cursor()
                cursor.execute("PRAGMA journal_mode=WAL")
                cursor.execute("PRAGMA busy_timeout=5000")
                cursor.execute("PRAGMA synchronous=NORMAL")
                cursor.close()
        self.metadata = MetaData()
        self.predictions = Table("predictions", self.metadata,
            Column("id", Integer, primary_key=True), Column("prediction_id", String, unique=True, nullable=False),
            Column("symbol", String, nullable=False), Column("interval", String, nullable=False),
            Column("model_name", String, nullable=False), Column("predicted_at", DateTime(timezone=True), nullable=False),
            Column("verify_at", DateTime(timezone=True), nullable=False), Column("probability_up", Float, nullable=False),
            Column("signal", String, nullable=False), Column("confidence", String, nullable=False),
            Column("features_snapshot", String), Column("price_at_prediction", Float, nullable=False), Column("market_condition", String),
            Column("verification_status", String), Column("verify_delay_h", Float))
        self.outcomes = Table("outcomes", self.metadata,
            Column("id", Integer, primary_key=True), Column("prediction_id", String, nullable=False),
            Column("verified_at", DateTime(timezone=True), nullable=False), Column("price_at_verification", Float, nullable=False),
            Column("price_change_pct", Float, nullable=False), Column("actual_direction", String, nullable=False),
            Column("was_correct", Integer, nullable=True), Column("why_correct", String), Column("why_wrong", String),
            Column("verify_delay_h", Float))
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
            Column("artifact_version", String, nullable=True),
            UniqueConstraint(
                "symbol", "horizon_h", "model_name", "forecast_at",
                name="uq_vol_forecasts_identity",
            ),
        )
        self.vol_widen_suggestions = Table(
            "vol_widen_suggestions", self.metadata,
            Column("id", Integer, primary_key=True, autoincrement=True),
            Column("symbol", String, nullable=False),
            Column("horizon_h", Integer, nullable=False),
            Column("computed_at", DateTime(timezone=True), nullable=False),
            Column("kind", String, nullable=False, default="suggestion"),
            Column("n", Integer, nullable=False, default=0),
            Column("n_effective", Float, nullable=False, default=0.0),
            Column("k_global", Float), Column("k2_global", Float),
            Column("k_raw", Float), Column("bias_log", Float), Column("vol_scale_suggested", Float),
            Column("k_stress_raw", Float), Column("k_stress_smoothed", Float),
            Column("k_stress_q68", Float), Column("k_base_q68", Float),
            Column("stress_count", Integer), Column("base_count", Integer),
            Column("ci_low", Float), Column("ci_high", Float), Column("ci_width", Float),
            Column("k_global_ci_low", Float), Column("k_global_ci_high", Float),
            Column("k_global_ci_width", Float),
            Column("k_stress_ci_low", Float), Column("k_stress_ci_high", Float),
            Column("k_stress_ci_width", Float),
            Column("k_active", Float), Column("status", String),
            Column("disagreement_threshold_suggested", Float),
            Column("disagreement_pct_active", Float),
            Column("stress_threshold", Float), Column("dispersion_n", Integer),
            Column("disagreement_n", Integer), Column("disagreement_status", String),
            Column("disagreement_progress", Float), Column("progress_pct", Float),
            Column("days_estimated", Float),
            Column("audit_actor", String), Column("audit_before", Text), Column("audit_after", Text),
        )
        self.coins_registry = Table(
            "coins_registry", self.metadata,
            Column("symbol", String, primary_key=True),
            Column("active", Integer, nullable=False, default=1),
            Column("added_at", DateTime(timezone=True), nullable=False),
            Column("notes", String, nullable=True),
        )
        self.coin_readiness = Table(
            "coin_readiness", self.metadata,
            Column("symbol", String, primary_key=True),
            Column("state", String, nullable=False),
            Column("stage_detail", String),
            Column("progress_pct", Float),
            Column("history_days", Integer),
            Column("error", Text),
            Column("started_at", DateTime(timezone=True)),
            Column("updated_at", DateTime(timezone=True), nullable=False),
            Column("ready_at", DateTime(timezone=True)),
            Column("pid", Integer),
        )
        self.training_jobs = Table(
            "training_jobs", self.metadata,
            Column("id", Integer, primary_key=True, autoincrement=True),
            Column("symbol", String, nullable=False),
            Column("interval", String, nullable=False),
            Column("models", Text, nullable=False),
            Column("days", Integer, nullable=False),
            Column("status", String, nullable=False),
            Column("phase", String, nullable=False),
            Column("created_at", DateTime(timezone=True), nullable=False),
            Column("started_at", DateTime(timezone=True)),
            Column("finished_at", DateTime(timezone=True)),
            Column("pid", Integer),
            Column("exit_code", Integer),
            Column("log_path", Text),
            Column("error", Text),
            Column("confirm_reset_evaluation", Integer, nullable=False, default=0, server_default="0"),
        )
        self.grids = Table(
            "grids", self.metadata,
            Column("id", Integer, primary_key=True, autoincrement=True),
            Column("symbol", String, nullable=False),
            Column("range_low", Float, nullable=False),
            Column("range_high", Float, nullable=False),
            Column("n_levels", Integer, nullable=False),
            Column("capital_total", Float, nullable=False),
            Column("reserve", Float, nullable=False, default=0.0, server_default="0"),
            Column("status", String, nullable=False),
            Column("created_at", DateTime(timezone=True), nullable=False),
            Column("environment", String, nullable=False, default="testnet"),
            Column("open_price", Float),
            Column("closed_at", DateTime),
            Column("fail_reason", String),
            Column("strategy", String, nullable=False, default="simple", server_default="simple"),
            Column("params", Text),
            Column("calibration_id", Integer),
            Column("dust_qty", String, nullable=False, default="0", server_default="0"),
        )
        self.grid_calibrations = Table(
            "grid_calibrations", self.metadata,
            Column("id", Integer, primary_key=True, autoincrement=True),
            Column("created_at", DateTime(timezone=True), nullable=False),
            Column("symbol", String, nullable=False), Column("data_start", String, nullable=False),
            Column("data_end", String, nullable=False), Column("method", Text, nullable=False),
            Column("seed", Integer, nullable=False), Column("params", Text, nullable=False),
            Column("metrics", Text, nullable=False), Column("verdict", String, nullable=False),
            Column("data_sha256", String, nullable=False), Column("notes", Text),
        )
        self.grid_levels = Table(
            "grid_levels", self.metadata,
            Column("grid_id", Integer, ForeignKey("grids.id"), primary_key=True),
            Column("level_idx", Integer, primary_key=True),
            Column("price", Float, nullable=False),
            Column("capital", Float, nullable=False),
            Column("capital_base", Float, nullable=True),
            Column("capital_compound", Float, nullable=False, default=0.0, server_default="0"),
            Column("capital_loan", Float, nullable=False, default=0.0, server_default="0"),
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
        self.pause_shadow_observations = Table(
            "pause_shadow_observations", self.metadata,
            Column("id", Integer, primary_key=True, autoincrement=True),
            Column("run_id", Integer, nullable=True), Column("grid_id", Integer, nullable=True),
            Column("ts", DateTime(timezone=True), nullable=True), Column("symbol", String, nullable=True),
            Column("break_prob_4h", Float, nullable=True), Column("would_pause_4h", Integer, nullable=True),
            Column("break_prob_24h", Float, nullable=True), Column("would_pause_24h", Integer, nullable=True),
            Column("paused_actual", Integer, nullable=True), Column("price", Float, nullable=True),
            Column("range_low", Float, nullable=True), Column("range_high", Float, nullable=True),
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
        self.app_flag_overrides = Table(
            "app_flag_overrides", self.metadata,
            Column("key", String, primary_key=True),
            Column("value", Text, nullable=False),
            Column("updated_at", DateTime(timezone=True), nullable=False),
            Column("source", String, nullable=False),
        )
        self.grid_loans = Table(
            "grid_loans", self.metadata,
            Column("id", Integer, primary_key=True, autoincrement=True),
            Column("grid_id", Integer, ForeignKey("grids.id"), nullable=False),
            Column("lender_idx", Integer), Column("borrower_idx", Integer, nullable=False),
            Column("amount", Float, nullable=False), Column("reserve_part", Float, nullable=False),
            Column("status", String, nullable=False),
            Column("created_at", DateTime(timezone=True), nullable=False),
            Column("updated_at", DateTime(timezone=True), nullable=False),
            Column("closed_at", DateTime(timezone=True)), Column("close_reason", String),
            Column("lender_cycles_at_open", Integer, nullable=False),
            Column("borrower_cycles_at_open", Integer, nullable=False),
            Column("plan", Text, nullable=False), Column("details", Text, nullable=False),
        )
        self.grid_dust_ledger = Table(
            "grid_dust_ledger", self.metadata,
            Column("grid_id", Integer, ForeignKey("grids.id"), primary_key=True),
            Column("source_key", String, primary_key=True),
            Column("qty", Float, nullable=False),
        )
        Index("ix_grid_loans_grid_status", self.grid_loans.c.grid_id, self.grid_loans.c.status)
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
            Column("sigma_monitor_h", Float), Column("monitor_horizon_h", Float),
            Column("source", String),
        )
        self.metadata.create_all(self.engine)
        self._migrate_direction_verification_columns()
        self._migrate_widen_columns()
        self._migrate_vol_forecast_columns()
        self._seed_widen_defaults()
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

    def _migrate_widen_columns(self) -> None:
        """Add missing declared widen suggestion columns without changing rows."""
        def sql_type(column):
            if isinstance(column.type, Float):
                return "FLOAT"
            if isinstance(column.type, Integer):
                return "INTEGER"
            if isinstance(column.type, (String, Text)):
                return "TEXT"
            if isinstance(column.type, DateTime):
                return "DATETIME"
            return column.type.compile(dialect=self.engine.dialect)

        with self.engine.begin() as conn:
            if self.engine.dialect.name == "sqlite":
                present = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info('vol_widen_suggestions')")}
                for column in self.vol_widen_suggestions.columns:
                    if column.name not in present:
                        conn.exec_driver_sql(
                            f"ALTER TABLE vol_widen_suggestions ADD COLUMN {column.name} {sql_type(column)}"
                        )
            elif self.engine.dialect.name == "postgresql":
                for column in self.vol_widen_suggestions.columns:
                    conn.exec_driver_sql(
                        f"ALTER TABLE vol_widen_suggestions ADD COLUMN IF NOT EXISTS {column.name} {sql_type(column)}"
                    )

    def _migrate_direction_verification_columns(self) -> None:
        """Add nullable direction-verification provenance without rewriting rows."""
        additions = {
            "predictions": {"verification_status": "TEXT", "verify_delay_h": "FLOAT"},
            "outcomes": {"verify_delay_h": "FLOAT"},
        }
        with self.engine.begin() as conn:
            for table, columns in additions.items():
                if self.engine.dialect.name == "sqlite":
                    present = {row[1] for row in conn.exec_driver_sql(f"PRAGMA table_info('{table}')")}
                    for name, sql_type in columns.items():
                        if name not in present:
                            conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {name} {sql_type}")
                elif self.engine.dialect.name == "postgresql":
                    for name, sql_type in columns.items():
                        conn.exec_driver_sql(
                            f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {name} {sql_type}"
                        )

    def _seed_widen_defaults(self) -> None:
        """Persist configured per-horizon defaults once, without altering other tables."""
        with self.engine.begin() as conn:
            for horizon in VOL_HORIZONS:
                exists = conn.execute(select(self.vol_widen_suggestions.c.id).where(
                    self.vol_widen_suggestions.c.symbol == VOL_SYMBOL,
                    self.vol_widen_suggestions.c.horizon_h == int(horizon),
                    self.vol_widen_suggestions.c.kind == "settings",
                ).limit(1)).first()
                if exists:
                    continue
                created = datetime.now(timezone.utc)
                if self.engine.dialect.name == "sqlite":
                    created = created.replace(tzinfo=None)
                conn.execute(self.vol_widen_suggestions.insert().values(
                    symbol=VOL_SYMBOL, horizon_h=int(horizon), computed_at=created,
                    kind="settings", k_active=VOL_WIDEN_K_ACTIVE,
                    disagreement_pct_active=VOL_WIDEN_DISAGREEMENT_PCT,
                    status="settings", audit_actor="config:default",
                ))

    def _migrate_vol_forecast_columns(self) -> None:
        """Add nullable forecast provenance without rewriting existing rows."""
        if self.engine.dialect.name == "sqlite":
            with self.engine.begin() as conn:
                present = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info('vol_forecasts')")}
                if "artifact_version" not in present:
                    conn.exec_driver_sql("ALTER TABLE vol_forecasts ADD COLUMN artifact_version TEXT")

    def _migrate_grid_columns(self) -> None:
        """Idempotently add the approved 15B columns to existing SQLite databases."""
        if self.engine.dialect.name != "sqlite":
            return
        additions = {
            "grids": {"strategy": "VARCHAR NOT NULL DEFAULT 'simple'", "params": "TEXT", "calibration_id": "INTEGER",
                      "dust_qty": "VARCHAR NOT NULL DEFAULT '0'",
                      "reserve": "FLOAT NOT NULL DEFAULT 0"},
            "grid_levels": {
                "entry_price": "FLOAT", "bought_at": "DATETIME", "stop_loss_pct": "FLOAT",
                "buy_client_order_id": "VARCHAR",
                "capital_base": "FLOAT", "capital_compound": "FLOAT NOT NULL DEFAULT 0",
                "capital_loan": "FLOAT NOT NULL DEFAULT 0",
            },
            "grid_snapshots": {
                "break_prob": "FLOAT", "sigma_24h": "FLOAT",
                "trapped_capital_pct": "FLOAT", "free_cells": "INTEGER",
                "sigma_monitor_h": "FLOAT", "monitor_horizon_h": "FLOAT", "source": "TEXT",
            },
        }
        with self.engine.begin() as conn:
            for table, columns in additions.items():
                existing = {row["name"] for row in conn.execute(text(f"PRAGMA table_info({table})")).mappings()}
                for name, sql_type in columns.items():
                    if name not in existing:
                        conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {sql_type}"))
            conn.execute(text("UPDATE grids SET strategy='simple' WHERE strategy IS NULL OR strategy=''"))
            conn.execute(text("UPDATE grids SET dust_qty='0' WHERE dust_qty IS NULL"))
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
                  "market_condition": self._json(prediction_dict.get("market_condition")),
                  "verification_status": "pending", "verify_delay_h": None}
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

    def save_outcome(self, prediction_id: str, price_at_verification: float,
                     verify_delay_h: float | None = None) -> dict:
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
                       "why_wrong": self._json({"signal": p["signal"], "actual_direction": actual}) if correct is not None and not correct else None,
                       "verify_delay_h": None if verify_delay_h is None else float(verify_delay_h)}
            conn.execute(self.outcomes.insert().values(**outcome))
            conn.execute(self.predictions.update().where(
                self.predictions.c.prediction_id == prediction_id
            ).values(verification_status="verified", verify_delay_h=verify_delay_h))
        return outcome

    def mark_prediction_unverifiable_late(self, prediction_id: str, verify_delay_h: float) -> None:
        with self.engine.begin() as conn:
            conn.execute(self.predictions.update().where(
                self.predictions.c.prediction_id == prediction_id
            ).values(verification_status="unverifiable_late", verify_delay_h=float(verify_delay_h)))

    def count_unverifiable_late(self, symbol=None, interval=None, model_name=None) -> int:
        statement = select(func.count(self.predictions.c.id)).where(
            self.predictions.c.verification_status == "unverifiable_late"
        )
        if symbol is not None:
            statement = statement.where(self.predictions.c.symbol == symbol)
        if interval is not None:
            statement = statement.where(self.predictions.c.interval == interval)
        if model_name is not None:
            statement = statement.where(self.predictions.c.model_name == model_name)
        with self.engine.connect() as conn:
            return int(conn.execute(statement).scalar_one() or 0)

    def get_pending_verifications(self) -> list[dict]:
        now = datetime.now(timezone.utc)
        stmt = select(self.predictions).where(
            self.predictions.c.verify_at <= now,
            or_(self.predictions.c.verification_status.is_(None),
                self.predictions.c.verification_status != "unverifiable_late"),
            ~self.predictions.c.prediction_id.in_(select(self.outcomes.c.prediction_id)),
        )
        with self.engine.connect() as conn:
            return [dict(row) for row in conn.execute(stmt).mappings().all()]

    def get_accuracy_by_model(self, model_name: str, last_n_days: int = 30) -> dict:
        cutoff = datetime.now(timezone.utc) - timedelta(days=last_n_days)
        valid = or_(self.predictions.c.verification_status.is_(None),
                    self.predictions.c.verification_status != "unverifiable_late")
        stmt = select(
            func.count(func.distinct(case((valid, self.predictions.c.id)))).label("total"),
            func.count(func.distinct(case((valid, self.outcomes.c.id)))).label("verified"),
            func.count(case((valid, self.outcomes.c.was_correct))).label("evaluated"),
            func.coalesce(func.sum(self.outcomes.c.was_correct), 0).label("correct"),
            func.count(func.distinct(case((self.predictions.c.verification_status == "unverifiable_late",
                                           self.predictions.c.id)))).label("unverifiable_late"),
        ).select_from(
            self.predictions.outerjoin(self.outcomes, self.predictions.c.prediction_id == self.outcomes.c.prediction_id)
        ).where(self.predictions.c.model_name == model_name, self.predictions.c.predicted_at >= cutoff)
        with self.engine.connect() as conn:
            row = conn.execute(stmt).mappings().one()
        total, verified, evaluated, correct = int(row["total"]), int(row["verified"]), int(row["evaluated"]), int(row["correct"])
        return {"model_name": model_name, "total_predictions": total, "verified_predictions": verified,
                "unverifiable_late_count": int(row["unverifiable_late"]),
                "evaluated_predictions": evaluated, "correct_predictions": correct, "accuracy": correct / evaluated if evaluated else None,
                "last_n_days": last_n_days}

    def get_battle_stats(self, model_name: str) -> dict:
        """Return all-time battle counts, including pending predictions."""
        valid = or_(self.predictions.c.verification_status.is_(None),
                    self.predictions.c.verification_status != "unverifiable_late")
        stmt = select(
            func.count(func.distinct(case((valid, self.predictions.c.id)))).label("total"),
            func.count(func.distinct(case((valid, self.outcomes.c.id)))).label("verified"),
            func.count(case((valid, self.outcomes.c.was_correct))).label("evaluated"),
            func.coalesce(func.sum(self.outcomes.c.was_correct), 0).label("correct"),
            func.count(func.distinct(case((self.predictions.c.verification_status == "unverifiable_late",
                                           self.predictions.c.id)))).label("unverifiable_late"),
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
            "n_unverifiable_late": int(row["unverifiable_late"]),
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
            predictions = conn.execute(select(self.predictions.c.prediction_id, self.predictions.c.features_snapshot, self.predictions.c.market_condition).where(
                self.predictions.c.model_name == model_name,
                or_(self.predictions.c.verification_status.is_(None),
                    self.predictions.c.verification_status != "unverifiable_late"),
            )).mappings().all()
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
                item["is_unverifiable_late"] = item.get("verification_status") == "unverifiable_late"
                item["was_correct"] = item.get("was_correct") if item["is_verified"] else None
                rows.append(item)
            return rows

    def get_prediction_signal_summary(self, symbol: str, interval: str, model_name: str) -> dict:
        p, o = self.predictions.c, self.outcomes.c
        joined = self.predictions.outerjoin(
            self.outcomes, p.prediction_id == o.prediction_id,
        )
        valid = or_(p.verification_status.is_(None), p.verification_status != "unverifiable_late")
        bullish = p.signal == "ALCISTA"
        columns = [
            func.count(func.distinct(case((valid, p.id)))).label("total_predictions"),
            func.count(func.distinct(case((valid, o.id)))).label("verified_count"),
            func.coalesce(func.sum(case((valid & bullish, 1), else_=0)), 0).label("bullish_count"),
            func.coalesce(func.sum(case((valid & bullish & (o.was_correct == 1), 1), else_=0)), 0).label("bullish_correct"),
            func.coalesce(func.sum(case((valid & bullish & (o.was_correct == 0), 1), else_=0)), 0).label("bullish_failed"),
            func.coalesce(func.sum(case((valid & (p.signal == "NEUTRAL"), 1), else_=0)), 0).label("neutral_count"),
            func.coalesce(func.sum(case((valid & (o.actual_direction == "UP"), 1), else_=0)), 0).label("base_up"),
            func.count(func.distinct(case((p.verification_status == "unverifiable_late", p.id)))).label("n_unverifiable_late"),
        ]
        statement = select(*columns).select_from(joined).where(
            p.symbol == symbol, p.interval == interval, p.model_name == model_name,
        )
        with self.engine.connect() as conn:
            row = conn.execute(statement).mappings().one()
        total = int(row["total_predictions"] or 0)
        verified = int(row["verified_count"] or 0)
        bullish_count = int(row["bullish_count"] or 0)
        correct, failed = int(row["bullish_correct"] or 0), int(row["bullish_failed"] or 0)
        return {
            "total_predictions": total, "verified_count": verified,
            "pending_count": total - verified, "bullish_count": bullish_count,
            "n_unverifiable_late": int(row["n_unverifiable_late"] or 0),
            "bullish_correct": correct, "bullish_failed": failed,
            "bullish_pending": bullish_count - correct - failed,
            "neutral_count": int(row["neutral_count"] or 0),
            "base_rate": int(row["base_up"] or 0) / verified if verified else None,
        }

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
        unverifiable_late = self.count_unverifiable_late(symbol, interval, model_name)
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
            "n_unverifiable_late": unverifiable_late,
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
            "artifact_version": row.get("artifact_version"),
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

    def get_champion_vol_forecast_before(
        self, symbol: str, horizon_h: int, as_of: datetime
    ) -> dict | None:
        """Return the latest champion forecast timestamped no later than a prediction."""
        statement = select(
            self.vol_forecasts.c.forecast_at,
            self.vol_forecasts.c.model_name,
            self.vol_forecasts.c.pred_logvol_cal,
        ).where(
            self.vol_forecasts.c.symbol == symbol,
            self.vol_forecasts.c.horizon_h == int(horizon_h),
            self.vol_forecasts.c.is_champion == 1,
            self.vol_forecasts.c.forecast_at <= self._utc(as_of),
        ).order_by(self.vol_forecasts.c.forecast_at.desc()).limit(1)
        with self.engine.connect() as conn:
            row = conn.execute(statement).mappings().first()
        return dict(row) if row else None

    def get_volatility_coverage(self, symbol: str, interval: str, model_name: str = "model_a") -> dict:
        """Measure verified 4h returns inside the forecast-time 1σ/2σ bands."""
        rows = self.get_predictions_with_outcomes(
            symbol=symbol, interval=interval, model_name=model_name, limit=5000
        )
        n = inside_1sigma = inside_2sigma = 0
        for row in rows:
            if row.get("verified_at") is None or row.get("price_at_verification") is None:
                continue
            try:
                market = json.loads(row.get("market_condition") or "{}")
                forecast = market.get("volatility_4h") or {}
                bands = (forecast["range_1sigma"], forecast["range_2sigma"])
                price = float(row["price_at_verification"])
                if not all(len(band) == 2 for band in bands):
                    continue
                n += 1
                inside_1sigma += int(float(bands[0][0]) <= price <= float(bands[0][1]))
                inside_2sigma += int(float(bands[1][0]) <= price <= float(bands[1][1]))
            except (TypeError, ValueError, KeyError, json.JSONDecodeError):
                continue
        return {
            "symbol": symbol,
            "interval": interval,
            "model_name": model_name,
            "n": n,
            "inside_1sigma": inside_1sigma,
            "inside_2sigma": inside_2sigma,
            "coverage_1sigma": inside_1sigma / n if n else None,
            "coverage_2sigma": inside_2sigma / n if n else None,
            "sample_sufficient": n >= 30,
            "n_unverifiable_late": self.count_unverifiable_late(symbol, interval, model_name),
        }

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

    def get_vol_forecast_version_counts(self, symbol: str, horizon_h: int) -> dict[str, int]:
        statement = select(
            self.vol_forecasts.c.artifact_version, func.count().label("n")
        ).where(
            self.vol_forecasts.c.symbol == symbol,
            self.vol_forecasts.c.horizon_h == int(horizon_h),
        ).group_by(self.vol_forecasts.c.artifact_version)
        with self.engine.connect() as conn:
            return {str(row["artifact_version"] or "legacy/null"): int(row["n"])
                    for row in conn.execute(statement).mappings()}

    def get_vol_model_stats_rows(self, symbol: str, horizon_h: int) -> list[dict]:
        """Return a bounded rolling window plus all rows still awaiting maturity."""
        from models.volatility.model_stats import N_MIN, ROLLING_VERIFICATIONS
        horizon_h = max(1, int(horizon_h))
        # Keep enough raw rows for N_MIN effective outcomes after the H-hour overlap adjustment.
        rolling_window = max(ROLLING_VERIFICATIONS, N_MIN * horizon_h)
        cutoff = datetime.now(timezone.utc) - timedelta(hours=horizon_h)
        if self.engine.dialect.name == "sqlite":
            cutoff = cutoff.replace(tzinfo=None)
        ranked = select(
            self.vol_forecasts.c.id,
            func.row_number().over(
                partition_by=self.vol_forecasts.c.model_name,
                order_by=self.vol_forecasts.c.forecast_at.desc(),
            ).label("rn"),
        ).where(
            self.vol_forecasts.c.symbol == symbol,
            self.vol_forecasts.c.horizon_h == int(horizon_h),
        ).subquery()
        statement = select(
            self.vol_forecasts.c.id, self.vol_forecasts.c.symbol,
            self.vol_forecasts.c.horizon_h, self.vol_forecasts.c.model_name,
            self.vol_forecasts.c.forecast_at, self.vol_forecasts.c.made_at,
            self.vol_forecasts.c.pred_logvol_raw, self.vol_forecasts.c.pred_logvol_cal,
            self.vol_forecasts.c.var_factor, self.vol_forecasts.c.is_champion,
            self.vol_forecasts.c.realized_logvol, self.vol_forecasts.c.verified_at,
        ).join(ranked, ranked.c.id == self.vol_forecasts.c.id).where(
            self.vol_forecasts.c.symbol == symbol,
            self.vol_forecasts.c.horizon_h == int(horizon_h),
            (ranked.c.rn <= rolling_window + N_MIN)
            | self.vol_forecasts.c.realized_logvol.is_(None)
            | self.vol_forecasts.c.verified_at.is_(None)
            | (self.vol_forecasts.c.forecast_at > cutoff),
        ).order_by(self.vol_forecasts.c.forecast_at, self.vol_forecasts.c.model_name)
        with self.engine.connect() as conn:
            return [dict(row) for row in conn.execute(statement).mappings().all()]

    def get_vol_model_stats_aggregates(self, symbol: str, horizon_h: int, sigma_refs: dict,
                                       now: datetime) -> dict[str, dict]:
        """SQL aggregates preserve all-history metrics without transferring full rows."""
        f = self.vol_forecasts.c
        mature_cutoff = now - timedelta(hours=int(horizon_h))
        if self.engine.dialect.name == "sqlite":
            mature_cutoff = mature_cutoff.replace(tzinfo=None)
        err = f.pred_logvol_cal - f.realized_logvol
        pred_var = func.exp(2.0 * f.pred_logvol_cal)
        actual_var = func.exp(2.0 * f.realized_logvol)
        ratio_each = actual_var / pred_var
        columns = [f.model_name.label("model_name"), func.count().label("n"),
                   func.avg(err).label("bias_mean"), func.avg(err * err).label("mse"),
                   func.avg(func.abs(err)).label("mae"), func.avg(pred_var).label("pred_var"),
                   func.avg(actual_var).label("actual_var"),
                   func.avg(ratio_each - func.ln(ratio_each) - 1.0).label("qlike"),
                   func.sum(case((err > 0, 1), else_=0)).label("over_n"),
                   func.sum(case((err < 0, 1), else_=0)).label("under_n")]
        for model, sigma in sigma_refs.items():
            if sigma is not None:
                columns.extend([
                    func.sum(case((func.abs(err) <= float(sigma), 1), else_=0)).label(f"{model}_hit1"),
                    func.sum(case((func.abs(err) <= 2 * float(sigma), 1), else_=0)).label(f"{model}_hit2"),
                ])
        statement = select(*columns).where(
            f.symbol == symbol, f.horizon_h == int(horizon_h),
            f.realized_logvol.is_not(None), f.verified_at.is_not(None),
            f.forecast_at <= mature_cutoff, f.verified_at <= now,
        ).group_by(f.model_name)
        with self.engine.connect() as conn:
            result = {}
            for row in conn.execute(statement).mappings():
                model = row["model_name"]
                n = int(row["n"])
                sigma = sigma_refs.get(model)
                hit1 = int(row.get(f"{model}_hit1") or 0) if sigma is not None else 0
                hit2 = int(row.get(f"{model}_hit2") or 0) if sigma is not None else 0
                ratio = (float(row["actual_var"]) / float(row["pred_var"])) if row["pred_var"] else None
                result[model] = {
                    "n": n, "mse": float(row["mse"]), "mae": float(row["mae"]),
                    "qlike": float(row["qlike"]), "var_ratio": ratio,
                    "bias_mean": float(row["bias_mean"]),
                    "over_pct": int(row["over_n"] or 0) * 100.0 / n,
                    "under_pct": int(row["under_n"] or 0) * 100.0 / n,
                    "success_1sigma": hit1, "success_2sigma": hit2,
                    "failures": n - hit2 if sigma is not None else 0,
                    "coverage_1sigma": hit1 / n if sigma is not None else None,
                    "coverage_2sigma": hit2 / n if sigma is not None else None,
                    "sigma_ref": sigma,
                }
        return result

    def get_vol_model_stats_dispersion_rows(self, symbol: str, horizon_h: int) -> list[dict]:
        """Narrow all-history projection used only for exact dispersion buckets."""
        f = self.vol_forecasts.c
        statement = select(f.forecast_at, f.horizon_h, f.model_name,
                           f.pred_logvol_cal, f.realized_logvol, f.verified_at,
                           f.pred_logvol_raw).where(
            f.symbol == symbol, f.horizon_h == int(horizon_h),
            f.pred_logvol_cal.is_not(None),
        ).order_by(f.forecast_at, f.model_name)
        with self.engine.connect() as conn:
            return [dict(row) for row in conn.execute(statement).mappings().all()]

    def get_latest_vol_model_stats_forecast_at(self, symbol: str, horizon_h: int):
        f = self.vol_forecasts.c
        statement = select(func.max(f.forecast_at)).where(
            f.symbol == symbol, f.horizon_h == int(horizon_h),
        )
        with self.engine.connect() as conn:
            return conn.execute(statement).scalar_one_or_none()

    def get_widen_factor_rows(self, symbol: str, horizon_h: int, as_of: datetime) -> list[dict]:
        """Read the narrow projection needed for exact causal widening quantiles."""
        f = self.vol_forecasts.c
        cutoff = self._utc(as_of)
        if self.engine.dialect.name == "sqlite":
            cutoff = cutoff.replace(tzinfo=None)
        statement = select(f.forecast_at, f.horizon_h, f.model_name, f.pred_logvol_raw,
                           f.pred_logvol_cal,
                           f.realized_logvol, f.verified_at, f.is_champion).where(
            f.symbol == symbol, f.horizon_h == int(horizon_h), f.forecast_at <= cutoff,
        ).order_by(f.forecast_at, f.model_name)
        with self.engine.connect() as conn:
            return [dict(row) for row in conn.execute(statement).mappings().all()]

    def count_new_widen_verifications(self, symbol: str, horizon_h: int, champion: str,
                                      after: datetime | None, as_of: datetime) -> int:
        f = self.vol_forecasts.c
        mature_before = self._utc(as_of) - timedelta(hours=int(horizon_h))
        verified_before = self._utc(as_of)
        after_value = self._utc(after) if after is not None else None
        if self.engine.dialect.name == "sqlite":
            mature_before, verified_before = mature_before.replace(tzinfo=None), verified_before.replace(tzinfo=None)
            if after_value is not None:
                after_value = after_value.replace(tzinfo=None)
        conditions = [f.symbol == symbol, f.horizon_h == int(horizon_h),
                      f.model_name == champion, f.realized_logvol.is_not(None),
                      f.verified_at.is_not(None), f.forecast_at <= mature_before,
                      f.verified_at <= verified_before]
        if after_value is not None:
            conditions.append(f.verified_at > after_value)
        with self.engine.connect() as conn:
            return int(conn.execute(select(func.count()).select_from(self.vol_forecasts)
                                    .where(*conditions)).scalar_one())

    def get_widen_factor_history(self, symbol: str, horizon_h: int, limit: int = 100):
        statement = select(self.vol_widen_suggestions).where(
            self.vol_widen_suggestions.c.symbol == symbol,
            self.vol_widen_suggestions.c.horizon_h == int(horizon_h),
            self.vol_widen_suggestions.c.kind == "suggestion",
        ).order_by(self.vol_widen_suggestions.c.computed_at.desc()).limit(int(limit))
        with self.engine.connect() as conn:
            rows = [dict(row) for row in conn.execute(statement).mappings().all()]
        return list(reversed(rows))

    def get_latest_widen_factor(self, symbol: str, horizon_h: int):
        statement = select(self.vol_widen_suggestions).where(
            self.vol_widen_suggestions.c.symbol == symbol,
            self.vol_widen_suggestions.c.horizon_h == int(horizon_h),
            self.vol_widen_suggestions.c.kind == "suggestion",
        ).order_by(self.vol_widen_suggestions.c.computed_at.desc()).limit(1)
        with self.engine.connect() as conn:
            row = conn.execute(statement).mappings().first()
        return dict(row) if row else None

    def get_widen_factor_audit_history(self, symbol: str, horizon_h: int, limit: int = 100):
        statement = select(self.vol_widen_suggestions).where(
            self.vol_widen_suggestions.c.symbol == symbol,
            self.vol_widen_suggestions.c.horizon_h == int(horizon_h),
            self.vol_widen_suggestions.c.kind.in_(["apply", "auto_apply"]),
        ).order_by(self.vol_widen_suggestions.c.computed_at.desc()).limit(int(limit))
        with self.engine.connect() as conn:
            rows = [dict(row) for row in conn.execute(statement).mappings().all()]
        return list(reversed(rows))

    def get_widen_active_values(self, symbol: str, horizon_h: int, default_k: float,
                                default_disagreement: float):
        statement = select(self.vol_widen_suggestions).where(
            self.vol_widen_suggestions.c.symbol == symbol,
            self.vol_widen_suggestions.c.horizon_h == int(horizon_h),
            self.vol_widen_suggestions.c.kind.in_(["apply", "auto_apply"]),
        ).order_by(self.vol_widen_suggestions.c.computed_at.desc()).limit(1)
        with self.engine.connect() as conn:
            row = conn.execute(statement).mappings().first()
        if not row:
            statement = select(self.vol_widen_suggestions).where(
                self.vol_widen_suggestions.c.symbol == symbol,
                self.vol_widen_suggestions.c.horizon_h == int(horizon_h),
                self.vol_widen_suggestions.c.kind == "settings",
            ).order_by(self.vol_widen_suggestions.c.computed_at.desc()).limit(1)
            with self.engine.connect() as conn:
                row = conn.execute(statement).mappings().first()
            if not row:
                return {"k_active": float(default_k),
                        "disagreement_pct_active": float(default_disagreement)}
        return {"k_active": float(row["k_active"] if row["k_active"] is not None else default_k),
                "disagreement_pct_active": float(row["disagreement_pct_active"]
                    if row["disagreement_pct_active"] is not None else default_disagreement)}

    def save_widen_factor_record(self, values: dict) -> int:
        record = dict(values)
        record["computed_at"] = self._utc(record["computed_at"])
        if self.engine.dialect.name == "sqlite":
            record["computed_at"] = record["computed_at"].replace(tzinfo=None)
        with self.engine.begin() as conn:
            result = conn.execute(self.vol_widen_suggestions.insert().values(**record))
            return int(result.inserted_primary_key[0])

    def _heavy_work_lock(self, conn) -> None:
        if self.engine.dialect.name == "postgresql":
            conn.execute(text("SELECT pg_advisory_xact_lock(2026100701)"))

    def has_active_training_job(self) -> bool:
        query = select(self.training_jobs.c.id).where(
            self.training_jobs.c.status.in_(("pendiente", "descargando", "entrenando", "running"))
        ).limit(1)
        with self.engine.connect() as conn:
            return conn.execute(query).first() is not None

    def try_claim_coin_onboarding(self, symbol: str) -> bool:
        now = datetime.now(timezone.utc)
        if self.engine.dialect.name == "sqlite":
            now = now.replace(tzinfo=None)
        with self.engine.connect() as conn:
            if self.engine.dialect.name == "sqlite":
                conn.exec_driver_sql("BEGIN IMMEDIATE")
            else:
                self._heavy_work_lock(conn)
            active = conn.execute(select(self.training_jobs.c.id).where(
                self.training_jobs.c.status.in_(("pendiente", "descargando", "entrenando", "running"))
            ).limit(1)).first()
            other_onboarding = conn.execute(select(self.coin_readiness.c.symbol).where(
                self.coin_readiness.c.symbol != symbol,
                self.coin_readiness.c.state.in_(("descargando", "entrenando", "consensuando")),
            ).limit(1)).first()
            if active or other_onboarding:
                conn.rollback()
                return False
            existing = conn.execute(select(self.coin_readiness.c.symbol).where(
                self.coin_readiness.c.symbol == symbol
            )).first()
            values = {"state": "descargando", "stage_detail": "descargando",
                      "progress_pct": 1.0, "error": None, "started_at": now,
                      "updated_at": now, "ready_at": None, "pid": None}
            if existing:
                conn.execute(self.coin_readiness.update().where(
                    self.coin_readiness.c.symbol == symbol).values(**values))
            else:
                conn.execute(self.coin_readiness.insert().values(symbol=symbol, **values))
            conn.commit()
            return True

    def get_readiness(self, symbol: str) -> dict | None:
        with self.engine.connect() as conn:
            row = conn.execute(select(self.coin_readiness).where(
                self.coin_readiness.c.symbol == symbol)).mappings().first()
        if row is None:
            return None
        result = dict(row)
        for key in ("started_at", "updated_at", "ready_at"):
            if result.get(key) is not None:
                value = result[key]
                if value.tzinfo is None:
                    value = value.replace(tzinfo=timezone.utc)
                result[key] = value.isoformat()
        return result

    def set_readiness(self, symbol: str, state: str, **fields) -> dict:
        allowed = {"stage_detail", "progress_pct", "history_days", "error",
                   "started_at", "ready_at", "pid"}
        unknown = set(fields) - allowed
        if unknown:
            raise ValueError(f"Campos de readiness no permitidos: {sorted(unknown)}")
        now = datetime.now(timezone.utc)
        values = {"state": state, "updated_at": now, **fields}
        if self.engine.dialect.name == "sqlite":
            values = {key: value.replace(tzinfo=None) if isinstance(value, datetime) else value
                      for key, value in values.items()}
        with self.engine.begin() as conn:
            existing = conn.execute(select(self.coin_readiness.c.symbol).where(
                self.coin_readiness.c.symbol == symbol)).first()
            if existing:
                conn.execute(self.coin_readiness.update().where(
                    self.coin_readiness.c.symbol == symbol).values(**values))
            else:
                conn.execute(self.coin_readiness.insert().values(symbol=symbol, **values))
        return self.get_readiness(symbol)

    def list_readiness(self) -> list[dict]:
        with self.engine.connect() as conn:
            rows = conn.execute(select(self.coin_readiness).order_by(
                self.coin_readiness.c.symbol)).mappings().all()
        result = []
        for row in rows:
            item = dict(row)
            for key in ("started_at", "updated_at", "ready_at"):
                if item.get(key) is not None:
                    value = item[key]
                    if value.tzinfo is None:
                        value = value.replace(tzinfo=timezone.utc)
                    item[key] = value.isoformat()
            result.append(item)
        return result

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

    def get_all_coins(self) -> list[dict]:
        statement = select(self.coins_registry).order_by(
            self.coins_registry.c.active.desc(), self.coins_registry.c.added_at
        )
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
            "reserve": float(grid.get("reserve", 0.0)),
            "status": grid.get("status", "OPENING"),
            "created_at": grid.get("created_at", now),
            "environment": grid.get("environment", "testnet"),
            "open_price": grid.get("open_price"),
            "closed_at": grid.get("closed_at"),
            "fail_reason": grid.get("fail_reason"),
            "strategy": str(grid.get("strategy", "simple")),
            "params": self._json(grid.get("params")),
            "calibration_id": grid.get("calibration_id"),
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
                    "capital_base": (
                        float(level["capital_base"]) if level.get("capital_base") is not None
                        else float(level["capital"]) - float(level.get("capital_compound", 0.0))
                    ),
                    "capital_compound": float(level.get("capital_compound", 0.0)),
                    "capital_loan": float(level.get("capital_loan", 0.0)),
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

    def save_grid_calibration(self, record: dict[str, Any]) -> int:
        values = dict(record)
        values.setdefault("created_at", self._utc_now())
        for key in ("params", "metrics"):
            values[key] = self._json(values[key])
        with self.engine.begin() as conn:
            result = conn.execute(self.grid_calibrations.insert().values(**values))
            return int(result.inserted_primary_key[0])

    def get_grid_calibration(self, calibration_id: int) -> dict | None:
        with self.engine.connect() as conn:
            row = conn.execute(select(self.grid_calibrations).where(
                self.grid_calibrations.c.id == calibration_id)).mappings().first()
        return self._decode_calibration(dict(row)) if row else None

    def get_latest_grid_calibration(self) -> dict | None:
        with self.engine.connect() as conn:
            row = conn.execute(select(self.grid_calibrations).order_by(
                self.grid_calibrations.c.id.desc()).limit(1)).mappings().first()
        return self._decode_calibration(dict(row)) if row else None

    @staticmethod
    def _decode_calibration(row):
        for key in ("params", "metrics"):
            try:
                row[key] = json.loads(row[key])
            except (TypeError, json.JSONDecodeError):
                pass
        return row

    def list_grid_calibrations(self):
        with self.engine.connect() as conn:
            rows = conn.execute(select(self.grid_calibrations).order_by(
                self.grid_calibrations.c.id.desc())).mappings().all()
        return [self._decode_calibration(dict(row)) for row in rows]

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
            rows = [dict(row) for row in conn.execute(statement).mappings().all()]
        for row in rows:
            if row.get("capital_base") is None:
                row["capital_base"] = float(row["capital"]) - float(row.get("capital_compound") or 0.0)
        return rows

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

    def list_app_flag_overrides(self) -> list[dict[str, Any]]:
        with self.engine.connect() as conn:
            rows = conn.execute(select(self.app_flag_overrides).order_by(
                self.app_flag_overrides.c.key)).mappings().all()
        result = []
        for row in rows:
            item = dict(row)
            item["value"] = json.loads(item["value"])
            result.append(item)
        return result

    def set_app_flag_override(self, key: str, value: bool, *, previous: bool,
                              origin: str) -> dict[str, Any]:
        now = self._utc_now()
        encoded = self._json(value)
        event_details = {
            "key": str(key), "previous": bool(previous), "new": bool(value),
            "origin": "app", "previous_origin": str(origin), "at": now.isoformat(),
        }
        with self.engine.begin() as conn:
            existing = conn.execute(select(self.app_flag_overrides.c.key).where(
                self.app_flag_overrides.c.key == str(key))).first()
            values = {"value": encoded, "updated_at": now, "source": "app"}
            if existing:
                conn.execute(self.app_flag_overrides.update().where(
                    self.app_flag_overrides.c.key == str(key)).values(**values))
            else:
                conn.execute(self.app_flag_overrides.insert().values(key=str(key), **values))
            conn.execute(self.grid_events.insert().values(
                run_id=None, source="CLI", ts=now, grid_id=None, level_idx=None,
                client_order_id=None, order_id=None, event_type="APP_SETTING_CHANGED",
                reason=str(key), price=None, details=self._json(event_details),
            ))
        return {"key": str(key), "value": bool(value), "updated_at": now, "source": "app"}

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

    def create_grid_loan(self, values: dict[str, Any]) -> dict:
        """Persist the write-ahead record before a loan changes exchange orders."""
        now = datetime.now(timezone.utc)
        if self.engine.dialect.name == "sqlite":
            now = now.replace(tzinfo=None)
        row = {
            **values, "status": "PENDING", "created_at": now, "updated_at": now,
            "plan": self._json(values.get("plan") or {}),
            "details": self._json(values.get("details") or {}),
        }
        with self.engine.begin() as conn:
            loan_id = conn.execute(self.grid_loans.insert().values(**row)).inserted_primary_key[0]
        return self.get_grid_loan(int(loan_id))

    def get_grid_loan(self, loan_id: int) -> dict | None:
        with self.engine.connect() as conn:
            row = conn.execute(select(self.grid_loans).where(
                self.grid_loans.c.id == int(loan_id)
            )).mappings().first()
        return self._decode_json_fields(dict(row), ("plan", "details")) if row else None

    def list_grid_loans(self, grid_id: int, statuses: set[str] | None = None) -> list[dict]:
        statement = select(self.grid_loans).where(self.grid_loans.c.grid_id == int(grid_id))
        if statuses:
            statement = statement.where(self.grid_loans.c.status.in_(sorted(
                {str(value).upper() for value in statuses}
            )))
        statement = statement.order_by(self.grid_loans.c.id)
        with self.engine.connect() as conn:
            rows = conn.execute(statement).mappings().all()
        return [self._decode_json_fields(dict(row), ("plan", "details")) for row in rows]

    def update_grid_loan(self, loan_id: int, **fields: Any) -> dict | None:
        allowed = {column.name for column in self.grid_loans.columns} - {"id", "created_at"}
        if not set(fields) <= allowed:
            raise ValueError("invalid grid loan update fields")
        now = datetime.now(timezone.utc)
        if self.engine.dialect.name == "sqlite":
            now = now.replace(tzinfo=None)
        values = {**fields, "updated_at": now}
        for name in ("plan", "details"):
            if name in values:
                values[name] = self._json(values[name] or {})
        with self.engine.begin() as conn:
            conn.execute(self.grid_loans.update().where(
                self.grid_loans.c.id == int(loan_id)
            ).values(**values))
        return self.get_grid_loan(int(loan_id))

    def apply_grid_loan_ledger(
        self, loan_id: int, *, level_updates: dict[int, dict[str, Any]],
        grid_fields: dict[str, Any], status: str, event: dict[str, Any],
    ) -> None:
        """Atomically apply loan accounting, ledger status and its audit event."""
        level_allowed = {column.name for column in self.grid_levels.columns} - {"grid_id", "level_idx"}
        grid_allowed = {column.name for column in self.grids.columns} - {"id"}
        if any(not set(fields) <= level_allowed for fields in level_updates.values()):
            raise ValueError("invalid grid level update fields")
        if not set(grid_fields) <= grid_allowed:
            raise ValueError("invalid grid update fields")
        source = str(event.get("source", "CLI")).upper()
        if source not in {"MONITOR", "CLI"}:
            raise ValueError("source must be MONITOR or CLI")
        now = datetime.now(timezone.utc)
        if self.engine.dialect.name == "sqlite":
            now = now.replace(tzinfo=None)
        event_values = {
            "run_id": event.get("run_id"), "source": source, "ts": now,
            "grid_id": int(event["grid_id"]), "level_idx": event.get("level_idx"),
            "client_order_id": event.get("client_order_id"), "order_id": event.get("order_id"),
            "event_type": str(event["event_type"]), "reason": event.get("reason"),
            "price": None if event.get("price") is None else float(event["price"]),
            "details": self._json(event.get("details") or {}),
        }
        loan_values = {"status": str(status).upper(), "updated_at": now}
        if str(status).upper() not in {"PENDING", "OPEN"}:
            loan_values["closed_at"] = now
            loan_values["close_reason"] = event.get("reason")
        with self.engine.begin() as conn:
            loan = conn.execute(select(self.grid_loans).where(
                self.grid_loans.c.id == int(loan_id)
            )).mappings().first()
            if loan is None:
                raise ValueError(f"grid loan {loan_id} does not exist")
            for idx, fields in level_updates.items():
                conn.execute(self.grid_levels.update().where(
                    self.grid_levels.c.grid_id == int(loan["grid_id"]),
                    self.grid_levels.c.level_idx == int(idx),
                ).values(**fields, updated_at=now))
            if grid_fields:
                conn.execute(self.grids.update().where(
                    self.grids.c.id == int(loan["grid_id"])
                ).values(**grid_fields))
            conn.execute(self.grid_loans.update().where(
                self.grid_loans.c.id == int(loan_id)
            ).values(**loan_values))
            conn.execute(self.grid_events.insert().values(**event_values))

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

    def add_pause_shadow_observation(self, values: dict) -> int:
        with self.engine.begin() as conn:
            result = conn.execute(self.pause_shadow_observations.insert().values(**values))
            return int(result.inserted_primary_key[0])

    @staticmethod
    def _shadow_utc(value):
        if isinstance(value, str):
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)

    def get_pause_shadow_summary(self) -> dict:
        with self.engine.connect() as conn:
            observations = [dict(row) for row in conn.execute(select(self.pause_shadow_observations)
                .order_by(self.pause_shadow_observations.c.ts, self.pause_shadow_observations.c.id)).mappings()]
            gaps = [self._shadow_utc(row[0]) for row in conn.execute(select(self.grid_events.c.ts).where(
                self.grid_events.c.event_type == "RUN_GAP")).all()]
        by_grid = {}
        for row in observations:
            if row.get("grid_id") is None or row.get("ts") is None:
                continue
            row["ts"] = self._shadow_utc(row["ts"])
            by_grid.setdefault(int(row["grid_id"]), []).append(row)
        result = {}
        for horizon in (4, 24):
            groups = {"actual_paused": {True: [], False: []}, "shadow_24h": {True: [], False: []}}
            excluded_gap = 0
            for grid_rows in by_grid.values():
                for origin in grid_rows:
                    end = origin["ts"] + timedelta(hours=horizon)
                    if not any(row["ts"] >= end for row in grid_rows):
                        continue
                    if any(origin["ts"] < gap <= end for gap in gaps):
                        excluded_gap += 1
                        continue
                    future = [row for row in grid_rows if origin["ts"] < row["ts"] <= end
                              and row.get("price") is not None]
                    if not future or origin.get("range_low") is None or origin.get("range_high") is None:
                        continue
                    low, high = float(origin["range_low"]), float(origin["range_high"])
                    if low <= 0 or high <= low:
                        continue
                    excursions = [max(0.0, (low - float(row["price"])) / low * 100.0,
                                      (float(row["price"]) - high) / high * 100.0) for row in future]
                    outcome = {"exit": any(value > 0 for value in excursions),
                               "over_1_9_pct": any(value > 1.9 for value in excursions),
                               "max_excursion_pct": max(excursions)}
                    for dimension, key in (("actual_paused", "paused_actual"), ("shadow_24h", "would_pause_24h")):
                        classification = origin.get(key)
                        if classification is not None:
                            groups[dimension][bool(classification)].append(outcome)
            window = {}
            for dimension, classes in groups.items():
                window[dimension] = {}
                for label, values in classes.items():
                    n = len(values)
                    window[dimension]["paused" if label else "not_paused"] = {
                        "n": n, "exits": sum(item["exit"] for item in values),
                        "exits_over_1_9_pct": sum(item["over_1_9_pct"] for item in values),
                        "mean_max_excursion_pct": (sum(item["max_excursion_pct"] for item in values) / n) if n else None,
                        "reason": "muestra insuficiente" if n < 20 else None,
                    }
            result[f"{horizon}h"] = {**window, "excluded_run_gap_windows": excluded_gap,
                "reason": "muestra insuficiente", "criterion":
                "Solo considerar que 4 h es mejor con al menos 20 salidas observadas y si las salidas >1,9 % sin pausa real no superan las de la pausa sombra 24 h; este endpoint no emite veredicto."}
        result["method"] = "Ventanas completas por grid; salida si una observación del monitor cruza el rango inicial; huecos RUN_GAP excluidos. Las observaciones de monitor pueden omitir excursiones intraperiodo."
        return result

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

    def merge_grid_params(self, grid_id: int, updates: dict, *, remove: set[str] = frozenset(),
                          allowed: frozenset[str]) -> dict:
        """Merge live parameters atomically while preserving concurrent counters/plans."""
        updates = dict(updates or {})
        remove = set(remove or ())
        if not (set(updates) | remove) <= set(allowed):
            raise ValueError("grid params contain keys outside the allowed set")
        for attempt in range(5):
            try:
                with self.engine.begin() as conn:
                    statement = select(self.grids.c.params).where(
                        self.grids.c.id == int(grid_id)).with_for_update()
                    row = conn.execute(statement).first()
                    if row is None:
                        raise ValueError(f"grid {grid_id} does not exist")
                    previous = row[0]
                    try:
                        params = json.loads(previous) if previous else {}
                    except (TypeError, json.JSONDecodeError):
                        params = {}
                    if not isinstance(params, dict):
                        raise ValueError("grid params are not a JSON object")
                    params.update({key: value for key, value in updates.items() if key not in remove})
                    for key in remove:
                        params.pop(key, None)
                    result = conn.execute(self.grids.update().where(
                        self.grids.c.id == int(grid_id), self.grids.c.params == previous
                    ).values(params=self._json(params)))
                    if result.rowcount:
                        return params
            except OperationalError:
                if attempt == 4:
                    raise
            time.sleep(0.01 * (attempt + 1))
        raise RuntimeError("concurrent grid parameter updates did not converge")

    def add_grid_dust_once(self, grid_id: int, source_key: str, qty: Any) -> bool:
        """Atomically add a dust delta once per fill/cell residue identity."""
        delta = Decimal(str(qty))
        if delta == 0:
            return False
        with self.engine.begin() as conn:
            exists_row = conn.execute(select(self.grid_dust_ledger.c.source_key).where(
                self.grid_dust_ledger.c.grid_id == int(grid_id),
                self.grid_dust_ledger.c.source_key == str(source_key),
            )).first()
            if exists_row:
                return False
            current = conn.execute(select(self.grids.c.dust_qty).where(
                self.grids.c.id == int(grid_id))).scalar_one_or_none()
            updated = Decimal(str(current or 0)) + delta
            conn.execute(self.grid_dust_ledger.insert().values(
                grid_id=int(grid_id), source_key=str(source_key), qty=delta))
            conn.execute(self.grids.update().where(self.grids.c.id == int(grid_id)).values(
                dust_qty=str(updated)))
        return True

    def record_grid_buy_fill_dust(self, grid_id: int, level_idx: int, source_key: str,
                                  qty: Any, level_fields: dict[str, Any]) -> bool:
        """Commit the fill's cell inventory and its residual dust in one transaction."""
        delta = Decimal(str(qty))
        with self.engine.begin() as conn:
            exists_row = conn.execute(select(self.grid_dust_ledger.c.source_key).where(
                self.grid_dust_ledger.c.grid_id == int(grid_id),
                self.grid_dust_ledger.c.source_key == str(source_key),
            )).first()
            if not exists_row:
                current = conn.execute(select(self.grids.c.dust_qty).where(
                    self.grids.c.id == int(grid_id))).scalar_one_or_none()
                conn.execute(self.grid_dust_ledger.insert().values(
                    grid_id=int(grid_id), source_key=str(source_key), qty=float(delta)))
                conn.execute(self.grids.update().where(self.grids.c.id == int(grid_id)).values(
                    dust_qty=str(Decimal(str(current or 0)) + delta)))
            now = self._utc_now()
            conn.execute(self.grid_levels.update().where(
                self.grid_levels.c.grid_id == int(grid_id),
                self.grid_levels.c.level_idx == int(level_idx),
            ).values(**{**level_fields, "updated_at": now}))
        return not bool(exists_row)

    def settle_grid_dust_sweep(self, grid_id: int, client_order_id: str, qty: Any,
                               proceeds_net: Any, fee_usdt: Any = 0) -> bool:
        """Exactly-once dust debit and proceeds credit for a recovered market order."""
        key, amount, proceeds, fee = (f"sweep:{client_order_id}", Decimal(str(qty)),
                                      float(proceeds_net), float(fee_usdt))
        with self.engine.begin() as conn:
            exists_row = conn.execute(select(self.grid_dust_ledger.c.source_key).where(
                self.grid_dust_ledger.c.grid_id == int(grid_id),
                self.grid_dust_ledger.c.source_key == key,
            )).first()
            if exists_row:
                return False
            current = conn.execute(select(self.grids.c.dust_qty).where(
                self.grids.c.id == int(grid_id))).scalar_one_or_none()
            updated = max(Decimal(0), Decimal(str(current or 0)) - amount)
            conn.execute(self.grid_dust_ledger.insert().values(
                grid_id=int(grid_id), source_key=key, qty=-amount))
            conn.execute(self.grids.update().where(self.grids.c.id == int(grid_id)).values(
                dust_qty=str(updated)))
            row = conn.execute(select(self.grids.c.params).where(self.grids.c.id == int(grid_id))).first()
            params = json.loads(row[0]) if row and row[0] else {}
            params["dust_cash_proceeds"] = float(params.get("dust_cash_proceeds", 0)) + proceeds
            params["dust_sweep_fee_usdt"] = float(params.get("dust_sweep_fee_usdt", 0)) + fee
            conn.execute(self.grids.update().where(self.grids.c.id == int(grid_id)).values(params=self._json(params)))
        return True

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

    def update_level_with_event(
        self, grid_id: int, level_idx: int, *, event: dict[str, Any], **fields: Any,
    ) -> dict | None:
        """Commit a level update and its causative audit event atomically."""
        allowed = {column.name for column in self.grid_levels.columns} - {"grid_id", "level_idx"}
        if not set(fields) <= allowed:
            raise ValueError("invalid grid level update fields")
        source = str(event.get("source", "CLI")).upper()
        if source not in {"MONITOR", "CLI"}:
            raise ValueError("source must be MONITOR or CLI")
        now = datetime.now(timezone.utc)
        if self.engine.dialect.name == "sqlite":
            now = now.replace(tzinfo=None)
        event_values = {
            "run_id": event.get("run_id"), "source": source, "ts": now,
            "grid_id": int(grid_id), "level_idx": int(level_idx),
            "client_order_id": event.get("client_order_id"),
            "order_id": event.get("order_id"),
            "event_type": str(event["event_type"]), "reason": event.get("reason"),
            "price": None if event.get("price") is None else float(event["price"]),
            "details": self._json(event.get("details") or {}),
        }
        with self.engine.begin() as conn:
            conn.execute(
                self.grid_levels.update().where(
                    self.grid_levels.c.grid_id == int(grid_id),
                    self.grid_levels.c.level_idx == int(level_idx),
                ).values(**fields, updated_at=now)
            )
            conn.execute(self.grid_events.insert().values(**event_values))
        return next((row for row in self.get_grid_levels(grid_id)
                     if int(row["level_idx"]) == int(level_idx)), None)

    def update_levels_and_grid_with_event(
        self, grid_id: int, *, level_updates: dict[int, dict[str, Any]],
        grid_fields: dict[str, Any], event: dict[str, Any],
    ) -> None:
        """Atomically commit an ADJUST capital redistribution and its audit event."""
        level_allowed = {column.name for column in self.grid_levels.columns} - {"grid_id", "level_idx"}
        grid_allowed = {column.name for column in self.grids.columns} - {"id"}
        if any(not set(values) <= level_allowed for values in level_updates.values()):
            raise ValueError("invalid grid level update fields")
        if not set(grid_fields) <= grid_allowed:
            raise ValueError("invalid grid update fields")
        source = str(event.get("source", "CLI")).upper()
        if source not in {"MONITOR", "CLI"}:
            raise ValueError("source must be MONITOR or CLI")
        now = datetime.now(timezone.utc)
        if self.engine.dialect.name == "sqlite":
            now = now.replace(tzinfo=None)
        event_values = {
            "run_id": event.get("run_id"), "source": source, "ts": now,
            "grid_id": int(grid_id), "level_idx": event.get("level_idx"),
            "client_order_id": event.get("client_order_id"),
            "order_id": event.get("order_id"),
            "event_type": str(event["event_type"]), "reason": event.get("reason"),
            "price": None if event.get("price") is None else float(event["price"]),
            "details": self._json(event.get("details") or {}),
        }
        with self.engine.begin() as conn:
            for level_idx, fields in level_updates.items():
                conn.execute(self.grid_levels.update().where(
                    self.grid_levels.c.grid_id == int(grid_id),
                    self.grid_levels.c.level_idx == int(level_idx),
                ).values(**fields, updated_at=now))
            conn.execute(self.grids.update().where(
                self.grids.c.id == int(grid_id),
            ).values(**grid_fields))
            conn.execute(self.grid_events.insert().values(**event_values))

    def add_grid_level(self, grid_id: int, level: dict[str, Any]) -> dict:
        now = datetime.now(timezone.utc)
        if self.engine.dialect.name == "sqlite":
            now = now.replace(tzinfo=None)
        values = {
            "grid_id": int(grid_id), "level_idx": int(level["level_idx"]),
            "price": float(level["price"]), "capital": float(level["capital"]),
            "capital_base": (
                float(level["capital_base"]) if level.get("capital_base") is not None
                else float(level["capital"]) - float(level.get("capital_compound", 0.0))
            ),
            "capital_compound": float(level.get("capital_compound", 0.0)),
            "capital_loan": float(level.get("capital_loan", 0.0)),
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
