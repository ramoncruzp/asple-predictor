from pathlib import Path
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from playwright.sync_api import expect


ROOT = Path(__file__).resolve().parents[2]


def test_account_screen_has_write_only_credential_controls():
    html = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
    source = (ROOT / "frontend" / "cuenta.js").read_text(encoding="utf-8")
    assert 'id="screen-cuenta"' in html
    assert "testnet-credentials-form" in html
    assert "type=\"password\"" in html
    assert "cuenta-token" in source
    assert "api_key: apiKey" in source and "api_secret: apiSecret" in source
    assert "keyInput.value = ''; secretInput.value = ''" in source
    assert "TESTNET_API_KEY" not in source
    assert "TESTNET_SECRET" not in source
    for block in ("Conexión", "Balance Testnet", "Ganancias", "Conciliación contra Testnet"):
        assert block in source
    assert "aria-live=\"polite\"" in html
    assert "setOffline(" not in source
    assert "APP.apiHealthState" not in source


def test_realized_gain_chart_month_selector_bars_values_total_and_refresh(ui_page, live_server):
    today = datetime.now(timezone.utc).astimezone(ZoneInfo("America/Santo_Domingo")).date()
    month = today.strftime("%Y-%m")
    current_rows = [{"date": f"{month}-01", "cycles": 1.25, "stoploss": 0.0,
                     "liquidations": 0.0, "net_pnl_usdt": 1.25}]
    if today.day > 1:
        current_rows.append({"date": today.isoformat(), "cycles": -0.25, "stoploss": 0.0,
                             "liquidations": 0.0, "net_pnl_usdt": -0.25})
    previous_month_date = today.replace(day=1) - timedelta(days=1)
    previous_month = previous_month_date.strftime("%Y-%m")
    rows = [{"date": f"{previous_month}-01", "cycles": 2.0, "stoploss": 0.0,
             "liquidations": 0.0, "net_pnl_usdt": 2.0}, *current_rows]
    total = sum(row["net_pnl_usdt"] for row in current_rows)
    ui_page.route("**/api/account/connection", lambda route: route.fulfill(json={
        "testnet_confirmed": False, "key_suffix": None, "can_trade": None,
        "unavailable_reason": "sin conexión de prueba", "checked_at": datetime.now(timezone.utc).isoformat()}))
    ui_page.route("**/api/account/summary", lambda route: route.fulfill(json={
        "balance": None, "unavailable_reason": "sin balance de prueba",
        "reconciliation": {"grid_capital_usdt": "0", "usdt_free": None, "assets": []},
        "grids": {"open": [], "repository": [], "closed": []},
        "gains": {"realized_usdt": total, "fee_real_usdt": 0, "fee_estimated_0_1pct_usdt": 0,
            "unrealized_usdt": 0, "total_with_inventory_usdt": total, "operations": 2,
            "daily": {"all": rows}, "monthly": [
                {"month": previous_month, "net_pnl_usdt": 2.0, "cycles": 2.0,
                 "stoploss": 0, "liquidations": 0, "days_with_data": 1},
                {"month": month, "net_pnl_usdt": total, "cycles": total,
                 "stoploss": 0, "liquidations": 0, "days_with_data": len(current_rows)}]}}))
    ui_page.set_viewport_size({"width": 390, "height": 844})
    ui_page.goto(f"{live_server.url}/#cuenta")
    selector = ui_page.locator(".gain-segments")
    expect(selector).to_be_visible()
    expect(selector.locator("button")).to_have_count(3)
    selector.locator('[data-gain-period="month"]').click()
    chart = ui_page.locator(".cuenta-gain-chart")
    expected_days = today.day
    expect(chart.locator(".gain-bar")).to_have_count(expected_days)
    expect(chart.locator(".gain-bar-value")).to_have_count(expected_days)
    assert all(value.strip() for value in chart.locator(".gain-bar-value").all_text_contents())
    expected_total = ui_page.evaluate("value => window.ASPLEFormat.formatUsdt(value)", total)
    expect(chart.locator(".gain-total b")).to_have_text(expected_total)
    expect(selector.locator('[data-gain-period="month"]')).to_have_attribute("aria-pressed", "true")
    ui_page.locator('[data-month-step="-1"]').click()
    previous_days = (today.replace(day=1) - timedelta(days=1)).day
    expect(chart.locator(".gain-bar")).to_have_count(previous_days)
    assert ui_page.locator(".gain-chart-scroll").evaluate("node => node.scrollWidth > node.clientWidth")
    assert ui_page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    ui_page.locator('[data-month-step="1"]').click()
    ui_page.reload()
    expect(ui_page.locator(".cuenta-gain-chart .gain-bar")).to_have_count(expected_days)
    expect(ui_page.locator('.gain-segments [data-gain-period="month"]')).to_have_attribute("aria-pressed", "true")
