# Fase 18B-6 — Informe

## Cambios

- `api/routes/grid_structure.py:62-89,107-158`: las variantes y la estructura editada usan `cell_ok` y margen bruto tras comisiones frente al mínimo configurado en servidor. La selección `balanced`/`dense`/`wide` de este endpoint busca niveles con ese mismo criterio; polvo, neto estimado y su aviso siguen en la respuesta informativa. `margin_target_met` se conserva por compatibilidad y ahora compara margen bruto.
- `api/routes/grid_structure.py:183-207,209-245,259-277`: se toma `grid_min_margin_after_fees_pct` (con compatibilidad al alias anterior y valor de reserva 0.7) y se expone `minimum_margin_after_fees_pct`. Si el cliente no envía un objetivo, el servidor usa ese mínimo como objetivo por defecto.
- `api/routes/grids.py:28-49,237-238`: el guard permite apertura si la celda cumple el mínimo y el margen bruto llega al mínimo; neto/polvo permanecen expuestos y el aviso no bloquea. El rechazo ya no atribuye el bloqueo al neto por polvo.
- `grid/auto_open.py:82-113`: retirado el bloqueo por neto no positivo; los eventos conservan margen, neto, polvo, booleano de aviso y texto informativo.
- `frontend/scanner.js:75,78,82,92-99,123,153-157,182,184`: el objetivo inicial viene del mínimo del servidor, y al bajarlo se muestra la nota requerida. El neto y el polvo se muestran como información; el diálogo solo atribuye una denegación a las razones reales del guard.
- `tests/test_grid_structure_preview_api.py:32-43,47-111`, `tests/test_grids_api.py:338-385`, `tests/test_grid_auto_open.py:120-134`, `tests/test_frontend_scanner.py:112,314-319`, `tests/ui/test_grids_browser.py:169-185`: fixtures, regresiones de comportamiento y expectativa del mínimo servidor.

## Caso → prueba

| Caso | Prueba / evidencia |
|---|---|
| ADA sintético: bruto 0.742 %, neto 0.282 %, objetivo 0.29 %, mínimo 0.7 % | `tests/test_grid_structure_preview_api.py::test_structure_viability_uses_gross_edge_target_and_exchange_cell_floor`: viable, objetivo cumplido y polvo alto avisado. |
| Bruto 0.50 % < mínimo 0.7 % | Misma prueba: no viable y motivo nuevo de margen tras comisiones. |
| Celda bajo el mínimo de exchange | Misma prueba: no viable por celda. |
| Guardia de apertura con neto no positivo | `tests/test_grids_api.py::test_margin_guard_blocks_only_cell_floor_or_gross_threshold_and_reports_dust` y `test_open_dry_run_and_fake_execution_allow_negative_dust_net_when_gross_passes`: guard y ejecución contra exchange fake permitidos, aviso conservado. |
| Auto-open con neto cero | `tests/test_grid_auto_open.py::test_auto_open_allows_nonpositive_estimated_net_and_records_dust_warning`: crea grid con el fake y persiste neto, estimación y aviso. |
| Valor por defecto del mínimo servidor y objetivo inferior | `tests/test_frontend_scanner.py::test_node_margin_target_uses_server_default_and_warns_when_lowered`; además Playwright Scanner comprueba el campo con el mínimo servido. |
| Mensajes antiguos de polvo como bloqueador | Comprobado en la prueba de viabilidad editada. |

## Verificación ejecutada

Comando enfocado final:

```powershell
$env:ASPLE_OFFLINE='1'; .\venv\Scripts\python.exe -m pytest tests/test_grid_structure_preview_api.py tests/test_grids_api.py tests/test_grid_auto_open.py tests/test_frontend_scanner.py -p no:cacheprovider --basetemp .pytest-18b6-focused2 -q
```

Resultado exacto: `47 passed in 8.97s`.

Playwright Scanner:

```powershell
$env:ASPLE_OFFLINE='1'; .\venv\Scripts\python.exe -m pytest tests/ui/test_grids_browser.py -k scanner -p no:cacheprovider --basetemp .pytest-18b6-playwright -q
```

Resultado exacto: `6 passed, 21 deselected, 2 warnings in 16.92s`. Las advertencias fueron deprecaciones de `websockets`; Playwright registró hosts externos bloqueados.

`node --check frontend/scanner.js`: salida vacía, código 0.

Búsqueda de archivos relacionados: `rg -l "grid_structure|structure-preview" tests` devolvió `tests/test_frontend_scanner.py`, `tests/test_grid_structure_preview_api.py`, `tests/ui/conftest.py` y `tests/ui/test_grids_browser.py`. Los tests unitarios requeridos corrieron en el comando enfocado; el navegador corrió con `-k scanner`.

## Mutaciones temporales

| Mutación | Resultado | Restauración |
|---|---|---|
| Viabilidad y objetivo vuelven a medir neto tras polvo | Comando: `$env:ASPLE_OFFLINE='1'; .\venv\Scripts\python.exe -m pytest tests/test_grid_structure_preview_api.py::test_structure_viability_uses_gross_edge_target_and_exchange_cell_floor -p no:cacheprovider --basetemp .pytest-18b6-mut-a -q`. Muere en `assert result["feasible"] is True` (exit 1). | Restauración del archivo y comparación byte a byte: `BYTE_IDENTICAL=True`. |
| Se quita el mínimo de margen bruto | El mismo comando y test con `--basetemp .pytest-18b6-mut-b`. Muere: el caso de 0.50 % pasa y falla `assert low_margin["feasible"] is False` (exit 1). | Restauración del archivo y comparación byte a byte: `BYTE_IDENTICAL=True`. |

Ejecuciones rojas corregidas (también cuentan dentro del total de siete invocaciones pytest):

- `$env:ASPLE_OFFLINE='1'; .\venv\Scripts\python.exe -m pytest tests/test_grid_structure_preview_api.py tests/test_grids_api.py tests/test_grid_auto_open.py tests/test_frontend_scanner.py -p no:cacheprovider --basetemp .pytest-18b6-focused -q` → `2 failed, 45 passed in 9.32s` (expectativas antiguas de neto/niveles y filtro stepSize fake).
- Mismo comando con `--basetemp .pytest-18b6-focused2` → `1 failed, 46 passed in 9.28s` (se esperaba un número de ciclos con neto estimado negativo).
- El Playwright Scanner con el comando indicado arriba → `1 failed, 5 passed, 21 deselected, 2 warnings in 24.01s` (expectativa antigua 0.60 % derivada del neto).

Se actualizaron esas expectativas dentro del alcance; sus repeticiones quedaron verdes.

## Límites y no verificado

- No se ejecutó la suite completa, tal como limita el prompt. No se llamó a Binance ni a Testnet, no se leyó `.env` y la apertura solo se probó con el exchange fake. **NO VERIFICADO: apertura real en Testnet.**
- `grid/engine.py`, `grid/monitor.py` y `grid/policy.py` no tienen diff ni se editaron. No se stageó ni se hizo commit/push.
