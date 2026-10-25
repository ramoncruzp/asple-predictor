// Fase 40c: pantalla "Metas" (Modo Meta). Sigue el estilo de grids.js: sin CDN,
// todo en español, estimaciones siempre marcadas como estimación (nunca promesa).
(function () {
  let refreshTimer = null;
  let analysisPollTimer = null;
  let countdownTimer = null;
  let currentHash = '';
  // Mientras hay un análisis corriendo o un plan a la vista, el refresco automático
  // no debe redibujar la pantalla (eso borraba el progreso y detenía la consulta).
  let planVisible = false;
  let lastTargetValue = '10';
  const JOB_KEY = 'asple-metas-analysis-job';
  function saveJob(job) { try { sessionStorage.setItem(JOB_KEY, JSON.stringify(job)); } catch (e) { /* sin almacenamiento */ } }
  function loadJob() { try { return JSON.parse(sessionStorage.getItem(JOB_KEY) || 'null'); } catch (e) { return null; } }
  function clearJob() { try { sessionStorage.removeItem(JOB_KEY); } catch (e) { /* sin almacenamiento */ } }
  let ledgerState = { page: 1, pageSize: 50, includeValuation: false, type: 'all' };

  function esc(value) {
    return String(value ?? '—').replace(/[&<>'"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' }[c]));
  }
  function fmtMoney(value, digits = 2) {
    if (value === null || value === undefined) return '—';
    const num = Number(value);
    if (!Number.isFinite(num)) return '—';
    return window.ASPLEFormat?.formatNumber(num, digits) ?? num.toFixed(digits);
  }
  function fmtPct(value, digits = 1) {
    if (value === null || value === undefined) return '—';
    const num = Number(value);
    if (!Number.isFinite(num)) return '—';
    return `${num.toFixed(digits)}%`;
  }
  function pnlClass(value) {
    if (value === null || value === undefined) return '';
    const num = Number(value);
    if (!Number.isFinite(num) || Math.abs(num) < 0.005) return 'pnl-neutral';
    return num > 0 ? 'pnl-positive' : 'pnl-negative';
  }
  // Precio con cifras significativas (no trunca monedas de precio muy bajo como PEPE).
  function fmtPrice(value) { return window.ASPLEFormat?.formatPrice?.(value) ?? fmtMoney(value, 6); }
  const BOOK_STATE_LABELS = { two_sided: 'Dos lados', one_sided: 'Un solo lado' };
  const MODEL_READINESS_LABELS = {
    lista: 'Lista', datos_insuficientes: 'Datos insuficientes',
    descargando: 'Descargando', pendiente: 'Pendiente',
  };
  function bookStateLabel(value) { return BOOK_STATE_LABELS[value] || 'Sin datos'; }
  function modelStatsLabel(stats) {
    if (!stats) return 'Sin datos';
    const state = String(stats.readiness || '').toLowerCase();
    const readiness = MODEL_READINESS_LABELS[state] || (state ? esc(stats.readiness) : 'Sin estado');
    const value = stats.effective_verifications != null ? Number(stats.effective_verifications) : 0;
    const verifications = Number.isFinite(value)
      ? value.toLocaleString('es-ES', { minimumFractionDigits: 1, maximumFractionDigits: 1 }) : '0,0';
    const required = Number(stats.required_verifications ?? 30);
    const denominator = Number.isFinite(required) ? required.toLocaleString('es-ES', { maximumFractionDigits: 0 }) : '30';
    return `${readiness} · ${verifications} de ${denominator} días verificados`;
  }
  // "100 -> 110 USDT (+10)" o "(+10 %)" si la meta se creó en porcentaje; evita
  // que "100 -> 10 USDT" se lea como si la meta fuera bajar a 10.
  function goalTitle(goal) {
    const capital = Number(goal.capital_initial);
    const targetUsdt = Number(goal.target_usdt);
    const endTotal = capital + targetUsdt;
    const deltaLabel = goal.target_input_type === 'PCT'
      ? `+${esc(goal.target_input_value)}%` : `+${fmtMoney(targetUsdt, 0)}`;
    return `${fmtMoney(capital, 0)} → ${fmtMoney(endTotal, 0)} USDT (${deltaLabel})`;
  }

  async function apiGet(path) {
    const base = (window.API_BASE || window.location.origin);
    const response = await fetch(base + path, { headers: window.gridApiToken ? { 'X-API-Token': window.gridApiToken } : {} });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) { const error = new Error(data.detail || `HTTP ${response.status}`); error.status = response.status; throw error; }
    return data;
  }
  async function apiPost(path, body) {
    const response = await fetch((window.API_BASE || window.location.origin) + path, {
      method: 'POST', headers: { 'Content-Type': 'application/json', ...(window.gridApiToken ? { 'X-API-Token': window.gridApiToken } : {}) },
      body: JSON.stringify(body || {}),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) { const error = new Error(data.detail || `HTTP ${response.status}`); error.status = response.status; throw error; }
    return data;
  }

  function showActionDialog(title, body, onConfirm, confirmLabel) {
    let modal = document.getElementById('meta-action-dialog');
    if (!modal) {
      modal = document.createElement('section');
      modal.id = 'meta-action-dialog'; modal.className = 'grid-action-dialog';
      modal.setAttribute('role', 'dialog'); modal.setAttribute('aria-modal', 'true');
      document.body.appendChild(modal);
    }
    modal.innerHTML = `<div class="grid-action-panel"><h2 id="meta-action-title">${esc(title)}</h2>
      <div class="grid-action-body">${body}</div><p class="grid-action-error" role="alert"></p>
      <div class="grid-action-buttons"><button type="button" class="button secondary" data-action-cancel>${onConfirm ? 'Cancelar' : 'Cerrar'}</button>
      ${onConfirm ? `<button type="button" class="button primary" data-action-confirm>${esc(confirmLabel || 'Confirmar')}</button>` : ''}</div></div>`;
    modal.setAttribute('aria-labelledby', 'meta-action-title');
    modal.hidden = false;
    modal.querySelector('[data-action-cancel]').onclick = () => { modal.hidden = true; };
    modal.onkeydown = event => { if (event.key === 'Escape') modal.querySelector('[data-action-cancel]')?.click(); };
    const confirm = modal.querySelector('[data-action-confirm]');
    if (confirm) confirm.onclick = async () => {
      const errorBox = modal.querySelector('.grid-action-error'); errorBox.textContent = '';
      try { confirm.disabled = true; await onConfirm(); modal.hidden = true; }
      catch (error) { errorBox.textContent = error.message || 'La acción no se pudo completar.'; }
      finally { if (confirm.isConnected) confirm.disabled = false; }
    };
    return modal;
  }

  function closeDialog() { const modal = document.getElementById('meta-action-dialog'); if (modal) modal.hidden = true; }

  const PROFILE_LABELS = { PRUDENTE: 'Prudente', RAPIDO: 'Rápido' };
  function profileLimitsTable(profiles, selected) {
    const rows = [
      ['Pérdida máxima', p => `−${fmtPct(p.max_loss_pct, 0)}`],
      ['Aviso (75 %)', p => `−${fmtPct(p.warning_loss_pct, 1)}`],
      ['Plazo máximo', p => `${p.max_days} días`],
      ['Capital por moneda', p => fmtPct(p.capital_per_coin_pct, 0)],
      ['Piezas', p => `${p.max_grids} grids / ${p.max_bots} bots`],
      ['Préstamos (tope)', p => fmtPct(p.loan_cap_pct, 0)],
    ];
    const cols = Object.keys(PROFILE_LABELS).filter(k => profiles[k]);
    return `<div class="metas-table-scroll"><table class="data-table metas-limits-table" data-selected-profile="${esc(selected || '')}">
      <thead><tr><th></th>${cols.map(c => `<th data-profile-col="${c}">${PROFILE_LABELS[c]}</th>`).join('')}</tr></thead>
      <tbody>${rows.map(([label, fn]) => `<tr><td>${label}</td>${cols.map(c => `<td data-profile-col="${c}">${fn(profiles[c])}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`;
  }

  // -- Zone 1: create ----------------------------------------------------

  function renderDisabled() {
    return `<div class="card metas-empty"><h2>El Modo Meta está apagado</h2>
      <p class="muted">Enciéndelo en <a href="#lab">Laboratorio</a> para crear una meta.</p></div>`;
  }

  function renderCreateForm(config) {
    const profiles = config.profiles || {};
    return `<div class="card metas-create">
      <h2>Crear una meta</h2>
      <p class="muted">Dale un objetivo a la app; ella decide cómo llegar.</p>
      <form id="meta-create-form" class="metas-create-form">
        <label>Capital (USDT)<input name="capital_usdt" type="number" min="1" step="0.01" value="100" required></label>
        <fieldset class="metas-target-type">
          <legend class="metas-field-legend">Meta (ganancia objetivo)</legend>
          <label><input type="radio" name="target_type" value="USDT" checked> USDT</label>
          <label><input type="radio" name="target_type" value="PCT"> %</label>
          <input name="target_value" type="number" min="0.01" step="0.01" value="${esc(lastTargetValue)}" required aria-label="Valor de la meta">
        </fieldset>
        <fieldset class="metas-profile-choice">
          <legend class="metas-field-legend">Perfil</legend>
          <label><input type="radio" name="profile" value="PRUDENTE"> Prudente</label>
          <label><input type="radio" name="profile" value="RAPIDO" checked> Rápido</label>
        </fieldset>
        ${profileLimitsTable(profiles, 'RAPIDO')}
        <button type="submit" class="button primary">Crear meta</button>
        <p class="metas-form-error" role="alert"></p>
      </form>
    </div>`;
  }

  function wireCreateForm(container) {
    const form = container.querySelector('#meta-create-form');
    if (!form) return;
    const targetInput = form.querySelector('[name="target_value"]');
    targetInput?.addEventListener('input', () => { lastTargetValue = targetInput.value; });
    form.querySelectorAll('[name="profile"]').forEach(radio => radio.addEventListener('change', () => {
      const table = form.querySelector('.metas-limits-table');
      if (table) table.dataset.selectedProfile = radio.value;
    }));
    form.addEventListener('submit', async event => {
      event.preventDefault();
      const errorBox = form.querySelector('.metas-form-error');
      errorBox.textContent = '';
      const data = new FormData(form);
      lastTargetValue = String(data.get('target_value') || lastTargetValue);
      try {
        await apiPost('/api/metas', {
          capital_usdt: Number(data.get('capital_usdt')),
          target_value: Number(data.get('target_value')),
          target_type: data.get('target_type'),
          profile: data.get('profile'),
        });
        await render(container, currentHash);
      } catch (error) { errorBox.textContent = error.message || 'No se pudo crear la meta.'; }
    });
  }

  // -- Zone 2: DRAFT analysis ---------------------------------------------

  function stopAnalysisPoll() { if (analysisPollTimer) { clearInterval(analysisPollTimer); analysisPollTimer = null; } if (countdownTimer) { clearInterval(countdownTimer); countdownTimer = null; } }

  function renderDraftCard(goal) {
    return `<div class="card metas-draft" data-meta-id="${goal.id}">
      <div class="metas-draft-head">
        <h3>Meta #${goal.id} · ${goalTitle(goal)} · ${PROFILE_LABELS[goal.profile] || goal.profile}</h3>
        <div class="metas-draft-actions">
          <button type="button" class="button primary" data-analyze="${goal.id}">Analizar</button>
          <button type="button" class="button secondary" data-discard="${goal.id}">Descartar</button>
        </div>
      </div>
      <div class="metas-analysis-slot" data-analysis-slot="${goal.id}"></div>
    </div>`;
  }

  function elapsedLabel(startedAtMs) {
    const seconds = Math.max(0, Math.round((Date.now() - startedAtMs) / 1000));
    const m = Math.floor(seconds / 60), s = seconds % 60;
    return `${m}:${String(s).padStart(2, '0')}`;
  }

  function startAnalysis(container, metaId) {
    stopAnalysisPoll();
    planVisible = false;
    const slot = container.querySelector(`[data-analysis-slot="${metaId}"]`);
    slot.innerHTML = `<p class="metas-analyzing">⏳ Iniciando análisis…</p>`;
    apiPost(`/api/metas/${metaId}/analysis`).then(({ job_id: jobId }) => {
      const job = { metaId, jobId, startedAtMs: Date.now() };
      saveJob(job);
      pollAnalysis(container, job);
    }).catch(error => { slot.innerHTML = `<p class="metas-error">${esc(error.message)}</p>`; });
  }

  function pollAnalysis(container, job) {
    stopAnalysisPoll();
    const { metaId, jobId, startedAtMs } = job;
    const slot = container.querySelector(`[data-analysis-slot="${metaId}"]`);
    if (!slot) { clearJob(); return; }
    slot.innerHTML = `<p class="metas-analyzing">⏳ Analizando… puede tardar varios minutos si el caché del día no está listo. Tiempo transcurrido: <span data-elapsed>${elapsedLabel(startedAtMs)}</span></p>`;
    countdownTimer = setInterval(() => {
      const el = slot.querySelector('[data-elapsed]');
      if (el) el.textContent = elapsedLabel(startedAtMs);
    }, 1000);
    analysisPollTimer = setInterval(async () => {
      try {
        const result = await apiGet(`/api/metas/${metaId}/analysis/${jobId}`);
        if (result.status === 'COMPLETE') { stopAnalysisPoll(); clearJob(); renderPlanResult(container, metaId, result.plan); }
        else if (result.status === 'FAILED') { stopAnalysisPoll(); clearJob(); slot.innerHTML = `<p class="metas-error">El análisis falló: ${esc(result.error)}</p><button type="button" class="button secondary" data-reanalyze="${metaId}">Analizar de nuevo</button>`; wireReanalyze(container, metaId); }
      } catch (error) {
        // El trabajo vive en memoria; si el servicio se reinició, ya no existe.
        stopAnalysisPoll(); clearJob();
        slot.innerHTML = `<p class="metas-error">El análisis ya no está disponible; analiza de nuevo.</p><button type="button" class="button secondary" data-reanalyze="${metaId}">Analizar de nuevo</button>`;
        wireReanalyze(container, metaId);
      }
    }, 3000);
  }

  function wireReanalyze(container, metaId) {
    container.querySelector(`[data-reanalyze="${metaId}"]`)?.addEventListener('click', () => startAnalysis(container, metaId));
  }

  function candidatesTable(candidates) {
    const rows = (candidates || []).map(c => {
      if (c.excluded_reason) return `<tr class="metas-candidate-excluded"><td>${esc(c.symbol)}</td><td colspan="4">Excluida: ${esc(c.excluded_reason)}</td></tr>`;
      const sigma = c.sigma_24h_open ?? c.sigma_24h;
      return `<tr><td>${esc(c.symbol)}</td><td>${sigma != null ? `${(Number(sigma) * 100).toFixed(2)}%` : '—'}</td>
        <td>${c.score != null ? Number(c.score).toFixed(2) : '—'}</td><td>${bookStateLabel(c.book_state)}</td>
        <td>${modelStatsLabel(c.model_statistics)}</td></tr>`;
    }).join('');
    return `<div class="metas-table-scroll"><table class="data-table"><thead><tr><th>Símbolo</th><th>σ</th><th>Score Scanner</th><th>Libro</th><th>Modelo</th></tr></thead><tbody>${rows}</tbody></table></div>`;
  }

  function piecesTable(pieces) {
    return `<div class="metas-table-scroll"><table class="data-table"><thead><tr><th>Moneda</th><th>Tipo</th><th>Capital</th><th>Piso</th><th>Techo</th><th>Niveles</th><th>Margen</th><th>Préstamos</th></tr></thead>
      <tbody>${(pieces || []).map(p => `<tr><td>${esc(p.symbol)}</td><td>${esc(p.piece_type === 'individual_bot_grid' ? 'bot' : p.strategy)}</td>
        <td>${fmtMoney(p.capital_usdt)} USDT</td><td>${fmtPrice(p.floor)}</td><td>${fmtPrice(p.ceiling)}</td>
        <td>${esc(p.n_levels)}</td><td>${fmtPct(p.margin_pct)}</td><td>${p.loans_pct ? fmtPct(p.loans_pct) : '—'}</td></tr>`).join('')}</tbody></table></div>`;
  }

  function renderPlanResult(container, metaId, plan) {
    const slot = container.querySelector(`[data-analysis-slot="${metaId}"]`);
    if (!slot) return;
    const est = plan.estimated_time || {};
    const expiresAtMs = Date.now() + (plan.expires_in_seconds || 600) * 1000;
    const warnings = [];
    if (est.low_likelihood) warnings.push('⚠ "Poco probable" (probabilidad bajo 25 %).');
    if (plan.capital_usage?.reason) warnings.push(`ℹ ${esc(plan.capital_usage.reason)}`);
    if (est.evidence_warning) warnings.push(`⚠ ${esc(est.evidence_warning)}`);
    if (!est.probability_replay && est.rolling_windows > 0) {
      warnings.push(`⚠ Ninguna alternativa alcanzó la meta en el historial; esta es la que más se acercó (${fmtPct(est.median_progress_pct, 1)}).`);
    }
    // Avisos propios del plan (ej. "el replay no modela préstamos de grids Smart").
    (plan.warnings || []).forEach(w => warnings.push(`⚠ ${esc(w)}`));
    slot.innerHTML = `
      <div class="metas-plan-section"><h4>Candidatas</h4>${candidatesTable(plan.candidates)}</div>
      <div class="metas-plan-section"><h4>Plan propuesto</h4>${piecesTable(plan.pieces)}</div>
      <div class="metas-plan-section"><h4>Estimación (histórica, nunca promesa)</h4>
        <div class="metas-estimate-grid">
          <div><span>Probabilidad histórica</span><b>${fmtPct((est.probability_replay || 0) * 100)}</b></div>
          <div><span>Probabilidad ajustada</span><b>${fmtPct((est.probability_adjusted || 0) * 100)}</b></div>
          <div><span>Avance mediano hacia la meta</span><b>${fmtPct(est.median_progress_pct)}</b></div>
          <div><span>Días (mediana)</span><b>${est.days_median != null ? Math.round(est.days_median) : '— (no alcanzada)'}</b></div>
          <div><span>Peor caída (desde el máximo de la ventana)</span><b>${fmtPct(est.worst_drawdown_pct)}</b></div>
          <div><span>Peor resultado vs. capital inicial</span><b>${fmtPct(est.worst_loss_vs_capital_pct)}</b></div>
          <div><span>Ventanas</span><b>${est.rolling_windows || 0} totales, ${est.independent_windows || 0} independientes</b></div>
          <div><span>Cortadas por pérdida</span><b>${est.loss_cut_windows ?? 0}</b></div>
          <div><span>Omitidas</span><b>${est.skipped_windows ?? 0}${est.skipped_reason ? ` (${esc(est.skipped_reason)})` : ''}</b></div>
        </div>
        ${warnings.length ? `<div class="metas-warnings">${warnings.map(w => `<p>${w}</p>`).join('')}</div>` : ''}
      </div>
      <div class="metas-plan-actions">
        <button type="button" class="button primary" data-start-plan="${plan.plan_id}" data-meta-id="${metaId}">Iniciar con este plan</button>
        <span class="muted">Este plan vence en <b data-plan-countdown>…</b></span>
      </div>
      <p class="metas-plan-expired hidden">El plan venció; analiza de nuevo.</p>`;
    const countdownEl = slot.querySelector('[data-plan-countdown]');
    const startButton = slot.querySelector('[data-start-plan]');
    const expiredNote = slot.querySelector('.metas-plan-expired');
    const tick = () => {
      const remaining = Math.max(0, Math.round((expiresAtMs - Date.now()) / 1000));
      countdownEl.textContent = `${Math.floor(remaining / 60)}:${String(remaining % 60).padStart(2, '0')}`;
      if (remaining <= 0) { clearInterval(planCountdown); planVisible = false; startButton.disabled = true; expiredNote.classList.remove('hidden'); }
    };
    planVisible = true;
    const planCountdown = setInterval(tick, 1000);
    tick();
    startButton.addEventListener('click', () => confirmStart(container, metaId, plan));
  }

  function confirmStart(container, metaId, plan) {
    const est = plan.estimated_time || {};
    const body = `<p>Piezas: ${(plan.pieces || []).map(p => esc(p.symbol)).join(', ')}</p>
      <p>Capital total: ${fmtMoney((plan.pieces || []).reduce((sum, p) => sum + Number(p.capital_usdt || 0), 0))} USDT</p>
      <p>Pérdida máxima: ${fmtMoney(plan.loss_max_usdt)} USDT · Plazo: ${plan.deadline_days} días</p>
      <p class="muted">Esto abre órdenes reales en Testnet.</p>`;
    showActionDialog('Confirmar inicio', body, async () => {
      await apiPost(`/api/metas/${metaId}/start`, { plan_id: plan.plan_id });
      await render(document.getElementById('metas-content'), currentHash);
    }, 'Iniciar');
  }

  function wireDraftActions(container) {
    container.querySelectorAll('[data-analyze]').forEach(btn => btn.addEventListener('click', () => startAnalysis(container, btn.dataset.analyze)));
    container.querySelectorAll('[data-discard]').forEach(btn => btn.addEventListener('click', () => {
      showActionDialog('Descartar borrador', `<p>¿Descartar la meta #${esc(btn.dataset.discard)}? No se toca el exchange.</p>`, async () => {
        await apiPost(`/api/metas/${btn.dataset.discard}/discard`);
        await render(container, currentHash);
      }, 'Descartar');
    }));
  }

  // -- Zone 3: active / blocked / surplus ---------------------------------

  function valuationBlock(goal, valuation) {
    const status = valuation.status;
    let banner = '';
    if (status === 'DESCONOCIDO') banner = `<p class="metas-unprotected">🔴 SIN PROTECCIÓN DE PÉRDIDA — el libro no tiene compradores o la lectura es vieja.</p>`;
    else if (status === 'PARCIAL') banner = `<p class="metas-partial">⚠ Parte del inventario no tiene comprador ahora mismo.</p>`;
    return `<div class="metas-valuation">
      <div class="metas-valuation-row"><span>Mejor bid</span><b>${fmtMoney(valuation.value_best_bid)} USDT</b></div>
      <div class="metas-valuation-row"><span>Ejecutable (${esc(status)})</span><b>${fmtMoney(valuation.value_executable)} USDT</b></div>
      ${banner}
    </div>`;
  }

  function progressBars(goal, valuation, cfg) {
    const resultPct = valuation.result_pct != null ? Number(valuation.result_pct) : null;
    const targetPct = resultPct != null ? Math.max(0, Math.min(100, (resultPct / (Number(goal.target_usdt) / Number(goal.capital_initial) * 100)) * 100)) : 0;
    const maxLoss = Number(cfg.max_loss_pct || 0);
    const lossPct = resultPct != null && resultPct < 0 ? Math.max(0, Math.min(100, (Math.abs(resultPct) / maxLoss) * 100)) : 0;
    return `<div class="metas-progress">
      <div class="metas-progress-row"><span>Hacia la meta (${fmtPct(resultPct)})</span>
        <div class="metas-bar"><div class="metas-bar-fill metas-bar-target" style="width:${targetPct}%"></div></div></div>
      <div class="metas-progress-row"><span>Hacia el límite de pérdida</span>
        <div class="metas-bar"><div class="metas-bar-fill metas-bar-loss" style="width:${lossPct}%"></div><div class="metas-bar-mark" style="left:75%"></div></div></div>
    </div>`;
  }

  function piecesLinkList(valuation) {
    return `<ul class="metas-pieces-list">${(valuation.pieces || []).map(p =>
      `<li>${esc(p.symbol)} — ${esc(p.status)} <a href="#grids/${p.grid_id}">ver grid #${p.grid_id}</a></li>`).join('') || '<li class="muted">Sin piezas ligadas aún.</li>'}</ul>`;
  }

  const LEDGER_TYPE_FILTERS = {
    all: () => true,
    decisions: t => t.startsWith('META_OPERATOR_') || t === 'META_PLAN_EVALUATED',
    pieces: t => ['META_PIECE_LINKED', 'META_OPERATOR_OPEN', 'META_OPERATOR_WITHDRAW', 'META_OPERATOR_CLOSE'].includes(t),
    alerts: t => ['META_LOSS_WARNING', 'META_UNKNOWN', 'META_UNPROTECTED', 'META_VALUATION_PARTIAL', 'META_VALUATION_RECOVERED', 'META_CORTADA_POR_PERDIDA', 'META_VENCIDA', 'META_CUMPLIDA'].includes(t),
  };

  function ledgerRow(event) {
    const shadow = event.details?.shadow_decision;
    const real = event.details?.decision || event.details?.reason || event.event_type;
    return `<tr>
      <td class="mono">${window.ASPLEFormat?.formatDateTime?.(event.ts) ?? esc(event.ts)}</td>
      <td>${esc(event.event_type)}</td>
      <td>${esc(real)}${shadow ? `<br><span class="muted">Sombra (Scanner+Advisor): ${esc(shadow.symbol || '')} ${esc(shadow.strategy || '')}</span>` : ''}</td>
    </tr>`;
  }

  async function renderLedger(container, goalId) {
    const slot = container.querySelector('[data-ledger-slot]');
    if (!slot) return;
    slot.innerHTML = '<p class="muted">Cargando bitácora…</p>';
    try {
      const page = await apiGet(`/api/metas/${goalId}/ledger?page=${ledgerState.page}&page_size=${ledgerState.pageSize}&include_valuation=${ledgerState.includeValuation}`);
      const filterFn = LEDGER_TYPE_FILTERS[ledgerState.type] || LEDGER_TYPE_FILTERS.all;
      const rows = page.events.filter(e => filterFn(e.event_type)).reverse();
      const totalPages = Math.max(1, Math.ceil(page.total / page.page_size));
      slot.innerHTML = `<div class="metas-table-scroll"><table class="data-table metas-ledger-table"><thead><tr><th>Hora</th><th>Evento</th><th>Detalle</th></tr></thead>
        <tbody>${rows.map(ledgerRow).join('') || '<tr><td colspan="3" class="muted">Sin eventos.</td></tr>'}</tbody></table></div>
        <div class="metas-ledger-pager">
          <button type="button" class="button secondary" data-ledger-prev ${page.page <= 1 ? 'disabled' : ''}>← Anterior</button>
          <span>Página ${page.page} de ${totalPages}</span>
          <button type="button" class="button secondary" data-ledger-next ${page.page >= totalPages ? 'disabled' : ''}>Siguiente →</button>
        </div>`;
      slot.querySelector('[data-ledger-prev]')?.addEventListener('click', () => { ledgerState.page -= 1; renderLedger(container, goalId); });
      slot.querySelector('[data-ledger-next]')?.addEventListener('click', () => { ledgerState.page += 1; renderLedger(container, goalId); });
    } catch (error) { slot.innerHTML = `<p class="metas-error">${esc(error.message)}</p>`; }
  }

  function wireLedgerControls(container, goalId) {
    const typeSelect = container.querySelector('[data-ledger-type]');
    const valSwitch = container.querySelector('[data-ledger-valuation]');
    typeSelect?.addEventListener('change', () => { ledgerState.type = typeSelect.value; ledgerState.page = 1; renderLedger(container, goalId); });
    valSwitch?.addEventListener('change', () => { ledgerState.includeValuation = valSwitch.checked; ledgerState.page = 1; renderLedger(container, goalId); });
  }

  function renderActivePanel(goal, valuation, config) {
    const profile = (config.profiles || {})[goal.profile] || {};
    const statusNote = goal.status === 'PIECES_BLOCKED'
      ? `<p class="metas-blocked-note">⏸ Piezas bloqueadas: ${valuation.status === 'DESCONOCIDO' ? 'valoración DESCONOCIDO' : 'aviso del 75 % del límite de pérdida'}.</p>` : '';
    return `<div class="card metas-active" data-meta-id="${goal.id}">
      <div class="metas-active-head">
        <h2>Meta #${goal.id} · ${goalTitle(goal)} · ${PROFILE_LABELS[goal.profile] || goal.profile} · ${esc(goal.status)}</h2>
        <button type="button" class="button secondary" data-cancel-op="${goal.id}">Cancelar operación</button>
      </div>
      ${statusNote}
      ${valuationBlock(goal, valuation)}
      ${progressBars(goal, valuation, profile)}
      <div class="metas-pieces"><h3>Piezas</h3>${piecesLinkList(valuation)}</div>
      <section class="metas-recent-adjustments"><h3>Reajustes recientes</h3><ul data-recent-adjustments><li class="muted">Cargando…</li></ul></section>
      <div class="metas-ledger">
        <h3>Bitácora</h3>
        <div class="metas-ledger-controls">
          <select data-ledger-type>
            <option value="all">Todas</option>
            <option value="decisions">Decisiones</option>
            <option value="pieces">Aperturas/retiros/cierres</option>
            <option value="alerts">Alertas</option>
          </select>
          <label><input type="checkbox" data-ledger-valuation> Mostrar valoraciones (una fila por minuto)</label>
        </div>
        <div data-ledger-slot></div>
      </div>
    </div>`;
  }

  function renderSurplusPanel(goal) {
    return `<div class="card metas-active" data-meta-id="${goal.id}">
      <h2>Meta #${goal.id} · CUMPLIDA; esperando vender sobrantes</h2>
      <p class="muted">La meta ya llegó al objetivo. Los bots sobrantes solo venden con ganancia, sin plazo; lo que vendan es ganancia extra.</p>
      <p>Ganancia extra realizada hasta ahora: <b>${fmtMoney(goal.realized_extra_usdt)}</b> USDT</p>
      <p class="muted">Como no cuenta como meta activa, puedes crear otra meta mientras tanto.</p>
    </div>`;
  }

  function confirmCancel(container, metaId) {
    apiPost(`/api/metas/${metaId}/cancel-plan`).then(plan => {
      const body = `<table class="data-table"><thead><tr><th>Pieza</th><th>Estimado</th></tr></thead>
        <tbody>${plan.pieces.map(p => `<tr><td>${esc(p.symbol)}</td><td class="${pnlClass(p.estimated_pnl_usdt)}">${fmtMoney(p.estimated_pnl_usdt)} USDT</td></tr>`).join('')}</tbody></table>
        <fieldset class="metas-cancel-modes">
          <label><input type="radio" name="cancel-mode" value="liquidate" checked> Vender todo</label>
          <label><input type="radio" name="cancel-mode" value="repository"> Mover al repositorio</label>
        </fieldset>`;
      const modal = showActionDialog('Cancelar operación', body, async () => {
        const mode = modal.querySelector('[name="cancel-mode"]:checked').value;
        await apiPost(`/api/metas/${metaId}/cancel`, { mode, confirm: true, plan_id: plan.plan_id });
        await render(document.getElementById('metas-content'), currentHash);
      }, 'Confirmar cancelación');
    }).catch(error => showActionDialog('Error', `<p>${esc(error.message)}</p>`));
  }

  function wireActivePanel(container, goalId) {
    container.querySelector(`[data-cancel-op]`)?.addEventListener('click', e => confirmCancel(container, e.target.dataset.cancelOp));
    wireLedgerControls(container, goalId);
    renderRecentAdjustments(container, goalId);
    renderLedger(container, goalId);
  }

  async function renderRecentAdjustments(container, goalId) {
    const slot = container.querySelector('[data-recent-adjustments]');
    if (!slot) return;
    try {
      const page = await apiGet(`/api/metas/${goalId}/ledger?page=1&page_size=100&include_valuation=false`);
      const rows = page.events.filter(row => row.event_type === 'META_OPERATOR_RECONFIGURE').slice(0, 5);
      slot.innerHTML = rows.length ? rows.map(row => {
        const d = row.details || {}, old = d.old || {}, next = d.new || {};
        const piece = `Pieza ${row.grid_id || 'Meta'}`;
        const change = `piso ${fmtMoney(old.floor, 6)} → ${fmtMoney(next.floor, 6)}, techo ${fmtMoney(old.ceiling, 6)} → ${fmtMoney(next.ceiling, 6)}, niveles ${esc(old.n_levels ?? '—')} → ${esc(next.n_levels ?? '—')}`;
        const improvement = `probabilidad ${fmtMoney(d.estimated_probability_improvement_pp, 1)} puntos; avance mediano ${fmtMoney(d.estimated_progress_improvement_pct, 1)}%`;
        return `<li><b>${piece}</b>: ${change}. Motivo: ${esc(d.reason || 'reajuste del operador')}. Mejora estimada: ${improvement}.</li>`;
      }).join('') : '<li class="muted">Todavía no hay reajustes.</li>';
    } catch (error) { slot.innerHTML = `<li class="metas-error">${esc(error.message)}</li>`; }
  }

  // -- Zone 4: history -----------------------------------------------------

  function runRow(run) {
    const duration = run.finished_at ? `${run.started_at} → ${run.finished_at}` : 'en curso';
    const control = run.control_result
      ? `<div class="metas-control-compare"><span>Esta corrida: ${fmtPct(run.worst_result_pct)}</span><span>Control (Smart, sin abrir): ${fmtPct(run.control_result.result_pct)}</span></div>`
      : run.control_plan ? `<button type="button" class="button secondary" data-compare-run="${run.id}">Comparar con grupo de control</button>` : '';
    return `<div class="metas-run-row">
      <div><b>Corrida ${run.id}</b> — ${esc(run.status)} · ${duration}</div>
      <div>Peor resultado: ${fmtPct(run.worst_result_pct)} · Ganancia extra: ${fmtMoney(run.realized_extra_usdt)} USDT</div>
      ${control}
    </div>`;
  }

  async function renderHistory(slot, goalId) {
    if (!slot) return;
    try {
      const { runs } = await apiGet(`/api/metas/${goalId}/runs`);
      slot.innerHTML = runs.map(runRow).join('') || '<p class="muted">Sin corridas todavía.</p>';
      slot.querySelectorAll('[data-compare-run]').forEach(btn => btn.addEventListener('click', async () => {
        btn.disabled = true; btn.textContent = 'Comparando…';
        try { await apiPost(`/api/metas/${goalId}/runs/${btn.dataset.compareRun}/control-comparison`); await renderHistory(slot, goalId); }
        catch (error) { btn.disabled = false; btn.textContent = `Error: ${error.message}`; }
      }));
    } catch (error) { slot.innerHTML = `<p class="metas-error">${esc(error.message)}</p>`; }
  }

  function renderHistorySection(goals) {
    const showDiscarded = goals.some(g => g.status === 'DESCARTADA');
    return `<div class="card metas-history">
      <h2>Historial</h2>
      ${showDiscarded ? `<label><input type="checkbox" data-show-discarded> Mostrar descartadas</label>` : ''}
      <div class="metas-history-list">${goals.filter(g => g.status !== 'DRAFT').map(g => `
        <div class="metas-history-goal ${g.status === 'DESCARTADA' ? 'metas-discarded hidden' : ''}">
          <h3>Meta #${g.id} · ${PROFILE_LABELS[g.profile] || g.profile} · ${esc(g.status)}</h3>
          <div data-history-slot data-goal-id="${g.id}"></div>
        </div>`).join('')}</div>
    </div>`;
  }

  // -- Orchestration --------------------------------------------------------

  async function render(container, hash) {
    if (!container) return;
    stopAnalysisPoll();
    planVisible = false;
    container.innerHTML = '<p class="muted">Cargando…</p>';
    let config, goals;
    try {
      [config, goals] = await Promise.all([apiGet('/api/metas/config'), apiGet('/api/metas').then(r => r.metas)]);
    } catch (error) {
      container.innerHTML = `<p class="metas-error">No se pudo cargar Metas: ${esc(error.message)}</p>`;
      return;
    }
    if (!config.enabled) { container.innerHTML = renderDisabled(); return; }
    const activeStatuses = ['ACTIVE', 'PIECES_BLOCKED', 'CLOSING', 'TARGET_CLOSING'];
    const active = goals.find(g => activeStatuses.includes(g.status));
    const surplus = goals.find(g => g.status === 'SURPLUS_WAIT');
    const drafts = goals.filter(g => g.status === 'DRAFT');

    let html = '';
    if (active) {
      let valuation;
      try { valuation = await apiGet(`/api/metas/${active.id}/valuation`); }
      catch (error) { valuation = { status: 'DESCONOCIDO', pieces: [] }; }
      html += renderActivePanel(active, valuation, config);
    } else if (surplus) {
      html += renderSurplusPanel(surplus);
      html += `<div class="card metas-create-hint"><p class="muted">La meta cumplida no cuenta como activa.</p></div>`;
      html += renderCreateForm(config);
    } else {
      if (drafts.length) html += `<div class="metas-drafts">${drafts.map(renderDraftCard).join('')}</div>`;
      html += renderCreateForm(config);
    }
    html += renderHistorySection(goals);
    container.innerHTML = html;

    wireCreateForm(container);
    wireDraftActions(container);
    // Si había un análisis en curso (por ejemplo, tras recargar la página), retoma su consulta.
    const pending = loadJob();
    if (pending && drafts.some(g => Number(g.id) === Number(pending.metaId))) pollAnalysis(container, pending);
    else if (pending) clearJob();
    if (active) wireActivePanel(container, active.id);
    container.querySelector('[data-show-discarded]')?.addEventListener('change', e => {
      container.querySelectorAll('.metas-discarded').forEach(el => el.classList.toggle('hidden', !e.target.checked));
    });
    container.querySelectorAll('[data-history-slot]').forEach(slot => renderHistory(slot, slot.dataset.goalId));
  }

  function stopAutoRefresh() { if (refreshTimer) { clearInterval(refreshTimer); refreshTimer = null; } }

  function startAutoRefresh(container) {
    stopAutoRefresh();
    refreshTimer = setInterval(() => {
      if (document.visibilityState !== 'visible') return;
      if ((location.hash || '').split('/')[0] !== '#metas') return;
      if (analysisPollTimer || planVisible) return;
      render(container, currentHash);
    }, 30000);
  }

  window.loadMetasScreen = async function loadMetasScreen(hash) {
    currentHash = hash;
    const container = document.getElementById('metas-content');
    await render(container, hash);
    startAutoRefresh(container);
  };
})();
