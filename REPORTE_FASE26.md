# Fase 26 — Guarda contra libros de órdenes unilaterales (Testnet)

HEAD base: `0ba6e6f` (árbol limpio al iniciar).

## 0. Preflight

`git status --short` vacío antes de empezar. Suite offline base
(`ASPLE_OFFLINE=1`, `-m "not live"`, `--basetemp` bajo `%TEMP%`), una sola vez:

```
3 failed, 1194 passed, 1 skipped, 18 deselected, 50 warnings in 500.00s
```

Fallos previos diagnosticados (ninguno toca libros de órdenes ni archivos del
alcance de esta fase, así que se continuó según lo acordado con Ramón):

- `tests/test_grid_advisor.py::test_advisor_uses_persisted_factor_and_adaptive_stress_trigger`:
  su `db` falso (`SimpleNamespace`) no implementa `get_readiness()`, que
  `api/routes/grid_advisor.py:233` ya invoca.
- `tests/test_grid_monitor.py::test_app_lifespan_starts_without_monitor_when_testnet_credentials_missing`
  y `::test_coin_onboarding_start_failure_does_not_prevent_lifespan_start`: su
  `DummyDB` no implementa `list_app_flag_overrides()`, que
  `config/settings.py:103` (`apply_app_flag_overrides`, añadido en la Fase 25)
  invoca durante `lifespan()`.

Ambos son *fixtures* de prueba desactualizadas de fases anteriores (25 y la de
"readiness"); ninguno de los dos archivos de test ni `config/settings.py` /
`api/routes/grid_advisor.py` está en el alcance autorizado de esta fase.

## 1. Tabla HECHO / NO HECHO

| Ítem | Estado | Notas |
|---|---|---|
| F1 `grid/market_book.py` | HECHO | `book_mid` / `book_is_two_sided`, puras, sin red |
| F2 Monitor (mids + decisiones + evento) | HECHO | ver tabla de consumidores §2 |
| F3 Rechazo de apertura | HECHO | `engine.py` (autoritativo) + `api/routes/grids.py` (mensaje temprano) |
| F4 Scanner no elegible | HECHO | motivo `libro_unilateral`, mismo formato que los demás |
| F5 Cierre y estado no promedian con lado en cero | HECHO | `control_service.py`, `grid_status.py`, `grid_ctl.py` |
| F6 Pruebas + mutaciones | HECHO | 24 pruebas nuevas (`test_market_book.py` + `test_fase26_book_guard.py`), 12 mutaciones, todas detectadas |
| `grid/policy.py` | NO TOCADO | ninguna prueba demostró que no tolere mid ausente; el monitor ya salta la evaluación de precio cuando `mid is None` |

## 2. Consumidores de `get_book_ticker` / del libro (`archivo:línea` → decisión)

