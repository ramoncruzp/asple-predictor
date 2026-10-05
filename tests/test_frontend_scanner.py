from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
JS=(ROOT/"frontend/scanner.js").read_text(encoding="utf-8")
HTML=(ROOT/"frontend/index.html").read_text(encoding="utf-8")


def test_scanner_ui_keeps_errors_local_and_explains_score_and_data_source():
    assert "puntaje descriptivo; no mide rentabilidad ni predice" in JS
    assert "Binance p\\u00fablico, no Testnet" not in JS
    assert "setOffline(" not in JS and "APP.apiHealthState" not in JS
    assert "gridApiToken" in JS and "X-API-Token" in JS
    assert "esc(" in JS
    assert "Margen bruto" in JS and "Tras polvo estimado" in JS


def test_variants_editable_and_open_flow_is_dry_run_then_confirmed_testnet():
    assert "data-variant" in JS and "#sc-low" in JS and "#sc-high" in JS and "#sc-levels" in JS
    assert 'value="7"' in JS
    assert "dry_run:true" in JS
    assert "dry_run:false,confirm:true" in JS
    assert "#sc-testnet" in JS
    assert "setTimeout(()=>loadPreview(source),400)" not in JS
    assert "setTimeout(()=>{timer=null;loadPreview(source);},400)" in JS
    assert "previewBusy" in JS
    assert 'href="#scanner"' in HTML and 'id="screen-scanner"' in HTML
    assert 'scanner.js' in HTML and 'scanner.css' in HTML


def test_new_scanner_source_escapes_dynamic_labels_and_has_local_failure_panels():
    assert "function errorText" in JS and "setError" in JS
    assert "${esc(row.symbol)}" in JS
    assert "${esc(errorText(error))}" in JS
    assert "${esc(c.name)}" in JS and "${esc(c.value)}" in JS
    assert 'role="alert"' in JS


import json
import os
import shutil
import subprocess
from decimal import Decimal
from types import SimpleNamespace

import pytest


