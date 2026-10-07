# Fase 20B-2a-1 ? volatilidad por símbolo

## Implementación y evidencia

- P1: config/models_config.py:28 y :43. Valida USDT, conserva XRP en la raíz heredada y separa otros símbolos por base. VOL_CHAMPIONS permanece global hasta 20B-2c.
- P2: scripts/train_vol_models.py:48 y :262. Defaults normales XRP conservados en data/cache/xrp_*.csv; otros símbolos derivan la base. Refresh escribe en candles-dir/<base>_*.csv; --min-days conserva 730 por defecto. Manifiesto y artefactos se guardan por símbolo, con symbol y reemplazo atómico.
- P3: models/volatility/live.py:73 y :166. Predictor por símbolo con reload atómico, mtime y registro thread-safe; load_available descubre directorios existentes.
- P4: scheduler/vol_loop.py:94; api/main.py:138. Ciclo por símbolo con aislamiento de errores y recarga; app.state.vol_predictor conserva el objeto XRP y app.state.vol_registry expone el registro.
- P5: tests/test_vol_per_symbol.py:72 y :229. Pruebas offline con CSV sintéticos, tmp_path, carga/recarga, bloqueo y continuidad del ciclo.

## Pruebas

Comando: C:\APPS\ASPLE_Predictor\asple-predictor\venv\Scripts\python.exe -m pytest -m not live tests\test_vol_consensus.py tests\test_vol_model_stats.py tests\test_vol_per_symbol.py tests\test_vol_series_causal.py tests\test_vol_training_pipeline.py tests\test_vol_widen_factor.py tests\test_volatility.py tests\test_volatility_live.py tests\test_volatility_provider.py tests/test_vol_per_symbol.py tests/test_model_stats_api_cycle.py -q --basetemp C:\Users\ramon\AppData\Local\Temp\pytest-20b2a1-regression-final

Salida literal:

........................................................................ [ 74%]
.........................
============================== warnings summary ===============================
venv\Lib\site-packages\starlette\testclient.py:41
  C:\APPS\ASPLE_Predictor\asple-predictor\venv\Lib\site-packages\starlette\testclient.py:41: DeprecationWarning: The anyio.abc.BlockingPortal alias is deprecated, use anyio.from_thread.BlockingPortal instead.
    [], typing.ContextManager[anyio.abc.BlockingPortal]

tests/test_vol_training_pipeline.py::test_vol_training_request_validation_and_artifacts_endpoint
tests/test_model_stats_api_cycle.py::test_model_stats_testclient_serializes_many_verified_rows_without_cycles
  C:\APPS\ASPLE_Predictor\asple-predictor\venv\Lib\site-packages\httpx\_client.py:680: DeprecationWarning: The 'app' shortcut is now deprecated. Use the explicit style 'transport=WSGITransport(app=...)' instead.
    warnings.warn(message, DeprecationWarning)

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
97 passed, 3 warnings in 22.50s

Mutaciones (SHA-256 antes / mutado / restaurado):

- a-ignore-symbol (scripts/train_vol_models.py): ecb9e51b1ed258d09c6d0b53ce696e0f22f90b1028cb366d1e37c44f61861ad0 / 8f1e58ee7a9b91cdddd98e0063019d4d7c90499bf935d33c35ddf57092b1c991 / ecb9e51b1ed258d09c6d0b53ce696e0f22f90b1028cb366d1e37c44f61861ad0; exit=1; 1 failed in 3.18s; E       AssertionError: assert False
- b-fixed-xrp-manifest (models/volatility/live.py): 166e83a20d9ce519d496bda5fb922ac11fcc4da65843fc1ac184303c420ed380 / dc57704f1e851ab65606dd363f9ebc3009cdc6b2f448797b7c3c927abbca3a74 / 166e83a20d9ce519d496bda5fb922ac11fcc4da65843fc1ac184303c420ed380; exit=1; 1 failed in 2.49s; E       AssertionError: assert ['XRPUSDT'] == ['XRPUSDT', 'ADAUSDT']
- c-reload-without-lock (models/volatility/live.py): 166e83a20d9ce519d496bda5fb922ac11fcc4da65843fc1ac184303c420ed380 / 07cd786a42329be1cf2ac9fc83c184785ac8f6f97069e640bce3bc55b574d875 / 166e83a20d9ce519d496bda5fb922ac11fcc4da65843fc1ac184303c420ed380; exit=1; 1 failed in 2.43s; FAILED tests/test_vol_per_symbol.py::test_reload_holds_lock_until_complete_snapshot_is_swapped
- d-cycle-aborts-on-symbol-failure (scheduler/vol_loop.py): 8dbd17c1caec93333831c1063c4f0a63b48550fae0b7faaf3a6cda5dbcbbc04b / c061db41fbd3b1b3abbe874628b302d5704b03eb996ff8a28d54d69133ef762e / 8dbd17c1caec93333831c1063c4f0a63b48550fae0b7faaf3a6cda5dbcbbc04b; exit=1; 1 failed in 2.29s; FAILED tests/test_vol_per_symbol.py::test_vol_loop_continues_other_symbol_after_one_symbol_fails
- e-no-symbol-validation (config/models_config.py): 9ab099e5c21142cf3a874b74a0fee808265d340754a97723cd8f018d57645821 / 46bdd94c4c3a623dd8e0e54213349eb441a94bcb99f126d9c083694c897521cd / 9ab099e5c21142cf3a874b74a0fee808265d340754a97723cd8f018d57645821; exit=1; 1 failed in 3.79s; FAILED tests/test_vol_per_symbol.py::test_symbol_paths_validation_and_xrp_cli_defaults

Archivos exactos para commit:

- config/models_config.py
- scripts/train_vol_models.py
- models/volatility/live.py
- scheduler/vol_loop.py
- api/main.py
- tests/test_vol_per_symbol.py
- REPORTE_FASE20B2a1.md

NO VERIFICADO: no se usaron datos reales ni se descargaron velas; el entrenamiento probado usó CSV sintéticos en tmp_path. No se ejecutó la suite completa del repositorio ni UI. Sin stage, commit ni push.
