from __future__ import annotations

import json
import re

import pytest
from playwright.sync_api import expect

from tests.ui.conftest import assert_no_js_errors


def _close_button(page):
    return page.locator('[data-grid-action="close"]').first


def _open_close_dialog(page, server):
    page.goto(f"{server.url}/#grids/{server.grid_id}")
    expect(page.locator("[data-grid-controls]")).to_be_visible()
    _close_button(page).click()
    expect(page.get_by_role("dialog")).to_be_visible()


def _is_close_post(request, grid_id):
    return request.method == "POST" and request.url.endswith(f"/api/grids/{grid_id}/close")


def test_close_dialog_options_are_visible_in_required_order(live_server, ui_page):
    _open_close_dialog(ui_page, live_server)
    labels = ui_page.locator(".grid-close-modes label")
    visible = [labels.nth(index).inner_text().strip() for index in range(labels.count())]
    assert visible == [
        "Vender todo a mercado",
        "Vender lo positivo y pasar lo negativo al grid especial",
        "Pasar todas las celdas al repositorio",
        "Solo cancelar órdenes (deja monedas sueltas)",
    ]


def test_coins_screen_shows_grid_metadata_and_reactivation(live_server, ui_page):
    ui_page.route("**/api/coins" + chr(63) + "include_inactive=true", lambda route: route.fulfill(json=[{
        "symbol": "SOLUSDT", "active": False, "added_at": "2026-10-01T12:00:00Z",
        "notes": None, "price": 150, "volume_24h_quote": 250000,
        "change_pct_24h": 1.2, "is_predictor_symbol": False,
        "open_grid_id": None, "volatility_model": "model_vol_a",
    }]))
    ui_page.goto(f"{live_server.url}/#coins")
    ui_page.locator("#coins-show-inactive").check()
    expect(ui_page.locator(".coin-reactivate-btn")).to_be_visible()
    expect(ui_page.locator("#coins-table")).to_contain_text("Modelo de volatilidad")
    expect(ui_page.locator("#coins-table")).to_contain_text("Volumen 24h (USDT)")
    ui_page.route("**/api/coins", lambda route: route.fulfill(status=201, json={"symbol": "SOLUSDT", "active": 1})
                   if route.request.method == "POST" else route.continue_())
    with ui_page.expect_request(lambda request: request.method == "POST" and request.url.endswith("/api/coins")):
        ui_page.get_by_role("button", name="Reactivar").click()
    assert_no_js_errors(ui_page)


def test_empty_grids_state_links_to_creation_screens(live_server, ui_page):
    ui_page.route("**/api/grids", lambda route: route.fulfill(json={"grids": []})
                  if route.request.method == "GET" else route.continue_())
    ui_page.goto(f"{live_server.url}/#grids")
    expect(ui_page.locator("#grids-content")).to_contain_text("Sin grids abiertos")
    expect(ui_page.locator('#grids-content a[href="#scanner"]')).to_have_text("Scanner")
    expect(ui_page.locator('#grids-content a[href="#grid"]')).to_have_text("Grid Advisor")
    expect(ui_page.locator("#screen-grids .page-heading")).to_contain_text("revisa el plan antes de confirmar")
    assert_no_js_errors(ui_page)


def test_grid_loan_summary_and_disable_action_use_preview_then_confirmation(live_server, ui_page):
    grid_id = live_server.secondary_grid_id
    live_server.db.update_grid(grid_id, status="ACTIVE")
    live_server.db.merge_grid_params(grid_id, {"loans_enabled": True, "loans_group": "loans_v2",
        "loan_lender_max_pct": 70.0}, allowed=frozenset({"loans_enabled", "loans_group", "loan_lender_max_pct"}))
    ui_page.route(f"**/api/grids/{grid_id}/loans", lambda route: route.fulfill(json={
        "grid_id": grid_id, "loans_group": "loans_v2", "loans_enabled": True,
        "counts": {"OPEN": 1, "REPAID": 2, "TRANSFERRED": 0, "PENDING": 0, "CANCELLED": 0},
        "total_amount_lent_usdt": 40, "average_repaid_open_hours": 3.5,
        "open_loans": [{"borrower_idx": 2, "lender_source": "reserva", "lender_idx": None,
            "amount_usdt": 10, "age_hours": 1.5}],
    }))
    ui_page.route("**/api/grids/loans/summary", lambda route: route.fulfill(json={
        "groups": [{"group": "loans", "grid_count": 1, "realized_pnl_usdt": 3,
            "pnl_per_open_day_usdt": 1, "pnl_pct_capital": 2.5,
            "pnl_pct_capital_per_day": 0.5, "cycles_completed": 2, "commissions_usdt": 0.5,
            "loans_created": 3, "loans_repaid": 2, "loans_transferred": 0},
            {"group": "loans_v2", "grid_count": 1, "realized_pnl_usdt": 5,
            "pnl_per_open_day_usdt": 1, "pnl_pct_capital": 2.5,
            "pnl_pct_capital_per_day": 0.5, "cycles_completed": 2, "commissions_usdt": 0.5,
            "loans_created": 3, "loans_repaid": 2, "loans_transferred": 0}],
        "cohort_comparisons": {"loans_vs_control": {"cohort": "loans",
            "diff_pct_per_day": 0.25, "ci_low": -0.5, "ci_high": 1.0,
            "n_cohort": 1, "n_control": 1, "conclusive": False,
            "reason": "muestra insuficiente", "small_sample": True},
            "loans_v2_vs_control": {"cohort": "loans_v2", "diff_pct_per_day": None,
            "ci_low": None, "ci_high": None, "n_cohort": 0, "n_control": 1,
            "conclusive": False, "reason": "muestra insuficiente", "small_sample": True}},
        "comparison_note": "P&L realizado por capital y d\u00eda abierto; no ajusta tama\u00f1o de celda ni moneda. Observacional: control es cada 3.er grid.",
        "note": "Muestra pequeña y mercado distinto por grid: es una guía, no una conclusión.",
    }))
    requests = []
    def disable(route):
        body = route.request.post_data_json
        requests.append(body)
        if body["dry_run"]:
            route.fulfill(json={"dry_run": True, "plan": {"action": "disable-loans", "loans_open_count": 1,
                "loans_open_amount_usdt": 10, "loans_enabled_after": False}})
        else:
            route.fulfill(json={"dry_run": False, "result": {"ok": True, "loans_transferred": 1}})
    ui_page.route(f"**/api/grids/{grid_id}/loans/disable", disable)
    ui_page.goto(f"{live_server.url}/#grids")
    expect(ui_page.locator(".loans-summary-line")).to_contain_text("Muestra pequeña")
    expect(ui_page.locator(".loans-summary-line")).to_contain_text("3 préstamos creados/2 devueltos")
    expect(ui_page.locator(".loans-summary-line")).to_contain_text("2.50% del capital")
    expect(ui_page.locator(".loans-summary-line")).to_contain_text("loans: 1 grids")
    expect(ui_page.locator(".loans-summary-line")).to_contain_text("loans_v2: 1 grids")
    expect(ui_page.locator(".loans-summary-line")).to_contain_text("loans vs control: no concluyente")
    expect(ui_page.locator(".loans-summary-line")).to_contain_text("muestra peque\u00f1a")
    expect(ui_page.locator(".loans-summary-line")).to_contain_text("IC 95%")
    expect(ui_page.locator(".loans-summary-line")).to_contain_text("Observacional")
    ui_page.goto(f"{live_server.url}/#grids/{grid_id}")
    expect(ui_page.locator(".loans-detail")).to_contain_text("Activo")
    expect(ui_page.locator(".loans-detail")).to_contain_text("reserva")
    button = ui_page.locator('[data-grid-action="disable-loans"]')
    expect(button).to_be_visible()
    button.click()
    dialog = ui_page.locator("#grid-action-dialog")
    expect(dialog).to_contain_text("transferencia contable")
    dialog.get_by_role("button", name="Continuar").click()
    expect(dialog).to_contain_text("Préstamos abiertos a transferir: 1")
    dialog.get_by_role("button", name="Continuar").click()
    expect(dialog).to_contain_text("Acción completada")
    assert requests == [{"dry_run": True, "confirm": False}, {"dry_run": False, "confirm": True}]
    assert_no_js_errors(ui_page)


def test_level_adjust_panel_shows_switch_legend_and_three_verdicts(live_server, ui_page):
    ui_page.route("**/api/grids/level-adjust/summary", lambda route: route.fulfill(json={
        "idle_shrink_enabled": False,
        "note": "apagado por defecto; sin evidencia de mejora en simulación.",
        "grids": [{"grid_id": 9, "symbol": "ADAUSDT",
            "adjustments": {"MONITOR": 1, "CAPITAL_SHRINK": 2, "IDLE_SHRINK": 0},
            "idle_evaluations": 3, "idle_applied": 0, "idle_omissions": {"cooldown": 3},
            "idle_blocked_reason": "interruptor_global_apagado"}],
        "verdicts": {"idle_shrink": {"verdict": "en prueba"},
            "capital_shrink": {"verdict": "apagar definitivamente"},
            "sample_only": {"verdict": "candidato a activar por defecto (decide Ramón)"}},
        "pairs": {"idle_shrink": {"n_pairs": 4, "excluded_pairs": []},
            "capital_shrink": {"n_pairs": 20, "excluded_pairs": [{"pair_id": "p1", "reason": "brazos no cerrados"}]}}
    }))
    ui_page.goto(f"{live_server.url}/#grids")
    panel = ui_page.locator(".level-adjust-panel")
    expect(panel).to_contain_text("Vigilancia de niveles")
    expect(panel).to_contain_text("apagado por defecto; sin evidencia de mejora en simulación")
    expect(panel).to_contain_text("en prueba")
    expect(panel).to_contain_text("apagar definitivamente")
    expect(panel).to_contain_text("candidato a activar por defecto (decide Ramón)")
    expect(panel).to_contain_text("capital_shrink")
    expect(panel).to_contain_text("cooldown: 3")
    expect(panel).to_contain_text("interruptor_global_apagado")
    assert panel.locator("[class*=success], [class*=failure], [class*=positive], [class*=negative]").count() == 0


