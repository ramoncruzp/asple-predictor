const API_BASE = window.location.protocol === 'file:' ? 'http://localhost:8000' : window.location.origin;
const APP = window.APP = { currentSymbol: 'XRPUSDT', currentInterval: '1h', lastData: {}, cacheKey: 'asple-predictor-cache', chart: null, candleSeries: null, apiHealthState: { failures: 0, status: 'checking', lastSuccessAt: null }, gridSymbol: 'XRPUSDT' };
const MODEL_DISPLAY_NAMES = { model_a: 'XGBoost', model_b: 'GRU (PyTorch)', model_c: 'Prophet+XGBoost', model_d: 'TFT (Temporal Fusion)', ensemble: 'Ensemble' };
function getModelName(key) { return MODEL_DISPLAY_NAMES[key] || key; }
const CONDITION_NAMES = { rsi_oversold: 'RSI Sobrevendido', high_volume: 'Volumen Alto', strong_trend: 'Tendencia Fuerte', ranging_market: 'Mercado Lateral', post_macd_cross: 'Cruce MACD' };
const CONDITION_TOOLTIPS = { rsi_oversold: 'RSI < 30: activo posiblemente sobrevendido, propenso a rebote alcista', high_volume: 'Volumen supera el promedio significativamente; movimiento respaldado por fuerza institucional', strong_trend: 'ADX > 25: tendencia definida con momentum sostenido (alcista o bajista)', ranging_market: 'Precio oscila entre soporte y resistencia sin dirección clara; condición ideal para grid trading', post_macd_cross: 'La línea MACD acaba de cruzar la señal; posible cambio de momentum inminente' };
const MODEL_TOOLTIPS = { model_a: 'Gradient Boosting sobre indicadores técnicos. Rápido y eficiente en tendencias claras.', model_b: 'Red neuronal recurrente. Captura patrones temporales en secuencias de velas.', model_c: 'Modelo híbrido: Prophet detecta ciclos y estacionalidad, XGBoost refina la predicción.', model_d: 'Temporal Fusion Transformer. Aprende dependencias temporales y pondera variables relevantes.' };
function formatAccuracy(model) { const accuracy = model.accuracy ?? model.accuracy_30d; const verified = model.verified_count ?? model.verified_predictions ?? 0; return accuracy == null || !verified ? '\u2014' : percent(accuracy); }
let tooltipCounter = 0;
function tooltip(content) { const id = `help-${++tooltipCounter}`; return `<span class="tooltip-wrap"><button type="button" class="tooltip-icon" aria-label="Ayuda" aria-describedby="${id}">&#9432;</button><span class="tooltip-content" id="${id}" role="tooltip">${escapeHtml(content)}</span></span>`; }
function modelHeader(key) { return `${escapeHtml(getModelName(key))} ${tooltip(MODEL_TOOLTIPS[key] || '')}`; }
function validationStatusTag(model) { return model?.validation_status === 'not_validated' ? `<span class="validation-chip">No validado ${tooltip('Se muestra para comparar; no se usa para decidir ni operar.')}</span>` : ''; }
class ApiClient {
  constructor(baseUrl = API_BASE) { this.baseUrl = baseUrl; }
  async _fetch(url, options = {}) {
    const headers = { ...(options.headers || {}), ...(window.gridApiToken ? { 'X-API-Token': window.gridApiToken } : {}) };
    let response = await fetch(url, { ...options, headers });
    if (response.status === 403 && !window.gridApiToken && !window.gridTokenPromptDeclined && typeof window.prompt === 'function') {
      const token = window.prompt('La API requiere X-API-Token. El token se conserva solo en memoria de esta pestaña.');
      if (token) { window.gridApiToken = token; response = await fetch(url, { ...options, headers: { ...headers, 'X-API-Token': token } }); }
      else window.gridTokenPromptDeclined = true;
    }
    return response;
  }
  async get(path, params = {}, timeoutMs = 10000) { const url = new URL(this.baseUrl + path); Object.entries(params).forEach(([key, value]) => { if (value !== undefined && value !== null && value !== '') url.searchParams.set(key, value); }); const controller = new AbortController(); const timer = setTimeout(() => controller.abort(), timeoutMs); try { const response = await this._fetch(url, { signal: controller.signal }); if (!response.ok) throw new Error(`HTTP ${response.status}`); return await response.json(); } finally { clearTimeout(timer); } }
  async post(path, body) { const controller = new AbortController(); const timer = setTimeout(() => controller.abort(), 10000); try { const response = await this._fetch(this.baseUrl + path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body), signal: controller.signal }); const data = await response.json().catch(() => null); if (!response.ok) { const error = new Error((data && data.detail) || `HTTP ${response.status}`); error.status = response.status; throw error; } return data; } finally { clearTimeout(timer); } }
  async delete(path) { const controller = new AbortController(); const timer = setTimeout(() => controller.abort(), 10000); try { const response = await this._fetch(this.baseUrl + path, { method: 'DELETE', signal: controller.signal }); const data = await response.json().catch(() => null); if (!response.ok) { const error = new Error((data && data.detail) || `HTTP ${response.status}`); error.status = response.status; throw error; } return data; } finally { clearTimeout(timer); } }
  status() { return this.get('/api/health', {}, 8000); }
  candles(symbol, interval) { return this.get('/api/candles', { symbol, interval: interval.toLowerCase() }); }
  consensus(symbol, interval) { return this.get('/api/predictions/consensus', { symbol, interval: interval.toLowerCase() }); }
  latest(symbol, interval, limit = 10) { return this.get('/api/predictions/latest', { symbol, interval: interval.toLowerCase(), limit }); }
  history(symbol, model, days = 30) { return this.get('/api/predictions/history', { symbol, model, days }); }
  modelsStatus() { return this.get('/api/models/status'); }
  shadowStatus() { return this.get('/api/models/shadow-status'); }
  conditions(model) { return this.get('/api/models/accuracy-by-condition', { model }); }
  grid(params) { return this.get('/api/grid/recommend', params); }
  coinsAvailable() { return this.get('/api/coins/available'); }
  coinsList(includeInactive = false) { return this.get(`/api/coins${includeInactive ? '?include_inactive=true' : ''}`); }
  addCoin(symbol, notes) { return this.post('/api/coins', { symbol, notes: notes || null }); }
  removeCoin(symbol) { return this.delete(`/api/coins/${symbol}`); }
}
const api = new ApiClient();
const $ = (selector) => document.querySelector(selector);
const escapeHtml = (value) => String(value ?? '-').replace(/[&<>\'"]/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' }[char]));
const UI_TIME_ZONE = 'America/Santo_Domingo';
function dateParts(value, withTime = false) {
  const date = value instanceof Date ? value : new Date(value);
  if (!Number.isFinite(date.getTime())) return null;
  const options = withTime
    ? { timeZone: UI_TIME_ZONE, year: '2-digit', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }
    : { timeZone: UI_TIME_ZONE, year: '2-digit', month: '2-digit', day: '2-digit' };
  return Object.fromEntries(new Intl.DateTimeFormat('en-GB', options).formatToParts(date).map(part => [part.type, part.value]));
}
function formatDate(value) { const p = dateParts(value); return p ? `${p.day}/${p.month}/${p.year}` : '—'; }
function formatDateTime(value) { const p = dateParts(value, true); return p ? `${p.day}/${p.month}/${p.year} ${p.hour}:${p.minute}` : '—'; }
function formatNumber(value, digits = 2) {
  const number = Number(value);
  return value == null || !Number.isFinite(number) ? '—' : new Intl.NumberFormat('en-US', { minimumFractionDigits: digits, maximumFractionDigits: digits }).format(number);
}
function formatUsdt(value) { return value == null || !Number.isFinite(Number(value)) ? '—' : `$${formatNumber(value, 2)}`; }
window.ASPLEFormat = { formatDate, formatDateTime, formatNumber, formatUsdt, tooltip: content => tooltip(content) };
const percent = (value, digits = 1) => value == null ? '\u2014' : Number.isFinite(Number(value)) ? `${(Number(value) * 100).toFixed(digits)}%` : '\u2014';
const money = (value, digits = 2) => value == null || !Number.isFinite(Number(value)) ? '\u2014' : `$${formatNumber(value, digits)}`;
function formatIndicator(value) {
  const formatted = formatPrice(value);
  return Math.abs(Number(value)) < 1 ? formatted.replace(/(\.\d*?[1-9])0+$/, '$1') : formatted;
}
function formatPrice(value) {
  if (value == null || value === '') return '\u2014';
  const number = Number(value);
  if (!Number.isFinite(number)) return '\u2014';
  if (number === 0) return '0';
  if (Math.abs(number) >= 1) {
    const [integer, rawDecimals] = Math.abs(number).toFixed(4).split('.');
    const decimals = rawDecimals.replace(/0+$/, '').padEnd(2, '0');
    const grouped = integer.replace(/\B(?=(\d{3})+(?!\d))/g, ',');
    return `${number < 0 ? '-' : ''}${grouped}.${decimals}`;
  }
  const precise = number.toPrecision(4);
  if (!/[eE]/.test(precise)) return precise;
  const [mantissa, exponentText] = precise.toLowerCase().split('e');
  const exponent = Number(exponentText), sign = mantissa.startsWith('-') ? '-' : '';
  const unsigned = mantissa.replace('-', ''), digits = unsigned.replace('.', ''), point = unsigned.indexOf('.') + exponent;
  return sign + (point <= 0 ? '0.' + '0'.repeat(-point) + digits : point >= digits.length ? digits + '0'.repeat(point - digits.length) : digits.slice(0, point) + '.' + digits.slice(point));
}
const GRID_SYMBOL_FALLBACK = ['XRPUSDT', 'BTCUSDT', 'ETHUSDT', 'SOLUSDT'];
function buildGridSymbolOptions(coins, selectedSymbol) {
  const symbols = [...new Set((Array.isArray(coins) ? coins : []).map(item => String(typeof item === 'string' ? item : item?.symbol || '').trim().toUpperCase()).filter(Boolean))];
  const selected = symbols.includes(selectedSymbol) ? selectedSymbol : (symbols.includes('XRPUSDT') ? 'XRPUSDT' : symbols[0] || '');
  const options = symbols.map(symbol => `<option value="${escapeHtml(symbol)}"${symbol === selected ? ' selected' : ''}>${escapeHtml(symbol.replace(/USDT$/, '/USDT'))}</option>`).join('');
  return { symbols, selected, options };
}
function advanceApiHealth(state, succeeded, now = Date.now()) {
  const failures = succeeded ? 0 : Math.min(3, Number(state?.failures || 0) + 1);
  return { failures, status: succeeded ? 'online' : failures >= 3 ? 'offline' : 'slow', lastSuccessAt: succeeded ? now : (state?.lastSuccessAt ?? null) };
}
function timeSinceSuccess(lastSuccessAt, now = Date.now()) {
  if (lastSuccessAt == null) return 'sin un chequeo exitoso previo';
  const seconds = Math.max(0, Math.floor((now - lastSuccessAt) / 1000));
  return seconds < 60 ? `hace ${seconds} s` : seconds < 3600 ? `hace ${Math.floor(seconds / 60)} min` : `hace ${Math.floor(seconds / 3600)} h ${Math.floor(seconds % 3600 / 60)} min`;
}
const signalClass = signal => signal === 'ALCISTA' ? 'bullish' : signal === 'BAJISTA' ? 'bearish' : 'neutral';
function cache(data) { APP.lastData = { ...APP.lastData, ...data }; localStorage.setItem(APP.cacheKey, JSON.stringify(APP.lastData)); }
function restoreCache() { try { APP.lastData = JSON.parse(localStorage.getItem(APP.cacheKey) || '{}'); } catch (_) { APP.lastData = {}; } }
function setOffline(offline) { $('#offline-banner').classList.toggle('hidden', !offline); }
function apiHealthLabel(state) { return state.status === 'checking' ? 'Comprobando…' : state.status === 'offline' ? 'API Offline' : state.status === 'slow' ? 'API lenta' : 'API Online'; }
function renderApiHealth() {
  const state = APP.apiHealthState, chip = $('#api-chip'), text = chip.querySelector('span');
  setOffline(state.status === 'offline');
  chip.classList.toggle('online', state.status === 'online'); chip.classList.toggle('offline', state.status === 'offline'); chip.classList.toggle('slow', state.status === 'slow');
  text.textContent = apiHealthLabel(state);
  if (state.status === 'offline') $('#offline-banner').textContent = `API desconectada; \u00faltimo chequeo exitoso ${timeSinceSuccess(state.lastSuccessAt)}`;
}
async function checkApiStatus() {
  try { await api.status(); APP.apiHealthState = advanceApiHealth(APP.apiHealthState, true); }
  catch (_) { APP.apiHealthState = advanceApiHealth(APP.apiHealthState, false); }
  renderApiHealth();
}
function panelLoadError(panel, error) {
  const reason = error?.name === 'AbortError' ? 'tiempo de espera agotado' : (error?.message || 'error no especificado');
  return `No se pudo cargar ${panel}: ${reason}. ${APP.apiHealthState.status === 'online' ? 'La API responde; puedes reintentar.' : 'Puedes reintentar cuando el servicio responda.'}`;
}
async function loadGridSymbols() {
  const select = $('#grid-symbol'), note = $('#grid-symbol-note');
  if (!select || !note) return;
  const previous = select.value || APP.gridSymbol || 'XRPUSDT';
  try {
    const built = buildGridSymbolOptions(await api.coinsList(), previous);
    select.innerHTML = built.options; select.value = built.selected; APP.gridSymbol = built.selected; note.textContent = '';
  } catch (_) {
    const built = buildGridSymbolOptions(GRID_SYMBOL_FALLBACK, previous);
    select.innerHTML = built.options; select.value = built.selected; APP.gridSymbol = built.selected;
    note.textContent = 'lista de respaldo: no se pudo leer la Coin Registry';
  }
}
async function loadDashboardSymbols() {
  const select = $('#dashboard-symbol');
  if (!select) return;
  const previous = select.value || APP.currentSymbol || 'XRPUSDT';
  try {
    const built = buildGridSymbolOptions(await api.coinsList(), previous);
    select.innerHTML = built.options;
    select.value = built.selected || 'XRPUSDT';
    APP.currentSymbol = select.value;
  } catch (_) {
    const built = buildGridSymbolOptions(GRID_SYMBOL_FALLBACK, previous);
    select.innerHTML = built.options;
    select.value = built.selected || 'XRPUSDT';
    APP.currentSymbol = select.value;
  }
}
function clearChart() { if (APP.candleSeries) { APP.candleSeries.setData([]); APP.candleSeries = null; } if (APP.chart) { APP.chart.remove(); APP.chart = null; } $('#price-chart').innerHTML = ''; }
function renderModelCards(consensus, status, rows = []) {
  if (consensus.model_available === false) { $('#model-cards').innerHTML = ''; return; }
  const byModel = Object.fromEntries((status?.models || []).map(item => [item.model_name, item]));
  const latest = Object.fromEntries((rows || []).map(row => [row.model_name, row]));
  const keys = ['model_a', 'model_b', 'model_c'].filter(key => Boolean(byModel[key]));
  $('#model-cards').innerHTML = keys.map(key => {
    const model = byModel[key] || {};
    const result = key === 'model_a' ? (consensus[key] || {}) : (latest[key] || {});
    const validation = model.validation_status === 'not_validated'
      ? `<span class="validation-chip" tabindex="0">No validado ${tooltip('Se muestra para comparar; no se usa para decidir ni operar.')}</span>` : '';
    if (model.available === false) return `<div class="card model-card ${key.replace('_','-')}"><div class="model-name"><h3>${escapeHtml(model.display_name || getModelName(key))}</h3>${validation}</div><p class="muted">No disponible: ${escapeHtml(model.unavailable_reason || 'modelo no cargado')}</p></div>`;
    if (!result.probability_up && result.probability_up !== 0) return `<div class="card model-card ${key.replace('_','-')}"><div class="model-name"><h3>${escapeHtml(model.display_name || getModelName(key))}</h3>${validation}</div><p class="muted">Sin predicci\u00f3n registrada todav\u00eda</p></div>`;
    const css = key.replace('_', '-');
    return `<div class="card model-card ${css}"><div class="model-name"><h3>${escapeHtml(model.display_name || getModelName(key))}</h3>${validation}<span class="accuracy">${percent(model.accuracy_30d)}</span></div><div class="model-meta"><span class="signal ${signalClass(result.signal)}">${escapeHtml(result.signal)}</span><span class="mono muted">${percent(result.probability_up)}</span></div><div class="progress"><i style="width:${Math.max(0, Math.min(100, Number(result.probability_up || 0) * 100))}%"></i></div><div class="features">${(result.top_features || []).slice(0, 3).map(feature => `<span class="feature">${escapeHtml(feature[0])}</span>`).join('')}</div></div>`;
  }).join('');
}
function renderConsensus(data) { if (data.model_available === false) { $('#consensus-card').innerHTML = '<div class="eyebrow">MODELO NO DISPONIBLE</div><p>Sin modelo entrenado para esta moneda/temporalidad</p>'; $('#chart-signal').className = 'signal neutral'; $('#chart-signal').textContent = '—'; return; } $('#consensus-card').innerHTML = `<div class="eyebrow">${Object.keys(data.weights || {}).length > 1 ? 'CONSENSO' : 'PROBABILIDAD DEL MODELO A'}</div><div class="big-prob">${percent(data.consensus_probability_up, 2)}</div><div class="agreement">${escapeHtml(data.agreement_count)}/${Object.keys(data.weights || {}).length || 1} modelos coinciden</div><div class="signal ${signalClass(data.consensus_signal)}" style="margin-top:13px">${escapeHtml(data.consensus_signal)}</div>`; $('#chart-signal').className = `signal ${signalClass(data.consensus_signal)}`; $('#chart-signal').textContent = data.consensus_signal; }
function renderShadowStatus(data) { const status = data.kill_status || 'pending'; const color = status === 'pass' ? 'var(--green, #3FB950)' : status === 'fail' ? 'var(--red, #F85149)' : 'var(--muted, #8B949E)'; const empty = Number(data.n_verified_total || 0) === 0; const count = Number(data.n_nonoverlap || 0); const missing = Math.max(0, 40 - count); const detail = empty ? '<p>Sin predicciones verificadas aún</p>' : `<p>Precisión: ${percent(data.precision_nonoverlap)} vs base ${percent(data.base_rate)}</p><p>Retorno medio: ${percent(data.mean_return_nonoverlap, 2)} vs 0.20%</p>`; const needSignals = status === 'pending' ? `<p class="muted">Faltan ${missing} señales ALCISTA no solapadas; hoy todas las predicciones son NEUTRAL por estar bajo el umbral 0,60.</p>` : ''; $('#shadow-status').innerHTML = `<div class="card-title"><h2>Modo sombra — Model A · XRPUSDT 1h</h2><strong style="color:${color}">${escapeHtml(status.toUpperCase())}</strong></div><p>${count} / 40 señales no solapadas</p>${needSignals}${detail}<p class="muted">Señal en evaluación. No usar para operar.</p>`; }
function renderRecent(rows) { $('#recent-predictions').innerHTML = (rows || []).slice(0, 10).map(row => `<div class="prediction-row"><span><b>${escapeHtml(row.symbol)}</b><br><span class="muted">${escapeHtml(getModelName(row.model_name))} &middot; ${escapeHtml(formatDate(row.predicted_at))}</span></span><span class="signal ${signalClass(row.signal)}">${escapeHtml(row.signal)}</span><span class="prob">${percent(row.probability_up)}</span><span class="${row.is_verified ? 'verified' : 'pending'}">${row.is_verified ? (row.was_correct === null ? 'pendiente' : row.was_correct ? 'OK' : 'X') : 'pendiente'}</span></div>`).join('') || '<p class="muted">Sin predicciones registradas.</p>'; }
const CHART_INTERVAL_SECONDS = { '1h': 3600, '4h': 14400, '12h': 43200, '1d': 86400, '1w': 604800 };
function chartDate(time) {
  if (typeof time === 'number') return new Date(time * 1000);
  if (time && typeof time === 'object' && time.year) return new Date(Date.UTC(time.year, time.month - 1, time.day));
  return new Date(NaN);
}
function chartTickLabel(time, intraday) {
  const date = chartDate(time);
  if (!intraday) return formatDate(date);
  if (!Number.isFinite(date.getTime())) return '\u2014';
  return `${String(date.getDate()).padStart(2,'0')}/${String(date.getMonth()+1).padStart(2,'0')} ${String(date.getHours()).padStart(2,'0')}:${String(date.getMinutes()).padStart(2,'0')}`;
}
function fillCandleTimeGaps(candles, interval) {
  const step = CHART_INTERVAL_SECONDS[interval] || 3600;
  const rows = [];
  for (const candle of candles || []) {
    const previous = rows.length ? Number(rows[rows.length - 1].time) : null;
    const current = Number(candle.time);
    if (previous != null && Number.isFinite(current) && current > previous + step) {
      const slots = Math.min(500, Math.floor((current - previous) / step) - 1);
      for (let index = 1; index <= slots; index++) rows.push({ time: previous + index * step });
    }
    rows.push(candle);
  }
  return rows;
}
function applyVolatilityOverlay() {
  if (!APP.candleSeries) return;
  (APP.volOverlayLines || []).forEach(line => APP.candleSeries.removePriceLine(line));
  APP.volOverlayLines = [];
  if (!$('#vol-overlay-enabled')?.checked || APP.currentSymbol !== 'XRPUSDT') return;
  const horizon = Number($('#vol-overlay-horizon')?.value || 4);
  const forecast = APP.volForecast?.forecasts?.find(row => Number(row.horizon_h) === horizon);
  if (!forecast || !Array.isArray(forecast.range_1sigma) || !Array.isArray(forecast.range_2sigma)) return;
  for (const [sigma, range, color] of [[1, forecast.range_1sigma, '#d29922'], [2, forecast.range_2sigma, '#bc8cff']]) {
    for (const [side, price] of [['m\u00ednimo', range[0]], ['m\u00e1ximo', range[1]]]) {
      if (Number.isFinite(Number(price))) APP.volOverlayLines.push(APP.candleSeries.createPriceLine({ price: Number(price), color, lineWidth: 1, lineStyle: 2, title: `${horizon}h ${sigma}\u03c3 ${side}` }));
    }
  }
}
function renderChart(candles, consensus, interval) {
  clearChart();
  const box = $('#price-chart');
  if (!window.LightweightCharts) { box.innerHTML = '<p class="muted">Chart no disponible.</p>'; return; }
  const isIntraday = ['1h', '4h', '12h'].includes((interval || '4h').toLowerCase());
  APP.chart = LightweightCharts.createChart(box, { layout: { background: { color: '#161B22' }, textColor: '#8B949E' }, grid: { vertLines: { color: '#21262D' }, horzLines: { color: '#21262D' } }, rightPriceScale: { borderColor: '#30363D' }, localization: { timeFormatter: time => chartTickLabel(time, isIntraday) }, timeScale: { borderColor: '#30363D', timeVisible: isIntraday, secondsVisible: false, tickMarkFormatter: time => chartTickLabel(time, isIntraday) } });
  const latestPrice = Number(candles[candles.length - 1]?.close);
  const precision = latestPrice > 0 && latestPrice < 1 ? Math.min(12, Math.max(2, Math.ceil(-Math.log10(latestPrice)) + 3)) : 4;
  APP.candleSeries = APP.chart.addCandlestickSeries({ priceFormat: { type: 'price', precision, minMove: Math.pow(10, -precision) }, upColor: '#3FB950', downColor: '#F85149', borderVisible: false, wickUpColor: '#3FB950', wickDownColor: '#F85149' });
  if (candles.length) {
    APP.candleSeries.setData(fillCandleTimeGaps(candles, (interval || '').toLowerCase()));
    const last = candles[candles.length - 1];
    if (consensus.consensus_signal === 'ALCISTA') APP.candleSeries.setMarkers([{ time: last.time, position: 'belowBar', color: '#3FB950', shape: 'arrowUp', text: consensus.consensus_signal }]);
    APP.chart.timeScale().fitContent(); APP.chart.timeScale().scrollToRealTime();
  }
  applyVolatilityOverlay();
}
async function loadDashboardV2a() { const symbol = $('#dashboard-symbol').value; const interval = $('#dashboard-interval').value.toLowerCase(); APP.currentSymbol = symbol; APP.currentInterval = interval; clearChart(); try { const [candles, consensus, rows, status, shadow] = await Promise.all([api.candles(symbol, interval), api.consensus(symbol, interval), api.latest(symbol, interval, 10), api.modelsStatus(), api.shadowStatus()]); cache({ candles, consensus, rows, status, shadow }); renderModelCards(consensus, status, rows); renderConsensus(consensus); renderShadowStatus(shadow); renderRecent(rows); renderChart(candles, consensus, interval); if (consensus.indicators) { $('#indicator-rsi').textContent = Number(consensus.indicators.rsi_14).toFixed(2); $('#indicator-macd').textContent = formatIndicator(consensus.indicators.macd); $('#indicator-atr').textContent = formatIndicator(consensus.indicators.atr_14); } $('#dashboard-updated').textContent = `Actualizado ${formatDateTime(new Date())}`; $('#chart-caption').textContent = `${symbol} - ${interval.toUpperCase()} - ${consensus.persisted === false ? 'vista previa (no registrada)' : 'consenso en vivo'}`; } catch (error) { $('#chart-caption').textContent = panelLoadError('Dashboard', error); if (APP.lastData.consensus) { renderModelCards(APP.lastData.consensus, APP.lastData.status, APP.lastData.rows); renderConsensus(APP.lastData.consensus); renderRecent(APP.lastData.rows); renderChart(APP.lastData.candles || [], APP.lastData.consensus, APP.currentInterval); } } }
async function refreshDashboard() { const button = $('#refresh-dashboard'); const original = button.textContent; button.disabled = true; button.textContent = 'Actualizando...'; try { await loadDashboard(); } finally { button.disabled = false; button.textContent = original; } }
function volatilityDetail(row) {
  let market = row.market_condition || {};
  if (typeof market === 'string') { try { market = JSON.parse(market); } catch (_) { market = {}; } }
  const snapshot = market.volatility_4h;
  if (!snapshot?.range_1sigma || !snapshot?.range_2sigma) return '\u2014 (sin pron\u00f3stico guardado al momento)';
  const one = snapshot.range_1sigma, two = snapshot.range_2sigma;
  let result = '';
  if (row.is_verified) {
    const price = Number(row.price_at_verification);
    result = Number.isFinite(price) ? ` \u00b7 ${price >= Number(one[0]) && price <= Number(one[1]) ? '\u2713 dentro de 1\u03c3' : '\u2717 fuera de 1\u03c3'}` : '';
  }
  return `Rango 4h 1\u03c3: ${formatPrice(one[0])} \u2013 ${formatPrice(one[1])} \u00b7 2\u03c3: ${formatPrice(two[0])} \u2013 ${formatPrice(two[1])}${result}`;
}
function renderBattle(status, conditionSets, history) { const models = status.models || []; const scores = models.map(model => { const verified = model.verified_count ?? model.verified_predictions ?? 0; return `<div class="card score-card"><h3>${modelHeader(model.model_name)} ${validationStatusTag(model)}</h3><strong>${model.available === false ? 'No disponible' : formatAccuracy(model)}</strong><p class="muted">${model.total_predictions || 0} predicciones<br><small>${verified} de ${model.total_predictions || 0} verificadas</small></p></div>`; }).join(''); const conditions = ['rsi_oversold', 'high_volume', 'strong_trend', 'ranging_market', 'post_macd_cross']; const lookup = Object.fromEntries(Object.entries(conditionSets).flatMap(([model, rows]) => rows.map(row => [`${row.condition_name}:${model}`, row]))); const conditionRows = conditions.map(condition => { const entries = models.map(model => lookup[`${condition}:${model.model_name}`] || {}); const values = entries.map(entry => entry.accuracy); const best = Math.max(...values.map(value => Number(value ?? -1))); const label = `<span class="condition-label">${escapeHtml(CONDITION_NAMES[condition] || condition)} ${tooltip(CONDITION_TOOLTIPS[condition] || '')}</span>`; return `<tr><td>${label}</td>${entries.map(entry => { const verified = entry.verified_count ?? entry.verified_predictions ?? 0; return `<td class="${entry.accuracy !== null && entry.accuracy !== undefined && entry.accuracy === best ? 'winner' : ''}">${entry.accuracy === null || entry.accuracy === undefined || !verified ? '-' : percent(entry.accuracy)}<small class="condition-count">${verified} de ${entry.total_predictions || 0} verificadas</small></td>`; }).join('')}</tr>`; }).join(''); const historyRows = (history || []).slice(0, 30).map(row => `<tr><td>${escapeHtml(formatDateTime(row.predicted_at))}</td><td>${escapeHtml(row.symbol)}</td><td>${escapeHtml(getModelName(row.model_name))}</td><td><span class="signal ${signalClass(row.signal)}">${escapeHtml(row.signal)}</span></td><td class="mono">${percent(row.probability_up)}</td><td>${row.is_verified ? (row.was_correct === null ? 'NEUTRAL' : row.was_correct ? 'ACERTO' : 'FALLO') : 'PENDIENTE'}</td><td class="muted">${escapeHtml(volatilityDetail(row))}</td></tr>`).join(''); $('#battle-content').innerHTML = `<div class="battle-grid">${scores}</div><div class="card table-card"><table class="data-table"><thead><tr><th>Condicion</th>${models.map(model => `<th>${modelHeader(model.model_name)} ${validationStatusTag(model)}</th>`).join('')}</tr></thead><tbody>${conditionRows}</tbody></table></div><div class="card table-card"><table class="data-table"><thead><tr><th>Fecha</th><th>S\u00edmbolo</th><th>Modelo</th><th>Se\u00f1al</th><th>Prob.</th><th>Resultado</th><th>Detalle</th></tr></thead><tbody>${historyRows || '<tr><td colspan="7">Sin historial.</td></tr>'}</tbody></table></div>`; }
async function loadBattleV2a() { try { const status = await api.modelsStatus(); const names = (status.models || []).map(model => model.model_name); const conditionRows = await Promise.all(names.map(name => api.conditions(name))); const history = await api.history(null, null, 30); renderBattle(status, Object.fromEntries(names.map((name, index) => [name, conditionRows[index]])), history); } catch (error) { $('#battle-content').innerHTML = `<p class=\"muted\">${escapeHtml(panelLoadError('Battle de Modelos', error))}</p>`; } }
function gridSvg(data) {
  const floor=Number(data.recommended_floor), ceiling=Number(data.recommended_ceiling), current=Number(data.current_price), count=Number(data.suggested_grids);
  const y=value=>170-((value-floor)/(ceiling-floor||1))*140;
  const compact=value=>Math.abs(value)>0&&Math.abs(value)<.0001?value.toExponential(2):formatPrice(value);
  const labelIndexes=new Set(Array.from({length:Math.min(6,count+1)},(_,i)=>Math.round(i*count/Math.min(5,count))));
  let lines=''; for(let index=0;index<=count;index++){const value=floor+(ceiling-floor)*index/count;lines+=`<line x1="110" x2="590" y1="${y(value)}" y2="${y(value)}" stroke="#30363D"/>${labelIndexes.has(index)?`<text x="596" y="${y(value)+4}" fill="#9DA7B3" font-size="10">${escapeHtml(compact(value))}</text>`:''}`;}
  return `<svg class="grid-svg" viewBox="0 0 680 220" role="img" aria-label="Rango de grid con ${count} niveles"><text x="8" y="34" fill="#F85149" font-size="11">Techo ${escapeHtml(formatPrice(ceiling))}</text><text x="8" y="174" fill="#3FB950" font-size="11">Piso ${escapeHtml(formatPrice(floor))}</text>${lines}<line x1="110" x2="540" y1="${y(floor)}" y2="${y(floor)}" stroke="#3FB950" stroke-width="2"/><line x1="110" x2="540" y1="${y(ceiling)}" y2="${y(ceiling)}" stroke="#F85149" stroke-width="2"/><circle cx="320" cy="${y(current)}" r="5" fill="#2F81F7"/><text x="110" y="204" fill="#9DA7B3" font-size="11">Precio actual ${escapeHtml(formatPrice(current))} (mercado p\u00fablico) \u00b7 ${count} niveles \u00b7 escala lineal USDT</text></svg>`;
}
window.gridSvg = gridSvg;
function renderGridV2a(data) {
  const analysis=data.analysis||{}, prediction=data.prediction_signal, signal=prediction?.consensus_signal;
  const simulations=data.simulations?.strategies||{};
  const simCard=(name,row)=>`<div><b>${name}</b>${row?.unavailable?`<p>Simulaci\u00f3n no disponible para ${escapeHtml(data.symbol)}: ${escapeHtml(row.unavailable)}</p>`:`<p>Equity final estimada: ${money(Number(data.capital)+Number(row?.pnl_total_net_usdt||0),2)} \xb7 ciclos: ${formatNumber(row?.cycles_completed??0,0)}</p>`}</div>`;
  const rangeWarning=data.range_warning?`<p class="scanner-warning">El rango ${Number(data.range_pct).toFixed(1)}% supera el tope ${Number(data.risk?.max_range_pct).toFixed(0)}% del perfil elegido.</p>`:'';
  const cellWarning=data.min_cell_warning?`<p class="scanner-warning">${escapeHtml(data.min_cell_warning)}</p>`:'';
  $('#grid-result').className='grid-result card';
  $('#grid-result').innerHTML=`<div class="grid-header"><div><p class="eyebrow">RECOMENDACIÓN ${escapeHtml(data.symbol)}</p><div class="price-large">${formatPrice(data.current_price)}</div><span class="muted">Rango ${Number(data.range_pct).toFixed(2)}% · perfil ${escapeHtml(data.risk?.label||'')}</span></div><span class="signal ${signalClass(signal)}">${signal?escapeHtml(signal):'<span title="Hace falta un modelo entrenado y vigilancia de esta moneda; hoy solo XRP 1 h y sin evidencia de ventaja predictiva.">Direcci\u00f3n: sin modelo para esta moneda (no se usa; el an\u00e1lisis se basa en volatilidad)</span>'}</span></div><div class="level-row"><div class="level floor"><span>Piso recomendado</span><strong>${formatPrice(data.recommended_floor)}</strong></div><div class="level ceiling"><span>Techo recomendado</span><strong>${formatPrice(data.recommended_ceiling)}</strong></div></div>${gridSvg(data)}<div class="card-title"><h2>Estructura derivada</h2><button id="copy-grid" class="button secondary">Copiar configuración</button></div><div class="indicator-strip"><div><span>NIVELES</span><b>${data.suggested_grids}</b></div><div><span>SEPARACIÓN</span><b>${Number(data.spacing_pct).toFixed(3)}%</b></div><div><span>CAPITAL / CELDA</span><b>${money(data.capital_per_grid,2)}</b></div><div><span>MARGEN TRAS COMISIONES / CICLO</span><b>${Number(data.edge_gross_pct).toFixed(3)}% · ${money(data.net_per_cycle_usdt,2)}</b></div></div><section class="advisor-cascade"><h3>Margen objetivo ${Number(data.margin_target_pct).toFixed(2)}% ${data.target_met?'alcanzable':'no alcanzado'}</h3><p>Separación ${Number(data.spacing_pct).toFixed(3)}% − comisiones ${Number(data.fee_pct*2).toFixed(3)}% = margen tras comisiones ${Number(data.edge_gross_pct).toFixed(3)}% por ciclo. Polvo estimado ${Number(data.dust_estimate_pct).toFixed(3)}%; neto teórico ${Number(data.net_margin_pct).toFixed(3)}% solo informativo.</p><p>Niveles: ${data.suggested_grids}; ciclos teóricos para la meta: ${data.estimated_cycles_to_target??'no disponible'}. Margen por ciclo tras comisiones: ${money(data.net_per_cycle_usdt,2)}; neto estimado tras polvo: ${money(data.net_per_cycle_after_dust_usdt,2)} (informativo).</p></section>${rangeWarning}${cellWarning}<section class="advisor-range-risk"><h3>Probabilidad estimada de tocar el rango</h3><p>Fuente de volatilidad: ${escapeHtml(data.range_risk?.source||'no disponible')}. ${escapeHtml(data.range_risk?.disclaimer||'')}</p><table class="data-table"><thead><tr><th>Horizonte</th><th>Tocar piso</th><th>Tocar techo</th><th>Salir (cota sup.)</th></tr></thead><tbody>${Object.entries(data.range_risk?.horizons||{}).map(([hours,r])=>`<tr><td>${hours} h</td><td>${percent(r.touch_floor)}</td><td>${percent(r.touch_ceiling)}</td><td>${percent(r.exit_upper_bound)}</td></tr>`).join('')}</tbody></table></section><section class="advisor-risk"><p class="scanner-warning">${escapeHtml(data.range_position_warning||'')}</p><h3>Perfil ${escapeHtml(data.risk?.label||'')}</h3><p>${escapeHtml(data.risk?.meaning||'')}</p><p>Capital en compras bajo el precio: ${Number(data.risk?.capital_below_price_pct||0).toFixed(1)}%. Pérdida no realizada estimada al piso: ${money(data.risk?.unrealized_loss_at_floor_usdt,2)}.</p></section><section class="advisor-simulation"><h3>Comparación con velas históricas</h3><p>${escapeHtml(data.simulations?.label||'histórico, no promesa de resultado')}</p><div class="advisor-simulation-grid">${simCard('Simple',simulations.simple)}${simCard('Smart',simulations.smart)}</div></section><h3>Por qué estos niveles</h3><div class="why">Soporte: <b>${formatPrice(analysis.main_support)}</b> (${analysis.support_touches} toques) · Resistencia: <b>${formatPrice(analysis.main_resistance)}</b> (${analysis.resistance_touches} toques) · ATR: <b>${formatPrice(analysis.atr)}</b></div><p class="disclaimer">${escapeHtml(data.disclaimer)}</p><button id="grid-create-from-advisor" class="button primary">Crear grid desde esta recomendación</button>`;
  $('#copy-grid').onclick=()=>{const text=`Grid ${data.symbol}\nPiso ${data.recommended_floor} · Techo ${data.recommended_ceiling}\nNiveles ${data.suggested_grids} · Capital por celda ${money(data.capital_per_grid,2)}\nGenerado ${formatDateTime(new Date())}`;navigator.clipboard.writeText(text);};
  $('#grid-create-from-advisor').onclick=()=>{sessionStorage.setItem('asple-advisor-open',JSON.stringify({symbol:data.symbol,capital:$('#grid-capital').value,strategy:'simple',range_low:data.recommended_floor,range_high:data.recommended_ceiling,n_levels:data.suggested_grids,spacing_pct:data.spacing_pct,margin_target_pct:data.margin_target_pct}));location.hash='#scanner';};
}
function route() { const name = (location.hash || '#dashboard').slice(1); const base = name.split('/')[0]; $('#testnet-notice')?.classList.toggle('hidden', !['dashboard','scanner','grid','grids','cuenta'].includes(base)); ['dashboard', 'battle', 'grid', 'scanner', 'coins', 'grids', 'cuenta'].forEach(screen => { $(`#screen-${screen}`).classList.toggle('hidden', screen !== base); document.querySelector(`[data-route=\"${screen}\"]`).classList.toggle('active', screen === base); }); if (name === 'dashboard') loadDashboard(); if (name === 'battle') loadBattle(); if (base === 'grid') loadGridSymbols(); if (name === 'coins') loadCoins(); if (base === 'grids') window.loadGridsScreen?.(name); if (base === 'scanner') window.loadScannerScreen?.(); if (name === 'cuenta') window.loadCuenta?.(); }
document.addEventListener('DOMContentLoaded', () => { restoreCache(); document.addEventListener('submit', event => { if (event.target?.id === 'grid-form') loadGrid(event); }, true); $('#grid-symbol').addEventListener('change', event => { APP.gridSymbol = event.target.value; }); $('#dashboard-symbol').addEventListener('change', loadDashboard); $('#dashboard-interval').addEventListener('change', loadDashboard); $('#refresh-dashboard').addEventListener('click', refreshDashboard); window.addEventListener('hashchange', route); route(); checkApiStatus(); setInterval(checkApiStatus, 30000); setInterval(() => { if ((location.hash || '#dashboard') === '#dashboard') loadDashboard(); }, 60000); });

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
  if (error) { const symbol=APP.currentSymbol||'XRPUSDT'; host.innerHTML=symbol==='XRPUSDT'?`<div class="card-title"><h2>Volatilidad esperada \u2014 XRPUSDT</h2></div><p class="muted">${escapeHtml(error.status===503?'Modelos de volatilidad no entrenados':'Pron\u00f3stico de volatilidad no disponible')}</p>`:`<div class="card-title"><h2>Sin modelo de volatilidad para ${escapeHtml(symbol)}</h2></div><p class="muted">Volatilidad realizada no disponible: ${escapeHtml(error.message||'error de consulta')}</p>`; return; }
  if (!data) { host.innerHTML = `<div class="card-title"><h2>Volatilidad esperada \u2014 ${escapeHtml(APP.currentSymbol||'XRPUSDT')}</h2></div><p class="muted">Pron\u00f3stico de volatilidad no disponible</p>`; return; }
  if(data.source){host.innerHTML=`<div class="card-title"><h2>Sin modelo de volatilidad para ${escapeHtml(data.symbol||APP.currentSymbol)}</h2></div><p>Fuente: ${escapeHtml(data.source)}</p><p>Rango realizado 2\u03c3: ${data.range_2sigma?`${formatPrice(data.range_2sigma[0])} \u2013 ${formatPrice(data.range_2sigma[1])}`:'no disponible'}</p>`;return;}
  const rows = VOL_HORIZONS_UI.map(h => data.forecasts?.find(row => Number(row.horizon_h) === h)).map((row, i) => row ? `<tr><td>${VOL_HORIZONS_UI[i]}h</td><td>±${volNumber(row.move_1sigma_pct)}%</td><td>${formatPrice(row.range_1sigma?.[0])} – ${formatPrice(row.range_1sigma?.[1])}</td><td>${formatPrice(row.range_2sigma?.[0])} – ${formatPrice(row.range_2sigma?.[1])}</td><td>${escapeHtml(row.champion || '—')}</td></tr>` : `<tr><td>${VOL_HORIZONS_UI[i]}h</td><td>—</td><td>—</td><td>—</td><td>—</td></tr>`).join('');
  const stale = data.forecasts?.some(row => row.stale);
  host.innerHTML = `<div class="card-title"><div><h2>Volatilidad esperada — XRP</h2><span class="muted">Precio de referencia: ${formatPrice(data.price)}</span><div class="vol-overlay-controls"><label>Horizonte <select id="vol-overlay-horizon"><option value="1">1h</option><option value="2">2h</option><option value="4" selected>4h</option><option value="24">24h</option></select></label><label><input id="vol-overlay-enabled" type="checkbox"> Dibujar rango 1&sigma;/2&sigma;</label></div></div><span class="vol-regime ${volatilityRegimeClass(data.regime)}">${escapeHtml(data.regime || 'NORMAL')}</span></div>${stale ? '<p class="vol-stale">Pronóstico desactualizado</p>' : ''}<div class="table-card vol-table-wrap"><table class="data-table"><thead><tr><th>Horizonte</th><th>Movimiento típico</th><th>Rango 1σ</th><th>Rango 2σ</th><th>Campeón</th></tr></thead><tbody>${rows}</tbody></table></div><p class="vol-footnote">Movimiento típico = 1 desviación (~68%). Rango 2σ ≈ 95%.</p>`;
}
async function loadVolDashboard() { const symbol=APP.currentSymbol||'XRPUSDT'; try { const data = symbol==='XRPUSDT' ? await volGet('/api/volatility/forecast', { symbol }) : await volGet('/api/grid/volatility', { symbol, days:90 }); APP.volForecast = symbol==='XRPUSDT'?data:null; if(symbol==='XRPUSDT')cache({ volForecast: data }); renderVolDashboard(data); $('#vol-overlay-horizon')?.addEventListener('change', applyVolatilityOverlay); $('#vol-overlay-enabled')?.addEventListener('change', applyVolatilityOverlay); applyVolatilityOverlay(); } catch (error) { if(symbol==='XRPUSDT')renderVolDashboard(APP.lastData.volForecast || null, error); else renderVolDashboard(null,error); } }
async function loadDashboard() { await loadDashboardSymbols(); await loadDashboardV2bBase(); const shadow=$('#shadow-status'); shadow?.classList.toggle('muted-shadow',APP.currentSymbol!=='XRPUSDT'); await loadVolDashboard(); }
function renderVolBattleTable(data) {
  const rows = (data.models || []).map(model => { const champ = model.is_champion, liveR2 = model.r2_live == null ? (Number(model.n_verified || 0) < 30 ? '\u2014 (n<30)' : '\u2014') : volNumber(model.r2_live, 3); return `<tr class="${champ ? 'vol-champion-row' : ''}"><td>${escapeHtml(model.model_name || '\u2014')}${champ ? ' <span class="champion-star" title="Campe\u00f3n">&#9733;</span>' : ''}</td><td>${volNumber(model.r2_cal, 3)}</td><td>${volNumber(model.qlike_cal, 4)}</td><td>${volNumber(model.n_verified, 0)}</td><td>${liveR2}</td></tr>`; }).join('');
  const champion = (data.models || []).find(model => model.is_champion);
  const missing = Math.max(0, 30 - Number(champion?.n_verified || 0));
  $('#vol-battle-table').innerHTML = `<thead><tr><th>Modelo</th><th>R\u00b2 hist\u00f3rico</th><th>QLIKE hist\u00f3rico</th><th>Verificadas en vivo</th><th>R\u00b2 en vivo</th></tr></thead><tbody>${rows || '<tr><td colspan="5">Sin modelos disponibles</td></tr>'}</tbody><tfoot><tr><td colspan="5">Faltan ${missing} verificadas para R\u00b2 en vivo (m\u00ednimo 30).</td></tr></tfoot>`;
}
function renderVolHistory(rows, horizon = 4) {
  const container = $('#vol-history-chart'), empty = $('#vol-history-empty');
  if (APP.volHistoryChart) { APP.volHistoryChart.remove(); APP.volHistoryChart = null; } container.innerHTML = '';
  const items = Array.isArray(rows) ? rows : [];
  if (!window.LightweightCharts || !items.length) { empty.textContent = items.length ? 'Gráfico no disponible' : 'Aún no hay resultados verificados'; empty.classList.remove('hidden'); return; }
  APP.volHistoryChart = LightweightCharts.createChart(container, { height: 250, layout: { background: { color: 'transparent' }, textColor: '#8b949e' }, grid: { vertLines: { color: '#21262d' }, horzLines: { color: '#21262d' } }, localization: { timeFormatter: time => chartTickLabel(time, true) }, timeScale: { timeVisible: true, secondsVisible: false, tickMarkFormatter: time => chartTickLabel(time, true) }, rightPriceScale: { borderColor: '#30363d' } });
  const forecast = APP.volHistoryChart.addLineSeries({ color: '#2F81F7', lineWidth: 2, title: `Pron\u00f3stico ${horizon}h (%)`, priceFormat: { type: 'custom', formatter: value => `${value.toFixed(2)}%` } }), realized = APP.volHistoryChart.addLineSeries({ color: '#3FB950', lineWidth: 2, title: `Realizado ${horizon}h (%)`, priceFormat: { type: 'custom', formatter: value => `${value.toFixed(2)}%` } });
  const points = key => items.filter(row => row.forecast_at && row[key] != null).map(row => ({ time: Math.floor(new Date(row.forecast_at).getTime() / 1000), value: Number(row[key]) })).filter(row => Number.isFinite(row.time) && Number.isFinite(row.value)).sort((a,b) => a.time-b.time);
  forecast.setData(points('pred_vol_pct')); const actual = points('realized_vol_pct'); realized.setData(actual);
  if (!actual.length) { empty.textContent = 'Aún no hay resultados verificados'; empty.classList.remove('hidden'); } else empty.classList.add('hidden');
  APP.volHistoryChart.timeScale().fitContent();
}
function renderVolCoverage(data) { const host = $('#vol-coverage'); if (!host) return; const n = Number(data?.n || 0); if (n < 30) { host.textContent = `Muestra insuficiente para cobertura en vivo: ${n}/30 predicciones verificadas con rango guardado.`; return; } host.textContent = `Cobertura verificada 4h (n=${n}): 1\u03c3 ${percent(data.coverage_1sigma)} (esperado \u224868%) \u00b7 2\u03c3 ${percent(data.coverage_2sigma)} (esperado \u224895%).`; }
async function loadVolBattle() {
  const horizon = Number($('#vol-horizon-select').value);
  try { const [data, coverage] = await Promise.all([volGet('/api/volatility/battle', { symbol: 'XRPUSDT', horizon }), volGet('/api/predictions/volatility-coverage', { symbol: 'XRPUSDT', interval: '1h' })]); renderVolCoverage(coverage); renderVolBattleTable(data); const champion = (data.models || []).find(model => model.is_champion)?.model_name; if (!champion) { renderVolHistory([]); return; } const history = await volGet('/api/volatility/history', { symbol: 'XRPUSDT', horizon, model: champion, limit: 200 }); renderVolHistory(history, horizon); }
  catch (error) { $('#vol-battle-table').innerHTML = `<tbody><tr><td>${error.status === 503 ? 'Modelos de volatilidad no entrenados' : 'Battle de volatilidad no disponible'}</td></tr></tbody>`; renderVolHistory([]); }
}
async function loadBattle() { await loadBattleV2bBase(); await loadVolBattle(); }
function renderGrid(data, forecast = null) {
  renderGridV2bBase(data); const host = $('#grid-result');
  if (data.symbol !== 'XRPUSDT') { const range=forecast?.range_2sigma;host.insertAdjacentHTML('beforeend', `<section class="vol-grid-reference"><h3>Volatilidad realizada (24h)</h3><button type="button" class="button secondary" id="calculate-grid-volatility">Calcular volatilidad</button><p id="grid-volatility-status" role="status">${escapeHtml(forecast?.source||'volatilidad realizada')} \u00b7 rango 2\u03c3: ${range?`${formatPrice(range[0])} \u2013 ${formatPrice(range[1])}`:'no disponible'} \u00b7 ${Number(forecast?.samples||0)} retornos horarios \u00b7 ${escapeHtml(forecast?.calculated_at||new Date().toLocaleString())}</p><p class="vol-footnote">Desviaci\u00f3n de log-retornos de velas; no es una predicci\u00f3n de direcci\u00f3n.</p></section>`); return; }
  const row24 = forecast?.forecasts?.find(row => Number(row.horizon_h) === 24), row4 = forecast?.forecasts?.find(row => Number(row.horizon_h) === 4), range = row24?.range_2sigma;
  host.insertAdjacentHTML('beforeend', `<section class="vol-grid-reference"><h3>Rango sugerido por volatilidad (24h)</h3><button type="button" class="button secondary" id="calculate-grid-volatility">Calcular volatilidad</button><p id="grid-volatility-status" role="status">Pron\u00f3stico de modelos XRP \u00b7 rango 2\u03c3: ${range ? `${formatPrice(range[0])} \u2013 ${formatPrice(range[1])}` : '\u2014'} \u00b7 ${escapeHtml(row24?.made_at||new Date().toLocaleString())}</p><p>Movimiento t\u00edpico 4h: <strong>${row4?.move_1sigma_pct == null ? '\u2014' : `\u00b1${volNumber(row4.move_1sigma_pct)}%`}</strong> (referencia de espaciado)</p><p class="vol-footnote">Referencia. Cada nivel debe superar 0.20% para cubrir comisiones.</p></section>`);
}
async function loadSelectedGridVolatility(button) {
  const symbol = $('#grid-symbol').value;
  const status = $('#grid-volatility-status');
  if (!status) return;
  button.disabled = true; status.textContent = 'Calculando volatilidad...';
  try {
    const days = $('#grid-days').value;
    const result = symbol === 'XRPUSDT'
      ? await volGet('/api/volatility/forecast', {symbol})
      : await volGet('/api/grid/volatility', {symbol, days});
    const range = symbol === 'XRPUSDT'
      ? result.forecasts?.find(row => Number(row.horizon_h) === 24)?.range_2sigma
      : result.range_2sigma;
    const source = symbol === 'XRPUSDT' ? 'pron\u00f3stico de modelos XRP' : (result.source || 'volatilidad realizada');
    status.textContent = `${source} \u00b7 rango 2\u03c3: ${range ? `${formatPrice(range[0])} \u2013 ${formatPrice(range[1])}` : 'no disponible'}${symbol === 'XRPUSDT' ? '' : ` \u00b7 ${Number(result.samples || 0)} retornos horarios`} \u00b7 ${result.calculated_at||new Date().toLocaleString()}`;
  } catch (_) { status.textContent = 'No se pudo calcular la volatilidad; el an\u00e1lisis del grid sigue disponible.'; }
  finally { button.disabled = false; }
}
async function loadGrid(event) {
  event.preventDefault(); const params = { symbol: $('#grid-symbol').value, capital: $('#grid-capital').value, risk: $('#grid-risk').value, days: $('#grid-days').value, margin_target_pct: $('#grid-margin-target').value || .7 }, button = event.target.querySelector('button'); button.disabled = true; button.textContent = 'ANALIZANDO...';
  try { const data = await api.grid(params); let forecast = null; try { forecast = data.symbol === 'XRPUSDT' ? await volGet('/api/volatility/forecast', { symbol: 'XRPUSDT' }) : await volGet('/api/grid/volatility', { symbol: data.symbol, days: params.days }); } catch (_) {} cache({ grid: data, ...(forecast ? { volForecast: forecast } : {}) }); renderGrid(data, forecast); }
  catch (error) { $('#grid-result').innerHTML = `<div class=\"empty-state\"><p class=\"muted\">${escapeHtml(panelLoadError('Grid Advisor', error))}</p></div>`; }
  finally { button.disabled = false; button.innerHTML = 'ANALIZAR <span>-&gt;</span>'; }
}
document.addEventListener('DOMContentLoaded', () => { $('#vol-horizon-select')?.addEventListener('change', loadVolBattle); });
document.addEventListener('click', event => { const button = event.target.closest('#calculate-grid-volatility'); if (button) loadSelectedGridVolatility(button); });

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
    const priceText = row.price == null ? '—' : formatPrice(row.price);
    const volumeText = row.volume_24h_quote == null ? '—' : formatNumber(row.volume_24h_quote, 0);
    const addedText = row.added_at ? escapeHtml(formatDate(row.added_at)) : '—';
    const notesText = row.notes ? escapeHtml(row.notes) : '—';
    const pending = APP.coinsPendingDelete === row.symbol;
    const inactive = row.active === false || row.active === 0;
    const buttonAttrs = row.is_predictor_symbol ? 'disabled title="XRPUSDT es el símbolo activo del predictor" aria-label="XRPUSDT es el símbolo activo del predictor"' : '';
    const action = inactive
      ? `<button type="button" class="button secondary coin-reactivate-btn" data-symbol="${escapeHtml(row.symbol)}">Reactivar</button>`
      : `<button type="button" class="button secondary coin-remove-btn" data-symbol="${escapeHtml(row.symbol)}" ${buttonAttrs}>${!row.is_predictor_symbol && pending ? '¿Sacar?' : '×'}</button>`;
    const grid = row.open_grid_id ? `<a href="#grids/${encodeURIComponent(row.open_grid_id)}">Sí (#${escapeHtml(row.open_grid_id)})</a>` : 'No';
    return `<tr class="${inactive ? 'coin-inactive' : ''}"><td>${escapeHtml(row.symbol)}</td><td>${priceText}</td><td>${volumeText}</td><td class="${changeClass}">${changeText}</td><td>${addedText}</td><td>${notesText}</td><td>${grid}</td><td>${escapeHtml(row.volatility_model || '—')}</td><td>${action}</td></tr>`;
  }).join('');
  $('#coins-table').innerHTML = `<thead><tr><th>Símbolo</th><th>Precio</th><th>Volumen 24h (USDT)</th><th>Cambio 24h</th><th>Alta</th><th>Notas</th><th>Grid abierto</th><th>Modelo de volatilidad</th><th></th></tr></thead><tbody>${body || '<tr><td colspan="9">Sin monedas activas.</td></tr>'}</tbody>`;
}
async function loadCoins() {
  loadCoinsAvailableOnce();
  try { const rows = await api.coinsList($('#coins-show-inactive')?.checked); showCoinsError(null); renderCoinsTable(rows); }
  catch (error) { showCoinsError(panelLoadError('Monedas', error)); $('#coins-table').innerHTML = '<tbody><tr><td>No se pudo cargar la lista; puedes reintentar.</td></tr></tbody>'; }
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
  const reactivate = event.target.closest('.coin-reactivate-btn');
  if (reactivate) {
    try { await api.addCoin(reactivate.dataset.symbol); await loadCoins(); }
    catch (error) { showCoinsError(error.message || 'No se pudo reactivar la moneda.'); }
    return;
  }
  const button = event.target.closest('.coin-remove-btn');
  if (!button || button.disabled) return;
  const symbol = button.dataset.symbol;
  if (APP.coinsPendingDelete !== symbol) { APP.coinsPendingDelete = symbol; await loadCoins(); return; }
  APP.coinsPendingDelete = null;
  showCoinsError(null);
  try { await api.removeCoin(symbol); await loadCoins(); }
  catch (error) { showCoinsError(error.message || 'No se pudo sacar la moneda.'); await loadCoins(); }
}
document.addEventListener('DOMContentLoaded', () => { $('#coins-form')?.addEventListener('submit', handleCoinFormSubmit); $('#coins-table')?.addEventListener('click', handleCoinsTableClick); $('#coins-show-inactive')?.addEventListener('change', loadCoins); });
