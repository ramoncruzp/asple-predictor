# Fase 20E-1h — latencia y descarga de velas del Advisor

| Punto | Estado | Evidencia |
|---|---|---|
| H1 | NO MEDIDO | `scripts/diag_20e1h_klines_timing.py`; Binance bloqueado por WinError 10013 durante `/api/v3/ping`, antes de solicitar klines. |
| H2 | HECHO | Caché TTL de 300 s por `(symbol, days)` y candado por clave: `api/routes/grid_advisor.py:36-83`. |
| H3 | HECHO | Inicio acotado al primer cierre horario en rango menos 9 días: `api/routes/grid_advisor.py:329-340`; cliente admite `start_time`: `data/binance_client.py:91-129`. |
| H4 | HECHO, no requerido | Sin paralelismo: estimación posterior al recorte 17 páginas × 0,4 s/página = 6,8 s (<8 s). Es un supuesto, no medición. |
| H5 | HECHO | Advisor 45.000 ms en `frontend/app.js:37`; ANALIZANDO existente en `:389`; UI prueba en `tests/ui/test_grids_browser.py:1014`. Otros `get` conservan 10.000 ms. |
| H6 | HECHO | Fallback registra símbolo, días y traceback en `api/routes/grid_advisor.py:353-354`; `caplog` en `tests/test_grid_advisor.py:392-418`. |
| H7 | HECHO | Cota configurable 25 s (`api/routes/grid_advisor.py:37,59-82`); el cliente verifica la fecha límite entre páginas (`data/binance_client.py:111-112`). No interrumpe una petición HTTP que ya está en curso. |

## Medición y reducción

La medición pública no llegó a descargar klines: el sandbox rechazó la conexión a `api.binance.com` con WinError 10013 al inicializar el cliente público. No se leyó `.env` ni se usaron llaves. El script registra NO MEDIDO y muestra la estimación con el supuesto explícito de 0,4 s por página: 30 días, 9 páginas / 3,6 s; 90 días, 26 páginas / 10,4 s. No son tiempos medidos.

Conteo sobre los CSV locales de caché, 90 días y los rangos ADA/PEPE de la fase 20E-1g; páginas = techo(velas/1000):

| Símbolo | Antes: velas / páginas | Con 9 días previos: velas / páginas | Reducción de velas |
|---|---:|---:|---:|
| ADA | 25.921 / 26 | 16.062 / 17 | 9.859 (38,0 %) |
| PEPE | 25.921 / 26 | 16.215 / 17 | 9.706 (37,4 %) |

Por tanto, con la misma suposición de latencia, el caso de 90 días pasa de 10,4 s a 6,8 s; no implementé paralelismo. La ventana se calcula primero con las velas de 1 h y se recorta a esa ventana más el calentamiento, conservando el calentamiento EWMA.

## Pruebas y mutaciones

Pruebas dirigidas antes de la suite final: `tests/test_grid_advisor.py` × `30 passed, 2 warnings`; UI timeout × `1 passed, 42 deselected, 2 warnings`. La prueba cubre caché caliente, expiración, dos descargas concurrentes, el inicio de rango, finitud de sigma, fallback/log y timeout.

- Mutación H2, sin usar caché: falló `test_5m_frame_cache_reuses_within_ttl_and_expires`. SHA antes/restaurado `c178404312534e84e254bf91cc7d6a7ef127c4ecf6ea3b269495cf5c1180b3b0`; mutado `30fcd4fe5b6c43230b4219cc41b36ab27deafa8d3f9479768bfcd786c8a3544b`.
- Mutación H3, sin recorte de calentamiento: falló `test_5m_simulation_uses_warmed_daily_sigma_and_monitor_cadence`. SHA antes/restaurado `c178404312534e84e254bf91cc7d6a7ef127c4ecf6ea3b269495cf5c1180b3b0`; mutado `8a7823368442637b52a10024c75313ac44fed0bc840799c0a6b34e841c296461`.

NO VERIFICADO: latencia real de Binance y tiempo por página, por bloqueo de red del sandbox. Los tiempos CSV de la fase anterior no son evidencia de latencia real.

Suite final no UI (salida literal): `953 passed, 1 skipped, 18 deselected, 38 warnings in 59.57s`.
Suite final UI (salida literal): `104 passed, 2 warnings in 61.31s`.
Comandos finales: `$env:ASPLE_OFFLINE='1'; .\venv\Scripts\python.exe -m pytest -m "not live" --ignore=tests/ui -q --basetemp $env:TEMP\pytest-20e1h-final-python` y, después, `$env:ASPLE_OFFLINE='1'; .\venv\Scripts\python.exe -m pytest tests/ui -m "not live" -q --basetemp $env:TEMP\pytest-20e1h-final-ui`. Sin stage, commit ni push.
