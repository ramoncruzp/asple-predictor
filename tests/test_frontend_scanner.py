from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
JS=(ROOT/"frontend/scanner.js").read_text(encoding="utf-8")
HTML=(ROOT/"frontend/index.html").read_text(encoding="utf-8")


def test_scanner_ui_keeps_errors_local_and_explains_score_and_data_source():
    assert "puntaje descriptivo; no mide rentabilidad ni predice" in JS
    assert "Binance p\\u00fablico, no Testnet" in JS
    assert "setOffline(" not in JS and "APP.apiHealthState" not in JS
    assert "gridApiToken" in JS and "X-API-Token" in JS
    assert "esc(" in JS
    assert "Margen bruto" in JS and "Tras polvo estimado" in JS


def test_variants_editable_and_open_flow_is_dry_run_then_confirmed_testnet():
    assert "data-variant" in JS and "#sc-low" in JS and "#sc-high" in JS and "#sc-levels" in JS
    assert 'value="7"' in JS
    assert "b.dry_run=true" in JS
    assert "dry_run:false,confirm:true" in JS
    assert "#sc-testnet" in JS
    assert "setTimeout(loadPreview,400)" in JS
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
const registrySymbol = 'XRPUSDT';
class Element {
  constructor(id, doc) { this.id=id; this.doc=doc; this.ownerDocument=doc; this.listeners={}; this.value=''; this.checked=true; this.disabled=false; this.hidden=false; this.textContent=''; this.dataset={}; this.options=[{value:registrySymbol}]; this.children={}; this._html=''; }
  set innerHTML(value) { this._html=String(value); if(this.id==='sc-dialog') this.children={}; }
  get innerHTML() { return this._html; }
  addEventListener(type,fn) { this.listeners[type]=fn; }
  async click() { if(this.disabled) return; const fn=this.onclick||this.listeners.click; if(fn) return await fn({currentTarget:this,target:this}); }
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
for(const id of ['#sc-capital','#sc-strategy','#sc-create-capital','#sc-create-strategy','#sc-symbol','#sc-low','#sc-high','#sc-levels','#sc-target-pct','#sc-target-usdt','#sc-target-basis','#sc-days','#sc-testnet','#sc-run','#sc-results','#sc-note','#sc-error','#sc-variants','#sc-open-grid-warning','#sc-edited','#sc-preview-open','#sc-open-error','#sc-dialog']) document.querySelector(id);
document.querySelector('#sc-capital').value='100';
document.querySelector('#sc-strategy').value='simple';
document.querySelector('#sc-create-capital').value='100';
document.querySelector('#sc-create-strategy').value='simple';
document.querySelector('#sc-symbol').value=registrySymbol;
document.querySelector('#sc-low').value='1';
document.querySelector('#sc-high').value='2';
document.querySelector('#sc-levels').value='6';
document.querySelector('#sc-target-pct').value='';
document.querySelector('#sc-target-usdt').value='';
document.querySelector('#sc-target-basis').value='cash';
document.querySelector('#sc-days').value='7';
document.querySelector('#sc-testnet').checked=true;
const window={location:{protocol:'http:',origin:'http://local'},gridApiToken:'test-token'};
const fetch=async(url,options={})=>{
  const path=new URL(url).pathname, method=options.method||'GET', body=options.body?JSON.parse(options.body):null;
  calls.push({path,method,body});
  if(path==='/api/coins') return response(200,[{symbol:registrySymbol}]);
  if(path==='/api/grids'&&method==='GET') return response(200,{grids:[]});
  if(path==='/api/grids/structure-preview') return response(200,{variants:{dense:{feasible:true},balanced:{feasible:true},wide:{feasible:true}},edited:{feasible:true,edge_gross_pct:.5,edge_after_dust_pct:.2}});
  if(path==='/api/grids/scan') {
    const row={symbol:'<script>alert(1)</script>',eligible:false,score:null,hard_filters:[{passed:false,reason:'No elegible'}],components:[],reasons:['No elegible'],suggested_structure:{spacing_pct:1,net_edge_pct_per_cycle:.2},warnings:[],warning:'public data'};
    if(scenario!=='scan_no_fee') row.fee_pct=.25;
    return response(200,{results:[row]});
  }
  if(path==='/api/grids/open'&&body.dry_run===true) return response(200,{dry_run:true,symbol:registrySymbol,strategy:'simple',capital:'100',range_low:'1',range_high:'2',n_levels:6,cell_usdt:'16.67'});
  if(path==='/api/grids/open'&&body.dry_run===false) {
    if(scenario==='partial') return response(200,{partial:true,status:'partial'});
    if(scenario==='error') return response(409,{detail:'conflict'});
    return response(200,{status:'ACTIVE',grid_id:71});
  }
  throw new Error('unexpected request '+method+' '+path);
};
function response(status,body){return {ok:status>=200&&status<300,status,json:async()=>body};}
const timers={setTimeout:(fn)=>{return 1;},clearTimeout:()=>{}};
vm.runInNewContext(source,{window,document,fetch,console,URL,clearTimeout:timers.clearTimeout,setTimeout:timers.setTimeout});
const flush=()=>new Promise(resolve=>setImmediate(resolve));
(async()=>{
  await window.loadScannerScreen(); await flush();
  const q=s=>document.querySelector(s);
  let output={calls};
  if(scenario==='scan_fee'||scenario==='scan_no_fee') {
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
                            capture_output=True, text=True, timeout=15)
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
    assert result["dialogHidden"] is False and result["previewDisabled"] is True
    assert result["confirmDisabled"] is False
    assert result["afterCancel"] == {"dialogHidden": True, "previewDisabled": False}
    if scenario == "partial":
        assert "parcial" in result["alertText"]
    else:
        assert "409" in result["alertText"]


@pytest.mark.parametrize("scenario,expected", [("scan_fee", "0.500%"), ("scan_no_fee", "no disponible")])
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
