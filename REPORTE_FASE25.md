# Fase 25: ajustes globales

Preflight: `HEAD 836ff9e`, árbol limpio.

## Estado por requisito

| Requisito | Estado | Evidencia |
|---|---|---|
| Lista blanca v1 y lectura GET | HECHO | `config/settings.py:87-98`; `api/routes/app_settings.py:21-40` expone solo `adjust_idle_shrink_enabled`, valor, default, origen y reinicio. |
| Confirmación, apagado y validación estricta | HECHO | `api/routes/app_settings.py:12-19, 42-58`; booleano `StrictBool`, campos extra prohibidos, encendido ejecutable exige confirmación y apagar se permite. |
| Persistencia, precedencia y evento | HECHO | `database/db_manager.py:224-230, 269-270, 1464-1500`; `create_all` crea la tabla idempotentemente; valor y `APP_SETTING_CHANGED` se guardan en una transacción. `api/main.py:47-53` aplica DB sobre Settings antes de crear consumidores. |
| Efecto en vivo sobre el objeto compartido | HECHO en código; NO VERIFICADO en proceso real | `api/main.py:47-53, 114, 127`; `GridMonitor` recibe `settings` y conserva la referencia (`grid/monitor.py:46`), y lee `adjust_idle_shrink_enabled` en `grid/monitor.py:612`. Las rutas consultan `request.app.state.settings` en `grid/control_service.py:214-216` y `api/routes/grids.py:1029-1033, 1062-1065`. |
| Sección Cuenta y enlace desde el grid | HECHO | `frontend/index.html:28`; `frontend/cuenta.js:63-95, 105-119`; `frontend/cuenta.css:3`; enlace a Cuenta en `frontend/grids.js:368`; cobertura Playwright en `tests/ui/test_cuenta_settings.py:4` y `tests/ui/test_grids_browser.py:1201-1250`. |
| `grid_policy_enabled` / `grid_monitor_enabled` | EVALUADO; no editables en v1 | `grid_policy_enabled` se consulta durante cada evaluación del monitor (`grid/monitor.py:381`), pero se excluye porque la lista blanca v1 solo autoriza el interruptor de reducción ociosa. `grid_monitor_enabled` decide la construcción del monitor durante el arranque (`api/main.py:113, 120-125`); requeriría reinicio. |

La descripción presentada para `adjust_idle_shrink_enabled` conserva el texto solicitado. El default de `Settings` sigue siendo `False` (`config/settings.py:46`). `api/main.py:47-53` crea un objeto `Settings`, carga el override y pasa ese mismo objeto al monitor; `app.state.settings` apunta a esa instancia (`api/main.py:127`). No se modificó `.env`.

## Pruebas

- API y compatibilidad con la ruta 23c: `$env:ASPLE_OFFLINE='1'; .\venv\Scripts\python.exe -m pytest tests/test_app_settings_api.py tests/test_grid_control_api.py tests/test_grid_level_adjust_summary.py -q --basetemp .pytest-fase25-final` → `26 passed, 9 warnings`.
- UI, al final: `$env:ASPLE_OFFLINE='1'; .\venv\Scripts\python.exe -m pytest tests/ui/test_cuenta_settings.py tests/ui/test_grids_browser.py -k "global_settings_section or detail_idle_shrink_button_states" -q --basetemp .pytest-fase25-final-ui` → `2 passed, 46 deselected, 2 warnings`.
- No se ejecutaron las suites completas; la verificación fue dirigida como pide el alcance.

## Mutaciones

| Comportamiento | Resultado | SHA-256 antes → mutado → restaurado |
|---|---|---|
| Omitir confirmación al encender: `test_confirmed_enable_persists_mutates_live_settings_and_allows_grid_toggle` falló porque recibió 200 en vez de 422. | Detectada | `43d2162edaeae2c3bf1c1999250dcebc46b6d212ff95063c1278321d2283d04f` → `9104cf85bfb70b80ae5b56d2432ba58c893075daaad06b2c9242f6daa77ec59e` → `43d2162edaeae2c3bf1c1999250dcebc46b6d212ff95063c1278321d2283d04f` |
| No actualizar el objeto Settings vivo: la misma prueba falló porque el valor siguió en `False`. | Detectada | `43d2162edaeae2c3bf1c1999250dcebc46b6d212ff95063c1278321d2283d04f` → `c0288d4db734c4c13bd1dd1905b8987bced9fad257ad53dacab2e5781fda5890` → `43d2162edaeae2c3bf1c1999250dcebc46b6d212ff95063c1278321d2283d04f` |
| Quitar el rechazo de claves no permitidas: `test_unknown_key_extra_field_and_non_boolean_value_are_rejected` falló al observar 500 en vez de 422. | Detectada | `43d2162edaeae2c3bf1c1999250dcebc46b6d212ff95063c1278321d2283d04f` → `4989aa0207925797fafdf06d367f7daa2320b4aec8813263ba26987815051290` → `43d2162edaeae2c3bf1c1999250dcebc46b6d212ff95063c1278321d2283d04f` |

## Límites y verificación pendiente

El cambio se probó con SQLite temporal y TestClient/Playwright locales. NO VERIFICADO: efecto sobre un monitor que ya está corriendo en el servidor real, ni ejecución con Testnet; no se arrancó la aplicación productiva. No se leyó `.env`, no se accedió a la BD real, `data/` ni `models/saved/`.

## Archivos para el commit posterior

- `api/main.py`
- `api/routes/app_settings.py`
- `config/settings.py`
- `database/db_manager.py`
- `frontend/cuenta.css`
- `frontend/cuenta.js`
- `frontend/grids.js`
- `frontend/index.html`
- `tests/test_app_settings_api.py`
- `tests/ui/test_cuenta_settings.py`
- `tests/ui/test_grids_browser.py`
- `REPORTE_FASE25.md`

No hice stage, commit ni push.
