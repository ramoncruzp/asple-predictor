from datetime import datetime, timedelta, timezone
import json

from playwright.sync_api import expect


def _level_without_order(live_server):
    level = live_server.db.get_grid_levels(live_server.grid_id)[0]
    live_server.db.update_level(live_server.grid_id, int(level["level_idx"]),
                                state="IDLE", order_id=None, client_order_id=None, held_qty=0.0, entry_price=None)
    return int(level["level_idx"])


def _open_detail(ui_page, live_server):
    ui_page.goto(f"{live_server.url}/#grids/{live_server.grid_id}")
    expect(ui_page.locator(".cells-table")).to_be_visible(timeout=10000)


def test_ladder_explains_order_presence_and_has_a_closed_legend(live_server, ui_page):
    level_idx = _level_without_order(live_server)
    live_level_idx = next(int(row["level_idx"]) for row in live_server.db.get_grid_levels(live_server.grid_id)
                          if int(row["level_idx"]) != level_idx)
    live_server.db.update_level(live_server.grid_id, live_level_idx, state="BUY_OPEN",
                                order_id=987654, held_qty=0.0)
    _open_detail(ui_page, live_server)

    legend = ui_page.locator("details.cells-legend")
    expect(legend.locator("summary")).to_have_text("\u00bfQu\u00e9 significan los estados?")
    assert legend.get_attribute("open") is None
    expect(legend).to_contain_text("Distancia a fill")
    expect(legend).to_contain_text("(precio objetivo - precio actual) / precio actual")
    row = ui_page.locator(f'.cell-row:has(td:first-child:text-is("{level_idx}"))')
    expect(row).to_have_class(__import__("re").compile(r"cell-no-order-warning"))
    expect(row.locator(".cell-order-status")).to_have_text("sin orden")
    live_row = ui_page.locator(f'.cell-row:has(td:first-child:text-is("{live_level_idx}"))')
    expect(live_row.locator(".cell-order-status")).to_have_text("con orden")
    assert "cell-no-order-warning" not in (live_row.get_attribute("class") or "")
    expect(row.locator(".cell-state")).to_have_attribute(
        "title", "Este nivel espera pero no tiene orden en Binance; el bot deber\u00eda recolocarla")

    live_server.db.update_grid(live_server.grid_id, status="PAUSED")
    ui_page.reload()
    paused_row = ui_page.locator(f'.cell-row:has(td:first-child:text-is("{level_idx}"))')
    expect(paused_row.locator(".cell-state")).to_have_text("pausado")
    assert "cell-no-order-warning" not in (paused_row.get_attribute("class") or "")


def test_level_error_shows_latest_translated_reason_and_age(live_server, ui_page):
    level_idx = _level_without_order(live_server)
    live_server.db.update_level(live_server.grid_id, level_idx, state="ERROR")
    now = datetime.now(timezone.utc)
    payload = {"events": [
        {"type": "LEVEL_ERROR", "level_idx": level_idx, "reason": "policy_adjust",
         "ts": (now - timedelta(hours=3)).isoformat(), "message": "Error de nivel", "severity": "error"},
        {"type": "LEVEL_ERROR", "level_idx": level_idx, "reason": "older raw reason",
         "ts": (now - timedelta(hours=4)).isoformat(), "message": "Error de nivel", "severity": "error"},
    ]}
    ui_page.route(f"**/api/grids/{live_server.grid_id}/events",
                  lambda route: route.fulfill(json=payload))
    _open_detail(ui_page, live_server)
    row = ui_page.locator(f'.cell-row:has(td:first-child:text-is("{level_idx}"))')
    expect(row).to_have_class(__import__("re").compile(r"cell-error-row"))
    expect(row.locator(".cell-error-reason")).to_contain_text("Reajuste por pol\u00edtica")
    expect(row.locator(".cell-error-reason")).to_contain_text("hace 3 h")


def test_level_error_without_event_uses_the_required_fallback(live_server, ui_page):
    level_idx = _level_without_order(live_server)
    live_server.db.update_level(live_server.grid_id, level_idx, state="ERROR")
    ui_page.route(f"**/api/grids/{live_server.grid_id}/events",
                  lambda route: route.fulfill(json={"events": []}))
    _open_detail(ui_page, live_server)
    row = ui_page.locator(f'.cell-row:has(td:first-child:text-is("{level_idx}"))')
    expect(row.locator(".cell-error-reason")).to_have_text(
        "motivo no disponible (ver Eventos del bot)")


def test_experiment_card_shows_existing_values_and_copies_plain_summary(live_server, ui_page):
    level = live_server.db.get_grid_levels(live_server.grid_id)[0]
    level_idx = int(level["level_idx"])
    live_server.db.update_level(live_server.grid_id, level_idx,
                                cycles_completed=3, pnl=12.34, fee_paid=0.56)
    live_server.db.update_grid(
        live_server.grid_id,
        created_at=datetime.now(timezone.utc) - timedelta(days=3),
    )
    live_server.db.merge_grid_params(
        live_server.grid_id, {"max_days": 14}, allowed=frozenset({"max_days"}),
    )
    ui_page.add_init_script("Object.defineProperty(navigator, 'clipboard', {configurable:true, value:{writeText: text => {window.__copiedSummary=text; return Promise.resolve();}}})")
    _open_detail(ui_page, live_server)
    card = ui_page.locator(".experiment-card")
    expect(card).to_contain_text("Experimento")
    expect(card).to_contain_text("3.0 d")
    expect(card).to_contain_text("11.0 d")
    expect(card).to_contain_text("Ciclos completados")
    expect(card).to_contain_text("3")
    expect(card).to_contain_text("12.34 USDT")
    expect(card).to_contain_text("Testnet cobra 0 de comisi\u00f3n")
    expect(card.locator('[role="progressbar"]')).to_be_visible()
    ui_page.get_by_role("button", name="Copiar resumen").click()
    ui_page.wait_for_function("window.__copiedSummary !== undefined")
    copied = ui_page.evaluate("window.__copiedSummary")
    assert f"Grid #{live_server.grid_id} XRPUSDT" in copied
    assert "Edad: 3.0 d" in copied and "D\u00edas restantes: 11.0 d" in copied
    assert "Ciclos completados: 3" in copied
    assert "Neta realizada: 12.34 USDT" in copied
    assert "Neta tras comisiones estimadas:" in copied
    assert "Rango:" in copied and "Niveles:" in copied and "Capital:" in copied
    assert "Precio actual:" in copied and "Generado:" in copied

    live_server.db.merge_grid_params(
        live_server.grid_id, {"max_days": 2}, allowed=frozenset({"max_days"}),
    )
    ui_page.reload()
    expect(ui_page.locator(".experiment-expired")).to_have_text("El plazo m\u00e1ximo ha vencido.")
    expect(ui_page.locator(".experiment-card")).to_contain_text("0.0 d")