def test_loan_pairs_panel_renders_conclusive_and_nonconclusive_without_verdict_colors(live_server, ui_page):
    summary = {"n_pairs": 20, "mean_d": 0.25, "sd_d": 0.1, "t_paired": 11.18,
        "ci_low": 0.12, "ci_high": 0.38, "wins": 15, "treated_pairs": 12,
        "mean_d_treated": 0.3, "n_treated": 12, "duration_ratio_flagged": 1,
        "detectable_effect_80pct": 0.063, "orphan_pairs": 1, "conclusive": True,
        "reason": [], "note": "Observacional dentro del par; P&L realizado." ,
        "pairs": [{"pair_id": "12345678-abcd", "symbol": "ADAUSDT",
            "pair_loans_status": "CLOSED", "pair_control_status": "CLOSED",
            "d_i": 0.25, "excluded_reason": None}]}
    ui_page.route("**/api/grids/loans/pairs/summary",
                  lambda route: route.fulfill(json=summary))
    ui_page.goto(f"{live_server.url}/#grids")
    panel = ui_page.locator(".loan-pairs-panel")
    expect(panel).to_contain_text("Préstamos — pares")
    expect(panel).to_contain_text("ADAUSDT")
    expect(panel).to_contain_text("12345678")
    expect(panel).to_contain_text("CLOSED")
    expect(panel).to_contain_text("0.250%/día")
    expect(panel).to_contain_text("concluyente")

    summary.update({"n_pairs": 4, "conclusive": False,
                    "reason": ["muestra insuficiente (n<15)"], "orphan_pairs": 2})
    ui_page.reload()
    expect(panel).to_contain_text("no concluyente (muestra insuficiente (n<15))")
    expect(panel).to_contain_text("pares huérfanos: 2")
    assert panel.locator(".pnl-positive, .pnl-negative").count() == 0
    expect(ui_page.locator(".loans-summary-line")).to_contain_text(
        "Histórico: sin préstamos reales (grid_loans=0); no usar para decidir.")
    assert_no_js_errors(ui_page)


def test_grid_advisor_explains_margin_risk_and_prefills_scanner(live_server, ui_page):
    advisor = {"symbol":"XRPUSDT","current_price":100,"recommended_floor":80.123456,"recommended_ceiling":120.987654,
        "range_pct":40,"range_mode":"centrado","recommended_range":"centrado",
        "range_centered":{"floor":90,"ceiling":111,"touch_probability_each_side_72h":.3,"sigma_widened":True,"sigma_widen_factor":1.25},
        "range_structural":{"floor":80.123456,"ceiling":120.987654},
        "range_preference_note":"El precio está a menos de 1 ATR de un borde estructural; se prefiere el rango centrado.",
        "suggested_grids":20,"capital":1000,"capital_per_grid":50,"spacing_pct":2,
        "margin_target_pct":.7,"fee_pct":.1,"dust_estimate_pct":.2,"net_margin_pct":1.6,
        "net_per_cycle_usdt":.8,"target_met":True,"estimated_cycles_to_target":9,"min_cell_usdt":"5",
        "min_cell_warning":None,"functional_cell_warning":"Con este capital por celda el grid no podrá prestar ni añadir niveles; solo podrá reducirlos. Está bajo el mínimo funcional.","range_warning":False,"risk":{"label":"Moderado","max_range_pct":45,
            "meaning":"Equilibrio","capital_below_price_pct":50,"unrealized_loss_at_floor_usdt":120},
        "simulations":{"label":"histórico, no promesa de resultado","sim_start":"2026-10-01T00:00:00+00:00","sim_days":12,"window_warning":"Ventana corta (menos de 30 días): poca evidencia.","strategies":{"simple":{"pnl_total_net_usdt":10,"max_drawdown_pct":1.25,"fees_usdt":2.5,"buy_hold_pnl_usdt":3.75,"cycles_completed":4},
            "smart":{"pnl_total_net_usdt":12,"max_drawdown_pct":2.5,"fees_usdt":3.5,"buy_hold_pnl_usdt":4.75,"cycles_completed":5}}},"analysis":{"main_support":90,"main_resistance":110,
            "atr":2,"support_touches":3,"resistance_touches":4},"prediction_signal":None,
        "range_risk":{"sigma_24h":.02,"source":"campeón","horizons":{"24":{"touch_floor":.2,"touch_ceiling":.25,"exit_upper_bound":.4},"72":{"touch_floor":.4,"touch_ceiling":.45,"exit_upper_bound":.7}},"disclaimer":"Estimaci\u00F3n te\u00F3rica."},
        "sigma_surfaces":{"realized_30d":{"value":.01,"source":"realizada","window":"30 d"},"champion_24h":{"value":.02,"source":"campe\u00f3n","window":"24 h"},"champion_monitor_h":{"value":.03,"source":"campe\u00f3n","window":"4 h"},"monitor_h":4},
        "pause_risk":{"horizon_h":4,"break_prob":.15,"pause_enter_prob":.10,"would_be_pausable":True},
        "pause_risk_24h":{"horizon_h":24,"break_prob":.20,"pause_enter_prob":.10,"would_be_pausable":True},
        "disclaimer":"Estimación teórica.",
        "unready_coin_warning":"Moneda sin modelo (historial insuficiente): la volatilidad es la realizada de 30 días; sin validación estadística."}
    def fulfill_advisor(route):
        payload = dict(advisor)
        if "range_mode=estructural" in route.request.url:
            payload.update(range_mode="estructural", recommended_range="estructural",
                recommended_floor=payload["range_structural"]["floor"],
                recommended_ceiling=payload["range_structural"]["ceiling"])
        route.fulfill(json=payload)
    ui_page.route("**/api/grid/recommend**", fulfill_advisor)
    ui_page.add_init_script("Object.defineProperty(navigator, 'clipboard', {configurable:true, value:{writeText: text => {window.__copiedText=text; return Promise.resolve();}}})")
    ui_page.goto(f"{live_server.url}/#grid")
    ui_page.locator("#grid-margin-target").fill("0.7")
    assert ui_page.evaluate("document.querySelector('#grid-form').checkValidity()")
    with ui_page.expect_request(lambda request: "/api/grid/recommend" in request.url):
        ui_page.evaluate("loadGrid({preventDefault(){},target:document.querySelector('#grid-form')})")
    expect(ui_page.locator(".advisor-cascade")).to_contain_text("comisiones")
    expect(ui_page.locator(".advisor-risk")).to_contain_text("Pérdida no realizada estimada al piso")
    assert ui_page.locator("#grid-result .advisor-risk > p.scanner-warning:empty").count() == 0
    expect(ui_page.locator("#grid-result")).to_contain_text("Con este capital por celda el grid no podrá prestar ni añadir niveles; solo podrá reducirlos")
    expect(ui_page.locator("#grid-result .unready-coin-warning")).to_be_visible()
    expect(ui_page.locator(".advisor-sigma-surfaces")).to_contain_text("\u03C3 realizada 30 d: 1.000%")
    expect(ui_page.locator(".advisor-sigma-surfaces")).to_contain_text("\u03C3 campe\u00F3n 24 h: 2.000%")
    expect(ui_page.locator(".advisor-sigma-surfaces")).to_contain_text("\u03C3 campe\u00F3n vigilancia 4 h: 3.000%")
    expect(ui_page.locator(".advisor-simulation")).to_contain_text("histórico, no promesa de resultado")
    expect(ui_page.locator(".advisor-simulation")).to_contain_text("Inicio histórico: 2026-10-01T00:00:00+00:00")
    expect(ui_page.locator(".advisor-simulation")).to_contain_text("Ventana corta")
    expect(ui_page.locator(".advisor-simulation")).to_contain_text("Velas de 5 min, evaluación cada 15 min")
    expect(ui_page.locator(".advisor-simulation")).to_contain_text("Equity final estimada: $1,010.00")
    expect(ui_page.locator(".advisor-simulation")).to_contain_text("P&L neto (USDT): $10.00")
    expect(ui_page.locator(".advisor-simulation")).to_contain_text("Drawdown m\u00e1ximo (%): 1.25%")
    expect(ui_page.locator(".advisor-simulation")).to_contain_text("Comisiones (USDT): $2.50")
    expect(ui_page.locator(".advisor-simulation")).to_contain_text("Comprar y mantener (USDT): $3.75")
    simulation_text=ui_page.locator(".advisor-simulation").inner_text()
    assert "NaN" not in simulation_text
    assert "Equity final estimada: \u2014" not in simulation_text
    expect(ui_page.locator(".advisor-range-mode")).to_contain_text("probabilidad de salir por cada lado en 72 h")
    expect(ui_page.locator("#grid-result")).to_contain_text("Probabilidad de salir del rango (cota superior)")
    expect(ui_page.locator("#grid-result")).to_contain_text("Toque por lado")
    pause_notice=ui_page.locator("#grid-result .advisor-pause-risk")
    assert pause_notice.is_visible() and "nacer\u00EDa pausable" in pause_notice.inner_text()
    assert "horizonte de vigilancia de Smart (4 h)" in pause_notice.inner_text()
    assert ui_page.locator("#grid-result .advisor-profile-risk").evaluate("el => getComputedStyle(el).display") != "none"
    expect(ui_page.locator(".advisor-range-mode")).to_contain_text("La volatilidad está ensanchada ×1.25: la probabilidad real de salir por cada lado es menor que la indicada.")
    expect(ui_page.locator(".advisor-range-mode")).to_contain_text("se prefiere el rango centrado")
    expect(ui_page.locator(".advisor-range-mode")).to_contain_text("Este grid compra solo cuando el precio baja")
    expect(ui_page.locator("#grid-range-mode")).to_have_value("centrado")
    with ui_page.expect_request(lambda request: "/api/grid/recommend" in request.url and "range_mode=estructural" in request.url):
        ui_page.locator("#grid-range-mode").select_option("estructural")
    expect(ui_page.locator(".advisor-range-mode")).to_contain_text("Rango recomendado: Estructural")
    ui_page.locator("#copy-grid").click()
    copied = ui_page.evaluate("window.__copiedText")
    assert "Piso 80.123456" in copied and "Techo 120.987654" in copied
    ui_page.get_by_role("button", name="Crear grid desde esta recomendación").click()
    expect(ui_page.locator("#sc-symbol")).to_have_value("XRPUSDT")
    expect(ui_page.locator("#sc-low")).to_have_value("80.1235")
    assert ui_page.locator("#sc-low").get_attribute("data-exact") == "80.123456"
    expect(ui_page.locator("#sc-high")).to_have_value("120.988")
    assert ui_page.locator("#sc-high").get_attribute("data-exact") == "120.987654"
    expect(ui_page.locator("#sc-levels")).to_have_value("20")
    expect(ui_page.locator("#sc-margin-target")).to_have_value("0.7")
    expect(ui_page.locator("#sc-spacing")).to_have_value("2")
    expect(ui_page.locator("#sc-strategy")).to_have_value("smart")
    expect(ui_page.locator("#sc-create-strategy")).to_have_value("smart")
    ui_page.evaluate("sessionStorage.setItem('asple-advisor-open', JSON.stringify({symbol:'XRPUSDT'}))")
    ui_page.reload()
    expect(ui_page.locator("#sc-strategy")).to_have_value("smart")
    expect(ui_page.locator("#sc-create-strategy")).to_have_value("smart")
    ui_page.evaluate("sessionStorage.setItem('asple-advisor-open', JSON.stringify({symbol:'XRPUSDT',strategy:'simple'}))")
    ui_page.reload()
    expect(ui_page.locator("#sc-strategy")).to_have_value("smart")
    expect(ui_page.locator("#sc-create-strategy")).to_have_value("simple")
    assert_no_js_errors(ui_page)


