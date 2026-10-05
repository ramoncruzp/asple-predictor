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

