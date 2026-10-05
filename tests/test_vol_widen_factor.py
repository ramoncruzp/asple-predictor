from datetime import datetime, timedelta, timezone
import json
from math import exp
from types import SimpleNamespace

import numpy as np
import pytest
from fastapi import HTTPException

from api.routes import volatility
from config.models_config import VOL_WIDEN_AUTO
from database.db_manager import DBManager
from models.volatility.widen_factor import (
    _bootstrap_ci, _is_high_stress_iqr, calculate_widen_factor, classify_widen_status,
    effective_sample_count,
    solve_coverage_factor,
)
from scheduler import widen_factor_loop


def _rows(count=20, now=None):
    now = now or datetime(2026, 10, 5, 12, tzinfo=timezone.utc)
    rows = []
    for i in range(count):
        at = now - timedelta(hours=count - i + 1)
        spread = .4 if i >= count * .8 else .04
        for model, value, champ in (("A", -4.0, True), ("B", -4.0 + spread, False),
                                    ("C", -4.0 + 2 * spread, False)):
            rows.append({"forecast_at": at, "verified_at": at + timedelta(hours=1),
                         "horizon_h": 1, "model_name": model, "pred_logvol_raw": value,
                         "pred_logvol_cal": value,
                         "realized_logvol": value - (2.0 if i >= count * .8 else 1.0),
                         "is_champion": champ})
    return rows


def test_widen_signed_rho_solver_clipping_and_smoothing_are_fixed():
    now = datetime(2026, 10, 5, 12, tzinfo=timezone.utc)
    rows = _rows(5, now)
    result = calculate_widen_factor(rows, 1, "A", {"A": .34, "B": .33, "C": .33},
                                    ["A", "B", "C"], 1.0, now)
    rho = [exp(-1)] * 4 + [exp(-2)]
    assert result["k_global"] == pytest.approx(solve_coverage_factor(rho))
    assert result["k2_global"] == pytest.approx(solve_coverage_factor(rho, .95, 2))
    assert result["k_raw"] < 1.0
    assert result["status"] == "no_aplicable"
    assert result["k_stress_smoothed"] == 1.0
    previous = [{"kind": "suggestion", "k_stress_raw": 1.2}]
    smoothed = calculate_widen_factor(_rows(5, now), 1, "A", {"A": .34, "B": .33, "C": .33},
                                      ["A", "B", "C"], 1.0, now, previous=previous)
    assert smoothed["k_stress_smoothed"] == pytest.approx(.3 * smoothed["k_raw"] + .7 * 1.2)


def test_coverage_solver_direction_bias_and_normal_coverage():
    assert solve_coverage_factor([1.0] * 50) == pytest.approx(0.994, abs=.002)
    assert solve_coverage_factor([exp(1)] * 20) > 1.0
    assert solve_coverage_factor([exp(-.5)] * 20) < 1.0
    rho = np.exp(np.random.default_rng(10).normal(0, .15, 1000))
    k = solve_coverage_factor(rho)
    simulated = np.random.default_rng(11).normal(0, rho)
    assert np.mean(np.abs(simulated) <= k) == pytest.approx(.68, abs=.01)
    now = datetime(2026, 10, 5, 12, tzinfo=timezone.utc)
    rows = _rows(5, now)
    result = calculate_widen_factor(rows, 1, "A", {"A": .34, "B": .33, "C": .33},
                                    ["A", "B", "C"], 1.0, now)
    assert result["bias_log"] == pytest.approx(1.2)
    assert result["vol_scale_suggested"] == pytest.approx(exp(-1.2))
    assert result["overestimate_message"]


def test_widen_status_precision_boundaries_and_effective_samples():
    assert effective_sample_count(456, 24) == 19
    assert effective_sample_count(480, 24) == 20
    assert classify_widen_status(19, .30, 1.0, 1.0) == "acumulando"
    assert classify_widen_status(20, .30, 1.0, 1.0) == "disponible"
    assert classify_widen_status(20, .300001, 1.0, 1.0) == "acumulando"
    assert classify_widen_status(20, .1, 1.0, 1.251) == "inconsistente"


def test_block_bootstrap_is_reproducible():
    values = np.linspace(.2, 2.0, 250)
    assert _bootstrap_ci(values) == _bootstrap_ci(values)
    assert _bootstrap_ci(values[:30]) == (None, None)


def test_widen_calculation_excludes_unmatured_and_future_rows():
    now = datetime(2026, 10, 5, 12, tzinfo=timezone.utc)
    rows = _rows(20, now)
    baseline = calculate_widen_factor(rows, 1, "A", {"A": .34, "B": .33, "C": .33},
                                      ["A", "B", "C"], 1.0, now)
    future = now + timedelta(hours=1)
    rows.extend([
        {"forecast_at": future, "verified_at": future, "model_name": "A",
         "pred_logvol_cal": -4.0, "realized_logvol": -1.0},
        {"forecast_at": future, "verified_at": future, "model_name": "B",
         "pred_logvol_cal": -1.0, "realized_logvol": -1.0},
        {"forecast_at": future, "verified_at": future, "model_name": "C",
         "pred_logvol_cal": -1.0, "realized_logvol": -1.0},
    ])
    result = calculate_widen_factor(rows, 1, "A", {"A": .34, "B": .33, "C": .33},
                                    ["A", "B", "C"], 1.0, now)
    assert result["n"] == baseline["n"]
    assert result["dispersion_n"] == baseline["dispersion_n"]
    assert result["disagreement_n"] == baseline["disagreement_n"]


