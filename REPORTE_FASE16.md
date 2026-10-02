# Fase 16 — Scanner explicable y API de grids

## Implementación

- `grid/scanner.py:31-120` implementa `score_symbol` sin I/O. Las puertas duras exponen nombre, medido, umbral, `passed` y motivo; ninguna puntuación sustituye esas puertas. La estructura usa `suggest_structure`; sigma se calcula de retornos históricos 1 h para describir variación pasada. No se cargan modelos A–D ni se emiten probabilidades.
- Los defaults viven en `config/settings.py:6-18,42-64` y se documentan también en `.env.example`. Valores iniciales: volumen quote 24 h ≥ **1.000.000 USDT**, spread ≤ **15 bps**, separación ≥ **0,8 %**, comisión **0,1 % por lado**, historia **30 días** con al menos 95 % de barras, TTL **300 s**, pausa entre llamadas **0,2 s**, **2** reintentos del servicio y plazo total **60 s**. La celda mínima es `max(minNotional × 1,1, 5,5 USDT)`.
- Pesos visibles y configurables (se normalizan para sumar 1): holgura neta de costo **0,35**; spread/volumen **0,20**; oscilación histórica (cruces de niveles y porcentaje del tiempo dentro del rango) **0,35**; penalización descriptiva ER 30 d **0,10**. Son defaults operativos transparentes, no parámetros calibrados para prometer rentabilidad. Las contribuciones, pesos, mediciones y explicaciones aparecen por componente.
- `grid/scan_service.py:18-157` usa cliente público anónimo, recorre Coin Registry, aísla errores por símbolo, aplica reintentos/ritmo/plazo y caché en memoria con TTL. `with_sim=true` ejecuta `run_simulation` sobre 90 días y marca el resultado `histórico, no predictivo`. Cada fila declara fuente y advertencia de diferencia de ejecución Testnet.
- `api/routes/grids.py:53-210` agrega `POST /api/grids/scan` y `/api/grids/open`, esquemas cerrados (campos extra rechazados), límites y validación de parámetros. `dry_run=true` arma niveles/celdas/guardas sin invocar Testnet; una apertura requiere `dry_run=false` y `confirm=true`, cliente confirmado Testnet y `GridEngine.create_grid`. Los rechazos de las guardas de apertura quedan como `GRID_OPEN_REJECTED`; aperturas completadas como `GRID_OPEN_API` con parámetros y snapshot del scan.
- El API requiere `X-API-Token` si `GRID_API_TOKEN` está configurado; de lo contrario acepta solo direcciones IP loopback. `api/main.py:77-124,134` publica cliente/engine en `app.state`, crea el cliente de mercado sin credenciales, registra el router y arranca/detiene el job dentro del `lifespan`. No se inicia tarea al importar el módulo raíz.
- `grid/auto_open.py:12-112` registra job APScheduler dentro del ciclo de vida de la app. Está apagado por defecto y vuelve a leer `SCANNER_AUTO_OPEN` en cada pasada. Exige Testnet, score mínimo, límite por pasada, tope diario y capacidad global. La clave de slot persistida (`AUTO_OPEN_STARTED`) evita repetir una pasada tras reinicio; eventos incluyen scan, motivos y parámetros.

## Política de símbolos y límites

La API y auto-open rechazan un segundo grid del símbolo si ya hay uno OPENING/ACTIVE/PAUSED/CLOSING/HOLDING, aunque la estrategia difiera. El motor actual (`grid/engine.py:267-277`) comprueba duplicado por símbolo **y estrategia** entre estados OPENING/ACTIVE/PAUSED/CLOSING; por tanto, la API elige deliberadamente la política más restrictiva y no cambia la política del motor/CLI.

`target_pct` y `target_usdt` requieren `strategy=smart`: el motor simple actualmente solo admite `max_days` en parámetros (`grid/engine.py:269-285`). Para auto-open, use `SCANNER_AUTO_OPEN_STRATEGY=smart` si configura un objetivo; `max_days` funciona con simple. Esta limitación está registrada en `DISCREPANCIAS_FASE16.md`.

El scanner describe factibilidad/costos y oscilación pasada; no mide rentabilidad futura ni predice dirección/régimen. El libro, volumen y velas usados para puntuar son públicos de Binance Spot. Testnet usa precios/libro separados y su liquidez no representa el mercado real; todas las respuestas conservan la advertencia correspondiente.

## Verificación

