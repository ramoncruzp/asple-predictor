# Fase 10 — Alertas por Telegram (operativas, compras/ventas, resumen diario)

HEAD base: `eae5b81` (Fase 26 ya commiteada; árbol limpio al iniciar, confirmado
con `git status --short` vacío y `git log --oneline -5`).

## 0. Primer paso obligatorio — event_type que el código emite

`grep -rhoE 'event_type"?[:=] ?"[A-Z_]+"' --include=*.py .` (excluyendo
`venv/` y `tests/`) no captura los que usan f-strings (`f"{side}_PLACED"`)
ni los que pasan el tipo como primer argumento positional a `_emit`/
`_policy_event`/`_close_event`, así que se complementó con una segunda
pasada dirigida a esos helpers. Tabla completa, con el primer sitio real de
emisión:

| event_type | archivo:línea | event_type | archivo:línea |
|---|---|---|---|
| ADJUST_BLOCKED | grid/monitor.py:730 | LOAN_CANCELLED | grid/engine.py:809 |
| APP_SETTING_CHANGED | database/db_manager.py:1494 | LOAN_CREATED | grid/engine.py:778 |
| AUTO_OPEN | grid/auto_open.py:104 | LOAN_DEFERRED_FUNDS | grid/engine.py:597 |
| AUTO_OPEN_STARTED | grid/auto_open.py:98 | LOAN_REPAID | grid/engine.py:893 |
| BOOK_ONE_SIDED | grid/monitor.py:154 (Fase 26) | MAX_DAYS_REACHED | grid/monitor.py:444 |
| BUY_DEFERRED_FUNDS | grid/engine.py:159 | PAIR_ORPHAN | api/routes/grids.py:598 |
| BUY_FILLED | grid/engine.py:1046 | POLICY_ACTION_FAILED | grid/monitor.py (12 sitios) |
| BUY_PARTIAL_SETTLED | grid/engine.py:1117 | PROFIT_CLOSE | grid/engine.py:2662 |
| BUY_PLACED | grid/engine.py:540 (`f"{side}_PLACED"`) | PROFIT_CLOSE_MARKET_SELL_FAILED | grid/engine.py:2744 |
| CELL_DUST | grid/engine.py:2361, 2374 | REPOSITORY_CREATED | grid/engine.py:2560 |
| CELL_LIQUIDATED | grid/engine.py:2427 | RESERVE_RETURNED | grid/engine.py:2454 |
| CELL_MOVED_TO_REPOSITORY | grid/engine.py:2590 | RUN_GAP | grid/monitor.py:389 |
| CELL_STOPLOSS | grid/engine.py:1820 | SELL_FILLED | grid/engine.py:1270 |
| CLOSE_PENDING | grid/monitor.py:350 | SELL_PLACED | grid/engine.py:540 (`f"{side}_PLACED"`) |
| COMPOUND_APPLIED | grid/engine.py:1246 | SNAPSHOT_FAILED | grid/monitor.py:377, 864 |
| COMPOUND_SKIPPED | grid/engine.py:1136 | SYNC_FAILED | grid/monitor.py:869 |
| DUST_RECONCILIATION | grid/monitor.py:196 | TARGET_REACHED | grid/engine.py:2779 (`close_event_type`) |
| GRID_ACTION_API | api/routes/grid_control.py:147, grid/control_service.py:267 | TARGET_SKIPPED | grid/monitor.py:569 |
| GRID_ACTION_REJECTED | api/routes/grid_control.py:28, grid/control_service.py:84, 277 | TESTNET_RESET_DETECTED | grid/monitor.py:261 |
| GRID_ADJUSTED | grid/engine.py:1723 | TESTNET_RESET_RECOVERED | grid/monitor.py:207 |
| GRID_AUTO_CLOSE | grid/monitor.py:798 | GRID_PAUSED | grid/engine.py:1765 |
| GRID_CLOSE_STARTED | grid/engine.py:2629 | GRID_RESUMED | grid/engine.py:1772 |
| GRID_OPENED | scripts/grid_ctl.py:402 | | |
| GRID_OPEN_API | api/routes/grids.py:510 | | |
| GRID_OPEN_REJECTED | api/routes/grids.py:277 | | |
| IDLE_SHRINK_EVAL | grid/monitor.py:664 | | |

