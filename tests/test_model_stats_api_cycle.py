import json
import math
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from api.routes import volatility
from config.models_config import VOL_CHAMPIONS, VOL_MODELS, VOL_SYMBOL
from database.db_manager import DBManager
from models.volatility.model_stats import calculate_model_stats


def _assert_acyclic_finite(value, ancestors=(), path="$", seen_paths=None):
    seen_paths = seen_paths if seen_paths is not None else []
    if isinstance(value, (dict, list, tuple)):
        identity = id(value)
        assert identity not in ancestors, f"cycle detected at {path}"
        next_ancestors = (*ancestors, identity)
        items = value.items() if isinstance(value, dict) else enumerate(value)
        for key, child in items:
            _assert_acyclic_finite(child, next_ancestors, f"{path}.{key}", seen_paths)
    elif isinstance(value, float):
        assert math.isfinite(value), f"non-finite float at {path}: {value}"


def test_model_stats_testclient_serializes_many_verified_rows_without_cycles(tmp_path):
    db = DBManager(f"sqlite:///{tmp_path / 'model-stats-cycle.sqlite'}")
    now = datetime.now(timezone.utc)
    forecasts = []
    for model in VOL_MODELS:
        for i in range(36):
            at = now - timedelta(days=8) + timedelta(hours=i)
            forecasts.append({
                "symbol": VOL_SYMBOL, "horizon_h": 4, "model_name": model,
                "forecast_at": at, "made_at": at,
                "pred_logvol_raw": 0.04 + i / 10000,
                "pred_logvol_cal": 0.04 + i / 10000,
                "var_factor": 1.0, "is_champion": model == VOL_CHAMPIONS[4],
            })
    db.save_vol_forecasts(forecasts)
    with db.engine.connect() as conn:
        forecast_ids = conn.execute(select(db.vol_forecasts.c.id).where(
            db.vol_forecasts.c.symbol == VOL_SYMBOL,
            db.vol_forecasts.c.horizon_h == 4,
        )).scalars().all()
    assert len(forecast_ids) == 36 * len(VOL_MODELS)
    for forecast_id in forecast_ids:
        assert db.save_vol_realized(forecast_id, 0.035)

    rows = db.get_vol_model_stats_dispersion_rows(VOL_SYMBOL, 4)
    assert {model: sum(row["model_name"] == model and row["verified_at"] is not None for row in rows)
            for model in VOL_MODELS} == {model: 36 for model in VOL_MODELS}
    calculated = calculate_model_stats(
        rows, VOL_MODELS, VOL_CHAMPIONS, {model: 0.02 for model in VOL_MODELS}, {}, now,
    )
    _assert_acyclic_finite(calculated)

    app = FastAPI()
    app.include_router(volatility.router, prefix="/api/volatility")
    app.state.db = db
    app.state.vol_predictor = SimpleNamespace(symbol=VOL_SYMBOL)
    app.state.vol_loop = object()
    with TestClient(app) as client:
        response = client.get(
            "/api/volatility/model-stats",
            params={"symbol": VOL_SYMBOL, "horizon": 4},
        )
    assert response.status_code == 200, response.text[:400]
    payload = response.json()
    json.dumps(payload, allow_nan=False)
    _assert_acyclic_finite(payload)
    assert "snapshots" not in payload["horizons"][0]["adaptive"]
    db.engine.dispose()
