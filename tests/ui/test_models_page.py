from urllib.parse import parse_qs, urlparse

from playwright.sync_api import expect


def _page_with_empty_api(ui_page, live_server):
    ui_page.route("**/api/**", lambda route: route.fulfill(json={}))
    ui_page.goto(f"{live_server.url}/#models")
    ui_page.wait_for_function("window.ModelsPageTest !== undefined")


def test_models_page_renders_accumulating_below_minimum(ui_page, live_server):
    _page_with_empty_api(ui_page, live_server)
    rendered = ui_page.evaluate("""() => {
      const model = {model_name:'GBM', n_predicciones:40, n_verificadas:29, estado:'activo', all:{mse:.01}};
      return window.ModelsPageTest.renderStats({symbol:'XRPUSDT',horizons:[{horizon_h:4,n_min:30,models:[model]}]},'XRPUSDT',4);
    }""")
    assert "Acumulando datos (29/30)" in rendered
    assert "<td>Activo</td>" not in rendered


def test_models_page_renders_symbol_and_horizon_mismatch_without_mixing(ui_page, live_server):
    _page_with_empty_api(ui_page, live_server)
    rendered = ui_page.evaluate("""() => window.ModelsPageTest.renderStats({symbol:'XRPUSDT',horizons:[
      {horizon_h:1,models:[{model_name:'SOLO-1H'}]},{horizon_h:4,models:[{model_name:'SOLO-4H'}]}]},'BTCUSDT',1)""")
    assert "no coincide con la moneda y el horizonte" in rendered
    assert "SOLO-1H" not in rendered and "SOLO-4H" not in rendered


def test_models_page_renders_no_model_notice(ui_page, live_server):
    _page_with_empty_api(ui_page, live_server)
    rendered = ui_page.evaluate("""() => window.ModelsPageTest.renderStats(
      {symbol:'ADAUSDT',horizons:[{horizon_h:4,models:[]}]},'ADAUSDT',4)""")
    assert "Sin modelos de volatilidad para esta moneda" in rendered
    assert "volatilidad realizada" in rendered


def test_unconfigured_tft_and_ensemble_have_configuration_notice(ui_page, live_server):
    _page_with_empty_api(ui_page, live_server)
    result = ui_page.evaluate("""() => ({
      tft:window.ModelsPageTest.renderDirectionRow('model_d',null,{}, {},null),
      ensemble:window.ModelsPageTest.renderDirectionRow('ensemble',null,{}, {},null)
    })""")
    assert "No habilitado (sin configuración)" in result["tft"]
    assert "No habilitado (sin configuración)" in result["ensemble"]
    assert "No disponible: no aparece" not in result["tft"] + result["ensemble"]


def test_models_page_renders_no_signal_evidence_and_low_effective_sample(ui_page, live_server):
    _page_with_empty_api(ui_page, live_server)
    rendered = ui_page.evaluate("""() => ({
      factor:window.ModelsPageTest.renderWiden({status:'acumulando',n_effective:.75,n:18,k_raw:.8,overestimate_message:'El modelo sobreestima la volatilidad'},24),
      zero:window.ModelsPageTest.renderDirectionRow('model_b',{display_name:'GRU',available:true,validation_status:'not_validated'},
        {total_predictions:0,verified_count:0,pending_count:0,bullish_correct:0,bullish_failed:0,bullish_pending:0,neutral_count:0,base_rate:null},{},null)
    })""")
    assert "Pocos datos (n efectiva 0.75)" in rendered["factor"]
    assert "El modelo sobreestima" not in rendered["factor"]
    assert "Sin evidencia" in rendered["zero"]
    assert "0 % de acierto" not in rendered["zero"]


def test_models_page_separates_historical_and_live_columns_and_shows_ranks(ui_page, live_server):
    _page_with_empty_api(ui_page, live_server)
    rendered = ui_page.evaluate("""() => {
      const data={models:[
        {model_name:'HAR',r2_cal:.8,qlike_cal:.1234567,n_verified:40,r2_live:.7},
        {model_name:'HAR_asym',r2_cal:.6,qlike_cal:.2,n_verified:29,r2_live:.9}]};
      return {historical:window.ModelsPageTest.renderVolBattleTable(data,'historical'),live:window.ModelsPageTest.renderVolBattleTable(data,'live')};
    }""")
    assert "Puesto" in rendered["historical"] and "QLIKE histórico" in rendered["historical"]
    assert "R² en vivo" not in rendered["historical"] and "Δ puesto" not in rendered["historical"]
    assert "Puesto" in rendered["live"] and "Δ puesto vs histórico" in rendered["live"]
    assert "QLIKE histórico" not in rendered["live"] and "R² histórico" not in rendered["live"]
    assert "HAR" in rendered["live"] and "HAR_asym" in rendered["live"]


