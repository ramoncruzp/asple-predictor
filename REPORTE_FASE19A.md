# Fase 19A - consenso offline de volatilidad XRPUSDT

## Criterio y límites

Se usaron las velas 5m locales y la agregación/feature frame de `scripts/train_vol_models.py` y `data.volatility`. Para cada horizonte se aplicó `data.splits.chronological_split` con 70/15/15 y embargo igual al horizonte en filas horarias. Los ocho modelos se ajustaron en memoria con TRAIN; `GBM` y `HAR_range` reciben VAL para parada temprana/selección de estimador según su implementación. No se guardó ni promovió ningún modelo.

Elegibilidad significa que el modelo supera Persistence en MSE de log-vol en VAL con bootstrap circular por bloques de 168 horas, IC 95 % que excluye cero a favor del modelo (2.000 réplicas, semilla 42). Los pesos P son el inverso del MSE VAL de pronósticos finales, normalizados; M es la mediana por timestamp de los elegibles. Solo P y M se comparan como variantes de consenso.

La calibración por varianza se estima en VAL con factor `mean(exp(2*y))/mean(exp(2*pred))`; se aplica solo si baja QLIKE y acerca `var_ratio` a 1. Las predicciones y comparaciones de TEST se informan una sola vez. Estado `validated` exige IC que excluye cero a favor del consenso y p ajustado Holm < 0,05 entre las 8 comparaciones (4 horizontes x 2 variantes).

**Honestidad sobre TEST en XRP:** los ocho modelos y sus campeones ya se evaluaron y eligieron mirando TEST en fases anteriores (V1/V1b). Para XRP, TEST no es virgen. El consenso se diseña con VAL; la comparación contra el campeón favorece al campeón y se declara así; la validación definitiva del consenso será la evidencia en vivo (19B), no este TEST.

Los artefactos de producción existentes están ajustados con el 85 % (ver `scripts/train_vol_models.py:108-120`) y no se usaron para elegibilidad/pesos. Los modelos se ajustaron en memoria con TRAIN 70 %; los pesos VAL son punto de partida, no verdad, porque en vivo se usarán artefactos de producción ajustados con 85 % y 19B los corregirá con desempeño real.

CSV SHA-256: `d18d8f9253cf4a27fc91311b12b4106e3c3aadaef76d4d621cae63e8ca3a9b96`. Fecha del resultado: `2026-10-05T14:29:17.803332+00:00`. Seed: `42`. Comparaciones Holm: `8`.

## Elegibles, pesos y confianza VAL

| H | Filas train / val / test | Elegibles | Pesos P (suma 1) | Confianza alta / media / baja |
|---:|---:|---|---|---:|
| 1 | 12132 / 2599 / 2599 | HAR, HAR_range, HAR_asym, GBM, NexoHAR | HAR=0.1922, HAR_range=0.2029, HAR_asym=0.1915, GBM=0.2167, NexoHAR=0.1968 | 1555 / 911 / 133 de 2599 |
| 2 | 12131 / 2599 / 2597 | HAR, HAR_range, HAR_asym, GBM, NexoHAR | HAR=0.1890, HAR_range=0.2008, HAR_asym=0.1880, GBM=0.2276, NexoHAR=0.1947 | 1521 / 935 / 143 de 2599 |
| 4 | 12130 / 2599 / 2592 | HAR, HAR_range, HAR_asym, GBM, NexoHAR | HAR=0.1866, HAR_range=0.1947, HAR_asym=0.1848, GBM=0.2339, NexoHAR=0.2000 | 1567 / 893 / 139 de 2599 |
| 24 | 12116 / 2596 / 2549 | HAR, HAR_range, HAR_asym, GBM, NexoHAR | HAR=0.1940, HAR_range=0.1976, HAR_asym=0.1884, GBM=0.2235, NexoHAR=0.1966 | 2110 / 476 / 10 de 2596 |

El vector de pesos incluye los ocho modelos; todo modelo no elegible tiene peso 0. Umbrales fijos IQR: alta <0,10; media entre 0,10 y 0,25 inclusive; baja >0,25 o menos de tres elegibles.

## TEST: métricas por modelo

