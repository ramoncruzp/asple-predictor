# Fase 18B - Mejoras de UI

## Resultado

Se implementaron los bloques A-H en la interfaz y sus rutas de soporte. No hice commit ni push, no llame a Binance/Testnet, no lei el `.env` real y no modifique `grid/engine.py` ni `grid/monitor.py` (diff vacio en ambos). El flujo de credenciales requiere reiniciar el servidor; no reconstruye clientes usados por los workers.

La linea base comprobada antes de los cambios fue **733 passed, 18 deselected, 0 skipped**. La verificacion final, despues de restaurar las mutaciones, fue **761 passed, 18 deselected, 9 warnings** con:

```powershell
$env:ASPLE_OFFLINE="1"
.\venv\Scripts\python.exe -m pytest -m "not live" -q --basetemp .pytest-18b-final-verified
```

Los nueve avisos son advertencias de coleccion/depreciacion de dependencias existentes. Node aprobo `node --check frontend/app.js` y `node --check frontend/scanner.js`. La fixture Playwright bloqueo y reporto los hosts CDN configurados, y la suite acabo sin defectos observados en navegador.

## Cambios por bloque

- **A - Base compartida:** formatos locales de fecha y numericos y aviso Testnet reutilizable (`frontend/app.js:40-61`, `frontend/index.html:20`); pruebas de formato y aviso (`tests/ui/test_frontend_format.py:15`, `tests/ui/test_grids_browser.py:301`). Tooltips compartidos admiten teclado, foco y toque (`frontend/app.js:9-11`).
- **B - Dashboard:** lista activa desde `/api/coins`, fallback XRP y reinicio de seleccion si deja de estar activa (`frontend/app.js:74-133`); estado explicito sin modelo por moneda/intervalo (`api/routes/predictions.py:36-45`, `frontend/app.js:140-156`); B/C opcionales en sombra y fuera del consenso (`models/shadow_predictor.py:14-126`, `api/main.py:50-119`, `config/models_config.py:29-45`); detalle de volatilidad con marca temporal/cobertura y rangos (`models/shadow_predictor.py:26-41,81-126`, `database/db_manager.py:615-630`, `frontend/app.js:214-218,249-289`). Pruebas principales: `tests/ui/test_frontend_format.py:69,95,119`, `tests/ui/test_grids_browser.py:316,332,364`.
- **C - Battle:** carga opcional tolerante a falta/error y estados No disponible; nombres/umbrales por modelo y estado de validacion (`models/shadow_predictor.py:14-126`, `api/main.py:50-119`, `config/models_config.py:29-45`, `api/routes/models_status.py:1-60`); rangos y cobertura vinculados al snapshot del pronostico, solo para datos con marca temporal. Tests: `tests/test_shadow_model_loading.py:21,62`, `tests/test_models_status.py:23`, `tests/ui/test_frontend_format.py:119`.
- **D - Monedas:** alta requiere simbolo `TRADING` en el mercado publico y Testnet (`api/routes/coins.py:55-82`); filtro/reactivacion, metadatos de grid/modelo, y volumen rotulado (`frontend/app.js:319-357`, `database/db_manager.py:672`). Tests con fake (`tests/test_coins_registry.py:155,194,277`, `tests/ui/test_grids_browser.py:39`).
- **E - Cuenta:** endpoint de claves Testnet write-only con autorizacion, loopback directo sin forwarded headers, bloqueo por grids/cierres/reposiciones, validacion y reemplazo atomico preservando el resto del archivo temporal (`api/routes/grid_account.py:32-125`); formulario confirma dos veces y limpia los campos tras envio (`frontend/cuenta.js:72-100`). La respuesta pide reiniciar el servidor. Conciliacion prioriza activos asignados y pliega saldos restantes (`api/routes/grid_account.py:250-279`, `frontend/cuenta.js:34-42`). Pruebas cubren credenciales invalidas, bloqueo, loopback, interrupcion de reemplazo y redaccion (`tests/test_grid_account_api.py:103-177`); navegador prueba borrado del secreto (`tests/ui/test_grids_browser.py:236-268`). `.env.bak` est- cubierto por `.gitignore:7` (`.env.*`, confirmado con `git check-ignore -v`).
- **F - Grids:** estado vacio enlaza Scanner y Advisor; aclaracion de controles y valores formateados (`frontend/grids.js:216,322`, `frontend/index.html:25-26`). Prueba: `tests/ui/test_grids_browser.py:58`.
- **G - Grid Advisor:** objetivo neto editable y niveles derivados usando evaluacion compartida (`api/routes/grid_advisor.py:10,47-101`, `grid/structure.py:161`, `api/routes/grid_structure.py:35-154`); riesgos cuantificados, volatilidad realizada para monedas sin modelo, comparacion simple/smart en mismas velas y limites descritos (`api/routes/grid_advisor.py:47-138`); se anadio acceso al flujo Scanner (`frontend/app.js:242-244`). Tests: `tests/test_grid_advisor.py:45,61`, `tests/ui/test_grids_browser.py:69`.
- **H - Scanner y guarda de margen:** -Usar- rellena campos, desplaza y enfoca la vista previa; preview tiene un -nico reintento con espera limitada; ayudas accesibles y cascada por fila, incluidos fee y valores ausentes (`frontend/scanner.js:13-25,33-43,68`). El preview completo obtiene niveles del calculo del backend. El minimo neto configurable default 0,7% se verifica al abrir y en auto-open, fuera del codigo de cierre (`config/settings.py:38`, `api/routes/grids.py:26-35,107-149`, `grid/auto_open.py:74-88`). Tests: `tests/test_frontend_scanner.py:186-193`, `tests/test_grids_api.py:147`, `tests/test_grid_auto_open.py:91`, `tests/ui/test_grids_browser.py:98`.

