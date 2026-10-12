from __future__ import annotations

from datetime import datetime, timedelta, timezone
import re

import pytest
from playwright.sync_api import expect


def _page(chromium_browser, timezone_id):
    return chromium_browser.new_page(viewport={"width": 1440, "height": 1000}, timezone_id=timezone_id)


@pytest.mark.parametrize("timezone_id", ["America/Santo_Domingo", "UTC"])
def test_api_time_parser_preserves_explicit_zones_and_assumes_utc_for_naive(chromium_browser, live_server, timezone_id):
    page = _page(chromium_browser, timezone_id)
    page.goto(f"{live_server.url}/#dashboard")
    result = page.evaluate("""() => {
      const parse = value => window.ASPLEFormat.parseApiTime(value);
      return [parse('2026-01-02T03:04:05'), parse('2026-01-02T03:04:05Z'), parse('2026-01-02T03:04:05-04:00')];
    }""")
    assert result == [1767323045000, 1767323045000, 1767337445000]
    page.close()


@pytest.mark.parametrize("timezone_id", ["America/Santo_Domingo", "UTC"])
def test_naive_monitor_timestamp_uses_utc_age_and_staleness(chromium_browser, live_server, timezone_id):
    page = _page(chromium_browser, timezone_id)
    old = (datetime.now(timezone.utc) - timedelta(minutes=5)).replace(tzinfo=None).isoformat(timespec="seconds")
    page.route("**/api/monitor/status", lambda route: route.fulfill(json={
        "last_run": {"finished_at": old, "status": "OK"}, "gap_minutes_configured": 15,
    }))
    page.route("**/api/alerts/status", lambda route: route.fulfill(json={"last_sent": None}))
    page.goto(f"{live_server.url}/#dashboard")
    chip = page.locator("#monitor-chip")
    expect(chip).to_contain_text(re.compile(r"hace [45] min"))
    expect(chip).not_to_contain_text("0 s")
    expect(chip).to_have_class(re.compile("health-ok"))
    page.close()


@pytest.mark.parametrize("timezone_id", ["America/Santo_Domingo", "UTC"])
def test_two_hour_monitor_is_stale_and_future_clock_skew_is_red(chromium_browser, live_server, timezone_id):
    page = _page(chromium_browser, timezone_id)
    old = (datetime.now(timezone.utc) - timedelta(hours=2)).replace(tzinfo=None).isoformat(timespec="seconds")
    payload = {"last_run": {"finished_at": old, "status": "OK"}, "gap_minutes_configured": 15}
    page.route("**/api/monitor/status", lambda route: route.fulfill(json=payload))
    page.route("**/api/alerts/status", lambda route: route.fulfill(json={"last_sent": None}))
    page.goto(f"{live_server.url}/#dashboard")
    chip = page.locator("#monitor-chip")
    expect(chip).to_have_class(re.compile(r"health-(amber|red)"))
    expect(chip).to_contain_text("hace 2 h")
    payload["last_run"]["finished_at"] = (datetime.now(timezone.utc) + timedelta(minutes=3)).replace(tzinfo=None).isoformat(timespec="seconds")
    page.evaluate("window.refreshOperationalHealth && window.refreshOperationalHealth()")
    page.wait_for_timeout(100)
    expect(chip).to_have_class(re.compile("health-red"))
    expect(chip).to_contain_text("reloj desajustado")
    page.close()