def test_dashboard_shows_model_stats_accumulating_and_active(live_server, ui_page):
    ui_page.route("**/api/volatility/forecast**", lambda route: route.fulfill(json={
        "symbol": "XRPUSDT", "price": 1.0, "regime": "NORMAL",
        "forecasts": [{"horizon_h": h, "champion": "GBM", "stale": False,
                       "move_1sigma_pct": 1.0, "range_1sigma": [0.99, 1.01],
                       "range_2sigma": [0.98, 1.02]} for h in (1, 2, 4, 24)],
    }))
    model_names = ["Persistence", "EWMA", "HAR", "HAR_range", "HAR_asym", "GBM", "NexoHAR", "GARCH_t"]
    ui_page.route("**/api/volatility/model-stats**", lambda route: route.fulfill(json={
        "symbol": "XRPUSDT", "horizons": [{"symbol": "XRPUSDT", "horizon_h": h, "n_min": 30,
            "adaptive": {"source": "val", "confidence": "baja", "eligible": []},
            "forward": {"n": 30, "n_efectivas": 7.5},
            "dispersion": [{"level": level, "stats": {"n": 0}, "estado": "acumulando"}
                           for level in ("alta", "media", "baja")],
            "dispersion_iqr_error_spearman": None,
            "models": [{"model_name": name, "n_predicciones": 30 if name == "GBM" else 8,
                "n_verificadas": 30 if name == "GBM" else 8,
                "estado": "activo" if name == "GBM" else "acumulando",
                "all": {"success_1sigma": 20, "success_2sigma": 28, "failures": 2,
                        "coverage_1sigma": .667, "coverage_2sigma": .933,
                        "bias_mean": .001, "over_pct": 60.0, "under_pct": 40.0,
                        "mse": .002}, "bias_alert": name == "GBM", "peso_actual": 0.0}
                for name in model_names]} for h in (1, 2, 4, 24)]
    }))
    ui_page.goto(f"{live_server.url}/#dashboard")
    expect(ui_page.locator("#vol-model-stats")).to_contain_text("Modelos y ponderación 4h")
    expect(ui_page.locator("#vol-model-stats")).to_contain_text("GBM")
    expect(ui_page.locator("#vol-model-stats")).to_contain_text("Errores dentro de 1\u03c3 / 2\u03c3 (log-vol; no varianza)")
    expect(ui_page.locator("#vol-model-stats")).to_contain_text("Sobre/Sub %")
    expect(ui_page.locator("#vol-model-stats")).to_contain_text("60.0% / 40.0%")
    expect(ui_page.locator("#vol-model-stats")).to_contain_text("Sesgo sostenido (revisar)")
    expect(ui_page.locator("#vol-model-stats")).to_contain_text("n efectivas=7.5")
    expect(ui_page.locator("#vol-model-stats")).to_contain_text("Activo")
    expect(ui_page.locator("#vol-model-stats")).to_contain_text("Acumulando datos (8/30)")
    expect(ui_page.locator("#vol-model-stats")).to_contain_text("Campeón por dispersión")
    assert_no_js_errors(ui_page)


def test_widen_suggestion_ui_only_applies_available_after_confirmation(live_server, ui_page):
    state = {"available": False, "applied": []}
    ui_page.route("**/api/volatility/forecast**", lambda route: route.fulfill(json={
        "symbol": "XRPUSDT", "price": 1.0, "regime": "NORMAL", "forecasts": []}))
    def stats(route):
        status = "disponible" if state["available"] else "acumulando"
        route.fulfill(json={"symbol": "XRPUSDT", "horizons": [{
            "symbol": "XRPUSDT", "horizon_h": h, "adaptive": {"source": "val", "confidence": "baja"},
            "forward": {"n": 0, "n_efectivas": 0}, "models": [], "dispersion": [],
            "widen_factor": {"horizon_h": h, "status": status, "k_active": 1.25,
                "k_stress_smoothed": 1.38 if state["available"] else 1.31, "k_raw": 1.12,
                "ci_low": 1.1, "ci_high": 1.62, "progress_pct": 62,
                "n": 620, "n_effective": 25.8, "days_estimated": 12,
                "disagreement_status": "acumulando"}
        } for h in (1, 2, 4, 24)]})
    ui_page.route("**/api/volatility/model-stats**", stats)
    def apply(route):
        state["applied"].append(route.request.post_data_json)
        route.fulfill(json={"applied": True})
    ui_page.route("**/api/volatility/widen-factor/apply**", apply)
    ui_page.on("dialog", lambda dialog: dialog.accept())
    ui_page.goto(f"{live_server.url}/#dashboard")
    expect(ui_page.locator("#vol-model-stats")).to_contain_text("Factor de ampliación")
    expect(ui_page.locator("#vol-model-stats")).to_contain_text("provisional")
    assert ui_page.locator("#vol-model-stats .apply-widen-suggestion").count() == 0
    state["available"] = True
    ui_page.reload()
    button = ui_page.locator("#vol-model-stats .apply-widen-suggestion").first
    expect(button).to_be_visible()
    with ui_page.expect_response("**/api/volatility/widen-factor/apply"):
        button.click()
    assert len(state["applied"]) == 1
    assert state["applied"][0] == {"horizon_h": 4, "k": 1.38, "confirm": True}
    assert_no_js_errors(ui_page)


@pytest.mark.parametrize("vol_status", [200, 503], ids=["resultado-realizado", "error-amable"])
def test_grid_advisor_can_calculate_realized_volatility_for_selected_other_symbol(live_server, ui_page, vol_status):
    advisor = {"symbol":"ADAUSDT","current_price":1.2,"recommended_floor":1.0,"recommended_ceiling":1.4,
        "range_pct":33.3,"suggested_grids":8,"capital_per_grid":125,"spacing_pct":5,"fee_pct":.1,
        "dust_estimate_pct":.1,"net_margin_pct":4.7,"net_per_cycle_usdt":5.8,"margin_target_pct":.7,
        "target_met":True,"estimated_cycles_to_target":2,"risk":{},"analysis":{},"simulations":{"strategies":{}},"prediction_signal":None}
    ui_page.route("**/api/grid/recommend**", lambda route: route.fulfill(json=advisor))
    ui_page.route("**/api/coins", lambda route: route.fulfill(json=["XRPUSDT", "ADAUSDT"]))
    volatility_calls = []
    def volatility(route):
        volatility_calls.append(route.request.url)
        if vol_status == 503:
            route.fulfill(status=503, json={"detail":"unavailable"})
        else:
            route.fulfill(json={"symbol":"ADAUSDT","source":"volatilidad realizada ADA 1h","samples":321,
                "range_2sigma":[1.1,1.3]})
    ui_page.route("**/api/grid/volatility**", volatility)
    ui_page.goto(f"{live_server.url}/#grid")
    expect(ui_page.locator("#grid-symbol option[value='ADAUSDT']")).to_have_count(1)
    ui_page.locator("#grid-symbol").select_option("ADAUSDT")
    ui_page.evaluate("loadGrid({preventDefault(){},target:document.querySelector('#grid-form')})")
    expect(ui_page.locator("#calculate-grid-volatility")).to_be_visible()
    ui_page.locator("#calculate-grid-volatility").click()
    if vol_status == 200:
        expect(ui_page.locator("#grid-volatility-status")).to_contain_text("volatilidad realizada ADA 1h")
        expect(ui_page.locator("#grid-volatility-status")).to_contain_text("1.1")
        assert ui_page.locator("#grid-volatility-status").count()==1
        assert ui_page.locator(".vol-grid-reference").locator("text=Rango 2?:").count()==0
    else:
        expect(ui_page.locator("#grid-volatility-status")).to_contain_text("No se pudo calcular")
        expect(ui_page.locator("#grid-result .grid-header")).to_be_visible()
    assert len(volatility_calls) >= 2
    assert all("/api/grid/volatility" in url and "symbol=ADAUSDT" in url for url in volatility_calls)
    assert all("/api/volatility/forecast" not in url for url in volatility_calls)
    assert_no_js_errors(ui_page)


def test_grid_advisor_volatility_button_recalculates_xrp_model_forecast(live_server, ui_page):
    advisor = {"symbol":"XRPUSDT","current_price":1.2,"recommended_floor":1.0,"recommended_ceiling":1.4,
        "range_pct":33.3,"suggested_grids":8,"capital_per_grid":125,"spacing_pct":5,"fee_pct":.1,
        "dust_estimate_pct":.1,"net_margin_pct":4.7,"net_per_cycle_usdt":5.8,"margin_target_pct":.7,
        "target_met":True,"estimated_cycles_to_target":2,"risk":{},"analysis":{},"simulations":{"strategies":{}},"prediction_signal":None,
        "volatility_advisory":{"vol_source_effective":"consenso","vol_source_requested":"auto","confidence":"baja",
            "range_widened":True,"show_comparison":True,"champion_sigma_24h":.03,"consensus_sigma_24h":.04,
            "accumulating_models":[{"model_name":"NexoHAR","n_verificadas":14,"n_min":30}],
            "bias_alerts":[{"model_name":"NexoHAR","direction":"sobreestima"}]}}
    ui_page.route("**/api/grid/recommend**", lambda route: route.fulfill(json=advisor))
    calls=[]
    def forecast(route):
        calls.append(route.request.url)
        route.fulfill(json={"forecasts":[{"horizon_h":24,"range_2sigma":[1.05,1.35],"move_1sigma_pct":3.2}]})
    ui_page.route("**/api/volatility/forecast**", forecast)
    ui_page.goto(f"{live_server.url}/#grid")
    ui_page.evaluate("loadGrid({preventDefault(){},target:document.querySelector('#grid-form')})")
    expect(ui_page.locator("#calculate-grid-volatility")).to_be_visible()
    expect(ui_page.locator("#grid-result")).to_contain_text("consenso")
    expect(ui_page.locator("#grid-result")).to_contain_text("confianza baja")
    expect(ui_page.locator("#grid-result")).to_contain_text("Los modelos discrepan: rango ampliado")
    expect(ui_page.locator("#grid-result")).to_contain_text("Este modelo lleva sesgo sostenido (revisar)")
    expect(ui_page.locator("#grid-result")).to_contain_text("sobreestima")
    expect(ui_page.locator("#grid-result")).to_contain_text("NexoHAR (14/30)")
    expect(ui_page.locator("#grid-result")).to_contain_text("σ campeón: 3.000% · σ consenso: 4.000%")
    ui_page.locator("#calculate-grid-volatility").click()
    expect(ui_page.locator("#grid-volatility-status")).to_contain_text("pron\u00f3stico de modelos XRP")
    expect(ui_page.locator("#grid-volatility-status")).to_contain_text("1.05")
    assert len(calls) >= 2
    assert all("symbol=XRPUSDT" in url for url in calls)
    assert_no_js_errors(ui_page)


