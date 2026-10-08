# Fase 20F — Reauditoría y medición

Base: HEAD `81ef32e`; árbol limpio al inicio. La auditoría indicada `claude/Auditoria_Parte2_Inteligencia_v2.md` no está en este checkout. Sin stage, commit ni push; no se abrió `.env`, no se tocaron `models/saved/` y las pruebas usaron SQLite temporal. La consulta de impacto de F1 abrió la base existente mediante URI `mode=ro`.

## Estado por punto

| Punto | Estado | Cambio y evidencia |
|---|---|---|
| F1 | HECHO | Ventana `max(168, 30×H)` en `models/volatility/model_stats.py:137-165,202-217`; límite SQL aumentado en `database/db_manager.py:805-834`. Estados y `bias_alert` usan n/H en `model_stats.py:218-222,268-279`. SQLite temporal con 800 verificaciones confirma >=720 y consenso vivo; sintéticos verifican H24: 719 val / 720 vivo, H1=30 vivo y los valores de referencia H1/H2/H4. Agregados SQL y selección de campeones consultados no dependen del límite móvil. |
| F2 | HECHO | EWMA acepta `bars_per_hour` conservando 12 por defecto en `grid/sim/data.py:77-87`; el runner infiere 300 s → 12 y 3600 s → 1, o rechaza otro espaciado cuando no hay `sigma_values`, en `grid/sim/runner.py:127-135`. Serie sintética horaria recupera σ24 ≈1%; la ruta de respaldo horaria del Advisor coincide con su serie realizada. No se tocaron `vol_quality.py`, `structure_study.py` ni `vol_series.py`. |
| F3 | HECHO | Advisor calcula el riesgo principal con el horizonte de `DEFAULT_SMART_PARAMS` y conserva la comparación 24 h en `api/routes/grid_advisor.py:410-415,496-502`; UI lo rotula en `frontend/app.js:280-284`. API y UI prueban horizonte de 4 h; el caso moderado no entra en pausa y una banda estrecha de prueba sí cruza el umbral. |
| F4 | HECHO | Nuevas cohortes con préstamos se registran como `loans_v2`, top-up y tope de prestamista 70%, en `grid/loan_cohorts.py:39-48`; `validate_params` acepta el metadato en `grid/policy.py:124-125`. Resumen separa `loans` histórico y `loans_v2` en `api/routes/grids.py:407-418`; UI reconoce el grupo en `frontend/grids.js:306`. Pruebas cubren inercia del tope, top-up 70%, guard de celda de 10/50 USDT, préstamos parciales, cohorte y resumen separado. El guard de margen mínimo existente se conserva. |
| F5 | NO HECHO | El candado actual sigue siendo local al proceso (`grid/loan_cohorts.py:9,52-64`). La asignación atómica entre procesos requeriría acoplar conteo y creación a una transacción SQLite común en API, CLI y auto-open; eso rebasa el límite de ~40 líneas y puede requerir cambios en el motor/DB. |
| F6 | HECHO | `test_is_virgin` ahora depende de si ya existe el archivo de consenso, manteniendo XRP como no virgen: `scripts/vol_consensus_eval.py:36-37,333`. Test temporal cubre primera y segunda corrida sin reescribir artefactos reales. |
| F7 | HECHO | Manifest no-XRP cacheado por símbolo y firma `(mtime_ns, size)` bajo lock en `grid/volatility_provider.py:19-41,81-82`. Prueba cuenta una lectura, cambia el archivo y confirma invalidación. |
| F8 | HECHO | `load_vol_consensus` devuelve copias profundas para lecturas cacheadas y frescas en `config/models_config.py:45,67`. Prueba muta resultado anidado y confirma que la lectura posterior queda intacta. |
| F9 | HECHO | Mensajes mojibake reales corregidos: `tests/ui/test_frontend_format.py:18,53,72,98,122`, `data/volatility.py:140` y `scripts/vol_research.py:277`. `data/volatility.py` contiene mensajes normales, no una rutina de detectar/reparar mojibake. Prueba estética y test de formateadores pasan. |
| F10 | HECHO | Quitado el campo de fixture muerto de `tests/ui/test_models_page.py:19`. Añadida nota "obsoleta desde 20E-1" a `REPORTE_FASE20D1.md:80-81`, conservando el texto histórico. |
| G1 | NO HECHO | Script multi-ventana no creado: requiere más de ~40 líneas nuevas y cálculo pareado/lectura de CSV; el prompt pide dejarlo si supera el límite. |
| G2 | NO HECHO | Bootstrap por cohortes y exposición API/UI no implementados: requiere más de ~40 líneas nuevas y una decisión estadística adicional. |