`SOME_FUTURE_EVENT` que aparecía en un primer barrido mal filtrado resultó
ser solo un valor de prueba en `tests/test_grid_status_view.py:338` — no es
un evento real, se descartó.

Todos los eventos (`_emit`, `_policy_event`, `_close_event` cuando
`event_sink` está activo) terminan persistidos en `grid_events` por
`grid/monitor.py:72` (`self.db.add_grid_event(**event_values)`) durante una
pasada del monitor; cuando `event_sink` es `None` (código ejecutado fuera
del monitor), `_policy_event`/`_close_event` persisten directo con
`self.db.add_grid_event(...)` (`grid/engine.py:1425-1426`, `:2270-2275`),
pero el `_emit` simple (usado por `BUY_PLACED`/`SELL_PLACED`/`BUY_FILLED`/
`SELL_FILLED`/`COMPOUND_APPLIED`) **no persiste nada si `event_sink` es
`None`** (`grid/engine.py:177-178`). Esto importa para la sección 4
("Discrepancia: BUY_PLACED de apertura no persistido").

## 1. Tabla HECHO / NO HECHO

| Ítem | Estado | Notas |
|---|---|---|
| A. `alerts/telegram_sender.py` | HECHO | token redactado en todo error; `trust_env=False`; 1 reintento a 5s |
| Variables de entorno nuevas | HECHO | 14 campos en `config/settings.py`, mismo estilo `validation_alias`/`ge`/`gt` |
| Tablas `alert_log`/`alert_state` | HECHO | `database/db_manager.py`; marca de agua inicial = `MAX(id)` |
| D1 Operativas | HECHO | 13 event_type (ver §3); cooldown + tope diario + `who:api` filtrado |
| D2 Compras/ventas | PARCIAL | ver discrepancia de BUY_PLACED de apertura (§4); resto completo |
| D3 Salud (monitor/Binance/bucles/arranque) | HECHO | probe propio **a través** del túnel (corregido en 10b — iba al revés); cooldown de arranque 10 min por proceso (corregido en 10b) |
| D4 Resumen diario | HECHO | una vez por día hora local America/Santo_Domingo |
| Cooldown + tope diario | HECHO | por `alert_key`; tope separado para operativas y para fills |
| `GET /api/alerts/status` / `POST /api/alerts/test` | HECHO | mismo `_authorize`; token nunca devuelto |
| Pre-aviso "cerca de salir de rango" | NO HECHO | fuera de alcance explícito (propuesta descartada) |
| Monitor de remanente calibrado (mejora 13) | NO HECHO | fuera de alcance explícito |
| Alertas de señal del ensemble | NO HECHO | fuera de alcance explícito (Fase 10 original, no esta) |
| UI de historial de alertas | NO HECHO | fuera de alcance explícito |
| Vigilante externo (proceso/máquina caídos) | NO HECHO | fuera de alcance explícito; el resumen diario cubre esto parcialmente como latido |
| Aviso casi inmediato de cada fill | NO HECHO | exigiría que el motor emita en el momento del fill; fuera de alcance (no se tocó engine.py/monitor.py) |
| "Inventario atrapado" en HOLDING | NO HECHO (parcial) | solo existe `CLOSE_PENDING` para grids en CLOSING >30 min; no hay evento equivalente para HOLDING, así que ese caso no se alerta |

## 2. Preflight y cierre

```
git status --short   # vacío
git log --oneline -5 # eae5b81 Fase 26 ... (commiteada)
```

Ningún archivo prohibido (`grid/engine.py`, `grid/monitor.py`, `grid/policy.py`,
`grid/sim/*`, `frontend/*`) fue tocado. Lista final de archivos modificados/nuevos:

```
 M api/main.py
 M config/settings.py
 M database/db_manager.py
?? alerts/__init__.py
?? alerts/telegram_sender.py
?? alerts/formatters.py
?? alerts/dispatcher.py
?? api/routes/alerts.py
?? tests/test_telegram_sender.py
?? tests/test_alert_formatters.py
?? tests/test_alert_dispatcher.py
?? tests/test_alerts_api.py
?? REPORTE_FASE10.md
```

`git diff --stat` de los 3 archivos modificados:
```
 api/main.py            |  15 ++++++-
 config/settings.py     |  13 ++++++
 database/db_manager.py | 113 +++++++++++++++++++++++++++++++++++++++++++++++++
 3 files changed, 140 insertions(+), 1 deletion(-)
```