def test_models_page_weights_and_shared_live_rank(ui_page, live_server):
    _page_with_empty_api(ui_page, live_server)
    result = ui_page.evaluate("""() => {
      const battle={models:[
        {model_name:'HAR',n_verified:32,r2_live:.8},
        {model_name:'HAR_asym',n_verified:32,r2_live:.9}]};
      const extract=html=>{
        const root=document.createElement('div'); root.innerHTML=html;
        return Object.fromEntries([...root.querySelectorAll('tbody tr')].map(row=>{
          const cells=row.querySelectorAll('td'), label=cells[1].querySelector('strong').textContent;
          const name=label==='HAR Asim\u00E9trico'?'HAR_asym':label;
          return [name,{rank:cells[0].firstChild.textContent.trim(),weight:cells[8].textContent.trim()}];
        }));
      };
      const render=models=>extract(window.ModelsPageTest.renderStats({symbol:'XRPUSDT',horizons:[{
        horizon_h:4,n_min:30,adaptive:{source:'val'},models}]},'XRPUSDT',4,battle));
      const val=render([
        {model_name:'HAR',n_verificadas:32,n_predicciones:35,fuente_pesos:'val',peso_respaldo_val:.3,peso_actual:0,all:{}},
        {model_name:'HAR_asym',n_verificadas:32,n_predicciones:35,fuente_pesos:'val',peso_respaldo_val:.2,peso_actual:0,all:{}}]);
      const live=render([{model_name:'HAR',n_verificadas:32,n_predicciones:35,fuente_pesos:'vivo',peso_actual:.25,all:{}}]);
      const below=render([{model_name:'HAR_asym',n_verificadas:29,n_predicciones:35,fuente_pesos:'vivo',peso_actual:.2,all:{}}]);
      const root=document.createElement('div'); root.innerHTML=window.ModelsPageTest.renderVolBattleTable(battle,'live');
      const battleRanks=Object.fromEntries([...root.querySelectorAll('tbody tr')].map(row=>[
        row.cells[1].textContent.trim(),row.cells[0].textContent.trim()]));
      const belowBattle={model_name:'HAR_asym',n_verified:29,r2_live:.9};
      const belowRoot=document.createElement('div'); belowRoot.innerHTML=window.ModelsPageTest.renderVolBattleTable({models:[belowBattle]},'live');
      return {val,live,below,battleRanks,belowBattleRank:belowRoot.querySelector('tbody tr td').textContent.trim()};
    }""")
    assert result["val"]["HAR"]["weight"] == "30.0% (val)"
    assert result["val"]["HAR_asym"]["weight"] == "20.0% (val)"
    assert result["live"]["HAR"]["weight"] == "25.0%"
    assert result["val"]["HAR_asym"]["rank"] == result["battleRanks"]["HAR_asym"] == "1"
    assert result["val"]["HAR"]["rank"] == result["battleRanks"]["HAR"] == "2"
    assert result["below"]["HAR_asym"]["rank"] == "—"
    assert result["belowBattleRank"] == "—"


def test_training_ui_confirmations_escaping_and_polling_lifecycle(ui_page, live_server):
    _page_with_empty_api(ui_page, live_server)
    result = ui_page.evaluate("""() => {
      const unsafe='<img src=x onerror=alert(1)>';
      const jobs=window.ModelsPageTest.renderTrainingJobs([{id:7,models:['b'],status:'error',phase:'error',error:unsafe}]);
      const root=document.createElement('div'); root.innerHTML=jobs;
      window.ModelsPageTest.configureTrainingPolling(true);
      const started=window.ModelsPageTest.isTrainingPolling();
      window.ModelsPageTest.configureTrainingPolling(false);
      return {jobs,images:root.querySelectorAll('img').length,text:root.textContent,started,stopped:!window.ModelsPageTest.isTrainingPolling(),
        active:window.ModelsPageTest.activeTraining({status:'entrenando'}),finished:window.ModelsPageTest.activeTraining({status:'listo'})};
    }""")
    assert "&lt;img" in result["jobs"]
    assert result["images"] == 0
    assert "<img" in result["text"]
    assert result["started"] is True and result["stopped"] is True
    assert result["active"] is True and result["finished"] is False


