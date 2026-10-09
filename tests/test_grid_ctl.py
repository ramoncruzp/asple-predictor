from __future__ import annotations

import json

import pytest

from scripts import grid_ctl


class FakeDB:
    def list_grids_by_status(self, statuses):
        rows = {"ACTIVE": [{"id": 7, "symbol": "XRPUSDT", "status": "ACTIVE"}],
                "HOLDING": [{"id": 8, "symbol": "XRPUSDT", "status": "HOLDING"}]}
        return rows.get(next(iter(statuses)), [])

    def get_grid_levels(self, grid_id):
        return [{"grid_id": grid_id, "level_idx": 0, "state": "SELL_OPEN"}]

    def get_last_monitor_run(self):
        return {"id": 3, "status": "OK"}


class FakeEngine:
    def close_grid(self, grid_id, mode):
        return {"grid_id": grid_id, "mode": mode, "status": "CLOSED"}


class FakeMonitor:
    def run_once(self, trigger):
        return {"trigger": trigger, "status": "OK"}


def test_grid_ctl_status_prints_grids_cells_repositories_and_last_run(monkeypatch, capsys):
    monkeypatch.setattr(grid_ctl, "build_context", lambda: {"db": FakeDB()})
    assert grid_ctl.main(["status"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert [grid["id"] for grid in payload["grids"]] == [7, 8]
    assert payload["grids"][0]["levels"][0]["state"] == "SELL_OPEN"
    assert payload["repositories"][0]["status"] == "HOLDING"
    assert payload["last_monitor_run"]["id"] == 3


def test_grid_ctl_close_liquidate_requires_yes_before_building_context(monkeypatch, capsys):
    monkeypatch.setattr(grid_ctl, "build_context", lambda: (_ for _ in ()).throw(AssertionError("built")))
    assert grid_ctl.main(["close", "7", "--mode", "liquidate"]) == 2
    assert "requiere --yes" in capsys.readouterr().err


def test_grid_ctl_close_with_yes_and_other_modes_call_engine(monkeypatch, capsys):
    engine = FakeEngine()
    monkeypatch.setattr(grid_ctl, "build_context", lambda: {"engine": engine})
    assert grid_ctl.main(["close", "7", "--mode", "liquidate", "--yes"]) == 0
    assert json.loads(capsys.readouterr().out)["mode"] == "liquidate"


def test_grid_ctl_run_once_calls_monitor_without_starting_scheduler(monkeypatch, capsys):
    monkeypatch.setattr(grid_ctl, "build_context", lambda: {"monitor": FakeMonitor()})
    assert grid_ctl.main(["run-once"]) == 0
    assert json.loads(capsys.readouterr().out) == {"trigger": "SCHEDULED", "status": "OK"}


def test_open_pair_cli_exposes_factor_choices(monkeypatch, capsys):
    with pytest.raises(SystemExit) as exit_info:
        grid_ctl.main(["open-pair", "--help"])
    assert exit_info.value.code == 0
    assert "--factor {loans,idle_shrink,capital_shrink}" in capsys.readouterr().out
