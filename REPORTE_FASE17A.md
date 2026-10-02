# Fase 17A — Pantalla «Grids» de solo lectura

API de estado (GET únicamente) + UI nueva. No mueve dinero, no cambia estado,
no predice nada. 17B (pausar/reanudar/cerrar/capital/meta/plazo) y 17C
(scanner/formulario de apertura) quedan fuera de esta entrega.

## 1. Qué datos salen de qué tabla/evento

| Dato en pantalla | Origen | Nota |
|---|---|---|
| id, símbolo, estrategia, estado, capital, rango, n_levels, params | `grids` | `grid/status_view.py:233-262` |
| edad del grid | `grids.created_at` vs. ahora | — |
| modo compuesto | `grids.params.compound_enabled` | `grid/policy.py:30` define el default (False) |
| compra/venta/capital/estado/cycles_completed por celda | `grid_levels` | `grid/status_view.py:273-310` |
| held_qty, dust_qty (por grid) | `grid_levels.held_qty` (suma), `grids.dust_qty` | la columna `dust_qty` de `grids` ya es el total acumulado; no se leyó `grid_dust_ledger` (solo guarda el detalle por fuente, no hacía falta sumarlo aparte) |
| ganancia bruta/neta realizada, comisión real | `grid_levels.pnl`, `grid_levels.fee_paid` | bruta = neta + fee_paid (`grid/status_view.py:171-197`) |
| comisión estimada | calculada (`capital × cycles_completed × 2 × fee_pct`) | ver sección 3 |
| inventario abierto (costo, valor de mercado, no realizado) | `grid_levels.held_qty/entry_price` + precio actual | `grid/status_view.py:146-163` |
| recovery_mode | derivado: ¿precio < sell_price de TODAS las celdas con `held_qty>0`? | `grid/status_view.py:139-148`; `None` si no hay precio |
| progreso de meta (target_pct/target_usdt) | caja estimada: capital + PnL realizado - costo de inventario retenido + polvo vendido | misma aproximación de costo que usa el monitor, ver sección 3 |
| días restantes de `max_days` | `grid.policy.evaluate_max_days` (reutilizada, no copiada) sobre `grids.created_at` y `grids.params.max_days` | exacto, sin aproximación |
| escalera de bots + fila «precio actual» | `grid_levels` ordenadas por precio + precio actual | `grid/status_view.py:264-322` |
| distancia % a próximo fill | derivado de `grid_levels.price`/`sell_price` y precio actual | — |
| últimas operaciones | `grid_events` (`BUY_FILLED` emparejado con `SELL_FILLED` del mismo `level_idx`) | ver limitación en sección 3 |
| ganancia diaria | `SELL_FILLED`, `CELL_STOPLOSS` y `CELL_LIQUIDATED`, desglosados por fuente y conciliados contra PnL neto | corte local `America/Santo_Domingo`; eventos fuera de ventana quedan no atribuidos |
| curva de equity con inventario | PnL realizado de la fila resumen por `run_id` + no realizado de filas por celda; fallback a celdas si falta resumen | una fila por `run_id`, sin duplicar el realizado |
| eventos legibles | `grid_events.event_type` → texto en español | tipos reales confirmados en el código, ver sección 3 |
| precio actual | `testnet_client.get_book_ticker(symbol)` (misma fuente que usa `grid/monitor.py:78-87`) | `price_as_of` siempre acompaña al precio |
| USDT libre de la cuenta | `testnet_client.get_balance("USDT")` | solo a nivel de cuenta, nunca por grid (regla 5 del plan) |
| último latido del monitor | `monitor_runs` vía `db.get_last_monitor_run()` | — |

## 2. Endpoints (todos GET, prefijo `/api/grids` salvo el de monitor)

`GET /api/gridsístatus=`, `GET /api/grids/{grid_id}`,
`GET /api/grids/{grid_id}/operations|events|daily|equity`, `GET /api/monitor/status`.
Seguridad: `_authorize` reutilizada de `api/routes/grids.py` (import, no copia) —
`X-API-Token` si `GRID_API_TOKEN` está configurado; si no, solo loopback.

