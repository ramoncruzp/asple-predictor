"""Fase 40c, parte C: pruebas de interfaz de la pantalla Metas con Playwright.

Usa un plan de análisis sintético (operator._build_plan mockeado) para que el
análisis "termine" en milisegundos en vez de los ~18 minutos reales -- el
contrato de la API (PENDING -> COMPLETE/FAILED) es el mismo que en producción,
solo se evita correr Scanner/Advisor/replay reales contra velas públicas.
"""
from __future__ import annotations

import socket
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

playwright_sync = pytest.importorskip("playwright.sync_api")
uvicorn = pytest.importorskip("uvicorn")

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from api.routes import grid_control, grid_status, grids, metas
from data.exchange_filters import SymbolFilters
from database.db_manager import DBManager
from grid.engine import GridEngine
from grid.meta import MetaCore
from grid.meta_operator import MetaOperator
from tests.grid_fakes import FakeExchange, fake_symbol_info

ROOT = Path(__file__).resolve().parents[2]

SYNTHETIC_PLAN = {
    "candidates": [{"symbol": "XRPUSDT", "sigma_24h": 0.02, "score": 0.7, "book_state": "two_sided",
                     "excluded_reason": None},
                    {"symbol": "PEPEUSDT", "excluded_reason": "fuera de banda de sigma"}],
    "pieces": [{"symbol": "XRPUSDT", "strategy": "simple", "piece_type": "grid",
                "capital_usdt": 100, "n_levels": 4, "floor": 90, "ceiling": 110,
                "mid_at_plan": 100.0, "margin_pct": 0.7}],
    "estimated_time": {"probability_replay": 0.0, "probability_adjusted": 0.03,
                        "median_progress_pct": 35.0, "days_median": None,
                        "worst_drawdown_pct": 20.1, "worst_loss_vs_capital_pct": 17.4,
                        "rolling_windows": 23, "independent_windows": 7,
                        "loss_cut_windows": 2, "skipped_windows": 1,
                        "skipped_reason": "ventana no simulable", "low_likelihood": True,
                        "evidence_warning": None},
    "piece_estimates": [],
    "combinations_evaluated": [], "advisor_comparisons": [],
    "capital_usage": {"reason": None}, "signals_used": [], "signals_omitted": [],
    "warnings": [], "control_plan": {"type": "control", "symbol": "XRPUSDT",
        "capital_usdt": 100, "strategy": "smart", "range_low": 90, "range_high": 110,
        "n_levels": 4, "opened": False},
    "target_usdt": 10.0, "loss_max_usdt": 18.0, "deadline_days": 20,
    "selection_reason": "fake", "shadow_decision": {"symbol": "XRPUSDT", "strategy": "simple",
        "probability": 0.03, "signals": []},
    "probability_under_25_pct": True, "historical_evidence_note": None,
}