Métricas sobre log-vol. `diff` es MSE campeón menos MSE del modelo; positivo favorece al modelo. Los modelos individuales son diagnóstico; Holm se reserva para las 8 comparaciones P/M.

| H | Modelo | R² | MSE | QLIKE | var_ratio | diff vs campeón | IC 95 % | Holm p | Estado | Peso VAL |
|---:|---|---:|---:|---:|---:|---:|---|---:|---|---:|
| 1 | Persistence | 0.461042 | 0.216243 | 0.607021 | 0.999694 | -0.045259 | [-0.056037, -0.034691] | N/A | diagnostic_only | 0.000000 |
| 1 | EWMA | 0.358897 | 0.257226 | 0.528847 | 0.975261 | -0.086242 | [-0.124433, -0.055620] | N/A | diagnostic_only | 0.000000 |
| 1 | HAR | 0.513268 | 0.195289 | 0.384992 | 1.089192 | -0.024305 | [-0.029693, -0.018687] | N/A | diagnostic_only | 0.192161 |
| 1 | HAR_range | 0.548274 | 0.181243 | 0.359685 | 1.065316 | -0.010260 | [-0.014251, -0.005877] | N/A | diagnostic_only | 0.202868 |
| 1 | HAR_asym | 0.517616 | 0.193544 | 0.384618 | 1.107929 | -0.022561 | [-0.028062, -0.016849] | N/A | diagnostic_only | 0.191511 |
| 1 | GBM | 0.573845 | 0.170984 | 0.329528 | 1.045016 | N/A | N/A | N/A | champion | 0.216671 |
| 1 | NexoHAR | 0.543703 | 0.183077 | 0.389352 | 1.065146 | -0.012094 | [-0.022082, -0.002987] | N/A | diagnostic_only | 0.196789 |
| 1 | GARCH_t | 0.339747 | 0.264909 | 0.498649 | 1.057540 | -0.093926 | [-0.123545, -0.066350] | N/A | diagnostic_only | 0.000000 |
| 2 | Persistence | 0.511070 | 0.175845 | 0.488287 | 0.999829 | -0.047117 | [-0.054736, -0.038927] | N/A | diagnostic_only | 0.000000 |
| 2 | EWMA | 0.417212 | 0.209601 | 0.464510 | 0.975435 | -0.080873 | [-0.116769, -0.052973] | N/A | diagnostic_only | 0.000000 |
| 2 | HAR | 0.559354 | 0.158479 | 0.334494 | 1.100433 | -0.029751 | [-0.034691, -0.024833] | N/A | diagnostic_only | 0.188959 |
| 2 | HAR_range | 0.589637 | 0.147588 | 0.317020 | 1.080275 | -0.018860 | [-0.022598, -0.014426] | N/A | diagnostic_only | 0.200790 |
| 2 | HAR_asym | 0.563766 | 0.156893 | 0.334274 | 1.122515 | -0.028164 | [-0.033682, -0.022688] | N/A | diagnostic_only | 0.187969 |
| 2 | GBM | 0.642076 | 0.128728 | 0.263155 | 1.053065 | N/A | N/A | N/A | champion | 0.227627 |
| 2 | NexoHAR | 0.600592 | 0.143648 | 0.328272 | 1.081572 | -0.014920 | [-0.024779, -0.005615] | N/A | diagnostic_only | 0.194655 |
| 2 | GARCH_t | 0.394855 | 0.217642 | 0.440226 | 1.063106 | -0.088914 | [-0.113964, -0.065576] | N/A | diagnostic_only | 0.000000 |
| 4 | Persistence | 0.466303 | 0.175004 | 0.486434 | 0.997713 | -0.063999 | [-0.075488, -0.051563] | N/A | diagnostic_only | 0.000000 |
| 4 | EWMA | 0.438296 | 0.184188 | 0.422247 | 0.973546 | -0.073183 | [-0.109967, -0.044082] | N/A | diagnostic_only | 0.000000 |
| 4 | HAR | 0.556700 | 0.145362 | 0.316459 | 1.112976 | -0.034357 | [-0.041969, -0.027221] | N/A | diagnostic_only | 0.186563 |
| 4 | HAR_range | 0.578426 | 0.138238 | 0.304349 | 1.096355 | -0.027233 | [-0.033740, -0.020173] | N/A | diagnostic_only | 0.194712 |
| 4 | HAR_asym | 0.561346 | 0.143839 | 0.317343 | 1.140040 | -0.032833 | [-0.040913, -0.025039] | N/A | diagnostic_only | 0.184830 |
| 4 | GBM | 0.661475 | 0.111005 | 0.229489 | 1.076157 | N/A | N/A | N/A | champion | 0.233858 |
| 4 | NexoHAR | 0.627962 | 0.121995 | 0.284385 | 1.097757 | -0.010989 | [-0.022545, 0.000095] | N/A | diagnostic_only | 0.200036 |
| 4 | GARCH_t | 0.407730 | 0.194211 | 0.397668 | 1.071087 | -0.083205 | [-0.104529, -0.063836] | N/A | diagnostic_only | 0.000000 |
| 24 | Persistence | 0.434347 | 0.144351 | 0.343897 | 0.998994 | -0.047640 | [-0.089365, -0.014431] | N/A | diagnostic_only | 0.000000 |
| 24 | EWMA | 0.441231 | 0.142595 | 0.350212 | 0.980100 | -0.045884 | [-0.083435, -0.011395] | N/A | diagnostic_only | 0.000000 |
| 24 | HAR | 0.554128 | 0.113784 | 0.257262 | 1.182700 | -0.017073 | [-0.029406, -0.004673] | N/A | diagnostic_only | 0.193981 |
| 24 | HAR_range | 0.558013 | 0.112793 | 0.255939 | 1.170745 | -0.016081 | [-0.028027, -0.003815] | N/A | diagnostic_only | 0.197580 |
| 24 | HAR_asym | 0.559871 | 0.112318 | 0.253418 | 1.215855 | -0.015607 | [-0.029293, -0.001806] | N/A | diagnostic_only | 0.188353 |
| 24 | GBM | 0.583197 | 0.106366 | 0.232331 | 1.169833 | -0.009654 | [-0.031259, 0.008430] | N/A | diagnostic_only | 0.223456 |
| 24 | NexoHAR | 0.621029 | 0.096711 | 0.213758 | 1.046610 | N/A | N/A | N/A | champion | 0.196630 |
| 24 | GARCH_t | 0.437378 | 0.143578 | 0.271815 | 1.157080 | -0.046867 | [-0.082242, -0.011408] | N/A | diagnostic_only | 0.000000 |

