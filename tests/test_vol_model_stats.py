from datetime import datetime, timedelta, timezone
from math import exp

import pytest
from types import SimpleNamespace
import time

from api.routes.volatility import model_stats
from api.routes.volatility import forecast as api_forecast
from config.models_config import VOL_CHAMPIONS, VOL_HORIZONS, VOL_MODELS
from database.db_manager import DBManager

from models.volatility.model_stats import (
    MAX_MODEL_WEIGHT, N_MIN, adaptive_weight_history, calculate_model_stats,
    capped_normalize, dispersion_bucket, forward_consensus_metrics, is_mature_verified,
    LIVE_VALIDATION_MIN_EFFECTIVE,
)


def _rows(n=30, now=None):
    now = now or datetime(2026, 1, 10, tzinfo=timezone.utc)
    rows = []
    for i in range(n):
        at = now - timedelta(hours=100 - i)
        for model, offset in (("Persistence", 0.2), ("GBM", 0.0), ("HAR", 0.03), ("NexoHAR", 0.06)):
            rows.append({"symbol": "XRPUSDT", "horizon_h": 1, "model_name": model,
                         "forecast_at": at, "verified_at": at + timedelta(hours=1),
                         "pred_logvol_cal": -5.0 + offset,
                         "realized_logvol": -5.0, "pred_logvol_raw": -5.0 + offset})
    return rows


def test_stats_fixed_metrics_and_active_threshold_29_30():
    now = datetime(2026, 1, 10, tzinfo=timezone.utc)
    sigma = {"GBM": 0.01, "HAR": 0.02, "Persistence": 0.03}
    val = {"GBM": 0.4, "HAR": 0.4, "Persistence": 0.0}
    data29 = calculate_model_stats(_rows(29, now), ["Persistence", "GBM", "HAR"],
                                   {1: "GBM"}, sigma, val, now)
    data30 = calculate_model_stats(_rows(30, now), ["Persistence", "GBM", "HAR"],
                                   {1: "GBM"}, sigma, val, now)
    assert data29["models"][1]["n_verificadas"] == 29
    assert data29["models"][1]["estado"] == "acumulando"
    gbm = data30["models"][1]
    assert gbm["estado"] == "activo"
    assert gbm["all"]["mse"] == pytest.approx(0)
    assert gbm["all"]["coverage_1sigma"] == 1
    assert gbm["all"]["coverage_2sigma"] == 1
    assert gbm["all"]["success_1sigma"] == 30
    assert gbm["all"]["failures"] == 0
    assert gbm["all"]["bias_mean"] == pytest.approx(0)


def test_maturity_excludes_future_and_unverified_results():
    now = datetime(2026, 1, 10, tzinfo=timezone.utc)
    row = _rows(1, now)[0]
    row["forecast_at"] = now - timedelta(hours=1)
    row["horizon_h"] = 4
    row["verified_at"] = now
    assert not is_mature_verified(row, now)
    row["forecast_at"] = now - timedelta(hours=2)
    row["verified_at"] = now + timedelta(seconds=1)
    assert not is_mature_verified(row, now)
    row["verified_at"] = now
    row["realized_logvol"] = None
    assert not is_mature_verified(row, now)


def test_adaptive_weights_use_persistence_reference_smooth_and_cap():
    now = datetime(2026, 1, 10, tzinfo=timezone.utc)
    rows = _rows(35, now)
    val = {"GBM": 0.4, "HAR": 0.4, "NexoHAR": 0.2, "Persistence": 0.0}
    models = ["Persistence", "GBM", "HAR", "NexoHAR"]
    state = adaptive_weight_history(rows, models, val, now)
    assert state["source"] == "vivo"
    assert state["eligible"] == ["GBM", "HAR", "NexoHAR"]
    assert sum(state["P"].values()) == pytest.approx(1)
    assert max(state["P"].values()) <= MAX_MODEL_WEIGHT + 1e-12
    assert max(state["P2"].values()) <= MAX_MODEL_WEIGHT + 1e-12
    assert state["P"]["GBM"] > state["P"]["HAR"]
    limited = adaptive_weight_history(_rows(29, now), models, val, now)
    assert limited["source"] == "val"
    assert limited["P"] == val
    assert limited["P2"] == {}


