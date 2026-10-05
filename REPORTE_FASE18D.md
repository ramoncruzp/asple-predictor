# Fase 18D — Vista previa del precio de Testnet

## Cambios

- `api/routes/grids.py:51-64, 212-229`: la vista previa lee el libro Testnet mediante `engine.exchange.get_book_ticker`, calcula el midpoint y devuelve `testnet_price`, `testnet_in_range` y `testnet_price_guard`. Si no se puede leer, devuelve `allowed: null` y mantiene la vista previa disponible.
- `frontend/scanner.js:47-50, 159-160`: muestra precio público y Testnet; bloquea Confirmar cuando Testnet está fuera de rango, mantiene el botón disponible si la lectura falla y traduce el error de apertura incluyendo los precios del último plan.
- `frontend/app.js:252`: identifica el precio de la gráfica del Scanner como mercado público.
- `tests/test_grids_api.py:156-204`: cubre libro dentro/fuera, lectura fallida y que solo se invoque el método de lectura, sin crear órdenes.
- `tests/ui/test_grids_browser.py:218, 245-327`: cubre estado del botón, aviso de lectura fallida y traducción del 422 durante confirmación.

## Casos y pruebas

| Caso | Prueba | Resultado |
|---|---|---|
| Testnet dentro, fuera y lectura fallida; método de solo lectura | `test_grids_api.py -k "dry_run or open"` | 10 passed, 9 deselected |
| Traducción y comportamiento JS existente | `test_frontend_scanner.py` | 22 passed |
| Diálogo, botón y error 422 en navegador | Playwright Scanner | 10 passed, 22 deselected |
| Mutación: ignorar `testnet_in_range` | `test_scanner_testnet_price_guard[outside]` | Falló en la aserción de botón deshabilitado, como se esperaba; bytes restaurados idénticos |
| Sintaxis JavaScript | `node --check frontend/scanner.js` | exit 0 |

## Ejecuciones

- `$env:ASPLE_OFFLINE='1'; .\venv\Scripts\python.exe -m pytest tests/test_grids_api.py -k "dry_run or open" -p no:cacheprovider -q --basetemp=.pytest-18d-api` → `10 passed, 9 deselected in 3.58s`.
- `$env:ASPLE_OFFLINE='1'; .\venv\Scripts\python.exe -m pytest tests/test_frontend_scanner.py -p no:cacheprovider -q --basetemp=.pytest-18d-scanner` → `22 passed in 9.16s`.
- Playwright inicial → `1 failed, 9 passed, 22 deselected`: una expectativa anterior decía `101` en vez del formato vigente `101.00`. Al ajustar ese texto se produjo una corrupción transitoria del archivo de prueba por lectura/escritura PowerShell; se reparó, se verificaron sus caracteres y se volvió a ejecutar.
- Comando Playwright inicial: `$env:$env:ASPLE_OFFLINE='1'; .\venv\Scripts\python.exe -m pytest tests/ui/test_grids_browser.py -k scanner -p no:cacheprovider -q --basetemp=.pytest-18d-ui` → `1 failed, 9 passed, 22 deselected`.
- Corrida de diagnostico tras el ajuste: `... pytest tests/ui/test_grids_browser.py -k scanner -p no:cacheprovider -q --basetemp=.pytest-18d-ui-retry` → `7 failed, 3 passed, 22 deselected`; el archivo de prueba estaba codificado incorrectamente por la escritura PowerShell y se reparo antes de continuar.
- Comando de Playwright final: `ASPLE_OFFLINE=1 .\venv\Scripts\python.exe -m pytest tests/ui/test_grids_browser.py -k scanner -p no:cacheprovider -q --basetemp=.pytest-18d-ui-final` → `10 passed, 22 deselected, 2 warnings in 31.12s`.
- Playwright final: `$env:ASPLE_OFFLINE='1'; .\venv\Scripts\python.exe -m pytest tests/ui/test_grids_browser.py -k scanner -p no:cacheprovider -q --basetemp=.pytest-18d-ui-final` → `10 passed, 22 deselected, 2 warnings in 31.12s`.
- Mutación temporal + sintaxis: la prueba mutada retornó exit 1 en `test_scanner_testnet_price_guard[outside]` (falla en `confirm.is_disabled()`); `BYTE_EXACT_RESTORE True`; `node --check frontend/scanner.js` → `NODE_CHECK_EXIT 0`.

## Límites y verificación

