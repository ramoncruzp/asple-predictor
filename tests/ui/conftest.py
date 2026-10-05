from __future__ import annotations

import socket
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlparse

import pandas as pd
import pytest

playwright_sync = pytest.importorskip("playwright.sync_api")
uvicorn = pytest.importorskip("uvicorn")

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from api.routes import coins, grid_account, grid_advisor, grid_control, grid_status, grid_structure, grids
from database.db_manager import DBManager
from grid.engine import GridEngine
from tests.grid_fakes import FakeExchange


ROOT = Path(__file__).resolve().parents[2]
_EXTERNAL_HOSTS: set[str] = set()
_UI_BUGS: set[str] = set()


@dataclass
class UIServer:
    url: str
    app: FastAPI
    db: DBManager
    engine: GridEngine
    exchange: FakeExchange
    settings: SimpleNamespace
    grid_id: int
    secondary_grid_id: int


@pytest.fixture(scope="session")
def chromium_browser():
    with playwright_sync.sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(
                headless=True,
                args=["--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE localhost, EXCLUDE 127.0.0.1"],
            )
        except playwright_sync.Error as exc:
            pytest.skip(f"Chromium de Playwright no está disponible: {exc}")
        yield browser
        browser.close()


@pytest.fixture
def live_server(tmp_path):
    db = DBManager(f"sqlite:///{tmp_path / 'ui.db'}")
    db.add_or_reactivate_coin("XRPUSDT")
    exchange = FakeExchange(fee_rate="0.001", fee_asset="XRP")
    exchange.client = SimpleNamespace(
        testnet=True,
        get_account=lambda: {"canTrade": True, "accountType": "SPOT"},
    )
    exchange._run_read = lambda callback: callback()
    settings = SimpleNamespace(
        max_grids_simultaneos=5,
        usdt_por_grid=1000.0,
        capital_max_por_nivel_pct=0.30,
        grid_min_step_pct=0.003,
        grid_api_token="",
        scanner_timeout_seconds=2.0,
        scanner_fee_pct=0.1,
        scanner_min_spacing_pct=0.8,
        scanner_min_cell_floor_usdt=5.5,
        scanner_history_days=30,
        scanner_cache_ttl_seconds=0,
        scanner_rate_limit_seconds=0,
        scanner_retries=0,
        grid_monitor_gap_minutes=20,
        testnet_api_key="",
        testnet_api_secret="",
        binance_api_key="",
        binance_api_secret="",
    )
    engine = GridEngine(db, exchange, settings)
    grid = engine.create_grid("XRPUSDT", 90, 110, 5, capital=1000)
    for level in db.get_grid_levels(grid["id"]):
        if level.get("order_id") is not None:
            exchange.fill(int(level["order_id"]))
    engine.sync_grid(int(grid["id"]))
    # One filled cell costs less than the current bid while another costs more.
    exchange.bid, exchange.ask, exchange.avg = (exchange.avg, exchange.ask, exchange.avg)
    secondary = engine.create_grid("XRPUSDT", 60, 140, 5, capital=500, strategy="smart")

    class FakeScanService:
        def __init__(self):
            self.settings = settings

        @staticmethod
        def clock():
            return time.monotonic()

        def _market(self, symbol, capital, deadline):
            return ({
                "bid": exchange.bid,
                "ask": exchange.ask,
                "klines_1h": pd.DataFrame({"close": [98, 101, 99, 102, 97, 103, 100]}),
            }, exchange.filters)

    app = FastAPI()
    app.include_router(coins.router, prefix="/api/coins")
    app.include_router(grid_advisor.router, prefix="/api/grid")
    app.include_router(grids.router, prefix="/api/grids")
    app.include_router(grid_control.router, prefix="/api/grids")
    app.include_router(grid_structure.router, prefix="/api/grids")
    app.include_router(grid_status.router)
    app.include_router(grid_account.router)
    app.state.db = db
    app.state.settings = settings
    app.state.client = exchange
    app.state.prediction_loop = SimpleNamespace(latest={})
    app.state.testnet_client = exchange
    app.state.grid_engine = engine
    app.state.grid_scan_service = FakeScanService()
    app.state.grid_monitor = None
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
    handle = UIServer(f"http://127.0.0.1:{port}", app, db, engine, exchange,
                      settings, int(grid["id"]), int(secondary["id"]))
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
def ui_page(chromium_browser, live_server, request, tmp_path):
    context = chromium_browser.new_context(viewport={"width": 1440, "height": 1000})
    page = context.new_page()
    page._ui_console_errors = []
    page._ui_page_errors = []
    page.on("console", lambda message: page._ui_console_errors.append(message.text)
            if message.type == "error" and not message.text.startswith("Failed to load resource:")
            else None)
    page.on("pageerror", lambda error: page._ui_page_errors.append(str(error)))

    def block_external(route):
        host = urlparse(route.request.url).hostname or ""
        if host not in {"127.0.0.1", "localhost"}:
            _EXTERNAL_HOSTS.add(host)
            content_type = "text/css" if route.request.url.endswith(".css") else "text/javascript"
            route.fulfill(status=200, content_type=content_type, body="")
            return
        route.continue_()

    page.route("**/*", block_external)
    try:
        yield page
    finally:
        report = getattr(request.node, "rep_call", None)
        if report is not None and report.failed:
            try:
                page.screenshot(path=str(tmp_path / "failure.png"), full_page=True)
            except playwright_sync.Error:
                pass
        context.close()


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    setattr(item, f"rep_{call.when}", outcome.get_result())


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    hosts = ", ".join(sorted(_EXTERNAL_HOSTS)) or "(ninguno)"
    terminalreporter.write_sep("-", f"Hosts externos bloqueados y registrados por Playwright: {hosts}")
    bugs = "; ".join(sorted(_UI_BUGS)) or "(ninguno observado)"
    terminalreporter.write_sep("-", f"Defectos de UI observados y no corregidos: {bugs}")


def assert_no_js_errors(page):
    assert page._ui_page_errors == [], page._ui_page_errors
    assert page._ui_console_errors == [], page._ui_console_errors


def record_ui_bug(description):
    _UI_BUGS.add(description)