## TEST: consenso P y M frente al campeón

| H | Variante | R² | MSE | QLIKE | var_ratio | diff vs campeón | IC 95 % | p bootstrap | p Holm | Estado |
|---:|---|---:|---:|---:|---:|---:|---|---:|---:|---|
| 1 | P | 0.557080 | 0.177710 | 0.348565 | 1.077176 | -0.006727 | [-0.011119, -0.002047] | 0.994503 | 1.000000 | not_validated |
| 1 | M | 0.547076 | 0.181724 | 0.357096 | 1.085206 | -0.010740 | [-0.015252, -0.005950] | 1.000000 | 1.000000 | not_validated |
| 2 | P | 0.614569 | 0.138621 | 0.291361 | 1.089671 | -0.009893 | [-0.014026, -0.005370] | 1.000000 | 1.000000 | not_validated |
| 2 | M | 0.594696 | 0.145769 | 0.308957 | 1.098129 | -0.017040 | [-0.021167, -0.012832] | 1.000000 | 1.000000 | not_validated |
| 4 | P | 0.624059 | 0.123274 | 0.265444 | 1.106175 | -0.012269 | [-0.017770, -0.006344] | 1.000000 | 1.000000 | not_validated |
| 4 | M | 0.590113 | 0.134406 | 0.292260 | 1.111046 | -0.023400 | [-0.029749, -0.016889] | 1.000000 | 1.000000 | not_validated |
| 24 | P | 0.598788 | 0.102387 | 0.230318 | 1.167878 | -0.005676 | [-0.015534, 0.004000] | 0.859570 | 1.000000 | not_validated |
| 24 | M | 0.579758 | 0.107243 | 0.241867 | 1.177658 | -0.010532 | [-0.021514, 0.000605] | 0.964518 | 1.000000 | not_validated |

## Calibración de varianza en VAL

