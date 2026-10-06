# Reporte Fase 20B-1

## Cierre 20B-1b

Suite focalizada: 60 passed, 1 skipped, 24 warnings in 19.74s. Se ejecutó sin red con ASPLE_OFFLINE=1. Node check: rc=0. UTF-8 sin BOM: PASS.

## Cambios C1-C8

| Punto | Antes | Después | Evidencia archivo:línea |
|---|---|---|---|
| C1 | Prueba de DB vieja usaba un fake que solo emitía descargando | Factor común FAKE_TRAINING_SCRIPT también emite entrenando y guardando; aserciones intactas | tests/test_training_jobs.py: 24 |
| C2 | Hora del modelo era reemplazada por hora de ejecución; UI comparaba texto | Conserva metrics.trained_at o usa run_trained_at; UI compara instantes con tolerancia 2 s | scripts/train_models.py:157; frontend/models.js:19 |
| C3 | Manifiesto global podía asignar la misma fecha a modelos sin entrada | Entrada propia; encabezado solo si existe entrada antigua; si falta entrada, mtime de artefacto del modelo, si falta retorna None. La ruta pasa símbolo/intervalo | models/training_jobs.py:63; api/routes/models_status.py:34 |
| C4 | SQLite serializaba fechas naive que el navegador interpretaba local | `_row` agrega UTC; UI calcula elapsed desde instante y muestra trabajos en fecha local legible | models/training_jobs.py:161; frontend/models.js:10 |
| C5 | Monitor podía sobrescribir cancelado y temporales se borraban antes de esperar | Transición cancelada atómica; terminales del monitor condicionados a estado activo; mata, espera hasta 5 s y limpia después; vencimiento queda registrado y conserva temporales | models/training_jobs.py:319 |
| C6 | PID vivo quedaba activo sin monitor tras reinicio | Se reengancha monitor por PID, procesa PROGRESS y decide listo/error al morir por el log; PID muerto queda interrumpido | models/training_jobs.py:368 |
| C7 | POSIX terminaba solo al padre; taskkill real no probado | POSIX usa grupo de procesos y prueba padre/hijo. Windows real NO VERIFICADO | models/training_jobs.py:147; Windows real: NO VERIFICADO. En la página Modelos, iniciar Entrenar para B; a los 2 minutos pulsar Cancelar. En PowerShell, comprobar `Get-Process python` y buscar temporales con `Get-ChildItem -LiteralPath models/saved -Filter *.tmp`. Esperado: ningún proceso del trabajo ni archivos .tmp. No ejecutar esa prueba desde Codex. |
| C8 | No había prueba de autenticación del endpoint de entrenamiento | Middleware de prueba usa authorize real; sin token HTTP 403, token válido HTTP 201 | tests/test_training_jobs.py:194 |

## Mutaciones