Pruebas enfocadas de los cuatro módulos: **23 passed** (`tests/test_grid_scanner.py`, `test_grid_scan_service.py`, `test_grids_api.py`, `test_grid_auto_open.py`; `ASPLE_OFFLINE=1`, `--basetemp data/cache/pytest_16_focus_complete`). Cubren puertas, determinismo, pesos, net edge positivo, celda de 10 USDT con filtros compatibles, errores/caché/plazo, simulación histórica, autenticación, rutas, dry-run, confirmación, duplicado, límites, saldo, parámetros objetivo, auto-open apagado, topes, Testnet y replay del slot.

Campaña temporal restaurada byte por byte, con `git diff --check` limpio: **6/6 mutaciones muertas**: omitir `confirm`, ignorar tope simultáneo, abrir con dry-run, admitir símbolo con spread fallido, auto-open con cliente no Testnet y omitir token.

Suite offline completa ejecutada al cierre: `ASPLE_OFFLINE=1 .\venv\Scripts\python.exe -m pytest tests -p no:cacheprovider -q -m "not live" --basetemp data/cache/pytest_16_final_complete` → **557 passed, 18 deselected, 7 warnings, 41.16 s**. Los warnings son los avisos ya existentes de colección de `TestnetOrderError`, `early_stopping_rounds` de XGBoost y dropout en GRU de una capa.


## Límites conocidos

- La autenticación por loopback no es segura detrás de un reverse proxy en la misma máquina: la aplicación ve las conexiones como `127.0.0.1`. En despliegue, `GRID_API_TOKEN` es obligatorio (Fase 11).
- El motor admite un grid simple y uno smart en el mismo símbolo, pero la API aplica una sola apertura por símbolo. Dos grids de la misma moneda con niveles idénticos pueden cruzar sus propias órdenes (BUY en el precio de una SELL); sin STP configurado no deben usarse para comparar variantes en la misma moneda.
- Algunas partes del score se saturan pronto (cruces/12, ER de 30 días y holgura neta/0,2 %), por lo que discrimina poco entre símbolos normales. `SCANNER_AUTO_OPEN_MIN_SCORE=0.6` no está calibrado. No activar auto-open hasta revisar una distribución real de scores.

## Correcciones Fase 16-2

- `grid/scan_service.py:70-75` normaliza una vez bid/ask del cliente público a `Decimal`; `api/routes/grids.py:20-21,152-167` calcula el punto medio con `_mid` y construye el dry-run con el cliente raw que devuelve precios string. Test sintético de forma pública real: scan elegible, dry-run 200 y apertura con los tres valores exactos del plan.
- `api/routes/grids.py:144-145,190-193` exige rango y niveles explícitos al ejecutar y pasa esos mismos valores a `GridEngine.create_grid`; elimina cálculo de estructura en la rama real.
- `api/main.py` quedó normalizado a LF sin cambiar el contenido. `git diff --stat -- api/main.py`: 63 líneas (39 insertions, 24 deletions); `--ignore-space-at-eol`: mismo resultado.
- `tests/test_grids_api.py` fija los textos de error de `GridEngine.create_grid` para grid duplicado y límite simultáneo.
- `api/main.py`: LF comprobado (0 CRLF y 162 LF). `git diff --stat -- api/main.py` y `git diff --ignore-space-at-eol --stat -- api/main.py` dieron ambos 63 líneas (39 insertions, 24 deletions).
- Pruebas enfocadas de los cuatro módulos (`test_grid_scanner.py`, `test_grid_scan_service.py`, `test_grids_api.py`, `test_grid_auto_open.py`): **26 passed**.
- Mutaciones temporales, restauradas byte por byte y verificadas tras cada ejecución: **8/8 muertas** (confirmación, límite simultáneo, dry-run que llegaría a abrir, puerta de spread, guardia Testnet de auto-open, token, conversión Decimal de precios públicos y rechazo de ejecución sin plan explícito).
- Suite offline completa: `ASPLE_OFFLINE=1 .\venv\Scripts\python.exe -m pytest tests -p no:cacheprovider -q -m "not live" --basetemp data/cache/pytest_16_2_full_confirm` → **560 passed, 18 deselected, 7 warnings, 42.70 s**. Warnings: 2 de colección de `TestnetOrderError`, 2 deprecations de XGBoost y 3 avisos de dropout GRU.
- Sin llamadas reales a Binance/Testnet, sin commit/push; no se modificaron `grid/engine.py` ni el `main.py` raíz.