`api/main.py`: solo 2 imports nuevos (`AlertDispatcher`, el módulo `alerts`
en la lista de routers), la construcción de `AlertDispatcher(db, Settings)`,
el arranque condicional (`ALERTS_ENABLED` + token + chat id válidos, si no
un solo `logging.warning` y no arranca el job), el `alert_dispatcher.stop()`
en el cierre, y `app.include_router(alerts.router, ...)`.

`git diff --check`: sin advertencias reales (solo el aviso de
`core.autocrlf` ya documentado en la Fase 26). 0 ocurrencias de U+FFFD en
los 12 archivos tocados/nuevos.

`git grep` del token/chat-id literal en el código fuente: ninguna
coincidencia fuera de los nombres de campo (`TELEGRAM_BOT_TOKEN`,
`TELEGRAM_CHAT_ID` como alias de configuración, nunca un valor).

Suite offline completa (`ASPLE_OFFLINE=1`, `-m "not live"`, una sola vez al
final):
```
3 failed, 1254 passed, 1 skipped, 18 deselected, 50 warnings in 215.96s (0:03:35)
```
Los 3 fallos son los mismos preexistentes de la Fase 26 (fixtures
desactualizadas de la Fase 25 / "readiness" en `test_grid_advisor.py` y
`test_grid_monitor.py`, ninguno de los dos archivos ni `config/settings.py`'s
rama de `apply_app_flag_overrides`/`api/routes/grid_advisor.py` está en el
alcance de esta fase). 1254 = 1215 (cierre de Fase 26) + 39 pruebas nuevas
(4 + 12 + 17 + 6, repartidas en los 4 archivos de test nuevos). Ningún
fallo nuevo.

**No commit, no push.**

## 3. D1 — eventos operativos elegidos y por qué

`alerts/dispatcher.py:OPERATIONAL_EVENT_TYPES`:

| event_type | Por qué se incluyó |
|---|---|
| RUN_GAP | pedido explícito |
| SYNC_FAILED | pedido explícito |
| GRID_AUTO_CLOSE | pedido explícito, con `reason`, símbolo y `grid_id` |
| TESTNET_RESET_DETECTED | pedido explícito |
| MAX_DAYS_REACHED | pedido explícito |
| BOOK_ONE_SIDED | pedido explícito (lo introduce la Fase 26) |
| CLOSE_PENDING | de la lista "entre los que existan"; cubre el caso CLOSING de "inventario atrapado" |
| POLICY_ACTION_FAILED | de la lista; una acción de política falló |
| GRID_PAUSED / GRID_RESUMED | "pausa/reanudación por volatilidad"; también se disparan manualmente vía API, filtrados por `who:api`/`source:API` |
| TARGET_REACHED | "meta de ganancia alcanzada" |
| CELL_STOPLOSS | "stop-loss/pérdida máxima" |
| DUST_RECONCILIATION | solo si `reason` no es `baseline` ni `no_material_drift` (`grid/monitor.py:196` confirma esos dos valores exactos) |

**No existen** eventos dedicados para: inventario atrapado específicamente
en HOLDING (solo CLOSING vía `CLOSE_PENDING`); ningún "pre-aviso" de rango
(fuera de alcance de todos modos).

Filtro `who: api` (`_is_manual_api`, `alerts/dispatcher.py:39-41`): cubre
las dos convenciones distintas que usa el código — `details.who == "api"`
(`api/routes/grids.py:269,511,598`, `grid/control_service.py:85,268,278`,
`api/routes/grid_control.py:29,148`) y `details.source == "API"`
(`grid/control_service.py:245-246`, usado al llamar `engine.pause_grid`/
`resume_grid` manualmente). Sin este segundo chequeo, una pausa manual vía
`POST /api/grids/{id}/pause` habría generado una alerta falsa de "pausado
por volatilidad".

## 4. D2 — compras y ventas

**Dónde está cada dato** (`grid/engine.py`):
- Precio de venta objetivo de una compra: evento `SELL_PLACED`
  (`grid/engine.py:540-543`, `details={"qty":..., "price":...}`), emitido
  inmediatamente después de `BUY_FILLED` dentro de `_handle_buy_fill` —
  confirmado leyendo el código, tal como pedía el prompt.
