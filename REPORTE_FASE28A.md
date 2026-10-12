# Fase 28a — órdenes huérfanas al cerrar grids

## Alcance y preflight

- **HECHO** — Preflight de un comando: `HEAD base=dda4464`; `git status --short` vacío antes de empezar.
- **HECHO** — Diagnóstico estático y herramienta de inventario de solo lectura. No se ejecutó el script contra Testnet ni se abrió la base operativa.
- **HECHO** — No se cambió ningún método existente de `data/testnet_client.py`; ya existía `get_open_orders(symbol)` en `data/testnet_client.py:252-256`.
- **HECHO** — No hubo `add`, commit ni push.

## A. Análisis estático del cierre

1. **Modo y ruta de la acción API del grid #3 — NO HECHO para el modo exacto.** El dato entregado (`GRID_ACTION_API reason=close`) no incluye `params.mode` ni `GRID_CLOSE_STARTED.details.mode`. La API pasa el modo solicitado a `engine.close_grid()` en `grid/control_service.py:247`; el motor lo guarda en `GRID_CLOSE_STARTED.details` en `grid/engine.py:2613-2629`. Por tanto, el modo concreto solo se confirma leyendo ese detalle del evento. La marca posterior de niveles `DONE` es compatible con la rama `liquidate` (`grid/engine.py:2681-2690`), pero no prueba por sí sola que ese fuera el modo. Si el detalle indica `liquidate`, la ruta es API → `close_grid(..., "liquidate")` → `sync_closing()` → `cancel_grid_orders(finalize=False)` → liquidación/cierre.

2. **Cancelación de compras — HECHO.** La ruta común toma `order_id`; si falta y existe `client_order_id`, intenta recuperar el `order_id` por ese ID de cliente (`grid/engine.py:2147-2159`). Después consulta órdenes abiertas y cancela por `order_id` (`grid/engine.py:2160-2161`, `2205-2224`). No se llama a la cancelación directamente por `client_order_id`. Un estado de respuesta distinto de `CANCELED`/`FILLED` se agrega a `cancel_errors`; las excepciones también se agregan y se intenta una consulta posterior de estado, cuyo error secundario sí se ignora (`grid/engine.py:2211-2233`). Las órdenes propias que siguen abiertas se devuelven en `pending_order_ids` y `fail_reason` (`grid/engine.py:2234-2246`). En la ruta `repository`, los errores detienen el cierre y dejan el grid `CLOSING` (`grid/engine.py:2474-2502`).

3. **Niveles `DONE` y grid `CLOSED` — HECHO.** En modo `liquidate`, cada nivel sin inventario se marca `DONE` aunque la cancelación anterior haya devuelto errores (`grid/engine.py:2682-2690`). El grid solo se marca `CLOSED` si la lista `errors` está vacía (`grid/engine.py:2716-2717`), pero esa lista solo copia `cancel_errors`; no incorpora `pending_order_ids` (`grid/engine.py:2682-2683`). Así, `DONE` no prueba cancelación exitosa y un residuo que solo figure como `pending_order_ids` no impide este cierre. La respuesta `cancel` también conserva el estado anterior si hay errores u órdenes pendientes (`grid/engine.py:2237-2246`).

4. **Ausencia de evento de cancelación — HECHO.** `cancel_grid_orders()` no emite un evento por orden. `close_grid()` sí emite `GRID_CLOSE_STARTED` y al final `GRID_CLOSED` (`grid/engine.py:2624-2629`, `2738`), y `_close_event()` persiste directamente si no hay `event_sink`, o delega al sink si existe (`grid/engine.py:2260-2275`). El monitor instala el sink y persiste sus eventos (`grid/monitor.py:365-366`; `_emit`, `grid/monitor.py:63-77`). Por ello, la falta de evento de cancelación no se explica por un `event_sink` inactivo: esa clase de evento no se genera. El patrón de `_emit()` sin sink sí descarta eventos enviados directamente a `_emit()` (`grid/engine.py:172-190`), pero no es el comportamiento de `_close_event()`.

5. **Cierre automático del grid #1 — HECHO.** `GRID_AUTO_CLOSE` llama a `close_grid(grid_id, "repository")` (`grid/monitor.py:797-803`). Comparte el método general `close_grid`, pero no la acción manual API ni su modo explícito; recorre la rama `_close_repository` (`grid/engine.py:2679-2680`). Esta rama cancela solo niveles cuyo estado sea `BUY_OPEN`; si no hay `order_id` y la búsqueda por `client_order_id` no devuelve una orden, deja el nivel `IDLE` y sigue (`grid/engine.py:2474-2483`).