## Impacto sobre datos actuales (consulta SQLite `mode=ro`)

Los conteos maduros por modelo son iguales para Persistence, EWMA, HAR, HAR_range, HAR_asym, GBM, NexoHAR y GARCH_t. Estado previo usa el umbral crudo n>=30; el nuevo usa n/H>=30. `source` también requiere al menos tres modelos mejores que Persistence.

| H | maduras/modelo | n/H efectivo de ventana | estado antes | estado después | elegibles vivos | source antes/después |
|---:|---:|---:|---|---|---|---|
| 1 | 54 | 53,0 | 8/8 activo | 8/8 activo | GBM, HAR_range (2; insuficientes para consenso vivo) | val / val |
| 2 | 53 | 26,0 | 8/8 activo | 0/8 activo | ninguno | val / val |
| 4 | 51 | 12,5 | 8/8 activo | 0/8 activo | ninguno | val / val |
| 24 | 42 | 1,8 | 8/8 activo | 0/8 activo | ninguno | val / val |

Para H=1,2,4 la ventana permanece en 168 filas y los pesos P/P2 sintéticos son idénticos. H=24 amplía la ventana a 720 filas y puede cambiar pesos EMA, como cambio esperado; hoy sigue en `val` por no tener 30 observaciones efectivas ni suficientes modelos elegibles.

## Pruebas y medición

Línea base reproducida antes de editar: no-UI `955 passed, 1 skipped, 18 deselected, 38 warnings`; UI `106 passed, 2 warnings`.

Pruebas dirigidas durante el desarrollo: F1 `19 passed, 2 warnings`; F2 inicial reprodujo 2 fallos y tras el cambio `61 passed, 3 warnings` en data/runner/Smart fidelity/Advisor; F3 reprodujo la diferencia de horizonte y pasó con `2 passed`; F4 `19 passed, 47 deselected`; F6 `1 passed`; F7/F8 `2 passed`; F9 `11 passed`. Las pruebas de UI se reservaron para la corrida final.

Resultados finales literales:

```text
967 passed, 1 skipped, 18 deselected, 38 warnings in 81.48s (0:01:21)
106 passed, 2 warnings in 124.09s (0:02:04)
```

Playwright registró bloqueos de `cdn.jsdelivr.net` y `fonts.googleapis.com`; no observó defectos de UI.

## Mutaciones

| Punto | SHA-256 antes | SHA-256 mutado | Resultado | SHA-256 restaurado |
|---|---|---|---|---|
| F1: ventana fija 168 | `da1bb1beaa86663a48cf49bb15d1941e6cd946e23f1331c63f193fd8eeb6361a` | `1ba5a3b14314dadd27cae9615bd2f6b6bc19e275b05a0603837b1d93f8fc6e05` | Falló el caso H24=720, que dejó de pasar a vivo | `da1bb1beaa86663a48cf49bb15d1941e6cd946e23f1331c63f193fd8eeb6361a` |
| F2: escala fija 288 | `99ec0a34bb56181ec4ec8a1fe5fa73506b67b15a17f7c1dc84f897887bc3a0cf` | `b51f5ba8ef8b32f02a58b1042f6224fed4a934fd1f2b9885e1e6ac6dab2ceca1` | Falló: σ sintética ≈0.0348 frente a 0.01 esperado | `99ec0a34bb56181ec4ec8a1fe5fa73506b67b15a17f7c1dc84f897887bc3a0cf` |
| F3: forzar horizonte 24 h | `251e33e6cb15fc8bfccc1eba4dc5c5dbd73d9182f2072a59471bbfc86495f688` | `eb8ef7478356e5886384afa2a7622afd4641ee9fe0390f5fe84e09df5b15ab50` | Falló aserción del riesgo con horizonte 4 h | `251e33e6cb15fc8bfccc1eba4dc5c5dbd73d9182f2072a59471bbfc86495f688` |
| F4: top-up 50% | `8b04b5246d67973c943e3650940ed4ac91e028b5caee215f0f9497d6a497d948` | `535f71522a0d57b5d84d91d2cee61eb2dfb6f3ea4ada1b0747e65be2a1c0a3ef` | Falló expectativa de top-up 70% | `8b04b5246d67973c943e3650940ed4ac91e028b5caee215f0f9497d6a497d948` |
| F6: ignorar archivo previo | `137de3162338a1796f2fa46e8a6c7981ac8a89f32ab28643431829cdd5db1c01` | `ab1bffdbebd38657072a2f895dc2e7fd90f421a586437323589b1db7f6fd3e12` | Falló: segunda evaluación devolvió virgen=True | `137de3162338a1796f2fa46e8a6c7981ac8a89f32ab28643431829cdd5db1c01` |
| F7: leer siempre de disco | `3a956ea856425de307fc12b49421e0039c99b7264da24be9f0b43e076ec821a6` | `4d74ec323d3f270b5f6dc9106c6d39edbf115ec76dc6bb618252ac316b78e922` | Falló: dos lecturas donde se esperaba una | `3a956ea856425de307fc12b49421e0039c99b7264da24be9f0b43e076ec821a6` |
| F8: devolver objeto cacheado mutable | `14b1836a8f7a13c06ab316df7ed2949ccc8523fd4147260d1f90806e1d61a54c` | `dbfc3e4a7f4b2ccea3b8abbfe97ee86ffc0a7997b106156bb87fdfdd7ad9bb6e` | Falló al contaminar la lectura posterior | `14b1836a8f7a13c06ab316df7ed2949ccc8523fd4147260d1f90806e1d61a54c` |
| F9: reintroducir mojibake | `8a4f916b9ee47fca5e262f9767f3bf5dc5dbc445afe5d06d63ea75fa09def92c` | `7f502e4f59fdb2c3fd33c6060fced8ff4211a13caec561dcb94140d18e697cce` | Falló la prueba de texto UTF-8/acento | `8a4f916b9ee47fca5e262f9767f3bf5dc5dbc445afe5d06d63ea75fa09def92c` |

