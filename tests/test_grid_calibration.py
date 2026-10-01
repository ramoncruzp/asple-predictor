from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

import numpy as np

from database.db_manager import DBManager
from grid.sim.calibration import (block_bootstrap_ci, calibrate, decision_rule, generate_candidates,
                                 make_folds, _select)
from grid.sim.data import CandleData


def _candles(days=151):
    stamps = np.arange(1_700_000_000, 1_700_000_000 + days * 86400, 300, dtype=np.int64)
    close = np.ones(len(stamps))
    return CandleData(stamps, close, close, close, close, 0)


def test_walk_forward_test_begins_after_train_and_windows_step_30d():
    folds = make_folds(_candles())
    assert len(folds) == 2
    for fold in folds:
        assert fold["train"].stop == fold["test"].start
        assert fold["train_end"] == fold["test_start"]
        assert fold["test_end"] - fold["test_start"] == 30 * 86400
    assert folds[1]["train_start"] - folds[0]["train_start"] == 30 * 86400


def test_defaults_are_candidate_zero_and_seed_repeats():
    first, invalid = generate_candidates(42, 20, 10)
    again, _ = generate_candidates(42, 20, 10)
    assert first == again and first[0]["stop_loss_pct"] == 5.0
    assert len(first) == 20 and invalid >= 0


def test_drawdown_constraint_and_tie_breaking():
    scores = [{"pnl_pct": 20, "dd_pct": 16}, {"pnl_pct": 4, "dd_pct": 10},
              {"pnl_pct": 4, "dd_pct": 10}]
    assert _select(scores) == 1
    assert _select([{"pnl_pct": 1, "dd_pct": 20}, {"pnl_pct": 9, "dd_pct": 18}]) == 1


def test_block_bootstrap_positive_and_symmetric_intervals():
    positive = block_bootstrap_ci([2.0] * 12, seed=12)
    symmetric = block_bootstrap_ci([-1, 1] * 6, seed=12)
    clustered = block_bootstrap_ci([-1.0] * 10 + [1.0] * 10, seed=4)
    assert positive[0] > 0
    assert symmetric[0] <= 0 <= symmetric[1]
    assert clustered[1] - clustered[0] > 1.2


def test_decision_rule_enforces_all_three_gates():
    good = (.7, [0.1, 2], 10, 10)
    assert decision_rule(*good) == "validated_walk_forward"
    assert decision_rule(.59, [0.1, 2], 10, 10) == "defaults_kept"
    assert decision_rule(.7, [-.1, 2], 10, 10) == "defaults_kept"
    assert decision_rule(.7, [.1, 2], 13, 10) == "defaults_kept"


def test_old_sqlite_migration_is_idempotent_and_calibration_attaches(tmp_path):
    path = tmp_path / "old.sqlite"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE grids (id INTEGER PRIMARY KEY, symbol VARCHAR NOT NULL, status VARCHAR NOT NULL)")
    con.commit()
    con.close()
    db = DBManager(f"sqlite:///{path}")
    db._migrate_grid_columns()
    cols = {r["name"] for r in db.engine.connect().exec_driver_sql("PRAGMA table_info(grids)").mappings()}
    assert "calibration_id" in cols
    calibration_id = db.save_grid_calibration({"symbol": "XRPUSDT", "data_start": "a", "data_end": "b",
        "method": "test", "seed": 1, "params": {"stop_loss_pct": 5}, "metrics": {},
        "verdict": "defaults_kept", "data_sha256": "0" * 64, "notes": "test"})
    assert db.get_grid_calibration(calibration_id)["verdict"] == "defaults_kept"


def test_invalid_candidates_are_discarded_and_counted(monkeypatch):
    import grid.sim.calibration as calibration
    original = calibration.validate_params
    def reject(candidate, n):
        if candidate and candidate.get("pause_enter_prob") != .1:
            raise ValueError("synthetic invalid combination")
        return original(candidate, n)
    monkeypatch.setattr(calibration, "validate_params", reject)
    candidates, invalid = calibration.generate_candidates(9, 10, 10)
    assert candidates[0]["stop_loss_pct"] == 5.0
    assert invalid > 0