NODE_HARNESS = r"""
const fs = require('fs');
const vm = require('vm');
const source = fs.readFileSync(process.env.SCANNER_JS, 'utf8');
const scenario = process.env.SCANNER_SCENARIO;
const calls = [];
let fakeNow=1000;
let releasePreview, releaseStructure;
const registrySymbol = 'XRPUSDT';
class Element {
  constructor(id, doc) { this.id=id; this.doc=doc; this.ownerDocument=doc; this.listeners={}; this.value=''; this.checked=true; this.disabled=false; this.hidden=false; this.textContent=''; this.dataset={}; this.options=[{value:registrySymbol}]; this.children={}; this._html=''; }
  set innerHTML(value) { this._html=String(value); if(this.id==='sc-dialog') this.children={}; }
  get innerHTML() { return this._html; }
  addEventListener(type,fn) { this.listeners[type]=fn; }
  async click() { if(this.disabled) return; const fn=this.onclick||this.listeners.click; if(fn) return await fn({currentTarget:this,target:this}); }
  input() { const fn=this.listeners.input; if(fn)fn({currentTarget:this,target:this}); }
  focus() {}
  scrollIntoView() {}
  querySelector(selector) {
    if(!this.children[selector]) this.children[selector]=new Element(selector,this.doc);
    return this.children[selector];
  }
  querySelectorAll(selector) {
    if(selector==='[data-variant]'||selector==='.scanner-variant'||selector==='.sc-use-symbol') return [];
    return [];
  }
}
const document={elements:{},querySelector(selector){return this.elements[selector]||(this.elements[selector]=new Element(selector,this));},querySelectorAll(selector){return selector==='.sc-coin:checked'?[{value:registrySymbol,checked:true}]:[];}};
const root=document.querySelector('#scanner-root');
root.querySelector=(selector)=>document.querySelector(selector);
root.querySelectorAll=(selector)=>[];
for(const id of ['#sc-capital','#sc-strategy','#sc-create-capital','#sc-create-strategy','#sc-symbol','#sc-low','#sc-high','#sc-levels','#sc-spacing','#sc-margin-target','#sc-target-pct','#sc-target-usdt','#sc-target-basis','#sc-days','#sc-compound-enabled','#sc-compound-fields','#sc-compound-ratio','#sc-compound-cap','#sc-testnet','#sc-run','#sc-results','#sc-note','#sc-error','#sc-variants','#sc-open-grid-warning','#sc-edited','#sc-preview-open','#sc-open-error','#sc-dialog','#sc-preview-progress']) document.querySelector(id);
document.querySelector('#sc-dialog').hidden=true;document.querySelector('#sc-preview-progress').hidden=true;
document.querySelector('#sc-capital').value='100';
document.querySelector('#sc-strategy').value='simple';
document.querySelector('#sc-create-capital').value='100';
document.querySelector('#sc-create-strategy').value='simple';
document.querySelector('#sc-symbol').value=registrySymbol;
document.querySelector('#sc-low').value='1';
document.querySelector('#sc-high').value='2';
document.querySelector('#sc-levels').value='6';
document.querySelector('#sc-spacing').value='1';
document.querySelector('#sc-margin-target').value='';
if(scenario==='open_contract') document.querySelector('#sc-margin-target').value='0.7';
document.querySelector('#sc-target-pct').value='';
document.querySelector('#sc-target-usdt').value='';
document.querySelector('#sc-target-basis').value='cash';
document.querySelector('#sc-days').value='7';
document.querySelector('#sc-testnet').checked=true;
const window={location:{protocol:'http:',origin:'http://local'},gridApiToken:'test-token'};
const hasDraft=['advisor_draft','reuse_signature','changed_signature','formatted_draft','formatted_edit'].includes(scenario);
const preciseDraft=['formatted_draft','formatted_edit','reuse_signature','changed_signature'].includes(scenario);
const sessionStorage={getItem:()=>hasDraft?JSON.stringify({symbol:'XRPUSDT',capital:'100',strategy:'simple',range_low:preciseDraft?'0.0000028023809523809':'1.1',range_high:preciseDraft?'0.0000043360714285714':'1.9',n_levels:6,spacing_pct:preciseDraft?1.7771616178336926:.42,margin_target_pct:.7}):null,removeItem:()=>{}};
const fetch=async(url,options={})=>{
  const path=new URL(url).pathname, method=options.method||'GET', body=options.body?JSON.parse(options.body):null;
  calls.push({path,method,body,signal:options.signal});
  if(path==='/api/coins') return response(200,[{symbol:registrySymbol}]);
  if(path==='/api/grids'&&method==='GET') return response(200,{grids:[]});
  if(path==='/api/grids/structure-preview'&&scenario==='advisor_draft'&&body?.range_low) return await new Promise(resolve=>{releaseStructure=()=>resolve(response(200,{minimum_cell_usdt:'5.5',fee_pct:.1,variants:{balanced:{feasible:true},wide:{feasible:true}},edited:{feasible:true,spacing_pct:.42,edge_gross_pct:.8,dust_estimate_pct:.1,edge_after_dust_pct:.7,n_levels:6}}));});
  if(path==='/api/grids/structure-preview'&&scenario==='timeout_abort'&&body?.range_low) return await new Promise(resolve=>{releaseStructure=()=>resolve(response(200,{minimum_cell_usdt:'5.5',fee_pct:.1,variants:{balanced:{feasible:true},wide:{feasible:true}},edited:{feasible:true,range_low:body.range_low,range_high:body.range_high,n_levels:6,spacing_pct:1.7771616178336926,cell_usdt:16.67,minimum_cell_usdt:'5.5',edge_gross_pct:1.577,dust_estimate_pct:.1,edge_after_dust_pct:1.477}}));});
  if(path==='/api/grids/structure-preview'&&scenario==='delayed_preview'&&body?.range_low) return await new Promise(resolve=>{releaseStructure=()=>resolve(response(200,{minimum_cell_usdt:'5.5',fee_pct:.1,variants:{balanced:{feasible:true},wide:{feasible:true}},edited:{feasible:true,range_low:body.range_low,range_high:body.range_high,n_levels:6,spacing_pct:1,cell_usdt:16.67,minimum_cell_usdt:'5.5',edge_gross_pct:.8,dust_estimate_pct:.1,edge_after_dust_pct:.7}}));});
  if(path==='/api/grids/structure-preview') {
    const infeasible=scenario==='infeasible'&&body?.range_low!=null,small=scenario==='small_values',zero=scenario==='exact_zero';
    const variant={feasible:!infeasible,range_low:'0.0000028023809523809',range_high:'0.0000043360714285714',n_levels:20,spacing_pct:small?1.220:1.7771616178336926,cell_usdt:zero?0:small?0.004:5.5,minimum_cell_usdt:'5.5',min_cell_warning:null,dust_min_cell_usdt:zero?0:small?0.004:14.91,dust_target_pct:.1,dust_estimate_pct:zero?0:small?0.0004:0,edge_gross_pct:1.577,edge_after_dust_pct:zero?0:small?0.0004:1.220,reasons:infeasible?['margen insuficiente','Celda inferior al minimo']:[]};
    const edited={...variant,feasible:!infeasible,range_low:body?.range_low||variant.range_low,range_high:body?.range_high||variant.range_high,n_levels:infeasible?20:6,cell_usdt:zero?0:infeasible?5:small?0.004:16.67,spacing_pct:small?1.220:1,edge_gross_pct:1.577,dust_estimate_pct:zero?0:small?0.0004:.2,edge_after_dust_pct:zero?0:small?0.0004:1.220,reasons:infeasible?['margen insuficiente','Celda inferior al minimo']:[]};
    return response(200,{minimum_margin_after_fees_pct:scenario==='server_min'?.9:.7,minimum_cell_usdt:'5.5',min_cell_warning:null,dust_target_pct:.1,dust_min_cell_usdt:zero?0:small?0.004:14.91,fee_pct:.1,variants:{balanced:variant,wide:variant},edited});
  }
  if(path==='/api/grids/scan') {
    const row={symbol:'<script>alert(1)</script>',eligible:false,score:null,hard_filters:[{passed:false,reason:'No elegible'}],components:[],reasons:['No elegible'],suggested_structure:{spacing_pct:1,net_edge_pct_per_cycle:.2},warnings:[],warning:'public data'};
    if(scenario!=='scan_no_fee') row.fee_pct=.25;
    return response(200,{results:[row]});
  }
  if(path==='/api/grids/open'&&body.dry_run===true&&scenario==='out_of_range') return response(422,{detail:'current price must be strictly inside range'});
  if(path==='/api/grids/open'&&body.dry_run===true&&scenario==='validation_error') return response(422,{detail:[{loc:['body','margin_target_pct'],msg:'Extra inputs are not permitted',type:'extra_forbidden'}]});
  if(path==='/api/grids/open'&&body.dry_run===true&&scenario==='preview_error') return response(503,{detail:'deadline expired'});
  if(path==='/api/grids/open'&&body.dry_run===true&&scenario==='delayed_preview') return await new Promise(resolve=>{releasePreview=()=>resolve(response(200,{dry_run:true,symbol:registrySymbol,strategy:'simple',capital:'100',range_low:'1',range_high:'2',n_levels:6,cell_usdt:'16.67',current_price:'1.5',cells:[],margin_guard:{allowed:true,actual_pct:.7,edge_gross_pct:1.577,minimum_pct:.7}}));});
  if(path==='/api/grids/open'&&body.dry_run===true) return response(200,{dry_run:true,symbol:registrySymbol,strategy:'simple',capital:'100',range_low:'1',range_high:'2',n_levels:6,cell_usdt:'16.67',current_price:'1.5',cells:[],margin_guard:{allowed:true,actual_pct:.7,edge_gross_pct:1.577,minimum_pct:.7}});
  if(path==='/api/grids/open'&&body.dry_run===false) {
    if(scenario==='partial') return response(200,{partial:true,status:'partial'});
    if(scenario==='error') return response(409,{detail:'conflict'});
    return response(200,{status:'ACTIVE',grid_id:71});
  }
  throw new Error('unexpected request '+method+' '+path);
};
function response(status,body){return {ok:status>=200&&status<300,status,json:async()=>body};}
const timers={setTimeout:(fn,delay)=>{if(hasDraft&&delay===400)fn();if(scenario==='timeout_abort'&&delay===30000){fakeNow=31000;fn();}return 1;},clearTimeout:()=>{}};
class FakeDate extends Date { static now(){return fakeNow;} }
vm.runInNewContext(source,{window,document,fetch,console,URL,sessionStorage,Date:FakeDate,AbortController,DOMException,clearTimeout:timers.clearTimeout,setTimeout:timers.setTimeout,setInterval:(fn)=>{if(scenario==='delayed_preview'||scenario==='timeout_abort')fakeNow=8000;fn();return 1;},clearInterval:()=>{}});
const flush=()=>new Promise(resolve=>setImmediate(resolve));
(async()=>{
  await window.loadScannerScreen(); await flush();
  const q=s=>document.querySelector(s);
  let output={calls};
  if(scenario==='advisor_draft') { await flush(); output={spacingBefore:q('#sc-spacing').value,range:q('#sc-low').value,levels:q('#sc-levels').value,calls}; releaseStructure(); await flush(); } else if(['formatted_draft','formatted_edit','reuse_signature','changed_signature'].includes(scenario)) { await flush();const before=calls.filter(c=>c.path==='/api/grids/structure-preview').length;if(scenario==='formatted_edit'||scenario==='changed_signature'){q('#sc-low').value='0.000003123456789';q('#sc-low').input();await flush();}if(scenario!=='formatted_draft')await q('#sc-preview-open').click();output={low:q('#sc-low').value,high:q('#sc-high').value,spacing:q('#sc-spacing').value,structureCallsBefore:before,structureCallsAfter:calls.filter(c=>c.path==='/api/grids/structure-preview').length,openCalls:calls.filter(c=>c.path==='/api/grids/open').length,bodies:calls.filter(c=>c.path==='/api/grids/structure-preview').map(c=>c.body),hint:q('#sc-margin-hint').textContent,edited:q('#sc-edited').innerHTML,root:root.innerHTML}; } else if(scenario==='server_min'){const initial=calls.find(c=>c.path==='/api/grids/structure-preview');q('#sc-margin-target').value='.5';q('#sc-margin-target').input();output={initialBody:initial.body,field:q('#sc-margin-target').value,hint:q('#sc-margin-hint').textContent};} else if(scenario==='small_values'||scenario==='exact_zero'){output={variants:q('#sc-variants').innerHTML,edited:q('#sc-edited').innerHTML,root:root.innerHTML};} else if(scenario==='infeasible'){await q('#sc-preview-open').click();output={error:q('#sc-open-error').textContent,openCalls:calls.filter(c=>c.path==='/api/grids/open').length};} else if(scenario==='delayed_preview') { const pending=q('#sc-preview-open').click(); await flush(); output.progress={text:q('#sc-preview-progress').textContent,hidden:q('#sc-preview-progress').hidden,buttonText:q('#sc-preview-open').textContent,disabled:q('#sc-preview-open').disabled}; releaseStructure();await flush();releasePreview(); await pending; output.after={hidden:q('#sc-preview-progress').hidden,buttonText:q('#sc-preview-open').textContent,disabled:q('#sc-preview-open').disabled}; } else if(scenario==='timeout_abort'){const pending=q('#sc-preview-open').click();await flush();const request=calls.find(c=>c.path==='/api/grids/structure-preview'&&c.body?.range_low);output.timeoutText=q('#sc-open-error').textContent;output.aborted=request?.signal?.aborted;output.dialogHidden=q('#sc-dialog').hidden;releaseStructure();await pending;await flush();output.dialogStillHidden=q('#sc-dialog').hidden;output.openCalls=calls.filter(c=>c.path==='/api/grids/open').length;} else if(scenario==='preview_error') { await q('#sc-preview-open').click(); output={error:q('#sc-open-error').textContent,hidden:q('#sc-preview-progress').hidden,disabled:q('#sc-preview-open').disabled}; } else if(scenario==='out_of_range'||scenario==='validation_error') {await q('#sc-preview-open').click();output={error:q('#sc-open-error').textContent,disabled:q('#sc-preview-open').disabled};} else if(scenario==='scan_fee'||scenario==='scan_no_fee') {
    await q('#sc-run').click();
    output.html=q('#sc-results').innerHTML;
  } else {
    await q('#sc-preview-open').click();
    const dialog=q('#sc-dialog'), preview=q('#sc-preview-open');
    if(scenario==='success_reopen') {
      await dialog.querySelector('[data-confirm]').click();
      const successHtml=dialog.innerHTML;
      const close=dialog.querySelector('[data-close]');
      const closeText=successHtml;
      await close.click(); await flush();
      const afterClose={dialogHidden:dialog.hidden,previewDisabled:preview.disabled};
      await preview.click();
      output={calls,successHtml,afterClose,secondPreviewOpened:dialog.hidden===false};
    } else {
      await dialog.querySelector('[data-confirm]').click();
      output={calls,dialogHtml:dialog.innerHTML,dialogHidden:dialog.hidden,previewDisabled:preview.disabled,confirmDisabled:dialog.querySelector('[data-confirm]').disabled,alertText:dialog.querySelector('[role=alert]').textContent};
      await dialog.querySelector('[data-cancel]').click();
      output.afterCancel={dialogHidden:dialog.hidden,previewDisabled:preview.disabled};
    }
  }
  process.stdout.write(JSON.stringify(output));
})().catch(error=>{console.error(error.stack);process.exitCode=1;});
"""


