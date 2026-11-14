(function () {
  const esc = value => String(value ?? '\u2014').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  async function get(path) {
    const headers = window.gridApiToken ? {'X-API-Token':window.gridApiToken} : {};
    const response = await fetch((window.API_BASE || location.origin) + path, {headers});
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.detail || `HTTP ${response.status}`);
    return payload;
  }
  async function createRound(body) {
    const headers = {'Content-Type':'application/json', ...(window.gridApiToken ? {'X-API-Token':window.gridApiToken} : {})};
    const response = await fetch((window.API_BASE || location.origin) + '/api/metas/tournament/rounds', {method:'POST', headers, body:JSON.stringify(body)});
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.detail || `HTTP ${response.status}`);
    return payload;
  }
  async function loadTournament() {
    const screen = document.getElementById('screen-lab');
    if (!screen) return;
    let host = document.getElementById('lab-tournament');
    if (!host) {
      host = document.createElement('section'); host.id = 'lab-tournament'; host.className = 'card lab-section';
      host.innerHTML = '<h2>Torneo de estrategias</h2><p>Saldo libre: <b data-free>consultando…</b></p><form data-round><label>Capital por competidor <input name="capital" type="number" min="10" value="50" required></label> <label>Objetivo USDT <input name="target" type="number" min="0.01" step="0.01" value="1.5" required></label> <label>Plazo días <input name="days" type="number" min="1" value="20" required></label><fieldset><legend>Competidores</legend>' + ['v1','v2','cazador','escalera','seguidor','control'].map(x => `<label><input type="checkbox" name="competitor" value="${x}" checked> ${x}</label> `).join('') + '</fieldset><button type="submit">Crear ronda y activar metas</button></form><p data-status role="status"></p><div data-rounds></div>';
      screen.insertBefore(host, screen.children[2] || null);
      host.querySelector('[data-round]').addEventListener('submit', async event => {
        event.preventDefault(); const form = event.currentTarget, status = host.querySelector('[data-status]');
        const competitors = [...form.querySelectorAll('[name="competitor"]:checked')].map(x => x.value);
        try { const result = await createRound({competitors, capital_usdt:Number(form.capital.value), target_value:Number(form.target.value), target_type:'USDT', profile:'RAPIDO', duration_days:Number(form.days.value)}); status.textContent = `Ronda ${result.ronda} activada: ${result.goals.map(x => `Meta #${x.id} (${x.config_snapshot.competidor})`).join(', ')}. Saldo libre antes de crear: ${Number(result.saldo_libre_usdt).toFixed(2)} USDT.`; }
        catch (error) { status.textContent = `No se creó la ronda: ${error.message}`; }
      });
    }
    try { const result = await get('/api/metas/tournament/availability'); host.querySelector('[data-free]').textContent = `${Number(result.free_usdt).toFixed(2)} USDT (reservado ${Number(result.reserved_usdt).toFixed(2)})`; }
    catch (error) { host.querySelector('[data-free]').textContent = `no disponible: ${error.message}`; }
  }
  function renderStats(stats) {
    const host = document.getElementById('lab-stoploss-stats-content');
    if (!host) return;
    if (!stats) { host.innerHTML = '<p class="muted">Sin datos disponibles para los últimos 7 días.</p>'; return; }
    host.innerHTML = `<p><b>CELL_STOPLOSS</b> (vendó): ${stats.sold}</p><p><b>STOPLOSS_SUSPENDED</b> (se habría vendido): ${stats.suspended}</p><p class="muted">Neto de CELL_STOPLOSS: sin datos en el endpoint de eventos.</p><small class="muted">Hasta 50 grids y 500 eventos recientes por grid.</small>`;
  }
  async function loadStats() {
    try {
      const statuses = 'OPENING,ACTIVE,PAUSED,CLOSING,HOLDING,CLOSED,CANCELLED,FAILED,ERROR';
      const statusQuery = new URLSearchParams({status: statuses});
      const result = await get(`/api/grids?${statusQuery.toString()}`);
      const grids = (result.grids || []).slice(0, 50);
      const eventQuery = new URLSearchParams({limit: "500"});
      const responses = await Promise.all(grids.map(grid => get(`/api/grids/${encodeURIComponent(grid.id)}/events?${eventQuery.toString()}`)));
      const cutoff = Date.now() - 7 * 24 * 60 * 60 * 1000;
      let sold = 0, suspended = 0;
      for (const response of responses) for (const event of response.events || []) {
        const ts = window.ASPLEFormat.parseApiTime(event.ts || '');
        if (!Number.isFinite(ts) || ts < cutoff) continue;
        if (event.type === 'CELL_STOPLOSS') sold++;
        if (event.type === 'STOPLOSS_SUSPENDED') suspended++;
      }
      renderStats({sold, suspended});
    } catch (_) { renderStats(null); }
  }
  async function loadEngineShadow() {
    const host = document.getElementById('lab-engine-shadow-content');
    if (!host) return;
    try {
      const query = new URLSearchParams({limit: '50'});
      const payload = await get(`/api/metas/engine-shadow-decisions?${query.toString()}`);
      const rows = payload.events || [];
      if (!rows.length) { host.innerHTML = '<p class="muted">Sin decisiones v1/v2 todavía.</p>'; return; }
      const decision = value => value?.result?.decision || value?.decision || 'sin decisión';
      const riskText = value => {
        const metrics = value?.v2_metrics || value?.result?.v2_metrics || {};
        const percent = number => number != null && number !== '' && Number.isFinite(Number(number)) ? `${(Number(number) * 100).toFixed(1)} %` : '—';
        const usdt = number => number != null && number !== '' && Number.isFinite(Number(number)) ? `${Number(number).toFixed(4)} USDT` : '—';
        return `P(límite) ${percent(metrics.p_loss)} · desliz ${usdt(metrics.cut_slippage_mean_usdt)} · P(inventario) ${percent(metrics.p_inventory)}`;
      };
      const outcomeText = outcome => {
        if (!outcome) return 'Pendiente de marcador';
        const pnl = Number(outcome.pnl_usdt);
        const gain = Number.isFinite(pnl) ? `${pnl.toFixed(4)} USDT` : 'sin ganancia';
        const days = Number.isFinite(Number(outcome.days)) ? `${Number(outcome.days).toFixed(2)} d` : 'sin duraci\u00f3n';
        return `${gain} \u00b7 meta ${outcome.reached ? 'alcanzada' : 'no alcanzada'} \u00b7 l\u00edmite ${outcome.loss_cut ? 'tocado' : 'no tocado'} \u00b7 ${days} \u00b7 ${esc(outcome.simulator || outcome.source || 'resultado')}`;
      };
      host.innerHTML = `<div class="metas-learning-scroll"><table class="data-table"><thead><tr><th>Meta</th><th>Fecha</th><th>Motor activo</th><th>Decisión activa</th><th>Motor sombra</th><th>Decisión sombra</th><th>Métricas v2</th><th>Resultado de la ventana</th></tr></thead><tbody>${rows.map(row => `<tr><td>${esc(row.goal_id)}</td><td>${esc(row.review_at)}</td><td>${esc(row.active_engine)}</td><td>${esc(decision(row.active_decision))}</td><td>${esc(row.shadow_engine)}</td><td>${esc(decision(row.shadow_decision))}</td><td>${esc(riskText(row.shadow_decision))}</td><td>${outcomeText(row.shadow_outcome)}</td></tr>`).join('')}</tbody></table></div>`;
    } catch (error) { host.innerHTML = `<p class="muted">No se pudo leer la bitácora de motores: ${esc(error.message)}</p>`; }
  }
  window.loadLabPage = async function () {
    await Promise.all([window.ASPLEGlobalFlags?.load('lab-global-settings-content'), loadStats(), loadEngineShadow(), loadTournament()]);
    const host = document.getElementById('lab-global-settings-content');
    await window.ASPLEMetaLearning?.loadSwitches(host);
  };
})();
