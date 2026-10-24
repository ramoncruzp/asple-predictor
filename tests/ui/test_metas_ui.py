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
    page.fill('[name="target_value"]', "10")
    page.click('[name="profile"][value="RAPIDO"]')
    page.click('#meta-create-form button[type="submit"]')
    page.wait_for_selector(".metas-draft")
    assert "100" in page.locator(".metas-draft").first.inner_text()


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