QA_PLAN = {
    "candidates": [{"symbol": "XRPUSDT", "sigma_24h_open": 0.0234, "score": 0.81,
                     "book_state": "two_sided",
                     "model_statistics": {"readiness": None, "effective_verifications": 12.0,
                                          "required_verifications": 30, "complete": True}, "excluded_reason": None},
                    {"symbol": "ADAUSDT", "sigma_24h_open": 0.018, "score": 0.6,
                     "book_state": "two_sided", "model_statistics": {
                        "readiness": "datos_insuficientes", "effective_verifications": 9.5,
                        "required_verifications": 30}, "excluded_reason": None}],
    "pieces": [{"symbol": "PEPEUSDT", "strategy": "simple", "piece_type": "grid",
                "capital_usdt": 50, "n_levels": 4, "floor": 0.00000437, "ceiling": 0.00000441,
                "mid_at_plan": 0.00000439, "margin_pct": 0.5, "loans_pct": 0},
               {"symbol": "JUPUSDT", "strategy": "smart", "piece_type": "grid",
                "capital_usdt": 50, "n_levels": 4, "floor": 0.35, "ceiling": 0.45,
                "mid_at_plan": 0.40, "margin_pct": 0.7, "loans_pct": 50}],
    "estimated_time": {"probability_replay": 0.0, "probability_adjusted": 0.03,
                        "median_progress_pct": 35.0, "days_median": None,
                        "worst_drawdown_pct": 20.1, "worst_loss_vs_capital_pct": 17.4,
                        "rolling_windows": 23, "independent_windows": 7,
                        "loss_cut_windows": 3, "skipped_windows": 1,
                        "skipped_reason": "ventana no simulable", "low_likelihood": True,
                        "evidence_warning": None},
    "piece_estimates": [{"symbol": "PEPEUSDT", "strategy": "simple", "loss_cut_windows": 2,
                          "skipped_windows": 0, "skipped_reason": None,
                          "bot_windows_bought": None, "bot_windows_sold": None, "rolling_windows": 10},
                         {"symbol": "JUPUSDT", "strategy": "smart", "loss_cut_windows": 1,
                          "skipped_windows": 0, "skipped_reason": None,
                          "bot_windows_bought": None, "bot_windows_sold": None, "rolling_windows": 10}],
    "combinations_evaluated": [], "advisor_comparisons": [],
    "capital_usage": {"reason": None}, "signals_used": [], "signals_omitted": [],
    "warnings": ["El replay no modela los préstamos de grids Smart; la estimación no incluye capital prestado."],
    "control_plan": {"type": "control", "symbol": "PEPEUSDT", "capital_usdt": 100, "strategy": "smart",
        "range_low": 0.00000437, "range_high": 0.00000441, "n_levels": 4, "opened": False},
    "target_usdt": 10.0, "loss_max_usdt": 18.0, "deadline_days": 20,
    "selection_reason": "fake", "shadow_decision": {"symbol": "PEPEUSDT", "strategy": "simple",
        "probability": 0.03, "signals": []},
    "probability_under_25_pct": True, "historical_evidence_note": None,
}


@dataclass
class MetasServer:
    url: str
    db: DBManager
    engine: GridEngine
    exchange: FakeExchange
    core: MetaCore
    operator: MetaOperator


@pytest.fixture
def metas_live_server(tmp_path):
    db = DBManager(f"sqlite:///{tmp_path / 'metas_ui.db'}")
    db.add_or_reactivate_coin("XRPUSDT")

    class TestExchange(FakeExchange):
        testnet = True
        client = SimpleNamespace(testnet=True)

    exchange = TestExchange()
    settings = SimpleNamespace(
        meta_mode_enabled=True, meta_max_active=1, alerts_enabled=False,
        grid_api_token="", max_grids_simultaneos=5, usdt_por_grid=100,
        capital_max_por_nivel_pct=.3, grid_min_step_pct=.003,
        same_coin_sell_tolerance_pct=.05, scanner_timeout_seconds=5,
        scanner_fee_pct=.1, scanner_min_spacing_pct=.8,
        grid_min_margin_after_fees_pct=.7, scanner_history_days=30,
        grid_monitor_gap_minutes=20,
    )
    engine = GridEngine(db, exchange, settings)
    core = MetaCore(db, exchange, engine, settings=settings)
    core.start = lambda: None

    class Scanner:
        client = exchange

        def __init__(self):
            self.settings = settings

        def clock(self):
            return time.monotonic()

        def _market(self, symbol, capital, deadline):
            return ({"bid": exchange.bid, "ask": exchange.ask,
                      "klines_1h": pd.DataFrame({"close": [98, 101, 99, 102, 97, 103, 100]})},
                     exchange.filters)

    operator = MetaOperator(core, db, Scanner(), engine, settings)
    operator._build_plan = lambda goal: {**SYNTHETIC_PLAN, "meta_id": goal["id"]}

    app = FastAPI()
    app.include_router(metas.router, prefix="/api/metas")
    app.include_router(grids.router, prefix="/api/grids")
    app.include_router(grid_control.router, prefix="/api/grids")
    app.include_router(grid_status.router)
    app.state.db, app.state.settings = db, settings
    app.state.meta_core, app.state.meta_operator = core, operator
    app.state.grid_engine, app.state.testnet_client = engine, exchange
    app.state.client = exchange
    app.state.vol_provider = None
    app.state.grid_scan_service = operator.scanner
    app.state.grid_monitor = None
    app.state.prediction_loop = SimpleNamespace(latest={})
    app.mount("/", StaticFiles(directory=str(ROOT / "frontend"), html=True), name="frontend")

    ready = threading.Event()
    app.add_event_handler("startup", ready.set)
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    port = listener.getsockname()[1]
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error", lifespan="on")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    thread.start()
    if not ready.wait(10):
        server.should_exit = True
        thread.join(timeout=5)
        pytest.fail("Uvicorn no completó startup en 10 segundos")
    handle = MetasServer(f"http://127.0.0.1:{port}", db, engine, exchange, core, operator)
    try:
        yield handle
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        if thread.is_alive():
            server.force_exit = True
            thread.join(timeout=5)
        listener.close()


