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
        conn.execute(db.predictions.insert().values(prediction_id='p1', symbol='XRPUSDT', interval='1h',
            model_name='model_a', predicted_at=datetime.now(timezone.utc),
            verify_at=datetime.now(timezone.utc), probability_up=.6, signal='UP', confidence='high',
            price_at_prediction=1.0))
    before = db.engine.connect().execute(db.meta_learning_switch_events.select()).all()
    response = client.get('/api/intelligence', headers={'X-API-Token':'intelligence-secret'})
    assert response.status_code == 200
    payload = response.json()
    assert payload['data']['candles']['available'] is False
    assert payload['data']['direction_predictions'][0]['count'] == 1
    assert payload['switches']['RAPIDO']['readiness']['status'] == 'gris'
    assert payload['switches']['RAPIDO']['enabled'] is False
    assert payload['signals']['touch_buy_to_sell']['sample_coverage'] == 0
    assert payload['signals']['touch_buy_to_sell']['coverage_pct'] == 0
    after = db.engine.connect().execute(db.meta_learning_switch_events.select()).all()
    assert before == after == []
