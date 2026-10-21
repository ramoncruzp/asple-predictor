from datetime import datetime, timezone
from pathlib import Path

from playwright.sync_api import expect


def _mock_lab_api(ui_page, live_server, *, fail_apply=False):
    calls = []
    flags = [
        {"key": "adjust_idle_shrink_enabled", "label": "Reducción de niveles ociosos", "description": "Regla de prueba.",
         "current": False, "default": False, "origin": "default", "requires_restart": False},
        {"key": "stoploss_enabled", "label": "Stop-loss por celda", "description": "Regla de prueba desde la API.",
         "current": True, "default": False, "origin": "app", "requires_restart": False},
        {"key": "smart_adjust_enabled", "label": "ADJUST automático de Smart", "description": "Regla de prueba.",
         "current": True, "default": True, "origin": "default", "requires_restart": False},
        {"key": "smart_close_wait_enabled", "label": "Fuera de rango: esperar en vez de cerrar", "description": "Regla de prueba.",
         "current": False, "default": False, "origin": "default", "requires_restart": False},
        {"key": "advisor_vol_spacing_enabled", "label": "Espaciado por volatilidad", "description": "Regla de prueba.",
         "current": False, "default": False, "origin": "default", "requires_restart": False},
    ]
    now = datetime.now(timezone.utc).isoformat()

    def api(route):
        req = route.request
        path = req.url.split(live_server.url, 1)[-1].split("?", 1)[0]
        if path == "/api/settings/flags" and req.method == "GET":
            route.fulfill(json={"flags": flags})
        elif path == "/api/settings/flags" and req.method == "POST":
            body = req.post_data_json
            calls.append(body)
            if body["dry_run"]:
                current = next(row["current"] for row in flags if row["key"] == body["key"])
                route.fulfill(json={"dry_run": True, "plan": {"previous": current, "value": body["value"], "requires_restart": False}})
            elif fail_apply:
                route.fulfill(status=422, json={"detail": "Cambio rechazado por la API."})
            else:
                for row in flags:
                    if row["key"] == body["key"]:
                        row["current"] = body["value"]
                        row["origin"] = "app"
                route.fulfill(json={"dry_run": False, "changed": True, "flag": {"key": body["key"]}, "updated_at": now})
        elif path == "/api/grids":
            route.fulfill(json={"grids": [{"id": 17}]})
        elif path == "/api/grids/17/events":
            route.fulfill(json={"events": [
                {"type": "CELL_STOPLOSS", "ts": now},
                {"type": "STOPLOSS_SUSPENDED", "ts": now},
                {"type": "CELL_STOPLOSS", "ts": "2020-01-01T00:00:00+00:00"},
            ]})
        elif path.startswith("/api/account/"):
            route.fulfill(status=403, json={"detail": "auth required"})
        else:
            route.fulfill(json={})

    ui_page.route("**/api/**", api)
    return calls


