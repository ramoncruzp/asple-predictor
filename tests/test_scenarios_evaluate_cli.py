from scripts import escenarios_evaluar


def test_objective_is_printed_in_alternatives_without_fee_comparison(monkeypatch, capsys):
    result = {"pieces": [{"piece": {"symbol": "XRPUSDT"}, "metrics": {
        "pnl_mean_usdt": 1.0, "cvar_5_usdt": -2.0, "p_target": .25,
        "p_loss_ci95": [0.0, .01], "days_to_target_p50": 12},
        "fills_mean": 0, "aviso": None}],
        "smart_paired": [], "fee_pct": .1}
    monkeypatch.setattr(escenarios_evaluar, "evaluate_pieces", lambda *a, **k: result)
    monkeypatch.setattr(escenarios_evaluar, "_rss_mb", lambda: 1.0)
    code = escenarios_evaluar.main(["--db", "missing.db", "--perfil", "RAPIDO",
        "--capital", "100", "--meta", "3", "--pieza", "XRPUSDT:simple:70:n=6"])
    assert code == 0
    output = capsys.readouterr().out
    assert "OBJETIVO_V2" in output
