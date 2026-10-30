from scripts.escenarios_validar import _report_markdown


def test_history_validation_report_labels_scope_and_metrics():
    text = _report_markdown({"horizon_days": 20, "fee_pct": .1, "symbols": {
        "XRPUSDT": {"ventanas_candidatas": 4, "pieces": [{"piece": {"symbol": "XRPUSDT"},
            "variantes": {"real": {"n": 4, "pnl_medio_usdt": 1, "pnl_diario_usdt": .05,
                "pnl_p10_usdt": -1, "pnl_p50_usdt": 1, "pnl_p90_usdt": 3,
                "p_loss": .1, "p_target": .2, "ic95_pnl_medio": [-1, 2]},
                "sin_tendencia": {"n": 0}}}]}}})
    assert "no sustituye la aceptación walk-forward" in text
    assert "P(límite)" in text and "sin_tendencia" in text
