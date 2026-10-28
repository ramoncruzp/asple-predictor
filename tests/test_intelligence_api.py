import json
from datetime import datetime, timezone
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routes import intelligence
from database.db_manager import DBManager
from grid.meta_operator import MetaOperator


def test_readiness_reports_gray_yellow_failures_and_green_without_side_effects():
    from api.routes.intelligence import _ready

    def row(summary, profile='RAPIDO'):
        return {'profile': profile, 'summary_json': json.dumps(summary)}

    green = {'signals': {'volatility': {'effective_samples': 35, 'p_value_holm': .01,
        'weight': .30}}, 'validation_effective': 6, 'improves': True,
        'validation': {'model': {'log_loss': .4, 'brier': .12}},
        'baseline_validation': {'baseline': {'log_loss': .5, 'brier': .18}}}
    results = _ready([row({}, 'PRUDENTE'), row({**green, 'validation_effective': 0}, 'RAPIDO')])
    assert results['PRUDENTE']['status'] == 'gris'
    assert results['RAPIDO']['status'] == 'amarillo'
    assert not results['RAPIDO']['conditions']['validacion']
    for summary, failed in (
        ({**green, 'signals': {'volatility': {**green['signals']['volatility'], 'weight': .25}}}, 'significancia_y_cambio'),
        ({**green, 'validation_effective': 4}, 'validacion'),
        ({**green, 'improves': False}, 'mejora'),
    ):
        item = _ready([row(summary)])['RAPIDO']
        assert item['status'] == 'amarillo'
        assert item['conditions'][failed] is False
    assert _ready([row(green)])['RAPIDO']['status'] == 'verde'


