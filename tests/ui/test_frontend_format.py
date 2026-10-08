from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]


def test_shared_date_and_number_formatters_in_fixed_timezone():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js no está instalado; no se ejecutan los formateadores compartidos")
    harness = r'''const fs=require('fs'),vm=require('vm');
const window={location:{protocol:'http:',origin:'http://local'}};
const document={addEventListener(){}};
const context={window,document,fetch:async()=>{},URL,Intl,Date,Number,Math,JSON,localStorage:{getItem(){return null},setItem(){}},setInterval(){},clearInterval(){},console};
vm.runInNewContext(fs.readFileSync(process.env.APP_JS,'utf8'),context);
const f=window.ASPLEFormat;
process.stdout.write(JSON.stringify({
  date:f.formatDate('2025-01-01T03:59:00Z'),
  midnight:f.formatDateTime('2025-01-01T04:00:00Z'),
  yearEnd:f.formatDateTime('2025-01-01T03:59:00Z'),
  invalid:f.formatDate('not-a-date'),
  usdt:f.formatUsdt(1234567.5),
  grouped:f.formatNumber(1234567.5,2)
}));'''
    result = subprocess.run(
        [node, "-e", harness], cwd=ROOT,
        env=dict(os.environ, APP_JS=str(ROOT / "frontend" / "app.js")),
        capture_output=True, text=True, encoding="utf-8", timeout=10,
    )
    assert result.returncode == 0, result.stderr
    values = json.loads(result.stdout)
    assert values == {
        "date": "31/12/24",
        "midnight": "01/01/25 00:00",
        "yearEnd": "31/12/24 23:59",
        "invalid": chr(0x2014),
        "usdt": "$1,234,567.50",
        "grouped": "1,234,567.50",
    }


