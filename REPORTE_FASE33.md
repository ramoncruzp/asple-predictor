# Reporte Fase 33 — defaults de Smart y Advisor

## D1 — Interruptores globales

- `config/settings.py:47-50`: defaults nuevos `stoploss_enabled=False`, `smart_adjust_enabled=False`, `smart_close_wait_enabled=True` y `advisor_vol_spacing_enabled=True`.
- `config/settings.py:122,129,134,139`: descripciones de los cuatro flags terminan con el texto de evidencia fuera de muestra (XRP, 87 ventanas); la espera cierra por pérdida máxima del 7 %.
- `config/settings.py:149-153`: `apply_app_flag_overrides` conserva precedencia de overrides persistidos. No se borran ni migran.
- `tests/test_app_settings_api.py:62-75`: la BD SQLite temporal recién creada no contiene overrides; una preferencia persistida sobrevive al recrear Settings.
- `tests/test_fase32_switches.py:18-24`: defaults de flags y metadatos de aplicación en vivo.

## D2 — Estrategia sugerida por el Advisor

- `api/routes/grid_advisor.py:460-473,532`: añade `suggested_strategy` y `suggested_strategy_reason`. Smart se sugiere solo cuando, en las simulaciones de la misma respuesta, su P&L neto supera Simple y su drawdown máximo es igual o menor; si faltan métricas se sugiere Simple.
- `frontend/app.js:371-380,396-398`: muestra “Sugerida”, el motivo y conserva la elección al abrir el Scanner.
- `frontend/scanner.js:108,242`: Scanner y formulario de apertura preseleccionan Simple; se puede elegir Smart.
- `config/settings.py:73`: `scanner_auto_open_strategy` ya era `simple`; no se cambió.
- `tests/test_grid_advisor.py:323-332` y `tests/ui/test_grids_browser.py:210-252`: ramas de recomendación y marca de UI ligada al `suggested_strategy` interceptado.

## D3 — Interruptores visibles en Grids

- `frontend/grids.js:195-200,273,288-289,374,475`: cada grid ACTIVE o PAUSED muestra los valores globales actuales y explica que aplican desde el siguiente ciclo del monitor. Repositorio no recibe esa línea.
- `tests/ui/test_grids_browser.py:69-83`: verifica valores actuales y cadencia del monitor.
- No se modificó Laboratorio.

## D4 — Parámetros Smart de grids nuevos

- `grid/policy.py:17-23`: `trapped_age_h=0.25`, `trapped_cap_pct=30`, `max_loss_pct=7`; `trapped_exit_factor=0.8` queda igual y el umbral de reanudación es menor de 24 %.
- `config/settings.py:134`: texto de espera explica que solo la pérdida máxima del 7 % cierra.
- `frontend/app.js:374,380`: “Tope de inventario (30 % del capital)” aparece dentro de la tarjeta Smart; se conserva el motivo técnico `trapped_capital_pct`.
- `tests/test_grid_policy.py:129-151`: pausa al 30 %, reanudación bajo 24 %, pérdida del 7 % y espera sin cierre por tiempo.
- `tests/test_grid_engine.py:54-67`: los grids nuevos guardan los defaults y los parámetros explícitos de un grid previo (50 %, 24 h, 10 %) se mantienen.

## Corrección de regresiones de revisión

- `frontend/grids.js:288-289`: grids abiertos reciben `switchLine`; los del repositorio se renderizan sin ella. Corrige el índice `0` pasado accidentalmente por `map(gridRowHtml)`.
- `frontend/grids.js:199`: `pr\u00f3ximo` evita el carácter dañado.
- `frontend/app.js:374,376,380`: el rótulo de inventario queda dentro de la tarjeta Smart, incluso si su simulación no está disponible.
- `tests/test_grid_engine.py:61-67`: el test de parámetros guardados usa una segunda instancia de engine/BD para no crear niveles de venta coincidentes.
- `tests/ui/test_grids_browser.py:69-83,210-252,290-306`: el fixture fija la respuesta de sugerencia, comprueba tarjeta y motivo, y actualiza expectativas del Scanner a Simple.
- Auditoría del diff: `git diff | Select-String '\?'` devolvió usos válidos de optional chaining, nullish coalescing y ternarios; búsqueda específica sin U+FFFD, mojibake añadido ni `pr?ximo`.

## Verificación

Selección enfocada ejecutada con `ASPLE_OFFLINE=1`, `-m "not live"` y `--basetemp` único bajo `%TEMP%`:

```text
tests/test_app_settings_api.py
tests/test_fase32_switches.py
tests/test_grid_policy.py
tests/test_grid_engine.py
tests/test_grid_advisor.py
tests/test_frontend_scanner.py
tests/ui/test_frontend_format.py
tests/ui/test_grids_browser.py
```

- Línea base: 203 passed, 13 warnings.
- Primera verificación tras cambios: 208 passed, 3 failed; corregidos los argumentos de `map`, el aislamiento de BD de la prueba y el fixture/expectativas UI.
- Pasada siguiente: 210 passed, 1 failed; corregida la expectativa heredada de Smart a Simple.
- Verificación final: **211 passed, 14 warnings en 71.98 s**.
- Hosts externos bloqueados por Playwright: ninguno. Tests live no ejecutados.
- Prueba de flags usó SQLite temporal; no se accedió a una BD real. No se usaron red, Testnet ni `.env`.

## Commits

Commits separados por D1–D4, con el reporte en el commit final; sin push. El cambio de texto a pérdida máxima 7 % comparte hunk con el default D1 de espera y va en D1. El rótulo D4 del Advisor comparte la línea HTML extensa de tarjetas D2 y va en D2; ambos límites quedan registrados aquí por no poder separarse limpiamente con `git add -p`.
