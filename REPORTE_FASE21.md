# Fase 21 - avance y cierre verificable

Base: `20d83e9`. No se hizo stage, commit ni push. El árbol ya contenía cambios de Fase 21 al retomar; se preservaron. `git diff --name-only -- data models/saved` no mostró cambios en esas rutas. No se inspeccionaron ni editaron directamente; las pruebas existentes pudieron acceder a fixtures, según la aclaración de Ramón. No se abrió `.env`.

## Estado por ítem

| ítem | Estado | Evidencia y límite |
|---|---|---|
| A1 | HECHO | Regla `30·H` en `api/routes/volatility.py:329`. |
| A2 | PARCIAL | Se quitó la asignación muerta en `grid/monitor.py`; falta la prueba de equivalencia PAUSE/ADJUST. La revisión del horizonte en `api/routes/grids.py:285` queda NO VERIFICADA. |
| A3 | PARCIAL | `frontend/app.js:179,182,184` usa "Puntaje del modelo"; se corrigieron expectativas en `tests/ui/test_frontend_format.py:136` y `tests/ui/test_grids_browser.py:965`. Battle aún no aclara que no es probabilidad calibrada. |
| A4 | HECHO | Modelos aclara error en log y que no es varianza/cobertura de precio: `frontend/models.js:140`; UI actualizada en `tests/ui/test_grids_browser.py:249`. |
| A5 | HECHO | Confianza por dispersión no validada: `frontend/app.js:326`, `frontend/models.js:140,153`. |
| A6 | HECHO | Advisor aclara condición de 1,9 %, ADJUST y su bloqueo: `frontend/app.js:284`. |
| A7 | HECHO | Nota sobre reajuste final de A/C: `frontend/models.js:140`. |
| B1 | HECHO | Varianza y QLIKE en `models/volatility/model_stats.py:56-68`; API/UI en `api/routes/volatility.py:317-319`, `frontend/models.js:140`. Prueba manual, predicción perfecta y mutación ejecutadas. |
| B2 | HECHO | Columna/migración nullable en `database/db_manager.py:65,320-321`; escritura/agrupación en `:643-658,816-825`; API en `api/routes/volatility.py:334-336`. SQLite temporal y mutación ejecutadas. |
| B3 | HECHO | Falta CSV 5m produce error claro: `scripts/train_vol_models.py:99`; manifest registra `used_intraday`: `:150`. Pruebas sintéticas y mutación ejecutadas. |
| B4 | NO HECHO | No se añadieron sigma realizada 30d, campeón 24h y campeón 4h al Advisor/dry-run; no se verificaron procedencia y ventanas equivalentes. |
| B5 | HECHO | Advisor reutiliza `minimum_effective_verifications(24)`: `api/routes/grid_advisor.py:176-184`. Prueba H=24 con 400/720 y mutación ejecutadas. |
| B6 | NO HECHO | `_model_stats_rows` obtiene filas acotadas y de dispersión, pero conserva las de dispersión para estadísticas/forward (`api/routes/volatility.py:50-56,298-310`). No cambió la selección sin equivalencia de métricas adaptativas/forward. Sin medición comparable; NO VERIFICADO. Advisor solicita H=24 (`api/routes/grid_advisor.py:169-172`). |
| C1 | NO HECHO | No se incorporaron `verify_delay_h`, `unverifiable_late` ni precio de vela en `verify_at`; requiere coordinación con migración, precisión, kill status, cobertura y Battle. |
| C2 | NO HECHO | No se creó `scripts/rescore_direction.py`; no se hizo dry-run ni `--apply`. |
| D1 | HECHO | `conclusive` exige n suficiente, préstamos creados y CI que excluya cero: `api/routes/grids.py:36-74`; prueba y mutación ejecutadas. |
| D2 | HECHO | Piso exacto de notional y préstamo parcial: `grid/loans.py:186-197`; default 0,2: `grid/policy.py:43`. Pruebas y mutación ejecutadas. |
| D3 | NO HECHO | No se aplicó el piso funcional Smart 1,3x en validadores, API, CLI y auto-open. |
| D4 | NO HECHO | No se implementó `plan_adjust_with_shrink` en monitor y simulador. |
| E1 | NO HECHO | No se cambió `test_is_virgin`; no se comprobó si hay rangos de entrenamiento/TEST suficientes en el manifest. |
| E2 | NO HECHO | No se unificó ni eliminó la tabla de precisión por condición; falta comprobar lectores y equivalencia. |
| E3 | NO HECHO | No se adaptó la simulación al capital/n recomendados; se dejó pendiente porque la integración puede exceder unas 40 líneas y altera el contrato del simulador. |
| E4 | NO HECHO | No se añadió prueba multiproceso Windows de la rama `msvcrt`. |
| F1 | NO HECHO | No se añadió pausa sombra 4h/24h ni endpoint; no se modificaron decisiones Smart. |

