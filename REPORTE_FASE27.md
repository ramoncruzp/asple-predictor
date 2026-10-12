# REPORTE FASE 27

## Base y alcance

- HEAD base: `5139b50` (comprobado antes de editar).
- `git status --short` en preflight: vacio.
- Sin red real, sin add/commit/push.
- Cambios limitados a los seis archivos autorizados y este reporte.

## A. Pulido del resumen diario

| Item | Estado | Resultado |
|---|---|---|
| A1 | HECHO | Un sondeo exitoso siempre persiste `probe_down=False`; solo envia recuperacion si el estado previo era caido. Prueba valida OK inicial, caida al umbral y recuperacion: `tests/test_alert_dispatcher.py:432`. Implementacion: `alerts/dispatcher.py:360`. |
| A2 | HECHO | Los timestamps sin zona se interpretan como UTC via `_as_utc`, se convierten a `America/Santo_Domingo` y se formatean como `YYYY-MM-DD HH:MM (hora local)`; `None` conserva `sin datos`. Prueba fija 22:54 UTC -> 18:54 local: `tests/test_alert_dispatcher.py:440`. Implementacion: `alerts/dispatcher.py:49`, `alerts/dispatcher.py:456`. |
| A3 | HECHO | PnL usa `Decimal`, redondeo `ROUND_HALF_UP`, signo explicito y cuatro decimales fijos. Prueba cubre cero, positivo, negativo y `1e-7`: `tests/test_alert_formatters.py:91`. Implementacion: `alerts/formatters.py:33`, `alerts/formatters.py:183`; sumas Decimal en `alerts/dispatcher.py:442`. |

Pruebas de alertas: `45 passed` (`tests/test_alert_dispatcher.py` y `tests/test_alert_formatters.py`).

### Mutaciones conductuales A

Cada mutacion fallo su prueba, y cada archivo volvio byte por byte al SHA anterior (`restored == before`).

| Item | SHA antes/restaurado | SHA mutado | Salida |
|---|---|---|---|
| A1 | `68059f50afc228876a20f856947c7b008093edb870818525654d729f01aef510` | `69f50b430da8e51ef189d64b7f66a1688aea3943f986d31703667715d581071b` | `FAILED test_probe_state_is_explicitly_healthy_after_success_and_tracks_down_recovery` |
| A2 | `68059f50afc228876a20f856947c7b008093edb870818525654d729f01aef510` | `fe3cfce6fcaaef7e1b6d92fefe92fec078ba4d8525497d1a155bcca16e764fa2` | `FAILED test_probe_state_is_explicitly_healthy_after_success_and_tracks_down_recovery` |
| A3 | `ed64cc394d37718572f9e603600904f9129e595a6fd8f01cdb99f34391df538b` | `0e5cf8271bfc095c7929149b64e03630b696a915f1adbbb4b83f7e4f6c96aaa3` | `FAILED test_format_digest_formats_local_times_and_signed_four_decimal_pnl` |

## B. Dobles de prueba

| Test | Estado | Evidencia |
|---|---|---|
| Advisor persisted factor | HECHO | `tests/test_grid_advisor.py:232` ahora implementa `get_readiness()` y devuelve `None`, igual al retorno real neutro. |
| Lifespan sin credenciales Testnet | HECHO | `tests/test_grid_monitor.py:801` agrega `list_app_flag_overrides()` -> `[]`, coherente con `DBManager`. |
| Fallo al iniciar coin onboarding | HECHO | `tests/test_grid_monitor.py:866` agrega `list_app_flag_overrides()` -> `[]`. |

Antes, con `--basetemp` local: `3 failed in 2.03s`; fallos literales: `AttributeError: 'types.SimpleNamespace' object has no attribute 'get_readiness'` y dos `AttributeError: 'DummyDB' object has no attribute 'list_app_flag_overrides'`. Despues: `3 passed in 5.28s`. Las pruebas completas de los cuatro archivos pedidos pasan: `117 passed, 2 warnings in 14.56s`.

## C. Inventario Git de solo lectura

Escaneo de `git log --all`: 76 commits, incluyendo ramas visibles `main`, `origin` y `origin/main`; se revisaron blobs de cada arbol historico y archivos borrados. El escaneo encontro dos valores Binance real de 64 caracteres en dos commits historicos de `.env.example`; ambos estan ausentes de HEAD y se deben rotar. No se imprime ningun valor ni fragmento.

### Literales con nombres de credenciales