| Archivo:línea (antes de esta fase) | Qué hacía | Qué hace ahora |
|---|---|---|
| `grid/monitor.py:124` (`_market_mids`) | Promediaba bid/ask crudos, sin validar lados | Usa `book_mid`; si `None`, `mids[symbol]=None` y emite `BOOK_ONE_SIDED` (cooldown 60 min) |
| `grid/monitor.py:550` (refetch dentro de evaluación de TARGET) | Promediaba bid/ask crudos para `_event_price`; usaba `bid` crudo sin validar | Si el libro recién leído es unilateral, salta la evaluación de TARGET esta pasada (`target_plan=None`) y notifica `BOOK_ONE_SIDED` con el mismo cooldown |
| `grid/engine.py:328` (`create_grid`, antes `:209/327` en la versión que leyó Codex) | Promediaba bid/ask para `_event_price` sin validar | **Rechaza** con `GridConfigError("libro unilateral...")` antes de crear ninguna fila/orden |
| `grid/engine.py:1775` (`stoploss_cell`) | Promediaba crudo para `_event_price` (solo logging) | `book_mid`; `None` si unilateral (campo honesto, no bloquea el stop-loss) |
| `grid/engine.py:1831` (`_sync_grid`) | Promediaba crudo para `_event_price` (solo logging); ya usaba solo `bid` para decisiones de venta | `book_mid`; `None` si unilateral. Sync y colocación de ventas **no se bloquean** (no dependen de `_event_price`) |
| `grid/engine.py:2497` (`_close_repository`, movimiento a repositorio) | Promediaba crudo para `market_mid` (detalle informativo) | `book_mid`; `None` si unilateral, con warning en el log. El cierre **no se bloquea** |
| `grid/engine.py:1455` (`_adjust_grid_locked`, usado por `adjust_grid`) | Promediaba crudo el libro recién leído y lo pasaba a `plan_adjust`, que decide qué celdas quedan BUY/SELL tras el ajuste | Rechaza con `{"ok": False, "reason": "libro_unilateral"}` **antes** de mover ninguna celda (mismo patrón que `"already_at_target"`/`"grid_not_active"` que ya usaba esta función) |
| `grid/engine.py:1734` (`preview_adjust`) | Mismo promedio crudo, misma decisión de celdas, pero para la vista previa | Mismo rechazo `{"ok": False, "reason": "libro_unilateral"}` |
| `grid/engine.py:991` (`process_grid_loans` → `lend_from_plan`) | Promediaba crudo un `mid` que solo se guarda como detalle de auditoría del préstamo (no decide el monto, que viene de `loan_policy.plan_loan`) | `book_mid`; `None` si unilateral. No se bloquea la creación del préstamo (el monto nunca dependió de este campo) |
| `grid/engine.py:2634` (`close_grid`, modo `profit_repository`) | Usaba `bid` crudo sin validar para planear qué celdas vender a mercado (`plan_profit_close`) | Si `bid<=0`, lanza dentro del `try` existente → mismo manejo que cualquier otro fallo de esa rama (`status=CLOSING`, `fail_reason` con el motivo); el cierre **no se bloquea**, solo la sub-rama de venta "profit" falla con un motivo honesto en vez de planear una venta a precio cero |
| `api/routes/grids.py:168` (`_preview_testnet_price`, vista previa) | Rechazaba bid≤0/ask≤0 pero NO libro cruzado (ask<bid) | Usa `book_mid`; también rechaza libro cruzado |
| `api/routes/grids.py:468` (apertura confirmada) | Igual: rechazaba bid≤0/ask≤0, no libro cruzado | Usa `book_mid`; también rechaza libro cruzado. `engine.create_grid` es la segunda barrera (autoritativa) |
| `api/routes/grid_status.py:52` (`_price_for_symbol`) | Promediaba crudo | `book_mid`; `None` si unilateral → la UI ya tenía la ruta "precio no disponible" |
| `grid/control_service.py:104` (pause/resume/close/sweep-dust/adjust) | Ya usaba solo `bid` (un lado) para valorar inventario; no promediaba | Añadido: si `bid<=0`, se trata como "precio no disponible" (antes se habría valorado el inventario en cero) |
| `grid/scan_service.py:67-69` (`_market`, libro público) | `raise ValueError` genérico → aparecía como `{"error": "..."}`, no como fila no elegible | Ya no lanza; `scan()` fuerza `eligible=False` con motivo `libro_unilateral` en `hard_filters`/`reasons`, mismo formato que los demás motivos |
| `scripts/grid_ctl.py:219` (`sweep-dust`) | Usaba `bid` crudo sin validar | Rechaza con `ValueError("libro unilateral...")` antes de barrer polvo |
| `scripts/grid_ctl.py:356` (`open`, vista previa CLI) | Alimentaba `plan_cells` con snapshot crudo (podía fallar con mensaje genérico si el mid resultante quedaba fuera de rango) | En dry-run reporta `testnet_book_mid`/`testnet_book_warning` honestos sin invocar `plan_cells` cuando el libro es unilateral; en ejecución real, rechaza antes de invocar `engine.create_grid` (defensa adicional, `engine.create_grid` ya rechaza también) |

`grid/policy.py` no se tocó: en todos los casos la decisión se resolvió
saltando la evaluación de precio en el monitor (`mid is None` ya existía como
ruta válida) o devolviendo `{"ok": False, "reason": "libro_unilateral"}` en
`engine.py` (mismo patrón que ya usaban otras guardas de esa función), nunca
sustituyendo el precio ni cambiando la política.

**Nota de auditoría propia:** las cuatro filas de `_adjust_grid_locked`,
`preview_adjust`, `process_grid_loans`/`lend_from_plan` y el modo
`profit_repository` de `close_grid` se encontraron en una segunda pasada,
después de que Ramón pidiera `git grep -n -E "bid_price|ask_price" -- '*.py'
':(exclude)tests'` sobre el estado "cerrado" de esta fase. El primer barrido
(hecho a partir del grep inicial de la auditoría de Nexo) no cubrió estas
cuatro rutas porque no aparecían en la lista de consumidores conocidos del
prompt original. Se corrigieron con el mismo patrón ya usado y se repitió el
ciclo rojo→mutación→verde→restauración para cada una (ver §4).

## 3. Pruebas (cifras reales)

- `tests/test_market_book.py`: 5 pruebas, todas verdes.
- `tests/test_fase26_book_guard.py`: 16 pruebas, todas verdes (ninguna de las
  21 en total requiere red ni Testnet real — todo contra `FakeExchange`/dobles).