| Mutación | Estado | SHA-256 antes / mutado / restaurado | Resultado |
|---|---|---|---|
| a_single_flight | HECHO | da8d4201c88452ac0325eb9cd0be4d6f15d51cacd9a9d983bb122f837827b53b / 3b791126caa7efaaf525050b64a71d5f4fc12d074d516bab34e547c503a69a58 / da8d4201c88452ac0325eb9cd0be4d6f15d51cacd9a9d983bb122f837827b53b | rc=1; aserción de comportamiento=True; restaurado idéntico=True |
| b_metrics_preservation | HECHO | 52588d7c34e61a2335a74b502c93cab90cbb39161015fd610f5b2c5d13bc2d91 / a65ddd140b8faa2dc1e8d7c5a603610055e08ae65682ee8bc88956f7778b2079 / 52588d7c34e61a2335a74b502c93cab90cbb39161015fd610f5b2c5d13bc2d91 | rc=1; aserción de comportamiento=True; restaurado idéntico=True |
| c_model_a_confirmation | HECHO | 9ac241d4eaaee03ccd939afe75ca763d86e59e17ab3aec2651ba7fd1041cf0ad / 330a4c135e1b5751067de2ca29ec915a3f00cb79c8fb1914a592c420a2bf0253 / 9ac241d4eaaee03ccd939afe75ca763d86e59e17ab3aec2651ba7fd1041cf0ad | rc=1; aserción de comportamiento=True; restaurado idéntico=True |
| d_stop_polling | HECHO | b2f697c22ec2dfd2c2cdc7bf40f110baf77a5dae0526c5251fdc9faaadf1251d / abece4021f2f8fe657b474c0ad0219bea0ed19ed8026dbdcc3b700b56ba63354 / b2f697c22ec2dfd2c2cdc7bf40f110baf77a5dae0526c5251fdc9faaadf1251d | rc=1; aserción de comportamiento=True; restaurado idéntico=True |
| e_utc_timestamp | HECHO | da8d4201c88452ac0325eb9cd0be4d6f15d51cacd9a9d983bb122f837827b53b / 0129b46f0b358c60ee4e498bb5acdf10b4fc15094c1bd066bd6ecf68dee85997 / da8d4201c88452ac0325eb9cd0be4d6f15d51cacd9a9d983bb122f837827b53b | rc=1; aserción de comportamiento=True; restaurado idéntico=True |
| f_model_trained_at | HECHO | 52588d7c34e61a2335a74b502c93cab90cbb39161015fd610f5b2c5d13bc2d91 / 24f2fe0a5d43d29bc099cdc29e7a52de534e674f8c15fa7b9d52b54702ab441e / 52588d7c34e61a2335a74b502c93cab90cbb39161015fd610f5b2c5d13bc2d91 | rc=1; aserción de comportamiento=True; restaurado idéntico=True |

## Resultado literal de pytest

Comando exacto: `C:\APPS\ASPLE_Predictor\asple-predictor\venv\Scripts\python.exe -m pytest tests/test_training_jobs.py tests/test_train_models_metadata.py tests/test_models_page_context.py tests/ui/test_models_page.py tests/test_frontend_encoding.py tests/ui/test_frontend_format.py tests/test_training_smoke.py tests/test_torch_import_order.py tests/test_shadow_model_loading.py -q --basetemp C:\Users\ramon\AppData\Local\Temp\pytest-20b1b` con `ASPLE_OFFLINE=1`.
```text
....................s........................................            [100%]
============================== warnings summary ===============================
venv\Lib\site-packages\starlette\testclient.py:41
  C:\APPS\ASPLE_Predictor\asple-predictor\venv\Lib\site-packages\starlette\testclient.py:41: DeprecationWarning: The anyio.abc.BlockingPortal alias is deprecated, use anyio.from_thread.BlockingPortal instead.
    [], typing.ContextManager[anyio.abc.BlockingPortal]

tests/test_training_jobs.py: 16 warnings
  C:\APPS\ASPLE_Predictor\asple-predictor\venv\Lib\site-packages\httpx\_client.py:680: DeprecationWarning: The 'app' shortcut is now deprecated. Use the explicit style 'transport=WSGITransport(app=...)' instead.
    warnings.warn(message, DeprecationWarning)

tests/ui/test_models_page.py::test_models_page_renders_accumulating_below_minimum
  C:\APPS\ASPLE_Predictor\asple-predictor\venv\Lib\site-packages\websockets\legacy\__init__.py:6: DeprecationWarning: websockets.legacy is deprecated; see https://websockets.readthedocs.io/en/stable/howto/upgrade.html for upgrade instructions
    warnings.warn(  # deprecated in 14.0 - 2024-11-09

tests/ui/test_models_page.py::test_models_page_renders_accumulating_below_minimum
  C:\APPS\ASPLE_Predictor\asple-predictor\venv\Lib\site-packages\uvicorn\protocols\websockets\websockets_impl.py:14: DeprecationWarning: websockets.server.WebSocketServerProtocol is deprecated
    from websockets.server import WebSocketServerProtocol

tests/test_training_smoke.py::test_model_a_train_save_load_predict_and_artifact_version
tests/test_training_smoke.py::test_model_c_train_save_load_predict
  C:\APPS\ASPLE_Predictor\asple-predictor\venv\Lib\site-packages\xgboost\sklearn.py:889: UserWarning: `early_stopping_rounds` in `fit` method is deprecated for better compatibility with scikit-learn, use `early_stopping_rounds` in constructor or`set_params` instead.
    warnings.warn(

tests/test_training_smoke.py::test_model_b_train_save_load_predict
tests/test_training_smoke.py::test_model_b_train_save_load_predict
tests/test_training_smoke.py::test_model_b_train_save_load_predict
  C:\APPS\ASPLE_Predictor\asple-predictor\venv\Lib\site-packages\torch\nn\modules\rnn.py:1358: UserWarning: dropout option adds dropout after all but last recurrent layer, so non-zero dropout expects num_layers greater than 1, but got dropout=0.3 and num_layers=1
    super().__init__("GRU", *args, **kwargs)

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
- Hosts externos bloqueados y registrados por Playwright: cdn.jsdelivr.net, fonts.googleapis.com -
------- Defectos de UI observados y no corregidos: (ninguno observado) --------
60 passed, 1 skipped, 24 warnings in 19.74s
```

