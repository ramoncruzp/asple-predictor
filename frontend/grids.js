// Fase 17A: read-only "Grids" screen. No pausar/reanudar/cerrar here (17B).
// No CDN dependencies; the equity curve is a hand-built inline SVG.
(function () {
  let refreshTimer = null;

  function fmtMoney(value, digits = 4) {
    if (value === null || value === undefined) return '—';
    const num = Number(value);
    if (!Number.isFinite(num)) return '—';
    return num.toLocaleString('en-US', { minimumFractionDigits: digits, maximumFractionDigits: 8 });
  }
  function fmtPct(value, digits = 2) {
    if (value === null || value === undefined) return '—';
    return `${Number(value).toFixed(digits)}%`;
  }
  function pnlClass(value) {
    if (value === null || value === undefined) return '';
    return Number(value) >= 0 ? 'pnl-positive' : 'pnl-negative';
  }
  function esc(value) {
    return String(value ?? '—').replace(/[&<>'"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' }[c]));
  }

  async function apiGet(path) {
    const base = (window.API_BASE || window.location.origin);
    const response = await fetch(base + path);
    if (!response.ok) {
      const error = new Error(`HTTP ${response.status}`);
      error.status = response.status;
      throw error;
    }
    return response.json();
  }

  const STATUS_LABELS = { ACTIVE: 'Activo', PAUSED: 'Pausado', OPENING: 'Abriendo', CLOSING: 'Cerrando',
    HOLDING: 'Repositorio', CLOSED: 'Cerrado', FAILED: 'Fallido' };
  const STATUS_CLASSES = { ACTIVE: 'status-active', PAUSED: 'status-paused', HOLDING: 'status-holding',
    CLOSING: 'status-closing', OPENING: 'status-active', CLOSED: 'status-closed', FAILED: 'status-error' };

  function statusLabel(status) { return STATUS_LABELS[status] || status; }
  function statusClass(status) { return STATUS_CLASSES[status] || ''; }
  function tooltip(text) { return `<span class="tooltip-icon" title="${esc(text)}">?</span>`; }

  function renderError(container, error) {
    if (error && error.status === 403) {
      container.innerHTML = '<div class="card grids-error">La API de grids requiere un token (X-API-Token) '
        + 'configurado en el servidor. Esta pantalla aun no pide ni guarda el token.</div>';
      return;
    }
    container.innerHTML = `<div class="card grids-error">No se pudo cargar la informacion de grids: ${esc(error && error.message)}</div>`;
  }

  function gridRowHtml(grid) {
    return `<a class="grid-row-card" href="#grids/${grid.id}">
      <div class="grid-row-top"><span class="grid-id">#${grid.id}</span><span class="grid-symbol">${esc(grid.symbol)}</span>
      <span class="grid-strategy">${esc(grid.strategy)}</span><span class="status-badge ${statusClass(grid.status)}">${statusLabel(grid.status)}</span></div>
      <div class="grid-row-metrics">
        <div><span>Neta realizada</span><b class="${pnlClass(grid.net_realized_usdt)}">${fmtMoney(grid.net_realized_usdt, 2)}</b></div>
        <div><span>Total con inventario</span><b class="${pnlClass(grid.total_with_inventory_usdt)}">${fmtMoney(grid.total_with_inventory_usdt, 2)}</b></div>
        <div><span>Desplegado</span><b>${fmtPct(grid.capital_deployed_pct, 1)}</b></div>
        <div><span>Precio</span><b class="mono">${grid.price === null ? 'sin precio' : fmtMoney(grid.price, 6)}</b></div>
      </div></a>`;
  }

  function renderList(container, data) {
    const grids = data.grids || [];
    const totals = data.totals || {};
    const warning = data.same_symbol_warning ? `<div class="grids-warning">${esc(data.same_symbol_warning)}</div>` : '';
    const openRows = grids.filter((grid) => grid.status !== 'HOLDING');
    const repoRows = grids.filter((grid) => grid.status === 'HOLDING');
    container.innerHTML = `
      ${warning}
      <div class="grids-totals card">
        <div><span>Ganancia neta total (realizada)</span><b class="${pnlClass(totals.net_realized_usdt)}">${fmtMoney(totals.net_realized_usdt, 2)}</b></div>
        <div><span>Total con inventario</span><b class="${pnlClass(totals.total_with_inventory_usdt)}">${fmtMoney(totals.total_with_inventory_usdt, 2)}</b></div>
        <div><span>Capital en grids</span><b>${fmtMoney(totals.capital_in_grids_usdt, 2)}</b></div>
        <div><span>USDT libre (Testnet)</span><b>${totals.free_usdt_unavailable_reason ? '—' : fmtMoney(totals.free_usdt, 2)}</b>
          ${totals.free_usdt_unavailable_reason ? `<small class="muted">${esc(totals.free_usdt_unavailable_reason)}</small>` : ''}</div>
      </div>
      <h2 class="grids-section-title">Grids abiertos</h2>
      <div class="grids-list">${openRows.map(gridRowHtml).join('') || '<p class="muted">Sin grids abiertos.</p>'}</div>
      ${repoRows.length ? `<h2 class="grids-section-title">Repositorio</h2><div class="grids-list">${repoRows.map(gridRowHtml).join('')}</div>` : ''}
    `;
  }

  function cellRowHtml(row) {
    if (row.marker === 'precio_actual') {
      return `<tr class="price-marker-row"><td colspan="7">Precio actual &rarr; ${row.price === null ? 'sin precio' : fmtMoney(row.price, 6)}</td></tr>`;
    }
    return `<tr class="cell-row">
      <td>${row.level_idx}</td><td class="mono">${fmtMoney(row.buy_price, 6)}</td><td class="mono">${fmtMoney(row.sell_price, 6)}</td>
      <td class="mono">${fmtMoney(row.capital_usdt, 2)}</td><td class="cell-state">${esc(row.state_label)}</td>
      <td class="${pnlClass(row.live_pnl_usdt)}">${row.live_pnl_usdt === null ? '—' : fmtMoney(row.live_pnl_usdt, 4)}</td>
      <td class="mono">${row.distance_to_fill_pct === null ? '—' : fmtPct(row.distance_to_fill_pct, 2)}</td></tr>`;
  }

  function buildEquitySvg(points) {
    if (!points.length) return '<p class="muted">Sin datos de equity aun</p>';
    const width = 600, height = 160, pad = 10;
    const values = points.flatMap((p) => [p.with_inventory_usdt, p.realized_usdt]).filter((v) => v !== null && v !== undefined);
    if (!values.length) return '<p class="muted">Sin datos de equity aun</p>';
    const min = Math.min(...values), max = Math.max(...values);
    const range = (max - min) || 1;
    const x = (i) => pad + (i / Math.max(1, points.length - 1)) * (width - 2 * pad);
    const y = (v) => height - pad - ((v - min) / range) * (height - 2 * pad);
    const line = (key) => points.map((p, i) => `${x(i)},${y(p[key] ?? min)}`).join(' ');
    return `<svg viewBox="0 0 ${width} ${height}" class="equity-svg" role="img" aria-label="Curva de equity con inventario">
      <polyline points="${line('realized_usdt')}" class="equity-line-realized"></polyline>
      <polyline points="${line('with_inventory_usdt')}" class="equity-line-inventory"></polyline>
    </svg>
    <div class="equity-legend"><span><i class="equity-dot-realized"></i>Realizado</span><span><i class="equity-dot-inventory"></i>Con inventario</span></div>`;
  }

  function renderDetail(container, detail, operations, events, daily, equity) {
    const summary = detail.summary;
    const cells = detail.cells;
    const recovery = summary.recovery_mode === true
      ? '<div class="recovery-banner">El precio esta bajo todos los niveles de venta con posicion abierta: las '
        + 'posiciones esperan su precio de venta. El sistema puede cerrar por perdida maxima, meta o plazo si '
        + 'estan configurados.</div>'
      : '';
    const progressTarget = summary.progress.target
      ? `<div class="progress-block"><span>Meta (${esc(summary.progress.target.basis)})</span>
         <b>${fmtPct(summary.progress.target.progress_pct, 1)}</b>
         <small>Caja estimada: ${fmtMoney(summary.progress.target.cash_now_usdt, 2)} USDT; avance: ${fmtMoney(summary.progress.target.cash_profit_usdt, 2)} USDT / ${fmtMoney(summary.progress.target.target_profit_usdt, 2)} USDT</small>
         ${summary.progress.target.equity_now_usdt === null ? '' : `<small>Equity estimada: ${fmtMoney(summary.progress.target.equity_now_usdt, 2)} USDT</small>`}
         <small class="muted">${esc(summary.progress.target.note)}</small></div>`
      : '';
    const progressDays = summary.progress.max_days
      ? `<div class="progress-block"><span>Plazo</span><b>${summary.progress.max_days.days_remaining === null ? '—'
          : summary.progress.max_days.days_remaining.toFixed(1) + ' d restantes'}</b></div>`
      : '';
    const dailyRows = daily.daily || [];
    const maxDaily = Math.max(1, ...dailyRows.map((row) => Math.abs(row.net_pnl_usdt || 0)));
    const dailyBars = dailyRows.map((row) => {
      const value = Number(row.net_pnl_usdt || 0);
      const barHeight = Math.max(4, Math.abs(value) / maxDaily * 30);
      const barTop = value < 0 ? 30 : 30 - barHeight;
      return `<div class="daily-bar-wrap" style="display:block;position:relative;height:60px;min-width:10px" title="${esc(row.date)}: ${fmtMoney(value, 2)}">
        <div class="daily-bar ${pnlClass(value)}" style="position:absolute;left:2px;top:${barTop}px;height:${barHeight}px;border-radius:0 0 2px 2px"></div>
      </div>`;
    }).join('');
    const opsRows = (operations.operations || []).map((op) => `<tr>
      <td>${esc(new Date(op.ts).toLocaleString())}</td><td>${esc(op.kind || 'ciclo')}</td><td>${op.level_idx}</td>
      <td class="mono" title="${op.prices_approx ? 'precio aproximado por un ajuste de celda' : 'precio de orden/celda'}">${fmtMoney(op.buy_price_approx, 6)}${op.prices_approx ? ' ~' : ''}</td><td class="mono" title="${op.prices_approx ? 'precio aproximado por un ajuste de celda' : 'precio de orden/celda'}">${fmtMoney(op.sell_price_approx, 6)}${op.prices_approx ? ' ~' : ''}</td>
      <td class="mono">${fmtMoney(op.sell_qty, 4)}</td><td class="${pnlClass(op.net_pnl_usdt)}">${fmtMoney(op.net_pnl_usdt, 4)}</td>
      <td>${op.duration_hours === null ? '—' : op.duration_hours.toFixed(1) + ' h'}</td></tr>`).join('');
    const eventRows = (events.events || []).map((ev) => `<tr class="severity-${esc(ev.severity)}">
      <td>${esc(new Date(ev.ts).toLocaleString())}</td><td>${esc(ev.message)}</td><td class="muted">${esc(ev.reason || '')}</td></tr>`).join('');

    container.innerHTML = `
      <a class="back-link" href="#grids">&larr; Volver a Grids</a>
      <div class="grid-detail-header card">
        <div class="grid-detail-identity"><span class="grid-id">#${summary.id}</span><h2>${esc(summary.symbol)}</h2>
          <span class="status-badge ${statusClass(summary.status)}">${statusLabel(summary.status)}</span>
          ${summary.compound_enabled ? '<span class="badge-compound">compuesto</span>' : ''}</div>
        <div class="grid-detail-price">${summary.price === null ? 'sin precio' : fmtMoney(summary.price, 6)}
          ${summary.price_as_of ? `<small class="muted">al ${esc(new Date(summary.price_as_of).toLocaleTimeString())}</small>` : '<small class="muted">sin precio actual</small>'}</div>
        <div class="grid-detail-controls">
          <button class="button secondary" disabled title="disponible en 17B">Pausar</button>
          <button class="button secondary" disabled title="disponible en 17B">Detener</button>
        </div>
      </div>
      ${recovery}
      <div class="metric-cards">
        <div class="card metric-card"><span>Bruta ${tooltip('Ganancia realizada antes de comisiones')}</span><b>${fmtMoney(summary.gross_realized_usdt, 2)}</b></div>
        <div class="card metric-card"><span>${esc(summary.fee_real_label)} ${tooltip('Comision efectivamente cobrada por el exchange; en Testnet suele ser 0')}</span><b>${fmtMoney(summary.fee_real_usdt, 4)}</b></div>
        <div class="card metric-card"><span>Neta (realizada) ${tooltip('Ganancia realizada, neta de la comision real')}</span><b class="${pnlClass(summary.net_realized_usdt)}">${fmtMoney(summary.net_realized_usdt, 2)}</b></div>
        <div class="card metric-card"><span>Capital ${tooltip('Capital total asignado a este grid')}</span><b>${fmtMoney(summary.capital_total_usdt, 2)}</b></div>
        <div class="card metric-card"><span>Operaciones ${tooltip('Ciclos de compra-venta completados')}</span><b>${summary.cycles_completed}</b></div>
        <div class="card metric-card"><span>Profit/ciclo ${tooltip(summary.profit_per_cycle_label || 'Ganancia neta promedio por ciclo, como % del capital')}</span><b>${summary.profit_per_cycle_pct === null ? '—' : fmtPct(summary.profit_per_cycle_pct, 3)}</b></div>
      </div>
      <div class="card fee-estimate-note">${esc(summary.fees_estimated_label)}: <b>${fmtMoney(summary.fees_estimated_usdt, 4)}</b>
        - neta tras comision estimada: <b class="${pnlClass(summary.net_after_estimated_fees_usdt)}">${fmtMoney(summary.net_after_estimated_fees_usdt, 2)}</b></div>
      <div class="card inventory-block">
        <h3>Inventario abierto</h3>
        <div class="inventory-grid">
          <div><span>Cantidad</span><b class="mono">${fmtMoney(summary.inventory.qty, 4)}</b></div>
          <div><span>Costo</span><b>${fmtMoney(summary.inventory.cost_usdt, 2)}</b></div>
          <div><span>Valor de mercado</span><b>${summary.inventory.market_value_usdt === null ? '—' : fmtMoney(summary.inventory.market_value_usdt, 2)}</b></div>
          <div><span>No realizado</span><b class="${pnlClass(summary.inventory.unrealized_pnl_usdt)}">${summary.inventory.unrealized_pnl_usdt === null ? '—' : fmtMoney(summary.inventory.unrealized_pnl_usdt, 2)}</b></div>
          <div><span>Total con inventario</span><b class="${pnlClass(summary.total_with_inventory_usdt)}">${summary.total_with_inventory_usdt === null ? '—' : fmtMoney(summary.total_with_inventory_usdt, 2)}</b></div>
          <div><span>Polvo</span><b class="mono">${fmtMoney(summary.dust_qty, 6)} ${summary.dust_value_usdt !== null ? `(${fmtMoney(summary.dust_value_usdt, 4)} USDT)` : ''}</b></div>
        </div>
        ${summary.inventory.unavailable_reason ? `<p class="muted">${esc(summary.inventory.unavailable_reason)}</p>` : ''}
      </div>
      ${(progressTarget || progressDays) ? `<div class="progress-row">${progressTarget}${progressDays}</div>` : ''}
      <div class="card cells-block">
        <h3>Escalera de bots</h3>
        <table class="data-table cells-table" aria-label="Escalera de celdas del grid, ordenada de mayor a menor precio">
          <thead><tr><th>Nivel</th><th>Compra</th><th>Venta</th><th>Capital</th><th>Estado</th><th>PNL vivo</th><th>Distancia a fill</th></tr></thead>
          <tbody>${cells.rows.map(cellRowHtml).join('') || '<tr><td colspan="7">Sin celdas</td></tr>'}</tbody>
        </table>
        <div class="cells-totals"><span>Esperado por ciclo: <b>${fmtMoney(cells.expected_profit_per_cycle_usdt, 4)}</b></span>
          <span>PNL vivo total: <b class="${pnlClass(cells.live_pnl_total_usdt)}">${fmtMoney(cells.live_pnl_total_usdt, 4)}</b></span></div>
      </div>
      <div class="card operations-block">
        <h3>Ultimas operaciones</h3>
        <table class="data-table" aria-label="Ultimas operaciones completadas">
          <thead><tr><th>Fecha</th><th>Tipo</th><th>Nivel</th><th>Compra</th><th>Venta</th><th>Cantidad</th><th>Neto</th><th>Duracion</th></tr></thead>
          <tbody>${opsRows || '<tr><td colspan="8">Sin operaciones aun</td></tr>'}</tbody>
        </table>
      </div>
      <div class="card daily-block">
        <h3>Ganancia diaria</h3>
        <div class="daily-chart" style="align-items:flex-start;position:relative;background:linear-gradient(to bottom,transparent 29px,var(--muted) 30px,transparent 31px)">${dailyBars || '<p class="muted">Sin datos aun</p>'}</div>
        ${(daily.unattributed_usdt !== null && Math.abs(daily.unattributed_usdt) > 1e-9) || daily.truncated
          ? `<p class="muted">No atribuido al diario: ${fmtMoney(daily.unattributed_usdt, 2)} USDT${daily.unattributed_note ? ` (${esc(daily.unattributed_note)})` : ''}${daily.truncated ? ' · consulta truncada a los últimos 5000 eventos' : ''}</p>` : ''}
      </div>
      <div class="card equity-block">
        <h3>Curva de equity</h3>
        ${buildEquitySvg(equity.points || [])}
        <p class="muted">${esc(equity.note || 'valor de mercado incluyendo posiciones retenidas')}</p>
      </div>
      <div class="card events-block">
        <h3>Eventos del bot</h3>
        <table class="data-table" aria-label="Ultimos eventos del grid">
          <thead><tr><th>Fecha</th><th>Evento</th><th>Motivo</th></tr></thead>
          <tbody>${eventRows || '<tr><td colspan="3">Sin eventos</td></tr>'}</tbody>
        </table>
      </div>
    `;
  }

  async function loadList(container) {
    try {
      renderList(container, await apiGet('/api/grids'));
    } catch (error) {
      renderError(container, error);
    }
  }

  async function loadDetail(container, gridId) {
    try {
      const [detail, operations, events, daily, equity] = await Promise.all([
        apiGet(`/api/grids/${gridId}`),
        apiGet(`/api/grids/${gridId}/operations`),
        apiGet(`/api/grids/${gridId}/events`),
        apiGet(`/api/grids/${gridId}/daily`),
        apiGet(`/api/grids/${gridId}/equity`),
      ]);
      renderDetail(container, detail, operations, events, daily, equity);
    } catch (error) {
      if (error && error.status === 404) {
        container.innerHTML = '<div class="card grids-error">Ese grid no existe.</div>';
      } else {
        renderError(container, error);
      }
    }
  }

  function scheduleRefresh() {
    if (refreshTimer) clearInterval(refreshTimer);
    refreshTimer = setInterval(() => {
      const current = (location.hash || '').slice(1);
      if (!current.startsWith('grids')) {
        clearInterval(refreshTimer);
        refreshTimer = null;
        return;
      }
      const scrollY = window.scrollY;
      window.loadGridsScreen(current, true).then(() => window.scrollTo(0, scrollY));
    }, 12000);
  }

  window.loadGridsScreen = async function loadGridsScreen(hash, silent) {
    const container = document.getElementById('grids-content');
    const heading = document.getElementById('grids-heading');
    if (!container) return;
    const parts = (hash || 'grids').split('/');
    const gridId = parts[1];
    if (heading) heading.textContent = gridId ? `Grid #${gridId}` : 'Grids';
    if (!silent) container.innerHTML = '<p class="muted">Cargando...</p>';
    if (gridId) {
      await loadDetail(container, gridId);
    } else {
      await loadList(container);
    }
    if (!silent) scheduleRefresh();
  };
})();
