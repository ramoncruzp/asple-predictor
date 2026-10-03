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
  function planLines(plan) {
    const n = value => value == null ? 'no disponible' : esc(value);
    const lines = [];
    if (plan.action === 'close') {
      if (plan.mode === 'liquidate') lines.push(`Se venderán ${n(plan.held_qty)} unidades a mercado; PnL no realizado que se materializaría ≈ $${n(plan.unrealized_pnl_materialized)} USDT; comisión estimada ≈ $${n(plan.estimated_commission_usdt)} USDT.`);
      else lines.push(`Se cancelarán ${n(plan.open_orders_to_cancel)} órdenes abiertas; quedarían ${n(plan.retained_qty)} unidades retenidas fuera del grid; valor ≈ $${n(plan.held_market_value_usdt)} USDT.`);
      if (plan.mode === 'repository') lines.push(`${n(plan.cells_to_repository)} celdas pasarían al repositorio; inventario no gestionado: ${n(plan.unmanaged_inventory_qty)}.`);
      lines.push(`La salida propuesta sería ${n(plan.resulting_status)}; revisa el resultado final en el estado del grid.`);
      if (plan.mode === 'liquidate') lines.push(`Órdenes abiertas a cancelar: ${n(plan.open_orders_to_cancel)}.`);
      if (plan.mode === 'cancel') lines.push('No se venderá inventario a mercado; el saldo retenido quedará fuera del grid.');
    } else if (plan.action === 'pause') {
      lines.push(`Se cancelarían ${n(plan.open_buy_orders_to_cancel)} compras; permanecerían ${n(plan.open_sell_orders_remain)} ventas abiertas.`);
      lines.push(`Inventario retenido: ${n(plan.held_qty)}; valoración: $${n(plan.held_market_value_usdt)} USDT.`);
      lines.push(`La acción dejaría el grid en ${n(plan.resulting_status)}; motivo: ${n(plan.reason)}.`);
    } else if (plan.action === 'resume') {
      lines.push(`El grid pasaría a ACTIVE con precio de referencia ${n(plan.bid)} USDT.`);
      lines.push(`Fuera del rango: ${plan.outside_range ? 'sí' : 'no'}; por debajo de todos los niveles: ${plan.below_all_levels ? 'sí' : 'no'}.`);
      lines.push(`Inventario mantenido en cuenta: ${n(plan.held_qty)} unidades.`);
    } else if (plan.action === 'adjust') {
      const a = plan.adjustment || {};
      lines.push(`Celdas que cambiarían de asignación: ${n((a.mapping || []).length)}; celdas cubiertas: ${n((a.covered || []).length)}; niveles que se retirarían: ${n((a.retire_level_idxs || []).length)}.`);
      lines.push(`Nuevo rango solicitado: ${n((plan.requested_range || {}).low)}–${n((plan.requested_range || {}).high)} USDT.`);
      lines.push(`Capital por celda estimado: ${n(a.capital_per_cell)} USDT; resultado: ${n(plan.adjustment_reason || 'propuesta disponible')}.`);
    } else if (plan.action === 'sweep-dust') {
      const d = plan.dust || {};
      lines.push(`Cantidad candidata a barrido: ${n(d.qty)}; residuo que permanecería: ${n(d.residual)}.`);
      lines.push(`Barrido permitido por filtros: ${d.sweepable == null ? 'no disponible' : (d.sweepable ? 'sí' : 'no')}; resultado neto estimado: $${n(d.proceeds_net)} USDT.`);
      lines.push(`Bid de referencia: ${n(plan.bid_used)} USDT; filtros Testnet aplicados.`);
    } else if (plan.action === 'params') {
      lines.push(`Parámetros que se actualizarían: ${n(Object.keys(plan.updates || {}).join(', ') || 'ninguno')}.`);
      lines.push(`Parámetros que se quitarían: ${n((plan.remove || []).join(', ') || 'ninguno')}.`);
      lines.push('Las cifras pertenecen a la configuración del grid; verifica el estado después de guardar.');
    }
    if (plan.price_note) lines.push(`Nota: ${esc(plan.price_note)}; la valoración no está disponible.`);
    return lines.map(line => `<li>${line}</li>`).join('');
  }

  async function apiGet(path) {
    const base = (window.API_BASE || window.location.origin);
    const response = await fetch(base + path, { headers: window.gridApiToken ? { 'X-API-Token': window.gridApiToken } : {} });
    if (!response.ok) {
      const error = new Error(`HTTP ${response.status}`);
      error.status = response.status;
      throw error;
    }
    return response.json();
  }

  async function apiPost(path, body) {
    const response = await fetch((window.API_BASE || window.location.origin) + path, {
      method: 'POST', headers: { 'Content-Type': 'application/json', ...(window.gridApiToken ? { 'X-API-Token': window.gridApiToken } : {}) },
      body: JSON.stringify(body),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) { const error = new Error(data.detail || `HTTP ${response.status}`); error.status = response.status; throw error; }
    return data;
  }

  function actionFields(action, card) {
    if (action === 'pause') return { reason: card.querySelector('[name="pause-reason"]')?.value || null };
    if (action === 'close') return { mode: card.querySelector('[name="close-mode"]')?.value || 'repository' };
    if (action === 'adjust') return { new_low: Number(card.querySelector('[name="new-low"]')?.value), new_high: Number(card.querySelector('[name="new-high"]')?.value), n: Number(card.querySelector('[name="new-n"]')?.value) || null };
    if (action === 'params') {
      const read = name => { const value = card.querySelector(`[name="${name}"]`)?.value; return value === '' || value == null ? null : Number(value); };
      const result = { target_pct: read('target-pct'), target_usdt: read('target-usdt'), max_days: read('max-days'), dust_sweep_threshold_pct: read('dust-threshold') };
      if (card.querySelector('[name="clear-target"]')?.checked) { result.target_pct = null; result.target_usdt = null; }
      if (card.querySelector('[name="clear-max-days"]')?.checked) result.max_days = null;
      return result;
    }
    return {};
  }

  function formFor(action, summary) {
    if (action === 'pause') return '<label>Motivo breve<input name="pause-reason" maxlength="120"></label>';
    if (action === 'close') return '<label>Modo<select name="close-mode"><option value="repository">Pasar celdas al repositorio</option><option value="cancel">Cancelar órdenes y dejar activo el inventario</option><option value="liquidate">Vender inventario a mercado</option></select></label>';
    if (action === 'adjust') return `<label>Rango mínimo<input name="new-low" type="number" min="0.00000001" step="any" value="${summary.range_low || ''}" required></label><label>Rango máximo<input name="new-high" type="number" min="0.00000001" step="any" value="${summary.range_high || ''}" required></label><label>Niveles (opcional)<input name="new-n" type="number" min="4" max="60"></label>`;
    if (action === 'params' && summary.strategy === 'simple') return '<label>Plazo maximo (dias)<input name="max-days" type="number" min="0.000001" step="any"></label><label><input name="clear-max-days" type="checkbox"> Quitar plazo actual</label>';
    if (action === 'params' && summary.strategy !== 'simple') return '<p>Deja vacio para mantener. Testnet no representa el mercado real.</p><label>Meta %<input name="target-pct" type="number" min="0.000001" step="any"></label><label>Meta USDT<input name="target-usdt" type="number" min="0.000001" step="any"></label><label><input name="clear-target" type="checkbox"> Quitar meta actual</label><label><input name="set-target-basis" type="checkbox"> Cambiar base de meta</label><select name="target-basis"><option value="cash">Caja</option><option value="equity">Equity</option></select><label>Plazo maximo (dias)<input name="max-days" type="number" min="0.000001" step="any"></label><label><input name="clear-max-days" type="checkbox"> Quitar plazo actual</label><label>Barrido de polvo (% capital)<input name="dust-threshold" type="number" min="0" step="any"></label>';
    if (action === 'params') return '<p>Deja vacío para mantener. Testnet no representa el mercado real.</p><label>Meta %<input name="target-pct" type="number" min="0.000001" step="any"></label><label>Meta USDT<input name="target-usdt" type="number" min="0.000001" step="any"></label><label><input name="clear-target" type="checkbox"> Quitar meta actual</label><label>Plazo máximo (días)<input name="max-days" type="number" min="0.000001" step="any"></label><label><input name="clear-max-days" type="checkbox"> Quitar plazo actual</label><label>Barrido de polvo (% capital)<input name="dust-threshold" type="number" min="0" step="any"></label>';
    return '';
  }

  async function controlFlow(gridId, action, summary, container) {
    if (action === 'unsupported') {
      showActionDialog(container, 'Capital y compuesto', '<p>No se puede cambiar el capital ni el interés compuesto en un grid abierto. Ciérralo y abre uno nuevo.</p><button type="button" class="button secondary" data-close-new>Ir a cerrar este grid</button>');
      document.getElementById('grid-action-dialog').querySelector('[data-close-new]').onclick = () => {
        document.getElementById('grid-action-dialog').hidden = true;
        controlFlow(gridId, 'close', summary, container);
      };
      return;
    }
    const path = `/api/grids/${gridId}/${action}`;
    const gather = () => actionFields(action, document.getElementById('grid-action-dialog'));
    const initialFields = formFor(action, summary);
    showActionDialog(container, `Preparar ${action}`, `${initialFields}<p class="muted">Las cifras son una estimación de Testnet; Testnet no representa el mercado real.</p>`, async () => {
      const input = gather();
      if (action === 'params') {
        const modal = document.getElementById('grid-action-dialog');
        const clearTarget = modal.querySelector('[name="clear-target"]')?.checked;
        const clearDays = modal.querySelector('[name="clear-max-days"]')?.checked;
        if (modal.querySelector('[name="set-target-basis"]')?.checked) input.target_basis = modal.querySelector('[name="target-basis"]').value;
        Object.keys(input).forEach(key => { if (input[key] === null && !(clearTarget && ['target_pct','target_usdt'].includes(key)) && !(clearDays && key === 'max_days')) delete input[key]; });
      }
      const first = await apiPost(path, { ...input, dry_run: true, confirm: false });
      const preview = JSON.stringify(first.plan, null, 2);
      const liquidation = action === 'close' && input.mode === 'liquidate';
      showActionDialog(container, 'Revisar plan', `<p>Plan estimado en Testnet. Testnet no representa el mercado real.</p><ul>${planLines(first.plan)}</ul><details><summary>Detalle técnico</summary><pre>${esc(preview)}</pre></details>${liquidation ? '<label>Escribe LIQUIDAR<input name="liquidate-confirm" autocomplete="off"></label>' : ''}`, async () => {
        if (liquidation && document.getElementById('grid-action-dialog').querySelector('[name="liquidate-confirm"]').value !== 'LIQUIDAR') throw new Error('Escribe LIQUIDAR para confirmar la venta a mercado.');
        const result = await apiPost(path, { ...input, ...(liquidation ? { confirm_text: 'LIQUIDAR' } : {}), dry_run: false, confirm: true });
        if (result.outcome === 'partial') {
          const errors = (result.errors || []).map(error => `<li>${esc(typeof error === 'string' ? error : JSON.stringify(error))}</li>`).join('') || '<li>El motor no informó detalles.</li>';
          const partialMessage = action === 'close' ? `El grid NO quedó cerrado: estado ${result.status_after || 'CLOSING'}` : `La acción ${action} quedó incompleta: estado ${result.status_after || 'sin confirmar'}`;
          showActionDialog(container, 'Acción incompleta', `<div class="grid-action-partial" role="alert"><strong>${esc(partialMessage)}</strong><ul>${errors}</ul><p>Reintenta la acción o revisa Testnet.</p></div><details><summary>Detalle técnico</summary><pre>${esc(JSON.stringify(result, null, 2))}</pre></details><p>Testnet no representa el mercado real.</p>`);
        } else {
          showActionDialog(container, 'Acción completada', `<p>Estado: ${esc(result.status_after || result.status || 'completado')}</p><details><summary>Detalle técnico</summary><pre>${esc(JSON.stringify(result.result || result, null, 2))}</pre></details><p>Testnet no representa el mercado real.</p>`);
        }
        await window.loadGridsScreen?.(`grids/${gridId}`, true);
      });
    });
  }

  function showActionDialog(container, title, body, onConfirm) {
    let modal = document.getElementById('grid-action-dialog');
    if (!modal) { modal = document.createElement('section'); modal.id = 'grid-action-dialog'; modal.className = 'grid-action-dialog'; modal.setAttribute('role', 'dialog'); modal.setAttribute('aria-modal', 'true'); document.body.appendChild(modal); }
    modal.innerHTML = `<div class="grid-action-panel"><h2 id="grid-action-title">${esc(title)}</h2><div class="grid-action-body">${body}</div><p class="grid-action-error" role="alert"></p><div class="grid-action-buttons"><button type="button" class="button secondary" data-action-cancel>Cancelar</button>${onConfirm ? '<button type="button" class="button primary" data-action-confirm>Continuar</button>' : ''}</div></div>`;
    modal.setAttribute('aria-labelledby', 'grid-action-title');
    modal.hidden = false;
    document.querySelectorAll('[data-grid-controls] button').forEach(button => { button.disabled = true; });
    modal.querySelector('[data-action-cancel]').onclick = () => { modal.hidden = true; document.querySelectorAll('[data-grid-controls] button').forEach(button => { button.disabled = false; }); };
    modal.onkeydown = event => { if (event.key === 'Escape') modal.querySelector('[data-action-cancel]')?.click(); };
    const confirm = modal.querySelector('[data-action-confirm]');
    if (confirm) confirm.onclick = async () => {
      const errorBox = modal.querySelector('.grid-action-error'); errorBox.textContent = '';
      try { confirm.disabled = true; await onConfirm(); }
      catch (error) {
        if (error.status === 403) {
          modal.querySelector('.grid-action-body').innerHTML += '<label>Token API (solo memoria de pestaña)<input type="password" name="api-token" autocomplete="off"></label><button type="button" class="button secondary" data-token-save>Reintentar con token</button>';
          modal.querySelector('[data-token-save]').onclick = async () => { window.gridApiToken = modal.querySelector('[name="api-token"]').value; modal.hidden = true; await onConfirm(); };
        }
        errorBox.textContent = error.message || 'La acción no se pudo completar.';
      } finally { if (confirm.isConnected) confirm.disabled = false; }
    };
    modal.querySelector('input,select,button')?.focus();
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
      container.innerHTML = '<div class="card grids-error">La API requiere X-API-Token. Token guardado solo en memoria de esta pestana. '
        + '<label>Token API<input id="grids-api-token" type="password" autocomplete="off"></label><button class="button secondary" id="grids-token-save">Reintentar</button></div>';
      container.querySelector('#grids-token-save').onclick = () => { window.gridApiToken = container.querySelector('#grids-api-token').value; window.loadGridsScreen?.((location.hash || '#grids').slice(1)); };
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
        <div class="grid-detail-controls" data-grid-controls="${summary.id}" data-status="${esc(summary.status)}" data-low="${detail.range_low}" data-high="${detail.range_high}" data-strategy="${esc(summary.strategy)}">
          ${summary.status === 'ACTIVE' ? '<button class="button secondary" data-grid-action="pause">Pausar</button>' : ''}
          ${summary.status === 'PAUSED' ? '<button class="button secondary" data-grid-action="resume">Reanudar</button>' : ''}
          ${['ACTIVE','PAUSED'].includes(summary.status) ? '<button class="button secondary" data-grid-action="close">Cerrar</button>' : ''}
          ${summary.status === 'ACTIVE' ? '<button class="button secondary" data-grid-action="adjust">Reubicar rango</button>' : ''}
          ${!['CLOSED','ERROR'].includes(summary.status) ? '<button class="button secondary" data-grid-action="sweep-dust">Barrer polvo</button>' : ''}
          ${['ACTIVE','PAUSED'].includes(summary.status) ? '<button class="button secondary" data-grid-action="params">Editar meta/plazo</button>' : ''}
          <button class="button secondary" data-grid-action="unsupported">Capital y compuesto</button>
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

  document.addEventListener('click', (event) => {
    const button = event.target.closest('[data-grid-action]');
    if (!button) return;
    const controls = button.closest('[data-grid-controls]');
    const gridId = controls?.dataset.gridControls;
    const summary = { id: gridId, status: controls?.dataset.status, strategy: controls?.dataset.strategy,
      range_low: controls?.dataset.low, range_high: controls?.dataset.high };
    const parent = document.getElementById('grids-content');
    button.disabled = true;
    controlFlow(gridId, button.dataset.gridAction, summary, parent).catch(error => showActionDialog(parent, 'Error de acción', `<p>${esc(error.message)}</p>`));
  });

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
