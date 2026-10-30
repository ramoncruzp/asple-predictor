# Reporte Fase 43

## 43a-1 — contrato y registro de motores

Comando ejecutado antes de la prueba:

```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43a1-93c51b2e'; pytest tests/test_meta_engines.py --basetemp="$base"
```

Resultado: 2 passed en 0,24 s.

## 43a-2 — motor fijo por meta y conexión en operador

Comando ejecutado antes de la prueba:

```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43a2-6bd71f43'; pytest tests/test_meta_engines.py tests/test_meta_engines_regression.py tests/test_meta_operator.py --basetemp="$base"
```

Resultado inicial: 98 passed, 2 failed en pruebas nuevas por fixture con `warning_loss_pct` ausente (el bloqueo de pérdida interceptó las ramas de motor). Se corrigió el fixture.

Comando de repetición anotado antes de correr:

```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43a2-rerun-0c159827'; pytest tests/test_meta_engines.py tests/test_meta_engines_regression.py --basetemp="$base"
```

Resultado de repetición: 10 passed en 0,38 s. En la corrida inicial del comando conjunto, `tests/test_meta_operator.py` quedó con 90 passed; las dos fallas fueron únicamente los fixtures nuevos antes de corregir el umbral.

## 43a-3 — selector de motor y etiqueta en Metas

Comando ejecutado antes de la prueba:

```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43a3-b7e6240f'; pytest tests/test_meta_engines.py tests/test_meta_engines_regression.py --basetemp="$base"
```

Resultados iniciales: primera corrida 9 passed, 2 failed por tabla duplicada; segunda corrida 9 passed, 2 failed por test client sin token y por intentar asignar v2 mediante la API genérica de perfil. Se corrigieron las pruebas y la tabla.

Comando de tercera corrida anotado antes de correr:

```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43a3-rerun2-a38c4711'; pytest tests/test_meta_engines.py tests/test_meta_engines_regression.py --basetemp="$base"
```

Resultado de tercera corrida: 11 passed en 1,56 s. `node --check frontend/metas.js` y `git diff --check` pasaron.

## 43b-1 — almacén de velas y lector

Comando ejecutado antes de la prueba:

```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43b1-04f17c3a'; pytest tests/test_market_store.py --basetemp="$base"
```

Resultado inicial: 3 passed, 1 failed por valor esperado incorrecto en el fixture de la vela de 900 s; el valor era 102.

Comando de repetición anotado antes de correr:

```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43b1-rerun-9fa629cd'; pytest tests/test_market_store.py --basetemp="$base"
```

Resultado de repetición: 4 passed en 0,21 s. Se optimizó el conteo de huecos por upsert.

Comando anotado antes de repetir tras optimizar upsert:

```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43b1-final-64be7f03'; pytest tests/test_market_store.py --basetemp="$base"
```

Resultado final inicial: 4 passed en 0,20 s.

Comando anotado antes de repetir tras añadir primeras/últimas velas y aserción WAL:

```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43b1-coverage-f6b82c10'; pytest tests/test_market_store.py --basetemp="$base"
```

Resultado de verificación: 4 passed en 0,25 s. `market_data.db`, `*.db-wal` y `*.db-shm` ya quedan cubiertos por `.gitignore` (`*.db`, `*.db-wal`, `*.db-shm`). La revisión de `deploy/actualizar_s1.sh` muestra respaldo de `asple_predictor.db` únicamente; el script no se modificó.

## 43b-2 — relleno desde Binance Vision

Comando anotado antes de ejecutar la prueba dirigida:

```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43b2-tests-4a4d2c81'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/test_velas_backfill.py --basetemp="$base"
```

Resultado inicial: 3 passed, 1 failed; el fixture esperaba un solo archivo ausente, pero el rango incluye dos archivos previos sin payload (archivo mensual y día anterior). Se ajustó la aserción a la cantidad real de 2.

Comando anotado antes de repetir tras corregir el conteo del fixture:

```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43b2-rerun-90ca2dd1'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/test_velas_backfill.py --basetemp="$base"
```

El directorio de esa repetición ya existía por una invocación anterior que alcanzó pytest tras fallar la preparación; se seleccionó otro directorio temporal nuevo.

Comando anotado antes de la siguiente repetición:

```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43b2-rerun2-8e4b5013'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/test_velas_backfill.py --basetemp="$base"
```

Resultado: 4 passed en 0,21 s.

## 43b-3 y 43b-5 — sincronización horaria y verificador