@pytest.fixture
def metas_page(chromium_browser, metas_live_server):
    context = chromium_browser.new_context(viewport={"width": 1440, "height": 1000})
    page = context.new_page()
    page.route("**/*", lambda route: route.continue_() if (
        "127.0.0.1" in route.request.url or "localhost" in route.request.url)
        else route.fulfill(status=200, content_type="text/plain", body=""))
    try:
        yield page, metas_live_server
    finally:
        context.close()


def test_create_meta_then_appears_as_draft(metas_page):
    page, server = metas_page
    page.goto(f"{server.url}/#metas")
    page.wait_for_selector("#meta-create-form")
    page.fill('[name="capital_usdt"]', "100")
    page.fill('[name="target_value"]', "25")
    page.click('[name="profile"][value="RAPIDO"]')
    page.click('#meta-create-form button[type="submit"]')
    page.wait_for_selector(".metas-draft")
    title = page.locator(".metas-draft").first.inner_text()
    # Fase 40c-3, defecto 1: "100 -> 10 USDT" se leía como si la meta fuera
    # bajar a 10; debe mostrar capital -> capital+objetivo con el delta.
    assert "100 → 125 USDT (+25)" in title
    assert page.locator('[name="target_value"]').input_value() == "25"


def test_analysis_completes_and_start_opens_a_grid(metas_page):
    page, server = metas_page
    goal = server.core.create_goal(150, 10, profile="RAPIDO")
    page.goto(f"{server.url}/#metas")
    page.wait_for_selector(f'[data-analyze="{goal["id"]}"]')
    page.click(f'[data-analyze="{goal["id"]}"]')
    page.wait_for_selector(".metas-analyzing")
    page.wait_for_selector('[data-start-plan]', timeout=15000)
    assert "XRPUSDT" in page.locator(".metas-plan-section").first.inner_text()
    page.click('[data-start-plan]')
    page.wait_for_selector('#meta-action-dialog:not([hidden])')
    page.click('#meta-action-dialog [data-action-confirm]')
    page.wait_for_selector(".metas-active", timeout=10000)
    linked = server.core._owned_grids(goal["id"])
    assert len(linked) == 1


def test_active_meta_with_unknown_valuation_shows_unprotected_banner(metas_page):
    page, server = metas_page
    goal = server.core.create_goal(150, 10, profile="RAPIDO")
    server.core.start_goal(goal["id"])
    server.exchange.get_order_book_depth = lambda symbol, limit=5: {
        "bids": [], "asks": [], "read_at": None}
    grid = server.engine.create_grid("XRPUSDT", 90, 110, 4, capital=100)
    server.db.update_level(grid["id"], 0, state="HOLDING", held_qty=1)
    server.core.link_grid(goal["id"], grid["id"])
    page.goto(f"{server.url}/#metas")
    page.wait_for_selector(".metas-unprotected", timeout=10000)
    assert "SIN PROTECCIÓN" in page.locator(".metas-unprotected").inner_text()