**Colisión de rutas**: un solo `APIRouter` con rutas absolutas
(`/api/grids`, `/api/grids/{grid_id}`, ...) en vez de dos routers con
prefijos distintos, para que `api/main.py` necesite una sola línea
`app.include_router(grid_status.router, tags=["grid-status"])`
(`api/main.py:135`). `GET /api/grids/scan` y `GET /api/grids/open` devuelven
**422** (FastAPI no puede convertir `"scan"`/`"open"` al `int` de `grid_id`),
nunca un grid — verificado en
`tests/test_grid_status_api.py::test_get_scan_and_open_never_return_a_grid`.
`POST /api/grids/scan` y `POST /api/grids/open` siguen respondiendo igual
(`test_existing_scan_and_open_routes_unaffected_by_new_router`).

## 3. Qué NO se pudo derivar tal cual, y qué se aproximó

1. **Precio exacto de compra/venta por operación histórica.** `BUY_FILLED` y
   `SELL_FILLED` (`grid/engine.py:967-973,1153-1159`) guardan `executed_qty`,
   `fee_usdt`/`cycle_pnl`, pero no el precio de la orden. `operations_view`
   usa el `price` de nivel superior del evento (`self._event_price`, el mid
   del mercado en esa pasada del monitor, no necesariamente el precio exacto
   del fill) como `buy_price_approx`/`sell_price_approx`, etiquetado como
   aproximado. El emparejamiento compra-venta es por `(grid_id, level_idx)` y
   orden cronológico (el BUY_FILLED más reciente antes de cada SELL_FILLED);
   si ese BUY no está dentro de la ventana de eventos consultada, los campos
   de compra quedan `null` con `unavailable_reason` explícito.
2. **Bruto y comisión de venta por operación individual.** `SELL_FILLED` solo
   guarda `cycle_pnl` (neto). No hay forma de separar bruto/comisión de esa
   venta específica sin inventar un número; `gross_pnl_usdt` y
   `sell_fee_usdt` quedan `null` con `gross_fee_unavailable_reason`
   (`grid/status_view.py:343-348`). A nivel de GRID completo sí se puede (ver
   punto 3), porque ahí se usa `fee_paid` acumulado y no por operación.
3. **Comisión estimada.** Testnet cobra comisión real 0, así que mostrar solo
   `fee_paid` infla la ganancia neta. `fees_estimated_usdt` aproxima lo que
   cobraría un exchange real: `capital_de_la_celda × cycles_completed × 2
   (compra+venta) × fee_pct`. Usa el `capital` ACTUAL de cada celda como
   proxy del capital histórico de cada ciclo — si una celda fue redimensionada
   por `ADJUST`, los ciclos anteriores a ese ajuste se estiman con el capital
   nuevo, no el viejo (dato no versionado en `grid_levels`).
4. **Progreso hacia `target_pct`/`target_usdt`.** El monitor calcula `cash_now` con capital total + PnL de las celdas - base de costo de posiciones retenidas + `params.dust_cash_proceeds` (`grid/monitor.py:398-399`). `cash_now_view` replica esa fórmula y estima la base retenida como `entry_price × held_qty × 1.001`. La tarjeta muestra caja, beneficio de caja y avance sobre la meta; en base equity también muestra `cash_now + held_qty × precio × 0.999` cuando hay precio. Es una estimación de vista: el monitor, al evaluar la meta, además proyecta el producto neto de vender celdas rentables. No llama a `evaluate_target`, que requiere filtros de exchange.
5. **`contract_version` en `GET /api/monitor/status`.** No existe ningún
   concepto de "versión de contrato" en este código (verificado, nada en
   `grid/monitor.py` ni en `database/db_manager.py`). Se devuelve `null` con
   `contract_version_unavailable_reason` en vez de inventar un número.
6. **Tipos de evento del plan vs. tipos reales emitidos.** El plan listaba
   `PAUSE/RESUME/ADJUST/CLOSE_REPOSITORY/invariant_violation/
   TESTNET_RESET_DETECTED`. El código real emite `GRID_PAUSED`,
   `GRID_RESUMED`, `GRID_ADJUSTED` (`grid/engine.py:1643,1650,1606`),
   `GRID_AUTO_CLOSE` (`grid/monitor.py:510-513`) y no emite
   `invariant_violation` ni `TESTNET_RESET_DETECTED` en ningún punto del
   código actual. `EVENT_LABELS` en `grid/status_view.py:29-85` mapea los
   tipos reales a español Y además incluye entradas para los nombres
   literales del plan (por si se introducen más adelante), dejando constancia
   de la discrepancia aquí en vez de en silencio.
