# Discrepancias Fase 20B-1

| Punto | Estado | Resultado/evidencia |
|---|---|---|
| C1 | HECHO | Prueba rota corregida sin retirar aserciones; suite: 60 passed, 1 skipped, 24 warnings in 19.74s |
| C2 | HECHO | trained_at del modelo preservado; UI compara timestamps por instante |
| C3 | HECHO | Entrada individual, mtime del artefacto y None; prueba de conteo anterior |
| C4 | HECHO | UTC en JSON, cronómetro con TZ America/Santo_Domingo y fecha local |
| C5 | HECHO | Cancelación prioritaria, finalizaciones condicionales, 20 repeticiones |
| C6 | HECHO | Reenganche de PID vivo, cancelación y recuperación de PID muerto |
| C7 | PARCIAL | Grupo POSIX probado. Windows real: NO VERIFICADO; seguir paso manual de REPORTE_FASE20B1.md |
| C8 | HECHO | authorize real: sin token 403; token configurado 201 |
| Mutaciones a-f | HECHO | Ver tabla de SHA y resultados literales en REPORTE_FASE20B1.md |

Pendiente externo: terminar el ciclo manual de cancelación en Windows. No se probó entrenamiento real ni tráfico de red.

Nota de verificación: una corrida intermedia del archivo `tests/test_api_auth_phase17d.py` encontró `RuntimeError: asyncio.run() cannot be called from a running event loop` en `test_all_api_routes_require_token_but_health_stays_open`. La prueba de C8 añadida a `tests/test_training_jobs.py` usa el `authorize` real y confirmó POST sin token 403 y con token 201; el test preexistente quedó fuera de la corrida final.

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
## Fase 20B-1d — evidencia y pendiente

La primera corrida de las pruebas solicitadas terminó con `1 failed, 74 passed, 1 skipped, 20 warnings in 16.49s`. Falló `tests/test_training_jobs.py::test_nonexistent_pid_is_not_alive` porque `psutil.pid_exists` elevó `OverflowError` para `9876543210`. Se añadió `OverflowError` al manejo Windows y POSIX de `_is_pid_alive`, pero no hubo una segunda corrida dentro del máximo de seis comandos. Las mutaciones de R1 (NameError), R2 (recuperación sin try/except) y R3 (sin OverflowError), incluyendo sus hashes, quedaron NO VERIFICADAS. No se ejecutó la suite completa.
