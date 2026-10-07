(() => {
  const state = { volRequest: 0, context: null, battle: null, coverage: null, history: [], volSymbols: [], coinReadiness: {}, activeSymbol: 'XRPUSDT', selection: null, sort: { key: 'r2_cal', direction: 'desc' }, trainingJob: null, trainingPoll: null, trainingClock: null, trainingBusy: false, trainingModel: null, volArtifacts: null };
  const $ = id => document.getElementById(id);
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
  const n = (value, digits = 3) => value == null || !Number.isFinite(Number(value)) ? '\u2014' : Number(value).toFixed(digits);
  const pct = value => value == null || !Number.isFinite(Number(value)) ? '\u2014' : `${(Number(value) * 100).toFixed(1)}%`;
  const msg = (text, cls = '') => `<p class="models-status ${cls}">${esc(text)}</p>`;
  const title = name => ({ model_a: 'XGBoost', model_b: 'GRU (PyTorch)', model_c: 'Prophet+XGBoost', model_d: 'TFT', ensemble: 'Ensamble', vol: 'Volatilidad', HAR_range: 'HAR Range', HAR_asym: 'HAR Asim\u00E9trico' }[name] || name || 'No disponible');

  const trainingElapsedSeconds = (value, now = Date.now()) => {
    const started = Date.parse(value || '');
    return Number.isFinite(started) ? Math.max(0, Math.floor((now - started) / 1000)) : 0;
  };
  const formatTrainingDate = value => {
    const parsed = new Date(value || '');
    return Number.isNaN(parsed.getTime()) ? '\u2014'
      : new Intl.DateTimeFormat('es-DO', { dateStyle: 'short', timeStyle: 'short' }).format(parsed);
  };
  const trainingTimesDiffer = (artifact, loaded) => {
    const artifactMs = Date.parse(artifact || ''), loadedMs = Date.parse(loaded || '');
    return !Number.isFinite(loadedMs) || (Number.isFinite(artifactMs) && artifactMs - loadedMs > 2000);
  };

  const sortValue = (row, key) => key === 'model_name' ? String(row.model_name || '') : Number(row[key]);
  const liveRankMap = models => new Map(
    [...(models || [])].sort((a, b) => Number(b.r2_live ?? -Infinity) - Number(a.r2_live ?? -Infinity))
      .map((row, index) => [row.model_name, index + 1]),
  );
  function renderVolBattleTable(data, mode = $('models-vol-mode')?.value || 'historical') {
    const models = Array.isArray(data?.models) ? data.models : [];
    const historical = [...models].sort((a, b) => Number(b.r2_cal ?? -Infinity) - Number(a.r2_cal ?? -Infinity));
    const histRank = new Map(historical.map((row, i) => [row.model_name, i + 1]));
    const liveRank = liveRankMap(models);
    const adaptedRank = mode === 'live' && ['r2_cal','qlike_cal','historical_rank'].includes(state.sort.key)
      || mode !== 'live' && ['r2_live','live_rank','delta'].includes(state.sort.key);
    const sortKey = mode === 'live' && ['r2_cal','qlike_cal','historical_rank'].includes(state.sort.key) ? 'live_rank'
      : mode !== 'live' && ['r2_live','live_rank','delta'].includes(state.sort.key) ? 'historical_rank' : state.sort.key;
    const direction = adaptedRank ? 1 : (state.sort.direction === 'asc' ? 1 : -1);
    const valueFor = row => sortKey === 'historical_rank' ? histRank.get(row.model_name)
      : sortKey === 'live_rank' ? liveRank.get(row.model_name)
      : sortKey === 'delta' ? (Number(row.n_verified || 0) >= 30 ? histRank.get(row.model_name) - liveRank.get(row.model_name) : null)
      : sortValue(row, sortKey);
    const ordered = [...models].sort((a, b) => {
      const av = valueFor(a), bv = valueFor(b);
      const missingA = av == null || (typeof av === 'number' && !Number.isFinite(av));
      const missingB = bv == null || (typeof bv === 'number' && !Number.isFinite(bv));
      if (missingA || missingB) return missingA === missingB ? 0 : missingA ? 1 : -1;
      return (av < bv ? -1 : av > bv ? 1 : 0) * direction;
    });
    const headers = mode === 'live'
      ? [['live_rank','Puesto'],['model_name','Modelo'],['n_verified','Verificadas en vivo'],['r2_live','R² en vivo'],['delta','Δ puesto vs histórico']]
      : [['historical_rank','Puesto'],['model_name','Modelo'],['r2_cal','R² histórico'],['qlike_cal','QLIKE histórico']];
    const rows = ordered.map(row => {
      const rank = mode === 'live' ? liveRank.get(row.model_name) : histRank.get(row.model_name);
      const delta = Number(row.n_verified || 0) >= 30 && row.r2_live != null
        ? `${histRank.get(row.model_name) - liveRank.get(row.model_name) > 0 ? '+' : ''}${histRank.get(row.model_name) - liveRank.get(row.model_name)}` : '\u2014';
      const visibleRank = Number(row.n_verified || 0) >= 30 ? rank : '\u2014';
      const cells = mode === 'live'
        ? [visibleRank, esc(row.model_name), volNumber(row.n_verified, 0), volNumber(row.r2_live, 3), delta]
        : [rank, esc(row.model_name), volNumber(row.r2_cal, 3), volNumber(row.qlike_cal, 6)];
      return `<tr>${cells.map(cell => `<td>${cell}</td>`).join('')}</tr>`;
    }).join('');
    const table = `<div class="table-card"><table class="data-table models-vol-battle-table"><thead><tr>${headers.map(([key, label]) => `<th><button type="button" class="models-sort" data-sort="${key}" aria-label="Ordenar por ${label}">${label} ${sortKey === key ? (direction === 1 ? '\u25B2' : '\u25BC') : ''}</button></th>`).join('')}</tr></thead><tbody>${rows || `<tr><td colspan="${headers.length}">Sin modelos disponibles</td></tr>`}</tbody></table></div>`;
    return table;
  }
  function renderVolCoverage(data, horizon) {
    if (Number(horizon) !== 4) return '<div class="models-presentation models-coverage"><span class="models-presentation-label">Cobertura</span><strong>Cobertura disponible solo a 4h.</strong></div>';
    const n = Number(data?.n || 0);
    return `<div class="models-presentation models-coverage"><span class="models-presentation-label">Cobertura en vivo 4h</span><strong>1σ ${pct(data?.coverage_1sigma)} · 2σ ${pct(data?.coverage_2sigma)}</strong><small>n=${n} · esperado ≈68% para 1σ y ≈95% para 2σ</small></div>`;
  }
  function sampleNotice(data, horizon, coverage, mode = $('models-vol-mode')?.value || 'historical') {
    if (mode !== 'live') return '';
    const champion = (data?.models || []).find(row => row.is_champion) || {};
    const horizonRow = (data?.models || []).find(row => Number(row.horizon_h) === Number(horizon) || Number(row.horizon_h || horizon) === Number(horizon)) || champion;
    const count = Number(horizonRow.n_verified || horizonRow.n_verificadas || 0);
    return count < 30 ? `Muestra insuficiente para resultados en vivo: ${count}/30 verificadas.` : '';
  }
  function formatVolCsv(rows) {
    const fields = ['forecast_at','model_name','pred_vol_pct','realized_vol_pct'];
    const quote = value => `"${String(value ?? '').replace(/"/g, '""')}"`;
    return [fields.join(','), ...(rows || []).map(row => fields.map(field => quote(row[field])).join(','))].join('\r\n');
  }
  function renderV4Qlike(data) {
    const rows = data?.models || [], har = rows.find(row => row.model_name === 'HAR')?.qlike_cal;
    const asym = rows.find(row => row.model_name === 'HAR_asym')?.qlike_cal;
    const fmt = value => value == null || !Number.isFinite(Number(value)) ? 'No disponible' : Number(value).toFixed(6);
    return `QLIKE de manifiesto a 4h: HAR ${fmt(har)} · HAR_asym ${fmt(asym)} · ${har == null || asym == null ? 'igualdad no verificable' : Number(har) === Number(asym) ? 'iguales' : 'distintos'}.`;
  }
  function setVolMode(mode) {
    $('models-vol-table')?.classList.remove('hidden');
    $('models-vol-live-table')?.classList.toggle('hidden', mode !== 'live');
    const testNote = $('models-test-note') || [...document.querySelectorAll('#screen-models p')].find(node => node.textContent.includes('TEST de XRP no es virgen'));
    testNote?.classList.toggle('hidden', mode !== 'historical');
  }
  function filterHistoryWindow(rows, windowValue = $('models-vol-window')?.value || '30', now = Date.now()) {
    const cutoff = windowValue === 'all' ? 0 : now - Number(windowValue) * 86400000;
    return (Array.isArray(rows) ? rows : []).filter(row => !row.forecast_at || new Date(row.forecast_at).getTime() >= cutoff);
  }
  function renderVolHistory(rows, horizon = 4, mode = $('models-vol-series')?.value || 'champion') {
    const container = $('models-vol-history'), empty = $('models-vol-history-empty');
    if (!container || !empty) return;
    if (APP.volHistoryChart) { APP.volHistoryChart.remove(); APP.volHistoryChart = null; }
    container.innerHTML = '';
    if (mode === 'consensus') { empty.textContent = 'Consenso: serie histórica no disponible.'; empty.classList.remove('hidden'); return; }
    const items = filterHistoryWindow(rows);
    if (!window.LightweightCharts || !items.length) { empty.textContent = items.length ? 'Gráfico no disponible' : 'Aún no hay resultados verificados en esta ventana.'; empty.classList.remove('hidden'); return; }
    // Reuses the Battle de Volatilidad chart implementation, now rendered in Modelos.
    APP.volHistoryChart = LightweightCharts.createChart(container, { height: 250, layout: { background: { color: 'transparent' }, textColor: '#8b949e' }, grid: { vertLines: { color: '#21262d' }, horzLines: { color: '#21262d' } }, localization: { timeFormatter: time => chartTickLabel(time, true) }, timeScale: { timeVisible: true, secondsVisible: false, rightOffset: 8, fixLeftEdge: true, tickMarkFormatter: time => chartTickLabel(time, true) }, rightPriceScale: { borderColor: '#303d43' } });
    const forecast = APP.volHistoryChart.addLineSeries({ color: '#2F81F7', lineWidth: 2, title: `Pronóstico ${horizon}h (%)`, priceFormat: { type: 'custom', formatter: value => `${value.toFixed(2)}%` } }), realized = APP.volHistoryChart.addLineSeries({ color: '#3FB950', lineWidth: 2, title: `Realizado ${horizon}h (%)`, priceFormat: { type: 'custom', formatter: value => `${value.toFixed(2)}%` } });
    const points = key => items.filter(row => row.forecast_at && row[key] != null).map(row => ({ time: Math.floor(new Date(row.forecast_at).getTime() / 1000), value: Number(row[key]) })).filter(row => Number.isFinite(row.time) && Number.isFinite(row.value)).sort((a,b) => a.time-b.time);
    forecast.setData(points('pred_vol_pct')); const actual = points('realized_vol_pct'); realized.setData(actual);
    if (!actual.length) { empty.textContent = 'Aún no hay resultados verificados'; empty.classList.remove('hidden'); } else empty.classList.add('hidden');
    APP.volHistoryChart.timeScale().fitContent();
  }

  function renderStats(payload, symbol, horizon, battle) {
    const item = (payload?.horizons || []).find(row => Number(row.horizon_h) === Number(horizon));
    if (String(payload?.symbol || '').toUpperCase() !== String(symbol).toUpperCase() || !item || Number(item.horizon_h) !== Number(horizon)) return msg('La respuesta no coincide con la moneda y el horizonte seleccionados.', 'error');
    const models = Array.isArray(item.models) ? item.models : [];
    if (!models.length) return '<div class="models-empty"><h3>Sin modelos de volatilidad para esta moneda: se usa volatilidad realizada</h3><p>Faltan los artefactos y la evaluaci\u00F3n de esta moneda. La descarga y el entrenamiento llegar\u00E1n con 20B.</p></div>';
    const champion = (battle?.models || []).find(row => row.is_champion)?.model_name || ({ 1: 'GBM', 2: 'GBM', 4: 'GBM', 24: 'NexoHAR' })[horizon];
    const rankByLive = liveRankMap(battle?.models || []);
    const min = Number(item.n_min || 30);
    const rows = models.map(model => {
      const m = model.all || {}, verified = Number(model.n_verificadas || 0);
      const active = model.estado === 'activo' && verified >= min;
      const championRow = model.model_name === champion;
      const consensus = (item.adaptive?.eligible || []).includes(model.model_name);
      const training = model.trained_at || model.training_date || model.ultima_actualizacion || 'No disponible';
      const span = model.training_span || model.data_span || 'No disponible';
      const rank = verified >= min ? rankByLive.get(model.model_name) : null;
      const weightSource = model.fuente_pesos || item.adaptive?.source;
      const weightValue = weightSource === 'val' ? model.peso_respaldo_val
        : weightSource === 'vivo' ? model.peso_actual : null;
      const weightText = weightValue == null || !Number.isFinite(Number(weightValue)) ? '\u2014'
        : `${(Number(weightValue) * 100).toFixed(1)}%${weightSource === 'val' ? ' (val)' : ''}`;
      return `<tr class="${championRow ? 'models-champion' : ''} ${consensus ? 'models-consensus' : ''}"><td>${rank == null ? '\u2014' : rank}${verified < min ? '<small class="models-few-data">Pocos datos</small>' : ''}</td><td><strong>${esc(title(model.model_name))}</strong>${championRow ? '<span class="models-tag">Campe\u00F3n</span>' : ''}${consensus ? '<span class="models-tag consensus">Consenso</span>' : ''}<small>Volatilidad \u00B7 ${esc(symbol)} \u00B7 ${Number(horizon)} h</small></td><td>${Number(model.n_predicciones || 0)} / ${verified} / ${Math.max(0, Number(model.n_predicciones || 0) - verified)}</td><td>${n(m.mse, 6)}</td><td>${pct(m.coverage_1sigma)}</td><td>${pct(m.coverage_2sigma)}</td><td>${n(m.over_pct, 1)}% / ${n(m.under_pct, 1)}%</td><td>bias_log ${m.bias_mean==null?'\u2014':n(m.bias_mean, 5)} \u00B7 var_ratio ${m.var_ratio==null?'\u2014':n(m.var_ratio, 3)}${model.bias_alert ? '<small class="models-alert">Sesgo sostenido</small>' : ''}</td><td>${weightText}</td><td>${active ? 'Activo' : `Acumulando datos (${verified}/${min})`}</td><td>${model.bias_alert ? 'Alerta' : 'Sin alerta'}</td></tr>`;
    }).join('');
    return `<div class="table-card models-table-wrap"><table class="data-table models-table"><thead><tr><th>Puesto</th><th>Modelo e identidad</th><th>Pred. / verificadas / pendientes</th><th>MSE vivo</th><th>Dentro de \u00B1\u03C3_ref (error log)</th><th>Dentro de \u00B12\u03C3_ref (error log)</th><th>Sobre/sub %</th><th>Sesgo</th><th>Peso</th><th>Estado</th><th>bias_alert</th></tr></thead><tbody>${rows}</tbody></table></div><p class="muted models-note">Dentro de \u00B1\u03C3_ref y \u00B12\u03C3_ref mide error en log, no cobertura del precio. El sesgo en log tiene un componente estructural por calibrar en varianza. TEST visto durante la selecci\u00F3n. Las fechas y tramos se muestran si el endpoint los entrega.</p>`;
  }

  function renderForecast(stats, forecast, battle, symbol, horizon) {
    if (String(forecast?.symbol || '').toUpperCase() !== String(symbol).toUpperCase()) return msg('Pron\u00F3stico de otra moneda; no se mezcl\u00F3 con la selecci\u00F3n.', 'error');
    const f = (forecast.forecasts || []).find(row => Number(row.horizon_h) === Number(horizon));
    if (!f) return msg('Pron\u00F3stico no disponible para este horizonte.');
    const champ = (battle?.models || []).find(row => row.is_champion)?.model_name || f.champion || 'No disponible';
    const consensus = f.consensus || {};
    const item = (stats?.horizons || []).find(row => Number(row.horizon_h) === Number(horizon));
    const adaptive = item?.adaptive || {};
    const rawValidation = consensus.validation_status_live || item?.validation_status_live || 'en_evaluacion';
    const validation = rawValidation === 'en_evaluacion' ? 'En evaluaci\u00F3n' : rawValidation;
    return `<div class="models-summary-grid"><article class="models-presentation"><span class="models-presentation-label">Campe\u00F3n</span><strong>${esc(title(champ))}</strong><small>\u03C3 ${n(f.move_1sigma_pct, 2)}%</small></article><article class="models-presentation"><span class="models-presentation-label">Consenso</span><strong>${consensus.sigma_pct == null ? 'No disponible' : `\u03C3 ${n(consensus.sigma_pct, 2)}%`}</strong><small>Dispersi\u00F3n ${n(consensus.dispersion_iqr, 4)} \u00B7 confianza ${esc(consensus.confidence || 'no disponible')}</small></article><article class="models-presentation"><span class="models-presentation-label">Validaci\u00F3n en vivo</span><strong>${esc(validation)}</strong><small>VOL_SOURCE: ${esc(f.vol_source || forecast.vol_source || 'no expuesto por el endpoint')}</small></article><article class="models-presentation"><span class="models-presentation-label">Fuente efectiva de pesos</span><strong>${esc(adaptive.source || 'no disponible')}</strong><small>Confianza de pesos: ${esc(adaptive.confidence || 'no disponible')}</small></article></div>`;
  }

  function renderWiden(factor, horizon) {
    if (!factor) return msg('Factor de ampliaci\u00F3n no disponible.');
    const effective = Number(factor.n_effective ?? factor.n_efectivas ?? 0);
    const threshold = factor.n_effective_min ?? factor.n_efectivas_min;
    const few = factor.status === 'acumulando' || (threshold != null && effective < Number(threshold));
    const interpretation = few ? `Pocos datos (n efectiva ${n(effective, 2)}). No se concluye sobreestimaci\u00F3n ni que el factor no aplique.` : (factor.overestimate_message || '');
    const apply = factor.status === 'disponible' && !few && Number(factor.k_raw ?? 1) >= 1
      ? ` <button type="button" class="button secondary apply-widen-suggestion" data-horizon="${Number(horizon)}" data-k="${Number(factor.k_stress_smoothed)}">Aplicar sugerido</button>` : '';
    return `<div class="models-factor"><div class="models-presentation"><span class="models-presentation-label">Factor de ampliaci\u00F3n</span><strong>activo ${n(factor.k_active ?? 1.25, 2)} \u00B7 sugerido ${n(factor.k_stress_smoothed, 2)}</strong><small>k_raw ${n(factor.k_raw, 2)} \u00B7 IC ${n(factor.ci_low, 2)}\u2013${n(factor.ci_high, 2)}</small></div><div class="models-presentation"><span class="models-presentation-label">Precisi\u00F3n y plazo</span><strong>${n(factor.progress_pct, 0)}%</strong><small>${factor.days_estimated == null ? 'd\u00EDas estimados no disponibles' : `${Math.ceil(Number(factor.days_estimated))} d\u00EDas estimados`}</small></div><div class="models-presentation"><span class="models-presentation-label">Sesgo y muestra</span><strong>sesgo log ${n(factor.bias_log, 4)} \u00B7 vol_scale_suggested ${n(factor.vol_scale_suggested, 3)}</strong><small>n=${Number(factor.n || 0)}, n efectiva ${n(effective, 2)}</small>${interpretation ? `<strong class="models-few-data">${esc(interpretation)}</strong>` : ''}</div>${apply}</div>`;
  }

  function renderHistory(rows, selectedModel) {
    if (!Array.isArray(rows) || !rows.length) return '<p class="muted">Sin historial reciente de pron\u00F3sticos verificados.</p>';
    const date = value => {
      if (!value) return '\u2014';
      const parsed = new Date(value);
      return Number.isNaN(parsed.getTime()) ? '\u2014' : new Intl.DateTimeFormat('es-DO', { day: '2-digit', month: '2-digit', year: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false }).format(parsed);
    };
    return `<h4>Historial reciente · ${esc(title(selectedModel))}</h4><div class="table-card"><table class="data-table"><thead><tr><th>Fecha</th><th>Pron\u00F3stico (%)</th><th>Realizado (%)</th><th>Estado</th></tr></thead><tbody>${rows.slice(0, 12).map(row => `<tr><td>${esc(date(row.forecast_at))}</td><td>${n(row.pred_vol_pct, 2)}%</td><td>${row.realized_vol_pct == null ? '\u2014' : `${n(row.realized_vol_pct, 2)}%`}</td><td>${row.realized_vol_pct != null ? 'Verificado' : 'Pendiente'}</td></tr>`).join('')}</tbody></table></div>`;
  }

  function renderDirectionRow(name, model, counts, shadow, winner, scope = {}) {
    const available = !!model && model.available !== false;
    const status = !model && ['model_d', 'ensemble'].includes(name) ? 'No habilitado (sin configuraci\u00F3n)'
      : !model ? 'No disponible: no aparece en /api/models/status'
      : !available ? `No disponible: ${model?.unavailable_reason || 'motivo no informado'}`
      : shadow?.model_name === name ? `Sombra \u00B7 ${name === 'model_a' ? 'XRPUSDT 1h' : `${shadow.symbol || 'XRPUSDT'} ${shadow.interval || '1h'}`}`
      : model?.validation_status === 'champion' ? 'Campe\u00F3n'
      : model?.validation_status === 'not_validated' || !model?.validation_status ? 'No validado'
      : winner === name ? 'L\u00EDder por precisi\u00F3n registrada' : 'Activo';
    const evaluated = Number(counts?.bullish_correct || 0) + Number(counts?.bullish_failed || 0);
    const accuracy = evaluated ? `${(Number(counts.bullish_correct || 0) * 100 / evaluated).toFixed(1)}%` : 'Sin evidencia';
    const priorCount = Number(counts?.predictions_before_last_training || 0);
    const priorNote = priorCount > 0 ? `<small class="models-status-subtext">${priorCount} predicciones anteriores al \u00FAltimo entrenamiento (otra versi\u00F3n del modelo)</small>` : '';
    const artifactSymbol = model?.symbol || model?.artifact_symbol || model?.trained_symbol || model?.config?.symbol || 'XRPUSDT';
    const artifactInterval = model?.interval || model?.artifact_interval || model?.trained_interval || model?.config?.interval || '1h';
    const directionIdentity = shadow?.model_name === name ? `${shadow.symbol || artifactSymbol} ${shadow.interval || artifactInterval}` : `${artifactSymbol} ${artifactInterval}`;
    const artifactTime = model?.artifact_trained_at;
    const loadedTime = model?.last_trained;
    const staleNote = artifactTime && (!model?.available || trainingTimesDiffer(artifactTime, loadedTime))
      ? `<small class="models-status-subtext">Entrenado en disco el ${esc(artifactTime)}. ${model?.available && loadedTime ? `El servidor sigue usando la versi\u00F3n del ${esc(loadedTime)}` : 'El servidor no tiene una versi\u00F3n cargada'}: rein\u00EDcialo para cargarlo.</small>` : '';
    return `<tr><td><strong>${esc(model?.display_name || model?.nombre || title(name))}</strong><small class="models-status-subtext">${esc(directionIdentity)}</small>${priorNote}</td><td>${esc(status)}${model?.last_trained ? `<small class="models-status-subtext">\u00DAltimo entrenamiento: ${esc(model.last_trained)}</small>` : ''}${staleNote}</td><td>${Number(counts?.total_predictions || 0)} / ${Number(counts?.verified_count || 0)} / ${Number(counts?.pending_count || 0)}</td><td>${Number(counts?.bullish_correct || 0)} / ${Number(counts?.bullish_failed || 0)} / ${Number(counts?.bullish_pending || 0)}</td><td>${Number(counts?.neutral_count || 0)} sin se\u00F1al</td><td>${evaluated ? accuracy : 'Sin evidencia'}</td><td>${pct(counts?.base_rate)}</td></tr>`;
  }

  const activeTraining = job => !!job && ['pendiente', 'descargando', 'entrenando', 'running'].includes(job.status);
  function renderTrainingModels(status) {
    const models = Object.fromEntries((status?.models || []).map(item => [item.model_name, item]));
    const otherSymbol = state.activeSymbol !== 'XRPUSDT';
    const disabled = activeTraining(state.trainingJob) || state.trainingBusy || otherSymbol ? 'disabled' : '';
    const directionRows = ['model_b', 'model_c', 'model_a'].map(name => {
      const model = models[name] || {}, key = name.slice(-1), warn = key === 'a' ? '<small class="models-training-note">Reinicia su evaluaci\u00F3n en vivo.</small>' : '';
      return `<tr><td><strong class="models-training-name">${esc(model.display_name || title(name))}</strong>${warn}</td><td>${esc(model.artifact_trained_at || '\u2014')}</td><td>${esc(model.last_trained || (model.available === false ? 'No cargado' : '\u2014'))}</td><td><button type="button" class="button secondary models-train-button" data-model="${esc(key)}" ${disabled}>Entrenar</button></td></tr>`;
    }).join('');
    const vol = state.volArtifacts || {}, disk = vol.artifact_trained_at, loaded = vol.loaded_trained_at;
    const diskMs = Date.parse(disk || ''), loadedMs = Date.parse(loaded || '');
    const stale = disk && (!Number.isFinite(loadedMs) || (Number.isFinite(diskMs) && Math.abs(diskMs - loadedMs) > 2000));
    const restart = stale ? '<small class="models-training-note">El artefacto en disco difiere de la versi\u00F3n cargada; reinicia el servidor para cargarlo.</small>' : '';
    const volDisabled = otherSymbol ? 'disabled title="El entrenamiento de esta moneda se gestiona desde Monedas."' : disabled;
    const volRow = `<tr><td><strong class="models-training-name">Volatilidad \u00B7 8 modelos \u00D7 4 horizontes \u00B7 XRPUSDT</strong></td><td>${esc(disk || '\u2014')}</td><td>${esc(loaded || (disk ? 'No cargado' : '\u2014'))}${restart}</td><td><button type="button" class="button secondary models-train-button" data-model="vol" ${volDisabled}>Entrenar</button></td></tr>`;
    const otherSymbolNote = otherSymbol ? '<p class="models-training-note">El entrenamiento de esta moneda se gestiona desde Monedas.</p>' : '';
    return `${otherSymbolNote}<div class="table-card"><table class="data-table models-training-table"><thead><tr><th>Modelo</th><th>Artefacto en disco</th><th>Versi\u00F3n cargada</th><th></th></tr></thead><tbody>${directionRows}${volRow}</tbody></table></div>`;
  }

  function renderTrainingJobs(jobs) {
    if (!Array.isArray(jobs) || !jobs.length) return '<p class="muted">Todav\u00EDa no hay trabajos de entrenamiento.</p>';
    return `<div class="table-card"><table class="data-table"><thead><tr><th>Creado</th><th>Modelos</th><th>Estado</th><th>Fase</th><th>Error</th></tr></thead><tbody>${jobs.slice(0, 5).map(job => `<tr><td>${esc(formatTrainingDate(job.created_at))}</td><td>${esc((job.models || []).map(name => name === 'vol' ? 'Volatilidad' : name).join(', '))}</td><td>${esc(job.status || '\u2014')}</td><td>${esc(job.phase || '\u2014')}</td><td class="models-job-error">${esc(job.error || '')}</td></tr>`).join('')}</tbody></table></div>`;
  }

  function renderTrainingCurrent(job) {
    if (!job) return '<p class="muted">No hay trabajos de entrenamiento.</p>';
    if (!activeTraining(job)) return `<p class="models-status">\u00DAltimo trabajo: ${esc(job.status)}${job.error ? ` · ${esc(job.error)}` : ''}</p>`;
    const seconds = trainingElapsedSeconds(job.started_at || job.created_at || '');
    return `<div class="models-training-current"><p><strong>Estado:</strong> ${esc(job.status)}</p><p><strong>Fase:</strong> ${esc(job.phase || job.status)}</p><p>Tiempo: <span id="models-training-elapsed">${seconds} s</span></p><button type="button" class="button secondary" id="models-training-cancel" data-job-id="${Number(job.id)}">Cancelar</button></div>`;
  }

  function updateTrainingElapsed() {
    const node = $('models-training-elapsed'), job = state.trainingJob;
    if (!node || !job || !activeTraining(job)) return;
    node.textContent = `${trainingElapsedSeconds(job.started_at || job.created_at || '')} s`;
  }

  function configureTrainingPolling(active) {
    if (active) {
      if (!state.trainingPoll) state.trainingPoll = setInterval(refreshTraining, 5000);
      if (!state.trainingClock) state.trainingClock = setInterval(updateTrainingElapsed, 1000);
    } else {
      if (state.trainingPoll) clearInterval(state.trainingPoll);
      if (state.trainingClock) clearInterval(state.trainingClock);
      state.trainingPoll = null;
      state.trainingClock = null;
    }
  }

  async function refreshTraining() {
    const [statusResult, jobsResult, volResult] = await Promise.allSettled([
      api.get('/api/models/train/status'), api.get('/api/models/train/jobs', { limit: 5 }), api.get('/api/models/vol/artifacts'),
    ]);
    if (statusResult.status === 'fulfilled') state.trainingJob = statusResult.value?.job || null;
    if (volResult.status === 'fulfilled') state.volArtifacts = volResult.value || null;
    $('models-training-current').innerHTML = statusResult.status === 'fulfilled'
      ? renderTrainingCurrent(state.trainingJob) : msg(`Estado de entrenamiento: ${statusResult.reason?.message || 'error de red'}`, 'error');
    $('models-training-jobs').innerHTML = jobsResult.status === 'fulfilled'
      ? renderTrainingJobs(jobsResult.value?.jobs) : msg(`Historial de trabajos: ${jobsResult.reason?.message || 'error de red'}`, 'error');
    $('models-training-models').innerHTML = renderTrainingModels(state.trainingModels || {});
    configureTrainingPolling(statusResult.status === 'fulfilled' && activeTraining(state.trainingJob));
  }

  function openTrainingDialog(modelName) {
    state.trainingModel = modelName;
    const isVol = modelName === 'vol';
    $('models-train-target').textContent = isVol ? 'Volatilidad · XRPUSDT · 1h, 2h, 4h y 24h' : `${title(`model_${modelName}`)} · XRPUSDT · 1h · ${$('models-train-days').value} días`;
    $('models-train-days-control').hidden = isVol;
    $('models-train-gru-duration').hidden = isVol;
    $('models-train-vol-note').hidden = !isVol;
    $('models-train-direction-note').hidden = isVol;
    $('models-train-reset').hidden = modelName !== 'a';
    $('models-train-reset-check').checked = false;
    $('models-train-reset-text').value = '';
    $('models-train-confirm').disabled = modelName === 'a';
    $('models-train-dialog').showModal();
  }

  function updateTrainingConfirmation() {
    $('models-train-confirm').disabled = state.trainingModel === 'a'
      && (!$('models-train-reset-check').checked || $('models-train-reset-text').value.trim() !== 'A');
  }

  async function submitTraining(event) {
    event.preventDefault();
    const modelName = state.trainingModel;
    const isVol = modelName === 'vol';
    const days = Number($('models-train-days').value);
    if (!modelName || (!isVol && (!Number.isInteger(days) || days < 365 || days > 730))) return;
    if (modelName === 'a' && $('models-train-confirm').disabled) return;
    state.trainingBusy = true;
    $('models-training-models').innerHTML = renderTrainingModels(state.trainingModels || {});
    try {
      await api.post('/api/models/train', {
        symbol: 'XRPUSDT', interval: '1h', models: [modelName], days: isVol ? 730 : days, confirm: true,
        ...(modelName === 'a' ? { confirm_reset_evaluation: true } : {}),
      });
      $('models-train-dialog').close();
      await refreshTraining();
    } catch (error) {
      $('models-training-current').innerHTML = msg(error?.message || 'No se pudo iniciar el entrenamiento.', 'error');
    } finally {
      state.trainingBusy = false;
      $('models-training-models').innerHTML = renderTrainingModels(state.trainingModels || {});
    }
  }

  async function cancelTraining(jobId) {
    const button = $('models-training-cancel');
    if (button) button.disabled = true;
    try {
      await api.post(`/api/models/train/${Number(jobId)}/cancel`, {});
      await refreshTraining();
    } catch (error) {
      $('models-training-current').innerHTML = `${renderTrainingCurrent(state.trainingJob)}${msg(error?.message || 'No se pudo cancelar el trabajo.', 'error')}`;
    }
  }

  function renderDirection(status, context, shadow) {
    if ((context?.symbol || 'XRPUSDT') !== 'XRPUSDT') return '<p class="models-direction-only-xrp">Los modelos de direcci\u00F3n solo existen para XRPUSDT.</p>';
    const names = ['model_a', 'model_b', 'model_c', 'model_d', 'ensemble'];
    const models = Object.fromEntries((status?.models || []).map(model => [model.model_name, model]));
    const rows = names.map(name => renderDirectionRow(name, models[name], context?.models?.[name], shadow, status?.winner_by_accuracy, context)).join('');
    return `<p class="muted models-direction-metric-note">BAJISTA acierta si el precio no sube m\u00E1s de 0,5 %.</p><p class="muted">Temporalidad activa: ${esc(context?.symbol || 'no disponible')} ${esc(context?.interval || 'no disponible')}. La selecci\u00F3n de moneda/horizonte de volatilidad no modifica estos datos.</p><div class="table-card"><table class="data-table models-direction-table"><thead><tr><th>Modelo</th><th>Estado</th><th>Predicciones / verificadas / pendientes</th><th>ALCISTA: aciertos / fallos / pendientes</th><th>NEUTRAL</th><th>Precisi\u00F3n ALCISTA</th><th>Tasa base UP</th></tr></thead><tbody>${rows}</tbody></table></div>`;
  }

  async function loadContext(requestedSymbol) {
    const requested = requestedSymbol || APP.currentSymbol || 'XRPUSDT';
    const context = await api.get('/api/models/page-context', { symbol: requested, interval: APP.currentInterval || '1h' });
    let coinRows = [];
    try { coinRows = await api.get('/api/coins'); } catch (_) {}
    state.coinReadiness = Object.fromEntries((Array.isArray(coinRows) ? coinRows : []).map(row => [row.symbol, row]));
    const select = $('models-symbol'), old = requestedSymbol || select.value || APP.currentSymbol || context.symbol;
    select.innerHTML = (context.coins || []).map(symbol => {
      const row = state.coinReadiness[symbol], ready = symbol === 'XRPUSDT' || row?.ready === true || row?.readiness?.state === 'lista';
      const status = row?.readiness?.state || (ready ? 'lista' : 'pendiente');
      const suffix = ready ? '' : (['descargando','entrenando','consensuando'].includes(status) ? ' (preparando)' : ' (no lista)');
      return `<option value="${esc(symbol)}">${esc(symbol)}${suffix}</option>`;
    }).join('');
    select.value = context.coins?.includes(old) ? old : (context.coins?.includes(context.symbol) ? context.symbol : context.coins?.[0] || 'XRPUSDT');
    return context;
  }

  function clearVolatilityPanels() {
    for (const id of ['models-vol-live-table','models-vol-factor','models-vol-table','models-vol-history','models-vol-short-history']) $(id).innerHTML = '';
    $('models-vol-coverage').textContent = '';
    $('models-vol-sample-notice').textContent = '';
    $('models-v4-qlike').textContent = '';
    $('models-vol-history-empty').textContent = '';
    $('models-vol-history-empty').classList.add('hidden');
    state.battle = null; state.coverage = null; state.history = [];
  }
  function unavailableVolatilityMessage(symbol) {
    const readiness = state.coinReadiness[symbol]?.readiness || {};
    const status = readiness.state || 'pendiente';
    return `<p>${esc(symbol)} a\u00FAn no tiene modelos de volatilidad: estado ${esc(status)}. Se prepara desde la pantalla <a href="#coins">Monedas</a>.</p>`;
  }
  function selectionNotice(selection, battle) {
    let note = selection === 'provisional'
      ? 'Selecci\u00F3n provisional: el consenso estad\u00EDstico de esta moneda a\u00FAn no se ha calculado. Los campeones se eligen por menor error en validaci\u00F3n.'
      : selection === 'consensus'
        ? 'Selecci\u00F3n por consenso estad\u00EDstico (estudio hist\u00F3rico). La validaci\u00F3n en vivo se acumula con el tiempo.' : '';
    const champion = (battle?.models || []).find(row => row.is_champion)?.model_name;
    if (selection === 'consensus' && champion === 'Persistence') note += `${note ? ' ' : ''}Ning\u00FAn modelo super\u00F3 a Persistence en este horizonte; se usa el modelo trivial.`;
    return note ? `<p class="models-selection-note">${esc(note)}</p>` : '';
  }
  async function loadVolatility() {
    const request = ++state.volRequest, symbol = $('models-symbol').value || 'XRPUSDT', horizon = Number($('models-horizon').value || 4);
    let symbolPayload = null;
    try { symbolPayload = await api.get('/api/volatility/symbols'); } catch (_) {}
    if (request !== state.volRequest || symbol !== $('models-symbol').value) return;
    state.volSymbols = symbolPayload?.symbols || [];
    const symbolInfo = state.volSymbols.find(row => String(row.symbol).toUpperCase() === symbol);
    if (symbol !== 'XRPUSDT' && !symbolInfo) {
      clearVolatilityPanels();
      $('models-vol-summary').innerHTML = unavailableVolatilityMessage(symbol);
      return;
    }
    for (const id of ['models-vol-live-table', 'models-vol-summary', 'models-vol-factor', 'models-vol-history', 'models-vol-short-history']) $(id).innerHTML = msg('Cargando datos\u2026');
    const results = await Promise.allSettled([
      api.get('/api/volatility/model-stats', { symbol, horizon }), api.get('/api/volatility/forecast', { symbol }),
      api.get('/api/volatility/widen-factor', { symbol }), api.get('/api/volatility/battle', { symbol, horizon }),
      horizon === 4 ? api.get('/api/predictions/volatility-coverage', { symbol, interval: '1h' }) : Promise.resolve(null),
    ]);
    if (request !== state.volRequest || symbol !== $('models-symbol').value || horizon !== Number($('models-horizon').value)) return;
    const [sr, fr, wr, br, cr] = results;
    const battleCandidate = br.status === 'fulfilled' ? br.value : null;
    const battlePayload = battleCandidate && String(battleCandidate.symbol || '').toUpperCase() === symbol
      && Number(battleCandidate.horizon_h) === horizon ? battleCandidate : null;
    state.battle = battlePayload; state.coverage = cr.status === 'fulfilled' ? cr.value : null;
    $('models-vol-table').innerHTML = renderVolBattleTable(battlePayload, $('models-vol-mode')?.value || 'historical');
    setVolMode($('models-vol-mode')?.value || 'historical');
    $('models-vol-sample-notice').textContent = sampleNotice(battlePayload, horizon, cr.status === 'fulfilled' ? cr.value : null);
    $('models-vol-coverage').innerHTML = cr.status === 'fulfilled' && cr.value ? renderVolCoverage(cr.value, horizon) : renderVolCoverage(null, horizon);
    $('models-v4-qlike').textContent = horizon === 4 ? renderV4Qlike(battlePayload) : '';
    const modelSelect = $('models-vol-model');
    if (modelSelect && battlePayload?.models) {
      const selected = modelSelect.value;
      modelSelect.innerHTML = battlePayload.models.map(row => `<option value="${esc(row.model_name)}">${esc(row.model_name)}</option>`).join('');
      modelSelect.value = battlePayload.models.some(row => row.model_name === selected) ? selected : (battlePayload.models.find(row => row.is_champion)?.model_name || battlePayload.models[0]?.model_name || '');
    }
    $('models-vol-table').innerHTML = selectionNotice(symbolInfo?.selection, battlePayload) + renderVolBattleTable(battlePayload, $('models-vol-mode')?.value || 'historical');
    const payload = sr.status === 'fulfilled' ? sr.value : null;
    const item = (payload?.horizons || []).find(row => Number(row.horizon_h) === horizon);
    if (sr.status !== 'fulfilled') {
      const isNoModel = Number(sr.reason?.status || String(sr.reason || '').match(/HTTP (\d+)/)?.[1] || 0) === 404;
      $('models-vol-live-table').innerHTML = isNoModel ? renderStats({ symbol, horizons: [{ horizon_h: horizon, models: [] }] }, symbol, horizon) : msg('Estad\u00EDsticas de volatilidad: error de red.', 'error');
    } else {
      $('models-vol-live-table').innerHTML = renderStats(payload, symbol, horizon, battlePayload);
    }
    const statsMatches = payload && String(payload.symbol || '').toUpperCase() === symbol && item && Number(item.horizon_h) === horizon;
    $('models-vol-summary').innerHTML = selectionNotice(symbolInfo?.selection, battlePayload) + (statsMatches && fr.status === 'fulfilled'
      ? renderForecast(payload, fr.value, battlePayload, symbol, horizon)
      : fr.status === 'fulfilled' ? renderForecast(null, fr.value, battlePayload, symbol, horizon)
      : msg('Pron\u00F3stico: error de red.', 'error'));
    if (wr.status === 'fulfilled') {
      const widenPayload = wr.value, factor = String(widenPayload?.symbol || '').toUpperCase() === symbol
        ? (widenPayload.horizons || []).find(row => Number(row.horizon_h) === horizon) : null;
      $('models-vol-factor').innerHTML = factor ? renderWiden(factor, horizon) : msg('Factor descartado: moneda u horizonte no coinciden.', 'error');
    } else $('models-vol-factor').innerHTML = msg('Factor de ampliaci\u00F3n: error de red.', 'error');
    if (battlePayload) {
      const series = $('models-vol-series')?.value || 'champion';
      const champion = battlePayload.models?.find(row => row.is_champion)?.model_name || ({ 1: 'GBM', 2: 'GBM', 4: 'GBM', 24: 'NexoHAR' })[horizon];
      const chartModel = series === 'model' ? $('models-vol-model')?.value : champion;
      if (series === 'consensus') { state.history = []; renderVolHistory([], horizon, 'consensus'); return; }
      try {
        const response = await api.getWithHeaders('/api/volatility/history', { symbol, horizon, model: chartModel || champion, limit: $('models-vol-window')?.value === 'all' ? 1000 : 200 });
        const history = response.data || [];
        const scopedHistory = history.filter(row => (!row.symbol || String(row.symbol).toUpperCase() === symbol) && (row.horizon_h == null || Number(row.horizon_h) === horizon));
        if (request === state.volRequest && symbol === $('models-symbol').value && horizon === Number($('models-horizon').value)) {
          state.selection = response.headers?.get('X-Vol-Selection') || symbolInfo?.selection || null;
          state.history = scopedHistory;
          $('models-vol-short-history').innerHTML = renderHistory(scopedHistory, chartModel || champion);
          $('models-vol-table').innerHTML = selectionNotice(state.selection, battlePayload) + renderVolBattleTable(battlePayload, $('models-vol-mode')?.value || 'historical');
          $('models-vol-summary').innerHTML = selectionNotice(state.selection, battlePayload) + (sr.status === 'fulfilled' && fr.status === 'fulfilled' ? renderForecast(sr.value, fr.value, battlePayload, symbol, horizon) : $('models-vol-summary').innerHTML);
          renderVolHistory(scopedHistory, horizon);
        }
      } catch (_) { if (request === state.volRequest) {
        const error = msg('Historial: error de red.', 'error');
        $('models-vol-history').innerHTML = error;
        $('models-vol-short-history').innerHTML = error;
        $('models-vol-history-empty').textContent = 'Historial: error de red.';
        $('models-vol-history-empty').classList.remove('hidden');
      } }
    } else { $('models-vol-history-empty').textContent = 'Historial: no disponible.'; $('models-vol-history-empty').classList.remove('hidden'); }
  }

  async function loadModelsPage() {
    $('models-direction-content').innerHTML = msg('Cargando modelos de direcci\u00F3n\u2026');
    let context;
    try {
      context = await loadContext($('models-symbol').value || undefined);
      $('models-active-context').textContent = `${context.symbol} ${context.interval}`; state.activeSymbol = context.symbol || 'XRPUSDT';
    } catch (_) {
      $('models-symbol').innerHTML = '<option value="XRPUSDT">XRPUSDT</option>';
      context = { symbol: APP.currentSymbol || 'XRPUSDT', interval: APP.currentInterval || '1h', coins: [], registryError: true }; state.activeSymbol = context.symbol;
      $('models-direction-content').innerHTML = msg('Registro de monedas: error de red. Los otros bloques siguen disponibles.', 'error');
    }
    const request = Date.now(); state.context = context;
    const direction = Promise.allSettled([api.modelsStatus(), api.shadowStatus()]).then(([sr, sh]) => {
      const errors = [context.registryError ? 'Registro de monedas: error de red.' : '', sr.status === 'rejected' ? 'Estado de modelos: error de red.' : '', sh.status === 'rejected' ? 'Estado sombra: error de red.' : ''].filter(Boolean);
      state.trainingModels = sr.status === 'fulfilled' ? sr.value : {};
      $('models-training-models').innerHTML = renderTrainingModels(state.trainingModels);
      $('models-direction-content').innerHTML = `${errors.map(text => msg(text, 'error')).join('')}${renderDirection(sr.status === 'fulfilled' ? sr.value : {}, context, sh.status === 'fulfilled' ? sh.value : {})}`;
      refreshTraining();
    });
    await Promise.all([loadVolatility(), direction]);
  }

  document.addEventListener('DOMContentLoaded', () => {
    $('models-symbol')?.addEventListener('change', loadModelsPage);
    $('models-horizon')?.addEventListener('change', loadVolatility);
    $('models-vol-mode')?.addEventListener('change', () => { const mode = $('models-vol-mode').value; state.sort = { key: mode === 'live' ? 'live_rank' : 'historical_rank', direction: 'asc' }; setVolMode(mode); $('models-vol-table').innerHTML = selectionNotice(state.selection, state.battle) + renderVolBattleTable(state.battle, mode); $('models-vol-sample-notice').textContent = sampleNotice(state.battle, Number($('models-horizon').value), state.coverage, mode); });
    $('models-vol-series')?.addEventListener('change', () => { if ($('models-vol-series').value === 'consensus') { renderVolHistory(state.history, Number($('models-horizon').value), 'consensus'); return; } loadVolatility(); });
    $('models-vol-model')?.addEventListener('change', () => loadVolatility());
    $('models-vol-window')?.addEventListener('change', () => $('models-vol-window').value === 'all' ? loadVolatility() : renderVolHistory(state.history, Number($('models-horizon').value)));
    $('models-vol-csv')?.addEventListener('click', () => {
      const rows = filterHistoryWindow(state.history);
      const blob = new Blob([formatVolCsv(rows)], { type: 'text/csv;charset=utf-8' }), url = URL.createObjectURL(blob), link = document.createElement('a');
      link.href = url; link.download = 'volatilidad_historial.csv'; link.click(); URL.revokeObjectURL(url);
    });
    $('models-vol-table')?.addEventListener('click', event => {
      const button = event.target.closest('.models-sort'); if (!button) return;
      state.sort = { key: button.dataset.sort, direction: state.sort.key === button.dataset.sort && state.sort.direction === 'desc' ? 'asc' : 'desc' };
      $('models-vol-table').innerHTML = renderVolBattleTable(state.battle, $('models-vol-mode').value);
    });
    $('models-training-models')?.addEventListener('click', event => {
      const button = event.target.closest('.models-train-button');
      if (button && !button.disabled) openTrainingDialog(button.dataset.model);
    });
    $('models-training-current')?.addEventListener('click', event => {
      const button = event.target.closest('#models-training-cancel');
      if (button) cancelTraining(button.dataset.jobId);
    });
    $('models-train-form')?.addEventListener('submit', submitTraining);
    $('models-train-reset-check')?.addEventListener('change', updateTrainingConfirmation);
    $('models-train-reset-text')?.addEventListener('input', updateTrainingConfirmation);
    $('models-train-days')?.addEventListener('input', () => {
      if (state.trainingModel) $('models-train-target').textContent = `${title(`model_${state.trainingModel}`)} · XRPUSDT · 1h · ${$('models-train-days').value} días`;
    });
    $('models-train-close')?.addEventListener('click', () => $('models-train-dialog').close());
  });
  window.loadModelsPage = loadModelsPage;
  window.ModelsPageTest = Object.freeze({ renderStats, renderForecast, renderWiden, renderDirection, renderDirectionRow, renderHistory, renderVolBattleTable, renderVolCoverage, sampleNotice, formatVolCsv, renderV4Qlike, renderVolHistory, filterHistoryWindow, renderTrainingCurrent, renderTrainingJobs, activeTraining, configureTrainingPolling, trainingElapsedSeconds, formatTrainingDate, trainingTimesDiffer, isTrainingPolling: () => Boolean(state.trainingPoll) });
})();
