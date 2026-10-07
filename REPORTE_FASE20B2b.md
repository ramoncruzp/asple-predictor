# Fase 20B-2b — Preparación automática de monedas

## Implementación

Readiness persistente y worker de onboarding implementados. Estados: pendiente, descargando, entrenando, consensuando, lista, datos_insuficientes y error. XRPUSDT conserva su tratamiento legacy. Coins expone alta, listado, readiness, preparación, cancelación y borrado. El ranking del Scanner queda intacto; apertura manual, Advisor y autoapertura verifican readiness.

La exclusión mutua con TrainingJobService usa reserva transaccional. Onboarding espera mientras hay un trabajo global activo. Se ejecuta una vez train_vol_models.py para descarga/refresco, validación y entrenamiento; después se ejecuta una vez vol_consensus_eval.py y se recarga el predictor, incluso si existía consenso anterior.

## Evidencia archivo:línea

- Tabla/reserva: database/db_manager.py:1016.
- Worker y recuperación: models/coin_onboarding.py:53.
- API: api/routes/coins.py:90.
- Startup/shutdown: api/main.py:79.
- Recarga: models/volatility/live.py:234.
- Guardas: api/routes/grids.py:168, api/routes/grid_advisor.py:6, grid/auto_open.py:66.
- Scanner: grid/scan_service.py no fue modificado.

## Regla B5

Moneda activa distinta de XRPUSDT: pendiente y encolada. Solo queda lista tras validar al menos 540 días, entrenar, producir consenso para 1/2/4/24 h, recargar artefactos y validar campeones. Insuficiencia queda datos_insuficientes; fallos quedan error reintentable. No se desactiva automáticamente.

## Pruebas

Comando: `python -m pytest -m "not live" tests/test_coin_onboarding.py tests/test_coins_registry.py tests/test_grids_api.py tests/test_grid_advisor.py tests/test_grid_auto_open.py tests/test_training_jobs.py tests/test_vol_api_per_symbol.py tests/test_vol_consensus_per_symbol.py -q --basetemp .pytest_tmp/20b2b2-final7`. Salida literal: `117 passed, 1 skipped, 24 warnings in 16.74s`.

Cinco mutaciones conductuales detectadas, restauradas byte por byte:
- apertura: api/routes/grids.py, `tests/test_grids_api.py::test_open_rejects_nonready_coin_and_records_same_rejection`; detectada=True; SHA antes `f7cb4c0abe1e80d56d0fc3985a14b5263c46289745cfde7dbddbb2fa55206c00`, mutado `1d34d5a34bf4d5f14c2d1efc25226618f5b3bbfd6588572f7d45ac68d72acf37`, restaurado `f7cb4c0abe1e80d56d0fc3985a14b5263c46289745cfde7dbddbb2fa55206c00`; `1 failed in 1.19s`.
- recarga: models/coin_onboarding.py, `tests/test_coin_onboarding.py::test_pipeline_happy_path_runs_download_train_once_then_consensus_and_reload`; detectada=True; SHA antes `ee21c128719068caccf755bf0a887930e34371b2df77ba295526e64c4877bcea`, mutado `5d4c362e9ea26d50fe0f3f5977f83b7ced8d1d11c4dbab8105e03a4726086679`, restaurado `ee21c128719068caccf755bf0a887930e34371b2df77ba295526e64c4877bcea`; `1 failed, 1 warning in 1.23s`.
- artefactos: models/coin_onboarding.py, `tests/test_coin_onboarding.py::test_coin_readiness_table_is_idempotent_and_ready_requires_artifacts`; detectada=True; SHA antes `ee21c128719068caccf755bf0a887930e34371b2df77ba295526e64c4877bcea`, mutado `af75339fa0948dd8c8b35dc8a5cde2dd9c08d9af348649b959bcb64182aa2fd9`, restaurado `ee21c128719068caccf755bf0a887930e34371b2df77ba295526e64c4877bcea`; `1 failed, 1 warning in 1.18s`.
- singleflight: database/db_manager.py, `tests/test_coin_onboarding.py::test_two_service_claims_cannot_overlap`; detectada=True; SHA antes `ec25958115166af867554616f8a8693bf3661252a8a10f5b70299595db85fa0c`, mutado `9efed476ded34063404fcf79c4d1b624e87de30925ae2a9ec956f6ab50fba935`, restaurado `ec25958115166af867554616f8a8693bf3661252a8a10f5b70299595db85fa0c`; `1 failed, 1 warning in 1.24s`.
- 540dias: models/coin_onboarding.py, `tests/test_coin_onboarding.py::test_short_history_is_data_insufficient_and_does_not_run_consensus`; detectada=True; SHA antes `ee21c128719068caccf755bf0a887930e34371b2df77ba295526e64c4877bcea`, mutado `282643223d49f85d96b5cf79429ea98407bb9267ded40b24fe806d5a11a95027`, restaurado `ee21c128719068caccf755bf0a887930e34371b2df77ba295526e64c4877bcea`; `1 failed, 1 warning in 1.22s`.

