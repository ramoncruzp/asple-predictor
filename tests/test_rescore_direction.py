from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone

import pandas as pd

from database.db_manager import DBManager
from database.learning_engine import LearningEngine, price_at
from scripts.rescore_direction import _close_at, main


def _seed_db(path):
    db = DBManager(f"sqlite:///{path}")
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for model_name, signal, offset in (
        ("model_a", "ALCISTA", 0),
        ("model_b", "BAJISTA", 0),
        ("model_c", "ALCISTA", 3),
    ):
        predicted_at = base + timedelta(days=offset)
        prediction_id = db.save_prediction({
            "prediction_id": model_name,
            "symbol": "XRPUSDT",
            "interval": "1h",
            "model_name": model_name,
            "predicted_at": predicted_at,
            "verify_at": predicted_at + timedelta(minutes=30),
            "probability_up": 0.7,
            "signal": signal,
            "confidence": "high",
            "price_at_prediction": 100.0,
            "features_snapshot": {},
        })
        db.save_outcome(prediction_id, 100.0, verify_delay_h=5.0)
        if offset == 0:
            with db.engine.begin() as connection:
                connection.exec_driver_sql(
                    "UPDATE outcomes SET verified_at='2026-01-01 01:00:00' WHERE prediction_id=?",
                    (prediction_id,),
                )
    db.engine.dispose()


def _hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _csv(path):
    pd.DataFrame([
        {"timestamp": "2026-01-01T00:00:00Z", "close_time": "2026-01-01T00:59:59.999Z", "open": 100.0, "close": 102.0},
    ]).to_csv(path, index=False)


def test_rescore_dry_run_reports_changes_and_keeps_database_bytes(tmp_path, capsys):
    db_path = tmp_path / "scores.sqlite"
    candles_path = tmp_path / "candles.csv"
    _seed_db(db_path)
    _csv(candles_path)
    before_hash = _hash(db_path)

    assert main(["--db", str(db_path), "--candles", str(candles_path)]) == 0

    report = json.loads(capsys.readouterr().out)
    assert _hash(db_path) == before_hash
    by_model = {item["model_name"]: item for item in report["models"]}
    assert by_model["model_a"]["before"]["n"] == 1
    assert by_model["model_a"]["after"]["n"] == 1
    assert by_model["model_a"]["after"]["base_rate_up"] == 1.0
    assert by_model["model_a"]["after"]["accuracy"] == 1.0
    assert by_model["model_b"]["after"]["accuracy"] == 0.0
    assert by_model["model_c"]["missing_candles"] == 1


def test_rescore_apply_updates_only_candle_matches_and_is_idempotent(tmp_path, capsys):
    db_path = tmp_path / "scores.sqlite"
    candles_path = tmp_path / "candles.csv"
    _seed_db(db_path)
    _csv(candles_path)
    args = ["--db", str(db_path), "--candles", str(candles_path), "--apply", "--i-know-this-is-a-copy"]

    assert main(args) == 0
    capsys.readouterr()
    first_hash = _hash(db_path)
    assert main(args) == 0
    capsys.readouterr()
    assert _hash(db_path) == first_hash

    db = DBManager(f"sqlite:///{db_path}")
    with db.engine.connect() as conn:
        rows = {row["prediction_id"]: row for row in conn.exec_driver_sql(
            "SELECT p.prediction_id, p.verification_status, p.verify_delay_h, o.price_at_verification, "
            "o.price_change_pct, o.actual_direction, o.was_correct, o.verify_delay_h AS outcome_delay "
            "FROM predictions p JOIN outcomes o USING(prediction_id)"
        ).mappings()}
    assert rows["model_a"]["price_at_verification"] == 101.0
    assert rows["model_a"]["actual_direction"] == "UP"
    assert rows["model_a"]["was_correct"] == 1
    assert rows["model_a"]["verification_status"] == "verified"
    assert rows["model_a"]["outcome_delay"] == 0.5
    assert rows["model_c"]["price_at_verification"] == 100.0
    assert rows["model_c"]["outcome_delay"] == 5.0
    db.engine.dispose()


def test_live_and_rescore_routes_share_the_same_interpolated_price(tmp_path):
    candles_path = tmp_path / "candles.csv"
    _csv(candles_path)
    frame = pd.read_csv(candles_path)
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True)
    frame["close_time"] = pd.to_datetime(frame["close_time"], utc=True)
    verify_at = pd.Timestamp("2026-01-01T00:30:00Z")

    class Client:
        def get_historical_klines(self, *_args, **_kwargs):
            return frame

    live_price = LearningEngine(None, Client())._close_for_verify_at("XRPUSDT", verify_at.to_pydatetime())
    assert live_price == _close_at(frame, verify_at) == price_at(verify_at, frame) == 101.0


def test_dry_run_reports_late_rows_missing_from_candle_csv(tmp_path, capsys):
    db_path = tmp_path / "scores.sqlite"
    candles_path = tmp_path / "candles.csv"
    _seed_db(db_path)
    _csv(candles_path)

    assert main(["--db", str(db_path), "--candles", str(candles_path)]) == 0
    report = json.loads(capsys.readouterr().out)
    missing = report["late_rows_missing_candles"]
    assert missing["count"] == 1
    assert missing["first_verify_at"].startswith("2026-01-04T00:30:00")
    assert missing["last_verify_at"] == missing["first_verify_at"]
    assert missing["latest_candle_timestamp"].startswith("2026-01-01T00:00:00")
    assert "estas filas no se corrigen con este CSV; actualiza el caché de velas y vuelve a correr el dry-run" in missing["message"]