def run_scanner_node(tmp_path, scenario):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js no est? instalado; se omite la prueba conductual del Scanner")
    env = dict(os.environ, SCANNER_JS=str(ROOT / "frontend/scanner.js"), SCANNER_SCENARIO=scenario)
    result = subprocess.run([node, "-e", NODE_HARNESS], cwd=ROOT, env=env,
                            capture_output=True, text=True, encoding="utf-8", timeout=15)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_node_open_order_success_close_allows_second_open(tmp_path):
    result = run_scanner_node(tmp_path, "success_reopen")
    opens = [call["body"] for call in result["calls"] if call["path"] == "/api/grids/open"]
    assert opens[0]["dry_run"] is True
    assert opens[1]["dry_run"] is False and opens[1]["confirm"] is True
    assert opens[1]["range_low"] == "1" and opens[1]["range_high"] == "2" and opens[1]["n_levels"] == 6
    assert result["afterClose"] == {"dialogHidden": True, "previewDisabled": False}
    assert result["secondPreviewOpened"] is True
    confirm_index=next(i for i,c in enumerate(result["calls"]) if c["path"]=="/api/grids/open" and c["body"].get("dry_run") is False)
    assert any(i>confirm_index and c["path"]=="/api/grids" and c["method"]=="GET" for i,c in enumerate(result["calls"]))
    assert sum(body.get("dry_run") is True for body in opens) == 2
    assert "Cerrar" in result["successHtml"]