- **NO VERIFICADO:** precio actual de Testnet de PEPEUSDT ni de otras monedas; ninguna apertura real en Testnet. Solo se usaron fakes y respuestas interceptadas en Playwright.
- El fake verifica que se invoca `get_book_ticker` una vez y que no se invoca la ruta de creación de órdenes durante dry-run; no es una prueba de red real.
- Los archivos de código modificados se guardaron UTF-8 sin BOM y LF; el JavaScript agregado usa escapes `\\uXXXX` para caracteres no ASCII. Los informes se guardan UTF-8 sin BOM y LF.
- No se ejecutó suite completa. No hubo stage, commit ni push.

## Complemento de cierre 18D ? libro vacío y prevalidación real

- `api/routes/grids.py:51-76`: la vista previa reconoce `bid <= 0` y/o `ask <= 0`; no calcula mid, marca `testnet_in_range: false` y devuelve motivo localizado con símbolo y ambos lados.
- `api/routes/grids.py:124-139, 284-324`: antes de `engine.create_grid`, la apertura confirmada lee el libro Testnet. Un lado vacío o un mid fuera de rango produce 422 en español sin invocar el motor. `_reject` agrega rango, niveles y el snapshot `bid/ask/mid` a `GRID_OPEN_REJECTED`.
- `frontend/scanner.js:159-160`: si el libro está vacío y no hay mid, el diálogo muestra el motivo del guard en rojo; el botón continúa bloqueado.
- `tests/test_grids_api.py:191-208, 302-353, 455-464`: casos ask cero en dry-run y confirmación, rechazo sin llamada a `create_grid`, diagnóstico con y sin mid; el doble del motor exitoso ahora incluye un lector de libro.

| Caso | Prueba | Resultado |
|---|---|---|
| Ask cero en dry-run | `test_dry_run_blocks_testnet_book_with_empty_ask_side` | Incluida en 11 passed |
| Ask cero al confirmar, sin motor y con evento | `test_confirm_rejects_empty_testnet_ask_before_engine_and_logs_snapshot` | Incluida en 11 passed |
| Mid fuera del rango al confirmar y snapshot auditado | `test_confirm_rejects_testnet_mid_out_of_range_and_logs_mid_snapshot` | Incluida en 11 passed |
| Mutación que ignora `testnet_in_range` | `test_scanner_testnet_price_guard[outside]` | 1 failed como esperado; `BYTE_EXACT_RESTORE True` |
| Sintaxis de Scanner JS | `node --check frontend/scanner.js` | `NODE_CHECK_EXIT 0` |

### Verificaciones de este complemento

- Primera corrida API: `... pytest tests/test_grids_api.py -k "dry_run or open" -p no:cacheprovider -q --basetemp=.pytest-18d2-api` → `1 failed, 10 passed, 11 deselected` (el doble de motor no tenía el lector requerido). Segunda corrida tras ajustar el doble: `ERROR collecting tests/test_grids_api.py` por `IndentationError`; se corrigió antes del pase verde.
- API: `$env:ASPLE_OFFLINE='1'; .\venv\Scripts\python.exe -m pytest tests/test_grids_api.py -k "dry_run or open" -p no:cacheprovider -q --basetemp=.pytest-18d2-api-retry` → `11 passed, 11 deselected in 3.69s`. Corridas previas: `1 failed, 10 passed, 11 deselected` porque el doble `RecordingEngine` no tenía `exchange`; luego `ERROR collecting tests/test_grids_api.py` por `IndentationError` tras editar el test. Ambos se corrigieron antes de la corrida verde.
- Scanner: `$env:ASPLE_OFFLINE='1'; .\venv\Scripts\python.exe -m pytest tests/test_frontend_scanner.py -p no:cacheprovider -q --basetemp=.pytest-18d2-scanner` → `22 passed in 9.58s`.
- Playwright: `$env:ASPLE_OFFLINE='1'; .\venv\Scripts\python.exe -m pytest tests/ui/test_grids_browser.py -k scanner -p no:cacheprovider -q --basetemp=.pytest-18d2-ui` → `10 passed, 22 deselected, 2 warnings in 31.82s`.
- Mutación y sintaxis se ejecutaron en una sola invocación; la aserción falló por botón habilitado y el archivo se restauró byte a byte.
- Archivos de esta fase comprobados UTF-8 sin BOM y sin CR. `git diff --cached --quiet` devolvió 0; sin stage, commit ni push.
- **NO VERIFICADO:** llamadas reales a Binance/Testnet, precios reales y apertura real. Sin suite completa.

- Nota de límite: para el 422 de libro vacío se añadió al mensaje el rango solicitado después de completar las seis invocaciones pytest permitidas en este ciclo; esa ampliación textual no tuvo una corrida adicional.
