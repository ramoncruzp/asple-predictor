from datetime import datetime, timedelta, timezone
from sqlalchemy import event

import pandas as pd
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routes import models_status
from database.db_manager import DBManager
from database.learning_engine import LearningEngine, price_at


def _prediction(db, verify_at, prediction_id="late-1"):
    return db.save_prediction({
        "prediction_id": prediction_id,
        "symbol": "XRPUSDT", "interval": "1h", "model_name": "model_a",
        "predicted_at": verify_at - timedelta(hours=4), "verify_at": verify_at,
        "probability_up": 0.7, "signal": "ALCISTA", "confidence": "high",
        "price_at_prediction": 100.0,
        "market_condition": {"volatility_4h": {
            "range_1sigma": [98, 102], "range_2sigma": [96, 104]}},
    })


class CandleClient:
    def __init__(self, current_price=120.0, frame=None):
        self.current_price = current_price
        self.frame = frame
        self.current_calls = 0
        self.kline_calls = []

    def get_current_price(self, symbol):
        self.current_calls += 1
        return {"price": self.current_price}

    def get_historical_klines(self, symbol, interval, lookback_days=1, start_time=None):
        self.kline_calls.append((symbol, interval, lookback_days, start_time))
        return self.frame if self.frame is not None else pd.DataFrame()


def test_verification_within_one_hour_uses_current_price():
    db = DBManager("sqlite:///:memory:")
    verify_at = datetime.now(timezone.utc) - timedelta(minutes=30)
    prediction_id = _prediction(db, verify_at)
    client = CandleClient(current_price=101.0)

    assert LearningEngine(db, client).verify_pending_predictions() == 1

    outcome = db.get_predictions_with_outcomes(model_name="model_a")[0]
    assert outcome["price_at_verification"] == 101.0
    assert outcome["verification_status"] == "verified"
    assert outcome["verify_delay_h"] == pytest.approx(0.5, abs=0.01)
    assert client.current_calls == 1
    assert client.kline_calls == []
    db.engine.dispose()


def test_five_hour_late_verification_interpolates_at_verify_at():
    db = DBManager("sqlite:///:memory:")
    verify_at = datetime.now(timezone.utc) - timedelta(hours=5)
    prediction_id = _prediction(db, verify_at)
    candle_open = verify_at - timedelta(minutes=30)
    frame = pd.DataFrame([{
        "timestamp": candle_open,
        "open": 100.0,
        "close_time": verify_at,
        "close": 102.5,
    }])
    client = CandleClient(current_price=120.0, frame=frame)

    assert LearningEngine(db, client).verify_pending_predictions() == 1

    row = next(row for row in db.get_predictions_with_outcomes(model_name="model_a")
               if row["prediction_id"] == prediction_id)
    assert row["price_at_verification"] == 101.25
    assert row["verification_status"] == "verified"
    assert row["verify_delay_h"] == pytest.approx(5.0, abs=0.01)
    assert client.current_calls == 0
    assert client.kline_calls and client.kline_calls[0][1] == "1h"
    db.engine.dispose()


def test_missing_late_candle_is_excluded_and_counted_in_api_summaries(tmp_path, monkeypatch):
    db = DBManager(f"sqlite:///{tmp_path / 'late-summary.sqlite'}")
    db.add_or_reactivate_coin("XRPUSDT")
    prediction_id = _prediction(db, datetime.now(timezone.utc) - timedelta(hours=5))
    client = CandleClient(frame=pd.DataFrame())

    assert LearningEngine(db, client).verify_pending_predictions() == 0
    assert db.get_pending_verifications() == []
    row = db.get_predictions_with_outcomes(model_name="model_a")[0]
    assert row["verification_status"] == "unverifiable_late"
    assert row["is_verified"] is False
    assert row["verify_delay_h"] == pytest.approx(5.0, abs=0.01)
    assert db.get_battle_stats("model_a")["n_unverifiable_late"] == 1
    signal_summary = db.get_prediction_signal_summary("XRPUSDT", "1h", "model_a")
    assert signal_summary["n_unverifiable_late"] == 1
    assert signal_summary["verified_count"] == 0
    assert signal_summary["base_rate"] is None
    assert db.get_shadow_stats("model_a", "XRPUSDT", "1h")["n_verified_total"] == 0
    coverage = db.get_volatility_coverage("XRPUSDT", "1h")
    assert coverage["n"] == 0 and coverage["n_unverifiable_late"] == 1

    app = FastAPI()
    app.include_router(models_status.router, prefix="/api/models")
    app.state.db = db
    app.state.models = {}
    app.state.model_load_report = {}
    monkeypatch.setattr(models_status, "read_metrics_manifest", lambda *_args: {})
    monkeypatch.setattr(models_status, "artifact_trained_at", lambda *_args, **_kwargs: None)
    with TestClient(app) as web:
        status = web.get("/api/models/status")
        context = web.get("/api/models/page-context", params={"symbol": "XRPUSDT", "interval": "1h"})
    assert status.status_code == 200
    assert context.status_code == 200
    assert next(item for item in status.json()["models"] if item["model_name"] == "model_a")["n_unverifiable_late"] == 1
    assert context.json()["models"]["model_a"]["n_unverifiable_late"] == 1
    db.engine.dispose()


