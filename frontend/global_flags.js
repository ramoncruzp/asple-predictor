(function () {
  const updatedAt = new Map();
  let pending = null;
  let busy = false;
  const esc = value => String(value ?? '\u2014').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  async function post(body) {
    const headers = { 'Content-Type': 'application/json', ...(window.gridApiToken ? { 'X-API-Token': window.gridApiToken } : {}) };
    const response = await fetch((window.API_BASE || location.origin) + '/api/settings/flags', { method: 'POST', headers, body: JSON.stringify(body) });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) { const error = new Error(payload.detail || `HTTP ${response.status}`); error.status = response.status; throw error; }
    return payload;
  }
  function render(flags, host) {
    if (!host) return;
    host.innerHTML = (flags || []).map(flag => {
      const changed = Boolean(flag.current) !== Boolean(flag.default);
      const when = updatedAt.get(flag.key);
      return `<article class="global-flag-row" data-flag-key="${esc(flag.key)}"><div class="global-flag-copy"><h3>${esc(flag.label)}</h3><p>${esc(flag.description)}</p><small class="muted">Estado: <b>${flag.current ? 'Encendido' : 'Apagado'}</b> · Origen: ${esc(flag.origin)} · Valor por defecto: ${flag.default ? 'Encendido' : 'Apagado'} · ${flag.requires_restart ? 'Requiere reiniciar' : 'Efecto en vivo'}</small>${changed ? '<span class="lab-default-badge">distinto del valor por defecto</span>' : ''}${when ? `<small class="global-flag-updated">Cambiado a las ${esc(when)} (hora local)</small>` : ''}</div><button class="button secondary" type="button" data-flag-change="${esc(flag.key)}" data-flag-value="${flag.current ? 'false' : 'true'}">${flag.current ? 'Apagar' : 'Encender'}</button></article>`;
    }).join('') || '<p class="muted">No hay interruptores editables.</p>';
  }
  async function load(containerId) {
    const host = document.getElementById(containerId);
    if (!host) return;
    try {
      const headers = window.gridApiToken ? { 'X-API-Token': window.gridApiToken } : {};
      const response = await fetch((window.API_BASE || location.origin) + '/api/settings/flags', { headers });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(payload.detail || `HTTP ${response.status}`);
      render(payload.flags, host);
    } catch (error) {
      host.innerHTML = `<p class="cuenta-error">No se pudieron cargar los ajustes globales: ${esc(error.message)}</p>`;
    }
  }
  function statusFor(containerId) { return document.getElementById(containerId === 'global-settings-content' ? 'global-settings-status' : 'lab-global-settings-status'); }
  function timeLocal(value) { const date = new Date(value); return Number.isNaN(date.getTime()) ? new Date().toLocaleTimeString([], {hour:'2-digit', minute:'2-digit', hour12:false}) : date.toLocaleTimeString([], {hour:'2-digit', minute:'2-digit', hour12:false}); }
  document.addEventListener('click', async event => {
    const button = event.target.closest('[data-flag-change]');
    if (button && !busy) {
      const host = button.closest('.global-flag-list') || button.closest('#global-settings-content');
      const containerId = host?.id;
      if (!containerId) return;
      const key = button.dataset.flagChange, value = button.dataset.flagValue === 'true';
      const row = button.closest('[data-flag-key]'), label = row?.querySelector('h3')?.textContent || key;
      const status = statusFor(containerId);
      busy = true; button.disabled = true;
      if (status) status.textContent = 'Preparando el cambio'+String.fromCharCode(0x2026);
      try {
        const preview = await post({key, value, dry_run:true, confirm:false});
        const plan = preview.plan || {};
        pending = {key, value, containerId};
        document.getElementById('global-flag-dialog-message').textContent = `${value ? 'Encender' : 'Apagar'} el interruptor ${label}. Plan: ${plan.previous ? 'Encendido' : 'Apagado'} ${String.fromCharCode(0x2192)} ${value ? 'Encendido' : 'Apagado'}. El cambio se guardar${String.fromCharCode(0x00e1)} y aplicar${String.fromCharCode(0x00e1)} ${plan.requires_restart ? 'tras reiniciar' : 'sin reiniciar'}.`;
        document.getElementById('global-flag-dialog').showModal();
      } catch (error) { if (status) status.textContent = error.message || 'No se pudo preparar el cambio.'; }
      finally { busy = false; button.disabled = false; }
      return;
    }
    if (event.target.closest('#global-flag-confirm') && pending && !busy) {
      const dialog = document.getElementById('global-flag-dialog'), confirmButton = document.getElementById('global-flag-confirm');
      const status = statusFor(pending.containerId);
      busy = true; confirmButton.disabled = true;
      if (status) status.textContent = 'Guardando el ajuste'+String.fromCharCode(0x2026);
      try {
        const result = await post({key:pending.key, value:pending.value, dry_run:false, confirm:pending.value === true});
        if (result.updated_at) updatedAt.set(pending.key, timeLocal(result.updated_at));
        const containerId = pending.containerId; pending = null; dialog.close();
        if (status) status.textContent = `Cambiado a las ${updatedAt.get(result.flag?.key || '') || timeLocal(result.updated_at)} (hora local).`;
        await load(containerId);
      } catch (error) { if (status) status.textContent = error.message || 'No se pudo guardar el ajuste global.'; }
      finally { busy = false; confirmButton.disabled = false; }
    }
  });
  document.getElementById('global-flag-dialog')?.addEventListener('close', () => { pending = null; });
  window.ASPLEGlobalFlags = {load, render};
})();