| SHA corto de introduccion | Fecha | Archivo:linea | Nombre/tipo | Longitud | Estado en HEAD |
|---|---|---|---|---:|---|
| `68f57ca` | 2026-09-21 | `.env.example:1` | `BINANCE_API_KEY`, Binance real, patron de llave | 64 | Solo historial; se repite en `7e269f3` |
| `68f57ca` | 2026-09-21 | `.env.example:2` | `BINANCE_API_SECRET`, Binance real, patron de llave | 64 | Solo historial; se repite en `7e269f3` |
| `7e269f3` | 2026-09-27 | `.env.example:1` | `BINANCE_API_KEY`, Binance real, patron de llave | 64 | Solo historial |
| `7e269f3` | 2026-09-27 | `.env.example:2` | `BINANCE_API_SECRET`, Binance real, patron de llave | 64 | Solo historial |
| `68f57ca` | 2026-09-21 | `config/settings.py:10` | `binance_api_key`, placeholder de configuracion | 15 | Si; placeholder |
| `68f57ca` | 2026-09-21 | `config/settings.py:11` | `binance_api_secret`, placeholder de configuracion | 18 | Si; placeholder |
| `48b660f` | 2026-09-29 | `config/settings.py:12` | `testnet_api_key`, placeholder Testnet | 23 | Si; placeholder |
| `48b660f` | 2026-09-29 | `config/settings.py:13` | `testnet_api_secret`, placeholder Testnet | 22 | Si; placeholder |
| `74a95d0` | 2026-10-04 | `tests/test_grid_account_api.py:116` | `TESTNET_API_KEY`, literal de fixture de prueba | 23 | Si; archivo de tests |
| `74a95d0` | 2026-10-04 | `tests/test_grid_account_api.py:117` | `TESTNET_SECRET`, literal de fixture de prueba | 19 | Si; archivo de tests |
| `4f83150` | 2026-10-09 | `tests/test_app_settings_api.py:14` | `GRID_API_TOKEN`, literal de fixture | 10 | Si; archivo de tests |
| `5139b50` | 2026-10-11 | `tests/test_alert_dispatcher.py:32` | `TELEGRAM_BOT_TOKEN`, literal corto de fixture; no coincide con el patron Telegram | 7 | Si; archivo de tests |

Los valores de llave Binance real de `.env.example` se repiten en dos commits y no estan en HEAD. Los valores actuales de `config/settings.py` son placeholders. Los literales Testnet de `tests/test_grid_account_api.py` se repiten en historial/HEAD y se clasifican como fixtures por su ruta de prueba; no tienen 64 caracteres.

### Archivos por nombre/tipo

`.env.example` aparece en siete versiones. El hallazgo de archivo es por nombre (linea 0; longitud indicada es tamano del blob, no del secreto). La version que permanece en HEAD contiene placeholders; dos commits historicos de la plantilla contienen los dos valores Binance de 64 caracteres listados arriba. No se encontraron `.env` reales, archivos `*.key`/`*.pem`, ni copias de bases de datos.

| SHA corto | Fecha | Archivo:linea | Tipo | Tamano bytes | HEAD |
|---|---|---|---|---:|---|
| `68f57ca` | 2026-09-21 | `.env.example:0` | plantilla de entorno | 309 | No |
| `665930d` | 2026-09-27 | `.env.example:0` | plantilla de entorno | 214 | No |
| `48b660f` | 2026-09-29 | `.env.example:0` | plantilla de entorno | 292 | No |
| `3525133` | 2026-09-29 | `.env.example:0` | plantilla de entorno | 389 | No |
| `2d6a496` | 2026-09-29 | `.env.example:0` | plantilla de entorno | 469 | No |
| `bd2c533` | 2026-09-29 | `.env.example:0` | plantilla de entorno | 494 | No |
| `b7e9329` | 2026-10-02 | `.env.example:0` | plantilla de entorno | 1167 | Si |

Resultados de patrones: 2 valores Binance real de 64 caracteres (cada uno aparece en dos commits); 0 tokens Telegram `8-10 digitos:35 caracteres`; 0 hallazgos `*.key`, `*.pem` o copias de base de datos.

### Que rotar

| Grupo | Resultado del historial |
|---|---|
| Binance real | ROTAR API key y API secret: ambos valores de 64 caracteres aparecen en historial publico (commits `68f57ca` y `7e269f3`) aunque no estan en HEAD. |
| Binance Testnet | Ninguna llave de 64 caracteres identificada. Hay dos literales de fixture en `tests/`; confirmar que no se reutilizaron como credenciales reales. |
| Telegram | Ningun token con patron operativo; solo un literal corto de test. |
| Otro | `GRID_API_TOKEN` aparece como literal de test; no se identifico credencial operativa. |

## Cierre y auditoria

- HECHO: pruebas dirigidas A y B; mutaciones A1-A3 detectadas y restauradas por SHA.
- NO HECHO: la suite offline completa se inicio una vez con `ASPLE_OFFLINE=1` y `-m "not live"`. El proceso continuo fuera de la ventana inicial del ejecutor; su salida final y exit code no quedaron disponibles para capturarlos. Por eso el resultado global y la cifra de pasadas quedan NO VERIFICADOS; no se reinicio la suite.
- Evidencia posterior de coleccion, sin ejecutar pruebas: 1271 IDs offline y 18 live deseleccionados; 0 IDs actuales coinciden con entradas persistidas de fallos en `.pytest_cache`. Esto no permite recuperar las cifras passed/xfail ni el exit code de la ejecucion completa.
- NO HECHO: no afirmar `0 failed` ni mas de 1254 pasadas sin la salida final. Los SHA-256 de mutaciones en este reporte son hashes de evidencia, no valores de credenciales.
- Archivos para un unico commit: `alerts/dispatcher.py`, `alerts/formatters.py`, `tests/test_alert_dispatcher.py`, `tests/test_alert_formatters.py`, `tests/test_grid_advisor.py`, `tests/test_grid_monitor.py`, `REPORTE_FASE27.md`.
- No se hizo commit.