def test_scanner_uses_scan_structure_and_exposes_accessible_margin_help(live_server, ui_page):
    ui_page.route("**/api/grids/scan", lambda route: route.fulfill(json={"results":[{
        "symbol":"XRPUSDT","eligible":False,"score":None,"fee_pct":.1,
        "hard_filters":[{"passed":False,"reason":"No elegible: spread supera el máximo."}],
        "components":[],"warnings":[],"suggested_structure":{"range_low":"95","range_high":"105",
            "n_levels":10,"spacing_pct":1,"dust_estimate_pct":.2,"net_edge_pct_per_cycle":.6}}]}))
    ui_page.route("**/api/grids/structure-preview", lambda route: route.fulfill(json={"fee_pct":.1,
        "minimum_margin_after_fees_pct":.7,
        "variants":{"balanced":{"feasible":True,"range_low":"95","range_high":"105","n_levels":10,
            "spacing_pct":1,"cell_usdt":"10","edge_gross_pct":.8,"dust_estimate_pct":.2,
            "edge_after_dust_pct":.6}},"edited":{"feasible":True,"range_low":"95","range_high":"105",
            "n_levels":10,"spacing_pct":1,"dust_estimate_pct":.2,"edge_after_dust_pct":.6}}))
    ui_page.goto(f"{live_server.url}/#scanner")
    ui_page.locator("#sc-run").click()
    expect(ui_page.locator(".scanner-table")).to_contain_text("neto estimado")
    expect(ui_page.locator(".scanner-table thead")).to_contain_text("Elegible")
    assert ui_page.locator(".scanner-table thead th details").count() == 3
    ui_page.get_by_role("button", name="Usar").click()
    expect(ui_page.locator("#sc-low")).to_have_value("95")
    expect(ui_page.locator("#sc-high")).to_have_value("105")
    expect(ui_page.locator("#sc-levels")).to_have_value("10")
    expect(ui_page.locator("#sc-margin-target")).to_have_value("0.70")
    assert_no_js_errors(ui_page)


def test_scanner_compound_open_preview_sends_settings_and_displays_them(live_server, ui_page):
    ui_page.route("**/api/grids/structure-preview", lambda route: route.fulfill(json={
        "fee_pct": .1, "variants": {"balanced": {"feasible": True}},
        "edited": {"feasible": True, "range_low": "90", "range_high": "110",
            "n_levels": 4, "spacing_pct": 5, "edge_gross_pct": 4.8,
            "dust_estimate_pct": .1, "edge_after_dust_pct": 4.7}}))
    ui_page.route("**/api/grids/open", lambda route: route.fulfill(json={
        "dry_run": True, "symbol": "XRPUSDT", "strategy": "simple", "capital": "100",
        "range_low": "90", "range_high": "110", "n_levels": 4,
        "levels": ["90", "95", "100", "105", "110"], "current_price": "101",
        "cell_usdt": "25", "cells": [],
        "sigma_surfaces":{"realized_30d":{"value":.01,"source":"realizada","window":"30 d"},"champion_24h":{"value":.03,"source":"campe\u00f3n","window":"24 h"},"champion_monitor_h":{"value":.02,"source":"campe\u00f3n","window":"4 h"},"monitor_h":4},
        "margin_guard": {"allowed": True, "actual_pct": 4.7, "minimum_pct": .7}})
        if route.request.method == "POST" else route.continue_())
    ui_page.goto(f"{live_server.url}/#scanner")
    ui_page.locator("#sc-low").fill("90")
    ui_page.locator("#sc-high").fill("110")
    ui_page.locator("#sc-levels").fill("4")
    ui_page.locator("#sc-compound-enabled").check()
    ui_page.locator("#sc-compound-ratio").fill("35")
    ui_page.locator("#sc-compound-cap").fill("60")
    expect(ui_page.locator("#sc-edited")).to_contain_text("Viable")
    with ui_page.expect_request(lambda request: request.method == "POST" and request.url.endswith("/api/grids/open")) as open_request:
        ui_page.locator("#sc-preview-open").click()
    body = json.loads(open_request.value.post_data)
    assert body["params"] == {"compound_enabled": True, "compound_ratio": 0.35, "compound_max_growth_pct": 60}
    graph = ui_page.locator("#sc-dialog .grid-svg")
    expect(graph).to_be_visible()
    expect(graph).to_contain_text("Techo")
    expect(graph).to_contain_text("Piso")
    expect(graph).to_contain_text("Precio actual 101.00 (mercado p\u00fablico)")
    expect(graph).to_contain_text("4 niveles")
    expect(graph).to_contain_text("escala lineal USDT")
    expect(ui_page.locator("#sc-dialog")).to_contain_text("Inter\u00e9s compuesto: reinvertir 35%")
    expect(ui_page.locator(".dry-run-sigma-surfaces")).to_contain_text("\u03C3 realizada 30 d: 1.000%")
    expect(ui_page.locator(".dry-run-sigma-surfaces")).to_contain_text("\u03C3 campe\u00F3n 24 h: 3.000%")
    expect(ui_page.locator(".dry-run-sigma-surfaces")).to_contain_text("\u03C3 campe\u00F3n vigilancia 4 h: 2.000%")


def test_scanner_dialog_displays_server_order_count_and_outside_signed_distances(live_server, ui_page):
    ui_page.route("**/api/grids/structure-preview", lambda route: route.fulfill(json={
        "fee_pct":.1,"variants":{"balanced":{"feasible":True}},
        "edited":{"feasible":True,"range_low":"90","range_high":"110","n_levels":4,
            "spacing_pct":5,"edge_gross_pct":4.8,"dust_estimate_pct":.1,"edge_after_dust_pct":4.7}}))
    ui_page.route("**/api/grids/open", lambda route: route.fulfill(json={
        "dry_run":True,"symbol":"XRPUSDT","strategy":"simple","capital":"100",
        "range_low":"90","range_high":"110","n_levels":4,"levels":["90","95","100","105","110"],
        "current_price":"120","price_in_range":False,"distance_to_floor_pct":"25",
        "distance_to_ceiling_pct":"-8.333","initial_order_count":2,"cell_usdt":"25","cells":[],
        "margin_guard":{"allowed":True,"actual_pct":4.7,"minimum_pct":.7}})
        if route.request.method == "POST" else route.continue_())
    ui_page.goto(f"{live_server.url}/#scanner")
    ui_page.locator("#sc-low").fill("90"); ui_page.locator("#sc-high").fill("110")
    ui_page.locator("#sc-levels").fill("4"); ui_page.locator("#sc-preview-open").click()
    expect(ui_page.locator("#sc-dialog")).to_contain_text("2 órdenes iniciales")
    expect(ui_page.locator("#sc-dialog")).to_contain_text("Precio fuera del rango")
    expect(ui_page.locator("#sc-dialog")).to_contain_text("25,000 %")
    expect(ui_page.locator("#sc-dialog")).to_contain_text("-8,333 %")


@pytest.mark.parametrize(
    ("case", "testnet_price", "in_range", "allowed"),
    [
        ("inside", "0.0000045", True, True),
        ("outside", "0.0000052", False, False),
        ("unavailable", None, None, None),
    ],
    ids=["inside", "outside", "unavailable"],
)
def test_scanner_testnet_price_guard(case, testnet_price, in_range, allowed, live_server, ui_page):
    reason = (
        "Precio Testnet 0.0000052 fuera del rango [0.00000393 – 0.00000491]; precio público 0.0000044."
        if case == "outside"
        else "No se pudo leer el precio de Testnet; la apertura puede fallar."
    )
    ui_page.route("**/api/grids/structure-preview", lambda route: route.fulfill(json={
        "fee_pct": .1, "variants": {"balanced": {"feasible": True}},
        "edited": {"feasible": True, "range_low": "0.00000393", "range_high": "0.00000491",
            "n_levels": 18, "spacing_pct": 1, "edge_gross_pct": .8,
            "dust_estimate_pct": .1, "edge_after_dust_pct": .7}}))
    ui_page.route("**/api/grids/open", lambda route: route.fulfill(json={
        "dry_run": True, "symbol": "PEPEUSDT", "strategy": "simple", "capital": "100",
        "range_low": "0.00000393", "range_high": "0.00000491", "n_levels": 18,
        "levels": ["0.00000393", "0.00000491"], "current_price": "0.0000044",
        "testnet_price": testnet_price, "testnet_in_range": in_range,
        "testnet_price_guard": {"allowed": allowed, "reason": None if allowed is True else reason},
        "cell_usdt": "5.55", "cells": [],
        "margin_guard": {"allowed": True, "actual_pct": .8, "minimum_pct": .7}})
        if route.request.method == "POST" else route.continue_())
    ui_page.goto(f"{live_server.url}/#scanner")
    ui_page.locator("#sc-symbol").select_option("XRPUSDT")
    ui_page.locator("#sc-low").fill("0.00000393")
    ui_page.locator("#sc-high").fill("0.00000491")
    ui_page.locator("#sc-levels").fill("18")
    ui_page.locator("#sc-preview-open").click()
    dialog = ui_page.locator("#sc-dialog")
    expect(dialog).to_contain_text("Precio público: 0.0000044")
    if testnet_price is None:
        expect(dialog).to_contain_text("Precio Testnet: no disponible")
        expect(dialog).to_contain_text(reason)
    else:
        expect(dialog).to_contain_text(f"Precio Testnet: {testnet_price}")
    confirm = dialog.get_by_role("button", name="Confirmar apertura Testnet")
    assert confirm.is_disabled() is (case == "outside")
    if case == "outside":
        expect(dialog).to_contain_text("Apertura bloqueada: el precio de Testnet (0.0000052) está fuera del rango")
        expect(dialog).to_contain_text("Esta moneda cotiza distinto en Testnet; elige otra moneda o ajusta el rango.")
    assert_no_js_errors(ui_page)