- Ganancia neta por ciclo: `cycle_pnl` en `SELL_FILLED.details`
  (`grid/engine.py:1155-1159`, exacta: `sell.cummulative_quote_qty -
  buy.cummulative_quote_qty - buy_fee - sell_fee`; fallback estimado en
  `:1163-1166` si no se pudieron leer los trades reales). **El evento no
  guarda qué rama se usó** (ni un flag `pnl_estimated`), así que la alerta
  no puede marcar "(estimada)" sin inventar ese dato — se muestra el número
  tal cual está guardado en ambos casos, documentado aquí como limitación.
- Comisión por venta individual: `sell_fee` se calcula en
  `grid/engine.py:1154` pero **no se persiste** en `SELL_FILLED.details`
  (solo quedan `cycle_pnl`, `cycles_completed`, `executed_qty`). Por eso el
  mensaje omite la comisión en la inmensa mayoría de los casos (se incluiría
  si algún día se persistiera, el formateador ya lo soporta vía el parámetro
  `commission`, pero hoy siempre se le pasa `None`).
- Ventas por cierre/liquidación: evento **`CELL_LIQUIDATED`**
  (`grid/engine.py:2396-2428`), distinto de `SELL_FILLED`, con
  `details={"qty", "price", "cycle_pnl", "cycles_completed", ...}` ya
  completos — no necesita emparejamiento con otro evento. Se muestra como
  "💰 Venta (cierre)".
- Ventas de la rejilla "repositorio": usan el mismo `SELL_FILLED` que una
  venta normal (mismo motor, mismo `_handle_sell_fill`, el `grid_id` es el
  del grid `strategy="repository"`). Se detectan consultando
  `db.get_grid(grid_id)["strategy"] == "repository"` y se etiquetan también
  como "(cierre)" (`alerts/dispatcher.py:_is_repository_grid`).

**Discrepancia encontrada y resuelta — BUY_PLACED de apertura no se
persiste:** `grid/engine.py:177-178` (`_emit`) solo llama a
`self.event_sink(event)` si `event_sink is not None`; `event_sink` solo se
pone durante una pasada del monitor (`grid/monitor.py:365-366`, restaurado
en `:968`). La apertura inicial de un grid (`POST /api/grids/open`,
`grid/auto_open.py`, `scripts/grid_ctl.py open`) llama a
`engine.create_grid()` **fuera** de cualquier pasada del monitor, así que el
`BUY_PLACED` de los niveles iniciales nunca llega a `grid_events`. Los
rearmes posteriores (tras cada venta, dentro de `_handle_sell_fill` llamado
por `monitor.sync_grid`) sí corren con `event_sink` activo y sí se
persisten. Verificado escribiendo la prueba `test_buy_then_sell_fill_...`
contra un grid recién creado: sin corrección, no se enviaba ninguna alerta
de compra para el primer ciclo. **Resuelto sin tocar `grid/engine.py`**:
`alerts/dispatcher.py:_resolve_buy_price` usa como respaldo
`grid_levels.price` — la línea de grid fija a la que ese nivel se abrió
(inmutable salvo un `ADJUST`), que es el mismo precio exacto al que se
colocó la orden límite original. No es un dato inventado, es la fuente
honesta disponible cuando el evento no existe; documentado con comentario
en el propio código (`alerts/dispatcher.py:236-243`).

**Agrupación**: todos los fills de un mismo grid detectados en la misma
pasada del despachador (no del monitor — el despachador agrupa por lo que
ve nuevo en cada sondeo de 60 s, que normalmente coincide con una sola
pasada del monitor de 15 min) van en un solo mensaje, máx. 10 líneas + "y N
más" (`alerts/formatters.py:MAX_FILL_GROUP_LINES`).

**Latencia**: el monitor corre cada `GRID_MONITOR_INTERVAL` (900 s por
defecto) y el despachador cada `ALERTS_POLL_SECONDS` (60 s), así que un fill
puede tardar hasta ~15-16 min en avisarse. Ninguno de los eventos de fill
guarda la hora real de ejecución en el exchange (solo `ts` de cuando el
monitor lo detectó), así que no hay un "detectado HH:MM" más preciso que
mostrar; no se implementó esa frase porque no hay un dato distinto que
mostrar (sería redundante con la hora del propio mensaje de Telegram).

## 5. D3 — salud

