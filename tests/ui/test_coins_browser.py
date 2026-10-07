import pytest
from playwright.sync_api import expect


def _coins_page(ui_page, live_server, rows, extra_routes=None):
    def route_api(route):
        path = route.request.url.split(live_server.url, 1)[-1].split('?', 1)[0]
        if path == '/api/coins/available':
            route.fulfill(json=[])
        elif path == '/api/coins':
            route.fulfill(json=rows)
        elif extra_routes and path in extra_routes:
            extra_routes[path](route)
        else:
            route.fulfill(json={})
    ui_page.route('**/api/**', route_api)
    ui_page.goto(f'{live_server.url}/#coins')
    expect(ui_page.locator('#coins-table tbody tr')).not_to_have_count(0)


def _row(symbol, state, **extra):
    readiness = {'state': state, 'stage_detail': state, 'progress_pct': 42,
                 'history_days': 210 if state == 'datos_insuficientes' else None,
                 'error': 'detalle completo del fallo' if state == 'error' else None}
    return {'symbol': symbol, 'active': True, 'is_predictor_symbol': symbol == 'XRPUSDT',
            'ready': state == 'lista', 'readiness': readiness,
            'price': None, 'volume_24h_quote': None, 'change_pct_24h': None,
            'added_at': None, 'notes': None, 'open_grid_id': None,
            'volatility_model': None, **extra}


def test_coin_readiness_states_show_status_progress_and_actions(ui_page, live_server):
    rows = [_row('XRPUSDT', 'lista'), _row('AAAUSDT', 'pendiente'),
            _row('BBBUsdt'.upper(), 'descargando'), _row('CCCUSDT', 'entrenando'),
            _row('DDDUSDT', 'consensuando'), _row('EEEUSDT', 'datos_insuficientes'),
            _row('FFFUSDT', 'error')]
    _coins_page(ui_page, live_server, rows)
    expected = {'XRPUSDT': ('Lista', False), 'AAAUSDT': ('Pendiente', True),
                'BBBUSDT': ('Descargando historial', False), 'CCCUSDT': ('Entrenando modelos', False),
                'DDDUSDT': ('Calculando consenso', False), 'EEEUSDT': ('Datos insuficientes', True),
                'FFFUSDT': ('Error', True)}
    for symbol, (label, can_prepare) in expected.items():
        row = ui_page.locator(f'#coins-table tbody tr:has-text("{symbol}")')
        expect(row).to_contain_text(label)
        expect(row.locator('.coin-progress')).to_have_count(1 if label in {'Descargando historial','Entrenando modelos','Calculando consenso'} else 0)
        expect(row.locator('.coin-prepare-btn')).to_have_count(1 if can_prepare else 0)
        expect(row.locator('.coin-prepare-cancel')).to_have_count(1 if label in {'Descargando historial','Entrenando modelos','Calculando consenso'} else 0)
    expect(ui_page.locator('#coins-table')).to_contain_text('Historial: 210 días; se necesitan al menos 540')
    expect(ui_page.locator('#coins-table [title="detalle completo del fallo"]')).to_have_count(1)
    expect(ui_page.locator('#coins-table')).to_contain_text('Modelo de volatilidad')
    expect(ui_page.locator('#coins-table tr:has-text("XRPUSDT") .coin-remove-btn')).to_be_disabled()
    expect(ui_page.locator('#coins-table')).to_contain_text('Grid abierto')
    expect(ui_page.locator('#coins-readiness-note, .coins-readiness-note')).to_contain_text('alrededor de 30 observaciones efectivas')


def test_prepare_button_is_disabled_while_another_coin_is_preparing(ui_page, live_server):
    _coins_page(ui_page, live_server, [_row('XRPUSDT', 'lista'), _row('AAAUSDT', 'descargando'), _row('BBBUSTD', 'pendiente')])
    button = ui_page.locator('#coins-table tr:has-text("BBBUSTD") .coin-prepare-btn')
    expect(button).to_have_attribute('disabled', '')
    expect(button).to_have_attribute('title', 'Espera a que termine la otra preparación')


def test_add_coin_renders_pending_and_background_notice(ui_page, live_server):
    rows = [_row('XRPUSDT', 'lista')]
    def api(route):
        path = route.request.url.split(live_server.url, 1)[-1].split('?', 1)[0]
        if path == '/api/coins/available': route.fulfill(json=[])
        elif path == '/api/coins' and route.request.method == 'POST':
            rows.append(_row('ADAUSDT','pendiente'))
            route.fulfill(status=201,json={'symbol':'ADAUSDT','readiness':{'state':'pendiente'}})
        elif path == '/api/coins': route.fulfill(json=rows)
        else: route.fulfill(json={})
    ui_page.route('**/api/**', api)
    ui_page.goto(f'{live_server.url}/#coins')
    ui_page.locator('#coin-symbol-input').fill('ADAUSDT')
    ui_page.locator('#coins-form button[type=submit]').click()
    expect(ui_page.locator('#coins-table tr:has-text("ADAUSDT")')).to_contain_text('Pendiente')
    expect(ui_page.locator('#coins-notice')).to_contain_text('ADAUSDT agregada. Se está preparando en segundo plano; no podrás abrir grids hasta que esté lista.')