def test_adaptive_history_matches_naive_numeric_snapshot():
    now = datetime(2026, 1, 10, tzinfo=timezone.utc)
    rows = _rows(35, now)
    models = ["Persistence", "GBM", "HAR", "NexoHAR"]
    val = {"GBM": .4, "HAR": .4, "NexoHAR": .2, "Persistence": 0.0}
    result = adaptive_weight_history(rows, models, val, now)
    timestamps = sorted({row["forecast_at"] for row in rows})
    prior = {model: {} for model in ("P", "P2")}
    snapshots = []
    for at in timestamps:
        mse = {}
        for model in models:
            past = [r for r in rows if r["model_name"] == model and r["realized_logvol"] is not None
                    and r["verified_at"] is not None and r["verified_at"] <= now
                    and r["forecast_at"] + timedelta(hours=r["horizon_h"]) <= now
                    and r["verified_at"] <= at][-168:]
            errors = [r["pred_logvol_cal"] - r["realized_logvol"] for r in past]
            mse[model] = sum(e * e for e in errors) / len(errors) if len(errors) >= N_MIN else None
        eligible = [m for m in models if m != "Persistence" and mse[m] is not None
                    and mse["Persistence"] is not None and mse[m] <= mse["Persistence"]]
        raw = {key: ({m: 1 / max(mse[m], 1e-12) ** (1 if key == "P" else 2) for m in eligible}
                     if len(eligible) >= 3 else {}) for key in ("P", "P2")}
        for key in ("P", "P2"):
            if raw[key]:
                smooth = {m: .8 * prior[key].get(m, 0) + .2 * raw[key].get(m, 0) for m in raw[key]}
                prior[key] = capped_normalize(smooth)
            else:
                prior[key] = {}
        snapshots.append((at, eligible[:], prior["P"].copy(), prior["P2"].copy()))
    actual = result["snapshots"][-1]
    expected = snapshots[-1]
    assert actual["forecast_at"] == expected[0]
    assert actual["eligible"] == expected[1]
    assert actual["P"] == pytest.approx(expected[2])
    assert actual["P2"] == pytest.approx(expected[3])


def test_adaptive_history_5000_forecasts_per_model_timing():
    now = datetime(2026, 1, 10, tzinfo=timezone.utc)
    models = ["Persistence", "GBM", "HAR", "NexoHAR"]
    rows = []
    for i in range(5000):
        at = now - timedelta(hours=6000 - i)
        for index, model in enumerate(models):
            rows.append({"horizon_h": 1, "model_name": model, "forecast_at": at,
                             "verified_at": at + timedelta(hours=1),
                             "pred_logvol_cal": -4.8 if index == 0 else -5 + index * .01,
                         "realized_logvol": -5.0})
    started = time.perf_counter()
    result = adaptive_weight_history(rows, models,
        {"Persistence": 0, "GBM": .4, "HAR": .4, "NexoHAR": .2}, now)
    elapsed = time.perf_counter() - started
    assert len(result["snapshots"]) == 5000
    assert result["source"] == "vivo"
    print(f"adaptive_weight_history: 5000 filas/modelo, 4 modelos: {elapsed:.3f}s")


def test_bounded_history_keeps_unverified_rows_and_limits_verified_tail(tmp_path):
    db = DBManager(f"sqlite:///{tmp_path / 'stats.sqlite'}")
    now = datetime.now(timezone.utc)
    rows = []
    for i in range(250):
        at = now - timedelta(days=40) + timedelta(hours=i)
        rows.append({"symbol": "XRPUSDT", "horizon_h": 1, "model_name": "GBM",
                     "forecast_at": at, "made_at": at, "pred_logvol_raw": -5,
                     "pred_logvol_cal": -5, "var_factor": 1, "is_champion": True})
    db.save_vol_forecasts(rows)
    with db.engine.begin() as conn:
        conn.execute(db.vol_forecasts.update().where(db.vol_forecasts.c.id > 1).values(
            realized_logvol=-5.1, verified_at=now - timedelta(days=1)))
    selected = db.get_vol_model_stats_rows("XRPUSDT", 1)
    assert len(selected) == 199
    assert any(row["realized_logvol"] is None and row["verified_at"] is None for row in selected)
    assert sum(row["realized_logvol"] is not None for row in selected) == 198
    aggregates = db.get_vol_model_stats_aggregates("XRPUSDT", 1, {"GBM": .01}, now)
    assert aggregates["GBM"]["n"] == 249
    assert aggregates["GBM"]["over_pct"] == 100.0
    assert aggregates["GBM"]["qlike"] is not None
    db.engine.dispose()