Comando anotado antes de ejecutar las pruebas enfocadas:

```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43b35-tests-6f370d8d'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/test_market_sync.py tests/test_verificar_velas.py --basetemp="$base"
```

Resultado inicial: 1 passed, 2 failed por expectativas de fixture: el sincronizador excluye la última vela cerrada futura respecto al reloj congelado y la comparación de 7 días ignoraba timestamps antiguos. Se ajustaron los fixtures al límite temporal del contrato.

Comando anotado antes de repetir:

```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43b35-rerun-8a11991c'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/test_market_sync.py tests/test_verificar_velas.py --basetemp="$base"
```

Resultado: 3 passed en 1,02 s.

## 43b-4 — lista inicial de monedas

Comando anotado antes de ejecutar la prueba enfocada:

```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43b4-test-0d4b1ce8'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/test_velas_backfill.py --basetemp="$base"
```

Resultado: 5 passed en 0,24 s.

## 43b-5 — verificador de velas

Comando anotado antes de repetir la prueba enfocada tras hacer bidireccional la comparación API/BD:

```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43b5-rerun-711e0eab'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/test_verificar_velas.py --basetemp="$base"
```

Resultado: 1 passed en 0,94 s.

## 43b-6 — cierre y pasos de despliegue en S1

La base separada se crea únicamente al ejecutar el backfill o al arrancar el job cuando el archivo ya existe. No se desplegó ni se consultó S1.

Comandos que Ramón ejecutará en S1 después del despliegue:

```bash
cd ~/asple-predictor
# 1) después de desplegar con bash deploy/actualizar.sh desde el PC:
venv2/bin/python scripts/velas_backfill.py --db market_data.db --lista-inicial --months 24 --dry-run
venv2/bin/python scripts/velas_backfill.py --db market_data.db --lista-inicial --months 24
# 2) reiniciar para que se registre el job horario
sudo systemctl restart asple-predictor
# 3) verificar
venv2/bin/python scripts/verificar_velas.py --db market_data.db --comparar-api 7
journalctl -u asple-predictor --since "1 hour ago" --no-pager | grep MARKET_SYNC
```

No se observó diferencia intencional en las decisiones v1; la regresión enfocada fue 11 passed para los motores y la ruta histórica del operador. La lista completa de commits de esta entrega se presenta en la respuesta de cierre.

## 43a-4 — interruptor de aprendizaje diario v1

Comando anotado antes de ejecutar las pruebas dirigidas:

```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43a4-tests-59fd82c1'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/test_meta_learning_v1_switch.py tests/test_meta_operator.py --basetemp="$base"
```

Resultado inicial: 93 passed y 1 failed; el endpoint rechazó la falta de token con HTTP 403 (la prueba esperaba 401). Se corrigió la expectativa al contrato real de autorización.

Comando anotado antes de repetir:

```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43a4-rerun-ec992716'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/test_meta_learning_v1_switch.py tests/test_meta_operator.py --basetemp="$base"
```

Resultado: 94 passed en 15,20 s (incluye `tests/test_meta_operator.py`).

Comando anotado antes de validar también la salida del verificador S1 con BD sintética:

```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43a4-final-38f279ab'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/test_meta_learning_v1_switch.py tests/test_meta_operator.py tests/test_meta_s1_diagnostic.py --basetemp="$base"
```

Resultado de esa verificación: 94 passed y 1 failed porque la aserción del fixture juntaba el encabezado y el detalle del interruptor en una sola línea; el verificador los imprime en dos. Se corrigió la aserción para respetar la salida real.

Comando anotado antes de repetir tras ajustar el fixture:

```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43a4-final-rerun-a49e3520'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/test_meta_learning_v1_switch.py tests/test_meta_operator.py tests/test_meta_s1_diagnostic.py --basetemp="$base"
```

La segunda corrida repitió la falla del verificador; se acota ahora el diagnóstico a esa prueba con traceback breve para identificar la aserción exacta.

Comando anotado antes del diagnóstico:

```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43a4-diag-s1-3ab780e1'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/test_meta_s1_diagnostic.py --basetemp="$base" --tb=short
```

El traceback identificó que el fixture no incluía la columna `method_version` que el verificador ya consulta. Se completó el esquema sintético con la columna y versión vigente.

Comando anotado antes de la siguiente verificación:

```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43a4-diag-s1-rerun-9c061d5b'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/test_meta_s1_diagnostic.py --basetemp="$base" --tb=short
```

Resultado: 1 passed en 0,37 s.

