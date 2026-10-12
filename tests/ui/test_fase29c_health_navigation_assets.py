from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import re
from urllib.parse import urlparse

import pytest
from playwright.sync_api import expect

from tests.ui.conftest import ROOT, assert_no_js_errors


def _monitor_payload(age_seconds=60, *, reset=False, interval_minutes=15, status="OK"):
    finished = datetime.now(timezone.utc) - timedelta(seconds=age_seconds)
    return {
        "last_run": {"finished_at": finished.isoformat(), "started_at": finished.isoformat(), "status": status},
        "testnet_reset_suspected": reset,
        "gap_minutes_configured": interval_minutes,
    }


@pytest.mark.parametrize(("age_seconds", "expected"), [(60, "health-ok"), (45 * 60, "health-amber"), (90 * 60, "health-red")])
def test_monitor_health_color_uses_configured_interval(ui_page, live_server, age_seconds, expected):
    ui_page.route("**/api/monitor/status", lambda route: route.fulfill(json=_monitor_payload(age_seconds)))
    ui_page.route("**/api/alerts/status", lambda route: route.fulfill(json={"last_sent": None}))
    ui_page.goto(f"{live_server.url}/#dashboard")
    expect(ui_page.locator("#monitor-chip")).to_have_class(re.compile(expected))


def test_monitor_failure_is_gray_and_testnet_reset_is_shown(ui_page, live_server):
    ui_page.route("**/api/monitor/status", lambda route: route.fulfill(json=_monitor_payload(60, reset=True)))
    ui_page.route("**/api/alerts/status", lambda route: route.fulfill(json={"last_sent": None}))
    ui_page.goto(f"{live_server.url}/#dashboard")
    expect(ui_page.locator("#testnet-reset-indicator")).to_be_visible()
    expect(ui_page.locator("#testnet-reset-indicator")).to_contain_text("Posible reinicio de Testnet")


def test_failed_monitor_status_is_red_even_when_recent(ui_page, live_server):
    ui_page.route("**/api/monitor/status", lambda route: route.fulfill(json=_monitor_payload(60, status="FAILED")))
    ui_page.route("**/api/alerts/status", lambda route: route.fulfill(json={"last_sent": None}))
    ui_page.goto(f"{live_server.url}/#dashboard")
    expect(ui_page.locator("#monitor-chip")).to_have_class(re.compile("health-red"))


def test_default_monitor_interval_uses_thirty_minute_green_threshold(ui_page, live_server):
    payload = _monitor_payload(25 * 60)
    payload.pop("gap_minutes_configured")
    ui_page.route("**/api/monitor/status", lambda route: route.fulfill(json=payload))
    ui_page.route("**/api/alerts/status", lambda route: route.fulfill(json={"last_sent": None}))
    ui_page.goto(f"{live_server.url}/#dashboard")
    expect(ui_page.locator("#monitor-chip")).to_have_class(re.compile("health-ok"))


def test_failed_monitor_endpoint_is_gray_without_blocking_alert_indicator(ui_page, live_server):
    ui_page.route("**/api/monitor/status", lambda route: route.abort())
    ui_page.route("**/api/alerts/status", lambda route: route.fulfill(json={"last_sent": {
        "ts": datetime.now(timezone.utc).isoformat(), "alert_key": "DUST_RECONCILIATION"}}))
    ui_page.goto(f"{live_server.url}/#dashboard")
    expect(ui_page.locator("#monitor-chip")).to_have_class(re.compile("health-unknown"))
    expect(ui_page.locator("#last-alert-indicator")).to_contain_text("DUST_RECONCILIATION")


def test_failed_alert_endpoint_is_gray_without_blocking_monitor(ui_page, live_server):
    ui_page.route("**/api/monitor/status", lambda route: route.fulfill(json=_monitor_payload()))
    ui_page.route("**/api/alerts/status", lambda route: route.abort())
    ui_page.goto(f"{live_server.url}/#dashboard")
    expect(ui_page.locator("#monitor-chip")).to_have_class(re.compile("health-ok"))
    expect(ui_page.locator("#last-alert-indicator")).to_have_class(re.compile("health-unknown"))


def test_protected_indicators_share_one_token_prompt(ui_page, live_server):
    prompts = []
    ui_page.add_init_script("window.prompt = message => { window.__phase29cPrompts.push(message); return null; }; window.__phase29cPrompts = [];")
    ui_page.route("**/api/monitor/status", lambda route: route.fulfill(status=403, json={"detail": "token requerido"}))
    ui_page.route("**/api/alerts/status", lambda route: route.fulfill(status=403, json={"detail": "token requerido"}))
    ui_page.goto(f"{live_server.url}/#dashboard")
    expect(ui_page.locator("#monitor-chip")).to_have_class(re.compile("health-unknown"))
    expect(ui_page.locator("#last-alert-indicator")).to_have_class(re.compile("health-unknown"))
    prompts.extend(ui_page.evaluate("window.__phase29cPrompts"))
    assert len(prompts) == 1


def test_latest_alert_escapes_api_text(ui_page, live_server):
    ui_page.route("**/api/monitor/status", lambda route: route.fulfill(json=_monitor_payload()))
    ui_page.route("**/api/alerts/status", lambda route: route.fulfill(json={"last_sent": {
        "ts": datetime.now(timezone.utc).isoformat(), "alert_key": "<img src=x onerror=alert(1)>"}}))
    ui_page.goto(f"{live_server.url}/#dashboard")
    expect(ui_page.locator("#last-alert-indicator")).to_contain_text("<img src=x onerror=alert(1)>")
    assert ui_page.locator("#last-alert-indicator img").count() == 0


