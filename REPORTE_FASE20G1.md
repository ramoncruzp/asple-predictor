# Fase 20G-1: ajustes de cohortes

Base: `f08dc1c`. Se conservaron los cambios previos de 20G; no hubo stage, commit ni push, red, lectura de `.env` ni acceso a `models/saved/`.

| Ajuste | Estado y evidencia |
|---|---|
| G2: denominador de días | HECHO. `api/routes/grids.py:483-489` mantiene la exclusión por P&L desconocido, capital no positivo, fechas ausentes y duración no positiva, pero calcula la muestra con `grid_days = max(1.0, actual_open_days)`. `:529` aclara que son días abiertos con mínimo de 1 día. Pruebas nuevas en `tests/test_grid_loan_summary_comparison.py:90-100`: 2 horas y +1 % producen 1,0 %/día; 3 días y +3 % producen 1,0 %/día. Los demás casos existentes siguieron pasando; ningún valor esperado se cambió. |
| G3: timeout del lock | HECHO. `grid/loan_cohorts.py:17-18` fija 120 s y documenta que debe superar el peor caso de una apertura con varias órdenes Testnet. `tests/test_loan_cohorts_process.py:72-86` comprueba el default >= 120 y reduce el timeout a 0,01 s para verificar `TimeoutError` y que no se cree el grid. No esperó 120 s ni medí una apertura real. |

## Rojo, verde y mutación

- G2 rojo: `test_two_hour_grid_uses_one_day_floor_for_cohort_metric` falló; cálculo observado `12.000000000000002`, esperado `1.0`. G3 rojo: el test del default falló con `AssertionError: assert 30.0 >= 120.0`.
- Verde enfocado: `11 passed in 1.83s` para `tests/test_grid_loan_summary_comparison.py` y `tests/test_loan_cohorts_process.py`.
- Mutación G2: volver a dividir por `actual_open_days` hizo fallar el caso de 2 h con `12.000000000000002` en vez de `1.0`.

| SHA G2 (`api/routes/grids.py`) | Valor |
|---|---|
| Antes | `44bf26733882868c3d19ab1189fa17ecbac24a735d524f548d989e9453be8fd6` |
| Mutado | `976347fa505e193c4ed7908c2de34328da4c764d18e01c72705159e325c23512` |
| Restaurado | `44bf26733882868c3d19ab1189fa17ecbac24a735d524f548d989e9453be8fd6` |

G3 no lleva mutación: al ser un ajuste de constante, se verific? el default y el `TimeoutError` con timeout reducido por `monkeypatch`, como indica el prompt.

## Suites finales

No-UI: `984 passed, 1 skipped, 18 deselected, 38 warnings in 174.88s (0:02:54)`.
UI: `106 passed, 2 warnings in 156.14s (0:02:36)`.

Comandos: `venv/Scripts/python.exe -m pytest -m "not live" --ignore=tests/ui -q --basetemp "$env:TEMP/pytest-20g1-final-no-ui"`; después `venv/Scripts/python.exe -m pytest tests/ui -m "not live" -q --basetemp "$env:TEMP/pytest-20g1-final-ui"`.

## Estado final

`git status --short`: los cuatro archivos modificados de 20G (`api/routes/grids.py`, `frontend/grids.js`, `grid/loan_cohorts.py`, `tests/ui/test_grids_browser.py`), los cinco archivos nuevos de 20G (`REPORTE_FASE20G.md`, `scripts/diag_20f_multiwindow.py`, `tests/test_diag_20f_multiwindow.py`, `tests/test_grid_loan_summary_comparison.py`, `tests/test_loan_cohorts_process.py`) y este reporte. índice sin stage. **NO VERIFICADO**: duración máxima de una apertura real en Testnet.
