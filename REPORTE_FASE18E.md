# Fase 18E ? Lista blanca del cuerpo de apertura

## Cambios

- `frontend/scanner.js:6-7,164,180`: define la lista blanca de `OpenRequest` y la aplica tanto al dry-run como a la confirmación. `margin_target_pct` y `spacing_pct` quedan disponibles para `/structure-preview`, pero no se envían a `/open`.
- `frontend/scanner.js:8-23`: errores de validación en lista se muestran como `campo: mensaje`; objetos se serializan con `JSON.stringify`, y las cadenas mantienen su texto.
- `tests/test_frontend_scanner.py:90,121,323-341`: harness Node con margen 0.7, contrato de claves para dry-run y confirmación, y error de validación como lista.
- `tests/test_grids_api.py:156-166`: confirma que `margin_target_pct` sigue rechazado por `OpenRequest` con `extra="forbid"`.
- No se modificó `OpenRequest` ni se relajó su validación.

## Casos y pruebas

| Caso | Prueba | Resultado |
|---|---|---|
| Whitelist Node con `margin_target_pct=0.7`; la estructura sí lo recibe | `test_node_open_body_is_whitelisted_even_when_margin_target_is_filled` | Pass en la suite; la mutación lo hizo fallar |
| Detalle de API como lista | `test_node_array_api_validation_details_are_human_readable` | Incluida en 24 passed |
| La API conserva el rechazo de campos extra | `test_open_still_rejects_margin_target_as_an_extra_field` | Incluida en 12 passed |
| Sintaxis JS | `node --check frontend/scanner.js` | exit 0 |

## Comandos y resultados exactos

- `$env:ASPLE_OFFLINE='1'; .\venv\Scripts\python.exe -m pytest tests/test_frontend_scanner.py -p no:cacheprovider -q --basetemp=.pytest-18e` → `24 passed in 8.56s`.
- `$env:ASPLE_OFFLINE='1'; .\venv\Scripts\python.exe -m pytest tests/test_grids_api.py -k "open or dry_run" -p no:cacheprovider -q --basetemp=.pytest-18e` → `12 passed, 11 deselected in 2.13s`.
- Mutación temporal: se reintrodujo `margin_target_pct` en el cuerpo de apertura y se ejecutó `pytest tests/test_frontend_scanner.py -k open_body_is_whitelisted -p no:cacheprovider -q --basetemp=.pytest-18e` → `1 failed, 23 deselected`; falló la inclusión de claves permitidas. `BYTE_EXACT_RESTORE True`.
- `node --check frontend/scanner.js` → exit 0.
- Se usó solo `.pytest-18e`; fue retirado tras las pruebas. No se ejecutó suite completa ni Playwright.

## Límites

- **NO VERIFICADO:** apertura real en Testnet.
- El contrato de cuerpo se comprueba con el harness Node y una whitelist explícita, más una prueba API que demuestra que `margin_target_pct` sigue siendo extra; no se envía directamente el resultado Node al cliente ASGI.
- Informes UTF-8 sin BOM y sin CR. No hubo stage, commit ni push.
