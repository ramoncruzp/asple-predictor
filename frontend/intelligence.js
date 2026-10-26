(function () {
  const $ = selector => document.querySelector(selector);
  const esc = value => escapeHtml(value ?? '—');
  const num = value => value == null || !Number.isFinite(Number(value)) ? '—' : Number(value).toLocaleString('es-ES', {maximumFractionDigits: 3});
  let updatedAt = 0, ageTimer = null;
  function updateAge() {
    const node = $('#intelligence-updated');
    if (!node || !updatedAt) return;
    const mins = Math.max(0, Math.floor((Date.now() - updatedAt) / 60000));
    node.textContent = mins < 1 ? 'Actualizado hace menos de un minuto' : `Actualizado hace ${mins} min`;
    ageTimer = setTimeout(updateAge, 60000);
  }
  const table = (headers, rows) => `<div class="table-card"><table class="data-table"><thead><tr>${headers.map(x => `<th>${esc(x)}</th>`).join('')}</tr></thead><tbody>${rows.length ? rows.map(row => `<tr>${row.map(x => `<td>${esc(x)}</td>`).join('')}</tr>`).join('') : `<tr><td colspan="${headers.length}">Aún sin datos guardados.</td></tr>`}</tbody></table></div>`;
  const signalLabel = name => ({touch_buy_to_sell:'Toque compra→venta',volatility:'Volatilidad',break_prob:'Probabilidad de ruptura',direction:'Dirección',scanner:'Scanner',advisor:'Advisor'}[name] || name);
  const baseWeight = name => name === 'touch_buy_to_sell' ? 0 : .25;
  const readiness = item => { const enye = String.fromCharCode(241), oacute = String.fromCharCode(243), bullet = String.fromCharCode(183); const checks = String.fromCharCode(10003), cross = String.fromCharCode(215); const reasons = {muestra:`Una se${enye}al alcanza 30 muestras efectivas`,significancia_y_cambio:'p Holm menor que 0,05 y peso distinto de su base',validacion:`Validaci${oacute}n con al menos 5 muestras`,mejora:'Log-loss y Brier superan solo p_replay'}; return `<article class="card intelligence-readiness ${esc(item.status)}"><h3>${esc(item.label)}</h3><p>${num(item.best_effective_samples)} de 30 muestras efectivas; faltan ${num(item.remaining)}.</p><progress max="30" value="${Math.min(30, Number(item.best_effective_samples || 0))}"></progress>${item.status === 'verde' ? `<p>Se${enye}ales con cambio aceptado: ${(item.signals || []).filter(x => Math.abs(Number(x.weight ?? baseWeight(x.name)) - baseWeight(x.name)) > 1e-9).map(x => `${esc(signalLabel(x.name))} ${num(x.weight)}`).join(` ${bullet} `) || 'ninguna'}</p>` : ''}<ul>${Object.entries(item.conditions || {}).map(([name, ok]) => `<li>${ok ? checks : cross} ${esc(reasons[name] || name)}</li>`).join('')}</ul><p class="muted">Este medidor informa; no modifica interruptores.</p></article>`; };
  function render(data) {
    const d = data.data || {}, s = data.samples || {}, r = data.replay || {}, profiles = data.regressions?.profiles || {};
    const forecastRows = (d.volatility_forecasts || []).map(x => [x.symbol,x.model_name,`${x.horizon_h} h`,x.count,x.first_at,x.last_at]);
    const predictionRows = (d.direction_predictions || []).map(x => [x.symbol,x.model_name,x.interval,x.count,x.first_at,x.last_at]);
    const sampleRows = (s.by_profile_source_symbol || []).map(x => [x.profile,x.source_kind,x.symbol,x.count,x.first_at,x.last_at]);
    const labelRows = (s.labels || []).map(x => [x.horizon,x.status,x.outcome,x.count]);
    const versionRows = (data.regressions?.versions || []).map(v => {
      const signals = v.summary?.signals || {};
      return [v.id,v.profile,v.source,v.status,v.created_at,Object.entries(signals).map(([name,x]) => `${signalLabel(name)}: β ${num(x.candidate)}; EE ${num(x.standard_error_clustered)}; p ${num(x.p_value)}; Holm ${num(x.p_value_holm)}; propuesto ${num(x.candidate)} / aplicado ${num(x.weight)}; ${x.reason || ''}`).join(' · '),`entreno ${num(v.summary?.training_effective)}; validación ${num(v.summary?.validation_effective)}; corte ${v.summary?.training_cutoff || '—'}`,JSON.stringify(v.summary?.validation || {}),JSON.stringify(v.summary?.baseline_validation || {})];
    });
    const recent = (data.decisions?.recent || []).map(x => [x.at,x.meta_id,x.event_type,JSON.stringify(x.choices || {})]);
    const runRows = (data.runs || []).map(x => [x.id,x.started_at,x.status,x.finished_at,JSON.stringify(x.summary?.timings_s || {}),JSON.stringify(x.summary?.pending_template_ids || [])]);
    const coverageRows = Object.entries(data.signals || {}).map(([name,x]) => [signalLabel(name),x.observations,x.sample_coverage,`${num(x.coverage_pct)}%`,num(x.mean),num(x.stddev),num(x.high_success_rate),num(x.low_success_rate)]);
    const flags = Object.entries(data.global_flags || {}).map(([name,value]) => [name,value == null ? 'No disponible' : value ? 'Encendido' : 'Apagado']);
    const content = $('#intelligence-content');
    content.innerHTML = `
      <section class="card"><h2>B1 · Datos disponibles</h2><p>Las velas no se guardan en esta base; el origen histórico y sus límites se describen aquí. Los demás totales vienen de registros persistidos.</p><p>${esc(d.candles?.reason || 'Velas no disponibles.')}</p><h3>Pronósticos de volatilidad</h3>${table(['Moneda','Modelo','Horizonte','Cantidad','Primero','Último'],forecastRows)}<h3>Predicciones de dirección</h3>${table(['Moneda','Modelo','Intervalo','Cantidad','Primero','Último'],predictionRows)}<h3>Observaciones de señales</h3>${table(['Moneda','Cantidad','Primero','Último'],(d.meta_signal_observations_by_symbol||[]).map(x=>[x.symbol,x.count,x.first_at,x.last_at]))}<h3>Observaciones por se\u00f1al y moneda</h3>${table(['Se\u00f1al','Moneda','Cantidad'],(d.meta_signal_counts_by_symbol||[]).map(x=>[x.signal,x.symbol,x.count]))}<h3>Bitácora por meta y evento</h3>${table(['Meta','Evento','Cantidad'],(d.meta_ledger||[]).map(x=>[x.meta_id,x.event_type,x.count]))}</section>
      <section class="card"><h2>B2 · Muestras y etiquetas</h2><p>Las filas distinguen observaciones en vivo e histórico reconstruido; las etiquetas inmaduras no cuentan como éxito o fracaso.</p>${table(['Perfil','Origen','Moneda','Muestras','Primera','Última'],sampleRows)}<h3>Etiquetas</h3>${table(['Horizonte','Estado','Resultado','Cantidad'],labelRows)}<p>Recalculación pendiente: ${num(s.recalculate_pending)}. Exclusiones: ${esc(JSON.stringify(s.exclusions||[]))}</p><p>Muestras efectivas por perfil/señal: ${esc(JSON.stringify(s.effective_by_signal||{}))}</p></section>
      <section class="card"><h2>B3 · Probabilidad base y replay</h2><p>p_replay es la referencia del replay antes de aplicar señales. La suavizada evita probabilidades exactas de 0 o 1.</p><p>Ventanas por muestra: ${esc(JSON.stringify(r.window_histogram||{}))}</p><p>p_replay crudo/suavizado por intervalo: ${esc(JSON.stringify(r.raw_probability_bins||{}))}</p><p>Entradas persistidas en cach\u00e9: ${num(r.cache_entries)} &middot; aciertos en la \u00faltima evaluaci\u00f3n: ${num(r.last_analysis?.timings_s?.replay_cache_hits)} &middot; simulaciones nuevas: no se persiste un contador separado.</p></section>
      <section class="card"><h2>B4 · Regresión y validación</h2><p>Los errores y frenos se leen de las versiones guardadas; abrir esta pantalla no entrena ni simula.</p>${table(['Versión','Perfil','Origen','Estado','Fecha','Señales: coeficiente/errores/prueba/freno','Tamaño y corte','Validación','Solo p_replay'],versionRows)}<p>Últimas versiones guardadas: ${num((data.regressions?.versions||[]).length)}.</p></section>
      <section class="card"><h2>B5 · Señales</h2><p>Cobertura, promedio, dispersión y tasas alta/baja se calculan solo si están guardadas en la corrida; guion significa que esa estadística no fue persistida.</p>${table(['Señal','Observaciones','Muestras','Cobertura','Promedio','Desviación','Éxito alta','Éxito baja'],coverageRows)}</section>
      <section class="card"><h2>B6 · Decisiones</h2><p>La comparación usa eventos etiquetados disponibles; resultados futuros faltantes se muestran como ausencia de muestra.</p>${table(['Fecha','Meta','Tipo de evento','Decisiones registradas'],recent)}<p>Comparación acumulada por perfil: ${esc(JSON.stringify(Object.fromEntries(Object.entries(profiles).map(([p,v])=>[p,v.decision_comparison||{}]))))}</p></section>
      <section class="card"><h2>B7 · Corridas del aprendizaje</h2><p>Los tiempos describen cada etapa registrada; “pendientes” son plantillas no procesadas en la corrida.</p>${table(['ID','Inicio','Estado','Fin','Tiempos','Pendientes'],runRows)}</section>
      <section class="card"><h2>B8 · Interruptores</h2><p>El modo Meta y los interruptores Smart son de solo lectura aquí. Cambios de pesos: <a href="#lab">Laboratorio → Reglas globales</a>.</p>${table(['Ajuste','Estado'],flags)}${Object.entries(data.switches||{}).map(([profile,item])=>`<p>Meta ${profile}: ${item.enabled?'ENCENDIDO':'APAGADO'} · <a href="#lab">cambiar en Laboratorio</a></p>${readiness(item.readiness||{})}`).join('')}<p><a href="#models">Abrir Modelos</a> · <a href="#battle">Abrir Battle de Modelos</a></p></section>`;
    updatedAt = Date.now(); if (ageTimer) clearTimeout(ageTimer); updateAge();
  }
  async function load() {
    $('#intelligence-content').innerHTML = '<p class="muted">Consultando agregados guardados…</p>';
    try { render(await api.get('/api/intelligence?page=1&page_size=50')); }
    catch (error) { $('#intelligence-content').innerHTML = `<p class="cuenta-error">No se pudo consultar Inteligencia: ${esc(error.message)}</p>`; }
  }
  window.loadIntelligencePage = load;
  document.addEventListener('DOMContentLoaded', () => $('#intelligence-refresh')?.addEventListener('click', load));
})();
