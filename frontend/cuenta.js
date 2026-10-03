(function () {
  let timer = null;
  let loading = false;
  const esc = value => String(value ?? '—').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const money = value => value == null ? '—' : `$${Number(value).toFixed(4)}`;
  async function get(path) {
    const headers = window.gridApiToken ? { 'X-API-Token': window.gridApiToken } : {};
    const response = await fetch((window.API_BASE || location.origin) + path, { headers });
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
    return `<div class="table-card"><table class="data-table"><thead><tr>${headers.map(x => `<th>${x === '#' ? '<button type="button" data-sort-grid>Grid # ↕</button>' : x}</th>`).join('')}</tr></thead><tbody>${rows || `<tr><td colspan="${headers.length}">${empty}</td></tr>`}</tbody></table></div>`;
  }
  function dailyBars(rows) {
    const max = Math.max(0.0001, ...(rows || []).map(row => Math.abs(Number(row.net_pnl_usdt || 0))));
    return `<div class="cuenta-daily-bars" style="display:flex;gap:5px;min-height:58px;overflow:auto;align-items:flex-end;padding:8px;background:var(--bg);border-radius:6px">${(rows || []).map(row => { const amount = Number(row.net_pnl_usdt || 0); return `<div title="${esc(row.date)}: ${money(amount)}" style="min-width:24px;display:flex;flex-direction:column;align-items:center;justify-content:flex-end;gap:4px;font-size:9px"><i style="display:block;width:12px;border-radius:3px 3px 0 0;background:${amount < 0 ? 'var(--red)' : 'var(--green)'};height:${Math.max(3,Math.abs(amount)/max*44)}px"></i><small>${esc(String(row.date).slice(5))}</small></div>`; }).join('') || '<span class="muted">Sin datos diarios</span>'}</div>`;
  }
  function render(connection, data) {
    const container = document.getElementById('cuenta-content');
    const conn = `<section class="card cuenta-section"><h2>Conexión</h2><p>${connection.testnet_confirmed ? 'Conectado a Testnet' : 'Sin conexión a Testnet'} · clave …${esc(connection.key_suffix || '—')} · puede operar: ${connection.can_trade == null ? 'no disponible' : connection.can_trade ? 'sí' : 'no'}</p><p class="muted">${esc(connection.unavailable_reason || `Comprobado ${connection.checked_at}`)}</p></section>`;
    const balance = data.balance;
    const balanceRows = balance ? balance.assets.map(row => `<tr><td>${esc(row.asset)}</td><td>${esc(row.free)}</td><td>${esc(row.locked)}</td><td>${esc(row.price_usdt)}</td><td>${esc(row.value_usdt)}</td></tr>`).join('') : '';
    const unvalued = balance?.unvalued?.length ? `<h3>Sin valorar</h3><p>${balance.unvalued.map(row => `${esc(row.asset)}: libre ${esc(row.free)}, bloqueado ${esc(row.locked)}`).join(' · ')}</p>` : '';
    const balanceBlock = `<section class="card cuenta-section"><h2>Balance Testnet</h2>${balance ? `<p>Equity de cuenta: <b>${money(balance.equity_usdt)}</b> · USDT libre ${esc(balance.usdt_free)} · bloqueado ${esc(balance.usdt_locked)}</p>${rowsTable(['Activo','Libre','Bloqueado','Precio USDT','Valor USDT'], balanceRows, 'Sin balances')}${unvalued}<small class="muted">Precios al ${esc(balance.price_as_of)}</small>` : `<p class="cuenta-error">Balance no disponible: ${esc(data.unavailable_reason)}</p>`}</section>`;
    const g = data.gains;
    const gain = `<section class="card cuenta-section"><h2>Ganancias</h2><div class="cuenta-metrics"><div><span>Realizada</span><b>${money(g.realized_usdt)}</b></div><div><span>Comisión real</span><b>${money(g.fee_real_usdt)}</b></div><div><span>Comisión estimada 0.1% (Testnet cobra 0)</span><b>${money(g.fee_estimated_0_1pct_usdt)}</b></div><div><span>No realizada</span><b>${money(g.unrealized_usdt)}</b></div><div><span>Total con inventario (realizada)</span><b>${money(g.total_with_inventory_usdt)}</b></div><div><span>Operaciones</span><b>${g.operations}</b></div></div><h3>Ganancia diaria agregada · 7 días</h3>${dailyBars(g.daily['7d'])}<h3>Ganancia diaria agregada · 30 días</h3>${dailyBars(g.daily['30d'])}${['open','repository','closed'].map(group => `<h3>${group === 'open' ? 'Grids abiertos' : group === 'repository' ? 'Repositorio' : 'Grids cerrados'}</h3>${rowsTable(['#','Símbolo','Estrategia','Estado','Capital','Realizada','No realizada','Total con inventario','% capital'],data.grids[group].map(row=>`<tr><td>${row.id}</td><td>${esc(row.symbol)}</td><td>${esc(row.strategy)}</td><td>${esc(row.status)}</td><td>${money(row.capital_usdt)}</td><td>${money(row.realized_usdt)}</td><td>${money(row.unrealized_usdt)}</td><td>${money(row.total_with_inventory_usdt)}</td><td>${row.capital_share_pct == null ? '—' : Number(row.capital_share_pct).toFixed(2)+'%'}</td></tr>`).join(''),'Sin grids')}`).join('')}</section>`;
    const recRows = data.reconciliation.assets.map(row => `<tr><td>${esc(row.asset)}</td><td>${esc(row.balance_exchange)}</td><td>${esc(row.assigned_to_grids)}</td><td>${esc(row.difference)}</td><td><span class="cuenta-severity ${esc(row.severity)}">${esc(row.severity)}</span> · ${esc(row.explanation)}</td></tr>`).join('');
    const recon = `<section class="card cuenta-section"><h2>Conciliación contra Testnet</h2>${data.balance ? rowsTable(['Activo','Balance exchange','En grids/repositorio','Diferencia','Estado'],recRows,'Sin diferencias calculables') : '<p>Conciliación no disponible sin balance de Testnet.</p>'}<p>Capital en grids ${esc(data.reconciliation.grid_capital_usdt)} USDT · USDT libre ${esc(data.reconciliation.usdt_free)}</p></section>`;
    container.innerHTML = conn + balanceBlock + gain + recon;
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
  async function load(force) {
    if (loading || (!force && location.hash !== '#cuenta')) return;
    loading = true;
    const container = document.getElementById('cuenta-content');
    try { const [connection, summary] = await Promise.all([get('/api/account/connection'), get('/api/account/summary')]); render(connection, summary); }
    catch (error) { if (error.status === 403) accountTokenPrompt(container); else container.innerHTML = `<div class="card cuenta-error">No se pudo cargar la cuenta: ${esc(error.message)}</div>`; }
    finally { loading = false; }
  }
  window.loadCuenta = () => { load(true); if (timer) clearInterval(timer); timer = setInterval(() => load(false), 15000); };
  document.addEventListener('DOMContentLoaded', () => document.getElementById('cuenta-refresh')?.addEventListener('click', () => load(true)));
})();