F5 y G1/G2 no tienen mutación porque no se implementaron. F10 es una limpieza de fixture/documentación, sin mutación de comportamiento.

## Archivos para commits separados

- F1: `models/volatility/model_stats.py`, `database/db_manager.py`, `tests/test_vol_model_stats.py`.
- F2: `grid/sim/data.py`, `grid/sim/runner.py`, `tests/test_grid_sim_data.py`, `tests/test_grid_sim_runner.py`, `tests/test_grid_advisor.py`, `tests/test_grid_maxdays.py`.
- F3: `api/routes/grid_advisor.py`, `frontend/app.js`, `tests/test_grid_advisor.py`, `tests/ui/test_grids_browser.py`.
- F4: `grid/loan_cohorts.py`, `grid/policy.py`, `api/routes/grids.py`, `frontend/grids.js`, `tests/test_grid_loans_pure.py`, `tests/test_grid_policy.py`, `tests/test_grids_api.py`, `tests/test_grid_auto_open.py`, `tests/ui/test_grids_browser.py`.
- F5: ninguno; no hecho.
- F6: `scripts/vol_consensus_eval.py`, `tests/test_vol_consensus_eval.py`.
- F7: `grid/volatility_provider.py`, `tests/test_vol_provider_per_symbol.py`.
- F8: `config/models_config.py`, `tests/test_vol_per_symbol.py`.
- F9: `tests/ui/test_frontend_format.py`, `data/volatility.py`, `scripts/vol_research.py`, `tests/test_vol_mojibake.py`.
- F10: `tests/ui/test_models_page.py`, `REPORTE_FASE20D1.md`.
- G1/G2: ninguno; no hechos.

NO VERIFICADO: comportamiento F5 bajo dos procesos SQLite independientes; experimentos multi-ventana de G1; intervalo bootstrap/API/UI de G2. No se alteraron defaults de Smart (`horizon_h=4`, `stop_loss_pct=5`, pausa/ajuste ni estrategia predeterminada).

## Auditoría final

`git diff --stat` y `git diff` revisados: 30 archivos modificados y 3 nuevos, todos dentro de los puntos F1-F10/G1-G2 descritos arriba. `git diff --check` no informó errores de whitespace; los avisos restantes de Git son solo sobre su conversión automática de LF a CRLF en futuras escrituras. índice vacío (`git diff --cached --name-only` sin salida). Los textos nuevos/modificados son UTF-8 sin BOM, LF y sin U+FFFD; no se tocó ningún default de Smart. No hubo escrituras a la base real ni cambios en `models/saved/`.

Archivos transversales de documentación: `REPORTE_FASE20F.md`; F10 actualiza además `REPORTE_FASE20D1.md`.

### Decisiones pendientes de Ramón

F4: las cohortes `loans` existentes se conservan como históricas; las aperturas nuevas con préstamos se marcan `loans_v2`. G1 para decidir la prueba de stop-loss por σ requiere autorización escrita aparte. 19C-2 sigue bloqueada.