def test_lab_route_dynamic_flags_badges_stats_and_enable_disable_flow(ui_page, live_server):
    calls = _mock_lab_api(ui_page, live_server)
    shots = Path(__file__).resolve().parents[2] / ".pytest-31-shots"
    shots.mkdir(exist_ok=True)
    ui_page.goto(f"{live_server.url}/#cuenta")
    expect(ui_page.locator("#global-settings")).to_be_visible()
    expect(ui_page.locator('#global-settings-content [data-flag-key="stoploss_enabled"]')).to_be_visible()
    expect(ui_page.locator("#global-settings-content [data-flag-key]")).to_have_count(5)
    ui_page.screenshot(path=str(shots / "desktop-before-cuenta.png"), full_page=False)
    ui_page.goto(f"{live_server.url}/#lab")
    expect(ui_page.locator("#lab-method")).to_be_visible()
    expect(ui_page.locator('#lab-global-settings-content [data-flag-key="stoploss_enabled"]')).to_be_visible()
    expect(ui_page.locator("#lab-stoploss-stats-content")).to_contain_text("CELL_STOPLOSS")
    ui_page.screenshot(path=str(shots / "desktop-after-laboratorio.png"), full_page=False)
    ui_page.set_viewport_size({"width": 390, "height": 844})
    ui_page.goto(f"{live_server.url}/#cuenta")
    expect(ui_page.locator("#global-settings")).to_be_visible()
    expect(ui_page.locator('#global-settings-content [data-flag-key="stoploss_enabled"]')).to_be_visible()
    expect(ui_page.locator("#global-settings-content [data-flag-key]")).to_have_count(5)
    ui_page.screenshot(path=str(shots / "mobile-before-cuenta.png"), full_page=False)
    ui_page.goto(f"{live_server.url}/#lab")
    expect(ui_page.locator('#lab-global-settings-content [data-flag-key="stoploss_enabled"]')).to_be_visible()
    expect(ui_page.locator("#lab-stoploss-stats-content")).to_contain_text("CELL_STOPLOSS")
    ui_page.screenshot(path=str(shots / "mobile-after-laboratorio.png"), full_page=False)
    expect(ui_page.locator("#screen-lab")).to_be_visible()
    expect(ui_page.locator('[data-route="lab"]')).to_have_class("active")
    host = ui_page.locator("#lab-global-settings-content")
    expect(host.locator('[data-flag-key="stoploss_enabled"]')).to_contain_text("distinto del valor por defecto")
    expect(host.locator('[data-flag-key]')).to_have_count(5)
    for key in ("adjust_idle_shrink_enabled", "smart_adjust_enabled",
                "smart_close_wait_enabled", "advisor_vol_spacing_enabled"):
        card = host.locator(f'[data-flag-key="{key}"]')
        expect(card).to_be_visible()
        expect(card.get_by_role("button")).to_have_count(1)
    expect(ui_page.locator("#lab-stoploss-stats-content")).to_contain_text("CELL_STOPLOSS (vend\u00f3): 1")
    expect(ui_page.locator("#lab-stoploss-stats-content")).to_contain_text("STOPLOSS_SUSPENDED (se habr\u00eda vendido): 1")
    expect(ui_page.locator("#lab-stoploss-stats-content")).to_contain_text("Neto de CELL_STOPLOSS: sin datos")

    stoploss_card = host.locator('[data-flag-key="stoploss_enabled"]')
    stoploss_card.get_by_role("button", name="Apagar").click()
    expect(ui_page.locator("#global-flag-dialog")).to_be_visible()
    ui_page.get_by_role("button", name="Confirmar cambio").click()
    expect(host.locator('[data-flag-key="stoploss_enabled"]')).to_contain_text("Apagado")
    stoploss_card.get_by_role("button", name="Encender").click()
    ui_page.get_by_role("button", name="Confirmar cambio").click()
    expect(ui_page.locator("#lab-global-settings-status")).to_have_text("Ajuste global actualizado.")
    expect(host.locator(".global-flag-updated")).to_contain_text("Cambiado a las")
    assert ui_page.locator("#lab-global-settings-content .global-flag-updated").count() == 1
    assert calls == [
        {"key": "stoploss_enabled", "value": False, "dry_run": True, "confirm": False},
        {"key": "stoploss_enabled", "value": False, "dry_run": False, "confirm": False},
        {"key": "stoploss_enabled", "value": True, "dry_run": True, "confirm": False},
        {"key": "stoploss_enabled", "value": True, "dry_run": False, "confirm": True},
    ]


def test_lab_422_keeps_page_usable(ui_page, live_server):
    _mock_lab_api(ui_page, live_server, fail_apply=True)
    ui_page.goto(f"{live_server.url}/#lab")
    host = ui_page.locator("#lab-global-settings-content")
    host.locator('[data-flag-key="stoploss_enabled"]').get_by_role("button", name="Apagar").click()
    ui_page.get_by_role("button", name="Confirmar cambio").click()
    expect(ui_page.locator("#lab-global-settings-status")).to_have_text("Cambio rechazado por la API.")
    expect(host.locator('[data-flag-key="smart_close_wait_enabled"]')).to_be_visible()
    expect(host.locator('[data-flag-key="stoploss_enabled"]').get_by_role(
        "button", name="Apagar")).to_be_enabled()


def test_lab_stats_degrade_when_events_unavailable(ui_page, live_server):
    def api(route):
        path = route.request.url.split(live_server.url, 1)[-1].split("?", 1)[0]
        if path == "/api/settings/flags": route.fulfill(json={"flags": []})
        elif path == "/api/grids": route.fulfill(json={"grids": [{"id": 17}]})
        elif path.endswith("/events"): route.fulfill(status=403, json={"detail": "no disponible"})
        elif path.startswith("/api/account/"): route.fulfill(status=403, json={"detail": "auth required"})
        else: route.fulfill(json={})
    ui_page.route("**/api/**", api)
    ui_page.goto(f"{live_server.url}/#lab")
    expect(ui_page.locator("#lab-stoploss-stats-content")).to_contain_text("Sin datos disponibles")


def test_mobile_menu_has_nine_routes_including_lab(ui_page, live_server):
    _mock_lab_api(ui_page, live_server)
    ui_page.set_viewport_size({"width": 390, "height": 844})
    ui_page.goto(f"{live_server.url}/#lab")
    ui_page.locator("#mobile-menu-toggle").click()
    links = ui_page.locator("[data-mobile-route]")
    expect(links).to_have_count(9)
    expect(links.filter(has_text="Laboratorio")).to_have_count(1)