@pytest.mark.parametrize("scenario", ["partial", "error"])
def test_node_partial_or_error_never_reports_open_and_cancel_resets_state(tmp_path, scenario):
    result = run_scanner_node(tmp_path, scenario)
    assert "Grid abierto" not in result["dialogHtml"]
    assert "Margen tras comisiones: 1,577 % (mínimo exigido: 0,700 %)" in result["dialogHtml"]
    assert result["dialogHidden"] is False and result["previewDisabled"] is True
    assert result["confirmDisabled"] is False
    assert result["afterCancel"] == {"dialogHidden": True, "previewDisabled": False}
    if scenario == "partial":
        assert "parcial" in result["alertText"]
    else:
        assert "409" in result["alertText"]


@pytest.mark.parametrize("scenario,expected", [("scan_fee", "0,500 %"), ("scan_no_fee", "no disponible")])
def test_node_ineligible_scan_row_uses_fee_or_shows_unavailable_and_escapes_html(tmp_path, scenario, expected):
    result = run_scanner_node(tmp_path, scenario)
    assert expected in result["html"]
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in result["html"]
    assert "<script>alert(1)</script>" not in result["html"]


def test_scan_route_adds_the_service_fee_on_ineligible_rows_without_changing_score():
    from api.routes import grids
    class Service:
        settings = SimpleNamespace(scanner_fee_pct=.25)
        def scan(self, **kwargs):
            return {"results":[{"symbol":"XRPUSDT","eligible":False,"score":None,"reasons":["spread"]}]}
    request=SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(
        settings=SimpleNamespace(grid_api_token="",scanner_fee_pct=.1),grid_scan_service=Service())),
        client=SimpleNamespace(host="127.0.0.1"),headers={})
    result=grids.scan(request,grids.ScanRequest(capital=Decimal("100")))
    row=result["results"][0]
    assert row["fee_pct"]==.25 and row["eligible"] is False and row["score"] is None


