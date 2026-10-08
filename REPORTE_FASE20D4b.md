# Fase 20D-4b — Cohortes de préstamos

## Cambios

| Punto | Resultado | Evidencia |
|---|---|---|
| C1 | HECHO. La asignación compartida se usa desde API, CLI y auto-open; solo modifica Smart. Auto-open transmite también `loans_group`, `loans_enabled` y el tope de 70 % cuando corresponde. | `grid/loan_cohorts.py:18-47,52-64`; `api/routes/grids.py:376`; `scripts/grid_ctl.py:328`; `grid/auto_open.py:128` |
| C2 | HECHO. Elegí validar `loans_group` como metadato en `validate_params`; el motor persiste el grupo dentro de la creación, sin `merge_grid_params` posterior ni ventana de fallo parcial. | `grid/policy.py:118-125`; `api/routes/grids.py:376-381`; persistencia comprobada en `tests/test_grids_api.py:395-416` |
| C3 | HECHO dentro del proceso. `LOAN_COHORT_LOCK` serializa el conteo y la creación para API, CLI y auto-open. No coordina procesos distintos. | `grid/loan_cohorts.py:10,52-64`; prueba concurrente `tests/test_grids_api.py:489-513` |
| C4 | HECHO. API entrega porcentajes por grid y cohorte; días por grid tienen mínimo de 1. El resumen UI enseña porcentaje acumulado por capital junto al P&L absoluto y conserva la nota existente. | `api/routes/grids.py:404-405,437-450,459-469`; `frontend/grids.js:212` |
| Ranking Scanner | HECHO. Prueba de dos símbolos: cambia la holgura, con peso cero no afecta el resultado; el orden sigue la puntuación de otros componentes. | `tests/test_grid_scanner.py:169-197` |

C2 usa una clave de metadatos validada, no una excepción para saltarse la política. En C3 el candado de módulo evita duplicar posiciones al abrir desde distintos puntos de entrada del mismo proceso; varios procesos requerirían coordinación compartida que este cambio no implementa.

## Pruebas

| Comprobación | Resultado |
|---|---|
| Dirigidas no-live: auto-open, API de grids, resumen de grids, Scanner y settings | `81 passed in 5.04s` |
| UI dirigida: resumen y acción de préstamos | `1 passed, 2 warnings in 1.49s` |
| Ranking Scanner tras reforzar las aserciones de componentes | `1 passed in 0.69s` |
| Mutación C3: reemplazar el candado por `if True` | Falló por comportamiento: esperado reparto `[2, 6]`; mutado `[0, 8]`. SHA-256 antes/restaurado `4cb85f7c0d59811fb8cf5cb8ef9351aee99f98409c51d6faa9c8a15304791187`; mutado `411507616c697460b62acec0248198a163ab347019421dcbd98b669728db8ddc`. Restauración coincide. |
| Mutación C1: desviar la llamada compartida en auto-open | Falló por comportamiento: se esperaba abrir 3; el mutado abrió 0. SHA-256 antes/restaurado `fda060c181d84d721e2637affae5e484187d4cc5b6611143754fabbc19b3b556`; mutado `32745335a19f74aff775ddbd3f9b8080fb4098f87d708986b6224b7a665f94f8`. Restauración coincide. |
| Suite no-UI final, con `ASPLE_OFFLINE=1` | `947 passed, 1 skipped, 18 deselected, 38 warnings in 65.96s (0:01:05)` |
| Suite UI final, con `ASPLE_OFFLINE=1` | `103 passed, 2 warnings in 73.93s (0:01:13)`; Playwright no observó defectos. |

Comandos finales:

```powershell
$env:ASPLE_OFFLINE='1'; .\venv\Scripts\python.exe -m pytest -m "not live" --ignore=tests/ui -q --basetemp $env:TEMP\pytest-20d4b-final-python
$env:ASPLE_OFFLINE='1'; .\venv\Scripts\python.exe -m pytest tests/ui -m "not live" -q --basetemp $env:TEMP\pytest-20d4b-final-ui
```

## Pendiente de verificación

No se probó coordinación entre procesos distintos; el candado es de `threading` y su alcance es un único proceso. Las pruebas excluyeron las marcadas `live`; no se abrió una conexión real a Testnet. No se hizo stage, commit ni push.
