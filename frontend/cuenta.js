(function () {
  let timer = null;
  let loading = false;
  const esc = value => String(value ?? '—').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const money = value => window.ASPLEFormat?.formatUsdt(value) ?? (value == null ? '\u2014' : `$${Number(value).toFixed(2)}`);
  async function get(path) {
    const headers = window.gridApiToken ? { 'X-API-Token': window.gridApiToken } : {};
    const response = await fetch((window.API_BASE || location.origin) + path, { headers });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) { const error = new Error(payload.detail || `HTTP ${response.status}`); error.status = response.status; throw error; }
    return payload;
  }
  async function post(path, body) {
    const headers = { 'Content-Type': 'application/json', ...(window.gridApiToken ? { 'X-API-Token': window.gridApiToken } : {}) };
    const response = await fetch((window.API_BASE || location.origin) + path, { method: 'POST', headers, body: JSON.stringify(body) });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) { const error = new Error(payload.detail || `HTTP ${response.status}`); error.status = response.status; throw error; }
    return payload;
  }
  function accountTokenPrompt(container) {
    container.innerHTML = '<section class="card cuenta-error"><p>La API requiere el token X-API-Token. Se conservará solo en memoria de esta pestaña.</p><label>Token de API <input id="cuenta-token" type="password" autocomplete="off"></label><button id="cuenta-token-save" class="button secondary">Reintentar</button></section>';
    container.querySelector('#cuenta-token-save').addEventListener('click', () => {
      window.gridApiToken = container.querySelector('#cuenta-token').value;
      load(true);
    });
  }
  function rowsTable(headers, rows, empty) {
    return `<div class="table-card"><table class="data-table"><thead><tr>${headers.map(x => `<th>${x === '#' ? '<button type="button" class="button secondary sort-grid-button" data-sort-grid>Grid # ↕</button>' : x}</th>`).join('')}</tr></thead><tbody>${rows || `<tr><td colspan="${headers.length}">${empty}</td></tr>`}</tbody></table></div>`;
  }
  const gainStorageKey = 'asple.cuenta.gain-chart.v1';
  const gainPrefs = {period: '7d', month: ''};
  try {
    const saved = JSON.parse(localStorage.getItem(gainStorageKey) || 'null');
    if (['7d', 'month', 'months'].includes(saved?.period)) gainPrefs.period = saved.period;
    if (typeof saved?.month === 'string' && /^\d{4}-\d{2}$/.test(saved.month)) gainPrefs.month = saved.month;
  } catch (_) {}
  let gainData = {daily: [], monthly: []};
  const dateOnly = iso => new Date(`${iso}T00:00:00Z`);
  const isoDate = date => date.toISOString().slice(0, 10);
  function localToday() {
    const parts = new Intl.DateTimeFormat('en-CA', {timeZone:'America/Santo_Domingo', year:'numeric', month:'2-digit', day:'2-digit'}).formatToParts(new Date());
    const value = Object.fromEntries(parts.map(part => [part.type, part.value]));
    return `${value.year}-${value.month}-${value.day}`;
  }
  function monthOf(date) { return date.slice(0, 7); }
  function shiftMonth(month, amount) {
    const [year, number] = month.split('-').map(Number);
    return new Date(Date.UTC(year, number - 1 + amount, 1)).toISOString().slice(0, 7);
  }
  function persistGainPrefs() {
    try { localStorage.setItem(gainStorageKey, JSON.stringify(gainPrefs)); } catch (_) {}
  }
  function monthLabel(month) {
    return new Intl.DateTimeFormat('es-ES', {month:'short', year:'2-digit', timeZone:'UTC'}).format(dateOnly(`${month}-01`)).replace('.', '');
  }
  function dayLabel(date, period) {
    const parsed = dateOnly(date);
    if (period === 'month') return String(parsed.getUTCDate());
    if (period === 'months') return monthLabel(date.slice(0, 7));
    const weekday = new Intl.DateTimeFormat('es-ES', {weekday:'short', timeZone:'UTC'}).format(parsed).replace('.', '');
    return `${weekday} ${parsed.getUTCDate()}`;
  }
  const rowValue = (row, key) => Number(row?.[key] || 0);
  function makeDailyBar(date, row, period) {
    return {key:date, label:dayLabel(date, period), amount:rowValue(row, 'net_pnl_usdt'),
      cycles:rowValue(row, 'cycles'), stoploss:rowValue(row, 'stoploss'),
      liquidations:rowValue(row, 'liquidations'), hasData:Boolean(row)};
  }
  function barsForPeriod(today) {
    const dailyMap = new Map(gainData.daily.map(row => [row.date, row]));
    if (gainPrefs.period === '7d') {
      return Array.from({length:7}, (_, index) => {
        const date = new Date(`${today}T00:00:00Z`); date.setUTCDate(date.getUTCDate() - 6 + index);
        const key = isoDate(date); return makeDailyBar(key, dailyMap.get(key), '7d');
      });
    }
    if (gainPrefs.period === 'month') {
      const firstMonth = gainData.monthly[0]?.month || monthOf(today);
      const currentMonth = monthOf(today);
      if (!gainPrefs.month || gainPrefs.month > currentMonth) gainPrefs.month = currentMonth;
      if (gainPrefs.month < firstMonth) gainPrefs.month = firstMonth;
      const [year, number] = gainPrefs.month.split('-').map(Number);
      const count = gainPrefs.month === currentMonth ? Number(today.slice(8, 10)) : new Date(Date.UTC(year, number, 0)).getUTCDate();
      return Array.from({length:count}, (_, index) => {
        const date = new Date(Date.UTC(year, number - 1, index + 1));
        const key = isoDate(date); return makeDailyBar(key, dailyMap.get(key), 'month');
      });
    }
    const monthlyMap = new Map(gainData.monthly.map(row => [row.month, row]));
    const firstMonth = gainData.monthly[0]?.month;
    if (!firstMonth) return [];
    const count = (Number(today.slice(0, 4)) - Number(firstMonth.slice(0, 4))) * 12 + Number(today.slice(5, 7)) - Number(firstMonth.slice(5, 7)) + 1;
    return Array.from({length:count}, (_, index) => {
      const month = shiftMonth(firstMonth, index), row = monthlyMap.get(month);
      return {key:month, label:dayLabel(month, 'months'), amount:rowValue(row, 'net_pnl_usdt'),
        cycles:rowValue(row, 'cycles'), stoploss:rowValue(row, 'stoploss'),
        liquidations:rowValue(row, 'liquidations'), hasData:Boolean(row)};
    });
  }
  function gainChartContents() {
    const today = localToday(), bars = barsForPeriod(today), mode = gainPrefs.period;
    const hasAnyData = gainData.daily.length > 0 || gainData.monthly.length > 0;
    const firstMonth = gainData.monthly[0]?.month || monthOf(today), currentMonth = monthOf(today);
    const total = bars.reduce((sum, bar) => sum + bar.amount, 0);
    const active = bars.filter(bar => bar.hasData);
    const gained = active.filter(bar => bar.amount > 0).length;
    const lost = active.filter(bar => bar.amount < 0).length;
    const noActivity = bars.filter(bar => !bar.hasData).length;
    const best = active.reduce((value, bar) => value == null || bar.amount > value.amount ? bar : value, null);
    const worst = active.reduce((value, bar) => value == null || bar.amount < value.amount ? bar : value, null);
    const max = Math.max(0, ...bars.map(bar => Math.abs(bar.amount)));
    const chart = bars.map(bar => {
      const height = max ? Math.max(bar.amount === 0 ? 0 : 2, Math.abs(bar.amount) / max * 48) : 0;
      const tip = `${bar.key} \\u00b7 Neto ${money(bar.amount)} \\u00b7 Ciclos ${money(bar.cycles)} \\u00b7 Stop-loss ${money(bar.stoploss)} \\u00b7 Liquidaciones ${money(bar.liquidations)}`;
      return `<div class="gain-bar" title="${esc(tip)}"><span class="gain-bar-value ${bar.amount < 0 ? 'loss' : ''}">${money(bar.amount)}</span><div class="gain-bar-track"><i class="gain-bar-fill ${bar.amount < 0 ? 'negative' : 'positive'}" style="--bar-height:${height}px"></i></div><small>${esc(bar.label)}</small></div>`;
    }).join('');
    const monthPicker = mode === 'month' ? `<div class="gain-month-picker"><button type="button" class="button secondary" data-month-step="-1" aria-label="Mes anterior" ${gainPrefs.month <= firstMonth ? 'disabled' : ''}>&lsaquo;</button><strong>${esc(monthLabel(gainPrefs.month))}</strong><button type="button" class="button secondary" data-month-step="1" aria-label="Mes siguiente" ${gainPrefs.month >= currentMonth ? 'disabled' : ''}>&rsaquo;</button></div>` : '';
    const summary = `<div class="gain-summary"><div class="gain-total"><span>Total ganado</span><b>${money(total)}</b></div><div class="gain-stats"><span>Con ganancia: <b>${gained}</b></span><span>Con p\\u00e9rdida: <b>${lost}</b></span><span>Sin actividad: <b>${noActivity}</b></span><span>Mejor: <b>${best ? `${esc(best.label)} \\u00b7 ${money(best.amount)}` : '&mdash;'}</b></span><span>Peor: <b>${worst ? `${esc(worst.label)} \\u00b7 ${money(worst.amount)}` : '&mdash;'}</b></span><span>Promedio por ${mode === 'months' ? 'mes' : 'd\\u00eda'} con actividad: <b>${active.length ? money(total / active.length) : money(0)}</b></span></div></div>`;
    return `<h3>Ganancia realizada</h3><div class="gain-controls"><div class="gain-segments" role="group" aria-label="Periodo de ganancias">${[['7d','7 d\\u00edas'],['month','Mes'],['months','Meses']].map(([value,label]) => `<button type="button" data-gain-period="${value}" aria-pressed="${mode === value}">${label}</button>`).join('')}</div>${monthPicker}</div>${hasAnyData ? `${summary}<div class="gain-chart-scroll ${mode === 'month' ? 'month-mode' : ''}" aria-label="Gr\\u00e1fico de ganancias"><div class="gain-chart-bars mode-${mode}" style="--bar-count:${bars.length}">${chart}<div class="gain-zero-line"></div></div></div>` : '<p class="muted">Sin ganancias registradas todav\\u00eda</p>'}`;
  }
  function bindGainChart(chart) {
    chart.addEventListener('click', event => {
      const period = event.target.closest('[data-gain-period]')?.dataset.gainPeriod;
      if (period) { gainPrefs.period = period; persistGainPrefs(); }
      const step = Number(event.target.closest('[data-month-step]')?.dataset.monthStep || 0);
      if (step) { gainPrefs.month = shiftMonth(gainPrefs.month || monthOf(localToday()), step); persistGainPrefs(); }
      if (period || step) chart.innerHTML = gainChartContents();
    });
  }
  function render(connection, data) {
    const container = document.getElementById('cuenta-content');
    const conn = `<section class="card cuenta-section"><h2>Conexión</h2><p>${connection.testnet_confirmed ? 'Conectado a Testnet' : 'Sin conexión a Testnet'} · clave …${esc(connection.key_suffix || '—')} · puede operar: ${connection.can_trade == null ? 'no disponible' : connection.can_trade ? 'sí' : 'no'}</p><p class="muted">${esc(connection.unavailable_reason || `Comprobado ${window.ASPLEFormat?.formatDateTime(connection.checked_at) ?? esc(connection.checked_at)}`)}</p></section>`;
    const balance = data.balance;
    const balanceRows = balance ? balance.assets.map(row => `<tr><td>${esc(row.asset)}</td><td>${window.ASPLEFormat?.formatNumber(row.free, 6) ?? esc(row.free)}</td><td>${window.ASPLEFormat?.formatNumber(row.locked, 6) ?? esc(row.locked)}</td><td>${window.ASPLEFormat?.formatNumber(row.price_usdt, 4) ?? esc(row.price_usdt)}</td><td>${window.ASPLEFormat?.formatUsdt(row.value_usdt) ?? esc(row.value_usdt)}</td></tr>`).join('') : '';
    const unvaluedRows = (balance?.unvalued || []).slice().sort((a,b) => String(a.asset).localeCompare(String(b.asset))).map(row => `<tr><td>${esc(row.asset)}</td><td>${esc(row.free)}</td><td>${esc(row.locked)}</td></tr>`).join('');
    const unvalued = unvaluedRows ? `<details class="unvalued-details"><summary>Sin valorar (${balance.unvalued.length} activos)</summary>${rowsTable(['Activo','Libre','Bloqueado'],unvaluedRows,'Sin activos sin valorar')}</details>` : '';
    const balanceBlock = `<section class="card cuenta-section"><h2>Balance Testnet</h2>${balance ? `<div class="cuenta-balance-primary"><p>Capital en grids <b>${money(data.reconciliation.grid_capital_usdt)}</b></p><p>USDT libre <b>${money(balance.usdt_free)}</b> \u00b7 bloqueado ${money(balance.usdt_locked)}</p></div><p class="cuenta-equity-note">Equity de cuenta: <b>${money(balance.equity_usdt)}</b> <small>incluye saldos de prueba de Testnet</small></p>${rowsTable(['Activo','Libre','Bloqueado','Precio USDT','Valor USDT'], balanceRows, 'Sin balances')}${unvalued}<small class="muted">Precios al ${window.ASPLEFormat?.formatDateTime(balance.price_as_of) ?? esc(balance.price_as_of)}</small>` : `<p class="cuenta-error">Balance no disponible: ${esc(data.unavailable_reason)}</p>`}</section>`;
    const g = data.gains;
    gainData = {daily:g.daily?.all || [], monthly:g.monthly || []};
    const gain = `<section class="card cuenta-section"><h2>Ganancias</h2><div class="cuenta-metrics"><div><span>Realizada</span><b>${money(g.realized_usdt)}</b></div><div><span>Comisión real</span><b>${money(g.fee_real_usdt)}</b></div><div><span>Comisión estimada 0.1% (Testnet cobra 0)</span><b>${money(g.fee_estimated_0_1pct_usdt)}</b></div><div><span>No realizada</span><b>${money(g.unrealized_usdt)}</b></div><div><span>Total con inventario (realizada)</span><b>${money(g.total_with_inventory_usdt)}</b></div><div><span>Operaciones</span><b>${g.operations}</b></div></div><section class="cuenta-gain-chart">${gainChartContents()}</section>${['open','repository','closed'].map(group => `<h3>${group === 'open' ? 'Grids abiertos' : group === 'repository' ? 'Repositorio' : 'Grids cerrados'}</h3>${rowsTable(['#','Símbolo','Estrategia','Estado','Capital','Realizada','No realizada','Total con inventario','% capital'],data.grids[group].map(row=>`<tr><td>${row.id}</td><td>${esc(row.symbol)}</td><td>${esc(row.strategy)}</td><td>${esc(window.ASPLEFormat?.statusLabel(row.status) ?? row.status)}</td><td>${money(row.capital_usdt)}</td><td>${money(row.realized_usdt)}</td><td>${money(row.unrealized_usdt)}</td><td>${money(row.total_with_inventory_usdt)}</td><td>${row.capital_share_pct == null ? '—' : Number(row.capital_share_pct).toFixed(2)+'%'}</td></tr>`).join(''),'Sin grids')}`).join('')}<p class="muted">La participación de capital se calcula sobre grids abiertos y repositorio; en grids cerrados no aplica.</p></section>`;
    const reconciliationRows = rows => rows.map(row => `<tr><td>${esc(row.asset)}</td><td>${esc(row.balance_exchange)}</td><td>${esc(row.assigned_to_grids)}</td><td>${esc(row.difference)}</td><td><span class="cuenta-severity ${row.severity === 'unassigned' ? 'unassigned-neutral' : esc(row.severity)}">${row.severity === 'unassigned' ? 'sin asignar' : esc(row.severity)}</span> · ${row.severity === 'unassigned' ? 'fuera de grids (saldo de la cuenta Testnet)' : esc(row.explanation)}</td></tr>`).join('');
    const allAssets = data.reconciliation.assets || [];
    const relevantAssets = allAssets.filter(row => row.relevant === true);
    const unassignedAssets = allAssets.filter(row => row.relevant !== true);
    const tableHeaders = ['Activo','Balance exchange','En grids/repositorio','Diferencia','Estado'];
    const recRows = `${reconciliationRows(relevantAssets)}${unassignedAssets.length ? `<tr><td colspan="5"><details><summary>Ver todo (${unassignedAssets.length} saldos sin asignar)</summary>${rowsTable(tableHeaders,reconciliationRows(unassignedAssets),'Sin saldos sin asignar')}</details></td></tr>` : ''}`;
    const recon = `<section class="card cuenta-section"><h2>Conciliación contra Testnet</h2>${data.balance ? rowsTable(tableHeaders,recRows,'Sin diferencias relevantes') : '<p>Conciliación no disponible sin balance de Testnet.</p>'}<p>Capital en grids ${esc(data.reconciliation.grid_capital_usdt)} USDT · USDT libre ${esc(data.reconciliation.usdt_free)}</p></section>`;
    container.innerHTML = conn + balanceBlock + gain + recon;
    bindGainChart(container.querySelector('.cuenta-gain-chart'));
    const gainsHeading = [...container.querySelectorAll('.cuenta-section h2')].find(heading => heading.textContent === 'Ganancias');
    if (gainsHeading && window.ASPLEFormat?.updateAgeLabel) {
      const age = document.createElement('small'); age.className = 'data-age muted';
      gainsHeading.after(age);
      window.ASPLEFormat.updateAgeLabel(age, data.gains.as_of || data.gains.updated_at || data.gains.checked_at || connection.checked_at);
    }
    if (!container.dataset.sortBound) {
      container.dataset.sortBound = '1';
      container.addEventListener('click', event => {
        if (!event.target.closest('[data-sort-grid]')) return;
        const table = event.target.closest('table'), body = table.querySelector('tbody');
        const asc = table.dataset.sortAsc !== 'false'; table.dataset.sortAsc = String(!asc);
        [...body.rows].sort((a,b) => (Number(a.cells[0].textContent) - Number(b.cells[0].textContent)) * (asc ? 1 : -1)).forEach(row => body.appendChild(row));
      });
    }
  }
  async function loadGlobalFlags() { await window.ASPLEGlobalFlags?.load('global-settings-content'); }
  async function load(force) {
    if (loading || (!force && location.hash !== '#cuenta')) return;
    loading = true;
    const container = document.getElementById('cuenta-content');
    try { const [connection, summary] = await Promise.all([get('/api/account/connection'), get('/api/account/summary')]); render(connection, summary); }
    catch (error) { if (error.status === 403) accountTokenPrompt(container); else container.innerHTML = `<div class="card cuenta-error">No se pudo cargar la cuenta: ${esc(error.message)}</div>`; }
    finally { loading = false; }
  }
  window.loadCuenta = () => { document.getElementById('global-settings-content')?.classList.add('global-flag-list'); loadGlobalFlags(); load(true); if (timer) clearInterval(timer); timer = setInterval(() => load(false), 15000); };
  document.addEventListener('DOMContentLoaded', () => {
    document.getElementById('global-settings-content')?.classList.add('global-flag-list');
    document.getElementById('cuenta-refresh')?.addEventListener('click', () => load(true));
    document.getElementById('testnet-credentials-form')?.addEventListener('submit', async event => {
      event.preventDefault();
      const keyInput = document.getElementById('testnet-api-key');
      const secretInput = document.getElementById('testnet-api-secret');
      const confirmation = document.getElementById('testnet-credentials-confirm');
      const status = document.getElementById('testnet-credentials-status');
      const button = event.currentTarget.querySelector('button[type="submit"]');
      if (!confirmation.checked || !window.confirm('Cambiar claves de Testnet. ¿Confirmas esta acción?')) return;
      const apiKey = keyInput.value, apiSecret = secretInput.value;
      keyInput.value = ''; secretInput.value = ''; confirmation.checked = false;
      button.disabled = true; status.textContent = 'Validando y guardando claves Testnet…';
      try {
        const result = await post('/api/account/testnet-credentials', { api_key: apiKey, api_secret: apiSecret });
        status.textContent = result.message || 'Reinicia el servidor para aplicar el cambio.';
        load(true);
      } catch (error) { status.textContent = error.message || 'No se pudieron guardar las claves Testnet.'; }
      finally { button.disabled = false; }
    });
  });
})();