def test_node_preview_shows_progress_while_waiting_and_clears_after_response(tmp_path):
    result=run_scanner_node(tmp_path,"delayed_preview")
    assert result["progress"]["text"].startswith("Calculando vista previa")
    assert result["progress"]["hidden"] is False
    assert result["progress"]["buttonText"].startswith("Calculando") and result["progress"]["disabled"] is True
    assert result["after"]=={"hidden":True,"buttonText":"Vista previa","disabled":True}

def test_node_preview_error_clears_progress_and_reenables_button(tmp_path):
    result=run_scanner_node(tmp_path,"preview_error")
    assert "503" in result["error"] and result["hidden"] is True and result["disabled"] is False


def test_node_advisor_draft_carries_spacing_and_runs_linked_recalculation(tmp_path):
    result=run_scanner_node(tmp_path,"advisor_draft")
    assert result["range"]=="1.1" and result["levels"]==6
    assert float(result["spacingBefore"])==pytest.approx(.42)
    assert any(call["path"]=="/api/grids/structure-preview" and call["body"].get("range_low")=="1.1" for call in result["calls"])

def test_node_out_of_range_preview_translates_api_error_to_spanish(tmp_path):
    result=run_scanner_node(tmp_path,"out_of_range")
    assert "fuera del rango" in result["error"]
    assert "ajusta el rango" in result["error"]
    assert "strictly inside" not in result["error"]
    assert result["disabled"] is False


