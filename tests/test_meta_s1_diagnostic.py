from __future__ import annotations
import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
SCRIPT=ROOT/'scripts'/'verificar_s1_meta.py'
spec=importlib.util.spec_from_file_location('verificar_s1_meta',SCRIPT)
module=importlib.util.module_from_spec(spec); sys.modules[spec.name]=module; spec.loader.exec_module(module)


def test_s1_diagnostic_is_read_only_and_reports_synthetic_meta_state(tmp_path,capsys):
    path=tmp_path/'synthetic.sqlite'
    with sqlite3.connect(path) as conn:
        conn.executescript('''
        CREATE TABLE meta_goals(id INTEGER,profile TEXT,status TEXT,capital_initial REAL,target_usdt REAL,environment TEXT);
        CREATE TABLE meta_runs(id INTEGER,meta_id INTEGER,status TEXT,started_at TEXT,finished_at TEXT);
        CREATE TABLE meta_piece_links(id INTEGER,meta_id INTEGER,grid_id INTEGER,piece_type TEXT,released_at TEXT);
        CREATE TABLE grids(id INTEGER,symbol TEXT,strategy TEXT,status TEXT,capital_total REAL,range_low REAL,range_high REAL,n_levels INTEGER);
        CREATE TABLE meta_ledger(id INTEGER,meta_id INTEGER,ts TEXT,event_type TEXT,reason TEXT,grid_id INTEGER,details TEXT);
        CREATE TABLE meta_analysis_cache(cache_key TEXT,created_at TEXT);
        CREATE TABLE meta_learning_runs(id INTEGER,started_at TEXT,finished_at TEXT,status TEXT,summary_json TEXT);
        CREATE TABLE meta_learning_samples(id INTEGER,symbol TEXT,sample_at TEXT,source_kind TEXT,signals_json TEXT);
        ''')
        conn.execute("INSERT INTO meta_goals VALUES(1,'RAPIDO','ACTIVE',100,3,'TESTNET')")
        conn.execute("INSERT INTO grids VALUES(12,'XRPUSDT','smart','ACTIVE',70,.9,1.1,5)")
        conn.execute("INSERT INTO meta_piece_links VALUES(1,1,12,'grid',NULL)")
        now='2026-10-27 20:00:00'
        conn.execute("INSERT INTO meta_ledger VALUES(1,1,?,'META_PLAN_EVALUATED',NULL,NULL,?)",
            (now,json.dumps({'current_pieces':{'12':{'estimate':{'probability_replay':.5}}},
                'timings_s':{'scanner':1.2,'portfolio':2.3,'allocation_shadow':.1}})))
        conn.execute("INSERT INTO meta_ledger VALUES(2,1,?,'META_OPERATOR_REVIEW_DECISION','mantener',12,?)",
            (now,json.dumps({'decision':{'decision':'no_hacer_nada','reason':'sin mejora'},
                'valuation':{'status':'KNOWN','cash_usdt':'99.9'}})))
        conn.execute("INSERT INTO meta_ledger VALUES(3,1,?,'META_PIECES_BLOCKED','valuation_unknown',12,?)",
            (now,json.dumps({'reason':'valuation_unknown','symbols':{'XRPUSDT':{
                'status':'DESCONOCIDO','reason':'libro sin compradores'}}})))
        conn.execute("INSERT INTO meta_learning_runs VALUES(1,?,?, 'completed', '{}')",(now,now))
        conn.execute("INSERT INTO meta_learning_samples VALUES(1,'XRPUSDT',?,'live',?)",
            (now,json.dumps({'signals_used':[{'name':'direction','normalized':0.0},{'name':'touch_buy_to_sell','normalized':.4}]})))
    before=path.read_bytes()
    assert module.verify(path,hours=10000)==0
    output=capsys.readouterr().out
    assert 'mode=ro' in output and 'Meta #1' in output and 'XRPUSDT smart ACTIVE' in output
    assert 'probability_replay' in output and 'allocation_shadow' in output
    assert 'Bloqueos transitorios (24 h):' in output
    assert 'XRPUSDT: libro sin compradores: 1' in output
    assert 'direction XRPUSDT: n=1' in output and 'touch_buy_to_sell XRPUSDT: n=1' in output
    assert path.read_bytes()==before
    with module._connect(path) as conn:
        with __import__('pytest').raises(sqlite3.OperationalError): conn.execute("DELETE FROM meta_goals")
