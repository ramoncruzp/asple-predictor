from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "estudios_meta.py"
spec = importlib.util.spec_from_file_location("estudios_meta", SCRIPT)
studies = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = studies
spec.loader.exec_module(studies)


def test_direction_calibration_uses_read_only_join_and_reports_expected_metrics(tmp_path):
    db = tmp_path / "synthetic.sqlite"
    with sqlite3.connect(db) as conn:
        conn.executescript("""
            CREATE TABLE predictions(prediction_id TEXT, symbol TEXT, model_name TEXT, interval TEXT,
                probability_up REAL, predicted_at TEXT);
            CREATE TABLE outcomes(prediction_id TEXT, actual_direction TEXT, price_change_pct REAL,
                was_correct INTEGER, verified_at TEXT);
        """)
        for index, (probability, actual) in enumerate(((.9, "DOWN"), (.8, "DOWN"), (.2, "UP"), (.1, "UP"))):
            conn.execute("INSERT INTO predictions VALUES(?,?,?,?,?,?)",
                (f"p{index}", "XRPUSDT", "model_a_down", "1h", probability, "2026-10-27 00:00:00"))
            conn.execute("INSERT INTO outcomes VALUES(?,?,?,?,?)",
                (f"p{index}", actual, -1 if actual == "DOWN" else 1, 1, "2026-10-27 01:00:00"))
    before = db.read_bytes()
    rows = studies.calibrate_direction(db, days=3650)
    assert len(rows) == 1
    row = rows[0]
    assert row["symbol"] == "XRPUSDT" and row["n"] == 4
    assert row["brier"] == pytest.approx(.025)
    assert row["accuracy_threshold_0_60"] == 1.0
    assert row["auc"] == 1.0
    assert len(row["deciles"]) == 4
    assert db.read_bytes() == before
    with studies._connect_read_only(db) as conn:
        try:
            conn.execute("DELETE FROM predictions")
        except sqlite3.OperationalError:
            pass
        else:
            raise AssertionError("la conexión permitió escritura")


def test_persistence_study_marks_stateful_policy_unavailable_instead_of_claiming_results():
    rows = studies.persistence_study("1,2", "XRPUSDT,ZROUSDT", 180, "RAPIDO")
    assert len(rows) == 4
    assert {row["status"] for row in rows} == {"not_modeled"}
    assert all("conserve \u00f3rdenes e inventario" in row["reason"] for row in rows)


def test_study_json_and_csv_writer(tmp_path):
    payload = studies._write([{"status": "ok", "n": 3}], tmp_path, "fixture", {"read_only": True})
    assert payload["metadata"]["read_only"] is True
    assert json.loads((tmp_path / "fixture.json").read_text(encoding="utf-8"))["rows"][0]["n"] == 3
    assert (tmp_path / "fixture.csv").read_text(encoding="utf-8").splitlines()[0] == "status,n"


def test_grid_calibration_reads_database_and_uses_same_geometry_without_network(tmp_path, monkeypatch):
    db = tmp_path / "grid.sqlite"
    with sqlite3.connect(db) as conn:
        conn.executescript("""
            CREATE TABLE grids(id INTEGER,symbol TEXT,strategy TEXT,range_low REAL,range_high REAL,
                n_levels INTEGER,capital_total REAL,params TEXT,created_at TEXT);
            CREATE TABLE grid_levels(grid_id INTEGER,cycles_completed INTEGER,pnl REAL);
            CREATE TABLE grid_events(grid_id INTEGER,event_type TEXT);
        """)
        conn.execute("INSERT INTO grids VALUES(13,'JUPUSDT','smart',.25,.32,6,70,'{}','2026-10-25 00:00:00')")
        conn.execute("INSERT INTO grid_levels VALUES(13,4,.8)")
        conn.executemany("INSERT INTO grid_events VALUES(13,?)",[("BUY_FILLED",),("SELL_FILLED",)])
    import data.binance_client as client_module
    import data.exchange_filters as filters_module
    import grid.sim.runner as runner_module
    import scripts.medir_orden_intravela as measurement
    from grid.sim.data import CandleData

    class PublicClient:
        def get_symbol_info(self, _symbol): return {"symbol":"JUPUSDT"}
        def get_klines(self, **kwargs): return [[kwargs["startTime"],"1","1","1","1"]]
    class BinanceStub:
        def __init__(self,*_args): self.client=PublicClient()
        def get_historical_klines(self,*_args,**_kwargs): return object()
        @staticmethod
        def _request_with_retries(fn,**_kwargs): return fn()
        @staticmethod
        def _klines_to_dataframe(_rows): return object()
    monkeypatch.setattr(client_module,"BinanceClient",BinanceStub)
    monkeypatch.setattr(filters_module.SymbolFilters,"from_symbol_info",staticmethod(lambda _info: object()))
    def candle_stub(_frame, interval):
        step=interval
        return CandleData([0,step],[1,1],[1,1],[1,1],[1,1],0)
    monkeypatch.setattr(measurement,"_from_frame",candle_stub)
    monkeypatch.setattr(runner_module,"run_simulation",lambda *_a,**_k:{"metrics":{"cycles":2,"pnl_total_net_usdt":.4,"buys":3,"sells":2}})
    before=db.read_bytes()
    rows=studies.calibrate_grid(13,db)
    assert rows[0]["geometry_same"] is True and rows[0]["n_levels"] == 6
    assert rows[0]["real_cycles"] == 4 and rows[0]["real_buy_fills"] == 1
    assert rows[0]["sim_cycles_5m"] == rows[0]["sim_cycles_1m"] == 2
    assert db.read_bytes()==before
