from __future__ import annotations

from playwright.sync_api import expect

from tests.ui.conftest import ROOT


def _open_close_dialog(page, server):
    page.goto(f"{server.url}/#grids/{server.grid_id}")
    expect(page.locator("[data-grid-controls]")).to_be_visible()
    close = page.locator('[data-grid-action="close"]').first
    expect(close).to_be_enabled()
    close.click()
    dialog = page.locator("#grid-action-dialog")
    expect(dialog).to_be_visible()
    return dialog


def test_close_mode_cards_highlight_one_selected_radio_and_keep_radios_compact(live_server, ui_page):
    dialog = _open_close_dialog(ui_page, live_server)
    options = dialog.locator('.grid-close-modes input[name="close-mode"]')
    cards = dialog.locator(".grid-close-modes label")
    expect(options).to_have_count(4)
    expect(cards).to_have_count(4)

    for index in range(options.count()):
        options.nth(index).check()
        assert ui_page.locator('.grid-close-modes input[name="close-mode"]:checked').count() == 1
        states = cards.evaluate_all("nodes => nodes.map(node => { const style = getComputedStyle(node); return { background: style.backgroundColor, border: style.borderColor }; })")
        selected = states[index]
        assert all(selected != state for other_index, state in enumerate(states) if other_index != index), states
        markers = cards.evaluate_all("nodes => nodes.map(node => getComputedStyle(node, '::after').content)")
        assert markers.count('\"Seleccionado\"') == 1, markers
        assert markers[index] == '\"Seleccionado\"'
        radio_width = options.nth(index).evaluate("node => node.getBoundingClientRect().width")
        assert radio_width <= 24, radio_width
        if index == 0:
            assert selected["border"] == "rgb(248, 81, 73)", {"states": states, "checked": options.evaluate_all("nodes => nodes.map(node => [node.value, node.checked])"), "css": ui_page.locator("link[rel=stylesheet]").evaluate_all("nodes => nodes.map(node => node.href)")}


def test_close_dialog_captures_desktop_and_mobile_layouts(live_server, ui_page):
    shots = ROOT / ".pytest-29d-shots" / "after"
    shots.mkdir(parents=True, exist_ok=True)
    for width, height in ((1440, 1000), (390, 844)):
        if ui_page.locator("#grid-action-dialog").is_visible():
            ui_page.locator("#grid-action-dialog [data-action-cancel]").click()
        ui_page.set_viewport_size({"width": width, "height": height})
        dialog = _open_close_dialog(ui_page, live_server)
        dialog.screenshot(path=str(shots / f"dialog-{width}x{height}.png"))
        if width == 390:
            expect(dialog.locator(".grid-close-modes")).to_be_visible()
            expect(dialog.locator(".grid-close-modes label")).to_have_count(4)