def test_adaptive_stress_and_disagreement_percentile_have_minimum_history():
    now = datetime(2026, 10, 5, 12, tzinfo=timezone.utc)
    rows = _rows(200, now)
    result = calculate_widen_factor(rows, 1, "A", {"A": .34, "B": .33, "C": .33},
                                    ["A", "B", "C"], 1.0, now)
    assert result["dispersion_n"] == 200
    assert result["stress_count"] > 0
    assert result["disagreement_n"] == 200
    assert result["disagreement_status"] == "disponible"
    assert result["disagreement_threshold_suggested"] == pytest.approx((np.exp(.396) - 1) * 100)
    assert result["stress_threshold"] is not None
    history, stress_flags = [], []
    for _ in range(200):
        stress_flags.append(_is_high_stress_iqr(.1, history))
        history.append(.1)
        history.sort()
    assert 39 <= sum(stress_flags) <= 41


def test_widen_table_is_idempotent_and_additive(tmp_path):
    path = tmp_path / "widen.sqlite"
    first = DBManager(f"sqlite:///{path}")
    with first.engine.connect() as conn:
        before = {name: [row[1] for row in conn.exec_driver_sql(f"PRAGMA table_info('{name}')")]
                  for name in ("predictions", "vol_forecasts", "grids")}
    first.engine.dispose()
    second = DBManager(f"sqlite:///{path}")
    with second.engine.connect() as conn:
        after = {name: [row[1] for row in conn.exec_driver_sql(f"PRAGMA table_info('{name}')")]
                 for name in before}
        tables = {row[0] for row in conn.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='table'")}
    assert before == after
    assert "vol_widen_suggestions" in tables
    second.engine.dispose()


def _api_client(db):
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(db=db)),
                           client=SimpleNamespace(host="127.0.0.1"))


def test_widen_apply_requires_confirmation_and_available_status(tmp_path):
    db = DBManager(f"sqlite:///{tmp_path / 'apply.sqlite'}")
    db.save_widen_factor_record({"symbol": "XRPUSDT", "horizon_h": 24,
        "computed_at": datetime.now(timezone.utc), "kind": "suggestion", "n": 600,
        "n_effective": 25, "k_active": 1.25, "status": "disponible",
        "disagreement_status": "disponible", "disagreement_threshold_suggested": 17.5})
    client = _api_client(db)
    with pytest.raises(HTTPException) as missing_confirm:
        volatility.apply_widen_factor(client, volatility.WidenApplyRequest(
            horizon_h=24, k=1.4, confirm=False))
    assert missing_confirm.value.status_code == 422
    success = volatility.apply_widen_factor(client, volatility.WidenApplyRequest(
        horizon_h=24, k=1.4, confirm=True))
    assert success["before"]["k_active"] == 1.25
    assert db.get_widen_active_values("XRPUSDT", 24, 1.25, 15)["k_active"] == 1.4
    threshold = volatility.apply_widen_factor(client, volatility.WidenApplyRequest(
        horizon_h=24, disagreement_pct=17.5, confirm=True))
    assert threshold["after"]["disagreement_pct_active"] == 17.5
    audit = db.get_widen_factor_audit_history("XRPUSDT", 24, 1)
    assert audit[-1]["kind"] == "apply"
    assert audit[-1]["audit_actor"].startswith("api-client:")
    db.engine.dispose()


def test_widen_apply_rejects_accumulating_and_bad_range(tmp_path):
    db = DBManager(f"sqlite:///{tmp_path / 'apply-guard.sqlite'}")
    db.save_widen_factor_record({"symbol": "XRPUSDT", "horizon_h": 1,
        "computed_at": datetime.now(timezone.utc), "kind": "suggestion", "n": 3,
        "n_effective": 3, "k_active": 1.25, "status": "acumulando",
        "disagreement_status": "acumulando"})
    client = _api_client(db)
    with pytest.raises(HTTPException) as accumulating:
        volatility.apply_widen_factor(client, volatility.WidenApplyRequest(
            horizon_h=1, k=1.4, confirm=True))
    with pytest.raises(HTTPException) as bad_range:
        volatility.apply_widen_factor(client, volatility.WidenApplyRequest(
            horizon_h=1, k=2.1, confirm=True))
    assert accumulating.value.status_code == 409
    assert bad_range.value.status_code == 422
    db.save_widen_factor_record({"symbol": "XRPUSDT", "horizon_h": 1,
        "computed_at": datetime.now(timezone.utc), "kind": "suggestion", "n": 480,
        "n_effective": 480, "k_active": 1.25, "status": "disponible", "k_raw": .85,
        "disagreement_status": "acumulando"})
    with pytest.raises(HTTPException) as overestimated:
        volatility.apply_widen_factor(client, volatility.WidenApplyRequest(
            horizon_h=1, k=1.4, confirm=True))
    assert overestimated.value.status_code == 409
    db.engine.dispose()