- Suites focalizadas tras cada cambio (sin regresión, cifras literales):
  - `test_grid_monitor.py` + `test_grid_monitor_live.py -m "not live"`: 37 passed, 2 failed (preexistentes, ver §0).
  - `test_grid_engine.py` + `test_grid_profit_close.py` + `test_grid_control_api.py`: 76 passed.
  - `test_grids_api.py`: 42 → 49 passed (+7 con las pruebas nuevas de libro cruzado).
  - `test_grid_scan_service.py` + `test_grid_scanner.py`: 20 → 28 passed.
  - `test_grid_status_api.py` + `test_grid_status_view.py`: 53 → 83 passed.
  - `test_grid_ctl*.py` + `test_grid_calibration.py` + `test_grid_loan_pairs.py` + `test_grid_target.py`: 81 → 94 passed.
- Suite offline completa final (misma invocación que el preflight):
  **ver cierre §6** (en curso al escribir esta sección; cifras pegadas abajo).

## 4. Mutaciones (15/15 detectadas, SHA antes → mutado → restaurado)

| Ítem | Archivo | SHA antes | SHA mutado | SHA restaurado |
|---|---|---|---|---|
| F1 `book_mid` guarda el lado vacío | `grid/market_book.py` | `cbf97a1601a6` | `50abce7419bf` | `cbf97a1601a6` |
| F2 cooldown de 60 min | `grid/monitor.py` | `f0f690c0c1b6` | `6ed08f59275d` | `f0f690c0c1b6` |
| F3 rechazo en `create_grid` | `grid/engine.py` | `d9df92535013` | `b804b7938c69` | `d9df92535013` |
| F3 vista previa usa `book_mid` (detecta libro cruzado) | `api/routes/grids.py` | `f3dba5d467ae` | `0c7e495600d6` | `f3dba5d467ae` |
| F3 apertura confirmada usa `book_mid` (detecta libro cruzado) | `api/routes/grids.py` | `f3dba5d467ae` | `8cbbaa4f02af` | `f3dba5d467ae` |
| F4 motivo `libro_unilateral` explícito | `grid/scan_service.py` | `d68e86e70288` | `77dcdde38570` | `d68e86e70288` |
| F5 `control_service` trata `bid<=0` como no disponible | `grid/control_service.py` | `0622a3c1670d`* | `11476a3bc0f7`* | `7843c2eecea1`† |
| F5 `grid_status._price_for_symbol` usa `book_mid` | `api/routes/grid_status.py` | `326f47bfd65f` | `c4b7b373e5f4` | `326f47bfd65f` |
| F5 `status_view` severidad `BOOK_ONE_SIDED` | `grid/status_view.py` | `888dea5abd75` | `54cda2c2b020` | `888dea5abd75` |
| F5 `grid_ctl` rechaza sweep-dust unilateral | `scripts/grid_ctl.py` | `2dfabe7be309` | `120e43893bee` | `2dfabe7be309` |
| F5 `grid_ctl` reporta `testnet_book_mid` honesto | `scripts/grid_ctl.py` | `2dfabe7be309` | `99846ca71706` | `2dfabe7be309` |
| extra `adjust_grid` rechaza libro unilateral | `grid/engine.py` | `67fba5f52828` | `c4359a3bd80d` | `67fba5f52828` |
| extra `preview_adjust` rechaza libro unilateral | `grid/engine.py` | `67fba5f52828` | `2a28283263b8` | `67fba5f52828` |
| extra `close_grid(profit_repository)` rechaza `bid<=0` | `grid/engine.py` | `67fba5f52828` | `339ec48eb2ed` | `67fba5f52828` |

\* Un script de restauración automática falló su `assert` (el patrón de
reemplazo no coincidía por un detalle de formato) y dejó el archivo en el
estado mutado. Se detectó inmediatamente (el siguiente comando de
verificación mostró la prueba en rojo con el código mutado todavía activo) y
se corrigió a mano con la herramienta de edición.
† SHA final de `control_service.py` tras la corrección manual; `git diff`
contra HEAD confirmó un único cambio limpio (el bloque de guarda, sin
duplicados ni restos de la mutación).

Las 15 mutaciones mataron su prueba correspondiente; las 15 restauraciones se
verificaron byte a byte (hash SHA-256) y con una re-ejecución de la suite
afectada en verde. Las 3 últimas (extra, encontradas tras el `git grep` de
Ramón) se hicieron con `pathlib.write_bytes` en vez de `write_text`, evitando
desde el inicio el problema de CRLF descrito en §5.

## 5. Incidente de codificación encontrado y corregido durante esta fase

