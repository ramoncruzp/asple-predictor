(() => {
  const root = () => document.querySelector('#scanner-root');
  const esc = value => String(value ?? 'no disponible').replace(/[&<>\'"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));
  const apiBase = () => window.location.protocol === 'file:' ? 'http://localhost:8000' : window.location.origin;
  const PREVIEW_TIMEOUT_MS = 30000;
  const OPEN_REQUEST_KEYS = ['symbol','strategy','capital','range_low','range_high','n_levels','target_pct','target_usdt','target_basis','max_days','params','dry_run','confirm','from_scan','allow_unready_coin'];
  function openRequestBody(body) { return Object.fromEntries(OPEN_REQUEST_KEYS.filter(key=>body[key]!==undefined).map(key=>[key,body[key]])); }
  function apiDetailText(detail) {
    if (Array.isArray(detail)) return detail.map(item => {
      if (item && typeof item === 'object') {
        const field = Array.isArray(item.loc) ? item.loc[item.loc.length - 1] : null;
        const message = typeof item.msg === 'string' ? item.msg : JSON.stringify(item);
        return field == null ? message : `${field}: ${message}`;
      }
      return String(item);
    }).join('; ');
    if (detail && typeof detail === 'object') return JSON.stringify(detail);
    return detail;
  }
  async function call(path, body, options={}) {
    const response = await fetch(apiBase() + path, { method: body ? 'POST' : 'GET', headers: { ...(body ? {'Content-Type':'application/json'} : {}), ...(window.gridApiToken ? {'X-API-Token':window.gridApiToken} : {}) }, ...(body ? {body:JSON.stringify(body)} : {}), ...(options.signal ? {signal:options.signal} : {}) });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) { const error = new Error(apiDetailText(data.detail) || `HTTP ${response.status}`); error.status=response.status; throw error; }
    return data;
  }
  async function callStructure(body, options={}) {
    for (let attempt=0; attempt<2; attempt++) {
      try { return await call('/api/grids/structure-preview', body, options); }
      catch (error) {
        if (error.name === 'AbortError') throw error;
        if (error.status === 405) throw new Error('El servidor no tiene esta funci\u00f3n; reinicia el backend.');
        if (attempt === 1) throw error;
        await new Promise(resolve=>setTimeout(resolve,250));
      }
    }
  }
  function expandPrecision(value, digits=6) {
    const number=Number(value);
    if (!Number.isFinite(number)) return String(value ?? '');
    if (number===0) return '0';
    const precise=Math.abs(number).toPrecision(digits), parts=precise.split('e');
    const mantissa=parts[0], exponent=Number(parts[1]||0), point=(mantissa.indexOf('.')<0?mantissa.length:mantissa.indexOf('.'))+exponent;
    const digitsOnly=mantissa.replace('.','');
    let expanded=point<=0?`0.${'0'.repeat(-point)}${digitsOnly}`:point>=digitsOnly.length?`${digitsOnly}${'0'.repeat(point-digitsOnly.length)}`:`${digitsOnly.slice(0,point)}.${digitsOnly.slice(point)}`;
    return expanded.includes('.')?expanded.replace(/0+$/,'').replace(/\.$/,''):expanded;
  }
  const pct = value => { if(value==null)return 'no disponible';const number=Number(value);if(number===0)return '0,000 %';if(number>0&&number<.001)return '< 0,001 %';return `${number.toFixed(3).replace('.',',')} %`; };
  const money = value => { if(value==null)return 'no disponible';const number=Number(value);if(number>0&&number<.01)return '< $0,01';return `$${number.toFixed(2).replace('.',',')}`; };
  function formatInput(value,spacing=false) { const number=Number(value);if(!Number.isFinite(number))return String(value??'');return spacing ? number.toFixed(3).replace(/\.?0+$/,''):expandPrecision(number,6); }
  function setNumericField(selector,value,spacing=false) { const field=document.querySelector(selector);field.value=formatInput(value,spacing);field.dataset.exact=String(value); }
  function fieldValue(selector) { const field=document.querySelector(selector);return field.dataset.exact??field.value; }
  const structureSignature = body => JSON.stringify(Object.fromEntries(['symbol','capital','strategy','k_width','range_low','range_high','n_levels','spacing_pct','target_pct','target_usdt','margin_target_pct'].filter(key=>body[key]!==undefined).map(key=>[key,body[key]])));
  function cellGuidance(data) {
    if (!data) return '';
    const warning=data.min_cell_warning?`<p class="scanner-warning">${esc(data.min_cell_warning)}</p>`:'';
    const functionalWarning=data.functional_cell_warning?`<p class="scanner-warning">${esc(data.functional_cell_warning)}</p>`:'';
    const dust=data.dust_min_cell_usdt==null?'':`<p>Tama\u00f1o m\u00ednimo estimado de celda para polvo \u2264 ${Number(data.dust_target_pct??.1).toFixed(1)}%: ${money(data.dust_min_cell_usdt)}.</p>`;
    return warning+functionalWarning+dust;
  }
  function sigmaSurfacesLine(data) {
    const values=data?.sigma_surfaces||{};
    const format=(key,label)=>{const item=values[key]||{};return `${label}: ${item.value==null?'no disponible':`${(Number(item.value)*100).toFixed(3)}%`} (${esc(item.source||'sin fuente')}, ${esc(item.window||'sin ventana')})${item.reason?` \u00B7 ${esc(item.reason)}`:''}`;};
    return `<p class="muted dry-run-sigma-surfaces">${format('realized_30d','\u03C3 realizada 30 d')} \u00B7 ${format('champion_24h','\u03C3 campe\u00F3n 24 h')} \u00B7 ${format('champion_monitor_h',`\u03C3 campe\u00F3n vigilancia ${Number(values.monitor_h||4)} h`)}</p>`;
  }
  function errorText(error) { return `${error.status && error.status !== 409 ? `${error.status}: ` : ''}${error.message || 'Error de conexi\u00f3n'}`; }
  function coinOption(coin) {
    const readiness = coin?.readiness || {};
    const insufficient = readiness.state === 'datos_insuficientes';
    const ready = coin?.symbol === 'XRPUSDT' || insufficient || !(coin?.ready === false || (readiness.state && readiness.state !== 'lista'));
    const label = insufficient ? ' (sin modelo)' : (ready ? '' : (['descargando','entrenando','consensuando'].includes(readiness.state) ? ' (preparando)' : ' (no lista)'));
    return `<option value="${esc(coin.symbol)}" ${ready ? '' : 'disabled'}>${esc(coin.symbol)}${label}</option>`;
  }
  function displayPrice(value) { return value == null ? 'no disponible' : expandPrecision(value, 12); }
  function previewError(error, pricePlan=null) {
    if (/strictly inside|outside range/i.test(error?.message || '')) {
      if (pricePlan) return new Error(`El precio de Testnet est\u00e1 fuera del rango del grid; vuelve a calcular la vista previa. Precio Testnet: ${displayPrice(pricePlan.testnet_price)}; precio p\u00fablico: ${displayPrice(pricePlan.current_price)}; rango [${pricePlan.range_low} \u2013 ${pricePlan.range_high}].`);
      return new Error('El precio actual est\u00e1 fuera del rango; ajusta el rango para ver la vista previa');
    }
    return error;
  }
  const baseForm = () => ({symbol:document.querySelector('#sc-symbol').value, capital:document.querySelector('#sc-capital').value, strategy:document.querySelector('#sc-strategy').value});
  function variantCard(key, row) {
    return `<button type="button" class="scanner-variant ${row.feasible?'':'is-infeasible'}" data-variant="${esc(key)}" ${row.feasible?'':'disabled'}><strong>${esc(key)}</strong><span>${esc(row.range_low)} \u2013 ${esc(row.range_high)}</span><span>${esc(row.n_levels)} niveles \u00b7 celda ${money(row.cell_usdt)}</span><span>Separaci\u00f3n ${pct(row.spacing_pct)} \u2212 comisiones ${pct(row.edge_gross_pct==null ? null:row.spacing_pct-row.edge_gross_pct)} \u2212 polvo ${pct(row.dust_estimate_pct)} = neto ${pct(row.edge_after_dust_pct)}</span>${cellGuidance(row)}<span>${row.feasible?'Viable':esc((row.reasons||[]).join(' '))}</span></button>`;
  }
  function levelTable(plan, structure) {
    const rows=(plan.cells||[]).map(cell=>`<tr><td>${Number(cell.level_idx)+1}</td><td>${esc(cell.buy_price)}</td><td>${esc(cell.sell_price)}</td><td>${esc(cell.quantity)}</td><td>${money(cell.capital)}</td><td>${pct(cell.gross_margin_pct)} \u00b7 ${money(cell.gross_margin_usdt)}</td><td>${pct(cell.net_margin_pct)} \u00b7 ${money(cell.net_margin_usdt)}</td></tr>`).join('');
    return `<div class="table-card"><table class="data-table"><thead><tr><th>Nivel</th><th>Compra</th><th>Venta</th><th>Cantidad</th><th>USDT</th><th>Promedio de la tabla tras comisiones</th><th>Neto tras polvo (estimaci\u00f3n pesimista)</th></tr></thead><tbody>${rows}</tbody></table><p class="vol-footnote">Estimaci\u00f3n: comisiones de 0,1 % por lado; el polvo es una estimaci\u00f3n conservadora, no una medici\u00f3n.</p></div>`;
  }
  function explainScanRows(container, rows) {
    const headings=container.querySelectorAll('.scanner-table thead th');
    const help=(text)=>`<details class="scan-help"><summary>Ayuda</summary><span>${esc(text)}</span></details>`;
    if(headings[1])headings[1].innerHTML=`Elegible ${help('Cumple filtros duros; spread bajo el m\u00e1ximo o de 1 tick. No garantiza ganancia.')}`;
    if(headings[2])headings[2].innerHTML=`Puntaje ${help('De 0 a 1; pesos: costo 0,35, oscilaci\u00f3n 0,35, liquidez 0,20 y tendencia 0,10. Descriptivo; no predice.')}`;
    if(headings[3])headings[3].textContent='Margen bruto tras comisiones';
    if(headings[4])headings[4].innerHTML=`Neto tras polvo estimado ${help('Ejemplo con esta fila: separaci\u00f3n \u2212 comisiones \u2212 polvo estimado = neto. La estimaci\u00f3n de polvo depende del tama\u00f1o de celda y stepSize.')}`;
    const mainRows=container.querySelectorAll('.scanner-table tbody > tr:nth-child(2n+1)');
    mainRows.forEach((tr,index)=>{const row=rows[index]||{},s=row.suggested_structure||{},fee=row.fee_pct;
      tr.cells[3].textContent=pct(s.spacing_pct==null||fee==null ? null:Number(s.spacing_pct)-2*Number(fee));
      tr.cells[4].textContent=s.net_edge_pct_per_cycle==null?'no disponible':`${pct(s.net_edge_pct_per_cycle)} neto estimado (pesimista, no medido)`;
    });
  }
  function render(rootNode, coins) {
    rootNode.innerHTML = `<div class="page-heading"><div><p class="eyebrow">DATOS P\u00daBLICOS DE MERCADO</p><h1>Scanner</h1></div><span class="muted">La apertura es solo Testnet</span></div>
      <section class="card scanner-panel"><h2>Escanear</h2><div class="scanner-controls"><label>Capital USDT<input id="sc-capital" type="number" min="1" step="any" value="100"></label><label>Estrategia<select id="sc-strategy"><option value="simple">Simple</option><option value="smart">Smart</option></select></label></div><fieldset><legend>Monedas activas del registro</legend><div class="scanner-coins">${coins.map(c=>`<label><input type="checkbox" class="sc-coin" value="${esc(c.symbol)}" checked>${esc(c.symbol)}</label>`).join('')}</div></fieldset><button id="sc-run" class="button primary">Escanear</button><p id="sc-error" class="scanner-error" role="alert" hidden></p><p id="sc-note" class="muted" hidden>Con pocas monedas el puntaje no permite comparar.</p><div id="sc-results" class="table-card"><p class="muted">Selecciona monedas y ejecuta el escaneo.</p></div></section>
      <section class="card scanner-panel"><h2>Crear grid</h2><div class="scanner-controls"><label>Moneda<select id="sc-symbol">${coins.map(coinOption).join('')}</select></label><label>Capital USDT<input id="sc-create-capital" type="number" min="1" step="any" value="100"></label><label>Estrategia<select id="sc-create-strategy"><option value="simple">Simple</option><option value="smart">Smart</option></select></label></div><div id="sc-unready-ack-container" hidden></div><div id="sc-open-grid-warning" class="scanner-warning" hidden></div><div id="sc-variants" class="scanner-variants"><p class="muted">Selecciona moneda para calcular variantes.</p></div><div class="scanner-controls"><label>Rango bajo<input id="sc-low" type="number" min="0" step="any"></label><label>Rango alto<input id="sc-high" type="number" min="0" step="any"></label><label>Niveles<input id="sc-levels" type="number" min="4" max="60" step="1"></label><label>Espaciado (%)<input id="sc-spacing" type="number" min="0" step="any"></label><label>Margen m\u00ednimo tras comisiones (%)<input id="sc-margin-target" type="number" min="0.01" step="0.01" value=""><small id="sc-margin-hint" class="muted"></small></label><label>Meta %<input id="sc-target-pct" type="number" min="0" step="any" placeholder="opcional"></label><label>Meta USDT<input id="sc-target-usdt" type="number" min="0" step="any" placeholder="opcional"></label><label>Base meta<select id="sc-target-basis"><option value="cash">Cash</option><option value="equity">Equity</option></select></label><label>Plazo m\u00e1ximo (d\u00edas)<input id="sc-days" type="number" min="0.0001" step="any" value="7"></label><label class="scanner-check"><input id="sc-compound-enabled" type="checkbox">Inter\u00e9s compuesto</label><div id="sc-compound-fields" hidden><label>Reinversi\u00f3n de ganancia (%)<input id="sc-compound-ratio" type="number" min="1" max="100" step="any" value="100"></label><label>Tope de crecimiento del capital (%)<input id="sc-compound-cap" type="number" min="0.01" step="any" value="100"></label></div></div><p class="muted">El inter\u00e9s compuesto reinvierte parte de las ganancias, aumenta las \u00f3rdenes futuras y requiere USDT libre. Aplica a ciclos futuros. No validado en Testnet.</p><p class="muted">Al editar espaciado se conserva el rango y se recalculan niveles; al editar niveles se calcula espaciado; al editar rango se conserva el n\u00famero de niveles.</p><label class="scanner-check"><input id="sc-testnet" type="checkbox" checked>Modo prueba Testnet; las \u00f3rdenes se env\u00edan al entorno de prueba.</label><div id="sc-edited" class="scanner-result" aria-live="polite">Edita o selecciona una variante para ver el margen.</div><button id="sc-preview-open" class="button primary">Vista previa</button><p id="sc-preview-progress" class="muted" role="status" hidden></p><p id="sc-open-error" class="scanner-error" role="alert" hidden></p><div id="sc-dialog" class="scanner-dialog" hidden role="dialog" aria-modal="true" aria-labelledby="sc-dialog-title"></div></section>`;
    const q = selector => rootNode.querySelector(selector);
    const readinessBySymbol = new Map(coins.map(coin => [coin.symbol, coin?.readiness || {}]));
    q('#sc-strategy').value='smart';q('#sc-create-strategy').value='smart';
    q('#sc-note').hidden=coins.length>2;
    let lastPreview=null, lastStructureSignature=null, selectedVariant=null, timer=null, previewBusy=false, opening=false, dialogOpen=false, serverMinimumMargin=null, marginTargetTouched=false;
    let requestGeneration=0, activeController=null, previewFlight=null, planNeedsUnreadyAck=false;
    const setError = (selector,error) => { const el=q(selector); el.textContent=error ? errorText(error):''; el.hidden=!error; };
    function selectedNeedsUnreadyAck() { return readinessBySymbol.get(q('#sc-symbol').value)?.state === 'datos_insuficientes'; }
    function syncUnreadyAckButtons() {
      const acknowledged = Boolean(q('#sc-unready-ack')?.checked);
      const preview = q('#sc-preview-open');
      preview.disabled = opening || dialogOpen || (selectedNeedsUnreadyAck() && !acknowledged);
      const confirm = q('#sc-dialog [data-confirm]');
      if(confirm)confirm.disabled=confirm.dataset.planBlocked==='true'||(planNeedsUnreadyAck&&!acknowledged);
    }
    function renderUnreadyAck() {
      const readiness=readinessBySymbol.get(q('#sc-symbol').value)||{},container=q('#sc-unready-ack-container');
      if(readiness.state!=='datos_insuficientes') { container.hidden=true;container.innerHTML=''; }
      else {
        const history=readiness.history_days==null?'\u2014':esc(readiness.history_days);
        container.hidden=false;
        container.innerHTML=`<label class="scanner-check"><input id="sc-unready-ack" type="checkbox">Entiendo que esta moneda no tiene modelo (historial ${history} d\u00edas de 540 requeridos); el grid usar\u00e1 volatilidad realizada y no est\u00e1 validado</label>`;
        q('#sc-unready-ack').addEventListener('change',syncUnreadyAckButtons);
      }
      syncUnreadyAckButtons();
    }
    async function refreshOpenWarning() { try { const data=await call('/api/grids'); const symbol=q('#sc-symbol').value; const active=(data.grids||[]).some(g=>g.symbol===symbol&&!['CLOSED','ERROR'].includes(g.status)); const el=q('#sc-open-grid-warning'); el.hidden=!active; el.textContent=active?'Dos grids de igual estructura en la misma moneda se cruzan entre s\u00ed; el API rechaza un segundo grid en el mismo s\u00edmbolo.':''; } catch (_) {} }
    function requestBody(edited=false) { const target=q('#sc-margin-target').value;const b={...baseForm(),symbol:q('#sc-symbol').value,capital:q('#sc-create-capital').value,strategy:q('#sc-create-strategy').value};if(target)b.margin_target_pct=Number(target); if (edited && q('#sc-low').value && q('#sc-high').value && q('#sc-levels').value) Object.assign(b,{range_low:fieldValue('#sc-low'),range_high:fieldValue('#sc-high'),n_levels:Number(q('#sc-levels').value)}); const targetPct=q('#sc-target-pct').value,targetUsdt=q('#sc-target-usdt').value; if(targetPct)b.target_pct=targetPct;if(targetUsdt)b.target_usdt=targetUsdt;return b; }
    function beginRequest() { if(activeController)activeController.abort();activeController=new AbortController();requestGeneration+=1;return {controller:activeController,generation:requestGeneration}; }
    function currentRequest(generation,controller) { return generation===requestGeneration&&!controller.signal.aborted; }
    function isAbort(error) { return error?.name==='AbortError'; }
    function cellFailure(data,capital) {
      const minimum=Number(data?.minimum_cell_usdt??lastPreview?.minimum_cell_usdt),levels=Number(data?.n_levels),amount=Number(data?.cell_usdt??(levels ? Number(capital)/levels:NaN));
      const reasons=(data?.reasons||[]).filter(Boolean);
      if(Number.isFinite(minimum)&&minimum>0&&Number.isFinite(amount)&&amount<minimum){const maxLevels=Math.floor(Number(capital)/minimum),required=levels*minimum;const suggestion=maxLevels>=4?`Reduce a ${maxLevels} niveles o sube el capital a ${money(required)}.`:`Sube el capital al menos a ${money(4*minimum)} para permitir cuatro niveles.`;reasons.unshift(`Celda ${money(amount)} < m\u00ednimo ${money(minimum)}. ${suggestion}`);}
      return reasons.join(' ' )||'La estructura no cumple los requisitos de apertura.';
    }
    function updateMarginHint() { if(serverMinimumMargin==null)return;const target=Number(q('#sc-margin-target').value);q('#sc-margin-hint').textContent=Number.isFinite(target)&&target<serverMinimumMargin?`Al abrir, el servidor exige al menos ${pct(serverMinimumMargin)} (GRID_MIN_MARGIN_AFTER_FEES_PCT).`:`M\u00ednimo del servidor: ${pct(serverMinimumMargin)}`; }
    function showStructure(data,signature,generation,controller) {
      if(!currentRequest(generation,controller))return;
      lastPreview=data;lastStructureSignature=signature;
      if(data.minimum_margin_after_fees_pct!=null){serverMinimumMargin=Number(data.minimum_margin_after_fees_pct);if(!marginTargetTouched||!q('#sc-margin-target').value)q('#sc-margin-target').value=serverMinimumMargin.toFixed(2);updateMarginHint();}
      const edited=data.edited;
      const gross=pct(edited?.edge_gross_pct),net=pct(edited?.edge_after_dust_pct),warning=edited?.feasible?'Viable':esc(cellFailure(edited,q('#sc-create-capital').value)),dustInfo=edited?.dust_warning?` Informativo: ${esc(edited.dust_warning_message||'Polvo estimado alto; dato informativo.')}`:'';
      q('#sc-edited').innerHTML=edited?`Margen tras comisiones ${gross}. Neto tras polvo ${net}: estimaci\u00f3n pesimista, no medida. ${warning}${dustInfo}${cellGuidance(edited)}`:'No hay una estructura editada completa.';
    }
    function structureBody(source='') { const body=requestBody(true);if(source==='spacing'){delete body.n_levels;body.spacing_pct=fieldValue('#sc-spacing');}return body; }
    function requestStructure(body, owner=null) {
      const signature=structureSignature(body);
      if(previewFlight?.signature===signature&&!previewFlight.controller.signal.aborted)return previewFlight.promise;
      if(previewBusy&&previewFlight?.signature!==signature)beginRequest();
      if(lastStructureSignature===signature&&lastPreview){const request=owner||beginRequest();showStructure(lastPreview,signature,request.generation,request.controller);return Promise.resolve(lastPreview);}
      const request=owner||beginRequest(),{controller,generation}=request;
      previewBusy=true;
      const flight={signature,controller,generation,promise:null};
      flight.promise=(async()=>{try{const data=await callStructure(body,{signal:controller.signal});if(!currentRequest(generation,controller))throw new DOMException('Stale preview','AbortError');showStructure(data,signature,generation,controller);return data;}finally{if(previewFlight===flight)previewBusy=false;}})();
      previewFlight=flight;
      return flight.promise;
    }
    async function loadPreview(source='') {
      const body=structureBody(source);
      try { const data=await requestStructure(body);return data; }
      catch(error){if(!isAbort(error))q('#sc-edited').textContent=`Vista previa no disponible: ${errorText(error)}`;}
    }
    function schedulePreview(source=''){clearTimeout(timer);timer=setTimeout(()=>{timer=null;loadPreview(source);},400);}
    async function loadVariants() { q('#sc-variants').innerHTML='<p class="muted">Calculando estructuras\u2026</p>';try{const body=requestBody(false),data=await requestStructure(body);q('#sc-variants').innerHTML=Object.entries(data.variants).map(([key,row])=>variantCard(key,row)).join('');q('#sc-variants').querySelectorAll('[data-variant]').forEach(button=>button.addEventListener('click',()=>{selectedVariant=button.dataset.variant;const row=data.variants[selectedVariant];setNumericField('#sc-low',row.range_low);setNumericField('#sc-high',row.range_high);q('#sc-levels').value=row.n_levels;setNumericField('#sc-spacing',row.spacing_pct,true);q('#sc-variants').querySelectorAll('.scanner-variant').forEach(el=>el.classList.toggle('selected',el===button));schedulePreview('linked');}));}catch(error){if(!isAbort(error))q('#sc-variants').innerHTML=`<p class="scanner-error">${esc(errorText(error))}</p>`;} }
    q('#sc-symbol').addEventListener('change',()=>{renderUnreadyAck();refreshOpenWarning();loadVariants();});
    ['#sc-create-capital','#sc-create-strategy'].forEach(s=>q(s).addEventListener('change',loadVariants));q('#sc-compound-enabled').addEventListener('change',()=>{q('#sc-compound-fields').hidden=!q('#sc-compound-enabled').checked;});
    ['#sc-low','#sc-high'].forEach(s=>q(s).addEventListener('input',event=>{delete event.currentTarget.dataset.exact;schedulePreview('linked');}));q('#sc-levels').addEventListener('input',()=>schedulePreview('linked'));q('#sc-spacing').addEventListener('input',event=>{delete event.currentTarget.dataset.exact;schedulePreview('spacing');});['#sc-target-pct','#sc-target-usdt','#sc-target-basis'].forEach(s=>q(s).addEventListener('input',()=>schedulePreview()));q('#sc-margin-target').addEventListener('input',()=>{marginTargetTouched=true;updateMarginHint();schedulePreview();});q('#sc-target-pct').addEventListener('input',()=>{if(q('#sc-target-pct').value)q('#sc-target-usdt').value='';});q('#sc-target-usdt').addEventListener('input',()=>{if(q('#sc-target-usdt').value)q('#sc-target-pct').value='';});
    q('#sc-preview-open').addEventListener('click',async()=>{
      if(opening||dialogOpen)return;
      if(selectedNeedsUnreadyAck()&&!q('#sc-unready-ack')?.checked){syncUnreadyAckButtons();return;}
      if(!q('#sc-testnet').checked){setError('#sc-open-error',new Error('Activa Modo prueba Testnet para continuar.'));return;}
      opening=true;
      const button=q('#sc-preview-open'),progress=q('#sc-preview-progress'),started=Date.now();
      button.disabled=true;button.textContent='Calculando\u2026';progress.hidden=false;
      progress.textContent=`Calculando vista previa\u2026 ${Math.floor((Date.now()-started)/1000)} s`;
      const ticker=setInterval(()=>{progress.textContent=`Calculando vista previa\u2026 ${Math.floor((Date.now()-started)/1000)} s`;},250);
      setError('#sc-open-error',null);
      let timeoutHandle=null,timedOut=false,owner=null;
      try {
        const b=requestBody(true);
        if(!b.range_low||!b.range_high||!b.n_levels)throw new Error('Elige una variante o completa rango y niveles antes de abrir.');
        if(timer){clearTimeout(timer);timer=null;}
        const signature=structureSignature(b);
        if(previewFlight?.signature===signature&&!previewFlight.controller.signal.aborted)owner=previewFlight;
        else if(lastStructureSignature===signature&&lastPreview){owner=beginRequest();}
        else { requestStructure(b);owner=previewFlight; }
        const controller=owner.controller,generation=owner.generation;
        const process=async()=>{
          const structure=lastStructureSignature===signature&&lastPreview ? lastPreview:await owner.promise;
          if(!currentRequest(generation,controller))throw new DOMException('Stale preview','AbortError');
          if(!structure.edited?.feasible)throw new Error(cellFailure(structure.edited,b.capital));
          const compoundParams=q('#sc-compound-enabled').checked?{compound_enabled:true,compound_ratio:Number(q('#sc-compound-ratio').value)/100,compound_max_growth_pct:Number(q('#sc-compound-cap').value)}:undefined;const allowUnreadyCoin=selectedNeedsUnreadyAck()&&q('#sc-unready-ack')?.checked;const openBody=openRequestBody({...b,...(allowUnreadyCoin?{allow_unready_coin:true}:{}),dry_run:true,max_days:q('#sc-days').value,target_basis:q('#sc-target-basis').value,params:compoundParams});planNeedsUnreadyAck=Boolean(openBody.allow_unready_coin);
          const plan=await call('/api/grids/open',openBody,{signal:controller.signal});
          if(!currentRequest(generation,controller))throw new DOMException('Stale preview','AbortError');
          if(!plan.dry_run)throw new Error('El API no devolvi\u00f3 un plan de vista previa.');
          const dialog=q('#sc-dialog'),margin=plan.margin_guard||{},edited=structure.edited;
          const minimum=margin.minimum_pct;
          if(minimum!=null){serverMinimumMargin=Number(minimum);if(!marginTargetTouched||!q('#sc-margin-target').value)q('#sc-margin-target').value=serverMinimumMargin.toFixed(2);updateMarginHint();}
          q('#sc-edited').innerHTML=`Margen tras comisiones ${pct(edited.edge_gross_pct)}${minimum==null?'':` (m\u00ednimo exigido: ${pct(minimum)})`}. Neto tras polvo ${pct(edited.edge_after_dust_pct)}: estimaci\u00f3n pesimista, no medida. ${edited.feasible?'Viable':esc(cellFailure(edited,b.capital))}${cellGuidance(edited)}`;
          const distance=`Precio ${plan.price_in_range?'dentro':'fuera'} del rango; distancia al piso ${pct(plan.distance_to_floor_pct)} y al techo ${pct(plan.distance_to_ceiling_pct)}.`;
          const orders=plan.initial_order_count??'no disponible',gross=margin.edge_gross_pct??margin.actual_pct,priceGuard=plan.testnet_price_guard||{allowed:null,reason:'No se pudo leer el precio de Testnet; la apertura puede fallar.'},publicPrice=displayPrice(plan.current_price),testnetPrice=displayPrice(plan.testnet_price),testnetBlocked=plan.testnet_in_range===false,testnetUnavailable=priceGuard.allowed===null,confirmDisabled=!margin.allowed||testnetBlocked;
          dialog.innerHTML=`<div class="scanner-dialog-card"><h3 id="sc-dialog-title">Plan de apertura Testnet</h3><p>${esc(plan.symbol)} \u00b7 ${esc(plan.strategy)} \u00b7 capital ${money(plan.capital)} USDT</p><ul><li>Rango ${esc(plan.range_low)} \u2013 ${esc(plan.range_high)}</li><li>Precio p\u00fablico: ${esc(publicPrice)}</li><li>Precio Testnet: ${esc(testnetPrice)}</li><li>${esc(plan.n_levels)} niveles; celda ${money(plan.cell_usdt)}; ${orders} \u00f3rdenes iniciales</li><li>${esc(distance)}</li><li>Margen tras comisiones: ${pct(gross)} (m\u00ednimo exigido: ${pct(minimum)}); promedio de la tabla, redondeado a tick, se informa por nivel abajo.</li><li>Neto tras polvo: ${pct(margin.net_after_dust_pct??edited.edge_after_dust_pct)} (estimaci\u00f3n pesimista, no medida).</li><li>Plazo ${esc(q('#sc-days').value)} d\u00edas</li>${openBody.params?'<li>Inter\u00e9s compuesto: reinvertir '+esc(q('#sc-compound-ratio').value)+'% de las ganancias, hasta '+esc(q('#sc-compound-cap').value)+'% de crecimiento. Requiere USDT libre y aplica a ciclos futuros.</li>':''}</ul>${plan.unready_coin_warning?`<p class="scanner-warning">${esc(plan.unready_coin_warning)}</p>`:''}${sigmaSurfacesLine(plan)}${priceGuard.allowed===false?(plan.testnet_price==null?`<p class="scanner-error">${esc(priceGuard.reason||'Apertura bloqueada por el libro de Testnet.')}</p>`:`<p class="scanner-error">Apertura bloqueada: el precio de Testnet (${esc(testnetPrice)}) est\u00e1 fuera del rango [${esc(plan.range_low)} \u2013 ${esc(plan.range_high)}]. Esta moneda cotiza distinto en Testnet; elige otra moneda o ajusta el rango.</p>`):''}${testnetUnavailable?`<p class="scanner-warning">${esc(priceGuard.reason||'No se pudo leer el precio de Testnet; la apertura puede fallar.')}</p>`:''}${margin.dust_warning?`<p class="scanner-warning">Informativo: ${esc(margin.dust_warning_message||'El polvo estimado es alto para esta celda; sube el capital por celda o reduce niveles. Es un tope pesimista, a\u00fan no medido en Testnet.')}</p>`:''}${window.gridSvg?.({recommended_floor:plan.range_low,recommended_ceiling:plan.range_high,current_price:plan.current_price,suggested_grids:Array.isArray(plan.levels)?plan.levels.length-1:plan.n_levels})||''}${cellGuidance(plan)}${levelTable(plan,edited)}<p>Estimaciones te\u00f3ricas; la ejecuci\u00f3n depende de precio, filtros y saldo Testnet.</p>${margin.allowed?'':`<p class="scanner-error">Apertura bloqueada: ${esc((margin.reasons||[]).join('; '))}. Margen tras comisiones: ${pct(gross)} (m\u00ednimo exigido: ${pct(minimum)}).</p>`}<p>Vista previa; a\u00fan no se ha abierto ning\u00fan grid.</p><p class="scanner-error" role="alert" hidden></p><div class="scanner-dialog-actions"><button type="button" class="button secondary" data-cancel>Cancelar</button><button type="button" class="button primary" data-confirm data-plan-blocked="${confirmDisabled?'true':'false'}" ${confirmDisabled||(planNeedsUnreadyAck&&!q('#sc-unready-ack')?.checked)?'disabled':''}>Confirmar apertura Testnet</button></div></div>`;
          dialog.hidden=false;dialogOpen=true;button.textContent='Vista previa';
          syncUnreadyAckButtons();
          dialog.querySelector('[data-cancel]').onclick=()=>{dialog.hidden=true;dialogOpen=false;syncUnreadyAckButtons();};
          dialog.querySelector('[data-confirm]').onclick=async event=>{
            const confirmButton=event.currentTarget;
            if(planNeedsUnreadyAck&&!q('#sc-unready-ack')?.checked){syncUnreadyAckButtons();return;}
            confirmButton.disabled=true;
            try {
              const opened=await call('/api/grids/open',openRequestBody({...openBody,dry_run:false,confirm:true}));
              if(opened.partial||['partial','PARTIAL','ERROR'].includes(opened.status)){const error=dialog.querySelector('[role=alert]');error.textContent='La operaci\u00f3n qued\u00f3 parcial; revisa el estado del grid.';error.hidden=false;confirmButton.disabled=false;return;}
              const id=opened.grid_id||opened.id;if(id==null)throw new Error('El API no confirm\u00f3 el identificador del grid.');
              dialog.innerHTML=`<div class="scanner-dialog-card"><h3>Grid abierto en Testnet</h3><p><a href="#grids/${esc(id)}">Ver detalle del grid ${esc(id)}</a></p><button type="button" class="button secondary" data-close>Cerrar</button></div>`;
              dialog.querySelector('[data-close]').onclick=()=>{dialog.hidden=true;dialogOpen=false;syncUnreadyAckButtons();refreshOpenWarning();};
            } catch(error) { const panel=dialog.querySelector('[role=alert]');panel.textContent=errorText(previewError(error,plan));panel.hidden=false;confirmButton.disabled=false; }
          };
        };
        const deadline=new Promise((_,reject)=>{timeoutHandle=setTimeout(()=>{timedOut=true;controller.abort();reject(new Error(`La vista previa super\u00f3 ${Math.ceil(PREVIEW_TIMEOUT_MS/1000)} s; int\u00e9ntalo de nuevo.`));},PREVIEW_TIMEOUT_MS);});
        await Promise.race([process(),deadline]);
      } catch(error) {
        if(timedOut)setError('#sc-open-error',new Error(`La vista previa super\u00f3 ${Math.ceil(PREVIEW_TIMEOUT_MS/1000)} s; int\u00e9ntalo de nuevo.`));
        else if(!isAbort(error)&&owner?.generation===requestGeneration)setError('#sc-open-error',previewError(error));
      } finally {
        if(timeoutHandle!=null)clearTimeout(timeoutHandle);
        clearInterval(ticker);
        if(owner?.generation===requestGeneration){progress.hidden=true;button.textContent='Vista previa';opening=false;button.disabled=dialogOpen;if(activeController===owner.controller)activeController=null;syncUnreadyAckButtons();}
      }
    });
    q('#sc-run').addEventListener('click',async event=>{const button=event.currentTarget;button.disabled=true;setError('#sc-error',null);q('#sc-results').innerHTML='<p class="muted">Escaneo en curso\u2026</p>';const symbols=[...q('#sc-results').ownerDocument.querySelectorAll('.sc-coin:checked')].map(el=>el.value);q('#sc-note').hidden=symbols.length>2;try{const data=await call('/api/grids/scan',{symbols,capital:q('#sc-capital').value,strategy:q('#sc-strategy').value});const rows=data.results||data; q('#sc-note').hidden=rows.length>2; q('#sc-results').innerHTML=rows.length?`<table class="scanner-table"><thead><tr><th>Moneda</th><th>Elegible</th><th>Puntaje</th><th>Margen bruto</th><th>Tras polvo estimado</th><th>Filtros y avisos</th></tr></thead><tbody>${rows.map((row,index)=>{const structure=row.suggested_structure||{};const fee=row.fee_pct;const gross=structure.spacing_pct==null||fee==null ? null:Number(structure.spacing_pct)-2*Number(fee);const failed=(row.hard_filters||[]).filter(f=>!f.passed).map(f=>f.reason);const warnings=[...new Set([...failed,...(row.warnings||[]),...(row.edge_warning?[row.edge_warning]:[]),...(row.warning?[row.warning]:[])])];return `<tr><td>${esc(row.symbol)}<button type="button" class="button secondary sc-use-symbol" data-symbol="${esc(row.symbol)}" data-row-index="${index}">Usar</button></td><td>${row.eligible?'S\u00ed':'No'}</td><td>${row.score==null?'\u2014':Number(row.score).toFixed(3)}<small>puntaje descriptivo; no mide rentabilidad ni predice</small></td><td>${pct(gross)}</td><td>${pct(structure.net_edge_pct_per_cycle)}</td><td>${esc(warnings.join('; ')||'Sin filtros fallidos ni avisos')}</td></tr><tr><td colspan="6"><details><summary>Componentes y procedencia</summary><ul>${(row.components||[]).map(c=>`<li>${esc(c.name)}: valor ${esc(c.value)}, peso ${esc(c.weight)}, contribuci\u00f3n ${esc(c.contribution)}</li>`).join('')}</ul></details></td></tr>`;}).join('')}</tbody></table>`:'<p class="muted">Sin resultados para las monedas seleccionadas.</p>';explainScanRows(q('#sc-results'),rows);q('#sc-results').querySelectorAll('.sc-use-symbol').forEach(button=>button.addEventListener('click',()=>{const symbol=button.dataset.symbol,row=rows[Number(button.dataset.rowIndex)]||{},structure=row.suggested_structure||{};if([...q('#sc-symbol').options].some(o=>o.value===symbol)){q('#sc-symbol').value=symbol;if(structure.range_low!=null)setNumericField('#sc-low',structure.range_low);if(structure.range_high!=null)setNumericField('#sc-high',structure.range_high);q('#sc-levels').value=structure.n_levels??'';if(structure.spacing_pct!=null)setNumericField('#sc-spacing',structure.spacing_pct,true);q('#sc-margin-target').value=serverMinimumMargin==null?'':serverMinimumMargin.toFixed(2);marginTargetTouched=false;updateMarginHint();q('#sc-target-pct').value=row.target_pct??'';q('#sc-target-usdt').value=row.target_usdt??'';refreshOpenWarning();loadVariants();const create=q('#sc-preview-open');create.scrollIntoView({behavior:'smooth',block:'center'});create.focus();}}));}catch(error){q('#sc-results').innerHTML='';setError('#sc-error',error);}finally{button.disabled=false;}});
    renderUnreadyAck();refreshOpenWarning();loadVariants();
    let advisorDraft=null;try{advisorDraft=JSON.parse(sessionStorage.getItem('asple-advisor-open')||'null');sessionStorage.removeItem('asple-advisor-open');}catch(_){}if(advisorDraft){if(advisorDraft.strategy==null)q('#sc-create-strategy').value='smart';for(const [key,selector] of Object.entries({symbol:'#sc-symbol',capital:'#sc-create-capital',strategy:'#sc-create-strategy',range_low:'#sc-low',range_high:'#sc-high',n_levels:'#sc-levels',spacing_pct:'#sc-spacing',margin_target_pct:'#sc-margin-target'})){if(advisorDraft[key]!=null){if(key==='range_low'||key==='range_high')setNumericField(selector,advisorDraft[key]);else if(key==='spacing_pct')setNumericField(selector,advisorDraft[key],true);else q(selector).value=advisorDraft[key];if(key==='margin_target_pct')marginTargetTouched=true;}}schedulePreview('linked');}
  }
  window.loadScannerScreen=async function(){const node=root();if(!node||node.dataset.loaded==='true')return;node.dataset.loaded='true';node.innerHTML='<p class="muted">Cargando monedas registradas\u2026</p>';try{const coins=await call('/api/coins');render(node,Array.isArray(coins)?coins:[]);}catch(error){node.innerHTML=`<section class="card scanner-panel"><h1>Scanner</h1><p class="scanner-error" role="alert">No se pudo cargar el registro de monedas: ${esc(errorText(error))}</p></section>`;node.dataset.loaded='false';}};
})();
