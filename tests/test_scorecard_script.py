import sqlite3
from types import SimpleNamespace

import numpy as np
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routes import intelligence
from data.market_store import CandleStore
from scripts.marcador_pronosticos import main


def test_scorecard_script_and_api_with_three_synthetic_200_day_symbols(tmp_path):
    market_db = tmp_path / "market_data.db"
    store = CandleStore(market_db)
    n = 200 * 288
    stamps = 1_700_000_100 + np.arange(n, dtype=np.int64) * 300
    for symbol_index, symbol in enumerate(("SYN1USDT", "SYN2USDT", "SYN3USDT")):
        rng = np.random.default_rng(700 + symbol_index)
        close = 100 * np.exp(np.cumsum(rng.normal(0, .001, n)))
        rows = [{"open_ts": int(stamps[i]), "open": float(close[i]),
            "high": float(close[i]*1.001), "low": float(close[i]*.999),
            "close": float(close[i])} for i in range(n)]
        store.upsert(symbol, "5m", rows, "synthetic", role="datos")

    report_dir = tmp_path / "reports"
    assert main(["--db", str(market_db), "--registro-db", str(tmp_path / "missing-registry.db"),
        "--symbols", "SYN1USDT", "SYN2USDT", "SYN3USDT", "--horizontes", "24",
        "--out", str(report_dir)]) == 0
    reports = list(report_dir.glob("marcador_*.md"))
    assert len(reports) == 1
    report = reports[0].read_text(encoding="utf-8")
    for section in ("Resumen ejecutivo (5 líneas)", "Veredictos agregados y por moneda",
        "Factores de calibración", "Ruptura por k", "Régimen, dirección y correlación mensual"):
        assert section in report
    with sqlite3.connect(market_db) as conn:
        run = conn.execute("SELECT id,status FROM scorecard_runs WHERE status='completed'").fetchone()
        symbols = {row[0] for row in conn.execute(
            "SELECT DISTINCT symbol FROM scorecard_verdicts WHERE run_id=? AND symbol IS NOT NULL", (run[0],))}
    assert run and symbols == {"SYN1USDT", "SYN2USDT", "SYN3USDT"}

    app = FastAPI()
    app.include_router(intelligence.router, prefix="/api/intelligence")
    app.state.settings = SimpleNamespace(grid_api_token="scorecard-test")
    app.state.market_data_path = market_db
    response = TestClient(app).get("/api/intelligence/scorecard", headers={"X-API-Token": "scorecard-test"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["run"]["status"] == "completed"
    assert any(item["symbol"] is None for item in payload["verdicts"])
    assert {item["symbol"] for item in payload["verdicts"] if item["symbol"]} == symbols
    assert "correlation_last_complete_month" in payload["summary"]["diagnostics"]
