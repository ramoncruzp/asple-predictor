from __future__ import annotations

from playwright.sync_api import expect


def _battle(ui_page, live_server):
    ui_page.route("**/api/**", lambda route: route.fulfill(json={}))
    ui_page.goto(f"{live_server.url}/#battle")
    ui_page.wait_for_function("window.BattlePageTest !== undefined")


def test_battle_cards_explain_zero_signals_unavailable_and_unconfigured_models(ui_page, live_server):
    _battle(ui_page, live_server)
    disclaimer = ui_page.locator(".battle-score-disclaimer")
    expect(disclaimer).to_be_visible()
    expect(disclaimer).to_contain_text("no son probabilidades calibradas")
    expect(disclaimer).to_contain_text("reponderan las clases")
    expect(disclaimer).to_contain_text("0,60")
    expect(disclaimer).to_contain_text("27 % real")
    result = ui_page.evaluate("""() => {
      const t=window.BattlePageTest;
      const no=t.card({available:true},{bullish_count:0,neutral_count:12,verified_count:19,base_rate:.42});
      const unavailable=t.card({available:false,unavailable_reason:'Artefacto no cargado'},{});
      const cards=t.renderCards({status:{models:[{model_name:'model_a',available:true}]},context:{models:{model_a:{bullish_count:0,neutral_count:0,verified_count:0}}}});
      return {no,unavailable,cards};
    }""")
    assert "Sin señales ALCISTA" in result["no"] and "12 neutrales · 19 verificadas" in result["no"]
    assert "42.0%" in result["no"] and "—" not in result["no"]
    assert "No disponible" in result["unavailable"] and "Artefacto no cargado" in result["unavailable"]
    assert "XGBoost" in result["cards"] and "model_d" not in result["cards"]


def test_battle_restores_validation_tooltips_winners_and_signal_chips(ui_page, live_server):
    _battle(ui_page, live_server)
    result = ui_page.evaluate("""() => {
      const models=[{model_name:'model_a',validation_status:'not_validated'},{model_name:'model_b',validation_status:'not_validated'}];
      const payload={status:{models},context:{symbol:'XRPUSDT',interval:'1h',models:{}},conditions:{
        model_a:[{condition_name:'rsi_oversold',accuracy:.8,verified_count:8,total_predictions:9}],
        model_b:[{condition_name:'rsi_oversold',accuracy:.7,verified_count:10,total_predictions:11}]
      },history:[{predicted_at:'2026-10-06T12:00:00Z',symbol:'XRPUSDT',model_name:'model_a',signal:'ALCISTA',probability_up:.7,is_verified:true,was_correct:true}]};
      const t=window.BattlePageTest, cards=t.renderCards(payload), matrix=t.renderMatrix(payload), history=t.renderHistory(payload);
      return {cards,matrix,history};
    }""")
    assert result["cards"].count("No validado") == 2
    assert result["matrix"].count("No validado") == 4
    assert "activo posiblemente sobrevendido" in result["matrix"]
    assert 'class="winner"' in result["matrix"]
    assert "signal " in result["history"] and "mono" in result["history"] and "muted" in result["history"]


def test_battle_score_cards_use_responsive_desktop_grid(ui_page, live_server):
    _battle(ui_page, live_server)
    ui_page.evaluate("""() => window.BattlePageTest.render({status:{models:[
      {model_name:'model_a'},{model_name:'model_b'},{model_name:'model_c'},{model_name:'model_d'}
    ]},context:{symbol:'XRPUSDT',interval:'1h',models:{}},conditions:{},history:[]})""")
    grid = ui_page.locator(".battle-score-grid").evaluate("e => ({display:getComputedStyle(e).display,columns:getComputedStyle(e).gridTemplateColumns.split(' ').length,width:innerWidth})")
    assert grid["display"] == "grid" and grid["columns"] > 1 and grid["width"] >= 1000


def test_battle_ranking_requires_thirty_bullish_verified_and_sorts_both_directions(ui_page, live_server):
    _battle(ui_page, live_server)
    result = ui_page.evaluate("""() => {
      const models=[{model_name:'model_a',display_name:'Alfa'},{model_name:'model_b',display_name:'Beta'}];
      const context={models:{model_a:{bullish_correct:20,bullish_failed:0,neutral_count:10},model_b:{bullish_correct:18,bullish_failed:12,neutral_count:0}}};
      const payload={status:{models},context};
      const t=window.BattlePageTest;
      const below=t.renderRanking({status:{models:[models[0]]},context:{models:{model_a:{bullish_correct:20,bullish_failed:0,neutral_count:10}}}});
      const descending=t.sortForTest(payload,'name','desc');
      const ascending=t.sortForTest(payload,'name','asc');
      return {below,descending,ascending};
    }""")
    assert "Pocos datos" in result["below"]
    assert "Puesto por precisión ALCISTA" in result["below"]
    assert result["descending"] == ["Beta", "Alfa"]
    assert result["ascending"] == ["Alfa", "Beta"]


def test_battle_matrix_groups_empty_conditions_and_uses_dark_sticky_scroll(ui_page, live_server):
    _battle(ui_page, live_server)
    ui_page.set_viewport_size({"width": 360, "height": 800})
    ui_page.evaluate("""() => {
      const models=[{model_name:'model_a',display_name:'Modelo largo para desplazar'}];
      window.BattlePageTest.render({status:{models},context:{symbol:'XRPUSDT',interval:'1h',models:{}},conditions:{model_a:[]},history:[]});
    }""")
    expect(ui_page.locator("#battle-content details summary")).to_contain_text("Sin datos aún (5 condiciones)")
    metrics = ui_page.locator(".battle-matrix-scroll").first.evaluate("e => ({color:getComputedStyle(e).scrollbarColor, first:getComputedStyle(e.querySelector('th:first-child')).position, width:e.scrollWidth, client:e.clientWidth})")
    assert metrics["color"] and metrics["color"] != "auto"
    assert metrics["first"] == "sticky"
    assert metrics["width"] > metrics["client"]
    assert "XRPUSDT · 1h" in ui_page.locator(".battle-context").inner_text()


