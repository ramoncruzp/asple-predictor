# Reporte Fase 29b: escalera legible, motivo de error y tarjeta del experimento

## A. Escalera de bots: semántica

**HECHO.** `grid_levels` persiste `order_id` (`database/db_manager.py:178`), `client_order_id` y `buy_client_order_id` (`database/db_manager.py:187-188`). La vista marca `has_live_order` solo cuando `state` es `BUY_OPEN` o `SELL_OPEN` y `order_id` no es nulo (`grid/status_view.py:315-352`). Se usa `order_id` junto con el estado de la base: un client ID por sí solo puede identificar una intención y no prueba que haya un ID de orden abierta. No se consulta Binance; el indicador describe lo que registra la base.

**HECHO.** La tabla muestra "con orden" o "sin orden". Un nivel en espera sin `has_live_order` recibe la clase ámbar y el tooltip solicitado; los estados pausados no reciben esa clase. El error conserva la clase roja (`frontend/grids.js:284-324`, `frontend/grids.css:58`).

**HECHO.** La leyenda plegada explica los cinco estados, la semántica local del indicador y el cálculo con su signo. El cálculo fuente es `(target - price) / price * 100.0` (`grid/status_view.py:309-312`); no se cambió `state_label` ni el signo (`frontend/grids.js:502`).

## B. Motivo del error de nivel

**HECHO.** La pantalla ya solicita `/api/grids/{id}/events` (`frontend/grids.js:520-529`). El endpoint devuelve por defecto los 50 eventos más recientes (`api/routes/grid_status.py:149-155`); el cliente elige el `LEVEL_ERROR` más reciente que coincide con nivel (`frontend/grids.js:297-310`). Si hay más de 50 eventos y el error quedó fuera de esa ventana, se muestra el texto de respaldo. No se añadieron campos al backend.

**HECHO.** En la fila roja se muestra el motivo, su antigüedad y el motivo de respaldo cuando no hay evento. Se reutiliza la traducción `policy_adjust` introducida en 29a; los demás motivos aparecen en crudo. El contenido de API se escapa con `esc()` (`frontend/grids.js:303-324`). Sin botón de reparación.

## C. Tarjeta del experimento

**HECHO.** La tarjeta se presenta justo bajo la cabecera y usa solo el resumen existente: `progress.max_days`, ciclos, neto realizado, neto tras comisiones estimadas, rango, número de niveles, capital y precio (`frontend/grids.js:427-445`). Si falta un valor se ve "—"; grids sin `max_days` configurado muestran guiones para edad y días restantes. El progreso reutiliza `.progress`; el vencimiento muestra aviso (`frontend/grids.js:431-441`, `frontend/grids.css:58-59`). La nota indica que Testnet cobra 0 de comisión.

**HECHO.** "Copiar resumen" envía texto plano con las cifras y la hora local mediante `navigator.clipboard.writeText`; el rechazo o ausencia del portapapeles se maneja sin mensaje (`frontend/grids.js:327-343`, `frontend/grids.js:535-537`).

## Pruebas y mutaciones

- `tests/test_grid_status_view_phase29b.py:4-19` verifica casos verdadero/falso para orden abierta, sin `order_id` y estado no abierto, incluido client ID sin orden.
- `tests/ui/test_fase29b_grid_detail.py:19-46` verifica indicador, ámbar, estado pausado y leyenda cerrada; `:48-76` verifica motivo traducido, antigüedad y respaldo; `:78-117` verifica plazo, ciclos, neto, barra, copia y aviso de vencimiento.
- No se actualizaron pruebas preexistentes ni anclas de texto.
- Mutación que invierte `has_live_order`: la prueba unitaria falló como se esperaba. SHA-256 antes/restaurado `e6f9a8ff24717915554e9f966a681af59f9c58c98fe4592af7203e3405f1d4ed`; SHA mutado `f78790a9af97dfb339b57fd1e876196f019f2a2cf43efd60ecfc340cbf70249b`.
- Mutación que reemplaza el `<details>` de la leyenda por un `<div>`: la prueba visual falló como se esperaba. SHA-256 antes/restaurado `09226d5cb93e8211a42423b4f3a1d6979a3ff082060dfc6223a1ab1dd2ae1772`; SHA mutado `56d47cd05867ce82231128ca57043248b3c4309a0d352a269e67a090e2aaa4b2`.
- Tras ambas mutaciones, la restauración fue byte idéntica.

## Verificación visual y automatizada

- Capturas fixture antes/después del detalle en `.pytest-29b-shots/before/` y `.pytest-29b-shots/after/`: escritorio 1440×1000 y móvil 390×844. Se inspeccionaron las capturas; la vista posterior muestra la tarjeta, la fila ámbar sin orden, indicadores de orden y error rojo con "Reajuste por política · hace 3 h". También se guardaron variantes de página completa posteriores. Son datos construidos por fixture.
- `node --check` pasó en cada uno de los seis `frontend/*.js`.
- Suite única: `ASPLE_OFFLINE=1 .\\venv\\Scripts\\python.exe -m pytest tests -m "not live" -q --basetemp=.pytest-29b-final` — **1322 passed, 0 failed, 1 skipped, 18 deselected**.
- `git diff --check` pasó. La auditoría de archivos del cambio y el reporte encontró 0 CRLF, BOM, U+FFFD, marcadores de mojibake o signos de interrogación sustitutivos; el escaneo de secretos no encontró coincidencias.
- **NO VERIFICADO:** datos reales de S1/Testnet y dispositivo móvil físico. El endpoint de eventos limita la búsqueda visual a sus últimos 50 eventos por defecto.

## Archivos incluidos

- `frontend/grids.js`
- `frontend/grids.css`
- `grid/status_view.py` (campo de lectura `has_live_order`)
- `tests/test_grid_status_view_phase29b.py`
- `tests/ui/test_fase29b_grid_detail.py`
- `REPORTE_FASE29B.md`