def test_active_meta_shows_remaining_time_free_capital_extra_and_spanish_status(metas_page):
    page, server = metas_page
    goal = server.core.create_goal(150, 10, profile="RAPIDO")
    server.core.start_goal(goal["id"])
    grid = server.engine.create_grid("XRPUSDT", 90, 110, 4, capital=100)
    server.core.link_grid(goal["id"], grid["id"])
    server.core._ledger(goal["id"], None, "META_OPERATOR_NOOP", {"decision": "no_hacer_nada"})
    server.core._ledger(goal["id"], None, "META_OPERATOR_REVIEW_DECISION", {
        "decision_choices": {"current_piece_estimate": {"grid_id": grid["id"],
            "piece": {"symbol": "XRPUSDT", "capital_usdt": 100, "floor": 90,
                "ceiling": 110, "n_levels": 4},
            "estimate": {"probability_replay": .42, "probability_adjusted": .45,
                "median_progress_pct": 23}}}})
    page.goto(f"{server.url}/#metas")
    active = page.locator(".metas-active")
    page.wait_for_selector(".metas-live-facts")
    assert "Activa" in active.locator("h2").inner_text()
    assert "50" in active.locator(".metas-live-facts").inner_text()
    assert "Plazo restante" in active.locator(".metas-live-facts").inner_text()
    assert "Ganancia extra realizada" in active.locator(".metas-live-facts").inner_text()
    page.check("[data-ledger-valuation]")
    page.wait_for_function("() => { const slot = document.querySelector('[data-ledger-slot]'); return !!slot && slot.innerText.includes('El operador mantuvo las piezas'); }")
    assert "Pieza actual: XRPUSDT" in page.locator("[data-ledger-slot]").inner_text()


def test_learning_section_is_global_and_read_only_on_metas_page(metas_page):
    page, server = metas_page
    page.goto(f"{server.url}/#metas")
    learning = page.locator("[data-learning-section]")
    page.wait_for_selector("[data-learning-profile='RAPIDO'] .metas-learning-scroll")
    page.wait_for_selector("[data-learning-profile='PRUDENTE'] .metas-learning-scroll")
    assert "Aprendizaje (todas las metas)" in learning.inner_text()
    assert "Aprende de todas las metas del perfil y del hist\u00f3rico reconstruido." in learning.inner_text()
    assert "se cambia en Laboratorio" in learning.inner_text()
    assert page.locator("[data-learning-refresh]").count() == 1
    assert page.locator("[data-learning-switch]").count() == 0
    assert page.locator("[data-learning-actor]").count() == 0
    assert page.locator("[data-learning-profile='RAPIDO']").inner_text().count("0,25") >= 1
    assert "Toque compra→venta" in page.locator("[data-learning-profile='RAPIDO']").inner_text()


def test_learning_switches_live_in_lab_and_require_actor(metas_page):
    page, server = metas_page
    page.goto(f"{server.url}/#lab")
    row = page.locator("[data-meta-learning-switch='RAPIDO']")
    page.wait_for_selector("[data-meta-learning-switch='PRUDENTE']")
    assert "Sin evidencia todav\u00eda" in row.inner_text()
    assert "APAGADO" in row.inner_text()
    page.click("[data-meta-learning-switch='RAPIDO'] [data-switch-open]")
    page.click("[data-meta-learning-switch='RAPIDO'] [data-switch-save]")
    assert "Escribe qui\u00e9n confirma" in row.inner_text()
    page.fill("[data-meta-learning-switch='RAPIDO'] [data-switch-actor]", "Prueba local")
    page.click("[data-meta-learning-switch='RAPIDO'] [data-switch-save]")
    page.wait_for_function("() => document.querySelector('[data-meta-learning-switch=RAPIDO]')?.innerText.includes('ENCENDIDO')")
    assert server.operator.learning_status('RAPIDO')['enabled'] is True