def test_battle_history_filters_neutrals_paginates_and_escapes_csv(ui_page, live_server):
    _battle(ui_page, live_server)
    ui_page.evaluate("""() => {
      const history=Array.from({length:35},(_,i)=>({predicted_at:'2026-10-06T12:00:00Z',symbol:i===0?'XRP,USDT':'XRPUSDT',model_name:i%2?'model_b':'model_a',interval:i===34?'4h':'1h',signal:i%3?'ALCISTA':'NEUTRAL',probability_up:.5,is_verified:true,was_correct:i===0?null:true,market_condition:'quoted "range", line\\nsecond'}));
      window.BattlePageTest.render({status:{models:[{model_name:'model_a'},{model_name:'model_b'}]},context:{symbol:'XRPUSDT',interval:'1h',models:{}},conditions:{},history});
    }""")
    expect(ui_page.locator("#battle-content h2").last).to_contain_text("Registro de predicciones · XRPUSDT · 1h")
    expect(ui_page.locator("#battle-content thead").last).to_contain_text("Prob. de subida")
    filters = ui_page.locator(".battle-filters").last
    expect(filters.locator("label").nth(0)).to_contain_text("Modelo")
    expect(filters.locator("label").nth(1)).to_contain_text("Señal")
    expect(filters.locator("label").nth(2)).to_contain_text("Resultado")
    expect(filters.locator("[data-battle-filter='model'] option").nth(1)).to_have_text("XGBoost")
    expect(filters.locator("[data-battle-filter='model'] option").nth(1)).to_have_attribute("value", "model_a")
    history = ui_page.locator("#battle-content > section:nth-of-type(3)")
    expect(history.locator("tbody tr")).to_have_count(30)
    ui_page.locator("[data-battle-filter='model']").select_option("model_a")
    assert all("XGBoost" == value for value in history.locator("tbody tr td:nth-child(3)").all_text_contents())
    ui_page.locator("[data-battle-hide-neutral]").check()
    assert all("NEUTRAL" not in value for value in history.locator("tbody tr td:nth-child(4)").all_text_contents())
    ui_page.locator("[data-battle-filter='model']").select_option("")
    ui_page.locator("[data-battle-hide-neutral]").uncheck()
    ui_page.locator("[data-battle-more]").click()
    expect(history.locator("tbody tr")).to_have_count(34)
    ui_page.locator("[data-battle-filter='model']").select_option("model_a")
    expect(history.locator("tbody tr")).to_have_count(17)
    escaped = ui_page.evaluate("""() => window.BattlePageTest.csvEscape('a,"b"\\nc')""")
    assert escaped == '"a,""b""\nc"'
    csv = ui_page.evaluate("""() => window.BattlePageTest.csv([{predicted_at:'d',symbol:'XRP,USDT',model_name:'model_a',signal:'NEUTRAL',probability_up:.5,is_verified:true,was_correct:null}])""")
    assert csv.startswith("\ufeffFecha,Símbolo,Modelo")
    assert '"XRP,USDT"' in csv and "NEUTRAL" in csv
    filename = ui_page.evaluate("""() => window.BattlePageTest.csvFilename({symbol:'XRPUSDT',interval:'1h'},new Date('2026-10-06T00:00:00Z'))""")
    assert filename == "battle_direccion_XRPUSDT_1h_20261006.csv"


def test_battle_chart_keeps_final_ticks_inside_narrow_viewport(ui_page, live_server):
    _battle(ui_page, live_server)
    ui_page.set_viewport_size({"width": 360, "height": 800})
    # Lightweight Charts is external and blocked by the UI fixture; verify the configured
    # chart margin in the browser and the actual narrow viewport geometry.
    result = ui_page.evaluate("""() => {
      const chart=document.createElement('div'); chart.id='models-vol-history'; chart.style.width='320px'; document.body.append(chart);
      let opts;
      window.LightweightCharts={createChart(_el,options){opts=options;return {addLineSeries(){return {setData(){}}},timeScale(){return {fitContent(){}}}}}};
      window.ModelsPageTest.renderVolHistory([{forecast_at:'2026-10-06T15:00:00Z',pred_vol_pct:2,realized_vol_pct:3}],4,'champion');
      const rect=chart.getBoundingClientRect();
      return {offset:opts.timeScale.rightOffset,width:rect.width,viewport:innerWidth};
    }""")
    assert result["offset"] >= 8
    assert result["width"] <= result["viewport"]


def test_battle_notes_when_history_reaches_the_100_row_limit(ui_page, live_server):
    _battle(ui_page, live_server)
    ui_page.evaluate("""() => window.BattlePageTest.render({status:{models:[{model_name:'model_a'}]},
      context:{symbol:'XRPUSDT',interval:'1h',models:{}},conditions:{},history:Array.from({length:120},(_,i)=>({
        predicted_at:`2026-10-06T12:${String(i%60).padStart(2,'0')}:00Z`,symbol:'XRPUSDT',model_name:'model_a',
        signal:'ALCISTA',probability_up:.7,is_verified:true,was_correct:true}))})""")
    for _ in range(3):
        ui_page.locator("[data-battle-more]").click()
    expect(ui_page.locator("#battle-content > section:last-of-type tbody tr")).to_have_count(100)
    expect(ui_page.locator("[data-battle-limit-note]")).to_have_text("Mostrando las últimas 100")
    assert ui_page.locator("[data-battle-more]").count() == 0