Comando anotado antes de la verificación final enfocada, que incluye readiness, UI y contrato del verificador:

```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43a4-final2-073f2db7'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/test_meta_learning_v1_switch.py tests/test_meta_operator.py tests/test_meta_s1_diagnostic.py --basetemp="$base"
```

Resultado final: 95 passed en 20,47 s; un aviso deprecado de Starlette/TestClient. `node --check frontend/metas.js` y `node --check frontend/intelligence.js` pasaron.

La configuración global conserva el valor predeterminado activo por compatibilidad. Después del despliegue, Ramón debe apagarlo en Laboratorio → Reglas globales para suspender las corridas diarias.

Comando anotado antes de la comprobación final de bloqueo en ambos perfiles tras corregir los mensajes mojibake existentes en la misma sección del operador:

```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43a4-profiles-62db4608'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/test_meta_learning_v1_switch.py --basetemp="$base"
```

Resultado: 4 passed en 1,21 s; un aviso deprecado de Starlette/TestClient.

Comando anotado antes de verificar que no se persisten observaciones de toque al estar apagado:

```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43a4-final3-78d4d1ee'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/test_meta_learning_v1_switch.py --basetemp="$base"
```

Resultado: 4 passed en 1,17 s; un aviso deprecado de Starlette/TestClient.

## 43c-0 — arreglos C43-1 a C43-5

Comando anotado antes de probar los papeles, el orden de backfill, la reanudación y el límite de comparación guardado/API:

```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43c0-market-59576b2d'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/test_market_store.py tests/test_market_sync.py tests/test_velas_backfill.py tests/test_verificar_velas.py --basetemp="$base"
```

Comando anotado antes de validar la representación de revisión y los medidores apagados en UI:

```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43c0-ui-b6a1383e'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/ui/test_metas_ui.py tests/ui/test_laboratorio.py -k "active_meta_shows_remaining_time_free_capital_extra_and_spanish_status or lab_learning_meters_explain_global_learning_switch_is_off" --basetemp="$base"
```

Reintento acotado por salida de terminal truncada (sin código de salida visible):
```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43c0-ui-lab-0c729ac4'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/ui/test_laboratorio.py -k lab_learning_meters_explain_global_learning_switch_is_off -q --tb=short --basetemp="$base"
```

Ajuste de alcance de aserción tras fallo inicial: el texto de confirmación oculto conserva la recomendación "Recolectando datos" para cuando se vuelva a encender; las aserciones ahora comprueban exclusivamente el medidor visible.
```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43c0-ui-lab-rerun-45df0d77'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/ui/test_laboratorio.py -k lab_learning_meters_explain_global_learning_switch_is_off -q --tb=short --basetemp="$base"
```

```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43c0-ui-meta-2b9c882c'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/ui/test_metas_ui.py -k active_meta_shows_remaining_time_free_capital_extra_and_spanish_status -q --tb=short --basetemp="$base"
```

Resultados 43c-0: pruebas de almacén/backfill/verificación: 15 passed en 2,36 s. Prueba UI del medidor: 1 passed en 1,40 s; revisión de bitácora: 1 passed en 1,50 s. Un primer intento de UI falló porque la aserción abarcaba el texto de confirmación oculto; se limitó al medidor mostrado y pasó. `node --check frontend/metas.js` y `git diff --check` pasaron.

## 43c-1 — walk-forward causal y métricas base

Comando anotado antes de ejecutar las pruebas sintéticas:
```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43c1-walk-93c001ab'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/test_scorecard_walkforward.py --basetemp="$base"
```

La primera ejecución encontró que el límite del dataset estaba contando una ventana final incompleta como hueco. Se corrigió para descartarla sin sumarla a huecos internos. Comando de reintento:
```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43c1-walk-rerun-b1ee1507'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/test_scorecard_walkforward.py --basetemp="$base"
```

Segundo intento: corregido el conteo del calentamiento, que exigía una vela adicional y clasificaba el primer instante elegible como hueco. Comando:
```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43c1-walk-rerun2-727ac9e0'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/test_scorecard_walkforward.py --basetemp="$base"
```
Resultado 43c-1: 2 passed en 0,06 s. El walk-forward usa solo cierres con timestamp menor que t; omite la ventana final incompleta y cuenta huecos interiores. La salida retiene índices de la ventana objetivo, no copia ni cachea los datos de velas.

## 43c-2/3 — pronósticos de volatilidad y diagnósticos

