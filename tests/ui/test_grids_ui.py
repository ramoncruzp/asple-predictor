"""Optional Playwright smoke test for the Fase 17A Grids screen.

Skips cleanly (via pytest.importorskip) when Playwright is not installed, so
it never breaks the offline suite. At the time this was written, Playwright
was not installed in this repo's venv, so this file is exercised by
`pytest.importorskip` skipping every test below -- it has not actually been
run against a live browser here; see DISCREPANCIAS_FASE17A.md.
"""
from __future__ import annotations

import threading
from decimal import Decimal

import pytest

playwright_sync_api = pytest.importorskip("playwright.sync_api")
uvicorn = pytest.importorskip("uvicorn")

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from api.routes import grid_status, grids
from database.db_manager import DBManager
from grid.engine import GridEngine
from tests.grid_fakes import FakeExchange


def _build_app(tmp_path):
    db = DBManager(f"sqlite:///{tmp_path}/ui.db")
    db.add_or_reactivate_coin("XRPUSDT")
    exchange = FakeExchange()
    from types import SimpleNamespace
    settings = SimpleNamespace(
        max_grids_simultaneos=5, usdt_por_grid=100, capital_max_por_nivel_pct=.30,
        grid_min_step_pct=.003, grid_api_token="", scanner_timeout_seconds=60,
        scanner_fee_pct=.1, scanner_min_spacing_pct=.8, grid_monitor_gap_minutes=20,
    )
    engine = GridEngine(db, exchange, settings)
    engine.create_grid("XRPUSDT", Decimal(90), Decimal(110), 5, capital=1000)
    second = engine.create_grid("XRPUSDT", Decimal(90), Decimal(110), 5, capital=500, strategy="smart")
    app = FastAPI()
    app.include_router(grids.router, prefix="/api/grids")
    app.include_router(grid_status.router)
    app.state.db, app.state.settings = db, settings
    app.state.grid_engine, app.state.testnet_client = engine, exchange
    app.mount("/", StaticFiles(directory="frontend", html=True), name="frontend")
    return app


@pytest.fixture
def live_server(tmp_path):
    app = _build_app(tmp_path)
    config = uvicorn.Config(app, host="127.0.0.1", port=8765, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    while not server.started:
        pass
    try:
        yield "http://127.0.0.1:8765"
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def test_grids_tab_lists_cards_and_disabled_controls(live_server):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page()
        page.goto(f"{live_server}/#grids")
        page.wait_for_selector(".grid-row-card")
        assert page.locator(".grid-row-card").count() >= 2
        assert page.locator(".grids-warning").count() == 1

        page.locator(".grid-row-card").first.click()
        page.wait_for_selector(".grid-detail-header")
        assert page.locator("button:disabled", has_text="Pausar").count() == 1
        assert page.locator(".price-marker-row").count() <= 1
        browser.close()


def test_grids_empty_and_error_states(live_server):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page()
        page.goto(f"{live_server}/#grids/999999")
        page.wait_for_selector(".grids-error")
        assert "no existe" in page.locator(".grids-error").inner_text()
        browser.close()