No se leyó `.env`, no se entrenó ni descargó nada, no hubo red, y no se modificó `models/saved/*` ni `.bak`/`.pytest_tmp`. Sin stage, commit o push.

### Salidas literales de las mutaciones

```text
a: FAILED tests/test_training_jobs.py::test_two_post_requests_are_serialized_with_201_then_409
1 failed, 2 warnings in 0.62s
b: FAILED tests/test_train_models_metadata.py::test_training_metrics_merge_only_replaces_selected_models
1 failed in 0.33s
c: FAILED tests/test_training_jobs.py::test_model_a_requires_reinforced_confirmation_then_is_accepted
1 failed, 2 warnings in 0.69s
d: FAILED tests/ui/test_models_page.py::test_training_poll_timers_are_cleared_when_job_finishes
1 failed, 2 warnings in 1.13s
e: FAILED tests/test_training_jobs.py::test_naive_job_timestamps_are_utc_and_elapsed_is_local_timezone_safe
1 failed, 1 warning in 0.63s
f: FAILED tests/test_train_models_metadata.py::test_main_preserves_model_returned_trained_at_in_manifest
1 failed in 0.28s
```

En una corrida intermedia también se probó `tests/test_api_auth_phase17d.py`; su prueba `test_all_api_routes_require_token_but_health_stays_open` falló con `RuntimeError: asyncio.run() cannot be called from a running event loop`. La prueba específica C8 de este cambio, con `authorize` real y POST al endpoint, sí pasó con 403/201; el archivo intermedio no forma parte del comando final registrado arriba.

## Fase 20B-1c — detección de PID en Windows

| Cambio | Resultado | Evidencia |
|---|---|---|
| Dependencia | `psutil>=5.9.3` declarada; NOT INSTALLED; real psutil child integration test is skipped. No se instaló nada | requirements.txt:25 |
| Windows con psutil | Consulta `pid_exists` y `Process.create_time`; valida ±5 s contra `started_at` del job. Sin `started_at` comprueba existencia/creación | models/training_jobs.py:44 |
| Windows sin psutil | Devuelve False con advertencia; nunca llama `os.kill` | models/training_jobs.py:44 |
| POSIX | Conserva `os.kill(pid, 0)` | models/training_jobs.py:44 |
| Pruebas | hijo vivo, hijo no terminado, inicio lejano, PID inexistente y fallback sin psutil | tests/test_training_jobs.py:200 |

