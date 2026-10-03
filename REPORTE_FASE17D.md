# Reporte Fase 17D — Endurecimiento previo a VPS

No se hicieron commits ni push, no se leyeron `.env` y no hubo conexiones reales a Binance/Testnet. Las pruebas usaron fakes y `ASPLE_OFFLINE=1`.

## Paso 0 — Diagnóstico

1. `GridEngine._sync_grid` compara las órdenes de la DB con `get_open_orders`; las ausentes se consultan con `get_order` (`grid/engine.py:1735-1869`). El cliente convierte una respuesta Binance `-2013` en `TestnetOrderError` (`data/testnet_client.py:301-313`). El catch genérico de `_sync_grid` trata esa excepción como fallo de consulta: para una SELL con inventario conserva la protección y reintenta; para otros niveles los pasa a `ERROR` y emite `LEVEL_ERROR` (`grid/engine.py:1869-1882`). Antes del bloque E no había una detección agregada de reset.
2. `cancel_order` normaliza la respuesta de cancelación mediante `_normalize_order`; `executedQty` se expone como `executed_qty` y `status` se conserva (`data/testnet_client.py:90-101,216-250`). Si el cancel informa que la orden ya no existe tras `-2011`, consulta y devuelve la orden real, incluido su llenado (`data/testnet_client.py:226-240`).
3. `VolLoop` obtiene klines con `self.binance_client.get_historical_klines` (`scheduler/vol_loop.py:64-70`). La app crea ese cliente en el lifespan y se lo da a `VolLoop` (`api/main.py:39,55-56`); el provider de grids recibe ese mismo cliente en `api/main.py:75,90-93`, sin nuevas credenciales.
4. `evaluate_grid` admite `sigma_24h=None`: no calcula probabilidad de ruptura y mantiene las decisiones que no dependen de sigma; una pausa motivada por `break_prob` no se reanuda sin volatilidad (`grid/policy.py:519-557`). El monitor emite `VOL_UNAVAILABLE` con el motivo y conserva la política existente (`grid/monitor.py:328-388`).

## Bloques

### A — Grid CLOSED por `max_days`

El bucle solo finaliza targets CLOSED cuando existe `target_close_plan` (`grid/monitor.py:665-678`). `close_grid_target` rechaza planes vacíos antes de modificar DB (`grid/engine.py:2586-2597`). La prueba inicia un grid CLOSED con `max_days_close_plan.phase=STARTED`, usa el monitor y motor reales, y comprueba cierre, un solo `MAX_DAYS_REACHED`, fase `EVENT_EMITTED` y cero llamadas al finalizador de target (`tests/test_grid_maxdays.py:84-106`). También se mantiene el caso target legítimo y se prueba la defensa directa.

Límite: no se ejercitó contra Testnet real.

### B — BUY cancelado con llenado parcial

El helper común reusa `_handle_buy_fill` con `executed_qty`, registra el evento `BUY_PARTIAL_SETTLED` y protege el inventario (`grid/engine.py:1016-1031`). Lo llaman pausa, cancelación y cierre a repositorio (`grid/engine.py:1295,2102,2369`). Las pruebas cubren los tres caminos y el caso de ejecución cero (`tests/test_grid_monitor.py:17-59`).

Límite: no se simuló una caída de proceso entre el registro de la compra parcial y la creación de la SELL de protección; se conserva la idempotencia del registro de fill ya existente.

### C — Colisiones de niveles SELL por moneda

La guarda pura aplica `max(tick_size, precio_nuevo × tolerancia_pct)` e ignora niveles DONE (`grid/guards.py:8-24`). El motor y el dry-run API la usan con grids OPENING/ACTIVE/PAUSED/CLOSING/HOLDING; el confirm pasa por el motor (`grid/engine.py:312-322`, `api/routes/grids.py:157-171`). El umbral se configura en `config/settings.py:60`. Hay pruebas de tolerancia/tick, estrategias distintas, grids HOLDING, conflicto y dos grids de igual símbolo con SELL distintos (`tests/test_grid_guards.py:6-37`, `tests/test_grid_schema_15b.py:55-66`, `tests/test_grid_status_api.py:104-123`).

Límite: el contrato fue probado con fakes locales; la carrera entre dos aperturas concurrentes no tiene una restricción transaccional nueva en DB.

### D — Autorización API

`authorize` comparte la regla de token/loopback (`api/auth.py:10-27`) y `grids.py` conserva `_authorize` como alias. El middleware protege rutas `/api/*` salvo `/api/health` (`api/main.py:144-154`). `ApiClient` añade `X-API-Token` cuando hay token y ante 403 permite ingresarlo en memoria y reintenta (`frontend/app.js:13-27`); los otros módulos de grids, scanner y cuenta ya envían el header si está configurado. Pruebas de token, loopback/remoto, health y header Node: `tests/test_api_auth_phase17d.py:17-65`.

Límite: el test recorre el middleware compartido y una ruta representativa, no enumera individualmente todos los métodos de cada router. La entrada interactiva se verificó en `app.js`; no se hizo una prueba visual de navegador.