def test_mobile_menu_has_eight_routes_and_closes_accessibly(ui_page, live_server):
    ui_page.set_viewport_size({"width": 390, "height": 844})
    ui_page.goto(f"{live_server.url}/#dashboard")
    button = ui_page.locator("#mobile-menu-toggle")
    menu = ui_page.locator("#mobile-route-menu")
    expect(button).to_have_attribute("aria-expanded", "false")
    button.click()
    expect(button).to_have_attribute("aria-expanded", "true")
    expect(menu.locator("a")).to_have_count(8)
    expect(menu.locator('[data-mobile-route="dashboard"]')).to_have_class(re.compile("active"))
    menu.locator('[data-mobile-route="coins"]').click()
    expect(button).to_have_attribute("aria-expanded", "false")
    expect(menu.locator('[data-mobile-route="coins"]')).to_have_class(re.compile("active"))
    button.click()
    ui_page.mouse.click(380, 220)
    expect(button).to_have_attribute("aria-expanded", "false")
    button.click()
    assert ui_page.locator("#mobile-route-menu a").first.evaluate("node => node.getBoundingClientRect().height") >= 44
    assert button.evaluate("node => node.getBoundingClientRect().height") >= 44
    ui_page.keyboard.press("Escape")
    expect(button).to_have_attribute("aria-expanded", "false")
    expect(button).to_be_focused()


def test_health_polling_does_not_duplicate_when_routes_change(ui_page, live_server):
    calls = []
    ui_page.add_init_script("""
      window.__phase29cIntervals = [];
      const originalSetInterval = window.setInterval.bind(window);
      window.setInterval = (callback, delay, ...args) => {
        const id = originalSetInterval(callback, delay, ...args);
        if (delay === 60000) window.__phase29cIntervals.push({id, callback});
        return id;
      };
    """)
    ui_page.route("**/api/monitor/status", lambda route: (calls.append(route.request.url), route.fulfill(json=_monitor_payload()))[-1])
    ui_page.route("**/api/alerts/status", lambda route: route.fulfill(json={"last_sent": None}))
    ui_page.goto(f"{live_server.url}/#dashboard")
    expect(ui_page.locator("#monitor-chip")).to_contain_text("Monitor: hace")
    initial_health_timer = ui_page.evaluate("window.APP.healthRefreshTimer")
    for path in ("battle", "models", "scanner", "grids", "cuenta", "dashboard"):
        ui_page.evaluate("path => { location.hash = '#' + path; }", path)
        ui_page.wait_for_timeout(30)
    health_timer = ui_page.evaluate("window.APP.healthRefreshTimer")
    assert health_timer == initial_health_timer
    assert ui_page.evaluate("id => window.__phase29cIntervals.filter(entry => entry.id === id).length", health_timer) == 1
    assert len(calls) == 1
    ui_page.evaluate("""() => {
      const timer = window.__phase29cIntervals.find(entry => entry.id === window.APP.healthRefreshTimer);
      timer.callback();
    }""")
    ui_page.wait_for_timeout(100)
    assert len(calls) == 2


def test_local_chart_and_fonts_load_without_external_requests(ui_page, live_server):
    external = []
    ui_page.on("request", lambda request: external.append(request.url)
               if (urlparse(request.url).hostname or "") not in {"127.0.0.1", "localhost"} else None)
    ui_page.goto(f"{live_server.url}/#dashboard")
    ui_page.wait_for_function("typeof window.LightweightCharts?.createChart === 'function'")
    ui_page.evaluate("document.fonts.load('14px Inter'); document.fonts.load('14px \\\"JetBrains Mono\\\"')")
    ui_page.evaluate("document.fonts.ready")
    assert ui_page.evaluate("document.fonts.check('14px Inter') && document.fonts.check('14px \\\"JetBrains Mono\\\"')")
    assert external == []
    assert_no_js_errors(ui_page)


def test_index_references_only_local_vendor_assets():
    html = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
    assert "http://" not in html and "https://" not in html
    assert 'vendor/fonts.css' in html
    assert 'vendor/lightweight-charts.standalone.production.js' in html


def test_every_vendor_asset_matches_its_recorded_sha256():
    expected = {
        "LICENSE-lightweight-charts.txt": "70c9d5382506dd184465425c08a99ad9bd6d9ac1313c252968ba0b585e5ef823",
        "lightweight-charts.standalone.production.js": "0c146b9bfbc6e5f19da1a0de876dae43023c332d5157d82f7575f9b7653ee519",
        "fonts.css": "76cb6d0b079d884504338c5c84f4a077c1ec14127ad2d48a8933e538c4591713",
        "fonts/Inter-OFL.txt": "262481e844521b326f5ecd053e59b98c8b2da78c8ee1bdbb6e8174305e54935a",
        "fonts/InterVariable.woff2": "693b77d4f32ee9b8bfc995589b5fad5e99adf2832738661f5402f9978429a8e3",
        "fonts/JetBrainsMono-OFL.txt": "78052cf9d914eeaf3c14221584a00feeab4e158a7f34681c05da53fa01de5221",
        "fonts/JetBrainsMonoVariable.woff2": "31ec365b93e4bad6f202ce23352a56d01ca4462b2afc782ed2cf6fa42ca9ac0e",
    }
    vendor = ROOT / "frontend" / "vendor"
    actual_files = {path.relative_to(vendor).as_posix() for path in vendor.rglob("*") if path.is_file()}
    assert actual_files == set(expected)
    for relative_path, recorded_hash in expected.items():
        file_hash = hashlib.sha256((vendor / relative_path).read_bytes()).hexdigest()
        assert file_hash == recorded_hash, f"SHA-256 distinto para frontend/vendor/{relative_path}"