def test_direction_verification_columns_migrate_idempotently_and_preserve_rows(tmp_path):
    path = tmp_path / "legacy-direction.sqlite"
    db = DBManager(f"sqlite:///{path}")
    prediction_id = _prediction(db, datetime.now(timezone.utc) - timedelta(hours=1), "legacy")
    db.save_outcome(prediction_id, 101.0)
    with db.engine.begin() as conn:
        conn.exec_driver_sql("ALTER TABLE predictions DROP COLUMN verification_status")
        conn.exec_driver_sql("ALTER TABLE predictions DROP COLUMN verify_delay_h")
        conn.exec_driver_sql("ALTER TABLE outcomes DROP COLUMN verify_delay_h")
        before = conn.exec_driver_sql(
            "SELECT prediction_id, price_at_verification, was_correct FROM outcomes"
        ).fetchall()
    db.engine.dispose()

    first = DBManager(f"sqlite:///{path}")
    with first.engine.connect() as conn:
        pred_info = conn.exec_driver_sql("PRAGMA table_info('predictions')").fetchall()
        outcome_info = conn.exec_driver_sql("PRAGMA table_info('outcomes')").fetchall()
        pred_columns = {row[1] for row in pred_info}
        outcome_columns = {row[1] for row in outcome_info}
        after = conn.exec_driver_sql(
            "SELECT prediction_id, price_at_verification, was_correct FROM outcomes"
        ).fetchall()
    assert {"verification_status", "verify_delay_h"} <= pred_columns
    assert {"verify_delay_h"} <= outcome_columns
    assert all(row[3] == 0 for row in pred_info if row[1] in {"verification_status", "verify_delay_h"})
    assert all(row[3] == 0 for row in outcome_info if row[1] == "verify_delay_h")
    assert before == after
    first.engine.dispose()

    second = DBManager(f"sqlite:///{path}")
    with second.engine.connect() as conn:
        again = conn.exec_driver_sql(
            "SELECT prediction_id, price_at_verification, was_correct FROM outcomes"
        ).fetchall()
    assert again == before
    second.engine.dispose()


def test_condition_accuracy_refresh_is_disabled_when_no_reader_consumes_the_table():
    db = DBManager("sqlite:///:memory:")
    prediction_id = db.save_prediction({
        "prediction_id": "condition-row", "symbol": "XRPUSDT", "interval": "1h",
        "model_name": "model_a", "probability_up": 0.7, "signal": "ALCISTA",
        "confidence": "high", "price_at_prediction": 100.0,
        "features_snapshot": {"rsi_14": 20, "vol_ratio": 2, "ema_cross": 1},
    })
    db.save_outcome(prediction_id, 101.0)
    statements = []
    event.listen(db.engine, "before_cursor_execute", lambda _conn, _cursor, sql, _params, *_args: statements.append(sql))

    result = LearningEngine(db, object()).update_condition_accuracy("model_a")

    writes = [sql for sql in statements if sql.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))]
    assert result == []
    assert statements == []
    assert writes == []
    with db.engine.connect() as conn:
        assert conn.exec_driver_sql("SELECT COUNT(*) FROM model_accuracy_by_condition").scalar_one() == 0
    db.engine.dispose()


def test_price_at_interpolates_hourly_candle_boundaries_and_gaps():
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    next_hour = start + timedelta(hours=1)
    candles = pd.DataFrame([
        {"timestamp": start, "close_time": next_hour - timedelta(milliseconds=1),
         "open": 100.0, "close": 110.0},
        {"timestamp": next_hour, "close_time": next_hour + timedelta(hours=1) - timedelta(milliseconds=1),
         "open": 200.0, "close": 220.0},
    ])
    assert price_at(start, candles) == 100.0
    assert price_at(start + timedelta(minutes=30), candles) == 105.0
    assert price_at(start + timedelta(hours=1) - timedelta(microseconds=1), candles) == pytest.approx(110.0)
    # At the exact hour boundary, the new candle's open is the point-in-time price.
    assert price_at(next_hour, candles) == 200.0
    assert price_at(start + timedelta(hours=2), candles) is None

    gap = pd.DataFrame([
        {"timestamp": start, "close_time": next_hour - timedelta(milliseconds=1),
         "open": 100.0, "close": 110.0},
        {"timestamp": next_hour + timedelta(hours=1),
         "close_time": next_hour + timedelta(hours=2) - timedelta(milliseconds=1),
         "open": 220.0, "close": 230.0},
    ])
    assert price_at(next_hour + timedelta(minutes=30), gap) is None


def test_price_at_requires_open_and_a_containing_candle():
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    no_open = pd.DataFrame([{"timestamp": start, "close": 101.0}])
    assert price_at(start + timedelta(minutes=20), no_open) is None
