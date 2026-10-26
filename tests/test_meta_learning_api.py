from datetime import datetime, timezone
import json
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routes import metas
from database.db_manager import DBManager
from grid.meta_operator import MetaOperator, signal_adjusted_probability


def _operator(tmp_path):
    db = DBManager(f"sqlite:///{tmp_path / 'meta-learning-api.sqlite'}")
    settings = SimpleNamespace(grid_api_token='learning-secret')
    core = SimpleNamespace()
    operator = MetaOperator(core, db, None, None, settings)
    app = FastAPI()
    app.include_router(metas.router, prefix='/api/metas')
    app.state.meta_operator = operator
    app.state.meta_core = core
    app.state.settings = settings
    return db, operator, TestClient(app)


def test_effective_weights_choose_live_then_historical_then_prior(tmp_path):
    db, operator, _client = _operator(tmp_path)
    now = datetime.now(timezone.utc)
    with db.engine.begin() as conn:
        for source, weights, signal_samples in (
            ('live', {'volatility': .4, 'direction': .3, 'touch_buy_to_sell': .4},
             {'volatility': 30, 'direction': 10, 'touch_buy_to_sell': 10}),
            ('historical_reconstructed', {'volatility': .6, 'direction': -.2, 'touch_buy_to_sell': .2},
             {'volatility': 40, 'direction': 35, 'touch_buy_to_sell': 35})):
            conn.execute(db.meta_learning_weight_versions.insert().values(
                created_at=now, profile='RAPIDO', source_kind=source, status='updated',
                weights_json=json.dumps(weights), summary_json=json.dumps({'signals': {
                    name: {'effective_samples': count, 'p_value_holm': .01, 'reason': 'aceptado'}
                    for name, count in signal_samples.items()}})))
    weights, sources, _details, _ids = operator._effective_learned_weights('RAPIDO')
    assert weights['volatility'] == .4 and sources['volatility'] == 'live'
    assert weights['direction'] == -.2 and sources['direction'] == 'historical_reconstructed'
    assert weights['scanner'] == .25 and sources['scanner'] == 'sin evidencia'
    assert weights['touch_buy_to_sell'] == .2 and sources['touch_buy_to_sell'] == 'historical_reconstructed'


def test_switch_requires_confirmation_records_actor_and_does_not_auto_enable(tmp_path):
    db, operator, _client = _operator(tmp_path)
    with db.engine.connect() as conn:
        row = conn.execute(db.meta_profile_settings.select().where(
            db.meta_profile_settings.c.profile == 'RAPIDO')).mappings().one()
    assert json.loads(row['settings'])['use_learned_weights'] is False
    try:
        operator.set_learning_switch('RAPIDO', True, confirm=False, actor='Ramón')
    except ValueError:
        pass
    else:
        raise AssertionError('switch accepted without explicit confirmation')
    result = operator.set_learning_switch('RAPIDO', True, confirm=True, actor='Ramón')
    assert result['enabled'] is True
    status = operator.learning_status('RAPIDO')
    assert status['enabled'] is True
    assert status['last_switch']['actor'] == 'Ramón'
    assert status['last_switch']['weight_version_id'] is None


def test_learning_api_requires_token_and_switch_endpoint_confirmation(tmp_path):
    _db, _operator_obj, client = _operator(tmp_path)
    assert client.get('/api/metas/learning?profile=RAPIDO').status_code == 403
    auth = {'X-API-Token': 'learning-secret'}
    assert client.get('/api/metas/learning?profile=RAPIDO', headers=auth).json()['enabled'] is False
    payload = {'profile': 'RAPIDO', 'enabled': True, 'confirm': False, 'actor': 'Ramón'}
    assert client.put('/api/metas/learning/switch', json=payload, headers=auth).status_code == 409
    payload['confirm'] = True
    assert client.put('/api/metas/learning/switch', json=payload, headers=auth).json()['enabled'] is True


def test_signal_contribution_is_capped_even_with_learned_weights():
    weights = {name: 1.0 for name in ('volatility', 'break_prob', 'direction', 'scanner', 'advisor', 'touch_buy_to_sell')}
    signals = [{'name': name, 'normalized': 1.0} for name in weights]
    probability = signal_adjusted_probability(.1, signals, weights, max_abs_logit=1.0)
    assert probability < .24