def test_auto_refresh_updates_live_blocks_without_replacing_meta_panel(metas_page):
    page, server = metas_page
    goal = server.core.create_goal(150, 10, profile="RAPIDO")
    server.core.start_goal(goal["id"])
    grid = server.engine.create_grid("XRPUSDT", 90, 110, 4, capital=100)
    server.core.link_grid(goal["id"], grid["id"])
    page.add_init_script("""(() => {
      const native = window.setInterval.bind(window);
      window.setInterval = (fn, ms, ...args) => {
        if (ms === 30000) window.__metasRefresh = fn;
        return native(fn, ms, ...args);
      };
    })()""")
    page.goto(f"{server.url}/#metas")
    page.wait_for_selector(".metas-active[data-meta-id]")
    page.evaluate("window.__panelBefore = document.querySelector('.metas-active')")
    with page.expect_response(lambda response: f"/api/metas/{goal['id']}/valuation" in response.url):
        page.evaluate("window.__metasRefresh()")
    page.wait_for_function("document.querySelector('[data-live-valuation] .metas-valuation') !== null")
    assert page.evaluate("document.querySelector('.metas-active') === window.__panelBefore")
    assert page.locator("[data-learning-refresh]").count() == 1

def test_active_meta_shows_recent_reconfiguration_in_plain_spanish(metas_page):
    page, server = metas_page
    goal = server.core.create_goal(150, 10, profile="RAPIDO")
    server.core.start_goal(goal["id"])
    grid = server.engine.create_grid("XRPUSDT", 90, 110, 4, capital=100)
    server.core.link_grid(goal["id"], grid["id"], "grid")
    server.core._ledger(goal["id"], None, "META_OPERATOR_RECONFIGURE", {
        "old": {"floor": 90, "ceiling": 110, "n_levels": 4},
        "new": {"floor": 92, "ceiling": 112, "n_levels": 5},
        "reason": "precio cerca del techo", "estimated_probability_improvement_pp": 6,
        "estimated_progress_improvement_pct": 12}, grid_id=grid["id"])
    page.goto(f"{server.url}/#metas")
    recent = page.locator("[data-recent-adjustments]")
    page.wait_for_selector("[data-recent-adjustments]")
    page.wait_for_function("() => document.querySelector('[data-recent-adjustments]')?.innerText.includes('precio cerca del techo')")
    assert "Pieza" in recent.inner_text()
    assert "Mejora estimada" in recent.inner_text()


def test_discard_draft_removes_it_from_the_screen(metas_page):
    page, server = metas_page
    goal = server.core.create_goal(100, 10, profile="RAPIDO")
    page.goto(f"{server.url}/#metas")
    page.wait_for_selector(f'[data-discard="{goal["id"]}"]')
    page.click(f'[data-discard="{goal["id"]}"]')
    page.wait_for_selector('#meta-action-dialog:not([hidden])')
    page.click('#meta-action-dialog [data-action-confirm]')
    page.wait_for_function("() => document.querySelectorAll('.metas-draft').length === 0")
    assert page.locator(".metas-draft").count() == 0


@pytest.mark.parametrize("mode", ["liquidate", "repository"])
def test_cancel_operation_each_mode(metas_page, mode):
    page, server = metas_page
    goal = server.core.create_goal(150, 10, profile="RAPIDO")
    server.core.start_goal(goal["id"])
    grid = server.engine.create_grid("XRPUSDT", 90, 110, 4, capital=100)
    server.core.link_grid(goal["id"], grid["id"])
    page.goto(f"{server.url}/#metas")
    page.wait_for_selector("[data-cancel-op]", timeout=10000)
    page.click("[data-cancel-op]")
    page.wait_for_selector('#meta-action-dialog:not([hidden])')
    page.check(f'[name="cancel-mode"][value="{mode}"]')
    page.click('#meta-action-dialog [data-action-confirm]')
    page.wait_for_function(
        "() => !document.querySelector('#meta-action-dialog') || "
        "document.getElementById('meta-action-dialog').hidden")
    updated = server.core.get_goal(goal["id"])
    assert updated["status"] in {"CLOSING", "CUMPLIDA", "CORTADA_POR_PERDIDA", "CANCELADA"}