@pytest.mark.parametrize("scenario", ["formatted_draft", "formatted_edit"])
def test_node_fields_are_compact_but_keep_exact_advisor_values_until_edited(tmp_path, scenario):
    result=run_scanner_node(tmp_path,scenario)
    if scenario == "formatted_draft":
        assert len(result["low"]) <= 14 and "e" not in result["low"].lower()
    else:
        assert result["low"] == "0.000003123456789"
    assert len(result["high"]) <= 14 and "e" not in result["high"].lower()
    assert len(result["spacing"].split(".")[-1]) <= 3
    body=[body for body in result["bodies"] if body and body.get("range_low")][-1]
    assert body["range_high"] == ("1.9" if scenario=="changed_signature" else "0.0000043360714285714")
    assert body["range_low"] == ("0.000003123456789" if scenario in ("formatted_edit", "changed_signature") else "0.0000028023809523809")


def test_node_small_positive_values_do_not_round_to_zero_but_exact_zero_does(tmp_path):
    result=run_scanner_node(tmp_path,"small_values")
    assert "< $0,01" in result["variants"]
    assert "< 0,001 %" in result["variants"]
    assert "placeholder=\"opcional\"" in result["root"]
    zero=run_scanner_node(tmp_path,"exact_zero")
    assert "$0,00" in zero["variants"] and "0,000 %" in zero["variants"]


