(function () {
  const esc = value => String(value ?? '\u2014').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  async function get(path) {
    const headers = window.gridApiToken ? {'X-API-Token':window.gridApiToken} : {};
    const response = await fetch((window.API_BASE || location.origin) + path, {headers});
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.detail || `HTTP ${response.status}`);
    return payload;
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
        const ts = Date.parse(event.ts || '');
        if (!Number.isFinite(ts) || ts < cutoff) continue;
        if (event.type === 'CELL_STOPLOSS') sold++;
        if (event.type === 'STOPLOSS_SUSPENDED') suspended++;
      }
      renderStats({sold, suspended});
    } catch (_) { renderStats(null); }
  }
  window.loadLabPage = async function () {
    await Promise.all([window.ASPLEGlobalFlags?.load('lab-global-settings-content'), loadStats()]);
  };
})();