def test_mobile_viewport_390_renders_without_horizontal_overflow(chromium_browser, metas_live_server):
    context = chromium_browser.new_context(viewport={"width": 390, "height": 760})
    page = context.new_page()
    page.route("**/*", lambda route: route.continue_() if (
        "127.0.0.1" in route.request.url or "localhost" in route.request.url)
        else route.fulfill(status=200, content_type="text/plain", body=""))
    try:
        page.goto(f"{metas_live_server.url}/#metas")
        page.wait_for_selector("#metas-content")
        scroll_width = page.evaluate("document.documentElement.scrollWidth")
        client_width = page.evaluate("document.documentElement.clientWidth")
        assert scroll_width <= client_width + 1
    finally:
        context.close()


def test_plan_details_show_translated_states_significant_prices_loans_and_warnings(metas_page):
    """Fase 40c-3, defectos 3, 4, 5, 6, 7: traducción del libro, precio de PEPE
    con cifras significativas, columna de préstamos, total real de ventanas
    cortadas por pérdida y todos los avisos del plan (incluido el de
    préstamos de grids Smart)."""
    page, server = metas_page
    goal = server.core.create_goal(100, 10, profile="RAPIDO")
    server.operator._build_plan = lambda _goal: {**QA_PLAN, "meta_id": goal["id"]}
    page.goto(f"{server.url}/#metas")
    page.wait_for_selector(f'[data-analyze="{goal["id"]}"]')
    page.click(f'[data-analyze="{goal["id"]}"]')
    page.wait_for_selector('[data-start-plan]', timeout=15000)
    content = page.locator(f'[data-analysis-slot="{goal["id"]}"]').inner_text()

    assert "Dos lados" in content
    assert "two_sided" not in content
    assert "Sin estado · 12,0 de 30 días verificados" in content
    assert "Datos insuficientes · 9,5 de 30 días verificados" in content
    # Defecto 2: sigma_24h_open (0.0234) con su unidad, no "—".
    assert "2.34%" in content
    # 0.00000437 / 0.00000441 no deben colapsar a "0.000004" (pierde el precio real).
    assert "437" in content
    assert "441" in content
    assert "0.000004\n" not in content and "0.000004 " not in content
    # Columna de préstamos: JUPUSDT Smart va con 50 %.
    assert "50" in content
    # El total de "cortadas por pérdida" debe ser la suma real de las piezas (2+1=3).
    assert "3" in content
    # Aviso del plan sobre préstamos de grids Smart, no solo los avisos calculados en el cliente.
    assert "préstamos de grids Smart" in content


def test_profile_limits_table_highlights_selected_column(metas_page):
    """Fase 40c-3, defecto 10: la tabla de límites resalta la columna del
    perfil elegido, no solo el radio."""
    page, server = metas_page
    page.goto(f"{server.url}/#metas")
    table = page.locator(".metas-limits-table")
    page.wait_for_selector(".metas-limits-table")
    assert table.get_attribute("data-selected-profile") == "RAPIDO"
    page.click('[name="profile"][value="PRUDENTE"]')
    assert table.get_attribute("data-selected-profile") == "PRUDENTE"


def test_create_form_limits_table_has_no_overflow_at_desktop_or_mobile(chromium_browser, metas_live_server):
    """Fase 40c-3, defecto 8: la tabla de límites se cortaba a la derecha con
    un scrollbar blanco sin estilo; no debe necesitar scroll en ninguno de los
    dos anchos."""
    for width, height in ((1440, 1000), (390, 760)):
        context = chromium_browser.new_context(viewport={"width": width, "height": height})
        page = context.new_page()
        page.route("**/*", lambda route: route.continue_() if (
            "127.0.0.1" in route.request.url or "localhost" in route.request.url)
            else route.fulfill(status=200, content_type="text/plain", body=""))
        try:
            page.goto(f"{metas_live_server.url}/#metas")
            page.wait_for_selector(".metas-table-scroll")
            wrap = page.locator(".metas-table-scroll").first
            scroll_width = wrap.evaluate("el => el.scrollWidth")
            client_width = wrap.evaluate("el => el.clientWidth")
            assert scroll_width <= client_width + 1, f"overflow at width={width}"
        finally:
            context.close()
