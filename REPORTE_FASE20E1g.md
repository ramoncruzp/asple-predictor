# Fase 20E-1g — simulación del Advisor con velas de 5 min

## Cambios

- `api/routes/grid_advisor.py:278-291`: mantiene las velas de 1 h para el análisis; descarga además 5 m para el mismo `days`, aplica `_simulation_window` y calcula `ewma_sigma_24h` con `halflife_h=72` sobre todas las velas recibidas antes de recortar la ventana. Pasa la parte alineada de sigma y velas de 5 m al runner.
- `data/binance_client.py:91-129`: `get_historical_klines(symbol, interval, lookback_days=90)` pagina con `limit=1000`, avanza desde `last openTime + 1` y continúa hasta completar el rango. Para 90 días de 5 m son unas 25.920 velas (más de una página).
- `config/settings.py:40` y `api/routes/grid_advisor.py:288-305`: cadencia derivada de `grid_monitor_interval=900` segundos: `max(1, round(900/300)) = 3` velas, es decir, 15 min. Si falla la ruta 5 m o la ventana contiene menos de dos velas, se conserva fallback 1 h, resync 3, sigma interna y aviso explícito; `resync_minutes` refleja 180 min en ese fallback.
- `frontend/app.js:286`: muestra bajo las tarjetas la resolución y cadencia recibidas; la prueba UI está en `tests/ui/test_grids_browser.py:155`.
- Pruebas nuevas: `tests/test_grid_advisor.py:339-398` verifica paso de velas de 5 min, sigma finita/caliente y cercana a 1 %, resync 3 y fallback horario con aviso/cadencia. La serie sintética da una sigma diaria mediana aproximada de 1 %, no una inflación cercana a 9×.

## Medición local reproducible

`scripts/diag_20e1g_advisor_5m.py` usa solo `data/cache/vol_train/*_{1h,5m}.csv`, filtros simulados, forecast falso y TestClient; no abre la base viva ni usa red. Capital 1.000 USDT, comisión 0,1 %, 90 días, rangos y niveles solicitados. STOP_LOSS es el conteo de eventos `STOP_LOSS`.

| Moneda | Ruta | Estrategia | Ciclos | P&L neto | Comisiones | DD máx. | Comprar y mantener | STOP_LOSS |
|---|---|---|---:|---:|---:|---:|---:|---:|
| ADA | anterior 1 h | Simple | 107 | 26,90 | 12,38 | 3,56 % | 53,28 | 0 |
| ADA | anterior 1 h | Smart | 326 | -1,97 | 35,86 | 3,51 % | 53,28 | 39 |
| ADA | nueva 5 m | Simple | 203 | 66,49 | 23,14 | 12,12 % | 89,71 | 0 |
| ADA | nueva 5 m | Smart | 546 | 17,65 | 61,62 | 3,90 % | 89,71 | 64 |
| PEPE | anterior 1 h | Simple | 195 | 52,29 | 21,14 | 12,82 % | 70,87 | 0 |
| PEPE | anterior 1 h | Smart | 238 | -29,65 | 25,94 | 8,62 % | 70,87 | 38 |
| PEPE | nueva 5 m | Simple | 446 | 145,54 | 47,66 | 17,56 % | 88,00 | 0 |
| PEPE | nueva 5 m | Smart | 808 | -62,13 | 86,43 | 7,78 % | 88,00 | 114 |

La nueva ruta conserva a Smart por debajo de Simple: diferencia Smart − Simple de -48,84 USDT en ADA y -207,67 USDT en PEPE. Frente a 20E-1e M4 (ADA Smart -33,02 / Simple +66,34), los niveles absolutos cambiaron con el snapshot local de CSV; la conclusión comparativa no cambia.

Cronometraje de `/api/grid/recommend` con TestClient y los CSV de caché (ruta anterior simulada al forzar fallo 5 m; después ruta nueva): ADA 0,319 s → 1,384 s; PEPE 0,556 s → 6,239 s. Ambos quedan bajo el umbral aproximado de 10 s, por lo que no añadí caché.

## Pruebas y mutaciones

- Dirigidas: `tests/test_grid_advisor.py`: `26 passed, 2 warnings`.
- No UI final: `949 passed, 1 skipped, 18 deselected, 38 warnings in 170.85s (0:02:50)`.
- UI final: `103 passed, 2 warnings in 102.46s (0:01:42)`.
- Mutación `sigma_values`: quitado de la llamada al runner; falló `test_5m_simulation_uses_warmed_daily_sigma_and_monitor_cadence`. SHA-256 antes/restaurado `871a02f3a4b390d71eb9d0934e99ea999fcb3ef2f9bb36a0f97bcd04504e8a5b`; mutado `e9ff19b445ce564eca4b4e67fad20028fa1f9d066c97e71c9efa78983419fed0`.
- Mutación `resync_candles`: quitado de la llamada; falló la misma prueba por su as erción de comportamiento. SHA-256 antes/restaurado `871a02f3a4b390d71eb9d0934e99ea999fcb3ef2f9bb36a0f97bcd04504e8a5b`; mutado `246f9ea115312b59035c556b81ccc329f37c14591e28bf33a397cd1221ed989f`.

No verificado: comparación con datos nuevos de mercado o con la base viva; el análisis es una reproducción de CSV cacheados. No se tocaron `grid/sim/*`, `grid/policy.py`, datos, `.env` ni Binance/Testnet. No hice stage, commit ni push.