Los scripts de mutación/restauración usaron `pathlib.Path.write_text(...)`
desde Python en Windows, que por defecto traduce `\n` → `\r\n` al escribir
(modo texto). Esto introdujo CRLF en los 9 archivos de producción tocados
(`api/routes/grid_status.py`, `api/routes/grids.py`, `grid/control_service.py`,
`grid/engine.py`, `grid/monitor.py`, `grid/scan_service.py`,
`grid/status_view.py`, `scripts/grid_ctl.py`, `grid/market_book.py`) — entre
26 y 2997 bytes `\r` según el archivo. Se detectó con el método confiable
documentado en memoria (`od -An -tx1 archivo | tr ' ' '\n' | grep -c '^0d$'`,
NO `grep -c $'\r'`, que da falsos positivos en este entorno) al hacer la
verificación de cierre, y se corrigió con un script que reemplaza `\r\n` →
`\n` a nivel de bytes en los 9 archivos. Se re-verificó sintaxis (`ast.parse`)
y se re-corrieron las pruebas nuevas (18/18 verdes) después de la corrección.
Los dos archivos de test nuevos nunca se escribieron con Python en este
proceso (solo con las herramientas Write/Edit), así que nunca tuvieron CRLF.

## 6. Verificación de cierre

```
git status --short
```
```
 M api/routes/grid_status.py
 M api/routes/grids.py
 M grid/control_service.py
 M grid/engine.py
 M grid/monitor.py
 M grid/scan_service.py
 M grid/status_view.py
 M scripts/grid_ctl.py
?? grid/market_book.py
?? tests/test_fase26_book_guard.py
?? tests/test_market_book.py
```
Exactamente la lista de archivos autorizada para esta fase; ningún archivo
prohibido (`frontend/*`, `grid/sim/*`, `grid/policy.py`, `grid/scanner.py`,
`config/settings.py`, etc.) fue modificado.

`git diff --check` sobre los 8 archivos modificados y los 3 nuevos: sin
advertencias de espacios en blanco (solo el aviso esperado de que Git
convertiría LF→CRLF en una futura operación de Git, por la configuración
`core.autocrlf` local — no afecta el contenido actual del árbol de trabajo,
que se verificó en 0 `\r` byte a byte).

0 ocurrencias de U+FFFD y 0 coincidencias de `[A-Za-z]\?[a-z]` (patrón BRE con
el `?` escapado) en los 11 archivos tocados.

`git diff --stat` (8 archivos modificados):
```
 api/routes/grid_status.py |   7 ++-
 api/routes/grids.py       |  34 ++++++------
 grid/control_service.py   |   6 +++
 grid/engine.py            |  30 ++++++++---
 grid/monitor.py           | 133 ++++++++++++++++++++++++++++------------------
 grid/scan_service.py      |  12 ++++-
 grid/status_view.py       |   2 +
 scripts/grid_ctl.py       |  18 ++++++-
 8 files changed, 161 insertions(+), 81 deletions(-)
```
Más 3 archivos nuevos: `grid/market_book.py` (26 líneas),
`tests/test_market_book.py` (37 líneas), `tests/test_fase26_book_guard.py`
(16 pruebas).

(`grid/engine.py` pasó de `+16/-X` a `+30/-X` tras añadir las 4 guardas extra
de la nota de auditoría de la sección 2.)

Suite offline completa (misma invocación que el preflight, una sola vez al
final, repetida una segunda vez tras encontrar y corregir las 4 rutas extra de
`grid/engine.py`):

```
Primer cierre (antes del git grep de auditoría): 3 failed, 1212 passed, 1 skipped, 18 deselected, 50 warnings in 222.49s (0:03:42)
Cierre definitivo (tras las correcciones extra): 3 failed, 1215 passed, 1 skipped, 18 deselected, 50 warnings in 225.13s (0:03:45)
```

En ambos casos los 3 fallos son exactamente los mismos 3 preexistentes de §0
(mismos nombres de prueba); ningún fallo nuevo en ninguna de las dos
ejecuciones. 1215 = 1212 + 3 (las 3 pruebas nuevas de `adjust_grid`,
`preview_adjust` y `close_grid(profit_repository)` añadidas en la segunda
pasada).

No verificado: comportamiento contra el Testnet real (todo esto corrió contra
`FakeExchange`/dobles en memoria, sin red); cómo muestra el frontend el
rechazo de apertura o el evento `BOOK_ONE_SIDED` (no se tocó `frontend/*`,
fuera de alcance).

No se hizo `git add`, `git commit` ni `git push` en ningún momento de esta
fase.

## 7. Archivos para un único commit (cuando Ramón lo decida)

```
api/routes/grid_status.py
api/routes/grids.py
grid/control_service.py
grid/engine.py
grid/monitor.py
grid/scan_service.py
grid/status_view.py
scripts/grid_ctl.py
grid/market_book.py
tests/test_fase26_book_guard.py
tests/test_market_book.py
REPORTE_FASE26.md
```