def test_scanner_translates_testnet_strict_range_error_on_confirm(live_server, ui_page):
    ui_page.route("**/api/grids/structure-preview", lambda route: route.fulfill(json={
        "fee_pct": .1, "variants": {"balanced": {"feasible": True}},
        "edited": {"feasible": True, "range_low": "0.00000393", "range_high": "0.00000491",
            "n_levels": 18, "spacing_pct": 1, "edge_gross_pct": .8,
            "dust_estimate_pct": .1, "edge_after_dust_pct": .7}}))

    def open_response(route):
        if route.request.method != "POST":
            route.continue_()
        elif route.request.post_data_json.get("dry_run"):
            route.fulfill(json={"dry_run": True, "symbol": "PEPEUSDT", "strategy": "simple",
                "capital": "100", "range_low": "0.00000393", "range_high": "0.00000491",
                "n_levels": 18, "levels": [], "current_price": "0.0000044",
                "testnet_price": "0.0000045", "testnet_in_range": True,
                "testnet_price_guard": {"allowed": True, "reason": None},
                "cell_usdt": "5.55", "cells": [],
                "margin_guard": {"allowed": True, "actual_pct": .8, "minimum_pct": .7}})
        else:
            route.fulfill(status=422, json={"detail": "exchange mid price must be strictly inside the grid range"})

    ui_page.route("**/api/grids/open", open_response)
    ui_page.goto(f"{live_server.url}/#scanner")
    ui_page.locator("#sc-low").fill("0.00000393")
    ui_page.locator("#sc-high").fill("0.00000491")
    ui_page.locator("#sc-levels").fill("18")
    ui_page.locator("#sc-preview-open").click()
    ui_page.locator("#sc-dialog [data-confirm]").click()
    alert = ui_page.locator("#sc-dialog [role=alert]")
    expect(alert).to_contain_text("El precio de Testnet está fuera del rango del grid; vuelve a calcular la vista previa.")
    expect(alert).to_contain_text("Precio Testnet: 0.0000045; precio público: 0.0000044")
    expect(alert).to_contain_text("rango [0.00000393 – 0.00000491]")
    assert "strictly inside" not in alert.inner_text()
    assert_no_js_errors(ui_page)


def test_scanner_shows_cell_minimum_and_dust_size_in_panel_and_dialog(live_server, ui_page):
    warning = "Mínimo típico de 5 USDT; no verificado en Testnet."
    ui_page.route("**/api/grids/structure-preview", lambda route: route.fulfill(json={
        "fee_pct": .1, "variants": {"balanced": {"feasible": True}},
        "edited": {"feasible": True, "range_low": "90", "range_high": "110",
            "n_levels": 30, "spacing_pct": .66, "edge_gross_pct": .46,
            "dust_estimate_pct": .1, "edge_after_dust_pct": .36,
            "minimum_cell_usdt": "5.5", "min_cell_warning": warning,
            "dust_target_pct": .1, "dust_min_cell_usdt": "149.125"}}))
    ui_page.route("**/api/grids/open", lambda route: route.fulfill(json={
        "dry_run": True, "symbol": "XRPUSDT", "strategy": "simple", "capital": "100",
        "range_low": "90", "range_high": "110", "n_levels": 30,
        "levels": [str(90 + i * (20 / 30)) for i in range(31)], "current_price": "101",
        "cell_usdt": "3.33", "cells": [], "min_cell_warning": warning,
        "minimum_cell_usdt": "5.5", "dust_target_pct": .1, "dust_min_cell_usdt": "149.125",
        "margin_guard": {"allowed": True, "actual_pct": .36, "minimum_pct": .7}})
        if route.request.method == "POST" else route.continue_())
    ui_page.goto(f"{live_server.url}/#scanner")
    ui_page.locator("#sc-low").fill("90")
    ui_page.locator("#sc-high").fill("110")
    ui_page.locator("#sc-levels").fill("30")
    expect(ui_page.locator("#sc-edited")).to_contain_text(warning)
    expect(ui_page.locator("#sc-edited")).to_contain_text("Tamaño mínimo estimado de celda para polvo")
    expect(ui_page.locator("#sc-edited")).to_contain_text("$149,13")
    ui_page.locator("#sc-preview-open").click()
    expect(ui_page.locator("#sc-dialog")).to_contain_text(warning)
    expect(ui_page.locator("#sc-dialog")).to_contain_text("Tamaño mínimo estimado de celda para polvo")
    expect(ui_page.locator("#sc-dialog")).to_contain_text("$149,13")


@pytest.mark.parametrize(
    ("bid", "positive"),
    [(100, True), (80, False)],
    ids=["ganancia", "perdida"],
)
def test_liquidate_requires_typed_confirmation_and_posts_preview_first(
    live_server, ui_page, bid, positive
):
    live_server.exchange.bid = live_server.exchange.avg.__class__(str(bid))
    live_server.exchange.ask = live_server.exchange.avg.__class__(str(bid + 0.01))
    live_server.exchange.avg = live_server.exchange.avg.__class__(str(bid))
    _open_close_dialog(ui_page, live_server)
    ui_page.locator('[name="close-mode"][value="liquidate"]').check()
    health_before = (ui_page.locator("#api-chip").get_attribute("class"),
                     ui_page.locator("#api-chip").inner_text())

    requests = []
    ui_page.on("request", lambda request: requests.append(request)
               if _is_close_post(request, live_server.grid_id) else None)
    with ui_page.expect_request(lambda request: _is_close_post(request, live_server.grid_id)) as preview_event:
        ui_page.get_by_role("button", name="Continuar", exact=True).click()
    preview = json.loads(preview_event.value.post_data)
    assert preview["dry_run"] is True and preview["confirm"] is False
    expect(ui_page.locator("#grid-action-title")).to_have_text("Revisar plan")

    result_label = ui_page.locator(".grid-action-body span.pnl-positive, .grid-action-body span.pnl-negative")
    expect(result_label).to_be_visible()
    text = result_label.inner_text()
    if positive:
        assert "GANANCIA" in text and "P" not in text
    else:
        assert "PÉRDIDA" in text and "GANANCIA" not in text

    confirm = ui_page.get_by_role("button", name="Continuar", exact=True)
    confirm.click()
    expect(ui_page.locator(".grid-action-error")).to_contain_text("LIQUIDAR")
    assert len(requests) == 1, "sin LIQUIDAR no debe enviarse el POST de ejecución"

    ui_page.locator('[name="liquidate-confirm"]').fill("LIQUIDAR")
    with ui_page.expect_request(lambda request: _is_close_post(request, live_server.grid_id)) as execute_event:
        confirm.click()
    execution = json.loads(execute_event.value.post_data)
    assert execution["dry_run"] is False and execution["confirm"] is True
    assert execution["confirm_text"] == "LIQUIDAR"
    expect(ui_page.locator("#grid-action-title")).to_have_text("Acción completada")
    assert health_before == (ui_page.locator("#api-chip").get_attribute("class"),
                             ui_page.locator("#api-chip").inner_text())
    assert_no_js_errors(ui_page)


def test_completed_action_result_is_translated_and_closable(live_server, ui_page):
    _open_close_dialog(ui_page, live_server)
    with ui_page.expect_request(lambda request: _is_close_post(request, live_server.grid_id)):
        ui_page.get_by_role("button", name="Continuar", exact=True).click()
    expect(ui_page.locator("#grid-action-title")).to_have_text("Revisar plan")
    with ui_page.expect_request(lambda request: _is_close_post(request, live_server.grid_id)):
        ui_page.get_by_role("button", name="Continuar", exact=True).click()
    expect(ui_page.locator("#grid-action-title")).to_have_text("Acción completada")
    expect(ui_page.locator(".grid-action-body")).to_contain_text("Estado: Cerrado")
    close = ui_page.locator("#grid-action-dialog").get_by_role("button", name="Cerrar", exact=True)
    expect(close).to_be_visible()
    close.click()
    expect(ui_page.get_by_role("dialog")).to_be_hidden()
    controls = ui_page.locator("[data-grid-controls] button")
    for index in range(controls.count()):
        expect(controls.nth(index)).to_be_enabled()


def test_incomplete_action_result_uses_close_button_and_escape(live_server, ui_page):
    def partial_close(route):
        body = json.loads(route.request.post_data)
        if body.get("dry_run"):
            route.continue_()
        else:
            route.fulfill(json={"outcome": "partial", "status_after": "CLOSING",
                                "errors": ["orden pendiente"]})

    ui_page.route(f"**/api/grids/{live_server.grid_id}/close", partial_close)
    _open_close_dialog(ui_page, live_server)
    with ui_page.expect_request(lambda request: _is_close_post(request, live_server.grid_id)):
        ui_page.get_by_role("button", name="Continuar", exact=True).click()
    expect(ui_page.locator("#grid-action-title")).to_have_text("Revisar plan")
    with ui_page.expect_request(lambda request: _is_close_post(request, live_server.grid_id)):
        ui_page.get_by_role("button", name="Continuar", exact=True).click()
    expect(ui_page.locator("#grid-action-title")).to_have_text("Acción incompleta")
    expect(ui_page.locator(".grid-action-body")).to_contain_text("Cerrando")
    expect(ui_page.locator("#grid-action-dialog").get_by_role("button", name="Cerrar", exact=True)).to_be_visible()
    ui_page.keyboard.press("Escape")
    expect(ui_page.get_by_role("dialog")).to_be_hidden()
    expect(ui_page.locator("[data-grid-controls] button").first).to_be_enabled()