- Monitor detenido: `monitor_runs.started_at` del último registro
  (`db.get_last_monitor_run()`, ya existente) vs. `ALERTS_STALE_MONITOR_MIN`;
  solo si `grid_monitor_enabled`.
- Binance inalcanzable: `GET https://testnet.binance.vision/api/v3/ping`
  **a través** de las variables de proxy del entorno — a propósito lo
  opuesto de `TelegramSender` — para que una caída del túnel sí se detecte
  (corregido en la Fase 10b, ver §12 ítem 1); cada pasada del despachador;
  cooldown natural vía el contador de fallos consecutivos
  (`alert_state.probe_fail_count`) y el flag `alert_state.probe_down`.
- Bucles detenidos: `predictions.predicted_at` (máximo,
  `db.get_last_prediction_time()`, nuevo) y `vol_forecasts.made_at` (máximo,
  `db.get_last_vol_forecast_time()`, nuevo) vs. `ALERTS_STALE_LOOP_MIN`.
- Arranque: un mensaje por arranque del proceso Python — bandera **en
  memoria** (`self._startup_announced_this_process`, se resetea en cada
  `start()`), corregido en la Fase 10b porque la versión original la
  guardaba para siempre en `alert_state` y nunca volvía a avisar tras el
  primer arranque de la vida del servicio (ver §12 ítem 2). El cooldown de
  10 min de `alert_key="startup"` (en `alert_log`, compartido entre
  instancias) sigue aparte, para que una ráfaga de reintentos de systemd no
  inunde el chat.

## 6. D4 — resumen diario

Una vez por día, a partir de `ALERTS_DIGEST_HOUR_LOCAL` hora
`America/Santo_Domingo`, usando `alert_state.digest_last_date` (fecha local
ISO) como marca; se compara contra la fecha local actual, no contra un
conteo de pasadas, así que es correcto sin importar cuántas veces corra el
despachador por hora. Lista grids ACTIVE/PAUSED/CLOSING/HOLDING con ciclos,
ganancia neta de hoy (suma de `SELL_FILLED.details.cycle_pnl` con
`ts >= inicio del día **local** convertido a UTC` — corregido en la Fase
10b, ver §12 ítem 4; antes usaba medianoche UTC, que en
`America/Santo_Domingo` (UTC-4) cae a las 20:00 del día local anterior y
desplazaba 4 horas de "ayer" hacia "hoy") y acumulada (`grid_levels.pnl`),
órdenes abiertas, última pasada del monitor, última predicción, último
ciclo de volatilidad y estado del sondeo a Binance.

## 7. Pruebas (cifras reales, actualizadas tras la Fase 10b — ver §12)

- `tests/test_telegram_sender.py`: 4 pruebas.
- `tests/test_alert_formatters.py`: 18 pruebas (12 originales + 6 de la
  Fase 10b, ítems 3 y 5).
- `tests/test_alert_dispatcher.py`: 20 funciones de prueba, 22 casos (una
  parametrizada en 3) — 17 originales + 4 de la Fase 10b (dos para el
  sondeo corregido, una para el arranque por proceso, una para el límite
  de día local del resumen) — construye un `GridEngine`+`GridMonitor`
  reales con `FakeExchange` (SQLite en memoria, sin red) y verifica los
  mensajes.
- `tests/test_alerts_api.py`: 6 pruebas.
- Total: 50 pruebas nuevas (39 de la Fase 10 + 11 de la Fase 10b), todas
  verdes; suite offline completa sin fallos nuevos (§2 y §12).

