from __future__ import annotations

import json

from scripts import grid_ctl
from tests.test_grid_engine import create, make_engine


def _context():
    engine, db, exchange = make_engine()
    engine.settings.environment = "testnet"
    return {"settings": engine.settings, "db": db, "exchange": exchange, "engine": engine}


def test_open_dry_run_accepts_reserve_and_loan_params_and_uses_distributable_capital(monkeypatch, capsys):
    context = _context()
    monkeypatch.setattr(grid_ctl, "build_context", lambda: context)
    args = ["open", "--symbol", "XRPUSDT", "--low", "90", "--high", "110", "--n", "5",
            "--capital", "1000", "--strategy", "smart", "--dry-run",
            "--param", "loans_enabled=true", "--param", "reserve_pct=10"]
    assert grid_ctl.main(args) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["reserve"] == "100.0"
    assert result["distributable_capital"] == "900.0"
    assert result["params"]["loans_enabled"] is True


def test_status_and_loans_command_show_reserve_cell_loan_and_history(monkeypatch, capsys):
    context = _context()
    engine, db = context["engine"], context["db"]
    grid = create(engine, capital=1000, strategy="smart",
                  params={"loans_enabled": True, "reserve_pct": 10})
    cell = db.get_grid_levels(grid["id"])[1]
    db.update_level(grid["id"], int(cell["level_idx"]),
                    capital=float(cell["capital"] + 5), capital_loan=5)
    loan = db.create_grid_loan({
        "grid_id": grid["id"], "lender_idx": None,
        "borrower_idx": int(cell["level_idx"]), "amount": 5.0, "reserve_part": 5.0,
        "lender_cycles_at_open": 0, "borrower_cycles_at_open": 0,
        "plan": {"stage": "PREPARED"}, "details": {"test": True},
    })
    db.update_grid_loan(loan["id"], status="OPEN")
    monkeypatch.setattr(grid_ctl, "build_context", lambda: context)
    assert grid_ctl.main(["status"]) == 0
    status = json.loads(capsys.readouterr().out)
    shown = next(row for row in status["grids"] if int(row["id"]) == int(grid["id"]))
    assert shown["reserve"] == 100.0
    assert shown["open_loans"][0]["id"] == loan["id"]
    assert shown["levels"][1]["capital_loan"] == 5.0
    assert grid_ctl.main(["loans", "--grid-id", str(grid["id"])]) == 0
    history = json.loads(capsys.readouterr().out)
    assert history["loans"][0]["status"] == "OPEN"