def test_action_api_error_result_uses_close_button_and_escape(live_server, ui_page):
    ui_page.route(f"**/api/grids/{live_server.grid_id}/close",
        lambda route: route.fulfill(status=500, json={"detail": "fallo simulado"}))
    _open_close_dialog(ui_page, live_server)
    with ui_page.expect_request(lambda request: _is_close_post(request, live_server.grid_id)):
        ui_page.get_by_role("button", name="Continuar", exact=True).click()
    expect(ui_page.locator("#grid-action-title")).to_have_text("Error de acción")
    expect(ui_page.locator("#grid-action-dialog").get_by_role("button", name="Cerrar", exact=True)).to_be_visible()
    ui_page.keyboard.press("Escape")
    expect(ui_page.get_by_role("dialog")).to_be_hidden()
    expect(ui_page.locator("[data-grid-controls] button").first).to_be_enabled()


def test_profit_repository_preview_shows_counts_gain_and_estimated_fee(live_server, ui_page):
    level = next(row for row in live_server.db.get_grid_levels(live_server.grid_id)
                 if row["state"] == "SELL_OPEN")
    live_server.db.update_level(live_server.grid_id, int(level["level_idx"]),
                                buy_client_order_id=None, client_order_id=None)
    _open_close_dialog(ui_page, live_server)
    ui_page.locator('[name="close-mode"][value="profit_repository"]').check()
    with ui_page.expect_request(lambda request: _is_close_post(request, live_server.grid_id)) as preview_event:
        ui_page.get_by_role("button", name="Continuar", exact=True).click()
    assert json.loads(preview_event.value.post_data)["dry_run"] is True
    body = ui_page.locator(".grid-action-body")
    expect(body).to_contain_text("celdas a vender")
    expect(body).to_contain_text("ganancia neta estimada")
    expect(body).to_contain_text("al repositorio")
    expect(body).to_contain_text(re.compile(r"\d+ celdas con comisión estimada"))
    expect(ui_page.get_by_role("dialog")).to_be_visible()
    assert_no_js_errors(ui_page)


@pytest.mark.parametrize("dismiss", ["button", "escape"])
def test_cancel_and_escape_close_dialog_and_reenable_controls(live_server, ui_page, dismiss):
    _open_close_dialog(ui_page, live_server)
    controls = ui_page.locator("[data-grid-controls] button")
    assert controls.count() > 0
    for index in range(controls.count()):
        expect(controls.nth(index)).to_be_disabled()
    if dismiss == "button":
        ui_page.get_by_role("button", name="Cancelar", exact=True).click()
    else:
        ui_page.keyboard.press("Escape")
    expect(ui_page.get_by_role("dialog")).to_be_hidden()
    for index in range(controls.count()):
        expect(controls.nth(index)).to_be_enabled()


def test_403_grid_auth_shows_token_field_then_retries(live_server, ui_page):
    live_server.settings.grid_api_token = "ui-fixture-token"
    ui_page.goto(f"{live_server.url}/#grids")
    expect(ui_page.locator("#grids-api-token")).to_be_visible()
    ui_page.locator("#grids-api-token").fill("ui-fixture-token")
    ui_page.get_by_role("button", name="Reintentar", exact=True).click()
    expect(ui_page.locator(".grid-row-card").first).to_be_visible()
    assert_no_js_errors(ui_page)


def test_plan_html_is_escaped_in_the_real_dialog(live_server, ui_page):
    payload = "<script>window.__uiInjected = true</script>"

    def patch_plan(route):
        response = route.fetch()
        body = response.json()
        body["plan"]["sell_gain_usdt"] = payload
        route.fulfill(response=response, json=body)

    ui_page.route(f"**/api/grids/{live_server.grid_id}/close", patch_plan)
    _open_close_dialog(ui_page, live_server)
    ui_page.locator('[name="close-mode"][value="profit_repository"]').check()
    ui_page.get_by_role("button", name="Continuar", exact=True).click()
    body = ui_page.locator(".grid-action-body")
    expect(body).to_contain_text(payload)
    assert body.locator("script").count() == 0
    assert ui_page.evaluate("window.__uiInjected === true") is False


def test_account_screen_shows_write_only_credentials_and_refreshes(live_server, ui_page):
    ui_page.goto(f"{live_server.url}/#cuenta")
    content = ui_page.locator("#cuenta-content")
    expect(content.locator("h2").nth(0)).to_contain_text("Conexi")
    expect(content.locator("h2").nth(1)).to_have_text("Balance Testnet")
    expect(content.locator("h2").nth(2)).to_have_text("Ganancias")
    expect(content.locator("h2").nth(3)).to_contain_text("Conciliaci")
    assert ui_page.locator("#testnet-api-key").is_visible()
    assert ui_page.locator("#testnet-api-secret").is_visible()
    with ui_page.expect_request(f"**/api/account/connection"):
        ui_page.get_by_role("button", name="Actualizar", exact=True).click()
    assert_no_js_errors(ui_page)


def test_account_credentials_are_cleared_from_dom_after_submission(live_server, ui_page):
    secret_key, secret_value = "private-key-sentinel", "private-secret-sentinel"
    requests = []
    ui_page.route("**/api/account/testnet-credentials", lambda route: (requests.append(route.request.post_data_json),
        route.fulfill(status=200, json={"saved": True, "key_suffix": "inel", "message": "Reinicia el servidor para aplicar el cambio."})))
    ui_page.on("dialog", lambda dialog: dialog.accept())
    ui_page.goto(f"{live_server.url}/#cuenta")
    ui_page.locator("#testnet-api-key").fill(secret_key)
    ui_page.locator("#testnet-api-secret").fill(secret_value)
    ui_page.locator("#testnet-credentials-confirm").check()
    ui_page.get_by_role("button", name="Cambiar claves de Testnet").click()
    expect(ui_page.locator("#testnet-credentials-status")).to_contain_text("Reinicia el servidor")
    assert requests == [{"api_key": secret_key, "api_secret": secret_value}]
    assert ui_page.locator("#testnet-api-key").input_value() == ""
    assert ui_page.locator("#testnet-api-secret").input_value() == ""
    assert secret_key not in ui_page.locator("#screen-cuenta").inner_text()
    assert secret_value not in ui_page.locator("#screen-cuenta").inner_text()
    assert_no_js_errors(ui_page)


def test_account_500_state_is_visible_without_breaking_page(live_server, ui_page):
    ui_page.route("**/api/account/summary", lambda route: route.fulfill(
        status=500, json={"detail": "fixture account failure"}))
    ui_page.goto(f"{live_server.url}/#cuenta")
    expect(ui_page.locator("#cuenta-content .cuenta-error")).to_contain_text("No se pudo cargar la cuenta")
    assert_no_js_errors(ui_page)


def test_grids_scanner_account_navigation_has_no_js_errors_and_preserves_health_chip(live_server, ui_page):
    ui_page.goto(f"{live_server.url}/#grids")
    expect(ui_page.locator(".grid-row-card").first).to_be_visible()
    ui_page.locator('[data-route="scanner"]').click()
    expect(ui_page.locator("#scanner-root h1")).to_have_text("Scanner")
    expect(ui_page.locator("#sc-variants .scanner-variant").first).to_be_visible(timeout=10000)
    ui_page.locator('[data-route="cuenta"]').click()
    expect(ui_page.locator("#cuenta-content h2").first).to_be_visible()
    ui_page.locator('[data-route="grids"]').click()
    expect(ui_page.locator(".grid-row-card").first).to_be_visible()
    ui_page.locator(".grid-row-card").first.click()
    expect(ui_page.locator("[data-grid-controls]")).to_be_visible()

    chip_before = (ui_page.locator("#api-chip").get_attribute("class"),
                   ui_page.locator("#api-chip").inner_text())
    _close_button(ui_page).click()
    expect(ui_page.get_by_role("dialog")).to_be_visible()
    chip_after = (ui_page.locator("#api-chip").get_attribute("class"),
                  ui_page.locator("#api-chip").inner_text())
    assert chip_after == chip_before
    assert_no_js_errors(ui_page)


def _mock_scanner_coin_readiness(ui_page):
    ui_page.route("**/api/coins", lambda route: route.fulfill(json=[
        {"symbol": "XRPUSDT", "ready": True, "readiness": {"state": "lista"}},
        {"symbol": "ADAUSDT", "ready": False, "readiness": {"state": "entrenando"}},
        {"symbol": "GRAMUSDT", "ready": False,
         "readiness": {"state": "datos_insuficientes", "history_days": 99}},
    ]))


def test_scanner_disables_unready_coin_only_in_create_selector(live_server, ui_page):
    _mock_scanner_coin_readiness(ui_page)
    ui_page.goto(f"{live_server.url}/#scanner")
    ada = ui_page.locator("#sc-symbol option[value='ADAUSDT']")
    assert ada.evaluate("option => option.disabled") is True
    expect(ada).to_have_text("ADAUSDT (preparando)")
    expect(ui_page.locator("#sc-symbol option[value='XRPUSDT']")).to_be_enabled()
    gram = ui_page.locator("#sc-symbol option[value='GRAMUSDT']")
    expect(gram).to_be_enabled()
    expect(gram).to_have_text("GRAMUSDT (sin modelo)")