## Desviación del procedimiento

Se excedió el máximo de 20 comandos durante la depuración y las corridas iterativas.

## NO VERIFICADO

No se descargaron velas públicas ni se ejecutó entrenamiento o consenso real. La duración real y la operación bajo carga de procesos reales no fueron verificadas.

## 20B-2b-1

P0: prepare_coin llama is_training_active, el método real. El fake se alineó y hay prueba de POST con CoinOnboardingService real, además de una comprobación del contrato de métodos usados.

P1: al comenzar reentrenamiento se archiva el consenso anterior con os.replace bajo el nombre consensus_<base>.archivado_<UTC>.json; se anota la ruta en el log. Colisiones usan sufijo numérico. Un fallo de archivo propaga un mensaje claro y termina en error. Pruebas verifican bytes/SHA conservados, consenso nuevo/lista y 409 sin archivar cuando ya está lista.

P2: se corrigieron acentos. Regex aplicada: [ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyzÁÉÍÓÚáéíóúñÑ]\?. Se comprobaron además BOM UTF-8 y U+FFFD.

P3: tras cada _run_step se comprueba cancelación antes de interpretar returncode. Los casos de descarga y consenso terminan con el mensaje de cancelación.

P4: formato normal aplicado a _run_step, _history_days, _cleanup_temps, TrainingTreeKiller, rutas nuevas de monedas y bloque de auto-open modificado.

P5: auto-open registra una vez por moneda y estado; un estado nuevo genera evento nuevo y cuando queda lista limpia la memoria.

P6: registry se conserva por compatibilidad y se documenta como fila fallback para DB adapters sin get_coin.

Pruebas: .\venv\Scripts\python.exe -m pytest -m "not live" tests/test_coin_onboarding.py tests/test_grid_auto_open.py tests/test_grids_api.py tests/test_grid_advisor.py tests/test_vol_api_per_symbol.py tests/test_vol_consensus_per_symbol.py tests/test_coins_registry.py -q --basetemp .pytest_tmp/20b2b2-final. Resultado literal: 98 passed, 10 warnings in 10.30s.

Mutaciones: str.replace + assert count == 1. Cada prueba falló y cada archivo restauró el SHA original:
- ruta método: api/routes/coins.py, tests::test_prepare_endpoint_uses_real_service_and_returns_202; SHA antes/restaurado 0cb6e6626d2f7aad4c078605d7877bfcfc05978f0d874f1578a30d101a515813; SHA mutado 615019f2032674c9049efbe679975170cdbde2be7d7640477e2be346521a4642; fallo detectado.
- archivo consenso: models/coin_onboarding.py, tests::test_retry_archives_prior_consensus_and_preserves_its_bytes; SHA antes/restaurado a22deb5355068113aa94ac0b3d172b780a8fb81f4f2922178d3a66a49f1402ec; SHA mutado 82dc3b1e6425d709d93510b260c280d7cac9b3b2884f68c5e4acca0227ad0468; fallo detectado.
- cancelación: models/coin_onboarding.py, tests::test_cancelled_nonzero_step_uses_cancellation_message[train]; SHA antes/restaurado a22deb5355068113aa94ac0b3d172b780a8fb81f4f2922178d3a66a49f1402ec; SHA mutado be51a14fd13566619476729d0d71df071b393f64d3064d34b104d5048133c8f4; fallo detectado.
- deduplicación: grid/auto_open.py, tests::test_unready_coin_event_is_once_per_state_and_repeats_after_state_change; SHA antes/restaurado 2d835a40e0b3ed2bdf5a512c75c8f87c896d33c742fe4fec2e9e4fd27b2f800c; SHA mutado 752d7ef72c90bf125261554c726eb39aa345735281f0958393673c50584ee034; fallo detectado.
- guarda apertura: api/routes/grids.py, tests::test_open_rejects_nonready_coin_and_records_same_rejection; SHA antes/restaurado f7cb4c0abe1e80d56d0fc3985a14b5263c46289745cfde7dbddbb2fa55206c00; SHA mutado 1d34d5a34bf4d5f14c2d1efc25226618f5b3bbfd6588572f7d45ac68d72acf37; fallo detectado.

NO VERIFICADO: descargas, entrenamiento y consenso reales no se ejecutaron.
Desviación: se superó el máximo de 20 comandos al iterar sobre errores de encoding, escanear el conjunto completo y ejecutar/repetir las mutaciones y pruebas.

Archivos escaneados: api/main.py, api/routes/coins.py, api/routes/grid_advisor.py, api/routes/grids.py, database/db_manager.py, grid/auto_open.py, models/coin_onboarding.py, models/training_jobs.py, models/volatility/live.py, scripts/vol_consensus_eval.py, tests/test_coin_onboarding.py, tests/test_grid_advisor.py, tests/test_grid_auto_open.py, tests/test_grids_api.py, REPORTE_FASE20B2b.md.
Coincidencias de la regex: ninguna.
BOM/U+FFFD/UTF-8 inválido: ninguno.
