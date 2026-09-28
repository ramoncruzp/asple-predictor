const API_BASE = window.location.protocol === 'file:' ? 'http://localhost:8000' : window.location.origin;
const APP = window.APP = { currentSymbol: 'XRPUSDT', currentInterval: '1h', lastData: {}, cacheKey: 'asple-predictor-cache', chart: null, candleSeries: null };
const MODEL_DISPLAY_NAMES = { model_a: 'XGBoost', model_b: 'GRU (PyTorch)', model_c: 'Prophet+XGBoost', model_d: 'TFT (Temporal Fusion)', ensemble: 'Ensemble' };
function getModelName(key) { return MODEL_DISPLAY_NAMES[key] || key; }
const CONDITION_NAMES = { rsi_oversold: 'RSI Sobrevendido', high_volume: 'Volumen Alto', strong_trend: 'Tendencia Fuerte', ranging_market: 'Mercado Lateral', post_macd_cross: 'Cruce MACD' };
const CONDITION_TOOLTIPS = { rsi_oversold: 'RSI < 30: activo posiblemente sobrevendido, propenso a rebote alcista', high_volume: 'Volumen supera el promedio significativamente; movimiento respaldado por fuerza institucional', strong_trend: 'ADX > 25: tendencia definida con momentum sostenido (alcista o bajista)', ranging_market: 'Precio oscila entre soporte y resistencia sin dirección clara; condición ideal para grid trading', post_macd_cross: 'La línea MACD acaba de cruzar la señal; posible cambio de momentum inminente' };
const MODEL_TOOLTIPS = { model_a: 'Gradient Boosting sobre indicadores técnicos. Rápido y eficiente en tendencias claras.', model_b: 'Red neuronal recurrente. Captura patrones temporales en secuencias de velas.', model_c: 'Modelo híbrido: Prophet detecta ciclos y estacionalidad, XGBoost refina la predicción.', model_d: 'Temporal Fusion Transformer. Aprende dependencias temporales y pondera variables relevantes.' };
function formatAccuracy(model) { const accuracy = model.accuracy ?? model.accuracy_30d; const verified = model.verified_count ?? model.verified_predictions ?? 0; return accuracy == null || !verified ? '\u2014' : percent(accuracy); }
function tooltip(content) { return `<span class="tooltip-wrap"><span class="tooltip-icon" tabindex="0">&#9432;</span><span class="tooltip-content">${escapeHtml(content)}</span></span>`; }
function modelHeader(key) { return `${escapeHtml(getModelName(key))} ${tooltip(MODEL_TOOLTIPS[key] || '')}`; }
class ApiClient {
  constructor(baseUrl = API_BASE) { this.baseUrl = baseUrl; }
  async get(path, params = {}) { const url = new URL(this.baseUrl + path); Object.entries(params).forEach(([key, value]) => { if (value !== undefined && value !== null && value !== '') url.searchParams.set(key, value); }); const controller = new AbortController(); const timer = setTimeout(() => controller.abort(), 10000); try { const response = await fetch(url, { signal: controller.signal }); if (!response.ok) throw new Error(`HTTP ${response.status}`); return await response.json(); } finally { clearTimeout(timer); } }
  async post(path, body) { const controller = new AbortController(); const timer = setTimeout(() => controller.abort(), 10000); try { const response = await fetch(this.baseUrl + path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body), signal: controller.signal }); const data = await response.json().catch(() => null); if (!response.ok) { const error = new Error((data && data.detail) || `HTTP ${response.status}`); error.status = response.status; throw error; } return data; } finally { clearTimeout(timer); } }
  async delete(path) { const controller = new AbortController(); const timer = setTimeout(() => controller.abort(), 10000); try { const response = await fetch(this.baseUrl + path, { method: 'DELETE', signal: controller.signal }); const data = await response.json().catch(() => null); if (!response.ok) { const error = new Error((data && data.detail) || `HTTP ${response.status}`); error.status = response.status; throw error; } return data; } finally { clearTimeout(timer); } }
  status() { return this.get('/'); }
  candles(symbol, interval) { return this.get('/api/candles', { symbol, interval: interval.toLowerCase() }); }
  consensus(symbol, interval) { return this.get('/api/predictions/consensus', { symbol, interval: interval.toLowerCase() }); }
  latest(symbol, interval, limit = 10) { return this.get('/api/predictions/latest', { symbol, interval: interval.toLowerCase(), limit }); }
  history(symbol, model, days = 30) { return this.get('/api/predictions/history', { symbol, model, days }); }
  modelsStatus() { return this.get('/api/models/status'); }
  shadowStatus() { return this.get('/api/models/shadow-status'); }
  conditions(model) { return this.get('/api/models/accuracy-by-condition', { model }); }
  grid(params) { return this.get('/api/grid/recommend', params); }
  coinsAvailable() { return this.get('/api/coins/available'); }
  coinsList() { return this.get('/api/coins'); }
  addCoin(symbol, notes) { return this.post('/api/coins', { symbol, notes: notes || null }); }
  removeCoin(symbol) { return this.delete(`/api/coins/${symbol}`); }
}
const api = new ApiClient();
const $ = (selector) => document.querySelector(selector);
const escapeHtml = (value) => String(value ?? '-').replace(/[&<>\'"]/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' }[char]));
const percent = (value, digits = 1) => value == null ? '\u2014' : Number.isFinite(Number(value)) ? `${(Number(value) * 100).toFixed(digits)}%` : '\u2014';
const money = (value, digits = 4) => value == null ? '\u2014' : Number.isFinite(Number(value)) ? `$${Number(value).toFixed(digits)}` : '\u2014';
const signalClass = signal => signal === 'ALCISTA' ? 'bullish' : signal === 'BAJISTA' ? 'bearish' : 'neutral';
function cache(data) { APP.lastData = { ...APP.lastData, ...data }; localStorage.setItem(APP.cacheKey, JSON.stringify(APP.lastData)); }
function restoreCache() { try { APP.lastData = JSON.parse(localStorage.getItem(APP.cacheKey) || '{}'); } catch (_) { APP.lastData = {}; } }
function setOffline(offline) { $('#offline-banner').classList.toggle('hidden', !offline); const chip = $('#api-chip'); chip.classList.toggle('online', !offline); chip.classList.toggle('offline', offline); chip.querySelector('span').textContent = offline ? 'API Offline' : 'API Online'; }
async function checkApiStatus() { try { await api.status(); setOffline(false); } catch (_) { setOffline(true); } }
function clearChart() { if (APP.candleSeries) { APP.candleSeries.setData([]); APP.candleSeries = null; } if (APP.chart) { APP.chart.remove(); APP.chart = null; } $('#price-chart').innerHTML = ''; }
function renderModelCards(consensus, status) { const byModel = Object.fromEntries((status?.models || []).map(item => [item.model_name, item])); $('#model-cards').innerHTML = Object.keys(consensus.weights || {}).map(key => { const css = key.replace('_', '-'); const result = consensus[key] || {}; const model = byModel[key] || {}; return `<div class="card model-card ${css}"><div class="model-name"><h3>${getModelName(key)}</h3><span class="accuracy">${percent(model.accuracy_30d)}</span></div><div class="model-meta"><span class="signal ${signalClass(result.signal)}">${escapeHtml(result.signal)}</span><span class="mono muted">${percent(result.probability_up)}</span></div><div class="progress"><i style="width:${Math.max(0, Math.min(100, Number(result.probability_up || 0) * 100))}%"></i></div><div class="features">${(result.top_features || []).slice(0, 3).map(feature => `<span class="feature">${escapeHtml(feature[0])}</span>`).join('')}</div></div>`; }).join(''); }
function renderConsensus(data) { if (data.model_available === false) { $('#consensus-card').innerHTML = '<div class="eyebrow">MODO SOMBRA</div><p>Sin modelo para este par/intervalo</p>'; $('#chart-signal').className = 'signal neutral'; $('#chart-signal').textContent = 'N/A'; return; } $('#consensus-card').innerHTML = `<div class="eyebrow">CONSENSO MODEL A</div><div class="big-prob">${percent(data.consensus_probability_up, 2)}</div><div class="agreement">${escapeHtml(data.agreement_count)}/${Object.keys(data.weights || {}).length || 1} modelos coinciden</div><div class="signal ${signalClass(data.consensus_signal)}" style="margin-top:13px">${escapeHtml(data.consensus_signal)}</div>`; $('#chart-signal').className = `signal ${signalClass(data.consensus_signal)}`; $('#chart-signal').textContent = data.consensus_signal; }
function renderShadowStatus(data) { const status = data.kill_status || 'pending'; const color = status === 'pass' ? 'var(--green, #3FB950)' : status === 'fail' ? 'var(--red, #F85149)' : 'var(--muted, #8B949E)'; const empty = Number(data.n_verified_total || 0) === 0; const detail = empty ? '<p>Sin predicciones verificadas a\u00fan</p>' : `<p>Precisi\u00f3n: ${percent(data.precision_nonoverlap)} vs base ${percent(data.base_rate)}</p><p>Retorno medio: ${percent(data.mean_return_nonoverlap, 2)} vs 0.20%</p>`; $('#shadow-status').innerHTML = `<div class="card-title"><h2>Modo sombra \u2014 Model A</h2><strong style="color:${color}">${escapeHtml(status.toUpperCase())}</strong></div><p>${data.n_nonoverlap || 0} / 40 se\u00f1ales no solapadas</p>${detail}<p class="muted">Se\u00f1al en evaluaci\u00f3n. No usar para operar.</p>`; }
function renderRecent(rows) { $('#recent-predictions').innerHTML = (rows || []).slice(0, 10).map(row => `<div class="prediction-row"><span><b>${escapeHtml(row.symbol)}</b><br><span class="muted">${escapeHtml(getModelName(row.model_name))}</span></span><span class="signal ${signalClass(row.signal)}">${escapeHtml(row.signal)}</span><span class="prob">${percent(row.probability_up)}</span><span class="${row.is_verified ? 'verified' : 'pending'}">${row.is_verified ? (row.was_correct === null ? 'N/A' : row.was_correct ? 'OK' : 'X') : '...'}</span></div>`).join('') || '<p class="muted">Sin predicciones registradas.</p>'; }
function renderChart(candles, consensus, interval) { clearChart(); const box = $('#price-chart'); if (!window.LightweightCharts) { box.innerHTML = '<p class="muted">Chart no disponible.</p>'; return; } const isIntraday = ['1h', '4h', '12h'].includes((interval || '4h').toLowerCase()); APP.chart = LightweightCharts.createChart(box, { layout: { background: { color: '#161B22' }, textColor: '#8B949E' }, grid: { vertLines: { color: '#21262D' }, horzLines: { color: '#21262D' } }, rightPriceScale: { borderColor: '#30363D' }, timeScale: { borderColor: '#30363D', timeVisible: isIntraday, secondsVisible: false } }); APP.candleSeries = APP.chart.addCandlestickSeries({ upColor: '#3FB950', downColor: '#F85149', borderVisible: false, wickUpColor: '#3FB950', wickDownColor: '#F85149' }); if (candles.length) { APP.candleSeries.setData(candles); const last = candles[candles.length - 1]; if (consensus.consensus_signal === 'ALCISTA') APP.candleSeries.setMarkers([{ time: last.time, position: 'belowBar', color: '#3FB950', shape: 'arrowUp', text: consensus.consensus_signal }]); APP.chart.timeScale().fitContent(); APP.chart.timeScale().scrollToRealTime(); } }
async function loadDashboardV2a() { const symbol = $('#dashboard-symbol').value; const interval = $('#dashboard-interval').value.toLowerCase(); APP.currentSymbol = symbol; APP.currentInterval = interval; clearChart(); try { const [candles, consensus, rows, status, shadow] = await Promise.all([api.candles(symbol, interval), api.consensus(symbol, interval), api.latest(symbol, interval, 10), api.modelsStatus(), api.shadowStatus()]); cache({ candles, consensus, rows, status, shadow }); renderModelCards(consensus, status); renderConsensus(consensus); renderShadowStatus(shadow); renderRecent(rows); renderChart(candles, consensus, interval); if (consensus.indicators) { $('#indicator-rsi').textContent = Number(consensus.indicators.rsi_14).toFixed(2); $('#indicator-macd').textContent = Number(consensus.indicators.macd).toFixed(5); $('#indicator-atr').textContent = Number(consensus.indicators.atr_14).toFixed(5); } $('#dashboard-updated').textContent = `Actualizado ${new Date().toLocaleTimeString()}`; $('#chart-caption').textContent = `${symbol} - ${interval.toUpperCase()} - ${consensus.persisted === false ? 'vista previa (no registrada)' : 'consenso en vivo'}`; setOffline(false); } catch (_) { setOffline(true); if (APP.lastData.consensus) { renderModelCards(APP.lastData.consensus, APP.lastData.status); renderConsensus(APP.lastData.consensus); renderRecent(APP.lastData.rows); renderChart(APP.lastData.candles || [], APP.lastData.consensus, APP.currentInterval); } } }
async function refreshDashboard() { const button = $('#refresh-dashboard'); const original = button.textContent; button.disabled = true; button.textContent = 'Actualizando...'; try { await loadDashboard(); } finally { button.disabled = false; button.textContent = original; } }
function renderBattle(status, conditionSets, history) { const models = status.models || []; const scores = models.map(model => { const verified = model.verified_count ?? model.verified_predictions ?? 0; return `<div class="card score-card"><h3>${modelHeader(model.model_name)}</h3><strong>${formatAccuracy(model)}</strong><p class="muted">${model.total_predictions || 0} predicciones<br><small>${verified} de ${model.total_predictions || 0} verificadas</small></p></div>`; }).join(''); const conditions = ['rsi_oversold', 'high_volume', 'strong_trend', 'ranging_market', 'post_macd_cross']; const lookup = Object.fromEntries(Object.entries(conditionSets).flatMap(([model, rows]) => rows.map(row => [`${row.condition_name}:${model}`, row]))); const conditionRows = conditions.map(condition => { const entries = models.map(model => lookup[`${condition}:${model.model_name}`] || {}); const values = entries.map(entry => entry.accuracy); const best = Math.max(...values.map(value => Number(value ?? -1))); const label = `<span class="condition-label">${escapeHtml(CONDITION_NAMES[condition] || condition)} ${tooltip(CONDITION_TOOLTIPS[condition] || '')}</span>`; return `<tr><td>${label}</td>${entries.map(entry => { const verified = entry.verified_count ?? entry.verified_predictions ?? 0; return `<td class="${entry.accuracy !== null && entry.accuracy !== undefined && entry.accuracy === best ? 'winner' : ''}">${entry.accuracy === null || entry.accuracy === undefined || !verified ? '-' : percent(entry.accuracy)}<small class="condition-count">${verified} de ${entry.total_predictions || 0} verificadas</small></td>`; }).join('')}</tr>`; }).join(''); const historyRows = (history || []).slice(0, 30).map(row => `<tr><td>${escapeHtml(new Date(row.predicted_at).toLocaleString())}</td><td>${escapeHtml(row.symbol)}</td><td>${escapeHtml(getModelName(row.model_name))}</td><td><span class="signal ${signalClass(row.signal)}">${escapeHtml(row.signal)}</span></td><td class="mono">${percent(row.probability_up)}</td><td>${row.is_verified ? (row.was_correct === null ? 'NEUTRAL' : row.was_correct ? 'ACERTO' : 'FALLO') : 'PENDIENTE'}</td><td class="muted">${escapeHtml((row.was_correct ? row.why_correct : row.why_wrong || '').slice(0, 60))}</td></tr>`).join(''); $('#battle-content').innerHTML = `<div class="battle-grid">${scores}</div><div class="card table-card"><table class="data-table"><thead><tr><th>Condicion</th>${models.map(model => `<th>${modelHeader(model.model_name)}</th>`).join('')}</tr></thead><tbody>${conditionRows}</tbody></table></div><div class="card table-card"><table class="data-table"><thead><tr><th>Fecha</th><th>Simbolo</th><th>Modelo</th><th>Senal</th><th>Prob.</th><th>Resultado</th><th>Detalle</th></tr></thead><tbody>${historyRows || '<tr><td colspan="7">Sin historial.</td></tr>'}</tbody></table></div>`; }
async function loadBattleV2a() { try { const status = await api.modelsStatus(); const names = (status.models || []).map(model => model.model_name); const conditionRows = await Promise.all(names.map(name => api.conditions(name))); const history = await api.history(null, null, 30); renderBattle(status, Object.fromEntries(names.map((name, index) => [name, conditionRows[index]])), history); setOffline(false); } catch (_) { setOffline(true); } }
function gridSvg(data) { const floor = Number(data.recommended_floor), ceiling = Number(data.recommended_ceiling), current = Number(data.current_price), count = Number(data.suggested_grids), y = value => 170 - ((value - floor) / (ceiling - floor)) * 140; let lines = ''; for (let index = 0; index <= count; index++) { const value = floor + (ceiling - floor) * index / count; lines += `<line x1="30" x2="570" y1="${y(value)}" y2="${y(value)}" stroke="#30363D"/>`; } return `<svg class="grid-svg" viewBox="0 0 620 190"><line x1="30" x2="570" y1="${y(floor)}" y2="${y(floor)}" stroke="#3FB950" stroke-width="2"/><line x1="30" x2="570" y1="${y(ceiling)}" y2="${y(ceiling)}" stroke="#F85149" stroke-width="2"/>${lines}<circle cx="300" cy="${y(current)}" r="5" fill="#2F81F7"/></svg>`; }
function renderGridV2a(data) { const analysis = data.analysis, prediction = data.prediction_signal, signal = prediction?.consensus_signal; $('#grid-result').className = 'grid-result card'; $('#grid-result').innerHTML = `<div class="grid-header"><div><p class="eyebrow">RECOMENDACION ${escapeHtml(data.symbol)}</p><div class="price-large">${money(data.current_price)}</div><span class="muted">Rango ${Number(data.range_pct).toFixed(2)}%</span></div><span class="signal ${signalClass(signal)}">${signal ? escapeHtml(signal) : 'Sin predicci\u00f3n reciente'}</span></div><div class="level-row"><div class="level floor"><span>Piso recomendado</span><strong>${money(data.recommended_floor)}</strong></div><div class="level ceiling"><span>Techo recomendado</span><strong>${money(data.recommended_ceiling)}</strong></div></div>${gridSvg(data)}<div class="card-title"><h2>Configuracion sugerida</h2><button id="copy-grid" class="button secondary">Copiar configuracion</button></div><div class="indicator-strip"><div><span>GRIDS</span><b>${data.suggested_grids}</b></div><div><span>ESPACIADO</span><b>${Number(data.spacing_pct).toFixed(2)}%</b></div><div><span>CAPITAL / GRID</span><b>${money(data.capital_per_grid, 2)}</b></div></div><h2 style="font-size:14px;margin-top:24px">Por que estos niveles</h2><div class="why">Soporte: <b>${money(analysis.main_support)}</b> (${analysis.support_touches} toques) - Resistencia: <b>${money(analysis.main_resistance)}</b> (${analysis.resistance_touches} toques) - ATR: <b>${money(analysis.atr)}</b></div><p class="disclaimer">${escapeHtml(data.disclaimer)}</p>`; $('#copy-grid').onclick = () => { const text = `ASPLE Trade - Grid ${data.symbol.replace('USDT', '/USDT')}\nPiso: ${money(data.recommended_floor)} | Techo: ${money(data.recommended_ceiling)}\nGrids: ${data.suggested_grids} | Capital/grid: ${money(data.capital_per_grid, 2)}\nGenerado: ${new Date().toLocaleString()}`; navigator.clipboard.writeText(text).then(() => { const toast = $('#toast'); toast.textContent = 'Configuracion copiada'; toast.classList.remove('hidden'); setTimeout(() => toast.classList.add('hidden'), 2200); }); }; }
function route() { const name = (location.hash || '#dashboard').slice(1); ['dashboard', 'battle', 'grid', 'coins'].forEach(screen => { $(`#screen-${screen}`).classList.toggle('hidden', screen !== name); document.querySelector(`[data-route="${screen}"]`).classList.toggle('active', screen === name); }); if (name === 'dashboard') loadDashboard(); if (name === 'battle') loadBattle(); if (name === 'coins') loadCoins(); }
document.addEventListener('DOMContentLoaded', () => { restoreCache(); $('#grid-form').addEventListener('submit', loadGrid); $('#dashboard-symbol').addEventListener('change', loadDashboard); $('#dashboard-interval').addEventListener('change', loadDashboard); $('#refresh-dashboard').addEventListener('click', refreshDashboard); window.addEventListener('hashchange', route); route(); checkApiStatus(); setInterval(checkApiStatus, 30000); setInterval(() => { if ((location.hash || '#dashboard') === '#dashboard') loadDashboard(); }, 60000); });