def test_model_a_training_confirmation_requires_checkbox_and_letter(ui_page, live_server):
    _page_with_empty_api(ui_page, live_server)
    ui_page.locator("#models-training-models button[data-model='a']").click()
    expect(ui_page.locator("#models-train-dialog")).to_be_visible()
    expect(ui_page.locator("#models-train-confirm")).to_be_disabled()
    ui_page.locator("#models-train-reset-check").check()
    expect(ui_page.locator("#models-train-confirm")).to_be_disabled()
    ui_page.locator("#models-train-reset-text").fill("A")
    expect(ui_page.locator("#models-train-confirm")).to_be_enabled()
    expect(ui_page.locator("#models-train-dialog")).to_contain_text("~35–40 min")
    ui_page.locator("#models-train-close").click()


def test_training_dates_are_human_readable_and_compare_instants(ui_page, live_server):
    _page_with_empty_api(ui_page, live_server)
    result = ui_page.evaluate("""() => ({
      same:window.ModelsPageTest.trainingTimesDiffer('2026-10-06T12:00:00+00:00','2026-10-06T08:00:00-04:00'),
      recent:window.ModelsPageTest.trainingTimesDiffer('2026-10-06T12:00:03Z','2026-10-06T12:00:00+00:00'),
      sameRow:window.ModelsPageTest.renderDirectionRow('model_b',{display_name:'GRU',available:true,artifact_trained_at:'2026-10-06T12:00:00+00:00',last_trained:'2026-10-06T08:00:00-04:00'}, {}, {}, null),
      recentRow:window.ModelsPageTest.renderDirectionRow('model_b',{display_name:'GRU',available:true,artifact_trained_at:'2026-10-06T12:00:03Z',last_trained:'2026-10-06T12:00:00+00:00'}, {}, {}, null),
      jobs:window.ModelsPageTest.renderTrainingJobs([{created_at:'2026-10-06T12:00:00+00:00',models:['b'],status:'listo',phase:'listo'}])
    })""")
    assert result["same"] is False
    assert result["recent"] is True
    assert "Entrenado en disco" not in result["sameRow"]
    assert "Entrenado en disco" in result["recentRow"]
    assert "2026-10-06T12:00:00" not in result["jobs"]
    assert "model_b" not in result["jobs"]


def test_training_poll_timers_are_cleared_when_job_finishes(ui_page, live_server):
    _page_with_empty_api(ui_page, live_server)
    result = ui_page.evaluate("""() => {
      const scheduled=[], cleared=[]; let next=0;
      window.setInterval=callback=>{const id=++next;scheduled.push({id,callback});return id;};
      window.clearInterval=id=>cleared.push(id);
      window.ModelsPageTest.configureTrainingPolling(true);
      window.ModelsPageTest.configureTrainingPolling(false);
      return {scheduled:scheduled.map(item=>item.id),cleared,active:window.ModelsPageTest.isTrainingPolling()};
    }""")
    assert len(result["scheduled"]) == 2
    assert result["cleared"] == result["scheduled"]
    assert result["active"] is False


def test_models_page_coverage_horizon_qlike_and_csv_escape(ui_page, live_server):
    _page_with_empty_api(ui_page, live_server)
    rendered = ui_page.evaluate("""() => ({
      onlyFour:window.ModelsPageTest.renderVolCoverage({n:40,coverage_1sigma:.68,coverage_2sigma:.95},1),
      four:window.ModelsPageTest.renderVolCoverage({n:40,coverage_1sigma:.68,coverage_2sigma:.95},4),
      qlike:window.ModelsPageTest.renderV4Qlike({models:[{model_name:'HAR',qlike_cal:.313456959},{model_name:'HAR_asym',qlike_cal:.313533974}]}),
      csv:window.ModelsPageTest.formatVolCsv([{forecast_at:'hoy, ayer',model_name:'HAR "A"',pred_vol_pct:1,realized_vol_pct:2}]),
      windows:(() => { const now=Date.now(), rows=[{forecast_at:new Date(now-2*86400000).toISOString()},{forecast_at:new Date(now-12*86400000).toISOString()},{forecast_at:new Date(now-45*86400000).toISOString()}]; return [window.ModelsPageTest.filterHistoryWindow(rows,'7',now).length,window.ModelsPageTest.filterHistoryWindow(rows,'30',now).length,window.ModelsPageTest.filterHistoryWindow(rows,'all',now).length]; })()
    })""")
    assert "Cobertura disponible solo a 4h" in rendered["onlyFour"]
    assert "Cobertura en vivo 4h" in rendered["four"]
    assert "HAR 0.313457" in rendered["qlike"] and "HAR_asym 0.313534" in rendered["qlike"]
    assert "distintos" in rendered["qlike"]
    assert '"hoy, ayer"' in rendered["csv"] and '"HAR ""A"""' in rendered["csv"]
    assert rendered["windows"] == [1, 2, 3]


