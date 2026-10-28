(function () {
  const $ = selector => document.querySelector(selector);
  const esc = value => escapeHtml(value ?? '\u2014');
  const num = value => value == null || !Number.isFinite(Number(value)) ? '\u2014' : Number(value).toLocaleString('es-ES', {maximumFractionDigits: 3});
  const pct = value => value == null ? '\u2014' : `${(Number(value) * 100).toLocaleString('es-ES', {maximumFractionDigits: 1})}%`;
  let updatedAt = 0, ageTimer = null, decisionPage = 1, decisionMeta = '';
  function updateAge() {
    const node = $('#intelligence-updated'); if (!node || !updatedAt) return;
    const mins = Math.max(0, Math.floor((Date.now() - updatedAt) / 60000));
    node.textContent = mins < 1 ? 'Actualizado hace menos de un minuto' : `Actualizado hace ${mins} min`;
    ageTimer = setTimeout(updateAge, 60000);
  }
  const table = (headers, rows, cls = '') => `<div class="table-card ${cls}"><table class="data-table"><thead><tr>${headers.map(x => `<th>${esc(x)}</th>`).join('')}</tr></thead><tbody>${rows.length ? rows.map(row => `<tr class="${row.includes('Pocas muestras') ? 'few-samples' : ''}">${row.map(x => `<td>${esc(x)}</td>`).join('')}</tr>`).join('') : `<tr><td colspan="${headers.length}">A\u00fan sin datos guardados.</td></tr>`}</tbody></table></div>`;
  const detail = (title, explanation, content) => `<details class="intelligence-detail"><summary>${esc(title)}</summary><p>${esc(explanation)}</p>${content}</details>`;
  const signalLabel = name => ({touch_buy_to_sell:'Toque compra\u2192venta',volatility:'Volatilidad',break_prob:'Probabilidad de ruptura',direction:'Direcci\u00f3n',scanner:'Scanner',advisor:'Advisor'}[name] || name);
  const baseWeight = name => name === 'touch_buy_to_sell' ? 0 : .25;
  const readiness = item => {
    const reasons = {muestra:'Una se\u00f1al alcanza 30 muestras efectivas',significancia_y_cambio:'p Holm menor que 0,05 y peso distinto de su base',validacion:'Validaci\u00f3n con al menos 5 muestras',mejora:'Log-loss y Brier superan solo p_replay'};
    return `<article class="card intelligence-readiness ${esc(item.status)}"><h3>${esc(item.label)}</h3><p>${num(item.best_effective_samples)} de 30 muestras efectivas; faltan ${num(item.remaining)}.</p><progress max="30" value="${Math.min(30, Number(item.best_effective_samples || 0))}"></progress><ul>${Object.entries(item.conditions || {}).map(([name, ok]) => `<li>${ok ? '\u2713' : '\u00d7'} ${esc(reasons[name] || name)}</li>`).join('')}</ul><p class="muted">Este medidor informa; no modifica interruptores.</p></article>`;
  };
  function replayBars(rows) {
    const max = Math.max(1, ...(rows || []).map(x => Number(x.windows_mean || 0)));
    return `<div class="replay-chart" role="img" aria-label="Probabilidad base y ventanas por intervalo">${(rows || []).map(x => `<div class="replay-chart-row"><span>${esc(x.bin)}</span><div class="replay-chart-bars"><i title="Cruda ${pct(x.raw_mean)}" style="width:${Math.max(0, Number(x.raw_mean || 0) * 100)}%"></i><b title="Suavizada ${pct(x.smoothed_mean)}" style="width:${Math.max(0, Number(x.smoothed_mean || 0) * 100)}%"></b><em title="Ventanas ${num(x.windows_mean)}" style="width:${Math.min(100, Number(x.windows_mean || 0) / max * 100)}%"></em></div><small>cruda ${pct(x.raw_mean)} · suavizada ${pct(x.smoothed_mean)} · ${num(x.windows_mean)} ventanas (${num(x.count)} muestras)</small></div>`).join('')}</div><p class="muted">Barras azul y verde: p_replay crudo y suavizado. Barra \u00e1mbar: promedio de ventanas disponibles por muestra.</p>`;
  }
  function decisionCell(choice) {
    if (!choice) return 'Sin registro';
    const action = choice.decision || choice.action || 'mantener';
    const strategy = choice.strategy || choice.piece_type || '\u2014';
    return `${action} \u00b7 ${choice.symbol || '\u2014'} \u00b7 ${strategy} \u00b7 ${pct(choice.probability)}`;
  }
  function decisionTable(rows) {
    return `<div class="table-card"><table class="data-table"><thead><tr><th>Fecha</th><th>Meta</th><th>Tipo</th><th>Real</th><th>Scanner + Advisor</th><th>Aprendida</th><th>Detalle</th></tr></thead><tbody>${rows.length ? rows.map((x, i) => {
      const c = x.choices || {}, cells = ['real','scanner_advisor','learned'].map(k => decisionCell(c[k]));
      const current = c.current_piece_estimate, piece = current?.piece || {}, estimate = current?.estimate || {};
      const currentText = current ? `Pieza actual: ${piece.symbol || ''} · ${num(piece.capital_usdt)} USDT · rango ${num(piece.floor)}–${num(piece.ceiling)} · ${num(piece.n_levels)} niveles · probabilidad histórica ${pct(estimate.probability_replay)} · ajustada ${pct(estimate.probability_adjusted || estimate.probability)} · avance mediano ${num(estimate.median_progress_pct)} %` : '';
      const differ = new Set(cells).size > 1;
      return `<tr class="${differ ? 'decision-diff' : ''}"><td>${esc(x.at)}</td><td>${num(x.meta_id)}</td><td>${esc(x.event_type)}</td>${cells.map(v => `<td>${esc(v)}</td>`).join('')}<td>${currentText ? `<p>${esc(currentText)}</p>` : ''}<details><summary>Ver JSON</summary><pre>${esc(JSON.stringify(x.details || {}, null, 2))}</pre></details></td></tr>`;
    }).join('') : '<tr><td colspan="7">Aún sin decisiones guardadas.</td></tr>'}</tbody></table></div>`;
  }
  function render(data) {
    const d = data.data || {}, s = data.samples || {}, r = data.replay || {}, profiles = data.regressions?.profiles || {}, hits = data.hits || {};
    const readinessHost = $('#intelligence-readiness');
    if (readinessHost) {
      const names = {learned_weights:'Pesos aprendidos', centered_signals:'Señales centradas', break_prob_v2:'Ruptura v2', touch_bots:'Bots de toque', extended_allocation:'Asignación ampliada'};
      const order = {'Listo para encender':0,'No conviene':1,'Evaluando':2,'Recolectando datos':3};
      const rows = Object.entries(data.readiness_switches || {}).flatMap(([profile, items]) => Object.entries(items || {}).map(([key, item]) => ({profile,key,item})))
        .sort((a,b) => (order[a.item.state] ?? 4) - (order[b.item.state] ?? 4));
      const gridRows = (data.grid_intelligence?.grids || []).map(grid => {
        const labels = grid.mature_labels || {}, actions = Object.entries(labels.readiness_by_action || {});
        const recs = (grid.recommendations_24h || []).map(rec => `${esc(rec.action)} \u00b7 ${esc(rec.at)} \u00b7 ganancia ${num(rec.expected_gain_7d)} USDT (mantener ${num(rec.baseline_gain_7d)})`).join('<br>') || 'Sin recomendaciones en las \u00faltimas 24 h.';
        const evidence = actions.length ? actions.map(([action, readiness]) => {
          const criteria = (readiness.criteria || []).map(item => `<li>${item.ok ? 'Cumple' : 'Pendiente'}: ${esc(item.text)}</li>`).join('');
          const estimate = readiness.estimated_ready_at ? ` \u00b7 estimado: ${esc(readiness.estimated_ready_at)}` : '';
          return `<div><b>${esc(action)}: ${esc(readiness.state)}</b> \u00b7 ${num(readiness.n)}/${num(readiness.n_required)} etiquetas <progress max="${num(readiness.n_required || 30)}" value="${Math.min(Number(readiness.n_required || 30), Number(readiness.n || 0))}"></progress><small>${esc(readiness.summary || '')}${estimate}</small><ul>${criteria}</ul></div>`;
        }).join('') : 'Sin datos suficientes de etiquetas maduras.';
        return `<article class="intelligence-readiness ${grid.intel_apply_enabled ? 'amarillo' : 'gris'}"><h3>Grid ${num(grid.grid_id)} \u00b7 ${esc(grid.symbol || '')} \u00b7 ${esc(grid.strategy || '')}</h3><p>Aplicaci\u00f3n: ${grid.intel_apply_enabled ? 'ENCENDIDA' : 'APAGADA'}</p><p>${recs}</p><p>Etiquetas maduras por acci\u00f3n: ${evidence}</p></article>`;
      }).join('');
      readinessHost.innerHTML = `<section class="card"><h2>¿Qué puedo encender?</h2><p>El medidor informa. Ramón decide; el sistema nunca activa interruptores automáticamente.</p>${rows.map(({profile,key,item}) => {
        const state = item.state || 'Recolectando datos', color = state === 'Listo para encender' ? 'verde' : state === 'No conviene' ? 'rojo' : state === 'Evaluando' ? 'amarillo' : 'gris';
        const estimate = item.estimated_ready_at ? `estimado: ${esc(item.estimated_ready_at)}` : 'estimado: sin ritmo suficiente';
        const destination = '#lab';
        return `<article class="intelligence-readiness ${color}"><h3>${esc(names[key] || key)} · Meta ${profile === 'RAPIDO' ? 'Rápido' : 'Prudente'}</h3><b>${esc(state)}</b> · ${num(item.n)} / ${num(item.n_required)} <progress max="${num(item.n_required || 30)}" value="${Math.min(Number(item.n_required || 30),Number(item.n || 0))}"></progress><p>${esc(estimate)} · interruptor ${item.enabled ? 'ENCENDIDO' : 'APAGADO'}</p><p>${esc(item.summary || '')}</p><ul>${(item.criteria || []).map(c => `<li>${c.ok ? '✓' : '×'} ${esc(c.text)}${c.value == null ? '' : ` · ${esc(typeof c.value === 'object' ? JSON.stringify(c.value) : c.value)}`}</li>`).join('')}</ul><a href="${destination}">Ir al interruptor</a></article>`;
      }).join('')}<h3>Grids Smart y Simple</h3><p>Recomendaciones y resultados históricos por grid. Los interruptores por grid se mantienen apagados por defecto.</p>${gridRows || '<p>Sin recomendaciones de grids registradas.</p>'}</section>`;
    }
    const forecastRows = (d.volatility_forecasts || []).map(x => [x.symbol,x.model_name,`${x.horizon_h} h`,x.count,x.first_at,x.last_at]);
    const predictionRows = (d.direction_predictions || []).map(x => [x.symbol,x.model_name,x.interval,x.count,x.first_at,x.last_at]);
    const sampleRows = (s.by_profile_source_symbol || []).map(x => [x.profile,x.source_kind,x.symbol,x.count,x.first_at,x.last_at]);
    const labelRows = (s.labels || []).map(x => [x.horizon,x.status,x.outcome,x.count]);
    const versionRows = (data.regressions?.versions || []).map(v => {
      const signals = v.summary?.signals || {};
      const validation = v.summary?.validation?.model || {}, baseline = v.summary?.baseline_validation?.baseline || {};
      const deciles = v.summary?.calibration_deciles || {};
      const decileCount = Object.values(deciles).reduce((n, group) => n + Object.keys(group || {}).length, 0);
      return [v.id,v.profile,v.source,v.status,v.created_at,Object.entries(signals).map(([name,x]) => `${signalLabel(name)}: \u03b2 ${num(x.candidate)}; EE ${num(x.standard_error_clustered)}; p ${num(x.p_value)}; Holm ${num(x.p_value_holm)}; aplicado ${num(x.weight)}; ${x.reason || ''}`).join(' · '),`entreno ${num(v.summary?.training_effective)}; validación ${num(v.summary?.validation_effective)}; corte ${v.summary?.training_cutoff || '—'}`,`log-loss ${num(validation.log_loss)}; Brier ${num(validation.brier)}`,`log-loss ${num(baseline.log_loss)}; Brier ${num(baseline.brier)}`,`${num(decileCount)} deciles guardados`];
    });
    const runRows = (data.runs || []).map(x => [x.id,x.started_at,x.status,x.finished_at,
      Object.entries(x.summary?.timings_s || {}).map(([name,seconds]) => `${name}: ${num(seconds)} s`).join(' · ') || 'Sin tiempos registrados',
      `nuevas ${num(x.summary?.replay_windows_new)}; caché ${num(x.summary?.replay_windows_cached)}; cortes ${num(x.summary?.cuts_processed)}`]);
    const inventoryRows = Object.entries(r.last_run?.candle_inventory || {}).map(([symbol,x]) => [symbol,x.count,x.first_at,x.last_at,`${num(x.interval_seconds)} s`,x.gaps]);
    const coverageRows = Object.entries(data.signals || {}).map(([name,x]) => [signalLabel(name),x.observations,x.sample_coverage,`${num(x.coverage_pct)}%`,name === 'direction' ? `${num(x.direction_active_coverage?.count)}/${num(x.direction_active_coverage?.total)} (${num(x.direction_active_coverage?.coverage_pct)}%)` : '—',num(x.mean),num(x.stddev),num(x.high_success_rate),num(x.low_success_rate)]);
    const flags = Object.entries(data.global_flags || {}).map(([name,value]) => [name,value == null ? 'No disponible' : value ? 'Encendido' : 'Apagado']);
    const directions = (hits.direction || []).map(x => [x.model,x.symbol,`${x.days} días`,x.n,pct(x.accuracy),x.wilson95?.map(pct).join('–') || '—',x.few_samples ? 'Pocas muestras' : '']);
    const vols = (hits.volatility || []).map(x => [x.model,x.symbol,`${x.days} días`,x.n,num(x.mae_log),pct(x.within_20_pct),x.few_samples ? 'Pocas muestras' : '']);
    const touches = (hits.touch_calibration || []).map(x => [x.bin,x.n,pct(x.predicted),pct(x.observed),x.few_samples ? 'Pocas muestras' : '']);
    const replayHits = (hits.replay_calibration || []).map(x => [x.horizon,x.bin,x.n,pct(x.predicted),pct(x.observed),x.few_samples ? 'Pocas muestras' : '']);
    const replayCalibrationSource = hits.replay_calibration_source || {};
    const replayCalibrationNote = replayCalibrationSource.version_id
      ? `Versi\u00f3n ${num(replayCalibrationSource.version_id)} \u00b7 ${replayCalibrationSource.profile} \u00b7 ${replayCalibrationSource.source_kind} \u00b7 ${replayCalibrationSource.created_at}`
      : (replayCalibrationSource.message || 'sin deciles guardados');
    const centeredHits = Object.entries(hits.centered_signal_comparison || {}).flatMap(([horizon, pair]) =>
      Object.entries(pair || {}).map(([kind, x]) => [horizon,kind,x.n,num(x.log_loss),num(x.brier),x.n < 30 ? 'Pocas muestras' : '']));
    const breakV2 = hits.break_v2_comparison || {n:0, message:'sin datos suficientes', actual:{}, break_v2:{}, terciles:[]};
    const breakV2Rows = [['Probabilidad actual (ruptura actual)', breakV2.actual?.n, num(breakV2.actual?.log_loss), num(breakV2.actual?.brier)],
      ['Probabilidad alternativa (ruptura v2)', breakV2.break_v2?.n, num(breakV2.break_v2?.log_loss), num(breakV2.break_v2?.brier)]];
    const breakTercileRows = (breakV2.terciles || []).map(x => [x.tercile, x.n,
      `${num(x.break_prob_min)}–${num(x.break_prob_max)}`, pct(x.range_exit_rate), x.few_samples ? 'Pocas muestras' : '']);
    const decisionsHits = Object.entries(hits.decision_rates || {}).flatMap(([kind, horizons]) => Object.entries(horizons).map(([h,x]) => [kind,h,x.n,x.successes,pct(x.rate),x.few_samples ? 'Pocas muestras' : '']));
    const replayHist = `<div class="replay-histogram">${Object.entries(r.window_histogram || {}).map(([bin,count]) => `<span>${esc(bin)} ventanas/muestra: ${num(count)}</span>`).join(' · ')}</div>`;
    const allocationRows = (data.allocation_shadow || []).map(x => {
      const d = x.details || {};
      const proposal = d.decision === 'agregar' && d.symbol
        ? `${d.symbol} ${d.piece_type || 'pieza'} · ${num(d.capital_usdt)} USDT`
        : `No agregar · ${d.reason || 'sin mejora suficiente'}`;
      return [x.at, x.meta_id, proposal,
        `${pct(d.probability_before)} → ${pct(d.probability_after)}`,
        `${num(d.delta_probability_pp)} puntos`, d.reason || ''];
    });
    const touchBots = d.touch_bot_shadow || {proposals:[],labels:{}};
    const touchBotRows = (touchBots.proposals || []).map(x => [x.at,x.meta_id,x.symbol,
      x.status === 'proposed' ? `${num(x.buy_distance_pct)}% bajo · compra ${num(x.buy_price)} · venta ${num(x.sell_price)}` : x.reason || x.status,
      `24 h ${pct(x.expected_sell_rate_24h)} · 72 h ${pct(x.expected_sell_rate_72h)}`,
      `${x.labels?.['24']?.sell_after_buy == null ? '—' : x.labels['24'].sell_after_buy ? 'Sí' : 'No'} · ${x.labels?.['72']?.sell_after_buy == null ? '—' : x.labels['72'].sell_after_buy ? 'Sí' : 'No'}`]);
    const centeredSwitches = Object.entries(data.shadow_switches || {}).map(([profile,x]) =>
      `Meta ${profile}: señales centradas ${x.use_centered_signals ? 'ENCENDIDO' : 'APAGADO'} · bots de toque ${x.use_touch_bots ? 'ENCENDIDO' : 'APAGADO'}`).join(' · ');
    const unknown = d.unknown_valuation_reviews || {last_24h: 0, last_7d: 0, reasons: []};
    const content = $('#intelligence-content');
    content.innerHTML = `
      <section class="card"><h2>B1 · Datos disponibles</h2><p class="intelligence-summary">${esc(d.summary_text || 'Todavía no hay datos suficientes para describir la actividad.')}</p><p>${esc(d.candles?.reason || 'Velas no disponibles.')}</p><p>Revisiones canceladas por valoración desconocida: ${num(unknown.last_24h)} en 24 h · ${num(unknown.last_7d)} en 7 días.</p>${detail('Motivos de valoración desconocida','Símbolo y fallo o antigüedad del libro en las revisiones recientes.',table(['Fecha','Meta','Moneda','Motivo'],(unknown.reasons||[]).map(x=>[x.at,x.meta_id,x.symbol,x.reason])))}
        ${detail('Pronósticos de volatilidad','Cantidad y fechas disponibles por moneda, modelo y horizonte.',table(['Moneda','Modelo','Horizonte','Cantidad','Primero','Último'],forecastRows))}
        ${detail('Predicciones de dirección','Predicciones guardadas por moneda, modelo e intervalo.',table(['Moneda','Modelo','Intervalo','Cantidad','Primero','Último'],predictionRows))}
        ${detail('Observaciones de señales','Observaciones de toque compra a venta guardadas por moneda.',table(['Moneda','Cantidad','Primero','Último'],(d.meta_signal_observations_by_symbol||[]).map(x=>[x.symbol,x.count,x.first_at,x.last_at])))}
        ${detail('Observaciones por señal y moneda','Conteo de filas de observación por señal, no de campos internos del JSON.',table(['Señal','Moneda','Cantidad'],(d.meta_signal_counts_by_symbol||[]).map(x=>[x.signal,x.symbol,x.count])))}
        ${detail('Bitácora por meta y evento','Eventos persistidos por meta y tipo.',table(['Meta','Evento','Cantidad'],(d.meta_ledger||[]).map(x=>[x.meta_id,x.event_type,x.count])))}</section>
      <section class="card"><h2>B2 · Muestras y etiquetas</h2><p>Las filas distinguen observaciones en vivo e histórico reconstruido; las etiquetas inmaduras no cuentan como éxito o fracaso.</p>${table(['Perfil','Origen','Moneda','Muestras','Primera','Última'],sampleRows)}${detail('Etiquetas','Resultado de replay cuando el horizonte ya maduró.',table(['Horizonte','Estado','Resultado','Cantidad'],labelRows))}<p>Recalculación pendiente: ${num(s.recalculate_pending)}. Exclusiones registradas: ${num((s.exclusions||[]).reduce((n,x)=>n+Number(x.count||0),0))}.</p></section>
      <section class="card"><h2>B3 · Probabilidad base y replay</h2><p>p_replay es la referencia histórica antes de aplicar señales. La suavizada evita valores exactos de 0 o 1.</p>${replayBars(r.chart)}<p>${esc(r.interpretation || '')}</p>${detail('Distribución de ventanas','Cantidad de muestras según ventanas independientes disponibles.',replayHist)}<p>Entradas en caché: ${num(r.cache_entries)}. Las cifras de replay se muestran sin JSON crudo.</p></section>
      <section class="card"><h2>Inventario de velas de la última corrida</h2><p>${esc(r.last_run ? (r.last_run.candle_inventory_at || 'Fecha de corrida no registrada') : 'Aún no hay corrida.')}</p>${table(['Moneda','Cantidad','Primera','Última','Intervalo (s)','Huecos'],inventoryRows)}</section>
      <section class="card"><h2>B4 · Regresión y validación</h2><p>Errores y frenos guardados por versión; abrir esta pantalla no entrena ni simula.</p>${detail('Versiones del modelo','Coeficientes, pruebas estadísticas y validaciones guardadas.',table(['Versión','Perfil','Origen','Estado','Fecha','Señales y frenos','Tamaño y corte','Validación','Solo p_replay','Calibración'],versionRows))}<p>Últimas versiones guardadas: ${num((data.regressions?.versions||[]).length)}.</p></section>
      <section class="card"><h2>B5 · Señales</h2><p>Cobertura, promedio, dispersión y tasas alta/baja se calculan con valores persistidos. Dirección activa cuenta solo P(baja) ≥ 0,60; el aprendizaje conserva como presente una predicción inferior al umbral con señal neutral 0, por lo que aún aporta a su muestra.</p>${detail('Cobertura y relación con resultados','Muestras disponibles y éxito observado por nivel de señal.',table(['Señal','Observaciones','Muestras','Cobertura','Dirección activa','Promedio','Desviación','Éxito alta','Éxito baja'],coverageRows))}</section>
      <section class="card"><details class="intelligence-detail"><summary>B6 · Decisiones</summary><p>Comparación completa (versión 2); resultados futuros se cuentan cuando sus etiquetas maduran.</p><label>Filtrar por meta <input type="number" min="1" data-decision-meta value="${esc(decisionMeta)}"></label> <button type="button" class="button secondary" data-decision-filter>Filtrar</button>${decisionTable(data.decisions?.recent || [])}<div class="decision-pages"><button type="button" data-page-prev ${decisionPage<=1?'disabled':''}>Anterior</button><span>Página ${num(data.decisions?.page||1)} · ${num(data.decisions?.total||0)} decisiones</span><button type="button" data-page-next ${(decisionPage*Number(data.decisions?.page_size||50))>=Number(data.decisions?.total||0)?'disabled':''}>Siguiente</button></div></details></section>
      <section class="card"><h2>B10 · Aciertos</h2><p>Resumen descriptivo de resultados guardados; con menos de 30 casos se marca que la evidencia es escasa.</p>${detail('Dirección','Acierto medido en outcomes y su intervalo Wilson del 95 %.',table(['Modelo','Moneda','Ventana','n','Acierto','IC 95 % Wilson','Nota'],directions))}${detail('Volatilidad','Error absoluto medio del log y proporción dentro de ±20 % del pronóstico.',table(['Modelo','Moneda','Ventana','n','MAE log','Dentro de ±20 %','Nota'],vols))}${detail('Toque compra→venta','Probabilidad predicha contrastada con la tasa real guardada en las observaciones de 40b-5.',table(['Rango predicho','n','Predicha','Observada','Nota'],touches))}${detail('p_replay',`Deciles de validaci\u00f3n para solo p_replay; una versi\u00f3n, sin mezclar or\u00edgenes. ${esc(replayCalibrationNote)}`,table(['Horizonte','Rango','n','Predicha','Éxito observado','Nota'],replayHits))}${detail('Scanner/Advisor centrados','Log-loss y Brier de probabilidad real frente a probabilidad con Scanner y Advisor centrados; solo muestras con etiqueta madura.',table(['Horizonte','Versión','n','Log-loss','Brier','Nota'],centeredHits))}${detail('Ruptura v2 · comparación a 72 h',`${breakV2.n < 30 ? 'sin datos suficientes' : 'Etiquetas maduras'} · ${num(breakV2.n)} etiquetas · intervalo ${esc(breakV2.interval?.first || '—')} a ${esc(breakV2.interval?.last || '—')}`,table(['Probabilidad','n','Log-loss','Brier'],breakV2Rows))}${detail('Ruptura v2 · salidas observadas del rango', 'Proporción de alternativas con al menos una pieza que salió del rango según máximos y mínimos de velas del replay de 72 h.',table(['Tercil de p','n','Rango p','Salió del rango','Nota'],breakTercileRows))}${detail('Decisiones en sombra','Porcentaje de alternativas elegidas que alcanzaron la meta proporcional de 72 h o la meta al plazo.',table(['Política','Horizonte','n','Éxitos','Tasa','Nota'],decisionsHits))}</section>
      <section class="card"><h2>Asignación ampliada en sombra</h2><p>Últimas diez evaluaciones del efectivo libre; no activan aperturas ampliadas si el interruptor está apagado.</p>${table(['Fecha','Meta','Propuesta','Probabilidad antes → después','Cambio','Motivo'],allocationRows)}</section>
      <section class="card"><h2>Bots de toque en sombra</h2><p>Propuestas causales de compra bajo el precio y venta con objetivo neto de 0,95 %. Nunca abren órdenes. Etiquetas reales solo al madurar 24/72 h. ${esc(centeredSwitches)}</p><p>Venta observada tras compra: 24 h n=${num(touchBots.labels?.['24']?.n)} (${pct(touchBots.labels?.['24']?.sell_after_buy_rate)}) · 72 h n=${num(touchBots.labels?.['72']?.n)} (${pct(touchBots.labels?.['72']?.sell_after_buy_rate)}).</p>${table(['Fecha','Meta','Moneda','Geometría','Tasa esperada','Venta observada 24 h · 72 h'],touchBotRows)}</section>
      <section class="card"><h2>B7 · Corridas del aprendizaje</h2><p>Los tiempos y conteos describen cada etapa registrada.</p>${detail('Corridas','Resumen de tiempos y actividad de cada proceso.',table(['ID','Inicio','Estado','Fin','Tiempos','Replays'],runRows))}</section>
      <section class="card"><h2>B8 · Interruptores</h2><p>El modo Meta y los interruptores Smart son de solo lectura aquí. Cambios de pesos: <a href="#lab">Laboratorio → Reglas globales</a>.</p>${table(['Ajuste','Estado'],flags)}${Object.entries(data.switches||{}).map(([profile,item])=>`<p>Meta ${profile}: ${item.enabled?'ENCENDIDO':'APAGADO'} · <a href="#lab">cambiar en Laboratorio</a></p>${readiness(item.readiness||{})}`).join('')}<p><a href="#models">Abrir Modelos</a> · <a href="#battle">Abrir Battle de Modelos</a></p></section>`;
    content.querySelector('[data-decision-filter]')?.addEventListener('click', () => { decisionMeta = content.querySelector('[data-decision-meta]').value; decisionPage = 1; load(); });
    content.querySelector('[data-page-prev]')?.addEventListener('click', () => { decisionPage = Math.max(1, decisionPage - 1); load(); });
    content.querySelector('[data-page-next]')?.addEventListener('click', () => { decisionPage += 1; load(); });
    updatedAt = Date.now(); if (ageTimer) clearTimeout(ageTimer); updateAge();
  }
  async function load() {
    $('#intelligence-content').innerHTML = '<p class="muted">Consultando agregados guardados…</p>';
    try { render(await api.get(`/api/intelligence?page=${decisionPage}&page_size=50${decisionMeta ? `&meta_id=${encodeURIComponent(decisionMeta)}` : ''}`)); }
    catch (error) { $('#intelligence-content').innerHTML = `<p class="cuenta-error">No se pudo consultar Inteligencia: ${esc(error.message)}</p>`; }
  }
  async function loadLearningLast() {
    const node = $('#learning-last-run'); if (!node) return;
    try {
      const data = await api.get('/api/metas/learning/last');
      const run = data.run;
      node.textContent = run ? `Última corrida: ${run.status} · origen ${run.trigger} · inicio ${run.started_at} · fin ${run.finished_at || 'en curso'} · muestras fallidas ${run.samples_failed} · geometrías inválidas ${run.labels_invalid_geometry}` : 'Aún no hay corridas de aprendizaje.';
    } catch (error) { node.textContent = `No se pudo consultar la última corrida: ${error.message}`; }
  }
  async function startLearning() {
    const button = $('#learning-run-confirm-yes'), statusNode = $('#learning-run-status');
    if (button) button.disabled = true;
    try {
      const result = await api.post('/api/metas/learning/run', {});
      statusNode.textContent = `HTTP 202 · ${result.status} · trabajo ${result.job_id} · agendado ${result.scheduled_at}`;
      $('#learning-run-confirm').hidden = true;
      await loadLearningLast();
    } catch (error) {
      statusNode.textContent = `HTTP ${error.status || 'error'} · No se pudo agendar: ${error.message}`;
      await loadLearningLast();
    }
    finally { if (button) button.disabled = false; }
  }
  window.loadIntelligencePage = load;
  document.addEventListener('DOMContentLoaded', () => {
    $('#intelligence-refresh')?.addEventListener('click', load);
    $('#learning-run')?.addEventListener('click', () => { $('#learning-run-confirm').hidden = false; });
    $('#learning-run-confirm-yes')?.addEventListener('click', startLearning);
    $('#learning-run-confirm-no')?.addEventListener('click', () => { $('#learning-run-confirm').hidden = true; });
    $('#learning-last-refresh')?.addEventListener('click', loadLearningLast);
    loadLearningLast();
  });
})();
