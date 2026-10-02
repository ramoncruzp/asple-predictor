# Discrepancias — Fase 17A

## 1. `api/main.py` necesitaba dos registros de router, se resolvió con uno

El plan permitía explícitamente un segundo router en el mismo archivo para
`/api/monitor` ("si hace falta un segundo router en el mismo archivo,
permitido"), pero la regla 1 solo autoriza **una** línea
`app.include_router(...)` en `api/main.py`. En vez de forzar dos líneas, se
definió un único `APIRouter` en `api/routes/grid_status.py` con rutas
**absolutas** (`/api/grids`, `/api/grids/{grid_id}`, ..., `/api/monitor/status`)
en lugar de usar el mecanismo de `prefix` de `include_router`. Así
`api/main.py` solo necesita `app.include_router(grid_status.router,
tags=["grid-status"])` (`api/main.py:135`), cumpliendo el límite de líneas sin
renunciar al prefijo propio de `/api/monitor`. Sin impacto funcional: las
rutas resultantes son idénticas a las que se habrían obtenido con dos routers
prefijados.

## 2. Tipos de evento del plan no coinciden con los que emite el código

El plan pedía incluir en `events_view` los tipos `PAUSE`, `RESUME`, `ADJUST`,
`CLOSE_REPOSITORY`, `invariant_violation` y `TESTNET_RESET_DETECTED`. Se
verificó con `grep` en `grid/engine.py` y `grid/monitor.py` que el código
real emite `GRID_PAUSED` (`grid/engine.py:1643`), `GRID_RESUMED`
(`grid/engine.py:1650`), `GRID_ADJUSTED` (`grid/engine.py:1606`),
`GRID_AUTO_CLOSE` (`grid/monitor.py:510-513`, la señal de política antes de
cerrar a repositorio) y que **no existe** ningún evento `invariant_violation`
ni `TESTNET_RESET_DETECTED` en ningún punto del código actual. `EVENT_LABELS`
(`grid/status_view.py:29-85`) mapea los tipos reales a español y, además,
mantiene entradas para los nombres literales del plan por si se introducen
en una fase posterior — no se inventó ningún evento nuevo, solo se dejó el
mapeo listo. Ver también la tabla de la sección 1 del reporte.

## 3. `dust_qty` por grid: se usó la columna existente, no `grid_dust_ledger`

El plan menciona "`held_qty` y `dust_qty` por grid" como dato nuevo del
resumen. `grids.dust_qty` (columna `String`, default `"0"`,
`database/db_manager.py:83`) ya es el total acumulado de polvo del grid — no
hizo falta sumar `grid_dust_ledger` (que solo guarda el detalle por fuente
para evitar doble conteo en `add_grid_dust_once`). Se usa directamente esa
columna en `grid_summary` (`grid/status_view.py:245`).

## 4. Progreso de meta: estimación de caja alineada con el monitor

La auditoría de 17A-2 corrigió la afirmación anterior: el sistema sí puede reconstruir una aproximación de caja por grid desde sus celdas y `params.dust_cash_proceeds`. `grid/status_view.cash_now_view` calcula `capital_total + sum(level.pnl) - sum(entry_price × held_qty × 1.001) + dust_cash_proceeds`. La base retenida es una aproximación documentada, igual a la que usa el monitor cuando no dispone del costo real de entrada. Para `target_basis=equity`, si existe precio actual, la vista también muestra `cash_now + sum(held_qty × price × 0.999)`. La vista no invoca `evaluate_target`: al decidir el cierre, el monitor también proyecta la venta a mercado de celdas rentables y necesita filtros de exchange.

## 5. Operaciones: no se puede reconstruir el precio exacto ni el bruto/comisión por operación

Ver sección 3 del reporte (puntos 1 y 2) para el detalle completo con
archivo:línea. Resumen: `BUY_FILLED`/`SELL_FILLED` no guardan el precio de la
orden ni la comisión/bruto de la venta por separado — solo `cycle_pnl` neto.
Se usa el `price` de nivel superior del evento (mid del mercado en esa pasada)
como aproximación explícita, y los campos que de verdad no se pueden derivar
quedan `null` con su `unavailable_reason`.

## 6. `contract_version`: no existe tal concepto en el código actual

`GET /api/monitor/status` devuelve `contract_version: null` con
`contract_version_unavailable_reason` en vez de inventar un número de
versión. Se buscó (`grep -rn "invariant\|TESTNET_RESET\|contract_version"`)
en todo `grid/` y `database/db_manager.py` sin encontrar nada parecido.

## 7. No se verificó en un navegador real (sin Playwright)

`playwright` no está instalado en el venv del repo
(`ModuleNotFoundError: No module named 'playwright'`, verificado antes de
escribir el test). `tests/ui/test_grids_ui.py` existe, con
`pytest.importorskip("playwright.sync_api")` y `pytest.importorskip("uvicorn")`
al nivel del módulo, exactamente como pedía el plan para este caso — se
confirmó que se salta limpiamente (`1 skipped`) y no rompe la suite offline
(`597 passed, 1 skipped, 18 deselected`). No se instaló Playwright porque el
plan lo trata como opcional ("opcional con Playwright") y agregar una
dependencia nueva al entorno no estaba autorizado por las reglas de scope
de esta fase (archivos permitidos, no paquetes nuevos). No hay capturas en
`data/cache/ui_shots/` como consecuencia directa de esto.

## 8. Estimación de comisión usa el capital ACTUAL de la celda, no el histórico

`fees_estimated_usdt` (`grid/status_view.py:166-197`) multiplica
`capital × cycles_completed × 2 × fee_pct` usando el valor **actual** de
`grid_levels.capital`. Si una celda fue redimensionada por un `ADJUST` en
algún momento de su historia, los ciclos completados ANTES de ese ajuste se
estiman con el capital nuevo (post-ajuste), no con el que tenía en ese
momento, porque `grid_levels` no versiona el capital histórico por ciclo.
Es una aproximación razonable para la mayoría de los grids (que no pasan por
muchos ADJUST), declarada explícitamente en el reporte y en el campo
`fees_estimated_label` que ve el usuario en la UI.

## 9. `route()` en `app.js` cambió su condición de comparación, no solo agregó una rama

El plan dice "en app.js solo el registro de la ruta". El cambio mínimo posible
para soportar `#grids/{id}` (un sub-path, a diferencia de las demás pestañas
que son de un solo segmento) requería que la función `route()` comparase
contra la base del hash (`name.split('/')[0]`) en vez del hash completo, no
solo agregar `'grids'` a la lista. Se verificó que este cambio es
transparente para las pestañas existentes (`dashboard`, `battle`, `grid`,
`coins` nunca tienen `/` en su hash, así que `base === name` siempre para
ellas) — ver el diff completo en el reporte, sección 6: es una sola línea
modificada, sin tocar nada más del archivo.


## 10. Correcciones de auditoría 17A-2

- `grid/status_view.py` elige la fila resumen de snapshot para el realizado; el fallback suma las filas de celda y expone su fuente. El no realizado siempre suma filas por celda.
- Diario: incluye ciclos, stop-loss y liquidaciones de mercado con desglose y conciliación; el API informa eventos truncados al superar 5000.
- Operaciones: las liquidaciones aparecen como `stop_loss`/`liquidacion`, no como ciclos; precios de orden/celda se usan mientras no haya un ADJUST que los afecte. Si hubo ajuste relevante, conserva el mid del evento y marca el precio aproximado.
- Cotizaciones del listado: caché de 5 s por símbolo sin fusionar grids distintos.
- Verificación final: 47 pruebas enfocadas pasaron; 12/12 mutaciones murieron y se restauraron byte por byte; suite offline **607 passed, 1 skipped, 18 deselected, 7 warnings**. Todos los archivos de alcance están actualmente en LF y UTF-8 sin BOM; no tienen U+FFFD. Git advierte que la configuración local puede convertir LF a CRLF al tocar archivos en futuras operaciones, aunque el conteo byte a byte del worktree dio 0 CR. `node --check` y el render sintético de barras pasaron. Playwright no está instalado, por lo que el test UI opcional quedó en 1 skipped y no se generaron capturas. No hubo llamadas reales.


## Fase 17A-3 — Cierre de conciliación parcial

El endpoint del diario filtra los tres tipos de evento que materializan PnL y solo calcula `unattributed_usdt` cuando el rango empieza antes o en `created_at` y no hay truncamiento. Para una ventana parcial o una consulta truncada informa `null` con la nota de disponibilidad; el PnL del resumen sigue rotulado como total de vida del grid. La caché de precio es por `app.state`, no se comparte entre instancias FastAPI.

Verificación: 50 pruebas enfocadas pasaron; la mutación de quitar la condición de ventana fue detectada; suite offline **610 passed, 1 skipped, 18 deselected, 7 warnings**. Se corrigieron las líneas de codificación listadas en `REPORTE_FASE17A.md`, sección 17A-3. Sin tráfico real, commit ni push.

Nota de verificación de codificación: en GNU grep, `?` en el patrón BRE literal `[A-Za-z]?[a-z]` actúa como cuantificador opcional, así que el comando pedido devuelve 35 y 16 líneas respectivamente; no puede dar cero para texto español normal. El patrón de detección pretendido con el signo literal escapado, `[A-Za-z]\?[a-z]`, devuelve 0 en ambos informes. No quedan sustituciones `?` seguidas de letra minúscula.