Cobertura de los 14 escenarios pedidos: (1) token nunca se filtra — ✅
`test_exploding_exception_never_leaks_token_in_the_returned_error`. (2)
marca de agua inicial = MAX(id) — ✅
`test_initial_watermark_is_max_id_and_sends_no_history`. (3) un
GRID_AUTO_CLOSE nuevo, un solo envío — ✅
`test_new_grid_auto_close_sends_exactly_one_alert_with_reason_and_symbol`.
(4) DUST_RECONCILIATION filtrado — ✅
`test_dust_reconciliation_only_alerts_on_material_drift`. (5) `who: api`
no avisa — ✅ `test_manual_api_events_do_not_alert`. (6) sondeo 2/3/4 fallos
+ recuperación — ✅ `test_probe_alerts_after_threshold_and_on_recovery`. (7)
tope diario y tope de fills — ✅
`test_daily_cap_for_operational_alerts_sends_one_cap_message`,
`test_daily_cap_for_fills`. (8) deshabilitado/sin token — ✅
`test_alerts_disabled_sends_nothing`,
`test_alerts_unconfigured_token_sends_nothing`. (9) sin proxy — ✅
`test_trust_env_is_disabled_so_the_tunnel_proxy_is_never_used`. (10) fallo
de Telegram no rompe el arranque — ✅
`test_dispatcher_does_not_raise_when_telegram_send_fails`. (11)
compra/venta con formato real, agrupación, no-duplicado — ✅
`test_buy_then_sell_fill_messages_use_real_stored_prices_and_pnl`,
`test_two_fills_same_grid_same_pass_produce_one_grouped_message`. (12)
`ALERTS_FILLS_ENABLED=false` — ✅
`test_fills_disabled_still_allows_operational_alerts`. (13) resumen diario
una vez por día hora local — ✅
`test_digest_sends_once_per_local_day_at_configured_hour`. (14)
BOOK_ONE_SIDED con cooldown — ✅ `test_book_one_sided_alerts_with_cooldown`.

## 8. Mutaciones (15/15 detectadas, SHA antes → mutado → restaurado — ver §12 para las 4 de la Fase 10b)

| Ítem | Archivo | SHA antes | SHA mutado | SHA restaurado |
|---|---|---|---|---|
| Redacción del token en errores | `alerts/telegram_sender.py` | `8ff30f0dffd5` | `ae2e8d637323` | `8ff30f0dffd5` |
| `fmt()` evita notación científica | `alerts/formatters.py` | `3ae628babf29` | `4bc234243843` | `3ae628babf29` |
| `format_operational` usa `EVENT_LABELS` | *(cubierto por la prueba de formato exacto, sin mutación dedicada — ver nota)* | — | — | — |
| Avance de marca de agua (no duplicar) | `alerts/dispatcher.py` | `7762f947f3e3` | `530f71088bc9` | `7762f947f3e3` |
| Filtro `who: api` | `alerts/dispatcher.py` | `7762f947f3e3` | `a88d7ca33f63` | `7762f947f3e3` |
| Filtro de motivo en DUST_RECONCILIATION | `alerts/dispatcher.py` | `7762f947f3e3` | `daa2bd6cec1c` | `7762f947f3e3` |
| Umbral de fallos del sondeo | `alerts/dispatcher.py` | `7762f947f3e3` | `34d765b7a5ef` | `7762f947f3e3` |
| Tope diario operativas | `alerts/dispatcher.py` | `7762f947f3e3` | `2241081c2711` | `7762f947f3e3` |
| Respaldo `grid_levels.price` para el precio de compra | `alerts/dispatcher.py` | `7762f947f3e3` | `1a2ee3f85a62` | `7762f947f3e3` |
| Agrupación de fills (umbral >1) | `alerts/dispatcher.py` | `7762f947f3e3` | `41caf48a6e59` | `7762f947f3e3` |
| Hora local del resumen diario | `alerts/dispatcher.py` | `7762f947f3e3` | `6359f24fc166` | `7762f947f3e3` |
| El token nunca aparece en `GET /status` | `api/routes/alerts.py` | `fe2e4201a0e7` | `1802a8a5626a` | `fe2e4201a0e7` |

12 mutaciones (no 13; la fila de `format_operational` no tuvo mutación
propia porque el único cambio significativo mutable —el mapeo de labels—
ya está cubierto por la prueba de igualdad de texto completo, y una mutación
ahí sería indistinguible de una prueba de formato normal). Las 12 mataron
su prueba; las 12 restauraciones se verificaron por SHA-256 y con la
suite del archivo en verde.

**Incidente de codificación durante esta fase**: igual que en la Fase 26,
algunos parches de mutación/restauración vía `pathlib.Path.write_text(...)`
desde Python en Windows introdujeron CRLF — esta vez en
`tests/test_alert_dispatcher.py` (332 bytes `\r`) además de en los propios
archivos de `alerts/` durante el ciclo de mutación. Se corrigió con un
script que reemplaza `\r\n` → `\n` a nivel de bytes; se usó `write_bytes`
en vez de `write_text` para todas las mutaciones posteriores a la primera
detección, y se reverificó 0 `\r` en los 12 archivos tocados/nuevos antes
de cerrar (§2).

