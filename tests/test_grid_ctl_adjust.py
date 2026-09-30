import json

from scripts import grid_ctl
from tests.test_grid_ctl_open import _context


def test_adjust_dry_run_returns_plan_without_orders(monkeypatch, capsys):
    context = _context()
    grid = context["engine"].create_grid("XRPUSDT", 90, 110, 5, capital=1000, strategy="smart")
    before = len(context["exchange"].create_calls)
    monkeypatch.setattr(grid_ctl, "build_context", lambda: context)
    assert grid_ctl.main(["adjust", "--grid-id", str(grid["id"]), "--low", "95", "--high", "105",
                          "--dry-run"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["ok"] and result["plan"]["n_levels"] == 5
    assert len(context["exchange"].create_calls) == before


def test_adjust_live_requires_yes_and_testnet(monkeypatch, capsys):
    monkeypatch.setattr(grid_ctl, "build_context", lambda: (_ for _ in ()).throw(AssertionError("built")))
    assert grid_ctl.main(["adjust", "--grid-id", "1", "--low", "95", "--high", "105"]) == 2
    assert "requiere --yes" in capsys.readouterr().err
    context = _context("development")
    monkeypatch.setattr(grid_ctl, "build_context", lambda: context)
    assert grid_ctl.main(["adjust", "--grid-id", "1", "--low", "95", "--high", "105", "--yes"]) == 1
    assert "testnet" in capsys.readouterr().err
