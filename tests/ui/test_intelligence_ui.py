from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_intelligence_tab_is_manual_refresh_read_only_and_links_model_views():
    html = (ROOT / 'frontend' / 'index.html').read_text(encoding='utf-8')
    app = (ROOT / 'frontend' / 'app.js').read_text(encoding='utf-8')
    js = (ROOT / 'frontend' / 'intelligence.js').read_text(encoding='utf-8')
    assert 'data-route="intelligence">Inteligencia' in html
    assert 'id="screen-intelligence"' in html and 'intelligence.js' in html
    assert "'intelligence'].forEach(screen" in app and "window.loadIntelligencePage" in app
    assert "api.get(`/api/intelligence?page=${decisionPage}&page_size=50" in js
    assert "addEventListener('click', load)" in js
    assert '/api/metas/learning/switch' not in js
    assert 'setInterval' not in js
    assert 'href="#models"' in js and 'href="#battle"' in js
    assert 'item.label' in js and 'Recolectando datos' in (ROOT / 'api' / 'routes' / 'intelligence.py').read_text(encoding='utf-8')
    assert 'calibration_deciles' in js and 'candle_inventory' in js
    assert 'replay_windows_new' in js and 'data-page-next' in js
    assert 'signalLabel(name)' in js
    assert 'intelligence-summary' in js
    assert '<details class="intelligence-detail">' in js
    assert 'replayBars(r.chart)' in js
    assert 'B10 \u00b7 Aciertos' in js
    assert '<summary>B6 \u00b7 Decisiones</summary>' in js
    assert 'replay_calibration' in js
    assert 'Ruptura v2 · comparación a 72 h' in js
    assert 'sin datos suficientes' in js
    assert 'salidas observadas del rango' in js
    assert 'JSON.stringify(x.details || {}, null, 2)' in js
    assert 'current_piece_estimate' in js and 'Pieza actual:' in js