7. **UI con navegador real.** No se instaló Playwright en este entorno;
   `tests/ui/test_grids_ui.py` existe y usa `pytest.importorskip`, por lo que
   se salta limpiamente (`1 skipped`) sin romper la suite. No hay capturas en
   `data/cache/ui_shots/` porque el test nunca llegó a correr contra un
   navegador real.

## 4. Suite de tests

- `tests/test_grid_status_view.py`: **24/24** — cifras conocidas (bruta,
  fees, neta, no realizado, total con inventario, % desplegado), liquidación
  no cuenta como ciclo, precio ausente → `null` en todo lo que dependa de él,
  no realizado siempre acompaña a la ganancia, escalera ordenada + fila
  «precio actual» en las tres posiciones exigidas (igual a un nivel, sobre el
  techo, bajo el piso), distancia % correcta, estados pausado/pausado con
  posición, `recovery_mode` en sus tres casos (true/false/`None`), progreso de
  meta en base cash, días de plazo, eventos desconocidos visibles, comisión
  estimada verificada a mano.
- `tests/test_grid_status_api.py`: **13/13** — listado y detalle con dos
  grids del mismo símbolo sin mezclar datos, 404 de grid inexistente, 405 en
  POST/PUT/DELETE sobre rutas de lectura, convivencia con `POST /scan` y
  `POST /open`, `GET /scan`/`GET /open` nunca devuelven un grid, token
  requerido cuando está configurado, loopback permitido sin token, ninguna
  llamada de lectura escribe en la base (conteo de filas antes/después en
  `grids`/`grid_levels`/`grid_events`), `price_as_of` presente/ausente según
  haya cliente Testnet, los cuatro sub-endpoints responden 200, `GET
  /api/monitor/status` responde con `contract_version=null` explícito, y la
  app de producción registra todas las rutas nuevas y las viejas.
- `tests/ui/test_grids_ui.py`: **1 skipped** (sin Playwright instalado).
- Suite offline completa, con el venv del repo:
  `ASPLE_OFFLINE=1 .\venv\Scripts\python.exe -m pytest tests -p no:cacheprovider -q -m "not live"`
  → **597 passed, 1 skipped, 18 deselected, 7 warnings en 60.2 s**
  (línea base antes de esta fase: 560 passed, 18 deselected; 597+1=598,
  exactamente 560+37 tests nuevos+1 skip).

## 5. Mutaciones (8/8 exigidas por el plan, todas murieron)

Cada una se probó rompiendo el código, corriendo el test relevante (falló
como se esperaba) y revirtiendo con el mismo editor antes de seguir — no
quedó ningún cambio de estas pruebas en los archivos finales.

| # | Mutación | Test que la mata |
|---|---|---|
| 1 | Sumar `TARGET_REACHED` como ciclo en `operations_view` | `test_operations_view_liquidation_does_not_count_as_cycle` |
| 2 | `total_with_inventory_usdt = net_realized` (ocultar no realizado) | `test_grid_summary_known_figures_bruto_fees_neto_no_realizado_total` |
| 3 | Ordenar la escalera de menor a mayor | `test_cells_view_is_sorted_highest_price_first` |
| 4 | Insertar siempre la fila de precio en el índice 0 | `test_cells_view_marker_inserted_at_exact_position` (2/3 casos fallan) |
| 5 | Permitir POST en `GET /api/grids` (`api_route` con ambos métodos) | `test_only_get_allowed_on_read_routes` |
| 6 | `_price_for_symbol` devuelve precio sin `price_as_of` | `test_price_as_of_present_when_testnet_client_available` |
| 7 | Indexar el listado por símbolo (deduplicar) en vez de por `grid_id` | `test_list_and_detail_return_distinct_data_for_two_grids_same_symbol` |
| 8 | Quitar la llamada a `_authorize` en `list_grids` | `test_security_token_required_when_configured` |

## 6. `git diff --stat` por archivo y LF

```
api/main.py         | 3 ++-   (2 insertions, 1 deletion)
frontend/app.js     | 2 +-    (1 insertion, 1 deletion — solo route())
frontend/index.html | 6 ++--  (CSS link, tab, sección, script tag)
```

Archivos nuevos (no tienen diff de git por ser `??`, pero se muestran sus
tamaños): `grid/status_view.py` (458 líneas), `api/routes/grid_status.py`
(166 líneas), `frontend/grids.js` (271 líneas), `frontend/grids.css`
(81 líneas), `tests/test_grid_status_view.py` (246 líneas),
`tests/test_grid_status_api.py` (234 líneas), `tests/ui/test_grids_ui.py`
(91 líneas).