6. **Causas posibles, ordenadas — HECHO como análisis de código, no como diagnóstico confirmado de S1.**
   1. Si el modo real fue `liquidate`, `pending_order_ids` no se incorpora a `errors`; la rama puede marcar niveles `DONE` y aceptar `CLOSED` aunque la comprobación final haya visto órdenes abiertas (`grid/engine.py:2234-2246`, `2682-2690`, `2716-2717`). Es la explicación de código que mejor concuerda con niveles `DONE` y grid `CLOSED`.
   2. Si el modo fue `repository`, una orden en un nivel cuyo estado ya no sea `BUY_OPEN` se omite; además, una búsqueda de ID de cliente que no encuentra resultado convierte el nivel en `IDLE` sin error (`grid/engine.py:2474-2483`).
   3. En `cancel_grid_orders()`, un nivel sin `order_id` ni `client_order_id` no entra en el conjunto de IDs propios ni en la iteración de cancelación; por tanto, esa función no puede verificar ese encargo a partir de la fila (`grid/engine.py:2147-2159`, `2187-2190`).
   4. Un error de `sync_closing()` se registra y deja el estado `CLOSING`, pero se captura y el flujo intenta continuar con el cierre (`grid/engine.py:2630-2634`); una vista local desactualizada puede afectar qué identificadores se inspeccionan después.
   5. La falta de evento individual de cancelación reduce la observabilidad, pero por sí sola no deja órdenes abiertas (`grid/engine.py:2138-2258`).

## B. Inventario de órdenes

- **HECHO** — `scripts/orphan_orders_report.py` lee `grids` y `grid_levels` mediante SQLite URI `mode=ro` y activa `PRAGMA query_only`.
- **HECHO** — Para cada símbolo histórico consulta `get_open_orders(symbol)` del cliente Testnet existente. El script redacta excepciones mostrando solo el tipo, nunca texto que pudiera incluir una URL firmada.
- **HECHO** — La función pura clasifica `OK`, `HUÉRFANA` y `DESCONOCIDA`; los importes se suman como USDT solo para pares terminados en `USDT`.
- **HECHO** — Salida real del script: **NO**; se deja para que Ramón lo ejecute en S1 con `venv2/bin/python scripts/orphan_orders_report.py`.
- **NO VERIFICADO** — Órdenes abiertas actuales de Testnet y valor actual de las huérfanas. No se hizo ninguna consulta al exchange.

## C. Pruebas y verificación

- **HECHO** — `ASPLE_OFFLINE=1 python -m pytest -q tests/test_orphan_orders_report.py`: `9 passed in 0.06s`.
- **NO HECHO** — Suite offline completa: `ASPLE_OFFLINE=1 python -m pytest tests -m "not live" -q` terminó durante la colección con **9 errores**, todos por `ModuleNotFoundError: No module named 'ta'` (por ejemplo, `tests/test_analyze_model.py:4` importa modelos que llegan a `data/feature_engineer.py:7`); **18 live** quedaron excluidas. No hubo resumen de pruebas completas ni se alcanzó el objetivo de más de 1270 pasadas. No se instaló ninguna dependencia.
- **HECHO** — La mutación de la regla `ACTIVE` usa `str.replace` con `assert count == 1`, demuestra el resultado invertido y compara SHA-256 de fuente original, mutada y fuente restaurada en disco. Evidencia: `replace_count=1`; antes/restaurado `bab7a956a759dd2ace228d06a31895c7842df729060b4eafcda34e33432ec49d`; mutada `6f4512c1e97fc63dec3717f1b976bae1dd928f8b57a6dac3c506f18c626e0bb2`.
- **HECHO** — Una prueba inspecciona el AST del script para impedir imports o llamadas a funciones de escritura de órdenes.
- **NO VERIFICADO** — Ejecución real del script en S1 e interacción con Testnet.

### Diseño propuesto para Fase 28b (no implementado)

Después de solicitar cancelaciones, volver a listar las órdenes abiertas del símbolo y cruzar por los prefijos/IDs propios del grid. Mantener el grid en `CLOSING` con `fail_reason` si sobrevive alguna orden; no marcar niveles como terminados ni el grid como cerrado hasta confirmar que no queda ninguna. Persistir un evento específico con IDs y motivo para que alertas posteriores puedan avisar. Esta fase no cambia el motor ni añade eventos.

### Archivos para un único commit futuro

- `scripts/orphan_orders_report.py`
- `tests/test_orphan_orders_report.py`
- `REPORTE_FASE28A.md`

No se creó el commit.

### Auditoría final

- **HECHO** — `git status --short` contiene solo los tres archivos nuevos listados arriba; `git diff --check` terminó con código 0 y sin salida. Escaneo de espacio final en los tres archivos: `0`.
- **HECHO** — Comprobación con `od -tx1`: `0d 0a` (CRLF) = `0` en cada archivo. UTF-8 BOM = `0`, U+FFFD = `0`, mojibake detectado = `0`.
- **HECHO** — No hay claves ni tokens literales en los archivos. Las dos coincidencias del escaneo textual corresponden a referencias de variables (`secret = str(settings.testnet_api_secret)` y el paso de `key`/`secret` al constructor) en `scripts/orphan_orders_report.py:124-127`, no a valores.