## 9. Propuestas fuera de alcance (no implementadas, recordatorio)

Pre-aviso de rango, monitor de remanente calibrado, alertas de señal del
ensemble, UI de historial de alertas, vigilante externo para proceso o
máquina caídos, aviso casi inmediato de cada fill.

**Pérdida de alertas si Telegram falla durante una pasada (Fase 10b, ítem
6 — solo documentado, no implementado):** `_process_events`
(`alerts/dispatcher.py`) avanza `alert_state.last_event_id` al final del
lote **aunque `_send` haya fallado** para alguno de los eventos de ese
lote. Un evento operativo ocurrido mientras Telegram (o el túnel) está
caído queda con `sent=false` en `alert_log`, pero como la marca de agua ya
avanzó, esa pasada posterior nunca lo vuelve a intentar — la alerta se
pierde en silencio (queda constancia en `alert_log`, pero no se reintenta
ni se vuelve a avisar). Arreglarlo correctamente requeriría no avanzar la
marca de agua más allá del primer evento fallido, con reintento acotado y
sin bloquear el resto de eventos indefinidamente si Telegram sigue caído
por horas — eso es un rediseño del lote, no una corrección puntual, así
que se deja fuera de esta fase tal como pedía el prompt.

## 10. No verificado

Entrega real a un chat de Telegram (todo se probó contra `FakeExchange`/
dobles en memoria, sin red); comportamiento con el túnel real caído o con
`HTTPS_PROXY` apuntando a él (la prueba solo confirma que `trust_env=False`
hace que la librería `requests` ignore las variables de entorno, no que el
túnel real se comporte como se espera); el formato visual exacto del
mensaje dentro de la app de Telegram (emojis, saltos de línea).

## 12. Fase 10b — correcciones de la auditoría de Nexo

Preflight: `git rev-parse --short HEAD` → `eae5b81` (coincide); `git status
--short` listaba exactamente los archivos de la Fase 10 (sin commitear).
Archivos autorizados para 10b: `alerts/dispatcher.py`,
`alerts/formatters.py`, `tests/test_alert_dispatcher.py`,
`tests/test_alert_formatters.py`, `REPORTE_FASE10.md`. No se tocó ningún
otro archivo.

| Ítem | Estado | archivo:línea | Nota |
|---|---|---|---|
| 1. `default_probe` siempre `False` (CRÍTICO) | HECHO | `alerts/dispatcher.py:52-58` (antes) | `requests.get(...)` no acepta `trust_env`; `TypeError` silenciada por el `except Exception`. Se quitó el argumento; `requests.get` ya respeta `HTTPS_PROXY`/`NO_PROXY` del entorno por defecto, así que el sondeo sigue pasando por el túnel a propósito (opuesto a `TelegramSender`, que lo evita). |
| 2. Aviso de arranque solo una vez en la vida | HECHO | `alerts/dispatcher.py:_process_startup` | Se cambió la bandera persistida en `alert_state` (para siempre) por una bandera en memoria por instancia (`self._startup_announced_this_process`), reseteada en `start()`. El cooldown de 10 min por `alert_key="startup"` (en `alert_log`, compartido entre instancias/procesos) se mantiene para absorber una ráfaga de reintentos de systemd. |
| 3. "Vendió X a None" | HECHO | `alerts/formatters.py` | `fmt(None)` ahora devuelve `"no disponible"`; se añadió `fmt_price(None) -> "precio no disponible"` para los campos de precio, y `_qty_fragment(None) -> ""` para omitir la cantidad de la frase en vez de imprimir `None`. |
| 4. Resumen diario: "hoy" no coincide con el día local | HECHO | `alerts/dispatcher.py:_build_digest` | Se añadió `_local_day_start_utc()` (medianoche en `America/Santo_Domingo` convertida a UTC) y se usa solo para `pnl_today` del resumen; el tope diario (`_daily_cap_remaining`) sigue usando `_day_start_utc` (medianoche UTC) porque el ítem no pedía cambiar eso. |
| 5. Posible mojibake | VERIFICADO, 0 encontrado | — | `open(path, encoding="utf-8").read()` de los 13 archivos nuevos/modificados de la Fase 10 (incluido este reporte) con conteo de `"Ã"`, `"â€"`, `"ï¸"`, `"Â"`: **0 en los 13 archivos**. El volcado con mojibake que vio Nexo es un artefacto de cómo PowerShell 5.1 (`Get-Content`/`Out-File`) lee/escribe UTF-8 sin BOM — no está en los bytes reales del archivo. Se añadió una prueba explícita (`test_format_buy_fill_uses_real_utf8_accents_and_em_dash`) que afirma literalmente `"Compra ejecutada — GRAMUSDT #4"` y `"Compró"` con los caracteres UTF-8 reales. |
| 6. Pérdida de alertas si Telegram falla | NO HECHO (documentado a propósito) | `alerts/dispatcher.py:_process_events` | Ver §9 — añadido a "Propuestas fuera de alcance" con la descripción completa del mecanismo del defecto. No se tocó el código. |