**0 bytes `\r` (CR)** confirmado con `od -An -tx1 <archivo> | tr ' ' '\n' |
grep -c '^0d$'` en los 10 archivos nuevos/editados (todos dieron 0). Nota:
`grep -c $'\r'` dentro de un bucle `for` de este shell dio falsos positivos
(contó todas las líneas); el conteo verificado con `od` byte a byte es el
correcto y es 0 en todos los casos.

## 7. Límites declarados

- Solo lectura: no hay botones funcionales de pausa/cierre (deshabilitados
  con tooltip «disponible en 17B»), ni se guarda ni pide token en esta fase.
- La UI usa SVG propio para la curva de equity (sin CDN nuevo); no se tocó
  `lightweight-charts` (ya cargado para el dashboard existente).
- `fees_estimated_usdt` es una aproximación declarada (capital actual de la
  celda × ciclos × 2 × fee_pct), no un valor exacto histórico.
- El progreso hacia `target_pct`/`target_usdt` estima caja con capital + realizado - costo de inventario retenido + polvo vendido; la fórmula de la vista replica la aproximación del monitor para base retenida. Al evaluar el objetivo, el monitor además suma la venta proyectada de celdas rentables.
- `operations_view` no reconstruye bruto/comisión de venta por operación
  individual ni el precio exacto de fill histórico — ver sección 3,
  puntos 1 y 2.
- No se verificó visualmente en un navegador real (sin Playwright instalado).


## Fase 17A-2 — Correcciones tras auditoría

- `grid/status_view.py`: la curva usa el PnL de la fila resumen por `run_id` y suma el no realizado solo desde filas de celda; si falta resumen usa la suma por celda y expone `source`. No combina ambas fuentes.
- La serie diaria agrega `SELL_FILLED.cycle_pnl`, `CELL_STOPLOSS.realized_pnl` y `CELL_LIQUIDATED.cycle_pnl` (con fallback a `realized_pnl`/`pnl_realized`). Cada día devuelve `cycles`, `stoploss`, `liquidations` y `net_pnl_usdt`. El API concilia el período contra `sum(grid_levels.pnl)` como `unattributed_usdt`; lo explica y marca truncamiento al superar 5000 eventos.
- El progreso de objetivo ya no usa PnL realizado como caja. `cash_now_view` usa capital + realizado - `entry_price × held_qty × 1.001` + polvo vendido. La base equity añade `held_qty × price × 0.999` solo si existe precio. `profit_per_cycle_pct` se conserva con etiqueta explícita: promedio sobre ciclos completados, con neto que incluye ventas a mercado.
- Operaciones distingue `ciclo`, `stop_loss` y `liquidacion`. Los precios de celda/orden se usan cuando no hay ADJUST que afecte la celda; después de ajuste se muestra el mid del evento con marca de aproximación. La vista no cuenta liquidaciones como ciclos.
- `GET /api/grids` conserva los grids distintos aunque compartan símbolo y reutiliza cotización por símbolo durante 5 s. La UI pinta pérdidas por debajo de la línea cero y muestra el PnL no atribuido/truncamiento del diario.
- Referencias del cambio: equity `grid/status_view.py:467-511`; caja/meta `grid/status_view.py:184-232`; operaciones `grid/status_view.py:349-424`; diario `grid/status_view.py:426-464`; `api/routes/grid_status.py:28-55,95-110,129-156`; UI `frontend/grids.js:122-151,206-211`.


### Verificación final de 17A-2

- Pruebas enfocadas: `tests/test_grid_status_view.py tests/test_grid_status_api.py` → **47 passed**.
- Mutaciones restauradas byte por byte: **12/12 muertas** (las ocho regresiones previas y cuatro nuevas: doble suma de equity, exclusión del stop-loss diario, progreso basado solo en realizado y cotizaciones sin deduplicación por símbolo).
- Suite offline completa contra la línea base indicada: `ASPLE_OFFLINE=1 .\venv\Scripts\python.exe -m pytest tests -p no:cacheprovider -q -m "not live" --basetemp data/cache/pytest_17a2_final2` → **607 passed, 1 skipped, 18 deselected, 7 warnings, 44.48 s**.
- Warnings: 2 de colección de `TestnetOrderError`, 2 avisos deprecados de XGBoost y 3 de dropout GRU.
- `node --check frontend/grids.js` pasó. Render de UI con Node confirmó barra negativa debajo de la línea cero y barra positiva encima. Playwright no está instalado en el venv; `tests/ui/test_grids_ui.py` se omitió (1 skipped), sin capturas de navegador.
- Archivos tocados conservan LF (0 bytes CR), UTF-8 sin BOM; ambos reportes no tienen caracteres U+FFFD. No hubo llamadas reales ni edición fuera de los siete archivos autorizados.