def test_scanner_data_insufficient_ack_gates_preview_and_confirmation(live_server, ui_page):
    coins = [
        {"symbol": "XRPUSDT", "ready": True, "readiness": {"state": "lista"}},
        {"symbol": "GRAMUSDT", "ready": False,
         "readiness": {"state": "datos_insuficientes", "history_days": 99}},
        {"symbol": "NOHISTORYUSDT", "ready": False,
         "readiness": {"state": "datos_insuficientes"}},
        {"symbol": "ADAUSDT", "ready": False, "readiness": {"state": "entrenando"}},
    ]
    ui_page.route("**/api/coins", lambda route: route.fulfill(json=coins))
    ui_page.route("**/api/grids/structure-preview", lambda route: route.fulfill(json={
        "fee_pct": .1, "variants": {"balanced": {"feasible": True}},
        "edited": {"feasible": True, "range_low": "90", "range_high": "110",
            "n_levels": 4, "spacing_pct": 5, "edge_gross_pct": 4.8,
            "dust_estimate_pct": .1, "edge_after_dust_pct": 4.7}}))
    warning = "Aviso <img src=x onerror=alert(1)> & datos"
    requests = []
    def open_route(route):
        if route.request.method != "POST":
            route.continue_()
            return
        body = route.request.post_data_json
        requests.append(body)
        if body.get("dry_run"):
            route.fulfill(json={"dry_run": True, "symbol": body["symbol"], "strategy": "smart",
                "capital": "100", "range_low": "90", "range_high": "110", "n_levels": 4,
                "levels": ["90", "95", "100", "105", "110"], "current_price": "100",
                "testnet_price": "100", "testnet_in_range": True, "price_in_range": True,
                "cell_usdt": "25", "cells": [], "unready_coin_warning": warning,
                "testnet_price_guard": {"allowed": True, "reason": None},
                "margin_guard": {"allowed": True, "actual_pct": 4.7, "minimum_pct": .7}})
        else:
            route.fulfill(json={"status": "ACTIVE", "grid_id": 4242})
    ui_page.route("**/api/grids/open", open_route)
    ui_page.goto(f"{live_server.url}/#scanner")
    selector = ui_page.locator("#sc-symbol")
    preview_button = ui_page.locator("#sc-preview-open")
    training = selector.locator("option[value='ADAUSDT']")
    assert training.evaluate("option => option.disabled") is True
    selector.select_option("NOHISTORYUSDT")
    expect(ui_page.locator("#sc-unready-ack-container")).to_contain_text("historial — días de 540 requeridos")
    expect(preview_button).to_be_disabled()

    selector.select_option("GRAMUSDT")
    acknowledgement = ui_page.locator("#sc-unready-ack")
    expect(acknowledgement).to_be_visible()
    expect(ui_page.locator("#sc-unready-ack-container")).to_contain_text("99 días de 540 requeridos")
    expect(ui_page.locator("#sc-unready-ack-container")).to_contain_text("Entiendo que esta moneda no tiene modelo")
    expect(ui_page.locator("#sc-unready-ack-container")).to_contain_text("volatilidad realizada y no está validado")
    expect(preview_button).to_be_disabled()
    selector.select_option("XRPUSDT")
    expect(acknowledgement).to_be_hidden()
    selector.select_option("GRAMUSDT")
    expect(acknowledgement).to_be_visible()
    assert acknowledgement.is_checked() is False
    expect(preview_button).to_be_disabled()

    ui_page.locator("#sc-low").fill("90")
    ui_page.locator("#sc-high").fill("110")
    ui_page.locator("#sc-levels").fill("4")
    acknowledgement.check()
    expect(preview_button).to_be_enabled()
    with ui_page.expect_request(lambda request: request.method == "POST" and request.url.endswith("/api/grids/open")) as dry_run:
        preview_button.click()
    assert json.loads(dry_run.value.post_data)["allow_unready_coin"] is True
    assert json.loads(dry_run.value.post_data)["dry_run"] is True
    dialog = ui_page.locator("#sc-dialog")
    expect(dialog).to_contain_text(warning)
    assert dialog.locator("img").count() == 0
    confirm = dialog.locator("[data-confirm]")
    expect(confirm).to_be_enabled()
    ui_page.evaluate("""() => { const input=document.querySelector('#sc-unready-ack'); input.checked=false; input.dispatchEvent(new Event('change',{bubbles:true})); }""")
    expect(confirm).to_be_disabled()
    ui_page.evaluate("""() => { const input=document.querySelector('#sc-unready-ack'); input.checked=true; input.dispatchEvent(new Event('change',{bubbles:true})); }""")
    expect(confirm).to_be_enabled()
    with ui_page.expect_request(lambda request: request.method == "POST" and request.url.endswith("/api/grids/open")) as confirmed:
        confirm.click()
    confirm_body = json.loads(confirmed.value.post_data)
    assert confirm_body["allow_unready_coin"] is True
    assert confirm_body["dry_run"] is False and confirm_body["confirm"] is True
    dialog.locator("[data-close]").click()

    selector.select_option("XRPUSDT")
    expect(acknowledgement).to_be_hidden()
    expect(preview_button).to_be_enabled()
    with ui_page.expect_request(lambda request: request.method == "POST" and request.url.endswith("/api/grids/open")) as ready_preview:
        preview_button.click()
    ready_body = json.loads(ready_preview.value.post_data)
    assert ready_body["dry_run"] is True
    assert "allow_unready_coin" not in ready_body
    assert_no_js_errors(ui_page)


def test_scanner_coin_selector_fails_open_without_readiness_information(live_server, ui_page):
    ui_page.route("**/api/coins", lambda route: route.fulfill(json=[
        {"symbol": "XRPUSDT"},
        {"symbol": "UNKNOWNUSDT"},
        {"symbol": "BLOCKEDUSDT", "ready": False},
        {"symbol": "TRAININGUSDT", "readiness": {"state": "entrenando"}},
        {"symbol": "READYUSDT", "readiness": {"state": "lista"}},
    ]))
    ui_page.goto(f"{live_server.url}/#scanner")
    selector = ui_page.locator("#sc-symbol")
    for symbol in ("XRPUSDT", "UNKNOWNUSDT", "READYUSDT"):
        assert selector.locator(f"option[value='{symbol}']").is_enabled()
    blocked = selector.locator("option[value='BLOCKEDUSDT']")
    assert blocked.evaluate("option => option.disabled") is True
    expect(blocked).to_have_text("BLOCKEDUSDT (no lista)")
    training = selector.locator("option[value='TRAININGUSDT']")
    assert training.evaluate("option => option.disabled") is True
    expect(training).to_have_text("TRAININGUSDT (preparando)")


def test_scanner_surfaces_server_409_detail_verbatim(live_server, ui_page):
    _mock_scanner_coin_readiness(ui_page)
    ui_page.route("**/api/grids/structure-preview", lambda route: route.fulfill(json={
        "fee_pct": .1, "variants": {},
        "edited": {"feasible": True, "range_low": "90", "range_high": "110",
            "n_levels": 4, "spacing_pct": 5, "edge_gross_pct": 4.8,
            "dust_estimate_pct": .1, "edge_after_dust_pct": 4.7},
    }))
    detail = "La moneda ADAUSDT aún no está lista: entrenando. Espera a que termine la preparación."
    def open_route(route):
        body = route.request.post_data_json
        if body.get("dry_run"):
            route.fulfill(json={"dry_run": True, "symbol": "XRPUSDT", "strategy": "simple",
                "capital": "100", "range_low": "90", "range_high": "110", "n_levels": 4,
                "levels": [], "current_price": "100", "testnet_price": "100",
                "price_in_range": True, "testnet_in_range": True,
                "testnet_price_guard": {"allowed": True, "reason": None}, "cells": [],
                "margin_guard": {"allowed": True, "actual_pct": 4.7, "minimum_pct": .7}})
        else:
            route.fulfill(status=409, json={"detail": detail})
    ui_page.route("**/api/grids/open", open_route)
    ui_page.goto(f"{live_server.url}/#scanner")
    ui_page.locator("#sc-low").fill("90")
    ui_page.locator("#sc-high").fill("110")
    ui_page.locator("#sc-levels").fill("4")
    ui_page.locator("#sc-preview-open").click()
    confirm = ui_page.locator("#sc-dialog [data-confirm]")
    expect(confirm).to_be_enabled()
    confirm.click()
    alert = ui_page.locator("#sc-dialog [role=alert]")
    expect(alert).to_have_text(detail)
    expect(confirm).to_be_enabled()


def test_scanner_ranking_keeps_unready_coins_visible(live_server, ui_page):
    _mock_scanner_coin_readiness(ui_page)
    ui_page.route("**/api/grids/scan", lambda route: route.fulfill(json={"results": [{
        "symbol": "ADAUSDT", "eligible": False, "score": .42, "fee_pct": .1,
        "hard_filters": [{"passed": False, "reason": "Moneda en preparación"}],
        "components": [], "warnings": [],
        "suggested_structure": {"range_low": "90", "range_high": "110", "n_levels": 4,
            "spacing_pct": 5, "net_edge_pct_per_cycle": .6},
    }]}))
    ui_page.goto(f"{live_server.url}/#scanner")
    ui_page.locator("#sc-run").click()
    expect(ui_page.locator(".scanner-table tbody")).to_contain_text("ADAUSDT")
    expect(ui_page.locator(".scanner-table tbody")).to_contain_text("Moneda en preparación")


def test_shared_testnet_notice_is_visible_only_on_relevant_screens(live_server, ui_page):
    ui_page.goto(f"{live_server.url}/#dashboard")
    notice = ui_page.locator("#testnet-notice")
    assert notice.is_visible()
    assert "mercado público real" in notice.inner_text()
    for screen in ("#scanner", "#grid", "#grids", "#cuenta"):
        ui_page.evaluate("screen => { location.hash = screen; }", screen)
        ui_page.wait_for_timeout(100)
        assert notice.is_visible(), screen
    for screen in ("#battle", "#coins"):
        ui_page.evaluate("screen => { location.hash = screen; }", screen)
        ui_page.wait_for_timeout(100)
        assert not notice.is_visible(), screen


def test_dashboard_symbol_uses_active_registry_and_resets_removed_selection(live_server, ui_page):
    live_server.db.add_or_reactivate_coin("ADAUSDT")
    ui_page.goto(f"{live_server.url}/#dashboard")
    symbol = ui_page.locator("#dashboard-symbol")
    expect(symbol.locator('option[value="ADAUSDT"]')).to_have_count(1)
    symbol.select_option("ADAUSDT")
    expect(symbol).to_have_value("ADAUSDT")

    live_server.db.deactivate_coin("ADAUSDT")
    ui_page.get_by_role("button", name="Actualizar", exact=True).click()
    expect(symbol).to_have_value("XRPUSDT")

    assert ui_page.locator('#dashboard-symbol option[value="BTCUSDT"]').count() == 0
    assert_no_js_errors(ui_page)


def test_topbar_background_is_opaque_while_scrolling_over_content(live_server, ui_page):
    ui_page.goto(f"{live_server.url}/#dashboard")
    ui_page.evaluate("""() => {
      const under = document.createElement('div');
      under.id = 'topbar-underlay-test';
      under.textContent = 'Texto de contenido bajo la barra';
      Object.assign(under.style, {position:'absolute',top:'100px',left:'0',height:'20px',zIndex:'0'});
      document.body.style.minHeight = '2000px';
      document.body.append(under);
      window.scrollTo(0,75);
    }""")
    result = ui_page.evaluate("""() => {
      const bar = document.querySelector('.topbar');
      const background = getComputedStyle(bar).backgroundColor;
      const under = document.querySelector('#topbar-underlay-test').getBoundingClientRect();
      const barRect = bar.getBoundingClientRect();
      return {background, opacity: background.startsWith('rgba(') ? Number(background.match(/,\\s*([\\d.]+)\\s*\\)$/)?.[1]) : 1,
        contentUnderBar: under.top < barRect.bottom && under.bottom > barRect.top,
        scrollY: window.scrollY};
    }""")
    assert result["background"] == "rgb(13, 17, 23)"
    assert result["opacity"] == 1
    assert result["contentUnderBar"] is True and result["scrollY"] > 0


