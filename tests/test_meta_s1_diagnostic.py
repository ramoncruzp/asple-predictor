from __future__ import annotations
import importlib.util
import json
import sqlite3
import sys
from datetime import datetime, timezone
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
        CREATE TABLE meta_analysis_cache(cache_key TEXT,cache_kind TEXT,value_json TEXT,created_at TEXT);
        CREATE TABLE meta_learning_runs(id INTEGER,started_at TEXT,finished_at TEXT,status TEXT,summary_json TEXT);
        CREATE TABLE meta_learning_samples(id INTEGER,symbol TEXT,sample_at TEXT,source_kind TEXT,signals_json TEXT,
            parameters_json TEXT,provenance_json TEXT);
        CREATE TABLE meta_learning_labels(id INTEGER,sample_id INTEGER,horizon TEXT,label_json TEXT,labeled_at TEXT);
        CREATE TABLE alert_state(key TEXT,value TEXT,updated_at TEXT);
        CREATE TABLE grid_intel_recommendations(id INTEGER,grid_id INTEGER,symbol TEXT,strategy TEXT,
            created_at TEXT,action TEXT,expected_gain_7d REAL,expected_gain_7d_baseline REAL,drawdown REAL,would_apply INTEGER);
        CREATE TABLE grid_intel_labels(id INTEGER,recommendation_id INTEGER,status TEXT,label_json TEXT,labeled_at TEXT);
        CREATE TABLE grid_events(id INTEGER,ts TEXT,event_type TEXT,details TEXT);
        ''')
        conn.execute("INSERT INTO alert_state VALUES(?,?,?)", ("meta_intelligence_readiness_snapshot",
            json.dumps({"RAPIDO:learned_weights":{"state":"Recolectando datos","n":12,
                "n_required":30,"estimated_ready_at":"2026-11-02T00:00:00+00:00"}}),
            "2026-10-28 12:00:00"))
        conn.execute("INSERT INTO meta_goals VALUES(1,'RAPIDO','ACTIVE',100,3,'TESTNET')")
        conn.execute("INSERT INTO grids VALUES(12,'XRPUSDT','smart','ACTIVE',70,.9,1.1,5)")
        conn.execute("INSERT INTO meta_piece_links VALUES(1,1,12,'grid',NULL)")
        now=datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
        conn.execute("INSERT INTO meta_ledger VALUES(1,1,?,'META_PLAN_EVALUATED',NULL,NULL,?)",
            (now,json.dumps({'current_pieces':{'12':{'estimate':{'probability_replay':.5}}},
                'timings_s':{'scanner':1.2,'portfolio':2.3,'allocation_shadow':.1,
                    'process_caches':{'analysis':{'rows':15,'kb_estimated':120.5}}}})))
        conn.execute("INSERT INTO meta_ledger VALUES(2,1,?,'META_OPERATOR_REVIEW_DECISION','mantener',12,?)",
            (now,json.dumps({'decision':{'decision':'no_hacer_nada','reason':'sin mejora'},
                'valuation':{'status':'KNOWN','cash_usdt':'99.9'}})))
        conn.execute("INSERT INTO meta_ledger VALUES(3,1,?,'META_PIECES_BLOCKED','valuation_unknown',12,?)",
            (now,json.dumps({'reason':'valuation_unknown','symbols':{'XRPUSDT':{
                'status':'DESCONOCIDO','reason':'libro sin compradores'}}})))
        conn.execute("INSERT INTO meta_ledger VALUES(4,1,?,'META_OPERATOR_ALLOCATION_SHADOW',NULL,12,?)",
            (now,json.dumps({'decision':'agregar','symbol':'XRPUSDT','piece_type':'individual_bot_grid','capital_usdt':12.5,'descartes':{'conteo_por_motivo':{'sin curva v3':2, 'sin readiness':5, 'limite por moneda':4, 'geometria invalida':1},
                'timings_s':{'total':2.5}}})))
        summary={'samples_failed':1,'labels_invalid_geometry':1,
            'failed_samples':[{'sample_id':7,'symbol':'XRPUSDT','error':'ValueError: fallo sintético'}]}
        conn.execute("INSERT INTO meta_learning_runs VALUES(1,?,?, 'completed', ?)",(now,now,json.dumps(summary)))
        conn.execute("INSERT INTO meta_learning_labels VALUES(1,7,'72h',?,?)",
            (json.dumps({'status':'invalid_geometry','reason':{'symbol':'XRPUSDT','tick_used':'0.0001'}}),now))
        conn.execute("INSERT INTO meta_learning_samples(id,symbol,sample_at,source_kind,signals_json,parameters_json,provenance_json) VALUES(1,'XRPUSDT',?,'live',?,'{}','{}')",
            (now,json.dumps({'break_prob_v2':{'value':.4,'normalized':.2},
                'signals_used':[{'name':'break_prob','normalized':.2}, {'name':'direction','normalized':0.0},
                {'name':'direction','normalized':None},{'name':'touch_buy_to_sell','normalized':.4}]})))
        conn.execute("INSERT INTO meta_analysis_cache VALUES('c1','replay',?,?)",
            (json.dumps({'window_results':[{'curve':{'curve_format_version':3}}]}),now))
        conn.execute("INSERT INTO meta_analysis_cache VALUES('c2','replay',?,?)",
            (json.dumps({'window_results':[{'curve':{'curve_format_version':2}}]}),now))
        recent=now
        conn.execute("INSERT INTO grid_intel_recommendations VALUES(1,99,'ADAUSDT','simple',?,'mantener',.2,.2,1,0)",(recent,))
        conn.execute("INSERT INTO grid_intel_labels VALUES(1,1,'mature',?,?)",
            (json.dumps({'gain_difference_usdt':.1}),recent))
        conn.execute("INSERT INTO grid_events VALUES(1,?,'GRID_INTEL_JOB_SUMMARY',?)",
            (recent,json.dumps({'status':'completed','duration_s':4.2,'included':[99],
                'excluded':[{'grid_id':12,'reason':'pieza ligada a Meta'}],'budget_exceeded':False})))
    before=path.read_bytes()
    assert module.verify(path,hours=10000)==0
    output=capsys.readouterr().out
    assert 'mode=ro' in output and 'Meta #1' in output and 'XRPUSDT smart ACTIVE' in output
    assert '¿Qué puedo encender?' in output
    assert 'RAPIDO:learned_weights: Recolectando datos · 12 / 30 · estimado: 2026-11-02T00:00:00+00:00' in output
    assert 'probability_replay' in output and 'allocation_shadow' in output
    assert 'Bloqueos transitorios (24 h):' in output
    assert 'XRPUSDT: libro sin compradores: 1' in output
    assert 'Asignación en sombra (24 h):' in output and 'descarte sin curva v3: 2' in output
    assert 'descarte sin readiness: 5' in output and 'descarte limite por moneda: 4' in output
    assert 'descarte geometria invalida: 1' not in output
    assert '"piece_type": "individual_bot_grid"' in output and '"capital_usdt": 12.5' in output
    assert 'formato 2: 1 filas' in output and 'formato 3: 1 filas' in output
    assert 'Cachés de proceso Meta' in output and 'analysis: filas=15; KB estimados=120.5' in output
    assert 'samples_failed' not in output and 'muestras fallidas: 1' in output
    assert 'etiquetas invalid_geometry desde la corrida: 1' in output
    assert 'muestras fallidas' in output and 'parameters_json:' in output
    assert 'direction XRPUSDT: n=2' in output and 'touch_buy_to_sell XRPUSDT: n=1' in output
    assert 'Ruptura v2:' in output and 'señal actual: n=1' in output and 'v2: n=1' in output
    assert 'Inteligencia de grids:' in output and 'recomendaciones 24 h: 1' in output
    assert 'último job: completed; duración=4.2 s; incluidos=1; excluidos=1' in output
    assert 'm\u00edn=0; mediana=0; m\u00e1x=0; distintos=1; nulos=1' in output
    assert len(output.splitlines()) <= 150
    assert path.read_bytes()==before
    assert module.verify(path,hours=10000,detail=True)==0
    detailed=capsys.readouterr().out
    assert 'valores normalizados completos: [0.0]' in detailed
    with module._connect(path) as conn:
        with __import__('pytest').raises(sqlite3.OperationalError): conn.execute("DELETE FROM meta_goals")
