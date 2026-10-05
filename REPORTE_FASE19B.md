# Fase 19B — estadísticas por modelo y pesos adaptativos

Observación local, solo lectura: 2026-10-05T15:43:32.608944+00:00 UTC. Origen: `asple_predictor.db` abierto con SQLite `mode=ro`; no se cargó `.env` ni se consultó red. Cada horizonte tiene 26 pronósticos por modelo (208 filas en total); las verificaciones maduras por modelo son 25 (1h), 24 (2h), 23 (4h) y 14 (24h). Por eso todos permanecen en `acumulando` y el consenso sigue usando pesos VAL.

## Definiciones

- Verificada: `realized_logvol` y `verified_at` presentes, `forecast_at + horizonte <= now` y `verified_at <= now` (UTC; timestamps naive se interpretan UTC).
- Error con signo: predicción calibrada menos realizado. Sesgo positivo es sobreestimación; negativo, subestimación. Porcentaje de sobre/subestimación cuenta errores estrictamente positivos/negativos.
- `sigma_ref`: desviación estándar de error residual de VAL guardada en `consensus_xrp.json`, derivada como `sqrt(max(MSE_VAL - bias_VAL^2, 0))`. Acierto 1σ: error absoluto <= sigma_ref; acierto 2σ: error absoluto <= 2*sigma_ref; fallo: >2*sigma_ref. La cobertura usa verificaciones maduras.
- MAE y MSE son sobre error de log-volatilidad calibrada. QLIKE = media de `r - ln(r) - 1`, `r = var_predicha/var_realizada`; `var_ratio = media(var_predicha)/media(var_realizada)`.
- Ventanas: todo el histórico maduro; últimas 168 horas respecto al pronóstico más reciente; últimas 168 verificaciones. Racha reciente son las 10 verificaciones maduras más recientes.
- Dispersión usa IQR de predicciones log-vol de los modelos elegibles por VAL: alta `<0.10`, media `0.10..0.25` inclusive, baja `>0.25`; con menos de 3 modelos se clasifica baja sin IQR. Los tres niveles presentan estadísticas del campeón del horizonte y quedan acumulando bajo 30 casos. Spearman relaciona IQR con error absoluto del campeón.
- `N_MIN=30`, EMA `alpha=0.20`, tope individual `0.50`, mínimo de elegibles en vivo `3`, evaluación futura mínima `200`. Peso adaptativo usa las últimas 168 verificaciones maduras y elegibilidad `activo` más MSE rolling <= Persistence. P usa 1/MSE; P2 usa 1/MSE² (post-hoc, solo vivo); M es mediana. Si hay menos de tres elegibles, pesos efectivos VAL y confianza baja.
- `validated` exige al menos 200 resultados P evaluados después del timestamp de sus pesos, MSE P <= campeón y coberturas 1σ/2σ dentro de ±10 puntos porcentuales de 68%/95%. No habilita grids: el campeón sigue siendo la fuente de grid.

## XRPUSDT observado

MSE, coberturas y sesgo usan todo el histórico maduro local en el instante indicado. Guion significa que no hubo verificaciones o sigma VAL para ese dato.