## Pruebas

Pruebas dirigidas: `95 passed, 3 warnings in 3.23s`.

Primera pasada no-UI: `1 failed, 988 passed, 1 skipped, 18 deselected, 38 warnings in 63.33s`; única falla: `tests/test_grid_policy.py::test_defaults_and_parameter_validation_rules` esperaba el default viejo 1,0. Se cambió la expectativa al default aprobado 0,2 y la prueba aislada pasó.

No-UI final: `989 passed, 1 skipped, 18 deselected, 38 warnings in 62.37s`.

Primera pasada UI: `3 failed, 103 passed, 2 warnings in 128.11s`; eran expectativas antiguas para las etiquetas A3/A4. Se actualizaron solo esas expectativas; los tres tests dirigidos pasaron (`3 passed, 2 warnings in 2.43s`). UI final: `106 passed, 2 warnings in 117.57s`. Ambas suites completas usaron `ASPLE_OFFLINE=1`, `-m "not live"` y `--basetemp` bajo `$env:TEMP`. Test UI focal tras corregir la tilde de una cadena de fixture: `1 passed in 0.09s`.

Mutaciones `str.replace` con SHA antes/mutado/restaurado; todas fallaron por aserción y restauraron el hash original:

| Mutación | Resultado | SHA original/restaurado | SHA mutado |
|---|---|---|---|
| B1: `exp(2y)` a `exp(y)` | 1 failed | `e4330a129f7b47153e6e59bc6e1b07c31d18553846f7d319ebc47cfb61cc90bc` | `24a78610744ccbcb1d036c2ce571748c3af138d8de119dfa6f05c9a4df0d5aa1` |
| B2: omitir `artifact_version` al guardar | 1 failed | `460ef8a824eb63048e07c7c68a896673c175074d8038ec5266db6d5efcf8a616` | `bc58b3ecc6e2b048579f40e00b39e3c9044c8b786be267c889e57e42154d57d2` |
| B3: forzar `used_intraday=false` | 1 failed | `f6a1f00d6ee2f8a207f6752bd965d271b427d8e4dff606314a0fa779995b1ba5` | `ec9916ec155099cf8dc9a72eefbca3b911b03af98c1862fb4f73d15f49e03b46` |
| B5: umbral fijo 30 | 1 failed | `33d901c415d0507b9b14be280d73a5ad01b31e7d123e21a844a52f6615e0b961` | `791c5197bad64468a07cec63769ef6a297619c8491aea2acadea4f367284bde0` |
| D1: ignorar préstamos creados | 1 failed | `af0f037e8df39aded4ed37784b209dafa5daa9f4832cf6c5e9c159ae94a0033e` | `a1777b0d1c47b842cb18c5aba96220f4867e30e55d1eb2480dda6a054860f86c` |
| D2: `ROUND_CEILING` a `ROUND_FLOOR` | 4 failed | `4974d0325b134272b32ef6ccf792bbbd99c9d6576f9c68e0520cbb25a956259b` | `3e9c48185b4a9a2d3e2f023db1dd2b434c4edc0f7cccdcc0712724c0a9f0768d` |

