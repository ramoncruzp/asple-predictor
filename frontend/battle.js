(() => {
  const d = window.BattlePageDeps;
  const state = { payload: null, limit: 30, sort: { key: 'accuracy', dir: 'desc' }, filters: { model: '', signal: '', result: '', hideNeutral: false } };
  const root = () => document.getElementById('battle-content');
  const esc = value => d.escapeHtml(value ?? '');
  const count = value => Math.max(0, Number(value || 0));
  const pct = value => value == null ? '\u2014' : `${(Number(value) * 100).toFixed(1)}%`;
  const conditionNames = ['rsi_oversold', 'high_volume', 'strong_trend', 'ranging_market', 'post_macd_cross'];

  function card(model, counts) {
    const positive = count(counts?.bullish_count);
    if (model.available === false) return `<strong>No disponible</strong><small>${esc(model.unavailable_reason || 'Motivo no informado')}</small>`;
    if (positive === 0) {
      const base = counts?.base_rate == null ? '' : ` \u00B7 tasa base ${pct(counts.base_rate)}`;
      return `<strong>Sin se\u00F1ales ALCISTA</strong><small>${count(counts?.neutral_count)} neutrales \u00B7 ${count(counts?.verified_count)} verificadas${base}</small>`;
    }
    const evaluated = count(counts?.bullish_correct) + count(counts?.bullish_failed);
    return `<strong>${evaluated ? pct(counts.bullish_correct / evaluated) : '\u2014'}</strong><small>${count(counts?.bullish_correct)} aciertos / ${count(counts?.bullish_failed)} fallos ALCISTA</small>`;
  }

  function renderCards(payload) {
    return (payload.status?.models || []).map(model => `<article class="card battle-score"><h3>${d.modelHeader(model.model_name)} ${d.validationStatusTag(model)}</h3>${card(model, payload.context?.models?.[model.model_name])}<a href="#models">Ver estado y detalle en Modelos</a></article>`).join('');
  }

  function renderMatrix(payload) {
    const models = payload.status?.models || [];
    const rows = conditionNames.map(name => {
      const entries = models.map(model => (payload.conditions?.[model.model_name] || []).find(row => row.condition_name === name) || {});
      const missing = entries.every(row => !count(row.verified_count ?? row.verified_predictions));
      const scored = entries.filter(row => count(row.verified_count ?? row.verified_predictions) > 0 && row.accuracy != null && Number.isFinite(Number(row.accuracy)));
      const best = scored.length ? Math.max(...scored.map(row => Number(row.accuracy))) : null;
      const html = `<tr><th scope="row"><span class="condition-label">${esc(d.CONDITION_NAMES[name] || name)} ${d.tooltip(d.CONDITION_TOOLTIPS[name] || '')}</span></th>${entries.map(row => {
        const verified = count(row.verified_count ?? row.verified_predictions);
        const winner = verified > 0 && best !== null && Number(row.accuracy) === best;
        return `<td class="${winner ? 'winner' : ''}">${row.accuracy == null || !verified ? '\u2014' : pct(row.accuracy)}<small>${verified} de ${count(row.total_predictions)} verificadas</small></td>`;
      }).join('')}</tr>`;
      return { missing, html };
    });
    const table = rows => `<div class="battle-matrix-scroll"><table class="data-table battle-matrix"><thead><tr><th>Condici\u00F3n</th>${models.map(m => `<th>${d.modelHeader(m.model_name)} ${d.validationStatusTag(m)}</th>`).join('')}</tr></thead><tbody>${rows.map(r => r.html).join('')}</tbody></table></div>`;
    const ready = rows.filter(row => !row.missing), empty = rows.filter(row => row.missing);
    return `<section class="card table-card"><h2>Matriz por condici\u00F3n</h2>${table(ready)}${empty.length ? `<details class="battle-no-data"><summary>Sin datos a\u00FAn (${empty.length} condiciones)</summary>${table(empty)}</details>` : ''}</section>`;
  }

  function ranking(payload) {
    const rows = (payload.status?.models || []).map(model => {
      const c = payload.context?.models?.[model.model_name] || {};
      const evaluated = count(c.bullish_correct) + count(c.bullish_failed);
      return { name: model.display_name || model.nombre || d.getModelName(model.model_name), accuracy: evaluated >= 30 ? count(c.bullish_correct) / evaluated : null,
        correct: count(c.bullish_correct), failed: count(c.bullish_failed), evaluated };
    });
    const key = state.sort.key, factor = state.sort.dir === 'asc' ? 1 : -1;
    return rows.sort((a, b) => {
      const left = a[key], right = b[key];
      if (left == null || right == null) return left == null ? (right == null ? 0 : 1) : -1;
      return (typeof left === 'string' ? left.localeCompare(right) : left - right) * factor;
    });
  }

  function sortForTest(payload, key, dir) {
    state.sort = { key, dir };
    return ranking(payload).map(row => row.name);
  }

  function renderRanking(payload) {
    let place = 0;
    const rows = ranking(payload).map(row => `<tr><td>${row.accuracy == null ? 'Pocos datos' : ++place}</td><td>${esc(row.name)}</td><td>${pct(row.accuracy)}</td><td>${row.correct} / ${row.failed}</td><td>${row.evaluated}</td></tr>`).join('');
    const sort = (key, label) => `<button type="button" data-battle-sort="${key}">${label} ${state.sort.key === key ? (state.sort.dir === 'asc' ? '\u25B2' : '\u25BC') : ''}</button>`;
    return `<section class="card table-card"><h2>Ranking de direcci\u00F3n</h2><p class="muted">Puesto por precisi\u00F3n ALCISTA solo con 30 o m\u00E1s se\u00F1ales verificadas; NEUTRAL no cuenta.</p><table class="data-table"><thead><tr><th>Puesto</th><th>${sort('name', 'Modelo')}</th><th>${sort('accuracy', 'Precisi\u00F3n')}</th><th>${sort('correct', 'Aciertos / fallos')}</th><th>${sort('evaluated', 'Se\u00F1ales')}</th></tr></thead><tbody>${rows}</tbody></table></section>`;
  }

  function filterRows(rows, filters = state.filters) {
    return rows.filter(row => (!filters.model || row.model_name === filters.model)
      && (!row.interval || row.interval === '1h')
      && (!filters.signal || row.signal === filters.signal)
      && (!filters.result || (filters.result === 'PENDIENTE' ? !row.is_verified : row.is_verified && (row.was_correct == null ? 'NEUTRAL' : row.was_correct ? 'ACIERTO' : 'FALLO') === filters.result))
      && (!filters.hideNeutral || String(row.signal || '').toUpperCase() !== 'NEUTRAL'));
  }

  function csvEscape(value) {
    const text = String(value ?? '');
    return /[",\r\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
  }

  function csv(rows) {
    const header = ['Fecha', 'S\u00EDmbolo', 'Modelo', 'Se\u00F1al', 'Prob. de subida', 'Resultado', 'Rango de volatilidad esperado 4h (1\u03C3 \u00B7 2\u03C3)'];
    const body = rows.map(row => [row.predicted_at, row.symbol, row.model_name, row.signal, row.probability_up,
      !row.is_verified ? 'PENDIENTE' : row.was_correct == null ? 'NEUTRAL' : row.was_correct ? 'ACIERTO' : 'FALLO', d.volatilityDetail(row)]);
    // BOM UTF-8 incluido solo para que Excel abra correctamente las tildes.
    return '\uFEFF' + [header, ...body].map(record => record.map(csvEscape).join(',')).join('\r\n');
  }

  function csvFilename(context, date = new Date()) {
    return `battle_direccion_${context?.symbol || 'XRPUSDT'}_${context?.interval || '1h'}_${date.toISOString().slice(0, 10).replace(/-/g, '')}.csv`;
  }

  function renderHistory(payload) {
    const rows = filterRows(payload.history || []), visible = rows.slice(0, state.limit), models = payload.status?.models || [];
    const option = (key, label, values) => `<label>${label}<select data-battle-filter="${key}"><option value="">Todos</option>${values.map(v => `<option value="${esc(v.value)}" ${state.filters[key] === v.value ? 'selected' : ''}>${esc(v.label)}</option>`).join('')}</select></label>`;
    const modelOptions = models.map(model => ({ value: model.model_name, label: model.display_name || model.nombre || d.getModelName(model.model_name) }));
    const filters = `<div class="battle-filters">${option('model', 'Modelo', modelOptions)}${option('signal', 'Se\u00F1al', ['ALCISTA', 'BAJISTA', 'NEUTRAL'].map(value => ({ value, label: value })))}${option('result', 'Resultado', ['ACIERTO', 'FALLO', 'NEUTRAL', 'PENDIENTE'].map(value => ({ value, label: value })))}<label><input type="checkbox" data-battle-hide-neutral ${state.filters.hideNeutral ? 'checked' : ''}> Ocultar neutrales</label><button type="button" data-battle-csv>Descargar CSV visible</button></div>`;
    const body = visible.map(row => {
      const result = !row.is_verified ? 'PENDIENTE' : row.was_correct == null ? 'NEUTRAL' : row.was_correct ? 'ACIERTO' : 'FALLO';
      return `<tr><td>${esc(d.formatDateTime(row.predicted_at))}</td><td>${esc(row.symbol)}</td><td>${esc(d.getModelName(row.model_name))}</td><td><span class="signal ${d.signalClass(row.signal)}">${esc(row.signal)}</span></td><td class="mono">${pct(row.probability_up)}</td><td>${result}</td><td class="muted">${esc(d.volatilityDetail(row))}</td></tr>`;
    }).join('');
    return `<section class="card table-card"><h2>Registro de predicciones \u00B7 XRPUSDT \u00B7 1h</h2>${filters}<table class="data-table"><thead><tr><th>Fecha</th><th>S\u00EDmbolo</th><th>Modelo</th><th>Se\u00F1al</th><th>Prob. de subida</th><th>Resultado</th><th>Rango de volatilidad esperado 4h (1\u03C3 \u00B7 2\u03C3)</th></tr></thead><tbody>${body || '<tr><td colspan="7">Sin historial.</td></tr>'}</tbody></table><p class="muted">\u2713 dentro de 1\u03C3 \u00B7 \u2717 fuera de 1\u03C3</p>${rows.length > 100 && visible.length === 100 ? '<p class="muted" data-battle-limit-note>Mostrando las \u00FAltimas 100</p>' : rows.length > visible.length ? '<button type="button" data-battle-more>Ver m\u00E1s</button>' : ''}</section>`;
  }

  function render(payload) {
    state.payload = payload;
    root().innerHTML = `<p class="battle-context"><strong>${esc(payload.context?.symbol || 'XRPUSDT')} \u00B7 ${esc(payload.context?.interval || '1h')}</strong><small>\u00DAnico par con modelos de direcci\u00F3n hoy</small></p><p class="muted battle-score-disclaimer">Los puntajes de A, B y C no son probabilidades calibradas: reponderan las clases. En la auditor\u00EDa, el umbral 0,60 correspondi\u00F3 a \u224827 % real.</p><div class="battle-score-grid">${renderCards(payload)}</div>${renderMatrix(payload)}${renderRanking(payload)}${renderHistory(payload)}`;
  }

  function downloadCsv() {
    const rows = filterRows(state.payload.history || []).slice(0, state.limit);
    const blob = new Blob([csv(rows)], { type: 'text/csv;charset=utf-8' }), url = URL.createObjectURL(blob), link = document.createElement('a');
    link.href = url; link.download = csvFilename(state.payload.context); link.click(); URL.revokeObjectURL(url);
  }

  async function loadBattlePage() {
    try {
      const [status, context] = await Promise.all([d.api.modelsStatus(), d.api.get('/api/models/page-context', { symbol: 'XRPUSDT', interval: '1h' })]);
      const names = (status.models || []).map(model => model.model_name);
      const conditions = await Promise.all(names.map(name => d.api.conditions(name)));
      const historyResponse = await d.api.history('XRPUSDT', null, 30);
      render({ status, context, conditions: Object.fromEntries(names.map((name, i) => [name, conditions[i]])), history: Array.isArray(historyResponse) ? historyResponse : historyResponse.predictions || historyResponse.items || [] });
    } catch (error) { root().innerHTML = `<p class="muted">${esc(d.panelLoadError('Battle de Modelos', error))}</p>`; }
  }

  document.addEventListener('click', event => {
    const sort = event.target.closest('[data-battle-sort]');
    if (sort && state.payload) {
      const key = sort.dataset.battleSort;
      state.sort = { key, dir: state.sort.key === key && state.sort.dir === 'desc' ? 'asc' : 'desc' };
      const section = [...root().querySelectorAll('section')].find(node => node.querySelector('h2')?.textContent === 'Ranking de direcci\u00F3n');
      if (section) section.outerHTML = renderRanking(state.payload);
    }
    if (event.target.closest('[data-battle-more]') && state.payload) {
      state.limit = Math.min(100, state.limit + 30);
      const section = root().querySelector('section:last-of-type');
      if (section) section.outerHTML = renderHistory(state.payload);
    }
    if (event.target.closest('[data-battle-csv]') && state.payload) downloadCsv();
  });
  document.addEventListener('change', event => {
    const target = event.target;
    if (!state.payload) return;
    if (target.matches('[data-battle-filter]')) state.filters[target.dataset.battleFilter] = target.value;
    else if (target.matches('[data-battle-hide-neutral]')) state.filters.hideNeutral = target.checked;
    else return;
    state.limit = 30;
    const section = root().querySelector('section:last-of-type');
    if (section) section.outerHTML = renderHistory(state.payload);
  });
  window.loadBattlePage = loadBattlePage;
  window.BattlePageTest = Object.freeze({ card, render, renderCards, renderMatrix, renderRanking, renderHistory, ranking, sortForTest, filterRows, csv, csvEscape, csvFilename });
})();
