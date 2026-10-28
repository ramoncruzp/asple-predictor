(function () {
  const esc = value => String(value ?? '\u2014').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  async function get(path) {
    const headers = window.gridApiToken ? {'X-API-Token':window.gridApiToken} : {};
    const response = await fetch((window.API_BASE || location.origin) + path, {headers});
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.detail || `HTTP ${response.status}`);
    return payload;
  }
  async function setBreakV2(profile, enabled) {
    const headers = {'Content-Type':'application/json', ...(window.gridApiToken ? {'X-API-Token':window.gridApiToken} : {})};
    const response = await fetch((window.API_BASE || location.origin) + '/api/metas/config/profile', {
      method:'PUT', headers, body:JSON.stringify({profile, values:{use_break_prob_v2:enabled}})});
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.detail || `HTTP ${response.status}`);
    return payload;
  }
  async function loadBreakV2(host) {
    if (!host) return;
    try {
      const config = await get('/api/metas/config');
      const profiles = config.profiles || {};
      const rows = ['RAPIDO','PRUDENTE'].map(profile => {
        const enabled = Boolean(profiles[profile]?.use_break_prob_v2);
        return `<article class="global-flag-row" data-break-v2-profile="${profile}"><div class="global-flag-copy"><h3>Meta ${profile === 'RAPIDO' ? 'Rápido' : 'Prudente'}: ruptura v2</h3><p>En sombra por defecto. Si se activa, sustituye solo la entrada de ruptura en la probabilidad de Meta.</p><small>Estado: <b>${enabled ? 'ENCENDIDO' : 'APAGADO'}</b></small></div><button class="button secondary" type="button" data-break-v2="${profile}" data-enabled="${enabled}">${enabled ? 'Apagar' : 'Encender'}</button></article>`;
      }).join('');
      host.insertAdjacentHTML('beforeend', `<section class="meta-break-v2-settings"><h3>Ruptura v2 de Meta</h3><p>La señal se guarda para comparación. APAGADO conserva la entrada y las decisiones actuales.</p>${rows}</section>`);
      host.querySelectorAll('[data-break-v2]').forEach(button => button.addEventListener('click', async () => {
        button.disabled = true;
        const profile = button.dataset.breakV2;
        try { await setBreakV2(profile, button.dataset.enabled !== 'true'); await window.loadLabPage(); }
        catch (error) { const status = document.getElementById('lab-global-settings-status'); if (status) status.textContent = error.message; }
        finally { button.disabled = false; }
      }));
    } catch (error) {
      host.insertAdjacentHTML('beforeend', `<p class="cuenta-error">No se pudo cargar Ruptura v2: ${esc(error.message)}</p>`);
    }
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
  window.loadLabPage = async function () {
    await Promise.all([window.ASPLEGlobalFlags?.load('lab-global-settings-content'), loadStats()]);
    const host = document.getElementById('lab-global-settings-content');
    await window.ASPLEMetaLearning?.loadSwitches(host);
    host?.querySelector('.meta-break-v2-settings')?.remove();
    await loadBreakV2(host);
  };
})();