// V2b volatility UI: fetched alongside the existing dashboard and battle views.
const loadDashboardV2bBase = loadDashboardV2a;
const loadBattleV2bBase = loadBattleV2a;
const renderGridV2bBase = renderGridV2a;
APP.volHistoryChart = null;
const VOL_HORIZONS_UI = [1, 2, 4, 24];
function volNumber(value, digits = 2) { return value == null || !Number.isFinite(Number(value)) ? '\u2014' : Number(value).toFixed(digits); }
async function volGet(path, params = {}) { try { return await api.get(path, params); } catch (error) { const match = String(error.message || '').match(/HTTP (\d+)/); if (match) error.status = Number(match[1]); throw error; } }
function volatilityRegimeClass(regime) { return regime === 'CALMA' ? 'calm' : regime === 'AGITADO' ? 'agitated' : 'normal'; }
function renderVolDashboard(data, error = null) {
  const host = $('#volatility-card');
  if (error?.status === 503) { host.innerHTML = '<div class="card-title"><div><h2>Volatilidad esperada — XRP</h2></div></div><p class="muted">Modelos de volatilidad no entrenados</p>'; return; }
  if (!data) { host.innerHTML = '<div class="card-title"><div><h2>Volatilidad esperada — XRP</h2></div></div><p class="muted">Pronóstico de volatilidad no disponible</p>'; return; }
  const rows = VOL_HORIZONS_UI.map(h => data.forecasts?.find(row => Number(row.horizon_h) === h)).map((row, i) => row ? `<tr><td>${VOL_HORIZONS_UI[i]}h</td><td>±${volNumber(row.move_1sigma_pct)}%</td><td>${money(row.range_1sigma?.[0])} – ${money(row.range_1sigma?.[1])}</td><td>${money(row.range_2sigma?.[0])} – ${money(row.range_2sigma?.[1])}</td><td>${escapeHtml(row.champion || '—')}</td></tr>` : `<tr><td>${VOL_HORIZONS_UI[i]}h</td><td>—</td><td>—</td><td>—</td><td>—</td></tr>`).join('');
  const stale = data.forecasts?.some(row => row.stale);
  host.innerHTML = `<div class="card-title"><div><h2>Volatilidad esperada — XRP</h2><span class="muted">${money(data.price)}</span></div><span class="vol-regime ${volatilityRegimeClass(data.regime)}">${escapeHtml(data.regime || 'NORMAL')}</span></div>${stale ? '<p class="vol-stale">Pronóstico desactualizado</p>' : ''}<div class="table-card vol-table-wrap"><table class="data-table"><thead><tr><th>Horizonte</th><th>Movimiento típico</th><th>Rango 1σ</th><th>Rango 2σ</th><th>Campeón</th></tr></thead><tbody>${rows}</tbody></table></div><p class="vol-footnote">Movimiento típico = 1 desviación (~68%). Rango 2σ ≈ 95%.</p>`;
}
async function loadVolDashboard() { try { const data = await volGet('/api/volatility/forecast', { symbol: 'XRPUSDT' }); cache({ volForecast: data }); renderVolDashboard(data); } catch (error) { renderVolDashboard(APP.lastData.volForecast || null, error); } }
async function loadDashboard() { await loadDashboardV2bBase(); await loadVolDashboard(); }
function renderVolBattleTable(data) {
  const rows = (data.models || []).map(model => { const champ = model.is_champion, liveR2 = model.r2_live == null ? (Number(model.n_verified || 0) < 30 ? '— (n<30)' : '—') : volNumber(model.r2_live, 3); return `<tr class="${champ ? 'vol-champion-row' : ''}"><td>${escapeHtml(model.model_name || '—')}${champ ? ' <span class="champion-star" title="Campeón">★</span>' : ''}</td><td>${volNumber(model.r2_cal, 3)}</td><td>${volNumber(model.qlike_cal, 4)}</td><td>${volNumber(model.n_verified, 0)}</td><td>${liveR2}</td></tr>`; }).join('');
  $('#vol-battle-table').innerHTML = `<thead><tr><th>Modelo</th><th>R² histórico</th><th>QLIKE histórico</th><th>Verificadas en vivo</th><th>R² en vivo</th></tr></thead><tbody>${rows || '<tr><td colspan="5">Sin modelos disponibles</td></tr>'}</tbody>`;
}
function renderVolHistory(rows) {
  const container = $('#vol-history-chart'), empty = $('#vol-history-empty');
  if (APP.volHistoryChart) { APP.volHistoryChart.remove(); APP.volHistoryChart = null; } container.innerHTML = '';
  const items = Array.isArray(rows) ? rows : [];
  if (!window.LightweightCharts || !items.length) { empty.textContent = items.length ? 'Gráfico no disponible' : 'Aún no hay resultados verificados'; empty.classList.remove('hidden'); return; }
  APP.volHistoryChart = LightweightCharts.createChart(container, { height: 250, layout: { background: { color: 'transparent' }, textColor: '#8b949e' }, grid: { vertLines: { color: '#21262d' }, horzLines: { color: '#21262d' } }, timeScale: { timeVisible: true, secondsVisible: false }, rightPriceScale: { borderColor: '#30363d' } });
  const forecast = APP.volHistoryChart.addLineSeries({ color: '#2F81F7', lineWidth: 2, title: 'Pronóstico' }), realized = APP.volHistoryChart.addLineSeries({ color: '#3FB950', lineWidth: 2, title: 'Realizado' });
  const points = key => items.filter(row => row.forecast_at && row[key] != null).map(row => ({ time: Math.floor(new Date(row.forecast_at).getTime() / 1000), value: Number(row[key]) })).filter(row => Number.isFinite(row.time) && Number.isFinite(row.value)).sort((a,b) => a.time-b.time);
  forecast.setData(points('pred_vol_pct')); const actual = points('realized_vol_pct'); realized.setData(actual);
  if (!actual.length) { empty.textContent = 'Aún no hay resultados verificados'; empty.classList.remove('hidden'); } else empty.classList.add('hidden');
  APP.volHistoryChart.timeScale().fitContent();
}
async function loadVolBattle() {
  const horizon = Number($('#vol-horizon-select').value);
  try { const data = await volGet('/api/volatility/battle', { symbol: 'XRPUSDT', horizon }); renderVolBattleTable(data); const champion = (data.models || []).find(model => model.is_champion)?.model_name; if (!champion) { renderVolHistory([]); return; } const history = await volGet('/api/volatility/history', { symbol: 'XRPUSDT', horizon, model: champion, limit: 200 }); renderVolHistory(history); }
  catch (error) { $('#vol-battle-table').innerHTML = `<tbody><tr><td>${error.status === 503 ? 'Modelos de volatilidad no entrenados' : 'Battle de volatilidad no disponible'}</td></tr></tbody>`; renderVolHistory([]); }
}
async function loadBattle() { await loadBattleV2bBase(); await loadVolBattle(); }
function renderGrid(data, forecast = null) {
  renderGridV2bBase(data); const host = $('#grid-result');
  if (data.symbol !== 'XRPUSDT') { host.insertAdjacentHTML('beforeend', '<section class="vol-grid-reference"><h3>Rango sugerido por volatilidad (24h)</h3><p class="muted">Referencia disponible para XRP/USDT.</p><p class="vol-footnote">Referencia. Cada nivel debe superar 0.20% para cubrir comisiones.</p></section>'); return; }
  const row24 = forecast?.forecasts?.find(row => Number(row.horizon_h) === 24), row4 = forecast?.forecasts?.find(row => Number(row.horizon_h) === 4), range = row24?.range_2sigma;
  host.insertAdjacentHTML('beforeend', `<section class="vol-grid-reference"><h3>Rango sugerido por volatilidad (24h)</h3><p>Rango 2σ: <strong>${range ? `${money(range[0])} – ${money(range[1])}` : '—'}</strong></p><p>Movimiento típico 4h: <strong>${row4?.move_1sigma_pct == null ? '—' : `±${volNumber(row4.move_1sigma_pct)}%`}</strong> (referencia de espaciado)</p><p class="vol-footnote">Referencia. Cada nivel debe superar 0.20% para cubrir comisiones.</p></section>`);
}
async function loadGrid(event) {
  event.preventDefault(); const params = { symbol: $('#grid-symbol').value, capital: $('#grid-capital').value, risk: $('#grid-risk').value, days: $('#grid-days').value }, button = event.target.querySelector('button'); button.disabled = true; button.textContent = 'ANALIZANDO...';
  try { const data = await api.grid(params); let forecast = null; if (data.symbol === 'XRPUSDT') { try { forecast = await volGet('/api/volatility/forecast', { symbol: 'XRPUSDT' }); } catch (_) {} } cache({ grid: data, ...(forecast ? { volForecast: forecast } : {}) }); renderGrid(data, forecast); setOffline(false); }
  catch (_) { setOffline(true); $('#grid-result').innerHTML = '<div class="empty-state"><h2>No fue posible analizar</h2><p class="muted">Comprueba que la API este activa y Binance sea accesible.</p></div>'; }
  finally { button.disabled = false; button.innerHTML = 'ANALIZAR <span>-&gt;</span>'; }
}
document.addEventListener('DOMContentLoaded', () => { $('#vol-horizon-select')?.addEventListener('change', loadVolBattle); });