Comando ejecutado: `ASPLE_OFFLINE=1 C:\APPS\ASPLE_Predictor\asple-predictor\venv\Scripts\python.exe -m pytest tests/test_training_jobs.py tests/test_frontend_encoding.py -q --basetemp C:\Users\ramon\AppData\Local\Temp\pytest-20b1c`.
Resultado literal final: `25 passed, 2 skipped, 17 warnings in 6.43s`. psutil real: **NO VERIFICADO** porque no está instalado en este entorno; la prueba real quedó omitida y no se instaló la dependencia. Repetir tras instalar requisitos: `ASPLE_OFFLINE=1 .\venv\Scripts\python.exe -m pytest tests/test_training_jobs.py -q --basetemp $env:TEMP\pytest-20b1c`.

Mutación Windows `os.kill(pid, 0)` en la rama sin psutil: rc=1 assertion=True sha=a7a7caaf5a1c507128b6e5f6917091c386650dbf176ec864aec886123d846755 / 6140d69298bf941960bc4c40e900c0c904fa33fbf48b5ca0efca4bb2f55484c0 / a7a7caaf5a1c507128b6e5f6917091c386650dbf176ec864aec886123d846755 restored=True.
Salida literal de mutación:
```text
F                                                                        [100%]
================================== FAILURES ===================================
C:\APPS\ASPLE_Predictor\asple-predictor\tests\test_training_jobs.py:244: AssertionError: assert True is False
============================== warnings summary ===============================
venv\Lib\site-packages\starlette\testclient.py:41
  C:\APPS\ASPLE_Predictor\asple-predictor\venv\Lib\site-packages\starlette\testclient.py:41: DeprecationWarning: The anyio.abc.BlockingPortal alias is deprecated, use anyio.from_thread.BlockingPortal instead.
    [], typing.ContextManager[anyio.abc.BlockingPortal]

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
=========================== short test summary info ===========================
FAILED tests/test_training_jobs.py::test_windows_without_psutil_never_kills_child_and_warns
1 failed, 1 warning in 0.63s
```

SHA-256 antes → después de cambios: requirements.txt 8856c3931d255875d6f7571a7a9c7c35273a9586a32121e8ff1d56a8f0943878 → 445810e4324a220dbbf4baa4d408a921ec0fbcc951f8067fdfb03e0a4ec5c512; models/training_jobs.py da8d4201c88452ac0325eb9cd0be4d6f15d51cacd9a9d983bb122f837827b53b → a7a7caaf5a1c507128b6e5f6917091c386650dbf176ec864aec886123d846755; tests/test_training_jobs.py 232a709a3e48178d7de2c23ff583a75d7ca62d38b8691b211a4fd3a3c7c2c188 → 3db4b0f3d86f2f25ddaf4138e25956b1a966d0f12ae1c770a097f6fc844678b6. El archivo mutado se restauró byte a byte según SHA.

No se leyó `.env`, no se instaló psutil ni se usó red; no hubo stage, commit o push.
## Fase 20B-1d — regresiones detectadas el 6-oct (parcial)

| Punto | Estado | Evidencia / limitacion |
|---|---|---|
| R1 | HECHO en código; prueba incluida | `api/routes/models_status.py` usa símbolos activos en `/status` y pasa `symbol`/`interval` a `artifact_trained_at` en page-context. Prueba TestClient añadida a `tests/test_models_status.py`. |
| R2 | HECHO en código; prueba incluida | `api/main.py` registra la excepción de `recover_interrupted()` y continúa. El test de lifespan usa un servicio que falla y conserva `DummyDB`. |
| R3 | PARCIAL | `models/training_jobs.py` captura `OverflowError` tanto en Windows/psutil como en POSIX; test de PID fuera de rango añadido. |

Ejecución focalizada literal antes de ampliar el `except` de Windows: `1 failed, 74 passed, 1 skipped, 20 warnings in 16.49s`. El único fallo fue `test_nonexistent_pid_is_not_alive`: `psutil.pid_exists(9876543210)` elevó `OverflowError`. Se añadió esa excepción después de la ejecución. No se volvió a ejecutar pytest ni las mutaciones por el límite de seis comandos. Mutaciones R1-R3: NO VERIFICADAS. Suite completa no ejecutada.
