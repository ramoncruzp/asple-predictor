import json

from scripts import grid_ctl
from tests.test_grid_ctl_open import _args, _context


def test_open_dry_run_accepts_compound_params_as_json_boolean_and_numbers(monkeypatch, capsys):
    monkeypatch.setattr(grid_ctl, "build_context", _context)
    args = _args("--dry-run", "--param", "compound_enabled=true",
                 "--param", "compound_ratio=0.5", "--param", "compound_max_growth_pct=75")
    assert grid_ctl.main(args) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["params"]["compound_enabled"] is True
    assert result["params"]["compound_ratio"] == 0.5
    assert result["params"]["compound_max_growth_pct"] == 75


def test_open_dry_run_rejects_invalid_compound_param_ranges(monkeypatch, capsys):
    monkeypatch.setattr(grid_ctl, "build_context", _context)
    for argument in ("compound_ratio=0", "compound_ratio=1.01", "compound_max_growth_pct=0"):
        assert grid_ctl.main(_args("--dry-run", "--param", argument)) == 1
        assert "compound" in capsys.readouterr().err


def test_status_reports_cell_compound_and_grid_effective_capital():
    class StatusDB:
        def list_grids_by_status(self, statuses):
            if "ACTIVE" in statuses:
                return [{"id": 44, "symbol": "XRPUSDT", "status": "ACTIVE",
                         "strategy": "smart", "params": {"compound_enabled": True}}]
            return []

        def get_grid_levels(self, grid_id):
            return [
                {"grid_id": grid_id, "level_idx": 0, "capital": 125.0,
                 "capital_base": 100.0, "capital_compound": 25.0},
                {"grid_id": grid_id, "level_idx": 1, "capital": 100.0,
                 "capital_base": 100.0, "capital_compound": 0.0},
            ]

        def list_grid_snapshots(self, grid_id, limit):
            return []

        def get_last_monitor_run(self):
            return None

    grid = grid_ctl._status({"db": StatusDB()})["grids"][0]
    assert grid["compound_total"] == 25.0
    assert grid["capital_effective"] == 225.0
    assert grid["levels"][0]["capital_base"] == 100.0
    assert grid["levels"][0]["capital_compound"] == 25.0
