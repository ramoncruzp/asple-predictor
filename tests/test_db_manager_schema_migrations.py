from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from api.routes import models_status, volatility
from config.models_config import VOL_SYMBOL
from database.db_manager import DBManager


MISSING_WIDEN_COLUMNS = {"k_stress_q68", "k_base_q68", "stress_count", "base_count"}


def _columns(conn, table):
    return {row[1] for row in conn.exec_driver_sql(f"PRAGMA table_info('{table}')")}


def test_old_widen_schema_migrates_only_four_columns_and_real_apis_work(tmp_path):
    path = tmp_path / "old-widen.sqlite"
    db = DBManager(f"sqlite:///{path}")
    original_schema = {}
    with db.engine.connect() as conn:
        original_schema = {
            name: _columns(conn, name)
            for name in db.metadata.tables
        }
    for i in range(2):
        db.save_widen_factor_record({
            "symbol": VOL_SYMBOL, "horizon_h": 4,
            "computed_at": datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(days=i),
            "kind": "suggestion", "n": 10 + i, "n_effective": 8 + i,
            "k_active": 1.25, "status": "acumulando",
            "k_stress_q68": 1.4 + i, "k_base_q68": 1.1 + i,
            "stress_count": 4 + i, "base_count": 6 + i,
        })
    with db.engine.begin() as conn:
        for column in sorted(MISSING_WIDEN_COLUMNS):
            conn.exec_driver_sql(f"ALTER TABLE vol_widen_suggestions DROP COLUMN {column}")
        old_columns = _columns(conn, "vol_widen_suggestions")
        before_rows = conn.exec_driver_sql(
            "SELECT id, symbol, horizon_h, kind, n, k_active FROM vol_widen_suggestions "
            "WHERE kind='suggestion' ORDER BY id"
        ).all()
    assert original_schema["vol_widen_suggestions"] - old_columns == MISSING_WIDEN_COLUMNS
    assert all(original_schema[name] == _columns_for(db, name) for name in original_schema if name != "vol_widen_suggestions")
    db.engine.dispose()

    db = DBManager(f"sqlite:///{path}")
    with db.engine.connect() as conn:
        migrated_columns = _columns(conn, "vol_widen_suggestions")
        after_rows = conn.exec_driver_sql(
            "SELECT id, symbol, horizon_h, kind, n, k_active FROM vol_widen_suggestions "
            "WHERE kind='suggestion' ORDER BY id"
        ).all()
        schema_after_first_open = {name: _columns(conn, name) for name in db.metadata.tables}
    assert migrated_columns == original_schema["vol_widen_suggestions"]
    assert before_rows == after_rows
    assert schema_after_first_open == original_schema
    with db.engine.connect() as conn:
        types = {row[1]: row[2].upper() for row in conn.exec_driver_sql("PRAGMA table_info('vol_widen_suggestions')")}
    assert {name: types[name] for name in MISSING_WIDEN_COLUMNS} == {
        "k_stress_q68": "FLOAT", "k_base_q68": "FLOAT",
        "stress_count": "INTEGER", "base_count": "INTEGER",
    }
    assert db.get_latest_widen_factor(VOL_SYMBOL, 4)["n"] == 11
    assert len(db.get_widen_factor_history(VOL_SYMBOL, 4)) == 2
    assert db.get_widen_active_values(VOL_SYMBOL, 4, 1.25, 15)["k_active"] == 1.25
    db.engine.dispose()

    db = DBManager(f"sqlite:///{path}")
    db.add_or_reactivate_coin(VOL_SYMBOL)
    predictions = []
    now = datetime.now(timezone.utc)
    for i in range(33):
        prediction_id = db.save_prediction({
            "prediction_id": f"xgb-{i}", "symbol": VOL_SYMBOL, "interval": "1h",
            "model_name": "model_a", "predicted_at": now + timedelta(seconds=i),
            "probability_up": 0.7, "signal": "ALCISTA", "confidence": "alta",
            "price_at_prediction": 100.0,
        })
        predictions.append(prediction_id)
    for prediction_id in predictions[:29]:
        db.save_outcome(prediction_id, 101.0)
    summary = db.get_prediction_signal_summary(VOL_SYMBOL, "1h", "model_a")
    assert (summary["total_predictions"], summary["verified_count"], summary["pending_count"]) == (33, 29, 4)

    app = FastAPI()
    app.include_router(models_status.router, prefix="/api/models")
    app.include_router(volatility.router, prefix="/api/volatility")
    app.state.db = db
    app.state.vol_predictor = SimpleNamespace(symbol=VOL_SYMBOL)
    app.state.vol_loop = object()
    with TestClient(app) as client:
        context = client.get("/api/models/page-context", params={"symbol": VOL_SYMBOL, "interval": "1h"})
        assert context.status_code == 200, context.text
        counts = context.json()["models"]["model_a"]
        assert (counts["total_predictions"], counts["verified_count"], counts["pending_count"]) == (33, 29, 4)
        stats = client.get("/api/volatility/model-stats", params={"symbol": VOL_SYMBOL, "horizon": 4})
        assert stats.status_code == 200, stats.text
        widen = client.get("/api/volatility/widen-factor", params={"symbol": VOL_SYMBOL})
        assert widen.status_code == 200, widen.text
    db.engine.dispose()


def _columns_for(db, table):
    with db.engine.connect() as conn:
        return _columns(conn, table)