### Estadística por archivo

Los siete archivos de 17A-2 aparecen como no rastreados (`??`) en este checkout, por lo que `git diff --stat` normal no los contabiliza. Comparación `git diff --no-index --stat -- NUL <archivo>` (contenido completo frente a vacío):

| Archivo | Líneas añadidas |
|---|---:|
| `grid/status_view.py` | 535 |
| `api/routes/grid_status.py` | 188 |
| `frontend/grids.js` | 281 |
| `tests/test_grid_status_view.py` | 357 |
| `tests/test_grid_status_api.py` | 275 |
| `REPORTE_FASE17A.md` | 212 |
| `DISCREPANCIAS_FASE17A.md` | 107 |

`git diff --check` no reportó errores de whitespace. Se conservaron los cambios ajenos ya presentes en el worktree; no se hizo stage ni commit.


## Fase 17A-3 — Conciliación por ventana y aislamiento de caché

- `api/routes/grid_status.py`: el diario solicita por separado `SELL_FILLED`, `CELL_STOPLOSS` y `CELL_LIQUIDATED`, combina los resultados y aplica el límite de 5000 eventos. La conciliación solo se publica cuando `cutoff <= grids.created_at` y la consulta completa no está truncada. El `net_realized_usdt` mostrado conserva la etiqueta `total de vida del grid`; una ventana parcial o truncada devuelve `unattributed_usdt: null` y la nota solicitada.
- La caché de precios vive ahora en `request.app.state.grid_status_price_cache` (diccionario y lock por aplicación); dos apps no comparten cotizaciones.
- Pruebas: rango de 7 días sobre grid de 10 días sin conciliación; rango de 30 días concilia con delta -3 USDT; truncamiento deshabilita conciliación; dos apps con mismo símbolo y precios distintos quedan aisladas.
- Verificación 17A-3: **50 pruebas enfocadas pasaron**; la mutación que quitaba la guarda de ventana murió. Suite offline: **610 passed, 1 skipped, 18 deselected, 7 warnings, 47.01 s**, comando `ASPLE_OFFLINE=1 .\venv\Scripts\python.exe -m pytest tests -p no:cacheprovider -q -m "not live" --basetemp data/cache/pytest_17a3_final`.
- Inventario de correcciones por línea (antes → después; `[?]` representa el carácter ASCII reemplazado): REPORTE L214 `17A-3 [?] Conciliaci[?]n ... cach[?]` → `17A-3 — Conciliación ... caché`; L216 `l[?]mite`, `conciliaci[?]n`, `est[?] truncada` → `límite`, `conciliación`, `está truncada`; L217 `cach[?]`, `aplicaci[?]n` → `caché`, `aplicación`; L218 `7 d[?]as`, `conciliaci[?]n`, `s[?]mbolo` → `7 días`, `conciliación`, `símbolo`; L219 `Verificaci[?]n`, `muri[?]`, carácter de control en `.<VT>env` → `Verificación`, `murió`, `./venv`; L221-225 inventario anterior con tildes/signos dañados y separador de línea partido → sección 17A-3 legible, íntegra y con el comando correcto. DISCREPANCIAS L44 `s[?]` → `sí`; L110 `17A-3 [?] Cierre de conciliaci[?]n parcial` → `17A-3 — Cierre de conciliación parcial`; L112 `cach[?]` → `caché`; L114 `Verificaci[?]n`, `mutaci[?]n`, `condici[?]n`, `codificaci[?]n`, `tr[?]fico` → `Verificación`, `mutación`, `condición`, `codificación`, `tráfico`.
- Se conservaron signos de interrogación funcionales como `?status=`, el marcador Git `??` y la pregunta textual de `recovery_mode`.
- Verificación del formato: UTF-8 sin BOM, LF, 0 CR y 0 U+FFFD. Sin llamadas reales ni commit/push.