## Pruebas de mutacion

Se hicieron cambios temporales por bytes y se restauro cada archivo; cada restauracion se comparo byte a byte con la copia previa. **10/10 mutaciones murieron**, dos por pantalla:

| Pantalla | Mutacion | Prueba que fallo |
|---|---|---|
| Dashboard | ocultar texto de ausencia de modelo; dejar tarjetas obsoletas | `test_dashboard_renders_explicit_missing_model_state_without_model_cards` (ambas) |
| Monedas | omitir validacion Testnet; quitar clase de reactivacion | `test_post_rejects_symbol_missing_from_testnet`; `test_coins_screen_shows_grid_metadata_and_reactivation` |
| Cuenta | saltar guarda loopback; devolver clave completa en sufijo | `test_testnet_credential_update_requires_direct_loopback`; `test_testnet_credential_update_is_atomic_preserves_env_and_redacts_secret` |
| Scanner | omitir ayudas/cascada; ocultar margen bruto calculado | `test_scanner_uses_scan_structure_and_exposes_accessible_margin_help`; `test_node_ineligible_scan_row_uses_fee_or_shows_unavailable_and_escapes_html` |
| Advisor | ignorar objetivo en la respuesta; sustituir fuente realizada | `test_recommendation_uses_editable_margin_and_named_risk_limits`; `test_realized_volatility_reports_two_sigma_band_for_any_symbol` |

## No verificado

- No se us- Binance ni Testnet real; las rutas de red se probaron con fakes. No queda probado que las credenciales sean aceptadas por Testnet real ni el margen/filtro minimo para simbolos reales.
- El minimo de 5 USDT se comunica como tipico y **no verificado en Testnet**.
- No medi arranque/carga/memoria con los artefactos reales de B y C. Se probaron escenarios sinteticos de artefacto ausente y fallo de carga.
- Los hosts CDN se bloquean en las pruebas Playwright; el grafico del Dashboard que depende de la libreria remota no quedo verificado visualmente en navegador.
- La duplicacion de resumenes de grids en Cuenta se conserva; no era un cambio trivial (`frontend/cuenta.js`).
- Las cifras y simulaciones de Advisor/Scanner son estimaciones historicas, no resultados futuros.