### E — Detección de reset Testnet

Antes de procesar grids se comparan órdenes abiertas registradas con las abiertas/consultables en exchange; la sospecha requiere al menos `testnet_reset_min_unknown` respuestas `-2013` y ninguna orden ausente en estado terminal (`grid/monitor.py:94-157`, `config/settings.py:69`). En sospecha emite un evento por episodio, bloquea los grids afectados y omite su procesamiento (`grid/monitor.py:290,668-670`). Los eventos `TESTNET_RESET_DETECTED` y `TESTNET_RESET_RECOVERED` hacen que el estado del episodio sobreviva reinicios (`grid/monitor.py:95-102,145-157`). `/api/monitor/status` expone bandera e inicio (`api/routes/grid_status.py:197-210`). Pruebas fakes cubren reset, repetición en nueva instancia, terminal FILLED ausente de open orders, una sola inexistente, recuperación, niveles intactos y ausencia de nuevas órdenes (`tests/test_grid_monitor.py:63-142`).

Límite: las pruebas usan respuestas fake `-2013`; no se conectó a Testnet.

### F — Sigma para símbolos adicionales

XRP mantiene el pronóstico modelo, incluyendo su ruta stale; otros símbolos obtienen 1h de siete días, retornos log, máximo entre EWMA λ=0.94 y desviación estándar, sigma 24h y caché de 30 minutos (`grid/volatility_provider.py:17-22,49-91`). Menos de 100 cierres o error de consulta produce `None` con motivo (`grid/volatility_provider.py:93-126`). El monitor añade `vol_source` a métricas/eventos (`grid/monitor.py:548-551`). Con provider inyectado, motor y ruta `/open` rechazan smart sin sigma (`grid/engine.py:277-278`, `api/routes/grids.py:122-126`). Pruebas sintéticas cubren sigma relativo, barras insuficientes, TTL y regresión XRP (`tests/test_volatility_provider.py:31-130`); API prueba rechazo en dry-run y confirm (`tests/test_grids_api.py:196-203`).

Límite: el sigma `realized` no está calibrado como el sigma de XRP. No se midió su calidad con mercado real ni se cambiaron umbrales de política.

### G — Umbral scanner

El default es `0.7` (`config/settings.py:59`), con prueba sin cargar `.env` (`tests/test_settings_phase17d.py:4-5`). Se mantuvieron pesos, filtros y `scanner_auto_open=False`. El dato auditado de XRP `0.702` y margen neto `0.034 %` fue proporcionado por el contexto del prompt y no se recalculó en esta fase; subir el umbral no corrige ese margen.

Límite: no se recalibró ni se ejecutó un scan de mercado.

### H — SQLite y respaldo

Las conexiones SQLite de archivo establecen WAL, busy timeout 5000 y synchronous NORMAL, sin activar foreign keys; memoria no recibe esos PRAGMA (`database/db_manager.py:24-34`). `BackupLoop` usa `sqlite3.Connection.backup`, corre al iniciar si corresponde y diariamente, conserva el número configurado y captura fallos sin propagarlos (`scheduler/backup_loop.py:15-69`). El lifespan lo registra y detiene (`api/main.py:72,118,124,136`). Settings y exclusiones están en `config/settings.py:66-68` y `.gitignore:9-11,23`. Pruebas verifican PRAGMA, copia consultable con el mismo registro, retención y modo deshabilitado (`tests/test_backup_loop.py:8-45`).

Límite: no se probó una caída abrupta del proceso durante una copia ni la recuperación de un backup en un VPS.

### I — Conciliación periódica de remanentes

Este bloque se detuvo conforme a la condición de diseño del prompt. La conciliación existente toma `free + locked` del saldo total de cuenta y lo compara con las cantidades asignadas a grids; cualquier exceso se etiqueta “saldo no asignado a ningún grid” (`api/routes/grid_account.py:168-203`). Esa cantidad también puede incluir XRP u otros activos previos/ajenos a grids, por lo que usarla como alarma del monitor sin una línea base produciría una advertencia ambigua. Sin consultar Testnet (prohibido) no hay evidencia para separar saldo preexistente de discrepancias nuevas; no se emitió `DUST_RECONCILIATION` ni se añadió umbral de alerta.

## Mutaciones y verificación

Mutación temporal restaurada byte por byte y detectada por pruebas: A, B, C, D, E, F y H: **7/7**. G: no aplica (cambio de default). I: no ejecutada porque el bloque se detuvo por la ambigüedad de línea base documentada arriba.

`node --check frontend/app.js`, `node --check frontend/grids.js` y `node --check frontend/scanner.js`: correctos. Suite offline final: `ASPLE_OFFLINE=1 .\venv\Scripts\python.exe -m pytest -q -m "not live" --basetemp=.pytest_17d_final` → **685 passed, 1 skipped, 18 deselected, 7 warnings**. No incluye pruebas live/Testnet.