// Coin registry: add/remove tradable pairs shown across the app.
APP.coinsAvailableLoaded = false;
APP.coinsPendingDelete = null;
async function loadCoinsAvailableOnce() {
  if (APP.coinsAvailableLoaded) return;
  try {
    const symbols = await api.coinsAvailable();
    $('#coin-symbol-options').innerHTML = symbols.map(symbol => `<option value="${escapeHtml(symbol)}"></option>`).join('');
    APP.coinsAvailableLoaded = true;
  } catch (_) {}
}
function showCoinsError(message) { const el = $('#coins-error'); if (!message) { el.classList.add('hidden'); el.textContent = ''; return; } el.textContent = message; el.classList.remove('hidden'); }
function renderCoinsTable(rows) {
  const body = (rows || []).map(row => {
    const changeClass = row.change_pct_24h == null ? '' : Number(row.change_pct_24h) >= 0 ? 'change-positive' : 'change-negative';
    const changeText = row.change_pct_24h == null ? '—' : `${Number(row.change_pct_24h).toFixed(2)}%`;
    const priceText = row.price == null ? '—' : money(row.price);
    const volumeText = row.volume_24h_quote == null ? '—' : Number(row.volume_24h_quote).toLocaleString('es-ES', { maximumFractionDigits: 0 });
    const addedText = row.added_at ? escapeHtml(new Date(row.added_at).toLocaleDateString()) : '—';
    const notesText = row.notes ? escapeHtml(row.notes) : '—';
    const pending = APP.coinsPendingDelete === row.symbol;
    const buttonAttrs = row.is_predictor_symbol ? 'disabled title="Símbolo activo del predictor"' : '';
    const buttonLabel = !row.is_predictor_symbol && pending ? '¿Sacar?' : '×';
    return `<tr><td>${escapeHtml(row.symbol)}</td><td>${priceText}</td><td>${volumeText}</td><td class="${changeClass}">${changeText}</td><td>${addedText}</td><td>${notesText}</td><td><button type="button" class="button secondary coin-remove-btn" data-symbol="${escapeHtml(row.symbol)}" ${buttonAttrs}>${buttonLabel}</button></td></tr>`;
  }).join('');
  $('#coins-table').innerHTML = `<thead><tr><th>Simbolo</th><th>Precio</th><th>Volumen 24h</th><th>Cambio 24h</th><th>Alta</th><th>Notas</th><th></th></tr></thead><tbody>${body || '<tr><td colspan="7">Sin monedas activas.</td></tr>'}</tbody>`;
}
async function loadCoins() {
  loadCoinsAvailableOnce();
  try { renderCoinsTable(await api.coinsList()); }
  catch (_) { $('#coins-table').innerHTML = '<tbody><tr><td>No se pudo cargar la lista de monedas.</td></tr></tbody>'; }
}
async function handleCoinFormSubmit(event) {
  event.preventDefault();
  const symbolInput = $('#coin-symbol-input'), notesInput = $('#coin-notes-input');
  const symbol = symbolInput.value.trim().toUpperCase();
  if (!symbol) return;
  showCoinsError(null);
  try { await api.addCoin(symbol, notesInput.value.trim()); symbolInput.value = ''; notesInput.value = ''; await loadCoins(); }
  catch (error) { showCoinsError(error.message || 'No se pudo agregar la moneda.'); }
}
async function handleCoinsTableClick(event) {
  const button = event.target.closest('.coin-remove-btn');
  if (!button || button.disabled) return;
  const symbol = button.dataset.symbol;
  if (APP.coinsPendingDelete !== symbol) { APP.coinsPendingDelete = symbol; await loadCoins(); return; }
  APP.coinsPendingDelete = null;
  showCoinsError(null);
  try { await api.removeCoin(symbol); await loadCoins(); }
  catch (error) { showCoinsError(error.message || 'No se pudo sacar la moneda.'); await loadCoins(); }
}
document.addEventListener('DOMContentLoaded', () => { $('#coins-form')?.addEventListener('submit', handleCoinFormSubmit); $('#coins-table')?.addEventListener('click', handleCoinsTableClick); });