def test_two_worse_learned_versions_alert_without_disabling_switch(tmp_path):
    db, operator, _client = _operator(tmp_path)
    now = datetime.now(timezone.utc)
    alerts = []
    operator.alert = lambda *args: alerts.append(args)
    with db.engine.begin() as conn:
        row = conn.execute(db.meta_profile_settings.select().where(
            db.meta_profile_settings.c.profile == 'RAPIDO')).mappings().one()
        config = json.loads(row['settings']); config['use_learned_weights'] = True
        conn.execute(db.meta_profile_settings.update().where(
            db.meta_profile_settings.c.profile == 'RAPIDO').values(settings=json.dumps(config)))
        goal_id = conn.execute(db.meta_goals.insert().values(capital_initial=100,
            target_usdt=10, target_input_type='USDT', target_input_value=10, profile='RAPIDO',
            status='ACTIVE', environment='TESTNET', created_at=now)).inserted_primary_key[0]
        worse = {'real': {'72h': {'n': 5, 'log_loss': .3, 'brier': .1}},
            'learned': {'72h': {'n': 5, 'log_loss': .5, 'brier': .2}}}
        conn.execute(db.meta_learning_runs.insert().values(started_at=now, finished_at=now,
            status='completed', summary_json=json.dumps({'learned_vs_real': {'RAPIDO': {
                'metrics': worse, 'weight_version_ids': [1]}}})))
    assert operator._alert_if_learned_worse('RAPIDO', worse, [2]) is True
    assert alerts and alerts[0][0] == 'META_LEARNED_WEIGHTS_WORSE'
    with db.engine.connect() as conn:
        row = conn.execute(db.meta_profile_settings.select().where(
            db.meta_profile_settings.c.profile == 'RAPIDO')).mappings().one()
    assert json.loads(row['settings'])['use_learned_weights'] is True


def test_review_ledger_saves_real_scanner_advisor_and_learned_choices(tmp_path):
    db = DBManager(f"sqlite:///{tmp_path / 'meta-review.sqlite'}")
    events = []
    goal = {'id': 7, 'status': 'ACTIVE', 'profile': 'RAPIDO', 'config_snapshot': {'warning_loss_pct': 6}}
    class Core:
        monitor = None
        def get_goal(self, _meta_id): return goal
        def valuation(self, _meta_id): return {'status': 'KNOWN', 'result_pct': 0}
        def _ledger(self, meta_id, run_id, event_type, details, **kwargs):
            events.append((event_type, details))
    operator = MetaOperator(Core(), db, None, None, SimpleNamespace(meta_mode_enabled=True))
    plan = {'decision_id': 'decision-1', 'alternatives': [], 'decision_choices': {
        'comparison_version': 2,
        'real': {'alternative_id': 'a-real'}, 'scanner_advisor': {'alternative_id': 'a-shadow'},
        'learned': {'alternative_id': 'a-learned'}}, 'learning': {'enabled': False, 'version_ids': []}}
    operator._build_plan = lambda _goal: plan
    operator._plan_price_moved = lambda *_args: False
    operator._review_allocations = lambda *_args, **_kwargs: {'decision': 'no_hacer_nada', 'reason': 'test'}
    operator._review_once_locked(7)
    event = next(details for name, details in events if name == 'META_OPERATOR_REVIEW_DECISION')
    assert event['decision_choices']['real']['decision'] == 'mantener'
    assert event['decision_choices']['scanner_advisor']['decision'] == 'mantener'
    assert event['decision_choices']['learned']['decision'] == 'mantener'
    assert event['decision_choices']['comparison_version'] == 2
    assert event['decision_choices']['learned_weights_enabled'] is False


def test_learning_comparison_counts_only_version_two_and_maintain_matches(tmp_path):
    db, operator, _client = _operator(tmp_path)
    now = datetime.now(timezone.utc)
    with db.engine.begin() as conn:
        meta_id = conn.execute(db.meta_goals.insert().values(capital_initial=100,
            target_usdt=10, target_input_type='USDT', target_input_value=10, profile='RAPIDO',
            status='ACTIVE', environment='TESTNET', created_at=now)).inserted_primary_key[0]
        for version in (None, 1, 2):
            choices = {'real': {'decision': 'mantener', 'alternative_id': None},
                'scanner_advisor': {'decision': 'mantener', 'alternative_id': None},
                'learned': {'decision': 'mantener', 'alternative_id': None}}
            if version is not None:
                choices['comparison_version'] = version
            conn.execute(db.meta_ledger.insert().values(meta_id=meta_id, ts=now,
                event_type='META_OPERATOR_REVIEW_DECISION', details=json.dumps({
                    'decision_choices': choices})))
    comparison = operator.learning_status('RAPIDO')['decision_comparison']
    assert comparison['real']['decisions'] == 1
    assert comparison['learned']['decisions'] == 1
    assert comparison['learned']['different_from_real'] == 0
    assert operator.learning_status('RAPIDO')['decision_comparison_since'] is not None


def test_learned_switch_does_not_bypass_existing_75_percent_block(tmp_path):
    db = DBManager(f"sqlite:///{tmp_path / 'meta-block.sqlite'}")
    goal = {'id': 8, 'status': 'ACTIVE', 'profile': 'RAPIDO', 'config_snapshot': {'warning_loss_pct': 4}}
    events = []
    class Core:
        monitor = None
        def get_goal(self, _meta_id): return goal
        def valuation(self, _meta_id): return {'status': 'KNOWN', 'result_pct': -5.0}
        def _ledger(self, meta_id, run_id, event_type, details, **kwargs):
            events.append((event_type, details))
    operator = MetaOperator(Core(), db, None, None, SimpleNamespace(meta_mode_enabled=True))
    operator.set_learning_switch('RAPIDO', True, confirm=True, actor='Ramón')
    opened = []
    operator._build_plan = lambda *_args: opened.append(True)
    result = operator.review_once(8)
    assert result['decision'] == 'no_hacer_nada'
    assert not opened
    assert any(name == 'META_OPERATOR_REVIEW_DECISION' for name, _details in events)
