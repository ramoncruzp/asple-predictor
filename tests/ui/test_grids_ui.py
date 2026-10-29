from __future__ import annotations

from playwright.sync_api import expect


def test_grids_tab_lists_cards_and_current_enabled_controls(live_server, ui_page):
    """The old assertion expected an ACTIVE grid's pause control to be disabled.

    Since 17B, ACTIVE grids expose an enabled pause action; only an open modal
    temporarily disables the grid controls.
    """
    ui_page.goto(f"{live_server.url}/#grids")
    expect(ui_page.locator(".grid-row-card").first).to_be_visible()
    assert ui_page.locator(".grid-row-card").count() >= 2
    assert ui_page.locator(".grids-warning").count() == 1

    ui_page.locator(".grid-row-card").first.click()
    expect(ui_page.locator(".grid-detail-header")).to_be_visible()
    pause = ui_page.get_by_role("button", name="Pausar", exact=True)
    expect(pause).to_be_visible()
    expect(pause).to_be_enabled()
    assert ui_page.locator(".price-marker-row").count() <= 1


def test_grids_empty_and_error_states(live_server, ui_page):
    ui_page.goto(f"{live_server.url}/#grids/999999")
    expect(ui_page.locator(".grids-error")).to_contain_text("no existe")


def test_daily_profit_shows_date_and_signed_amount_for_each_day(live_server, ui_page):
    ui_page.route(
        f"**/api/grids/{live_server.grid_id}/daily**",
        lambda route: route.fulfill(json={
            "timezone": "America/Santo_Domingo",
            "daily": [
                {"date": "2026-10-26", "net_pnl_usdt": 0.45},
                {"date": "2026-10-28", "net_pnl_usdt": -0.12},
            ],
            "net_realized_usdt": 0.33,
            "unattributed_usdt": None,
            "unattributed_note": None,
            "truncated": False,
        }),
    )
    ui_page.goto(f"{live_server.url}/#grids")
    ui_page.locator(".grid-row-card").first.click()

    days = ui_page.locator(".daily-bar-wrap")
    expect(days).to_have_count(3)
    expect(days.nth(0).locator(".daily-date")).to_have_text("26/10")
    expect(days.nth(0).locator(".daily-amount")).to_have_text("+0,45")
    expect(days.nth(1).locator(".daily-date")).to_have_text("27/10")
    expect(days.nth(1).locator(".daily-amount")).to_have_text("0,00")
    expect(days.nth(2).locator(".daily-date")).to_have_text("28/10")
    expect(days.nth(2).locator(".daily-amount")).to_have_text("−0,12")
    expect(ui_page.locator(".daily-summary")).to_contain_text("Total del período: +0,33 USDT")

