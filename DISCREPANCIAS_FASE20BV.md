# Discrepancias Fase 20B-V

| Tema | Estado / evidencia |
|---|---|
| HEAD de partida | El prompt indicaba `81325fb`, pero el repositorio estaba en `442779c` (Fase 20C). No se reescribió el historial. |
| Prueba del patrón solicitado | `tests/test_volatility*.py` no coincide con archivos existentes y produjo `ERROR: file or directory not found`. Se repitió enumerando solo `tests/test_vol_*.py` y `tests/test_model_stats*.py` presentes; resultado final: `127 passed, 1 skipped, 22 warnings in 24.88s`. |
| Mutación de ruta aislada | Para mantener la prueba segura, la mutación redirigió las velas a la carpeta padre temporal, no a `data/cache` real. Falló la aserción de aislamiento; el archivo se restauró byte por byte. |
| Archivos de Model C preexistentes | `models/saved/metrics_xrp_1h.json` ya figuraba modificado antes de este trabajo; no se revirtió ni editó. Se preservaron también `.bak`, `.pytest_tmp/` y los cambios ajenos preexistentes. |
| SHA del archivo de Model C | SHA-256 final de `models/saved/metrics_xrp_1h.json`: `04db6e0e284456f2b609c6fcd212ad5c2ee3b86f5c1c873a8102a0e7ad99cead`. No se capturó el SHA al inicio de esta continuación; por eso no afirmo una comparación antes/después. |
| Descarga/entrenamiento real | NO VERIFICADO por el límite de no acceder a Binance ni entrenar. El tiempo real de entrenamiento y la descarga de 5m quedan pendientes. |
| `vol_forecasts.model_version` | No existe esa columna en el esquema revisado. El endpoint informa los metadatos que sí existen y no inventa fecha/rango de consenso. |
| Windows `taskkill` real | NO VERIFICADO; la prueba de cancelación usa servicio y base SQLite temporales. |

Las cinco mutaciones conductuales y sus SHA-256 antes/después/restaurado constan en [REPORTE_FASE20BV.md](REPORTE_FASE20BV.md). Cada SHA restaurado coincide con el SHA anterior y las restauraciones fueron byte idénticas.
