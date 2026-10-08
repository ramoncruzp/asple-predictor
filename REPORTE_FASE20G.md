# Fase 20G — cierre G1, G2 y G3

Base: `f08dc1c` (20F commiteada; árbol limpio al preflight). Trabajo offline: sin red, `.env`, `models/saved/` ni escrituras a la base real. No se cambió ningún default ni archivo del motor.

## Estado por punto

| Punto | Estado | Cambios y evidencia |
|---|---|---|
| G1 | HECHO | `scripts/diag_20f_multiwindow.py:74-106,108-128,138-172`: ventanas 5m no solapadas, sigma24 = std muestral de diff(log(cierre_1h)) x sqrt(24), con 720 horas previas; replay con EWMA interna de las velas 5m y `run_simulation` existente y estadísticas pareadas. `sl=none` usa `stop_loss_pct=1,000,000` como centinela, no como interruptor semántico (`:30-40`; documentado en `diag_20e1e_smart_vs_simple.py:218,345,363`). La salida se escribe solo en `--out` (`:174-193`). Pruebas: `tests/test_diag_20f_multiwindow.py:28-81`. |
| G2 | HECHO | `api/routes/grids.py:36-73,447-491,518-535`: P&L realizado / capital / días abiertos x 100 por grid; excluye y cuenta P&L desconocido, capital no válido y duración no positiva. IC percentil del 95 % con 2.000 remuestreos y semillas fijas. Agrega comparaciones sin alterar los campos existentes. `frontend/grids.js:214-226,239` muestra diferencia, IC, n, estado, muestra pequeña y nota observacional sin color de éxito/fracaso. API: `tests/test_grid_loan_summary_comparison.py:29-85`; UI: `tests/ui/test_grids_browser.py:69-122`. |
| G3 | HECHO | `grid/loan_cohorts.py:24-81,130-145`: lock del sistema operativo en `%TEMP%`, nombre derivado de SHA-256 de `str(db.engine.url)` (fijo si no hay URL), timeout de 30 s; cubre conteo y creación junto al lock de hilo. Si falla abrir/crear el archivo, registra WARNING y degrada al lock de hilo. No coordina otras máquinas ni URLs de BD distintas. Pruebas: `tests/test_loan_cohorts_process.py:38-101`. |

G1 se probó con CSV sintéticos en `tmp_path`. **NO VERIFICADO**: resultados sobre los CSV reales de `data/cache/`; no ejecuté el análisis con datos de una moneda real. El script no usa red ni BD.

G2 es descriptivo, observacional y no aleatorizado: no ajusta por moneda ni tamaño de celda; el control se asigna cada tercer grid. `conclusive` solo es verdadero si ambos n >= 2 y el IC no incluye cero. `small_sample` informa cuando algún n < 10. No es evidencia causal ni recomendación de trading.

G3 se verificó en Windows con ocho procesos y SQLite temporal. La garantía se limita a procesos de esta máquina que compartan el directorio temporal y la URL de BD. Si no se puede abrir el archivo, solo queda el lock local y se registra el aviso. La rama POSIX con `fcntl.flock` no fue ejecutada en este entorno Windows.

## Pruebas y mutaciones

Suites base antes de editar: no-UI `968 passed, 1 skipped, 18 deselected, 38 warnings`; UI `106 passed, 2 warnings`.

Rojo/verde enfocado:

- G1: antes de crear el módulo, la primera recolección dio `ModuleNotFoundError: No module named 'scripts.diag_20f_multiwindow'`. Después, G1 dio `5 passed`; la mutación conductual falló en la aserción de desviación muestral.
- G2: antes de implementar la respuesta, las cuatro pruebas API fallaron con `KeyError: 'cohort_comparisons'`. Luego pasaron API y UI; corrida enfocada conjunta G1/G2/G3 y `tests/test_grids_api.py`: `47 passed`.
- G3: al omitir el lock de archivo, la prueba multiproceso falló: `groups.count("control")` fue 0 y esperaba 2. Con lock, la corrida enfocada conjunta dio `47 passed`; prueba UI enfocada `1 passed, 2 warnings`.

Mutaciones con `str.replace`; SHA-256 antes, mutado y restaurado:

| Punto y cambio | Antes | Mutado | Resultado | Restaurado |
|---|---|---|---|---|
| G1: `ddof=1` a `ddof=0` en `scripts/diag_20f_multiwindow.py` | `cd341a3da2588e637f7b8510bebd332e80790842b8db5c5eace25252a2aefd69` | `8fb7ac0fc5d58d9ac199cb48de0848cc969af2ae5aca3d017b2aaf2047755de1` | Falló: `sample_sd == 0.5`, obtenido `0.4330127018922193`. | `cd341a3da2588e637f7b8510bebd332e80790842b8db5c5eace25252a2aefd69` |
| G2: declarar concluyente sin consultar IC en `api/routes/grids.py` | `a545305b96ea6ba25920f905e8c9c96b37e0f4be60751b2979c528bce620cd15` | `101309ed70b56fcf4564c99f88ea3aea894a4a3f75d0a1b2ea5763097bb8a150` | Falló: el test del IC que incluye 0 esperaba `False` y obtuvo `True`. | `a545305b96ea6ba25920f905e8c9c96b37e0f4be60751b2979c528bce620cd15` |
| G3: omitir `_process_creation_lock` en `grid/loan_cohorts.py` | `dce8dbde7aaff4a110833ba6092d6cb0ac98be189e0aa7350a5006d21f00b05f` | `4701026ed4456141c4028e8fc044285f315b148907d9fc795a4a95a5e8bf0032` | Falló: reparto con 0 `control`; esperaba 2 de 8. | `dce8dbde7aaff4a110833ba6092d6cb0ac98be189e0aa7350a5006d21f00b05f` |

Suites finales, offline y en el orden solicitado:

```text
982 passed, 1 skipped, 18 deselected, 38 warnings in 156.90s (0:02:36)
106 passed, 2 warnings in 151.37s (0:02:31)
```

Comandos ejecutados: `venv/Scripts/python.exe -m pytest -m "not live" --ignore=tests/ui -q --basetemp "$env:TEMP/pytest-20g-final-no-ui"`; después `venv/Scripts/python.exe -m pytest tests/ui -m "not live" -q --basetemp "$env:TEMP/pytest-20g-final-ui"`.

Auditoría final: `git diff --check` sin errores. Los ocho archivos de código/pruebas y este reporte son UTF-8 sin BOM, CR, U+FFFD ni signos corruptos entre letras. La búsqueda `[A-Za-z]\?[a-z]` dio 0 coincidencias por archivo. No hice stage, commit ni push.

## Archivos para commits separados

- G1: `scripts/diag_20f_multiwindow.py`, `tests/test_diag_20f_multiwindow.py`.
- G2: `api/routes/grids.py`, `frontend/grids.js`, `tests/test_grid_loan_summary_comparison.py`, `tests/ui/test_grids_browser.py`.
- G3: `grid/loan_cohorts.py`, `tests/test_loan_cohorts_process.py`, `REPORTE_FASE20G.md` (cierre conjunto de G1-G3).

## Decisiones pendientes de Ramón

- Decidir si la evidencia de G1 justifica otro análisis. `sl=none` es el centinela de 1.000.000 %, no desactiva semánticamente el stop-loss.
- Revisar las comparaciones observacionales de G2 antes de decidir sobre cohortes; no son causales y no ajustan moneda ni tamaño de celda.
- 19C-2 sigue bloqueada sin autorización escrita. No cambié parámetros Smart, umbrales ni política de préstamos.