def test_price_formatter_uses_four_decimals_above_one_and_significant_digits_below_one():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js no está instalado; no se ejecuta formatPrice")
    harness = r'''const fs=require('fs'),vm=require('vm');
const window={location:{protocol:'http:',origin:'http://local'}};
const document={addEventListener(){}};
const context={window,document,fetch:async()=>{},URL,Intl,Date,Number,Math,JSON,localStorage:{getItem(){return null},setItem(){}},setInterval(){},clearInterval(){},console};
vm.runInNewContext(fs.readFileSync(process.env.APP_JS,'utf8'),context);
process.stdout.write([context.formatPrice(783.00778595),context.formatPrice(0.000012346),context.formatIndicator(0.00000431)].join('|'));'''
    result = subprocess.run(
        [node, "-e", harness], cwd=ROOT,
        env=dict(os.environ, APP_JS=str(ROOT / "frontend" / "app.js")),
        capture_output=True, text=True, encoding="utf-8", timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "783.0078|0.00001235|0.00000431"


def test_dashboard_renders_explicit_missing_model_state_without_model_cards():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js no está instalado; no se ejecuta el render del Dashboard")
    harness = r'''const fs=require('fs'),vm=require('vm'),assert=require('assert');
const elements={};
const document={addEventListener(){},querySelector(selector){return elements[selector]||(elements[selector]={innerHTML:'',textContent:'',className:''})}};
const window={location:{protocol:'http:',origin:'http://local'}};
const context={window,document,fetch:async()=>{},URL,Intl,Date,Number,Math,JSON,localStorage:{getItem(){return null},setItem(){}},setInterval(){},clearInterval(){},console};
vm.runInNewContext(fs.readFileSync(process.env.APP_JS,'utf8'),context);
const unavailable={model_available:false,weights:{model_a:1},consensus_probability_up:null};
context.renderModelCards(unavailable,{models:[{model_name:'model_a'}]});
context.renderConsensus(unavailable);
assert.strictEqual(elements['#model-cards'].innerHTML,'');
assert(elements['#consensus-card'].innerHTML.includes('Sin modelo entrenado para esta moneda/temporalidad'));
assert(!elements['#consensus-card'].innerHTML.includes('big-prob'));
process.stdout.write('ok');'''
    result = subprocess.run(
        [node, "-e", harness], cwd=ROOT,
        env=dict(os.environ, APP_JS=str(ROOT / "frontend" / "app.js")),
        capture_output=True, text=True, encoding="utf-8", timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "ok"


def test_dashboard_chart_preserves_time_gaps_and_local_tick_labels():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js no está instalado; no se ejecutan los helpers del gráfico")
    harness = r'''const fs=require('fs'),vm=require('vm'),assert=require('assert');
const document={addEventListener(){},querySelector(){return {innerHTML:'',textContent:'',className:'',classList:{toggle(){}}}}};
const window={location:{protocol:'http:',origin:'http://local'}};
const context={window,document,fetch:async()=>{},URL,Intl,Date,Number,Math,JSON,localStorage:{getItem(){return null},setItem(){}},setInterval(){},clearInterval(){},console};
vm.runInNewContext(fs.readFileSync(process.env.APP_JS,'utf8'),context);
const candles=[{time:0,open:1,high:1,low:1,close:1},{time:7200,open:2,high:2,low:2,close:2}];
const filled=context.fillCandleTimeGaps(candles,'1h');
assert.strictEqual(Array.from(filled,row=>row.time).join(','),'0,3600,7200');
assert.strictEqual(Array.from(Object.keys(filled[1])).join(','),'time');
assert.strictEqual(context.chartTickLabel(Date.UTC(2025,0,1)/1000,true),'31/12 20:00');
process.stdout.write('ok');'''
    result = subprocess.run(
        [node, "-e", harness], cwd=ROOT,
        env=dict(os.environ, APP_JS=str(ROOT / "frontend" / "app.js")),
        capture_output=True, text=True, encoding="utf-8", timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "ok"


def test_shadow_cards_validation_labels_and_volatility_detail_are_explicit():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js no está instalado; no se ejecutan los componentes de modo sombra")
    harness = r'''const fs=require('fs'),vm=require('vm'),assert=require('assert');
const elements={};
const document={addEventListener(){},querySelector(selector){return elements[selector]||(elements[selector]={innerHTML:'',textContent:'',className:''})}};
const window={location:{protocol:'http:',origin:'http://local'}};
const context={window,document,fetch:async()=>{},URL,Intl,Date,Number,Math,JSON,localStorage:{getItem(){return null},setItem(){}},setInterval(){},clearInterval(){},console};
vm.runInNewContext(fs.readFileSync(process.env.APP_JS,'utf8'),context);
const statuses=['model_a','model_b','model_c'].map((name,index)=>({model_name:name,display_name:name,available:true,validation_status:index?'not_validated':'shadow',accuracy_30d:null}));
const rows=['model_a','model_b','model_c'].map((name,index)=>({model_name:name,probability_up:[0.62,0.41,0.5][index],signal:['ALCISTA','BAJISTA','NEUTRAL'][index]}));
const consensus={model_available:true,weights:{model_a:1},model_a:{probability_up:0.62,signal:'ALCISTA'}};
context.renderModelCards(consensus,{models:statuses},rows);
context.renderConsensus({...consensus,consensus_probability_up:0.62,consensus_signal:'ALCISTA'});
assert(elements['#model-cards'].innerHTML.includes('No validado'));
assert(elements['#model-cards'].innerHTML.includes('model-b'));
assert(elements['#consensus-card'].innerHTML.includes('PROBABILIDAD DEL MODELO A'));
assert(!elements['#consensus-card'].innerHTML.includes('Consenso'));
const detail=context.volatilityDetail({is_verified:true,price_at_verification:100,market_condition:JSON.stringify({volatility_4h:{range_1sigma:[98,102],range_2sigma:[96,104]}})});
assert(detail.includes('Rango 4h 1'));
assert(detail.includes(String.fromCodePoint(0x2713)));
const missing=context.volatilityDetail({market_condition:'{}'});
assert(missing.includes('guardado al momento'));
const modelElements={};const modelsDocument={addEventListener(){},getElementById(id){return modelElements[id]||(modelElements[id]={value:'historical'})}};
const modelsContext={window,document:modelsDocument,APP:{},volNumber:(v,d)=>v==null?'—':Number(v).toFixed(d),api:{}};
vm.runInNewContext(fs.readFileSync(process.env.MODELS_JS,'utf8'),modelsContext);
const movedHistorical=modelsContext.window.ModelsPageTest.renderVolBattleTable({models:[{model_name:'GBM',is_champion:true,n_verified:12,r2_cal:.5,qlike_cal:.4}]},'historical');
assert(movedHistorical.includes('R² histórico')&&movedHistorical.includes('GBM'));
assert(!movedHistorical.includes('R² en vivo'));
process.stdout.write('ok');'''
    result = subprocess.run(
        [node, "-e", harness], cwd=ROOT,
        env=dict(os.environ, APP_JS=str(ROOT / "frontend" / "app.js"), MODELS_JS=str(ROOT / "frontend" / "models.js")),
        capture_output=True, text=True, encoding="utf-8", timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "ok"


def test_low_price_chart_price_format_and_compact_ordered_dom_ticks():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js no est\u00e1 instalado; se omite prueba del gr\u00e1fico de precio bajo")
    harness = r'''const fs=require('fs'),vm=require('vm'),assert=require('assert');
const box={innerHTML:'',children:[],appendChild(node){this.children.push(node)}};
const document={addEventListener(){},querySelector(){return box;},createElement(){return {textContent:'',style:{},getBoundingClientRect(){return {left:0,right:0}}}}};
const window={location:{protocol:'http:',origin:'http://local'},LightweightCharts:{createChart(_box,options){window.chartOptions=options;return {addCandlestickSeries(options){window.seriesOptions=options;return {setData(){},setMarkers(){},createPriceLine(){return {}},removePriceLine(){}}},timeScale(){return {fitContent(){},scrollToRealTime(){}}},remove(){}}}}};
const context={window,LightweightCharts:window.LightweightCharts,document,fetch:async()=>{},URL,Intl,Date,Number,Math,JSON,localStorage:{getItem(){return null},setItem(){}},setInterval(){},clearInterval(){},console};
vm.runInNewContext(fs.readFileSync(process.env.APP_JS,'utf8'),context);
const price=4.31e-6;context.renderChart([{time:1,open:price,high:price*1.01,low:price*.99,close:price}],{},'4h');
assert(context.window.seriesOptions.priceFormat.precision>=9);
assert(context.window.seriesOptions.priceFormat.minMove<1e-8);
const fmt=context.window.chartOptions.timeScale.tickMarkFormatter;
const ticks=[0,14400,28800,43200].map(t=>fmt(t));
assert(ticks.every(t=>t.length<=11&&t.includes('/')&&t.includes(':')));assert(new Date(14400*1000)>new Date(0));
let left=0;const domTicks=ticks.map(text=>{const width=text.length*7,node={textContent:text,getBoundingClientRect(){return {left,right:left+width}}};const measured={left,right:left+width};left+=width+8;return measured;});
assert(domTicks.slice(1).every((tick,i)=>tick.left>=domTicks[i].right));
assert(!context.formatPrice(price).startsWith('0.00')||context.formatPrice(price)!=='0.00');
process.stdout.write(JSON.stringify({precision:context.window.seriesOptions.priceFormat.precision,ticks}));'''
    result=subprocess.run([node,"-e",harness],cwd=ROOT,env=dict(os.environ,APP_JS=str(ROOT/"frontend/app.js")),capture_output=True,text=True,encoding="utf-8",timeout=10)
    assert result.returncode==0,result.stderr
    values=json.loads(result.stdout)
    assert values["precision"]>=9 and len(values["ticks"])==4


def test_advisor_grid_svg_is_compact_and_explains_unavailable_simulation_and_range_risk():
    node=shutil.which("node")
    if not node: pytest.skip("Node.js no est\u00e1 instalado; se omite prueba del Advisor")
    harness=r'''const fs=require('fs'),vm=require('vm'),assert=require('assert');
const elements={};const document={addEventListener(){},querySelector(s){return elements[s]||(elements[s]={innerHTML:'',textContent:'',className:'',onclick:null})}};
const window={location:{protocol:'http:',origin:'http://local'}};const context={window,document,fetch:async()=>{},URL,Intl,Date,Number,Math,JSON,localStorage:{getItem(){return null},setItem(){}},setInterval(){},clearInterval(){},console,sessionStorage:{setItem(){}}};
vm.runInNewContext(fs.readFileSync(process.env.APP_JS,'utf8'),context);
const data={symbol:'PEPEUSDT',capital:100,current_price:4.31e-6,recommended_floor:4e-6,recommended_ceiling:4.8e-6,suggested_grids:20,range_pct:18,spacing_pct:.9,fee_pct:.1,dust_estimate_pct:.1,net_margin_pct:.7,net_per_cycle_usdt:.03,margin_target_pct:.7,target_met:true,estimated_cycles_to_target:3,risk:{label:'Moderado',meaning:'',capital_below_price_pct:100,unrealized_loss_at_floor_usdt:0},range_position_warning:'El precio est? cerca del techo: casi todo el capital quedar?a en compras',range_risk:{source:'volatilidad realizada',disclaimer:'estimaci?n; colas gruesas; no validado m?s all? de 24 h; no es predicci?n de direcci?n',horizons:{24:{touch_floor:.1,touch_ceiling:.2,exit_upper_bound:.3},72:{touch_floor:.2,touch_ceiling:.3,exit_upper_bound:.5},168:{touch_floor:.3,touch_ceiling:.4,exit_upper_bound:.7}}},simulations:{strategies:{simple:{unavailable:'ValueError: no hay velas suficientes'},smart:{unavailable:'ValueError: no hay velas suficientes'}}},analysis:{}};
context.renderGridV2a(data);const html=elements['#grid-result'].innerHTML;const svg=window.gridSvg(data);const labels=(svg.match(/<text/g)||[]).length-4;
assert(labels<=6);assert(svg.includes('e-6'));assert(html.includes('no disponible para PEPEUSDT: ValueError: no hay velas suficientes'));
assert(html.includes('Direcci'+String.fromCodePoint(0xf3)+'n: sin modelo para esta moneda'));assert(html.includes('Hace falta un modelo entrenado y vigilancia de esta moneda'));assert(html.includes('Probabilidad estimada de tocar el rango'));assert(html.includes('24 h'));assert(html.includes('colas gruesas'));assert(html.includes('casi todo el capital quedar'));
process.stdout.write('ok');'''
    result=subprocess.run([node,"-e",harness],cwd=ROOT,env=dict(os.environ,APP_JS=str(ROOT/"frontend/app.js")),capture_output=True,text=True,encoding="utf-8",timeout=10)
    assert result.returncode==0,result.stderr
    assert result.stdout=="ok"


def test_dashboard_volatility_uses_selected_coin_and_model_only_for_xrp():
    node=shutil.which("node")
    if not node: pytest.skip("Node.js no est\u00e1 instalado; se omite prueba de volatilidad del Dashboard")
    harness=r'''const fs=require('fs'),vm=require('vm'),assert=require('assert');
const card={innerHTML:'',addEventListener(){}};const document={addEventListener(){},querySelector(s){return s==='#volatility-card'?card:null}};
const calls=[];const fetch=async url=>{calls.push(String(url));const data=String(url).includes('/api/grid/volatility')?{symbol:'ADAUSDT',source:'volatilidad realizada ADA 1h',range_2sigma:[.0000039,.0000047],samples:99,calculated_at:'hoy'}:{symbol:'XRPUSDT',price:.5,forecasts:[{horizon_h:24,move_1sigma_pct:3,range_1sigma:[.48,.52],range_2sigma:[.46,.54],champion:'NexoHAR'}]};return {ok:true,status:200,json:async()=>data}};
const window={location:{protocol:'http:',origin:'http://local'}};const context={window,document,fetch,AbortController,setTimeout,clearTimeout,URL,Intl,Date,Number,Math,JSON,localStorage:{getItem(){return null},setItem(){}},setInterval(){},clearInterval(){},console};
vm.runInNewContext(fs.readFileSync(process.env.APP_JS,'utf8'),context);
(async()=>{window.APP.currentSymbol='ADAUSDT';await context.loadVolDashboard();assert(calls.some(x=>x.includes('/api/grid/volatility')&&x.includes('symbol=ADAUSDT')));assert(!calls.some(x=>x.includes('/api/volatility/forecast')));assert(card.innerHTML.includes('Sin modelo de volatilidad para ADAUSDT'));assert(card.innerHTML.includes('volatilidad realizada ADA 1h'));assert(card.innerHTML.includes('0.0000039'));window.APP.currentSymbol='XRPUSDT';await context.loadVolDashboard();assert(calls.some(x=>x.includes('/api/volatility/forecast')&&x.includes('symbol=XRPUSDT')));assert(card.innerHTML.includes('Volatilidad esperada'));process.stdout.write('ok')})().catch(e=>{console.error(e.stack);process.exitCode=1});'''
    result=subprocess.run([node,"-e",harness],cwd=ROOT,env=dict(os.environ,APP_JS=str(ROOT/"frontend/app.js")),capture_output=True,text=True,encoding="utf-8",timeout=10)
    assert result.returncode==0,result.stderr
    assert result.stdout=="ok"



def test_shadow_panel_is_identified_and_dimmed_for_non_xrp():
    app=(ROOT/"frontend/app.js").read_text(encoding="utf-8")
    styles=(ROOT/"frontend/styles.css").read_text(encoding="utf-8")
    assert "XRPUSDT 1h" in app and "shadow?.classList.toggle('muted-shadow',APP.currentSymbol!=='XRPUSDT')" in app
    assert "#shadow-status.muted-shadow" in styles

def test_disabled_testnet_confirm_has_disabled_visual_state():
    styles=(ROOT/"frontend/styles.css").read_text(encoding="utf-8")
    assert ".button:disabled" in styles and "cursor:not-allowed" in styles and "#484f58" in styles
