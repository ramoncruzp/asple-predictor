# Fase 20B-2c-1: consenso por símbolo

## Resultado

Implementado el estudio sintético por símbolo, selección de campeones por consenso completo y conservación de la respuesta lista de `/history`. XRP mantiene rutas, campeones globales y `test_is_virgin=false`. No se leyó ni escribió ningún artefacto real de `models/saved/`; las pruebas usaron carpetas temporales y entradas sintéticas.

## Evidencia

- `scripts/vol_consensus_eval.py:297`: argumentos `--symbol`, `--candles`, `--candles-5m`, `--output`; rutas por base, metadatos/sumas SHA-256, división fija 70/15/15 con embargo por horizonte, error contextual cuando el tramo no alcanza 50 filas, y publicación temporal con `.tmp`, candado exclusivo y `os.replace`. La selección de candidatos/Har_range y pesos utiliza VAL; TEST se puntúa tras congelar esa selección. TEST sigue marcado no virgen para XRP y virgen para otros símbolos. Se rehúsa escribir un consenso existente.
- `config/models_config.py:50`: XRP devuelve exactamente `VOL_CHAMPIONS`; otros símbolos aceptan solo reportes propios con los cuatro horizontes y campeones válidos, si no usan campeones globales provisionales.
- `models/volatility/live.py:88`: `VolPredictor` etiqueta campeón desde la selección del símbolo y `reload()` vuelve a cargarla.
- `api/routes/volatility.py:493`: `/history` devuelve siempre la lista y comunica `X-Vol-Selection: provisional|consensus`. `/symbols` distingue campeones XRP, consenso válido y selección provisional.
- `tests/test_vol_consensus_per_symbol.py`: prueba de escritura ADA aislada, rechazo de sobrescritura incluso ante una aparición concurrente, error con datos sintéticos cortos, defaults XRP, validez/fallback de campeones y recarga real del objeto predictor con artefactos temporales.
- `tests/test_vol_api_per_symbol.py`: cobertura de listas de historial y encabezado por símbolo, `/symbols` y compatibilidad XRP.

## Pruebas

Comando: ` .\venv\Scripts\python.exe -m pytest -m "not live" tests/test_vol_*.py tests/test_model_stats*.py -q --basetemp .pytest_tmp/20b2c1 `

Salida literal:

```text
.....................................................................    [100%]
============================== warnings summary ===============================
venv\Lib\site-packages\starlette\testclient.py:41
  C:\APPS\ASPLE_Predictor\asple-predictor\venv\Lib\site-packages\starlette\testclient.py:41: DeprecationWarning: The anyio.abc.BlockingPortal alias is deprecated, use anyio.from_thread.BlockingPortal instead.
    [], typing.ContextManager[anyio.abc.BlockingPortal]

tests/test_vol_api_per_symbol.py::test_symbol_routes_selection_and_xrp_compatibility
tests/test_vol_api_per_symbol.py::test_invalid_and_unloaded_symbols_have_expected_status
tests/test_vol_api_per_symbol.py::test_symbols_endpoint_reports_registry_and_consensus_state
tests/test_vol_api_per_symbol.py::test_artifacts_endpoint_uses_ada_manifest_and_registry_predictor
tests/test_vol_api_per_symbol.py::test_widen_factor_remains_xrp_only
tests/test_vol_api_per_symbol.py::test_ada_valid_consensus_selection_and_history_header
tests/test_vol_training_pipeline.py::test_vol_training_request_validation_and_artifacts_endpoint
tests/test_model_stats_api_cycle.py::test_model_stats_testclient_serializes_many_verified_rows_without_cycles
  C:\APPS\ASPLE_Predictor\asple-predictor\venv\Lib\site-packages\httpx\_client.py:680: DeprecationWarning: The 'app' shortcut is now deprecated. Use the explicit style 'transport=WSGITransport(app=...)' instead.
    warnings.warn(message, DeprecationWarning)

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
69 passed, 9 warnings in 17.42s
```

Las cinco mutaciones fallaron en la prueba de comportamiento esperada y se restauraron byte por byte:

| Mutación | Prueba | SHA-256 antes | SHA-256 mutado | SHA-256 restaurado |
|---|---|---|---|---|
| a. Saltar protección ante consenso aparecido durante escritura | `test_existing_consensus_created_during_evaluation_is_not_overwritten` | `724b7a580a1df993ed2018605232bad39e534e30df6a23ddc375e7fbc6a9c86e` | `5b125b9e1dece3dc81d07b9ccc77b17aaa376247aa62cb90e0e1108b81250589` | igual al anterior |
| b. Forzar `test_is_virgin=True` | `test_xrp_eval_keeps_legacy_paths_and_test_is_not_virgin` | `724b7a580a1df993ed2018605232bad39e534e30df6a23ddc375e7fbc6a9c86e` | `bc1a76edacd0de8af313cb538f68b8ee8c96f264dec728c32fae54d2a9657512` | igual al anterior |
| c. No conservar campeones globales de XRP | `test_symbol_champions_validate_complete_consensus_and_keep_xrp_global` | `4502b8591fafb7a15c356f385dd59b4e5a44f3a5e13578f3e7bd88f9f83252a9` | `132e6a954075e6c6dec4785a271937edad505d09ca2c8db71a90833328baf006` | igual al anterior |
| d. Ignorar `--symbol` al serializar | `test_symbol_study_writes_only_ada_and_never_overwrites` | `724b7a580a1df993ed2018605232bad39e534e30df6a23ddc375e7fbc6a9c86e` | `5ca84a2a484ba328c49354000f84d95c370d0819c3c22334352fedaf910206ba` | igual al anterior |
| e. Volver `/history` un objeto para ADA | `test_ada_valid_consensus_selection_and_history_header` | `a7fdf09b6082d8e2186f0852920d4f584e548e44f1ea7807079fc9122c333f01` | `0b0b09b702ebd44b972d98e34830c794a750e52b96e1def5bff6540161f8cf16` | igual al anterior |

## No verificado

- No se ejecutó el estudio contra velas reales, no se descargaron datos y no se midió su duración; Ramón debe medir el tiempo al ejecutarlo localmente.
- La selección de campeón ADA se basa en el menor MSE de VAL entre modelos elegibles; el bloque TEST solo puntúa resultados y no interviene en esa decisión.
- No se hizo stage, commit ni push.

Archivos previstos para el commit: `api/routes/volatility.py`, `config/models_config.py`, `models/volatility/live.py`, `scripts/vol_consensus_eval.py`, `tests/test_vol_api_per_symbol.py`, `tests/test_vol_consensus_per_symbol.py`, `REPORTE_FASE20B2c1.md`.