def test_dashboard_shows_b_and_c_as_comparison_only_not_validated_models(live_server, ui_page):
    statuses = [
        {"model_name": "model_a", "display_name": "XGBoost", "available": True,
         "validation_status": "shadow", "accuracy_30d": None},
        {"model_name": "model_b", "display_name": "GRU (PyTorch)", "available": True,
         "validation_status": "not_validated", "accuracy_30d": None},
        {"model_name": "model_c", "display_name": "Prophet+XGBoost", "available": True,
         "validation_status": "not_validated", "accuracy_30d": None},
    ]
    ui_page.route("**/api/candles**", lambda route: route.fulfill(json=[]))
    ui_page.route("**/api/predictions/consensus**", lambda route: route.fulfill(json={
        "symbol": "XRPUSDT", "interval": "1h", "model_available": True,
        "consensus_probability_up": 0.62, "consensus_signal": "ALCISTA",
        "agreement_count": 1, "weights": {"model_a": 1.0},
        "model_a": {"probability_up": 0.62, "signal": "ALCISTA", "top_features": []},
    }))
    ui_page.route("**/api/predictions/latest**", lambda route: route.fulfill(json=[
        {"model_name": "model_a", "symbol": "XRPUSDT", "probability_up": 0.62, "signal": "ALCISTA"},
        {"model_name": "model_b", "symbol": "XRPUSDT", "probability_up": 0.41, "signal": "BAJISTA"},
        {"model_name": "model_c", "symbol": "XRPUSDT", "probability_up": 0.50, "signal": "NEUTRAL"},
    ]))
    ui_page.route("**/api/models/status", lambda route: route.fulfill(json={"models": statuses}))
    ui_page.route("**/api/models/shadow-status", lambda route: route.fulfill(json={"kill_status": "pending", "n_nonoverlap": 0}))

    ui_page.goto(f"{live_server.url}/#dashboard")
    expect(ui_page.locator(".model-card.model-b")).to_contain_text("No validado")
    expect(ui_page.locator(".model-card.model-c")).to_contain_text("No validado")
    expect(ui_page.locator("#consensus-card .eyebrow")).to_have_text("PUNTAJE DEL MODELO A")
    expect(ui_page.locator("#consensus-card .big-prob")).to_have_text("62.00%")
    assert_no_js_errors(ui_page)


def test_battle_shows_direction_validation_and_models_link_without_live_volatility(live_server, ui_page):
    statuses = [
        {"model_name": "model_a", "display_name": "XGBoost", "available": True,
         "validation_status": "shadow", "accuracy": None, "verified_count": 0},
        {"model_name": "model_b", "display_name": "GRU (PyTorch)", "available": True,
         "validation_status": "not_validated", "accuracy": None, "verified_count": 0},
        {"model_name": "model_c", "display_name": "Prophet+XGBoost", "available": False,
         "validation_status": "not_validated", "unavailable_reason": "fixture no disponible",
         "accuracy": None, "verified_count": 0},
    ]
    ui_page.route("**/api/models/status", lambda route: route.fulfill(json={"models": statuses}))
    ui_page.route("**/api/models/accuracy-by-condition**", lambda route: route.fulfill(json=[]))
    ui_page.route("**/api/predictions/history**", lambda route: route.fulfill(json=[{
        "predicted_at": "2026-01-01T00:00:00+00:00", "symbol": "XRPUSDT",
        "model_name": "model_b", "signal": "BAJISTA", "probability_up": 0.41,
        "is_verified": True, "was_correct": False, "price_at_verification": 100,
        "market_condition": '{"volatility_4h":{"range_1sigma":[98,102],"range_2sigma":[96,104]}}',
    }]))
    ui_page.route("**/api/models/page-context**", lambda route: route.fulfill(json={"symbol": "XRPUSDT", "interval": "1h", "models": {}}))

    ui_page.goto(f"{live_server.url}/#battle")
    expect(ui_page.locator("#battle-content")).to_contain_text("No validado")
    expect(ui_page.locator("#battle-content")).to_contain_text("No disponible")
    expect(ui_page.locator("#battle-content")).to_contain_text("Rango 4h 1σ: 98.00 – 102.00 · 2σ: 96.00 – 104.00 · ✓ dentro de 1σ")
    expect(ui_page.locator("#vol-coverage")).to_have_count(0)
    expect(ui_page.locator("#vol-battle-table")).to_have_count(0)
    card_links = ui_page.locator('#battle-content a[href="#models"]')
    expect(card_links).to_have_count(len(statuses))
    for link in card_links.all():
        expect(link).to_contain_text("Ver estado y detalle en Modelos")
    header_link = ui_page.locator('#screen-battle > p.card.muted a[href="#models"]')
    expect(header_link).to_have_count(1)
    assert_no_js_errors(ui_page)


def test_detail_compound_modal_shows_current_values_and_confirms_update(live_server, ui_page):
    live_server.db.update_grid(live_server.grid_id, params=live_server.db._json({
        "compound_enabled": True, "compound_ratio": 0.25, "compound_max_growth_pct": 40,
    }))
    ui_page.goto(f"{live_server.url}/#grids/{live_server.grid_id}")
    button = ui_page.get_by_role("button", name="Inter\u00e9s compuesto")
    button.click()
    dialog = ui_page.get_by_role("dialog")
    expect(dialog).to_be_visible()
    expect(dialog.locator('[name="compound-enabled"]')).to_be_checked()
    expect(dialog.locator('[name="compound-ratio"]')).to_have_value("25")
    expect(dialog.locator('[name="compound-cap"]')).to_have_value("40")
    expect(dialog).to_contain_text("Requiere USDT libre")
    expect(dialog).to_contain_text("ciclos futuros")
    assert "Ci\u00e9rralo y abre uno nuevo" not in dialog.inner_text()
    calls = []
    ui_page.on("request", lambda request: calls.append(json.loads(request.post_data))
               if request.method == "POST" and request.url.endswith(f"/api/grids/{live_server.grid_id}/params") else None)
    ui_page.get_by_role("button", name="Continuar", exact=True).click()
    expect(ui_page.locator("#grid-action-title")).to_have_text("Revisar plan")
    ui_page.get_by_role("button", name="Continuar", exact=True).click()
    expect(ui_page.locator("#grid-action-title")).to_have_text("Acci\u00f3n completada")
    assert len(calls) == 2
    assert calls[0]["dry_run"] is True and calls[0]["confirm"] is False
    assert calls[1]["dry_run"] is False and calls[1]["confirm"] is True
    assert calls[1]["compound_ratio"] == 0.25
    assert_no_js_errors(ui_page)


def test_detail_idle_shrink_button_states_and_activation_warning(live_server, ui_page):
    grid_id = live_server.secondary_grid_id
    live_server.db.update_grid(grid_id, status="ACTIVE")
    state = {"enabled": False, "global": True}
    def loans(route):
        route.fulfill(json={"grid_id": grid_id, "loans_group": None, "loans_enabled": False,
            "idle_shrink_enabled": state["enabled"],
            "idle_shrink_global_enabled": state["global"],
            "counts": {}, "total_amount_lent_usdt": 0,
            "average_repaid_open_hours": None, "open_loans": []})
    ui_page.route(f"**/api/grids/{grid_id}/loans", loans)
    calls = []
    def params(route):
        body = route.request.post_data_json
        calls.append(body)
        if body["dry_run"]:
            route.fulfill(json={"dry_run": True, "plan": {"action": "params",
                "updates": {"adjust_idle_shrink": body["adjust_idle_shrink"]}, "remove": []}})
        else:
            state["enabled"] = body["adjust_idle_shrink"]
            route.fulfill(json={"dry_run": False, "result": {"ok": True},
                "status": "ACTIVE", "status_after": "ACTIVE", "outcome": "completed", "errors": []})
    ui_page.route(f"**/api/grids/{grid_id}/params", params)
    ui_page.goto(f"{live_server.url}/#grids/{grid_id}")
    activate = ui_page.locator('[data-grid-action="idle-shrink-on"]')
    expect(activate).to_be_visible()
    expect(activate).to_be_enabled()
    activate.click()
    dialog = ui_page.locator("#grid-action-dialog")
    warning = ("Regla apagada por defecto: en simulación XRP no mejoró la ganancia. "
        "Quita niveles sin ciclos en 24 h y no se puede revertir sin reabrir el grid. "
        "La primera evaluación solo guarda una línea base.")
    expect(dialog).to_contain_text(warning)
    dialog.get_by_role("button", name="Continuar", exact=True).click()
    expect(dialog.get_by_role("heading", name="Revisar plan")).to_be_visible()
    expect(dialog).to_contain_text(warning)
    dialog.get_by_role("button", name="Continuar", exact=True).click()
    expect(dialog).to_contain_text("Acción completada")
    assert calls == [
        {"adjust_idle_shrink": True, "dry_run": True, "confirm": False},
        {"adjust_idle_shrink": True, "dry_run": False, "confirm": True},
    ]

    state.update({"enabled": False, "global": False})
    ui_page.reload()
    settings_link = ui_page.get_by_role("link", name=(
        "Interruptor global apagado: act\u00edvalo en Cuenta \u2192 Ajustes globales"))
    expect(settings_link).to_be_visible()
    assert settings_link.get_attribute("href") == "#cuenta"

    state.update({"enabled": True, "global": False})
    ui_page.reload()
    expect(ui_page.locator('[data-grid-action="idle-shrink-off"]')).to_have_text(
        "Apagar reducción de niveles ociosos")
    assert_no_js_errors(ui_page)


def test_advisor_uses_45_second_timeout_and_other_gets_keep_default(live_server, ui_page):
    ui_page.goto(f"{live_server.url}/#grid")
    timeouts = ui_page.evaluate("""async () => {
        const client = new ApiClient();
        const seen = [];
        const originalSetTimeout = window.setTimeout;
        const originalClearTimeout = window.clearTimeout;
        client._fetch = async () => ({ok: true, json: async () => ({})});
        window.setTimeout = (_callback, delay) => { seen.push(delay); return seen.length; };
        window.clearTimeout = () => {};
        try {
            await client.grid({symbol: 'ADAUSDT'});
            await client.get('/api/health');
        } finally {
            window.setTimeout = originalSetTimeout;
            window.clearTimeout = originalClearTimeout;
        }
        return seen;
    }""")
    assert timeouts == [45000, 10000]