| H | Modelo/variante | Factor | Aplicado | QLIKE raw -> calibrado | var_ratio raw -> calibrado |
|---:|---|---:|---|---:|---:|
| 1 | Persistence | 1.000221 | sí | 0.668465 -> 0.668317 | 1.000221 -> 1.000000 |
| 1 | EWMA | 1.058532 | sí | 0.535127 -> 0.523050 | 1.058532 -> 1.000000 |
| 1 | HAR | 1.333598 | sí | 0.480522 -> 0.407493 | 1.333598 -> 1.000000 |
| 1 | HAR_range | 1.303083 | sí | 0.446896 -> 0.388195 | 1.303083 -> 1.000000 |
| 1 | HAR_asym | 1.316098 | sí | 0.473589 -> 0.406851 | 1.316098 -> 1.000000 |
| 1 | GBM | 1.307177 | sí | 0.424426 -> 0.369297 | 1.307177 -> 1.000000 |
| 1 | NexoHAR | 1.183013 | sí | 0.483858 -> 0.432355 | 1.183013 -> 1.000000 |
| 1 | GARCH_t | 0.801529 | sí | 0.490747 -> 0.479236 | 0.801529 -> 1.000000 |
| 1 | P | 1.019968 | sí | 0.380762 -> 0.379471 | 1.019968 -> 1.000000 |
| 1 | M | 1.019632 | sí | 0.384427 -> 0.383087 | 1.019632 -> 1.000000 |
| 2 | Persistence | 1.000041 | sí | 0.529968 -> 0.529946 | 1.000041 -> 1.000000 |
| 2 | EWMA | 1.058295 | sí | 0.457354 -> 0.444024 | 1.058295 -> 1.000000 |
| 2 | HAR | 1.255854 | sí | 0.390215 -> 0.340898 | 1.255854 -> 1.000000 |
| 2 | HAR_range | 1.232314 | sí | 0.360276 -> 0.321513 | 1.232314 -> 1.000000 |
| 2 | HAR_asym | 1.236797 | sí | 0.384388 -> 0.340821 | 1.236797 -> 1.000000 |
| 2 | GBM | 1.225138 | sí | 0.321110 -> 0.289217 | 1.225138 -> 1.000000 |
| 2 | NexoHAR | 1.081026 | sí | 0.378640 -> 0.359530 | 1.081026 -> 1.000000 |
| 2 | GARCH_t | 0.785946 | sí | 0.412274 -> 0.400333 | 0.785946 -> 1.000000 |
| 2 | P | 1.018815 | sí | 0.308306 -> 0.307147 | 1.018815 -> 1.000000 |
| 2 | M | 1.016763 | sí | 0.317850 -> 0.316709 | 1.016763 -> 1.000000 |
| 4 | Persistence | 0.999313 | no | 0.461607 -> 0.461924 | 0.999313 -> 1.000000 |
| 4 | EWMA | 1.057979 | sí | 0.403227 -> 0.388411 | 1.057979 -> 1.000000 |
| 4 | HAR | 1.197186 | sí | 0.322689 -> 0.290673 | 1.197186 -> 1.000000 |
| 4 | HAR_range | 1.180340 | sí | 0.302971 -> 0.277320 | 1.180340 -> 1.000000 |
| 4 | HAR_asym | 1.175410 | sí | 0.317710 -> 0.291060 | 1.175410 -> 1.000000 |
| 4 | GBM | 1.158858 | sí | 0.253156 -> 0.236224 | 1.158858 -> 1.000000 |
| 4 | NexoHAR | 1.022029 | sí | 0.293191 -> 0.289529 | 1.022029 -> 1.000000 |
| 4 | GARCH_t | 0.756773 | sí | 0.354778 -> 0.336414 | 0.756773 -> 1.000000 |
| 4 | P | 1.016365 | sí | 0.255819 -> 0.254994 | 1.016365 -> 1.000000 |
| 4 | M | 1.012006 | sí | 0.272268 -> 0.271532 | 1.012006 -> 1.000000 |
| 24 | Persistence | 0.992659 | no | 0.293187 -> 0.295347 | 0.992659 -> 1.000000 |
| 24 | EWMA | 1.056559 | sí | 0.362925 -> 0.340851 | 1.056559 -> 1.000000 |
| 24 | HAR | 1.019160 | sí | 0.173943 -> 0.173084 | 1.019160 -> 1.000000 |
| 24 | HAR_range | 1.013419 | sí | 0.169544 -> 0.169079 | 1.013419 -> 1.000000 |
| 24 | HAR_asym | 0.983403 | no | 0.175075 -> 0.175607 | 0.983403 -> 1.000000 |
| 24 | GBM | 1.003101 | sí | 0.152190 -> 0.152127 | 1.003101 -> 1.000000 |
| 24 | NexoHAR | 0.881521 | no | 0.164744 -> 0.165713 | 0.881521 -> 1.000000 |
| 24 | GARCH_t | 0.556503 | sí | 0.341674 -> 0.212910 | 0.556503 -> 1.000000 |
| 24 | P | 0.981838 | no | 0.155415 -> 0.155571 | 0.981838 -> 1.000000 |
| 24 | M | 0.989127 | no | 0.163770 -> 0.163985 | 0.989127 -> 1.000000 |