B3 validó también el manifest con velas sintéticas (`tests/test_volatility_live.py:90`). Otras pruebas clave: Advisor H=24 (`tests/test_grid_advisor.py:157-187`), artefacto/API (`tests/test_vol_model_stats.py:408-438`), cohorte sin préstamo (`tests/test_grid_loan_summary_comparison.py:104-108`), préstamo y filtros (`tests/test_grid_loans_pure.py:107-152`), default actualizado (`tests/test_grid_policy.py:35-58`). Mutaciones A1-A7, C1/C2 y D3/D4/E/F no ejecutadas.

## Auditoría de alcance

Escaneo final: 23 archivos de texto cambiados; UTF-8 sin BOM, LF, sin U+FFFD ni secuencias de letra, signo de interrogación y letra minúscula. El índice está vacío.


Archivos modificados: `api/routes/grid_advisor.py`, `api/routes/grids.py`, `api/routes/volatility.py`, `database/db_manager.py`, `frontend/app.js`, `frontend/grids.js`, `frontend/models.js`, `grid/loans.py`, `grid/monitor.py`, `grid/policy.py`, `models/volatility/live.py`, `models/volatility/model_stats.py`, `scripts/train_vol_models.py`, y pruebas dirigidas/UI (`tests/test_grid_advisor.py`, `tests/test_grid_loan_summary_comparison.py`, `tests/test_grid_loans_pure.py`, `tests/test_grid_policy.py`, `tests/test_vol_model_stats.py`, `tests/test_vol_training_pipeline.py`, `tests/test_volatility_live.py`, `tests/ui/test_frontend_format.py`, `tests/ui/test_grids_browser.py`), más este reporte. `data/` y `models/saved/` no aparecen en el diff; `grid/engine.py` no se modificó. La corrección solicitada en `scripts/train_vol_models.py:99` conserva la tilde de "entrenará". índice vacío; no stage/commit/push.

## Archivos para commits separados

- A: `api/routes/volatility.py`, `frontend/app.js`, `frontend/models.js`, `grid/monitor.py`, `tests/test_grid_advisor.py`, `tests/test_vol_model_stats.py`, `tests/ui/test_frontend_format.py`, `tests/ui/test_grids_browser.py`. A2 parcial y A3 parcial.
- B: `api/routes/grid_advisor.py`, `api/routes/volatility.py`, `database/db_manager.py`, `models/volatility/live.py`, `models/volatility/model_stats.py`, `scripts/train_vol_models.py`, `tests/test_grid_advisor.py`, `tests/test_vol_model_stats.py`, `tests/test_vol_training_pipeline.py`, `tests/test_volatility_live.py`. B4/B6 pendientes.
- C: sin archivos implementados; C1/C2 pendientes.
- D: `api/routes/grids.py`, `frontend/grids.js`, `grid/loans.py`, `grid/policy.py`, `tests/test_grid_loan_summary_comparison.py`, `tests/test_grid_loans_pure.py`, `tests/test_grid_policy.py`. D3/D4 pendientes.
- E: sin archivos implementados; E1-E4 pendientes.
- F: sin archivos implementados; F1 pendiente.

## Decisiones pendientes de Ramón

Confirmar si mantiene el default `loan_min_amount=0,2`, fijar el multiplicador funcional Smart en 1,3 y activar `adjust_shrink_n` por defecto cuando se implementen D3/D4. Quedan pendientes B4/B6, C1/C2, D3/D4, E1-E4 y F1. No se alteró Smart por defecto ni la lógica de pausa.

## Efectos visibles

B1 agrega QLIKE y razón de varianza a Modelos; D1 muestra por qué una cohorte no es concluyente; D2 permite préstamo parcial sin bajar al prestamista del mínimo de Binance. C1/C2 no cambian todavía Battle ni sus verificaciones; D3 no cambia el número de niveles de grids Smart nuevos. Las migraciones aditivas se aplican al arrancar la aplicación.