Comando anotado antes de probar walk-forward causal, sigma acumulada, calibración y métricas:
```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43c23-core-1ffebcdf'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/test_scorecard_walkforward.py --basetemp="$base"
```

Comando anotado antes de probar los diagnósticos de ruptura y el modo seco con SQLite sintética:
```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43c23-synth-845edd52'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/test_scorecard_walkforward.py --basetemp="$base"
```
Resultado 43c-2/3: 4 passed en 0,11 s. Se verificó causalidad, sigma acumulada, calibración, probabilidad de ruptura decreciente al ampliar banda, ER dentro de [0,1], y lectura seca de DB sintética byte-identica sin crear tablas de resultados.

Comando de regresión antes de cerrar la base estadística 43c-2/3:
```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43c23-final-2499ec4f'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/test_scorecard_walkforward.py --basetemp="$base"
```
Resultado de cierre 43c-2/3: 3 passed en 0,06 s.

## 43c-4 — persistencia, CLI y modo seco

Comando anotado antes de verificar que la corrida seca no muta la BD sintética y que el motor estadístico sigue pasando:
```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43c4-runner-60b6a957'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/test_scorecard_walkforward.py --basetemp="$base"
```
Resultado 43c-4: 4 passed en 0,12 s. El CLI muestra los argumentos solicitados. La prueba de `--dry-run` confirmó bytes sin cambios y ausencia de tablas nuevas. No se midió tiempo sobre dos años ni se ejecutó sobre las 21 monedas: solo DB sintéticas están autorizadas.

## 43c-5 — panel de lectura

Comando anotado antes de verificar el endpoint del marcador sin datos y con corrida sintética:
```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43c5-panel-72ab57bb'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/test_intelligence_api.py -k scorecard_panel_readonly_handles_missing_and_synthetic_run --basetemp="$base"
```

El primer test del endpoint usó el header Bearer, pero la API usa `X-API-Token`; se corrige el cliente de prueba. Comando de reintento:
```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43c5-panel-rerun-427f586b'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/test_intelligence_api.py -k scorecard_panel_readonly_handles_missing_and_synthetic_run --basetemp="$base"
```

El reintento ya autenticó; el mismatch restante es la tilde en `todavía` dentro del assert al leer texto legacy mezclado. El test comprueba ahora la frase estable sin tilde. Comando:
```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43c5-panel-rerun2-5fc1b88f'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/test_intelligence_api.py -k scorecard_panel_readonly_handles_missing_and_synthetic_run --basetemp="$base"
```
Resultado 43c-5: endpoint sin BD/sin corrida y endpoint con corrida/veredicto sintéticos: 1 passed en 0,97 s. `node --check frontend/intelligence.js` pasó. El endpoint consulta `market_data.db` con `mode=ro` y la pantalla muestra última corrida, veredictos y factores.


## 43c ? cierre de entrega parcial

Commits: a35240f (43c-0), 28f93d0 (43c-1), 16ac395 (43c-2), 241533a (43c-4), a80476a (43c-5). Sin push.

### L?mites pendientes

- El walk-forward incluye simple, EWMA y una aproximaci?n HAR. No se midieron series GBM de r?gimen ni el rendimiento con dos a?os completos.
- La persistencia y el panel contienen m?tricas de volatilidad, factores y veredictos. Faltan ruptura completa con Brier/deciles/pendiente/factor de colas, direcci?n AUC, correlaciones 21x21 y validaci?n cuantitativa de r?gimen por moneda.
- Falta el job semanal y su interruptor apagado por defecto.
- La cobertura 80 % guardada actualmente es exploratoria, no out-of-sample validada; no se debe interpretar como calibraci?n certificada.
- No se hizo benchmark sint?tico de dos a?os ni medici?n en S1. Esos resultados quedan pendientes.

### Pasos S1 (los ejecutar? Ram?n)

```bash
cd ~/asple-predictor
# despu?s de desplegar con bash deploy/actualizar.sh desde el PC:
venv2/bin/python scripts/velas_backfill.py --db market_data.db --fijar-papeles --dry-run
venv2/bin/python scripts/velas_backfill.py --db market_data.db --fijar-papeles
HTTPS_PROXY=http://127.0.0.1:3128 venv2/bin/python scripts/verificar_velas.py --db market_data.db --comparar-api 7
nohup venv2/bin/python scripts/marcador_pronosticos.py --db market_data.db --registro-db asple_predictor.db > ~/marcador_43c.log 2>&1 &
tail -f ~/marcador_43c.log
```
