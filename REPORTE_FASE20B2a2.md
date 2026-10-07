# Fase 20B-2a-2: API de volatilidad por símbolo

## Cambios y evidencia

- P0a. Mensaje UTF-8 y prueba: config/models_config.py:28; prueba: tests/test_vol_per_symbol.py:83. Se revisaron los siete archivos del commit 2256256. Los únicos signos de interrogación dentro de palabras eran en config/models_config.py y tests/test_vol_per_symbol.py; se corrigieron.
- P0b. --days/--min-days y validación: scripts/train_vol_models.py:55; prueba de 730 días de descarga, 540 mínimo, defaults y rechazo de 800: tests/test_vol_per_symbol.py:125.
- P1. vol_consensus_path: config/models_config.py:49.
- P2. latest_by_symbol y conservación de latest para XRP: scheduler/vol_loop.py:84. get_latest_vol_forecasts no incluye el precio actual; VolLoop lo publica por símbolo.
- P3. Normalización/422/registro: api/routes/volatility.py:79; precio por símbolo: api/routes/volatility.py:98. Forecast, model-stats, Battle e history admiten predictores listos y ponen selection provisional en símbolos no XRP. XRP no recibe esos campos.
- P3. Catálogo symbols: api/routes/volatility.py:468. Widen-factor sigue solo XRP; ADA 404: tests/test_vol_api_per_symbol.py:76. Compute/apply sin cambios.
- P4. Artefactos y predictor desde registry: api/routes/models_status.py:11. Default y forma XRP preservados.
- Pruebas TestClient con datos sintéticos: tests/test_vol_api_per_symbol.py:45.

## Pruebas

Comando: python -m pytest -m "not live" tests/test_vol_per_symbol.py tests/test_vol_training_pipeline.py tests/test_vol_consensus.py tests/test_vol_model_stats.py tests/test_vol_series_causal.py tests/test_vol_widen_factor.py tests/test_volatility.py tests/test_volatility_live.py tests/test_volatility_provider.py tests/test_model_stats_api_cycle.py tests/test_vol_api_per_symbol.py -q --basetemp $env:TEMP\pytest-20b2a2-final

Salida literal:
```
........................................................................ [ 75%]
........................                                                 [100%]
============================== warnings summary ===============================
venv\Lib\site-packages\starlette\testclient.py:41
  C:\APPS\ASPLE_Predictor\asple-predictor\venv\Lib\site-packages\starlette\testclient.py:41: DeprecationWarning: The anyio.abc.BlockingPortal alias is deprecated, use anyio.from_thread.BlockingPortal instead.
    [], typing.ContextManager[typing.Any]

tests/test_vol_training_pipeline.py::test_vol_training_request_validation_and_artifacts_endpoint
tests/test_model_stats_api_cycle.py::test_model_stats_testclient_serializes_many_verified_rows_without_cycles
tests/test_vol_api_per_symbol.py::test_symbol_routes_selection_and_xrp_compatibility
tests/test_vol_api_per_symbol.py::test_invalid_and_unloaded_symbols_have_expected_status
tests/test_vol_api_per_symbol.py::test_symbols_endpoint_reports_registry_and_consensus_state
tests/test_vol_api_per_symbol.py::test_artifacts_endpoint_uses_ada_manifest_and_registry_predictor
tests/test_vol_api_per_symbol.py::test_widen_factor_remains_xrp_only
  C:\APPS\ASPLE_Predictor\asple-predictor\venv\Lib\site-packages\httpx\_client.py:680: DeprecationWarning: The 'app' shortcut is now deprecated. Use the explicit style 'transport=WSGITransport(app=...)' instead.
    warnings.warn(message, DeprecationWarning)

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html --
96 passed, 8 warnings in 20.98s
```

## Mutaciones

Todas usaron str.replace con assert count==1, fallaron en la prueba de comportamiento y restauraron el SHA previo.

| Mutación | Archivo | SHA antes | SHA mutado | SHA restaurado | Estado |
|---|---|---|---|---|---|
| Quitar 422 | api/routes/volatility.py | 15479d119621053af259150dc9b522da43ad03a99a7f7dbeb9d831ed89990421 | e1876a0b200a3ad732370c2ee6fa19f031619c5fe14437baabc717dba1177fda | 15479d119621053af259150dc9b522da43ad03a99a7f7dbeb9d831ed89990421 | Detectada (1 failed) |
| Omitir provisional | api/routes/volatility.py | 15479d119621053af259150dc9b522da43ad03a99a7f7dbeb9d831ed89990421 | aac5393309a21f6772f0f5dd96a66f11a79b0ed5b64b40263ea0f0397a15c7ef | 15479d119621053af259150dc9b522da43ad03a99a7f7dbeb9d831ed89990421 | Detectada (1 failed) |
| Forzar consenso XRP | config/models_config.py | fbcbc6037523de2339aba34b28c3d7c3eef8ab35e42706529230f1438955ac97 | d7c578aca32edddb80124bbe94d4679fdbfab4436f3ba68cdfe5a9009ad9ceaa | fbcbc6037523de2339aba34b28c3d7c3eef8ab35e42706529230f1438955ac97 | Detectada (1 failed) |
| Marcar ADA champions | api/routes/volatility.py | 15479d119621053af259150dc9b522da43ad03a99a7f7dbeb9d831ed89990421 | 40b0ea863677181dbfac324c1a8439dd28ec07b855b4d482068df13fcfb1409f | 15479d119621053af259150dc9b522da43ad03a99a7f7dbeb9d831ed89990421 | Detectada (1 failed) |
| Reusar precio XRP | api/routes/volatility.py | 15479d119621053af259150dc9b522da43ad03a99a7f7dbeb9d831ed89990421 | e3cb4c80f23396a75da751f1b6774a31c36862840e9d4222ac795a41a2895988 | 15479d119621053af259150dc9b522da43ad03a99a7f7dbeb9d831ed89990421 | Detectada (1 failed) |

## Limitaciones

- Otros símbolos utilizan campeones globales de XRP como selección provisional y no se les atribuye consenso propio.
- TestClient y artefactos sintéticos verificados. NO VERIFICADO: artefactos reales o mercado de símbolos distintos de XRP; frontend queda fuera de alcance.

## Archivos para commit posterior

- api/routes/volatility.py
- api/routes/models_status.py
- config/models_config.py
- scheduler/vol_loop.py
- scripts/train_vol_models.py
- tests/test_vol_per_symbol.py
- tests/test_vol_api_per_symbol.py
- REPORTE_FASE20B2a2.md