def test_live_gate_uses_effective_sample_count_at_49_and_50():
    models = ["Persistence", "GBM", "HAR", "NexoHAR"]
    weights = {"GBM": .4, "HAR": .4, "NexoHAR": .2, "Persistence": 0.0}
    now = datetime(2026, 1, 10, tzinfo=timezone.utc)
    assert LIVE_VALIDATION_MIN_EFFECTIVE == 50
    results = {}
    for effective in (49, 50):
        rows = []
        n = effective * 24
        for i in range(n):
            at = now - timedelta(hours=n + 30 - i)
            err = .005 if i % 50 < 34 else .015 if i % 50 < 48 else .03
            for model in models:
                rows.append({"horizon_h": 24, "model_name": model, "forecast_at": at,
                             "verified_at": at,
                             "pred_logvol_cal": -5 + err, "realized_logvol": -5.0})
        # Mature and causally observed outcomes use sigma with 68% / 96% coverage.
        results[effective] = forward_consensus_metrics(rows, models, {24: "GBM"}, weights,
                                                         now, sigma_ref=.01)
    assert results[49]["n"] == 49 * 24
    assert results[49]["n_efectivas"] == pytest.approx(49)
    assert results[49]["validation_status_live"] == "en_evaluacion"
    assert results[50]["n_efectivas"] == pytest.approx(50)
    assert results[50]["validation_status_live"] == "validated"


def test_bias_alert_requires_large_persistent_same_sign_bias():
    now = datetime(2026, 1, 10, tzinfo=timezone.utc)
    base = _rows(30, now)
    for row in base:
        row["realized_logvol"] = -5.0
        row["pred_logvol_cal"] = -4.8 if row["model_name"] == "GBM" else -5.0
    # All 30 GBM outcomes have positive bias above half sigma.
    stats = calculate_model_stats(base, ["Persistence", "GBM", "HAR", "NexoHAR"],
        {1: "GBM"}, {"GBM": .1}, {"GBM": .5}, now)
    assert next(row for row in stats["models"] if row["model_name"] == "GBM")["bias_alert"] is True
    changed = 0
    for row in base:
        if row["model_name"] == "GBM":
            if changed < 7:
                row["pred_logvol_cal"] = -5.2
                changed += 1
    mixed = calculate_model_stats(base, ["Persistence", "GBM", "HAR", "NexoHAR"],
        {1: "GBM"}, {"GBM": .1}, {"GBM": .5}, now)
    assert next(row for row in mixed["models"] if row["model_name"] == "GBM")["bias_alert"] is False


def test_forward_p_uses_val_fallback_weights_before_three_live_eligible():
    now = datetime(2026, 1, 10, tzinfo=timezone.utc)
    rows = _rows(5, now)
    models = ["Persistence", "GBM", "HAR", "NexoHAR"]
    val = {"Persistence": 0.0, "GBM": 0.5, "HAR": 0.5, "NexoHAR": 0.0}
    result = forward_consensus_metrics(rows, models, {1: "GBM"}, val, now, 0.02)
    assert result["outcomes_used"] == 5
    assert result["forward_evaluation"]["P"]["n"] == 5
    assert result["forward_evaluation"]["champion"]["n"] == 5


def test_dispersion_buckets_order_and_spearman_report_shape():
    models = ["a", "b", "c", "d"]
    assert dispersion_bucket(dict(zip(models, [0.0, 0.02, 0.04, 0.05])), models)[1] == "alta"
    assert dispersion_bucket(dict(zip(models, [0.0, 0.1, 0.2, 0.3])), models)[1] == "media"
    assert dispersion_bucket(dict(zip(models, [0.0, 0.2, 0.4, 0.6])), models)[1] == "baja"
    assert N_MIN == 30