### Mutaciones de la Fase 10b (4/4 detectadas, SHA antes → mutado → restaurado)

| Ítem | Archivo | SHA antes | SHA mutado | SHA restaurado |
|---|---|---|---|---|
| 1. `default_probe` sin `trust_env` | `alerts/dispatcher.py` | `6a7515c05b9b` | `ace392d89574` | `6a7515c05b9b` |
| 2. Bandera de arranque en memoria | `alerts/dispatcher.py` | `b5c220f2f86b` | `ba603cfa29fe` | `b5c220f2f86b` |
| 3. `fmt(None)` no imprime "None" | `alerts/formatters.py` | `20dd1d97164f` | `e80e03f3a247` | `20dd1d97164f` |
| 4. Día local del resumen diario | `alerts/dispatcher.py` | `21db2158170a` | `7b054a2c2a64` | `21db2158170a` |

Las 4 mutaciones mataron su prueba correspondiente; las 4 restauraciones se
verificaron por SHA-256 y con la suite completa de los dos archivos
tocados en verde (`tests/test_alert_dispatcher.py` +
`tests/test_alert_formatters.py`, 38 pruebas). Todas las mutaciones y
restauraciones de esta sub-fase se escribieron con `pathlib.write_bytes`
desde el principio (no `write_text`), así que no reintrodujeron el
problema de CRLF de las fases anteriores.

### Pruebas nuevas de la Fase 10b (11, todas verdes)

`tests/test_alert_dispatcher.py`: `test_default_probe_returns_true_when_the_endpoint_responds`,
`test_default_probe_respects_env_proxy_settings`,
`test_startup_message_fires_once_per_process_but_not_forever_silenced`,
`test_digest_pnl_today_uses_local_day_boundary_not_utc_midnight`.
`tests/test_alert_formatters.py`: `test_fmt_of_none_never_prints_the_word_none`,
`test_format_buy_fill_line_with_missing_price_says_unavailable_not_none`,
`test_format_buy_fill_line_with_missing_qty_omits_the_quantity`,
`test_format_sell_fill_line_with_missing_sell_price_says_unavailable`,
`test_format_sell_fill_with_missing_qty_omits_the_quantity`,
`test_format_buy_fill_with_missing_buy_price_says_unavailable`,
`test_format_buy_fill_uses_real_utf8_accents_and_em_dash`.

### Cierre de la Fase 10b

Suite offline completa, una sola vez:
```
3 failed, 1265 passed, 1 skipped, 18 deselected, 50 warnings in 278.84s (0:04:38)
```
1265 = 1254 (cierre de la Fase 10) + 11 pruebas nuevas de esta sub-fase.
Mismos 3 fallos preexistentes de siempre, ninguno nuevo. `git status
--short` seguía listando
exactamente los archivos de la Fase 10 (ninguno nuevo fuera de los 5
autorizados para 10b, que ya estaban dentro de esa lista). `git diff
--check`: sin advertencias reales. 0 CRLF (verificado con `od` byte a
byte en los 5 archivos autorizados), 0 U+FFFD, 0 mojibake (§item 5), 0
apariciones del token/chat id. **No se hizo commit.**

```
alerts/__init__.py
alerts/telegram_sender.py
alerts/formatters.py
alerts/dispatcher.py
api/routes/alerts.py
api/main.py
config/settings.py
database/db_manager.py
tests/test_telegram_sender.py
tests/test_alert_formatters.py
tests/test_alert_dispatcher.py
tests/test_alerts_api.py
REPORTE_FASE10.md
```
