# Fase 18C ? Inter?s compuesto en grids Simple

## Cambios y trazabilidad

| Punto | Archivo:l?nea | Evidencia |
|---|---|---|
| Simple acepta ?nicamente plazo y claves de compuesto, validadas por `validate_params`; Simple conserva valores por defecto cuando el compuesto est? ausente/desactivado. | `grid/engine.py:291-307` | API de creaci?n y ledger por celda. |
| El motor aplica el c?lculo de compuesto tambi?n a Simple; se retir? solo la comparaci?n de estrategia de la guarda de ciclo. | `grid/engine.py:1163-1168` | Test de ciclo Simple y mutaci?n. |
| Acci?n `params` acepta y persiste las tres claves compuestas en Simple y Smart, manteniendo dry-run, confirmaci?n, validaci?n y auditor?a existentes. | `grid/control_service.py:204-229,242-247`; `api/routes/grid_control.py:63-72` | Preview, confirmaci?n, `StrictBool`, rango y claves desconocidas. |
| Apertura Simple acepta esos par?metros despu?s de la validaci?n com?n. | `api/routes/grids.py:150-158` | Dry-run y apertura fake guardan los par?metros. |
| Detalle expone ratio y tope vigentes. | `grid/status_view.py:274-282` | El modal usa estos valores. |
| Scanner agrega opci?n apagada por defecto, porcentajes, descripci?n de USDT libre/ciclos futuros/estado Testnet, y env?a par?metros en ambas llamadas de apertura. | `frontend/scanner.js:75,122,147,157` | Playwright verifica cuerpo y resumen. |
| Detalle a?ade modal separado para compound, flujo preview/confirm, valores actuales y nota de capital no editable. | `frontend/grids.js:86-101,112-123,292,405-408` | Playwright verifica valores, plan, payloads y resultado. |
| Se aclara que el esperado por ciclo suma las celdas; equity vac?o indica ?Sin datos a?n?, el detalle t?cnico pasa al tooltip y el vac?o diario no tiene estilo de enlace. | `frontend/grids.js:231,234,342,354,360-361` | Etiqueta basada en `cells_view`; tooltip usa `equity.note`. |
| Pruebas de motor, APIs y UI. | `tests/test_grid_compound_engine.py:126,148-171,225`; `tests/test_grid_control_api.py:163`; `tests/test_grids_api.py:252`; `tests/ui/test_grids_browser.py:189,571` | Positivo Simple, disabled/absent, saldo insuficiente, cap, abrir/configurar. |

### Diff completo de `grid/engine.py`

```diff
@@ create_grid, validación de params Simple @@
-                if "max_days" not in params or any(
-                        key not in {"max_days", *DEFAULT_SMART_PARAMS}
-                        or (key in DEFAULT_SMART_PARAMS and value != DEFAULT_SMART_PARAMS[key])
-                        for key, value in params.items()):
-                    raise GridConfigError("simple grids only support the max_days parameter")
+                simple_keys = {"max_days", "compound_enabled", "compound_ratio",
+                               "compound_max_growth_pct", *DEFAULT_SMART_PARAMS}
+                if any(
+                        key not in simple_keys
+                        or (key in DEFAULT_SMART_PARAMS and key not in {
+                            "compound_enabled", "compound_ratio", "compound_max_growth_pct"
+                        } and value != DEFAULT_SMART_PARAMS[key])
+                        for key, value in params.items()):
+                    raise GridConfigError(
+                        "simple grids only support max_days and compound parameters"
+                    )
                 try:
-                    effective_params = {"max_days": validate_params(params, n_levels)["max_days"]}
+                    validated_params = validate_params(params, n_levels)
                 except ValueError as exc:
                     raise GridConfigError(str(exc)) from exc
+                effective_params = {
+                    key: validated_params[key]
+                    for key in params
+                    if key in {"max_days", "compound_enabled", "compound_ratio", "compound_max_growth_pct"}
+                }
@@ compound gate @@
-            str(grid.get("strategy", "simple")).lower() == "smart"
-            and params.get("compound_enabled", False) is True
+            params.get("compound_enabled", False) is True
```

La extensión en `create_grid` fue necesaria: el test de apertura mostró que esa validación rechazaba el compound Simple antes de que pudiera persistirse. No se cambió `grid/policy.py` porque ya valida booleano, ratio `(0, 1]` y tope positivo. `grid/monitor.py` queda sin diff.

## Verificaciones

| Comando / caso | Resultado |
|---|---|
| `$env:ASPLE_OFFLINE='1'; .\venv\Scripts\python.exe -m pytest tests/test_grid_compound_pure.py tests/test_grid_compound_engine.py -p no:cacheprovider -q --basetemp=.pytest-18c-engine3` | `30 passed in 1.60s` (tras ampliar los casos de cap/saldo a Simple). |
| Primera ejecución de esos archivos antes de ampliar validación de `create_grid` | `25 passed, 3 failed in 1.84s`; los tres fallos mostraron el rechazo prematuro de params Simple. Corregido y repetido. |
| `$env:ASPLE_OFFLINE='1'; .\venv\Scripts\python.exe -m pytest tests/test_grid_engine.py -k simple -p no:cacheprovider -q --basetemp=.pytest-18c-engine-simple` | `41 deselected, 1 warning`; no hubo tests seleccionados por ese filtro. |
| `$env:ASPLE_OFFLINE='1'; .\venv\Scripts\python.exe -m pytest tests/test_grid_control_api.py -k params tests/test_grid_ctl_compound.py -p no:cacheprovider -q --basetemp=.pytest-18c-control` | `4 passed, 14 deselected in 1.10s`. |
| `$env:ASPLE_OFFLINE='1'; .\venv\Scripts\python.exe -m pytest tests/test_grids_api.py -k open -p no:cacheprovider -q --basetemp=.pytest-18c-open-api` | `4 passed, 12 deselected in 1.31s`. |
| `$env:ASPLE_OFFLINE='1'; .\venv\Scripts\python.exe -m pytest tests/test_frontend_scanner.py -p no:cacheprovider -q --basetemp=.pytest-18c-scanner-unit` | `22 passed in 7.65s`. |
| `$env:ASPLE_OFFLINE='1'; .\venv\Scripts\python.exe -m pytest tests/ui/test_grids_browser.py -k "compound or detail" -p no:cacheprovider -q --basetemp=.pytest-18c-ui3` | `2 passed, 26 deselected in 8.29s`; Playwright reported no UI defects. |
| `node --check frontend/scanner.js` / `node --check frontend/grids.js` | Ambos exit code `0`. |
| Mutation: volver a poner la guarda Smart y rechazar compound Simple en API; ejecutar ambos tests afectados en una sola corrida. | `2 failed in 1.08s` como se esperaba: `test_simple_grid_compounds_profit_and_increases_next_buy_quantity` y `test_params_simple_grid_accepts_compound_and_rejects_other_smart_keys`. `pytest_exit=1`; restauraci?n byte exacta de ambos archivos: `True` (**2/2 mutaciones detectadas**). |

## No verificado

`tests/test_grid_compound_live.py` y cualquier interacci?n real con Binance/Testnet no se ejecutaron: el archivo est? marcado `live` y usa ?rdenes reales. Tampoco se ejecut? la suite offline completa; la validaci?n fue enfocada. El filtro `tests/test_grid_engine.py -k simple` no seleccion? tests, as? que no cuenta como cobertura. No se hizo stage, commit ni push.

Chequeo adicional: `git diff --check` devolvió exit 1 solo por una línea en blanco al final de `tests/ui/test_grids_ui.py:28`, archivo ajeno a esta fase y preexistente; no se modificó.
