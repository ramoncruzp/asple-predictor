from pathlib import Path
import json
import os
import shutil
import subprocess


ROOT = Path(__file__).resolve().parents[2]


def test_grid_controls_require_preview_then_explicit_confirmation_and_do_not_touch_health_chip():
    source = (ROOT / "frontend" / "grids.js").read_text(encoding="utf-8")
    assert "dry_run: true, confirm: false" in source
    assert "dry_run: false, confirm: true" in source
    assert "LIQUIDAR" in source
    assert "setOffline(" not in source
    assert "APP.apiHealthState" not in source
    for action in ("pause", "resume", "close", "adjust", "sweep-dust", "params", "compound", "disable-loans"):
        assert f'data-grid-action="{action}"' in source


def test_grid_controls_show_compound_editable_and_capital_note():
    source = (ROOT / "frontend" / "grids.js").read_text(encoding="utf-8")
    assert "Inter\\u00e9s compuesto" in source
    assert "El capital asignado no se puede cambiar mientras el grid est\\u00e1 abierto." in source
    assert "Capital y compuesto" not in source
    assert "Ci\u00e9rralo y abre uno nuevo" not in source

def test_profit_repository_preview_is_confirmed_only_after_dry_run_and_escapes_values():
    node = shutil.which("node")
    if not node:
        import pytest
        pytest.skip("Node.js no está instalado; no se ejecuta la prueba de grids.js")
    harness = r'''const fs=require('fs'),vm=require('vm'); const payloads=[]; let call=0;
const confirm={isConnected:true,disabled:false,focus(){}}; const cancel={focus(){}}; const error={textContent:''};
const modal={hidden:true,_html:'',setAttribute(){},querySelector(sel){if(sel==='[data-action-cancel]')return cancel;if(sel==='[data-action-confirm]')return this._html.includes('data-action-confirm')?confirm:null;if(sel==='.grid-action-error')return error;if(sel==='input,select,button')return cancel;if(sel==='[name="close-mode"]:checked')return {value:'profit_repository'};return {value:''}},set innerHTML(v){this._html=v},get innerHTML(){return this._html}};
const document={addEventListener(){},getElementById(id){return id==='grid-action-dialog'?modal:null},createElement(){return modal},body:{appendChild(){}},querySelectorAll(){return []}};
const window={location:{origin:'http://local',hash:'#grids/1'},API_BASE:'http://local',__ASPLE_GRID_TEST__:true,loadGridsScreen:async()=>{}};
const fetch=async(_url,options)=>{const body=JSON.parse(options.body);payloads.push(body);call++;const value=call===1?{plan:{action:'close',mode:'profit_repository',cells_to_sell:1,sell_gain_usdt:2,cells_to_repository:1,repo_unrealized_usdt:3}}:{status:'CLOSED',status_after:'CLOSED',outcome:'completed'};return {ok:true,status:200,json:async()=>value}};
const context={window,document,fetch,location:window.location,URL,setInterval,clearInterval,console}; vm.runInNewContext(fs.readFileSync(process.env.GRIDS_JS,'utf8'),context);
(async()=>{const ui=window.__ASPLE_GRID_TEST__;const form=ui.formFor('close',{});const shown=ui.planLines({action:'close',mode:'profit_repository',cells_to_sell:'<script>',sell_gain_usdt:2,cells_to_repository:1,repo_unrealized_usdt:3,estimated_fee_cells:2});const win=ui.planLines({action:'close',mode:'liquidate',net_result_usdt:4,cells_winning:2,cells_losing:0,estimated_commission_usdt:1});const loss=ui.planLines({action:'close',mode:'liquidate',net_result_usdt:-4,cells_winning:0,cells_losing:2,estimated_commission_usdt:1});
await ui.controlFlow('1','close',{id:'1'},{});await confirm.onclick();const afterPreview=payloads.length;await confirm.onclick();
process.stdout.write(JSON.stringify({form,shown,winLabel:win.includes('GANANCIA'),winClass:win.includes('pnl-positive'),lossLabel:loss.includes('P'+String.fromCharCode(201)+'RDIDA'),lossClass:loss.includes('pnl-negative'),payloads,afterPreview}));})().catch(e=>{process.stderr.write(String(e.stack||e));process.exitCode=1});'''
    env = dict(os.environ, GRIDS_JS=str(ROOT / "frontend/grids.js"))
    result = subprocess.run([node, "-e", harness], cwd=ROOT, env=env, capture_output=True,
                            text=True, encoding="utf-8", timeout=10)
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    offsets = [data["form"].index(f'value="{mode}"') for mode in
               ("liquidate", "profit_repository", "repository", "cancel")]
    assert offsets == sorted(offsets)
    assert "&lt;script&gt;" in data["shown"] and "mantiene" in data["shown"] and "precio" in data["shown"]
    assert "2 celdas con comisión estimada" in data["shown"]
    assert data["winLabel"] and data["winClass"]
    assert data["lossLabel"] and data["lossClass"]
    assert data["afterPreview"] == 1 and len(data["payloads"]) == 2
    assert data["payloads"][0]["dry_run"] is True and data["payloads"][0]["confirm"] is False
    assert data["payloads"][0]["mode"] == "profit_repository"
    assert data["payloads"][1]["dry_run"] is False and data["payloads"][1]["confirm"] is True