def test_flat_synthetic_history_keeps_defaults_and_worker_results_match():
    candles = _candles(120)
    one = calibrate(candles, n=10, capital=100, width_pct=9, seed=42,
                    candidate_count=3, workers=1, csv_hash="a" * 64)
    two = calibrate(candles, n=10, capital=100, width_pct=9, seed=42,
                    candidate_count=3, workers=2, csv_hash="a" * 64)
    assert one["verdict"] == two["verdict"] == "defaults_kept"
    stable_one = [{k: v for k, v in row.items() if k != "duration_s"} for row in one["folds"]]
    stable_two = [{k: v for k, v in row.items() if k != "duration_s"} for row in two["folds"]]
    assert stable_one == stable_two
    assert one["params"] == two["params"] and one["candidates"] == two["candidates"]


def test_grid_open_calibration_rejection_acceptance_and_explicit_precedence(monkeypatch, capsys):
    from scripts import grid_ctl
    from tests.test_grid_ctl_open import _context, _args
    context = _context()
    db = context["db"]
    calibration = {"id": 17, "verdict": "validated_walk_forward",
                   "params": {"stop_loss_pct": 7.0}, "data_end": "2026-01-01"}
    db.get_latest_grid_calibration = lambda: calibration
    db.get_grid_calibration = lambda cid: {**calibration, "id": cid, "verdict": "defaults_kept"}
    monkeypatch.setattr(grid_ctl, "build_context", lambda: context)
    assert grid_ctl.main(_args("--dry-run", "--calibration", "latest", "--param", "stop_loss_pct=9")) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["calibrated"] is True and out["calibration_id"] == 17
    assert out["params"]["stop_loss_pct"] == 9
    assert grid_ctl.main(_args("--dry-run", "--calibration", "18")) == 1
    assert "defaults_kept" in capsys.readouterr().err


def test_calibrate_command_persists_defaults_kept(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from scripts import grid_sim
    csv = tmp_path / "input.csv"
    csv.write_text("timestamp,open,high,low,close\n", encoding="utf-8")
    saved = []
    class FakeDB:
        def save_grid_calibration(self, record):
            saved.append(record)
            return 81
    monkeypatch.setattr(grid_sim, "load_candles", lambda *a, **k: object())
    monkeypatch.setattr(grid_sim, "calibrate", lambda *a, **k: {
        "data_start": 1, "data_end": 2, "method": "walk_forward_90d_30d_step30d",
        "params": {}, "metrics": {}, "verdict": "defaults_kept", "seed": 42,
        "folds": [], "candidates": [], "data_sha256": "x"})
    monkeypatch.setattr(grid_sim, "Settings", lambda: SimpleNamespace(database_url="sqlite://"))
    monkeypatch.setattr(grid_sim, "DBManager", lambda url: FakeDB())
    monkeypatch.setattr(grid_sim, "ROOT", tmp_path)
    assert grid_sim.main(["calibrate", "--csv", str(csv), "--candidates", "1"]) == 0
    assert len(saved) == 1 and saved[0]["verdict"] == "defaults_kept"


def test_status_exposes_validated_calibration_reference():
    from scripts import grid_ctl
    class StatusDB:
        def list_grids_by_status(self, statuses):
            if "ACTIVE" in statuses:
                return [{"id": 9, "symbol": "XRPUSDT", "status": "ACTIVE", "calibration_id": 3,
                         "params": {}}]
            return []
        def get_grid_calibration(self, calibration_id):
            return {"id": 3, "verdict": "validated_walk_forward", "data_end": "2026-01-01"}
        def get_grid_levels(self, grid_id): return []
        def list_grid_snapshots(self, grid_id, limit): return []
        def get_last_monitor_run(self): return None
    grid = grid_ctl._status({"db": StatusDB()})["grids"][0]
    assert grid["calibrated"] is True
    assert grid["calibration"] == {"id": 3, "verdict": "validated_walk_forward", "data_end": "2026-01-01"}

