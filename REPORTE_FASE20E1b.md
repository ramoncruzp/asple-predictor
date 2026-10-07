# Fase 20E-1b — cierre G2, etiqueta de dirección y errores de artefactos

## Alcance

Se preservaron los cambios de 20E-1. Sin stage, commit ni push; sin Binance/Testnet ni lectura de `.env`. Las pruebas usan velas sintéticas pasadas por el constructor real `data/binance_client.py:133`.

## G2 ? Timestamp

El constructor convierte el timestamp de milisegundos a `datetime64[ns, UTC]`; el frame tiene `RangeIndex` y columna temporal `pandas.Timestamp`. `grid/sim/runner.py:386` (Simple) y `:182` (Smart) usan `int(ts)` como segundos Unix, lo que produce el error. El recorte anterior ya reiniciaba el índice y había quitado `KeyError: 0`; persistía este segundo defecto.

La prueba `tests/test_grid_advisor.py:334` pasa ambos símbolos por el mismo builder y forma: mismos dtypes, columnas e índice. Por tanto, no hay una diferencia por símbolo en el constructor. La simulación encuentra el mismo defecto en ambos cuando recorre velas. No se consultó el histórico real para verificar qué condición del rango mostró primero el fallo en ADA: **NO VERIFICADO**.

Traceback completo capturado antes de la normalización:
```text
Traceback (most recent call last):
  File "tests/test_grid_advisor.py:354", in capture
    return original(*args, **kwargs)
  File "grid/sim/runner.py:386", in run_simulation
    now = datetime.fromtimestamp(int(ts), timezone.utc)
TypeError: int() argument must be a string, a bytes-like object or a real number, not 'Timestamp'

Traceback (most recent call last):
  File "tests/test_grid_advisor.py:354", in capture
    return original(*args, **kwargs)
  File "grid/sim/runner.py:182", in run_simulation
    datetime.fromtimestamp(int(ts), timezone.utc), pause_reasons)
TypeError: int() argument must be a string, a bytes-like object or a real number, not 'Timestamp'
```

Arreglo mínimo en `api/routes/grid_advisor.py:271`: tras calcular metadatos y reiniciar el índice, convierte cada timestamp datetime a segundos Unix antes de llamar `run_simulation`. No cambia fórmulas ni parámetros. Se conserva el mensaje de historia disponible. La prueba cubre simple/smart para ADA y XRP y requiere métricas o aviso honesto, sin excepciones Python.

## D2 ? Contexto de dirección

`frontend/models.js:458` muestra siempre Dirección XRPUSDT 1h, aun con volatilidad ADA. La prueba `tests/ui/test_models_page.py:654` obtiene contexto llamando `loadContext`; su respuesta tiene `symbol=ADAUSDT`.

## D3 ? Error al cargar artefactos

`frontend/models.js:257` marca fallos como `unavailable`; el render `frontend/models.js:212` presenta ?no disponible?. ?Sin modelos? queda para moneda no lista. Prueba UI `tests/ui/test_models_page.py:669` comprueba 503 y moneda pendiente.

## J ? Texto

`api/routes/grid_advisor.py:197` presenta ?pronóstico campeón?. `tests/test_grid_advisor.py:298` afirma el texto exacto.

## Pruebas y mutaciones

G2 antes de corregir:
```powershell
.\venv\Scripts\python.exe -m pytest tests/test_grid_advisor.py::test_advisor_simulation_normalizes_real_builder_timestamps_for_ada_and_xrp -m "not live" -q --tb=long --basetemp .pytest_tmp/20e1b-g2-red
```
Salida: `1 failed, 2 warnings`; captura los tracebacks anteriores. Tras corregir: `tests/test_grid_advisor.py` ? `24 passed, 2 warnings in 2.14s`.

UI enfocada: `tests/ui/test_models_page.py -k "training_summary_uses_selected_symbol_and_selected_horizon or volatility_artifact_fetch_failure_is_not_reported_as_no_models" -m "not live" -q` ? `2 passed, 27 deselected, 2 warnings in 1.74s`.

Mutación G2: SHA antes/restaurado `1fc1cfa2bec513ab70643fd29376435ae6f4d8bb80fb9fa37851d45dc6be0af6`; mutado `4322a73bd69b4570d877efa7ae5daa18d92550eb08721a959d908e3613344737`. Prueba falló por `TypeError` en ambos motores; bytes restaurados.

Mutación D2: SHA antes/restaurado `956db79789993e6bff77b6292fde4d44a6ff219167fb25c4e2e4c378ea66c0a2`; mutado `4a6a2b51055ca259498a8245c009c9fbb900cbd876a4614844a32b99172b9df5`. Prueba falló al mostrar Dirección ADAUSDT; bytes restaurados.

Suite no-UI:
```powershell
.\venv\Scripts\python.exe -m pytest tests -m "not live" --ignore=tests/ui -q --basetemp .pytest_tmp/20e1b-final-no-ui
```
```text
939 passed, 1 skipped, 18 deselected, 38 warnings in 135.36s (0:02:15)
```
UI, corrida después de no-UI:
```powershell
.\venv\Scripts\python.exe -m pytest tests/ui -m "not live" -q --basetemp .pytest_tmp/20e1b-final-ui
```
```text
102 passed, 2 warnings in 74.07s (0:01:14)
```
Comparado con 20E-1 (938 no-UI y 101 UI), ambas suites pasan y suman una prueba neta cada una; no hubo fallos nuevos.

## Límites

**NO VERIFICADO:** velas reales de ADA/XRP y causa de mercado específica por la que ADA mostró primero el fallo. No hubo red. Se respetó el mensaje de historia corta. Sin stage, commit ni push.

## Verificación de codificación

Los seis archivos editados para 20E-1b (cuatro de código y dos reportes) son UTF-8 sin BOM, sin CRLF y sin U+FFFD. El escaneo del patrón letra-interrogación-letra dio cero coincidencias en el código y este reporte. REPORTE_FASE20E1.md conserva una coincidencia legítima en la URL de prueba `include_inactive=true`.

## Límite de comandos

Se excedió el máximo solicitado de 20 comandos durante las iteraciones de prueba, mutación, restauración y verificación de codificación. El alcance de archivos se mantuvo.