## Cambios, pruebas y mutaciones

- `models/volatility/consensus.py`: combinación pura, ponderación inyectada, mediana, IQR y confianza.
- `scripts/vol_consensus_eval.py`: ajuste en memoria, split, métricas, bootstrap/Holm y escritura exclusiva del nuevo JSON.
- `models/volatility/evaluation.py`: agrega p bootstrap unilateral para el ajuste Holm; conserva IC y decisión existentes.
- `api/routes/volatility.py`: añade `consensus` por horizonte solo para XRPUSDT; conserva campos del campeón. Ausencia de JSON, elegibles o predicciones frescas produce `consensus: null` con razón.
- Pruebas nuevas en `tests/test_vol_consensus.py` y `tests/test_volatility_live.py` cubren funciones puras, split/embargo, antifuga, p determinista, API con/sin archivo, datos obsoletos y no XRP.

Mutaciones, todas detectadas y restauradas byte a byte (SHA-256 antes/después igual): (a) MSE en vez de inverso -> `test_inverse_mse_weights_are_normalized_and_zero_noneligible`; (b) introducir `HAR` no elegible -> `test_validation_weights_do_not_change_when_test_data_changes`; (c) selección con TEST -> la misma prueba antifuga. Resultado: 3/3 muertas.

Durante la primera comprobación de (c), la prueba no detectó la mutación porque el fixture aplicaba el mismo desplazamiento a los valores TEST y sus pronósticos, conservando los errores. Ajusté el fixture para que TEST cambie de forma independiente; la mutación final sí falla y quedó restaurada.

Pruebas dirigidas finales: `ASPLE_OFFLINE=1 .\venv\Scripts\python.exe -m pytest tests/test_volatility.py tests/test_volatility_provider.py tests/test_volatility_live.py tests/test_vol_series_causal.py tests/test_vol_consensus.py -q --basetemp $env:TEMP\pytest-19a` -> **50 passed in 4.86s**.

Evaluación: `ASPLE_OFFLINE=1 .\venv\Scripts\python.exe scripts/vol_consensus_eval.py` -> código 0, **79.08 s**; escribió el nuevo `models/saved/vol/consensus_xrp.json` sin modificar artefactos de modelos. Un intento previo terminó con NameError de índice en el resumen de confianza a los 34.50 s; se corrigió el defecto y la corrida completa quedó verde.

## Discrepancias y no verificado

- `scripts/vol_research.py:82-108` incluye nueve modelos de investigación (incluye HARQ); `VOL_MODELS` enumera ocho y no incluye HARQ. El estudio siguió `VOL_MODELS`, reutilizó `chronological_split`, `feature_columns`, `build_volatility_frame`, `score_forecast`, `_load_data` y `_new_model`; HARQ quedó fuera por alcance explícito.
- **NO VERIFICADO:** comportamiento en vivo; validez más allá de 24 h; costo de CPU en vivo; respuesta real de `/api/volatility/forecast` conectada a predicciones producidas en Testnet/entorno operativo.
- Sin Binance/Testnet, sin `.env`, sin stage/commit/push. Los informes se guardan UTF-8 sin BOM y LF; se verifican bytes tras escribirlos.