| Horizonte | Modelo | Pred. | Verif. | Ac 1σ | Ac 2σ | Fallos | Cob. 1σ | Cob. 2σ | Sobre | Sub | Sesgo | MAE | MSE | QLIKE | Var ratio | Estado |
|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| 1 | Persistence | 26 | 25 | 17 | 23 | 2 | 68.0% | 92.0% | 44.0% | 56.0% | 0.0082 | 0.4215 | 0.2672 | 0.7489 | 1.0263 | acumulando |
| 1 | EWMA | 26 | 25 | 9 | 22 | 3 | 36.0% | 88.0% | 60.0% | 40.0% | 0.1146 | 0.6056 | 0.4598 | 1.2531 | 0.6337 | acumulando |
| 1 | HAR | 26 | 25 | 13 | 20 | 5 | 52.0% | 80.0% | 72.0% | 28.0% | 0.2618 | 0.4752 | 0.3141 | 1.0063 | 0.9751 | acumulando |
| 1 | HAR_range | 26 | 25 | 12 | 24 | 1 | 48.0% | 96.0% | 72.0% | 28.0% | 0.2106 | 0.4306 | 0.2380 | 0.6468 | 0.8243 | acumulando |
| 1 | HAR_asym | 26 | 25 | 13 | 20 | 5 | 52.0% | 80.0% | 72.0% | 28.0% | 0.2569 | 0.4755 | 0.3146 | 1.0064 | 0.9703 | acumulando |
| 1 | GBM | 26 | 25 | 12 | 25 | 0 | 48.0% | 100.0% | 76.0% | 24.0% | 0.2532 | 0.3960 | 0.2082 | 0.5690 | 0.8545 | acumulando |
| 1 | NexoHAR | 26 | 25 | 13 | 22 | 3 | 52.0% | 88.0% | 72.0% | 28.0% | 0.2621 | 0.4232 | 0.2661 | 0.8577 | 0.9951 | acumulando |
| 1 | GARCH_t | 26 | 25 | 13 | 22 | 3 | 52.0% | 88.0% | 68.0% | 32.0% | 0.1952 | 0.4739 | 0.3257 | 1.0497 | 0.7602 | acumulando |
| 2 | Persistence | 26 | 24 | 13 | 24 | 0 | 54.2% | 100.0% | 54.2% | 45.8% | 0.0117 | 0.3813 | 0.1961 | 0.4180 | 1.1539 | acumulando |
| 2 | EWMA | 26 | 24 | 7 | 19 | 5 | 29.2% | 79.2% | 58.3% | 41.7% | 0.0943 | 0.5768 | 0.4280 | 1.1938 | 0.8180 | acumulando |
| 2 | HAR | 26 | 24 | 13 | 20 | 4 | 54.2% | 83.3% | 75.0% | 25.0% | 0.2592 | 0.4531 | 0.2890 | 0.9831 | 1.2347 | acumulando |
| 2 | HAR_range | 26 | 24 | 13 | 21 | 3 | 54.2% | 87.5% | 70.8% | 29.2% | 0.2087 | 0.3800 | 0.2006 | 0.5968 | 1.0525 | acumulando |
| 2 | HAR_asym | 26 | 24 | 13 | 20 | 4 | 54.2% | 83.3% | 75.0% | 25.0% | 0.2528 | 0.4525 | 0.2906 | 0.9879 | 1.2280 | acumulando |
| 2 | GBM | 26 | 24 | 13 | 21 | 3 | 54.2% | 87.5% | 83.3% | 16.7% | 0.2479 | 0.3322 | 0.1670 | 0.5220 | 1.0838 | acumulando |
| 2 | NexoHAR | 26 | 24 | 13 | 21 | 3 | 54.2% | 87.5% | 79.2% | 20.8% | 0.2330 | 0.3739 | 0.2255 | 0.7864 | 1.1356 | acumulando |
| 2 | GARCH_t | 26 | 24 | 12 | 21 | 3 | 50.0% | 87.5% | 66.7% | 33.3% | 0.1819 | 0.4463 | 0.2912 | 0.9964 | 0.9804 | acumulando |
| 4 | Persistence | 26 | 23 | 15 | 22 | 1 | 65.2% | 95.7% | 73.9% | 26.1% | 0.1138 | 0.3882 | 0.2915 | 1.4071 | 1.9519 | acumulando |
| 4 | EWMA | 26 | 23 | 6 | 19 | 4 | 26.1% | 82.6% | 56.5% | 43.5% | 0.0906 | 0.5750 | 0.4055 | 1.0931 | 1.0262 | acumulando |
| 4 | HAR | 26 | 23 | 9 | 18 | 5 | 39.1% | 78.3% | 78.3% | 21.7% | 0.2948 | 0.4566 | 0.2777 | 0.9046 | 1.5561 | acumulando |
| 4 | HAR_range | 26 | 23 | 10 | 21 | 2 | 43.5% | 91.3% | 69.6% | 30.4% | 0.2477 | 0.3799 | 0.1930 | 0.5550 | 1.3502 | acumulando |
| 4 | HAR_asym | 26 | 23 | 9 | 18 | 5 | 39.1% | 78.3% | 73.9% | 26.1% | 0.2865 | 0.4568 | 0.2805 | 0.9161 | 1.5471 | acumulando |
| 4 | GBM | 26 | 23 | 12 | 21 | 2 | 52.2% | 91.3% | 78.3% | 21.7% | 0.2806 | 0.3358 | 0.1517 | 0.4436 | 1.3225 | acumulando |
| 4 | NexoHAR | 26 | 23 | 12 | 21 | 2 | 52.2% | 91.3% | 73.9% | 26.1% | 0.2441 | 0.3754 | 0.1938 | 0.5753 | 1.5005 | acumulando |
| 4 | GARCH_t | 26 | 23 | 11 | 21 | 2 | 47.8% | 91.3% | 65.2% | 34.8% | 0.1969 | 0.4439 | 0.2631 | 0.9196 | 1.2374 | acumulando |
| 24 | Persistence | 26 | 14 | 9 | 9 | 5 | 64.3% | 64.3% | 78.6% | 21.4% | 0.3844 | 0.4638 | 0.4261 | 1.9985 | 2.0273 | acumulando |
| 24 | EWMA | 26 | 14 | 5 | 12 | 2 | 35.7% | 85.7% | 85.7% | 14.3% | 0.4128 | 0.4276 | 0.2884 | 1.1015 | 1.5611 | acumulando |
| 24 | HAR | 26 | 14 | 4 | 6 | 8 | 28.6% | 42.9% | 100.0% | 0.0% | 0.5969 | 0.5969 | 0.4663 | 1.8483 | 2.2404 | acumulando |
| 24 | HAR_range | 26 | 14 | 4 | 8 | 6 | 28.6% | 57.1% | 100.0% | 0.0% | 0.5301 | 0.5301 | 0.3651 | 1.3122 | 1.9658 | acumulando |
| 24 | HAR_asym | 26 | 14 | 4 | 7 | 7 | 28.6% | 50.0% | 100.0% | 0.0% | 0.5989 | 0.5989 | 0.4762 | 1.9297 | 2.2675 | acumulando |
| 24 | GBM | 26 | 14 | 3 | 5 | 9 | 21.4% | 35.7% | 92.9% | 7.1% | 0.4861 | 0.4933 | 0.2945 | 0.9459 | 1.6934 | acumulando |
| 24 | NexoHAR | 26 | 14 | 4 | 9 | 5 | 28.6% | 64.3% | 100.0% | 0.0% | 0.4177 | 0.4177 | 0.2175 | 0.6669 | 1.7672 | acumulando |
| 24 | GARCH_t | 26 | 14 | 5 | 12 | 2 | 35.7% | 85.7% | 100.0% | 0.0% | 0.4996 | 0.4996 | 0.3512 | 1.3821 | 1.8370 | acumulando |