def test_intelligence_api_is_token_protected_aggregated_and_read_only(tmp_path):
    db = DBManager(f"sqlite:///{tmp_path / 'intelligence.sqlite'}")
    settings = SimpleNamespace(grid_api_token='intelligence-secret', meta_mode_enabled=False)
    operator = MetaOperator(SimpleNamespace(), db, None, None, settings)
    app = FastAPI()
    app.include_router(intelligence.router, prefix='/api/intelligence')
    app.state.settings, app.state.db, app.state.meta_operator = settings, db, operator
    client = TestClient(app)
    assert client.get('/api/intelligence').status_code == 403
    with db.engine.begin() as conn:
        goal_id = conn.execute(db.meta_goals.insert().values(capital_initial=100, target_usdt=10,
            target_input_type='usdt', target_input_value=10, profile='RAPIDO', status='ACTIVE',
            environment='TESTNET', created_at=datetime.now(timezone.utc).replace(tzinfo=None))).inserted_primary_key[0]
        conn.execute(db.predictions.insert().values(prediction_id='p1', symbol='XRPUSDT', interval='1h',
            model_name='model_a', predicted_at=datetime.now(timezone.utc),
            verify_at=datetime.now(timezone.utc), probability_up=.6, signal='UP', confidence='high',
            price_at_prediction=1.0))
        conn.execute(db.outcomes.insert().values(prediction_id='p1', verified_at=datetime.now(timezone.utc),
            price_at_verification=1.1, price_change_pct=10, actual_direction='UP', was_correct=1))
        conn.execute(db.vol_forecasts.insert().values(symbol='XRPUSDT', horizon_h=24, model_name='vol_a',
            forecast_at=datetime.now(timezone.utc), made_at=datetime.now(timezone.utc),
            pred_logvol_raw=.02, pred_logvol_cal=.02, var_factor=1, is_champion=1,
            realized_logvol=.03, verified_at=datetime.now(timezone.utc)))
        conn.execute(db.meta_learning_runs.insert().values(started_at=datetime.now(timezone.utc),
            finished_at=datetime.now(timezone.utc), status='completed', summary_json=json.dumps({
                'candle_inventory_at': '2026-10-26T00:00:00+00:00',
                'candle_inventory': {'XRPUSDT': {'count': 12, 'interval_seconds': 300, 'gaps': 1}},
                'replay_windows_new': 8, 'replay_windows_cached': 4, 'cuts_processed': 2})))
        conn.execute(db.meta_signal_observations.insert().values(meta_id=goal_id, decision_id='d1',
            alternative_id='a1', symbol='XRPUSDT', piece_type='grid', cell_index=0,
            window_start='2026-10-01', horizon='72h', buy_relative=-.01, sell_relative=.01,
            stats_json=json.dumps({'touches': 2, 'sold_within_horizon': 1, 'sell_rate': .5}),
            created_at=datetime.now(timezone.utc).replace(tzinfo=None)))
        for index in range(2):
            details = {'decision_choices': {'real': {'decision': 'mantener',
                'alternative_id': f'a{index}', 'symbol': 'XRPUSDT', 'probability': .6}}}
            if index == 1:
                details.update({'ledger_schema_version': 2, 'plan_decision_id': 'plan-v2',
                    'valuation': {'status': 'DESCONOCIDO', 'symbol': 'XRPUSDT', 'reason': 'libro obsoleto'},
                    'valuation_unknown': {'symbol': 'XRPUSDT', 'reason': 'libro obsoleto', 'pieces': []}})
            conn.execute(db.meta_ledger.insert().values(meta_id=goal_id,
                ts=datetime.now(timezone.utc).replace(tzinfo=None), event_type='META_OPERATOR_REVIEW_DECISION',
                details=json.dumps(details)))
        conn.execute(db.meta_ledger.insert().values(meta_id=goal_id,
            ts=datetime.now(timezone.utc).replace(tzinfo=None), event_type="META_PLAN_EVALUATED",
            details=json.dumps({"ledger_schema_version": 3, "ledger_detail": "resumido",
                "alternatives_total": 17, "alternatives": [{}]})))
        bot_event_id = conn.execute(db.meta_ledger.insert().values(meta_id=goal_id,
            ts=datetime.now(timezone.utc).replace(tzinfo=None), event_type="META_BOT_TOUCH_SHADOW",
            details=json.dumps({"symbol":"XRPUSDT", "status":"proposed", "buy_price":.99,
                "sell_price":1.01, "buy_distance_pct":1.0, "expected_sell_rate":.4,
                "proposed_at":int(datetime.now(timezone.utc).timestamp())-90000}))).inserted_primary_key[0]
        conn.execute(db.meta_ledger.insert().values(meta_id=goal_id,
            ts=datetime.now(timezone.utc).replace(tzinfo=None), event_type="META_BOT_TOUCH_SHADOW_LABEL",
            details=json.dumps({"source_event_id":bot_event_id, "horizon_hours":24,
                "sell_after_buy":True, "expected_sell_rate":.4})))
    before = db.engine.connect().execute(db.meta_learning_switch_events.select()).all()
    response = client.get('/api/intelligence', headers={'X-API-Token':'intelligence-secret'})
    assert response.status_code == 200
    payload = response.json()
    assert payload['data']['candles']['available'] is False
    assert payload['data']['unknown_valuation_reviews']['last_24h'] == 1
    assert payload['data']['unknown_valuation_reviews']['last_7d'] == 1
    assert payload['data']['unknown_valuation_reviews']['reasons'][0]['reason'] == 'libro obsoleto'
    assert payload['signals']['direction']['direction_active_coverage'] == {
        'count': 0, 'total': 0, 'coverage_pct': 0, 'threshold_p_down': 0.60}
    assert payload['data']['direction_predictions'][0]['count'] == 1
    assert payload['switches']['RAPIDO']['readiness']['status'] == 'gris'
    assert payload['switches']['RAPIDO']['enabled'] is False
    assert payload['shadow_switches']['RAPIDO']['use_break_prob_v2'] is False
    assert payload['signals']['touch_buy_to_sell']['sample_coverage'] == 0
    assert payload['signals']['touch_buy_to_sell']['coverage_pct'] == 0
    assert payload['data']['meta_signal_counts'] == {'touch_buy_to_sell': 1}
    assert payload['data']['meta_signal_counts_by_symbol'] == [
        {'signal': 'touch_buy_to_sell', 'symbol': 'XRPUSDT', 'count': 1}]
    assert 'alternativas' in payload['data']['summary_text']
    assert '17 alternativas' in payload['data']['summary_text']
    assert payload['replay']['chart'] and len(payload['replay']['chart']) == 10
    assert 'direction' in payload['hits'] and 'volatility' in payload['hits']
    assert 'replay_calibration' in payload['hits']
    assert payload['hits']['centered_signal_comparison']['72h']['actual']['n'] == 0
    assert payload['data']['touch_bot_shadow']['proposals'][0]['labels']['24']['sell_after_buy'] is True
    assert payload['data']['touch_bot_shadow']['labels']['24']['n'] == 1
    direction_hit = payload['hits']['direction'][0]
    assert direction_hit['n'] == 1 and direction_hit['accuracy'] == 1
    assert direction_hit['few_samples'] is True and direction_hit['wilson95'][0] < 1 and direction_hit['wilson95'][1] <= 1
    assert payload['hits']['volatility'][0]['n'] == 1
    page = client.get(f'/api/intelligence?meta_id={goal_id}&page_size=10',
        headers={'X-API-Token':'intelligence-secret'}).json()['decisions']
    assert page['total'] == 3 and len(page['recent']) == 3
    review_events = [event for event in page['recent'] if event['event_type'] == 'META_OPERATOR_REVIEW_DECISION']
    assert len(review_events) == 2
    legacy, v2 = sorted(review_events, key=lambda event: event['details'].get('ledger_schema_version', 1))
    assert legacy['choices']['real']['symbol'] == v2['choices']['real']['symbol'] == 'XRPUSDT'
    assert legacy['details']['decision_choices']['real']['decision'] == 'mantener'
    assert v2['details']['ledger_schema_version'] == 2 and v2['details']['plan_decision_id'] == 'plan-v2'
    assert payload['replay']['last_run']['replay_windows_new'] == 8
    assert payload['replay']['last_run']['candle_inventory']['XRPUSDT']['gaps'] == 1
    after = db.engine.connect().execute(db.meta_learning_switch_events.select()).all()
    assert before == after == []