def test_capped_normalize_limits_single_model_dominance():
    result = capped_normalize({"A": 1000, "B": 1, "C": 1})
    assert sum(result.values()) == pytest.approx(1)
    assert max(result.values()) <= MAX_MODEL_WEIGHT


def test_model_stats_api_returns_accumulating_for_empty_symbol_horizon():
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(
        vol_predictor=SimpleNamespace(symbol="XRPUSDT"), vol_loop=SimpleNamespace(latest={}),
        db=SimpleNamespace(get_vol_model_stats_rows=lambda symbol, horizon: []),
    )))
    body = model_stats(request, symbol="XRPUSDT", horizon=4)
    horizon = body["horizons"][0]
    assert horizon["horizon_h"] == 4
    assert horizon["n_min"] == 30
    assert len(horizon["models"]) == 8
    assert all(model["estado"] == "acumulando" and model["n_verificadas"] == 0
               and model["peso_actual"] == 0 for model in horizon["models"])
    assert horizon["adaptive"]["source"] == "val"
    assert horizon["forward"]["outcomes_used"] == 0


def test_model_stats_cache_is_keyed_by_horizon_and_invalidated_by_latest_forecast(monkeypatch):
    import api.routes.volatility as route
    clock = {"value": 10.0}
    monkeypatch.setattr(route.time, "monotonic", lambda: clock["value"])
    latest = {"value": "t1"}
    calls = []
    db = SimpleNamespace(
        get_latest_vol_model_stats_forecast_at=lambda symbol, horizon: latest["value"],
        get_vol_model_stats_rows=lambda symbol, horizon: calls.append((symbol, horizon)) or [],
    )
    state = SimpleNamespace(db=db)
    request = SimpleNamespace(app=SimpleNamespace(state=state))
    route._model_stats_rows(request, "XRPUSDT", 1)
    route._model_stats_rows(request, "XRPUSDT", 1)
    route._model_stats_rows(request, "XRPUSDT", 2)
    assert calls == [("XRPUSDT", 1), ("XRPUSDT", 2)]
    latest["value"] = "t2"
    route._model_stats_rows(request, "XRPUSDT", 1)
    assert calls[-1] == ("XRPUSDT", 1)
    assert len(calls) == 3


def test_forecast_applies_saved_p_variance_factor_only_when_marked_applied(tmp_path, monkeypatch):
    import json
    import api.routes.volatility as route

    monkeypatch.setattr(route, "VOL_ARTIFACT_DIR", tmp_path)
    now = datetime.now(timezone.utc)
    rows = [{"horizon_h": h, "model_name": model, "is_champion": model == VOL_CHAMPIONS[h],
             "forecast_at": now, "made_at": now, "pred_logvol_cal": -5.0}
            for h in VOL_HORIZONS for model in VOL_MODELS]
    weights = {name: float(name == "EWMA") for name in VOL_MODELS}
    report = {"report": {"horizons": {str(h): {
        "eligible_models": ["EWMA"], "weights": weights,
        "ensemble_calibration_val": {"P": {"factor": 1.21, "applied": True}},
    } for h in VOL_HORIZONS}}}
    (tmp_path / "consensus_xrp.json").write_text(json.dumps(report), encoding="utf-8")
    db = SimpleNamespace(get_latest_vol_forecasts=lambda symbol: rows,
                         get_vol_model_stats_rows=lambda symbol, horizon: [])
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(
        vol_predictor=SimpleNamespace(symbol="XRPUSDT", manifest={}),
        vol_loop=SimpleNamespace(latest={"price": 100.0}), db=db,
    )))
    result = api_forecast(request)
    consensus = result["forecasts"][0]["consensus"]
    assert consensus["fuente_pesos"] == "val"
    assert consensus["validation_status_live"] == "en_evaluacion"
    assert consensus["validation_status"] == "not_validated"
    assert consensus["sigma_pct"] == pytest.approx(100 * exp(-5) * 1.1)