| Horizonte | Campeón | N P | MSE P | MSE P2 | MSE M | MSE campeón | Cob. P 1σ | Cob. P 2σ | Fuente |
|---:|---|---:|---:|---:|---:|---:|---:|---:|---|
| 1 | GBM | 25 | 0.2556 | — | 0.2553 | 0.2082 | 44.0% | 88.0% | VAL |
| 2 | GBM | 24 | 0.2218 | — | 0.2409 | 0.1670 | 50.0% | 79.2% | VAL |
| 4 | GBM | 23 | 0.2055 | — | 0.2176 | 0.1517 | 39.1% | 87.0% | VAL |
| 24 | NexoHAR | 14 | 0.3499 | — | 0.3709 | 0.2175 | 28.6% | 42.9% | VAL |

El consenso no se declara `validated`: los resultados P futuros son 25, 24, 23 y 14 por horizonte, todos bajo 200; además, el MSE P excede al del campeón y las coberturas mostradas quedan fuera del intervalo de ±10 puntos. Durante este periodo se usa el respaldo VAL. Los datos cubren cerca de una semana y no demuestran estabilidad por meses.

## Cambios por bloque

- `models/volatility/model_stats.py:12-307`: cálculo puro, madurez temporal, ventanas, métricas, buckets, Spearman, pesos P/P2 con EMA/tope, consenso mediano M y evaluación hacia adelante.
- `database/db_manager.py:659-674`: lectura de `vol_forecasts` por símbolo/horizonte; no se añadió tabla ni columna.
- `api/routes/volatility.py:42-125,139-145,206-257`: pesos/calibración en forecast, robustez para consenso nulo y endpoint `/model-stats` con respaldo VAL y status en evaluación.
- `frontend/app.js:286-300`, `frontend/styles.css:17`: tabla por modelo y horizonte, badges, campeón/consenso, coberturas de dispersión y pesos VAL de respaldo.
- `tests/test_vol_model_stats.py:1-149`, `tests/ui/test_grids_browser.py:105-133`: casos numéricos, bordes y test Playwright activo/acumulando.

## Verificación

- `ASPLE_OFFLINE=1 .\venv\Scripts\python.exe -m pytest tests/test_vol_model_stats.py tests/test_volatility.py tests/test_volatility_live.py tests/test_volatility_provider.py tests/test_frontend_encoding.py tests/ui/test_grids_browser.py::test_dashboard_shows_model_stats_accumulating_and_active -q --basetemp $env:TEMP\pytest-19b`  → **45 passed, 2 warnings, 9.70s**.
- `node --check frontend/app.js` → salida vacía, exit 0.
- Mutaciones temporales, todas detectadas y restauradas al SHA-256 `eb76add3a009588dbdbb9e3388ffe7d1a06f7732a3b0a13e6e84fce1f1ec840e`: N_MIN ignorado → `test_stats_fixed_metrics_and_active_threshold_29_30`; invertir bucket de dispersión → `test_dispersion_buckets_order_and_spearman_report_shape`; aceptar verificación inmadura → `test_maturity_excludes_future_and_unverified_results`; quitar tope → `test_capped_normalize_limits_single_model_dominance` y `test_adaptive_weights_use_persistence_reference_smooth_and_cap`. **4/4 muertas**.
- En la campaña final, la mutación N_MIN dio el fallo esperado en el test 29/30 y el test Playwright pasó en la misma invocación. Una corrida anterior reveló dos aserciones preexistentes desplazadas al insertar el test UI; se reubicaron y el test Playwright quedó verde.

## No verificado

Meses de datos, costo/latencia en vivo, desempeño futuro de P/P2/M, disponibilidad de modelos cuando no hay nuevos forecasts y cualquier operación de Binance/Testnet. No se ejecutó suite completa por el límite expreso de esta fase. No se agregó dependencia.

Archivos UTF-8 sin BOM; el bloque nuevo de JavaScript usa escapes `\uXXXX`. Sin archivos stageados, commit ni push.
