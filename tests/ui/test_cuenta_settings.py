from playwright.sync_api import expect


def test_global_settings_section_confirms_changes_and_explains_live_effect(ui_page, live_server):
    requests = []
    flag = {"current": False}

    def api(route):
        request = route.request
        path = request.url.split(live_server.url, 1)[-1].split("?", 1)[0]
        if path == "/api/settings/flags" and request.method == "GET":
            route.fulfill(json={"flags": [{
                "key": "adjust_idle_shrink_enabled",
                "label": "Reducci\u00f3n de niveles ociosos",
                "description": "Permite que grids Smart quiten niveles sin ciclos en 24 h. Apagado por defecto: en simulaci\u00f3n XRP no mejor\u00f3 la ganancia. Encenderlo aqu\u00ed solo habilita el bot\u00f3n por grid; no activa la regla en ning\u00fan grid.",
                "current": flag["current"], "default": False,
                "origin": "default" if not flag["current"] else "app",
                "requires_restart": False,
            }]})
        elif path == "/api/settings/flags" and request.method == "POST":
            body = request.post_data_json
            requests.append(body)
            if body["dry_run"]:
                route.fulfill(json={"dry_run": True, "plan": {"previous": flag["current"], "value": body["value"], "requires_restart": False}})
            else:
                flag["current"] = body["value"]
                route.fulfill(json={"dry_run": False, "changed": True, "updated_at": "2026-10-12T12:00:00+00:00"})
        elif path.startswith("/api/account/"):
            route.fulfill(status=403, json={"detail": "auth required"})
        else:
            route.fulfill(json={})

    ui_page.route("**/api/**", api)
    ui_page.goto(f"{live_server.url}/#cuenta")
    panel = ui_page.locator("#global-settings")
    expect(panel).to_be_visible()
    expect(panel).to_contain_text("Ajustes globales")
    expect(panel).to_contain_text("Apagado por defecto: en simulaci\u00f3n XRP no mejor\u00f3 la ganancia.")
    expect(panel).to_contain_text("Encenderlo aqu\u00ed solo habilita el bot\u00f3n por grid; no activa la regla en ning\u00fan grid.")
    expect(panel).to_contain_text("Estado: Apagado")
    ui_page.get_by_role("button", name="Encender").click()
    dialog = ui_page.locator("#global-flag-dialog")
    expect(dialog).to_be_visible()
    expect(dialog).to_contain_text("Encender el interruptor Reducci\u00f3n de niveles ociosos")
    expect(dialog).to_contain_text("sin reiniciar")
    ui_page.get_by_role("button", name="Confirmar cambio").click()
    expect(panel).to_contain_text("Estado: Encendido")
    assert requests == [
        {"key": "adjust_idle_shrink_enabled", "value": True, "dry_run": True, "confirm": False},
        {"key": "adjust_idle_shrink_enabled", "value": True, "dry_run": False, "confirm": True},
    ]
    expect(panel).to_contain_text("Cambiado a las")
    assert "hora local" in ui_page.locator("#global-settings-status").inner_text()


def test_stoploss_global_flag_appears_with_disable_button(ui_page, live_server):
    description = (
        "Con el interruptor encendido, una celda con posición se vende a mercado cuando el precio cae por debajo "
        "de su precio de compra menos su stop_loss_pct (5 % por defecto). Apagado, esa regla no se ejecuta y las "
        "posiciones esperan su venta normal (la pérdida queda sin realizar y puede crecer). No afecta a las demás "
        "protecciones de la política. El cambio aplica en el siguiente ciclo del monitor sin reiniciar."
    )

    def api(route):
        request = route.request
        path = request.url.split(live_server.url, 1)[-1].split("?", 1)[0]
        if path == "/api/settings/flags" and request.method == "GET":
            route.fulfill(json={"flags": [{
                "key": "stoploss_enabled", "label": "Stop-loss por celda", "description": description,
                "current": True, "default": True, "origin": "default", "requires_restart": False,
            }]})
        elif path.startswith("/api/account/"):
            route.fulfill(status=403, json={"detail": "auth required"})
        else:
            route.fulfill(json={})

    ui_page.route("**/api/**", api)
    ui_page.goto(f"{live_server.url}/#cuenta")
    row = ui_page.locator('[data-flag-key="stoploss_enabled"]')
    expect(row).to_be_visible()
    expect(row).to_contain_text("Stop-loss por celda")
    expect(row).to_contain_text("la pérdida queda sin realizar y puede crecer")
    expect(row).to_contain_text("siguiente ciclo del monitor sin reiniciar")
    expect(row.get_by_role("button", name="Apagar")).to_be_visible()