def test_models_page_selector_horizon_and_apply_confirmation(live_server, ui_page):
    calls = {"stats": [], "apply": [], "history": []}
    ui_page.on("dialog", lambda dialog: dialog.accept())

    def api(route):
        request = route.request
        parsed = urlparse(request.url)
        query = parse_qs(parsed.query)
        path = parsed.path
        symbol = query.get("symbol", ["XRPUSDT"])[0]
        horizon = int(query.get("horizon", [4])[0])
        if path == "/api/health":
            return route.fulfill(json={"status": "ok"})
        if path == "/api/models/page-context":
            return route.fulfill(json={"symbol": "XRPUSDT", "interval": "1h", "coins": ["XRPUSDT", "BTCUSDT"], "models": {
                "model_a": {"total_predictions": 0, "verified_count": 0, "pending_count": 0, "base_rate": None},
                "model_b": {"total_predictions": 0, "verified_count": 0, "pending_count": 0, "base_rate": None},
                "model_c": {}, "model_d": {}, "ensemble": {}}})
        if path == "/api/models/status":
            models = [{"model_name": name, "display_name": name, "available": name == "model_a",
                       "validation_status": "not_validated", "unavailable_reason": "model_b no pudo cargar el artefacto"}
                      for name in ("model_a", "model_b", "model_c", "model_d", "ensemble")]
            return route.fulfill(json={"models": models, "winner_by_accuracy": None})
        if path == "/api/models/shadow-status":
            return route.fulfill(json={"model_name": "model_a", "symbol": "XRPUSDT", "interval": "1h"})
        if path == "/api/volatility/model-stats":
            calls["stats"].append((symbol, horizon))
            if symbol != "XRPUSDT":
                return route.fulfill(status=404, json={"detail": "no model"})
            verified = 29 if horizon == 1 else 30
            return route.fulfill(json={"symbol": symbol, "horizons": [{"symbol": symbol, "horizon_h": horizon, "n_min": 30,
                "adaptive": {"source": "val", "confidence": "baja", "eligible": []}, "validation_status_live": "en_evaluacion",
                "models": [{"model_name": "GBM", "n_predicciones": 35, "n_verificadas": verified,
                    "estado": "activo", "peso_actual": .2, "all": {"success_1sigma": 20, "success_2sigma": 28,
                    "failures": 2, "mse": .01, "qlike": .02, "var_ratio": 1.1, "over_pct": 55, "under_pct": 45}}]}]})
        if path == "/api/volatility/forecast":
            return route.fulfill(json={"symbol": symbol, "forecasts": [{"horizon_h": horizon, "champion": "GBM", "move_1sigma_pct": 3.2,
                "consensus": {"sigma_pct": 3.1, "dispersion_iqr": .04, "confidence": "baja", "validation_status_live": "en_evaluacion"}}]})
        if path == "/api/volatility/widen-factor":
            horizons = []
            for value in (1, 2, 4, 24):
                status = "acumulando" if value == 1 else "disponible"
                horizons.append({"horizon_h": value, "status": status, "k_active": 1.25,
                    "k_stress_smoothed": 1.3, "k_raw": 1.1, "ci_low": 1.01, "ci_high": 1.4, "progress_pct": 70,
                    "days_estimated": 4, "n": 620, "n_effective": .75 if value == 1 else 60,
                    "overestimate_message": "El modelo sobreestima la volatilidad", "bias_log": .02, "vol_scale_suggested": .98})
            return route.fulfill(json={"symbol": symbol, "horizons": horizons})
        if path == "/api/volatility/battle":
            return route.fulfill(json={"symbol": symbol, "horizon_h": horizon, "models": [
                {"model_name": "GBM", "is_champion": True, "r2_cal": .7, "qlike_cal": .3, "n_verified": 29 if horizon == 1 else 30, "r2_live": .6},
                {"model_name": "HAR", "r2_cal": .8, "qlike_cal": .3134569593664547, "n_verified": 30, "r2_live": .75},
                {"model_name": "HAR_asym", "r2_cal": .75, "qlike_cal": .31353397434507996, "n_verified": 29, "r2_live": .8}]})
        if path == "/api/predictions/volatility-coverage":
            return route.fulfill(json={"n": 25, "coverage_1sigma": .64, "coverage_2sigma": .9})
        if path == "/api/volatility/history":
            calls["history"].append(query)
            return route.fulfill(status=503, json={"detail": "offline fixture"})
        if path == "/api/volatility/widen-factor/apply" and request.method == "POST":
            calls["apply"].append(request.post_data_json)
            return route.fulfill(json={"applied": True})
        return route.fulfill(json={})

    ui_page.route("**/api/**", api)
    ui_page.goto(f"{live_server.url}/#models")
    expect(ui_page.locator(".tabs a").nth(2)).to_have_text("Modelos")
    expect(ui_page.locator("#models-vol-table")).to_contain_text("GBM")
    qlike = ui_page.locator('#models-vol-table button[data-sort="qlike_cal"]')
    qlike.click()
    expect(ui_page.locator("#models-vol-table tbody tr").first).to_contain_text("HAR_asym")
    expect(qlike).to_contain_text("▼")
    qlike.click()
    expect(ui_page.locator("#models-vol-table tbody tr").first).to_contain_text("GBM")
    expect(qlike).to_contain_text("▲")
    expect(ui_page.locator("#models-vol-live-table")).to_contain_text("Activo")
    expect(ui_page.locator("#models-vol-history-empty")).to_contain_text("Historial: error de red")
    expect(ui_page.locator("#models-vol-history")).not_to_contain_text("Cargando")
    expect(ui_page.locator("#models-vol-short-history")).to_contain_text("Historial: error de red")
    expect(ui_page.locator("#models-vol-short-history")).not_to_contain_text("Cargando")
    expect(ui_page.locator("#models-v4-qlike")).to_contain_text("HAR 0.313457")
    expect(ui_page.locator("#models-v4-qlike")).to_contain_text("HAR_asym 0.313534")
    expect(ui_page.locator("#models-v4-qlike")).to_contain_text("distintos")
    expect(ui_page.locator("#models-direction-content")).to_contain_text("XRPUSDT 1h")
    expect(ui_page.locator("#models-direction-content")).to_contain_text("Sin evidencia")
    expect(ui_page.locator("#models-direction-content")).to_contain_text("model_b no pudo cargar el artefacto")
    button = ui_page.locator("#models-vol-factor .apply-widen-suggestion")
    expect(button).to_be_visible()
    with ui_page.expect_response("**/api/volatility/widen-factor/apply"):
        button.click()
    assert calls["apply"] == [{"horizon_h": 4, "k": 1.3, "confirm": True}]
    ui_page.locator("#models-vol-mode").select_option("live")
    expect(ui_page.locator("#models-vol-table")).to_contain_text("Δ puesto vs histórico")
    expect(ui_page.locator("#models-vol-table")).not_to_contain_text("QLIKE histórico")
    expect(ui_page.locator("#models-vol-sample-notice")).to_have_text("")
    asym_live = ui_page.locator("#models-vol-table tbody tr").filter(has_text="HAR_asym")
    expect(asym_live.locator("td").nth(0)).to_have_text("—")
    expect(asym_live.locator("td").nth(4)).to_have_text("—")
    ui_page.locator("#models-vol-series").select_option("consensus")
    expect(ui_page.locator("#models-vol-history-empty")).to_contain_text("Consenso: serie histórica no disponible")
    ui_page.locator("#models-vol-model").select_option("HAR")
    with ui_page.expect_request(lambda request: "/api/volatility/history" in request.url and "model=HAR" in request.url):
        ui_page.locator("#models-vol-series").select_option("model")
    ui_page.locator("#models-vol-window").select_option("7")
    with ui_page.expect_download() as download_info:
        ui_page.locator("#models-vol-csv").click()
    assert download_info.value.suggested_filename == "volatilidad_historial.csv"
    ui_page.locator("#models-horizon").select_option("1")
    expect(ui_page.locator("#models-vol-live-table")).to_contain_text("Acumulando datos (29/30)")
    expect(ui_page.locator("#models-vol-sample-notice")).to_contain_text("29/30")
    expect(ui_page.locator("#models-vol-coverage")).to_contain_text("Cobertura disponible solo a 4h")
    expect(ui_page.locator("#models-vol-factor")).to_contain_text("Pocos datos (n efectiva 0.75)")
    expect(ui_page.locator("#models-vol-factor")).not_to_contain_text("El modelo sobreestima")
    ui_page.locator("#models-symbol").select_option("BTCUSDT")
    expect(ui_page.locator("#models-vol-live-table")).to_contain_text("Volatilidad solo disponible para XRPUSDT")
    expect(ui_page.locator("#models-vol-live-table")).not_to_contain_text("error")
    expect(ui_page.locator("#models-vol-summary")).not_to_contain_text("σ 3.2%")
    assert ("XRPUSDT", 1) in calls["stats"] and ("BTCUSDT", 1) not in calls["stats"]

