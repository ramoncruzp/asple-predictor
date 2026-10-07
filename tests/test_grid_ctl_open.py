from __future__ import annotations

import json
from types import SimpleNamespace

from database.db_manager import DBManager
from grid.engine import GridEngine
from tests.grid_fakes import FakeExchange
from tests.test_grid_engine import make_engine
from scripts import grid_ctl


def _context(environment="testnet"):
    engine, db, exchange = make_engine()
    settings = engine.settings
    settings.environment = environment
    return {"settings": settings, "db": db, "exchange": exchange, "engine": engine}


def _args(*extra):
    return ["open", "--symbol", "XRPUSDT", "--low", "90", "--high", "110", "--n", "5",
            "--capital", "1000", "--strategy", "smart", *extra]


def test_open_dry_run_reports_levels_params_and_calibrated_false(monkeypatch, capsys):
    monkeypatch.setattr(grid_ctl, "build_context", _context)
    assert grid_ctl.main(_args("--dry-run")) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["dry_run"] and result["calibrated"] is False
    assert len(result["levels"]) == 6
    assert result["range"] == {"low": "90", "high": "110"}
    assert result["params"]["horizon_h"] == 4


def test_open_shift_half_step_uses_exact_decimal_range(monkeypatch, capsys):
    monkeypatch.setattr(grid_ctl, "build_context", _context)
    assert grid_ctl.main(_args("--dry-run", "--shift-half-step")) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["range"] == {"low": "92", "high": "112"}


def test_open_live_requires_yes_unknown_param_and_non_testnet_rejected(monkeypatch, capsys):
    monkeypatch.setattr(grid_ctl, "build_context", _context)
    assert grid_ctl.main(_args()) == 1
    assert "requiere --yes" in capsys.readouterr().err
    assert grid_ctl.main(_args("--dry-run", "--param", "evil=1")) == 1
    assert "desconocido" in capsys.readouterr().err
    monkeypatch.setattr(grid_ctl, "build_context", lambda: _context("development"))
    assert grid_ctl.main(_args("--dry-run")) == 1
    assert "testnet" in capsys.readouterr().err


def test_open_dry_run_rejects_binance_minimum_margin(monkeypatch, capsys):
    monkeypatch.setattr(grid_ctl, "build_context", _context)
    args = ["open", "--symbol", "XRPUSDT", "--low", "90", "--high", "110", "--n", "5",
            "--capital", "20", "--strategy", "simple", "--dry-run"]
    assert grid_ctl.main(args) == 1
    assert "min_notional" in capsys.readouterr().err


def test_status_exposes_smart_params_snapshot_and_cell_stoploss_fields():
    class StatusDB:
        def list_grids_by_status(self, statuses):
            if "ACTIVE" in statuses:
                return [{"id": 44, "symbol": "XRPUSDT", "status": "ACTIVE",
                         "strategy": "smart", "params": {"horizon_h": 24}}]
            return []

        def get_grid_levels(self, grid_id):
            return [{"grid_id": grid_id, "level_idx": 0, "state": "SELL_OPEN",
                     "entry_price": 1.25, "stop_loss_pct": 5.0}]

        def list_grid_snapshots(self, grid_id, limit):
            return [{"level_idx": None, "break_prob": 0.12, "sigma_24h": 0.08,
                     "trapped_capital_pct": 33.0, "free_cells": 3}]

        def get_last_monitor_run(self):
            return None

    result = grid_ctl._status({"db": StatusDB()})
    grid = result["grids"][0]
    assert grid["calibrated"] is False and grid["params"]["horizon_h"] == 24
    assert (grid["break_prob"], grid["sigma_24h"], grid["trapped_capital_pct"], grid["free_cells"]) == (
        0.12, 0.08, 33.0, 3,
    )
    assert grid["levels"][0]["entry_price"] == 1.25
    assert grid["levels"][0]["stop_loss_pct"] == 5.0