def test_auto_mode_defaults_off_and_get_returns_factor_payload(tmp_path):
    assert VOL_WIDEN_AUTO is False
    db = DBManager(f"sqlite:///{tmp_path / 'get.sqlite'}")
    client = _api_client(db)
    payload = volatility.get_widen_factor(client, symbol="XRPUSDT")
    assert payload["auto_enabled"] is False
    assert len(payload["horizons"]) == 4
    assert payload["horizons"][0]["status"] == "acumulando"
    assert payload["horizons"][0]["k_active"] == 1.25
    with db.engine.connect() as conn:
        settings_count = conn.execute(
            db.vol_widen_suggestions.select().where(db.vol_widen_suggestions.c.kind == "settings")
        ).all()
    assert len(settings_count) == 4
    db.engine.dispose()


def test_daily_calculation_waits_for_24_new_mature_champion_results(tmp_path, monkeypatch):
    db = DBManager(f"sqlite:///{tmp_path / 'daily.sqlite'}")
    now = datetime(2026, 10, 5, 12, tzinfo=timezone.utc)
    names = ("GBM", "HAR", "NexoHAR")
    rows = _rows(24, now)
    for row in rows:
        row["model_name"] = names[0] if row["model_name"] == "A" else names[1] if row["model_name"] == "B" else names[2]
        row["is_champion"] = row["model_name"] == "GBM"
    monkeypatch.setattr(widen_factor_loop, "_val_report", lambda _h: {
        "eligible_models": list(names), "weights": {"GBM": .34, "HAR": .33, "NexoHAR": .33},
        "calibration_models": {"GBM": {"calibrated": {"mse_log": .01, "bias_log": 0.0}}},
    })
    monkeypatch.setattr(db, "get_widen_factor_rows", lambda *_a: rows[:69])
    new_count = {"value": 23}
    monkeypatch.setattr(db, "count_new_widen_verifications", lambda *_a: new_count["value"])
    assert widen_factor_loop.compute_horizon(db, 1, now=now) is None
    new_count["value"] = 24
    monkeypatch.setattr(db, "get_widen_factor_rows", lambda *_a: rows[:72])
    result = widen_factor_loop.compute_horizon(db, 1, now=now)
    assert result is not None
    assert result["n"] == 24
    assert db.get_latest_widen_factor("XRPUSDT", 1)["kind"] == "suggestion"
    db.engine.dispose()


def test_auto_apply_requires_four_stable_available_suggestions_and_limits_step():
    from scheduler.widen_factor_loop import _maybe_auto_apply

    class FakeDB:
        def __init__(self):
            self.saved = []
        def get_widen_factor_history(self, *_a, **_k):
            return [{"status": "disponible", "k_stress_smoothed": value}
                    for value in (1.40, 1.42, 1.45, 1.43)]
        def get_widen_factor_audit_history(self, *_a, **_k):
            return []
        def save_widen_factor_record(self, row):
            self.saved.append(row)

    db = FakeDB()
    now = datetime(2026, 10, 5, 12, tzinfo=timezone.utc)
    active = {"k_active": 1.25, "disagreement_pct_active": 15.0}
    suggestion = {"status": "disponible", "k_stress_smoothed": 1.43, "n": 100, "n_effective": 25}
    assert _maybe_auto_apply(db, 4, suggestion, now, active) is True
    assert db.saved[-1]["k_active"] == pytest.approx(1.35)
    overestimate = {**suggestion, "k_raw": .92}
    blocked = FakeDB()
    assert _maybe_auto_apply(blocked, 4, overestimate, now, active) is False
    assert blocked.saved == []
    assert json.loads(db.saved[-1]["audit_before"])["k_active"] == 1.25
    unstable = FakeDB()
    unstable.get_widen_factor_history = lambda *_a, **_k: [
        {"status": "disponible", "k_stress_smoothed": v} for v in (1.2, 1.4, 1.2, 1.4)]
    assert _maybe_auto_apply(unstable, 4, suggestion, now, active) is False
    assert unstable.saved == []
    recent = FakeDB()
    recent.get_widen_factor_audit_history = lambda *_a, **_k: [
        {"kind": "auto_apply", "computed_at": now - timedelta(days=6)}]
    assert _maybe_auto_apply(recent, 4, suggestion, now, active) is False
    assert recent.saved == []
    fewer = FakeDB()
    fewer.get_widen_factor_history = lambda *_a, **_k: [
        {"status": "disponible", "k_stress_smoothed": v} for v in (1.40, 1.42, 1.43)]
    assert _maybe_auto_apply(fewer, 4, suggestion, now, active) is False
    assert fewer.saved == []
    boundary = FakeDB()
    boundary.get_widen_factor_audit_history = lambda *_a, **_k: [
        {"kind": "auto_apply", "computed_at": now - timedelta(days=7)}]
    assert _maybe_auto_apply(boundary, 4, suggestion, now, active) is True
    assert boundary.saved[-1]["k_active"] == pytest.approx(1.35)