def test_node_infeasible_preview_shows_numeric_cell_reason_and_never_calls_open(tmp_path):
    result=run_scanner_node(tmp_path,"infeasible")
    assert "Celda $5,00 < m\u00ednimo $5,50" in result["error"]
    assert "Reduce a 18 niveles o sube el capital a $110,00" in result["error"]
    assert "margen insuficiente" in result["error"]
    assert result["openCalls"] == 0


def test_node_preview_reuses_matching_signature_and_waits_for_linked_recalculation(tmp_path):
    result=run_scanner_node(tmp_path,"reuse_signature")
    assert result["structureCallsBefore"] == result["structureCallsAfter"]
    assert result["openCalls"] == 1
    assert "1,577 %" in result["edited"] and "1,220 %" in result["edited"]
    assert "M\u00ednimo del servidor: 0,700 %" in result["hint"]


def test_node_new_signature_runs_one_structure_request_before_open(tmp_path):
    result=run_scanner_node(tmp_path,"changed_signature")
    assert result["structureCallsAfter"] == result["structureCallsBefore"] + 1
    body=next(body for body in result["bodies"] if body and body.get("range_low")=="0.000003123456789")
    assert body["range_high"] == "0.0000043360714285714"
    assert result["openCalls"] == 1


def test_node_preview_progress_has_no_promised_deadline_and_continues_after_six_seconds(tmp_path):
    result=run_scanner_node(tmp_path,"delayed_preview")
    assert "hasta" not in result["progress"]["text"].lower()
    assert result["progress"]["text"].endswith("7 s")


def test_node_preview_deadline_aborts_request_and_discards_late_structure_response(tmp_path):
    result=run_scanner_node(tmp_path,"timeout_abort")
    assert "super\u00f3 30 s" in result["timeoutText"]
    assert result["aborted"] is True
    assert result["dialogHidden"] and result["dialogStillHidden"]
    assert result["openCalls"] == 0


def test_node_margin_target_uses_server_default_and_warns_when_lowered(tmp_path):
    result=run_scanner_node(tmp_path,"server_min")
    assert "margin_target_pct" not in result["initialBody"]
    assert result["field"]==".5"
    assert "Al abrir, el servidor exige al menos 0,900 % (GRID_MIN_MARGIN_AFTER_FEES_PCT)." in result["hint"]


def test_node_open_body_is_whitelisted_even_when_margin_target_is_filled(tmp_path):
    result=run_scanner_node(tmp_path,"open_contract")
    open_bodies=[call["body"] for call in result["calls"] if call["path"]=="/api/grids/open"]
    allowed={"symbol","strategy","capital","range_low","range_high","n_levels",
        "target_pct","target_usdt","target_basis","max_days","params","dry_run",
        "confirm","from_scan"}
    assert len(open_bodies)==2
    assert all(set(body)<=allowed for body in open_bodies)
    assert all("margin_target_pct" not in body and "spacing_pct" not in body for body in open_bodies)
    structure_bodies=[call["body"] for call in result["calls"]
        if call["path"]=="/api/grids/structure-preview"]
    assert any(body.get("margin_target_pct")==.7 for body in structure_bodies)
    assert open_bodies[0]["dry_run"] is True
    assert open_bodies[1]["dry_run"] is False and open_bodies[1]["confirm"] is True


def test_node_array_api_validation_details_are_human_readable(tmp_path):
    result=run_scanner_node(tmp_path,"validation_error")
    assert "422: margin_target_pct: Extra inputs are not permitted" in result["error"]
    assert "[object Object]" not in result["error"]