@pytest.mark.parametrize('action', ['prepare','cancel'])
def test_prepare_and_cancel_show_server_conflict_detail(ui_page, live_server, action):
    symbol = 'AAAUSDT'
    state = 'pendiente' if action == 'prepare' else 'descargando'
    rows = [_row('XRPUSDT','lista'),_row(symbol,state)]
    calls=[]
    def api(route):
        path = route.request.url.split(live_server.url, 1)[-1].split('?', 1)[0]
        if path == '/api/coins/available': route.fulfill(json=[])
        elif path == '/api/coins': route.fulfill(json=rows)
        elif path == f'/api/coins/{symbol}/{"prepare" if action == "prepare" else "prepare/cancel"}':
            calls.append(route.request.method)
            route.fulfill(status=409,json={'detail':'La preparación está guardando; espera unos segundos'})
        else: route.fulfill(json={})
    ui_page.route('**/api/**',api)
    ui_page.goto(f'{live_server.url}/#coins')
    ui_page.locator(f'.coin-prepare-{"btn" if action=="prepare" else "cancel"}[data-symbol="{symbol}"]').click()
    expect(ui_page.locator('#coins-error')).to_contain_text('La preparación está guardando; espera unos segundos')
    assert calls == ['POST']


def test_coin_polling_stops_when_readiness_becomes_complete(ui_page, live_server):
    requests={'count':0}
    rows=[_row('XRPUSDT','lista'),_row('AAAUSDT','descargando')]
    def api(route):
        path=route.request.url.split(live_server.url,1)[-1].split('?',1)[0]
        if path=='/api/coins/available': route.fulfill(json=[])
        elif path=='/api/coins':
            requests['count']+=1
            if requests['count']>=2: rows[1]=_row('AAAUSDT','lista')
            route.fulfill(json=rows)
        else: route.fulfill(json={})
    ui_page.route('**/api/**',api)
    ui_page.goto(f'{live_server.url}/#coins')
    expect(ui_page.locator('#coins-table')).to_contain_text('Descargando historial')
    ui_page.wait_for_timeout(5200)
    expect(ui_page.locator('#coins-table')).to_contain_text('Lista')
    stopped=requests['count']
    ui_page.wait_for_timeout(5200)
    assert requests['count']==stopped


def test_coin_polling_stops_when_leaving_coins(ui_page, live_server):
    requests={'count':0}
    rows=[_row('XRPUSDT','lista'),_row('AAAUSDT','entrenando')]
    def api(route):
        path=route.request.url.split(live_server.url,1)[-1].split('?',1)[0]
        if path=='/api/coins/available': route.fulfill(json=[])
        elif path=='/api/coins':
            requests['count']+=1
            route.fulfill(json=rows)
        else: route.fulfill(json={})
    ui_page.route('**/api/**',api)
    ui_page.goto(f'{live_server.url}/#coins')
    expect(ui_page.locator('#coins-table')).to_contain_text('Entrenando modelos')
    ui_page.evaluate("location.hash='#grid'")
    expect(ui_page.locator('#grid-symbol option[value=\"AAAUSDT\"]')).to_have_count(1)
    settled=requests['count']
    ui_page.wait_for_timeout(5200)
    assert requests['count']==settled


def test_grid_selector_disables_unready_coins_and_keeps_xrp_enabled(ui_page, live_server):
    rows=[_row('XRPUSDT','lista'),_row('ADAUSDT','entrenando'),_row('BTCUSDT','lista')]
    _coins_page(ui_page,live_server,rows)
    ui_page.goto(f'{live_server.url}/#grid')
    xrp=ui_page.locator('#grid-symbol option[value="XRPUSDT"]')
    ada=ui_page.locator('#grid-symbol option[value="ADAUSDT"]')
    expect(xrp).not_to_have_attribute('disabled', '')
    expect(ada).to_have_attribute('disabled', '')
    expect(ada).to_contain_text('(preparando)')



def test_grid_advisor_preserves_not_ready_conflict_detail(ui_page, live_server):
    rows=[_row('XRPUSDT','lista'),_row('ADAUSDT','pendiente')]
    _coins_page(ui_page,live_server,rows)
    ui_page.route('**/api/grid/recommend**',lambda route: route.fulfill(status=409,json={'detail':'ADAUSDT aún no está lista; espera a que termine la preparación.'}))
    ui_page.goto(f'{live_server.url}/#grid')
    expect(ui_page.locator('#grid-symbol option[value="ADAUSDT"]')).to_have_attribute('disabled','')
    with ui_page.expect_response('**/api/grid/recommend**'):
        ui_page.locator('#grid-form button[type=submit]').click()
    expect(ui_page.locator('#grid-result')).to_contain_text('ADAUSDT aún no está lista; espera a que termine la preparación.')
