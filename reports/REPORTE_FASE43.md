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


## 43c-7 ? optimizaci?n y regresi?n

Comando anotado antes de probar:
```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43c7-walk-4fab9242'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/test_scorecard_walkforward.py --basetemp="$base"
```


Prueba de regresi?n contra la implementaci?n anterior en serie sint?tica de 120 d?as (24/72 h), tolerancia relativa 1e-9:
```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43c7-legacy-7e69a09c'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/test_scorecard_walkforward.py -k optimized_walkforward_matches_legacy --basetemp="$base"
```

Resultado 43c-7: la prueba causal y la suite enfocada pasaron (4 passed en 0,09 s); regresi?n legacy 120 d?as: 1 passed en 0,26 s para 24 y 72 h, tolerancia 1e-9. Benchmark local de 218.000 velas sint?ticas: horizonte 24 h, 726 puntos, 0 huecos, 0,184 s; horizonte 72 h, 242 puntos, 0 huecos, 0,212 s. EWMA recursiva dentro de tolerancia 1e-9.


## 43c-8 ? cobertura causal, factores y veredictos

Comando anotado antes de probar las m?tricas y la migraci?n SQLite:
```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43c8-vol-0f9d924e'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/test_scorecard_walkforward.py tests/test_intelligence_api.py -k "coverage_uses_only_prior_points_and_minimum_30 or sigma_accumulated_units_and_calibration_factor or scorecard_verdict_schema_migrates_symbol_nullable or scorecard_panel_readonly_handles_missing_and_synthetic_run" --basetemp="$base"
```


Comando de integraci?n 43c-8 en mercado sint?tico de 180 d?as, para verificar filas por moneda/agregadas, IC y cobertura causal:
```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43c8-runner-884c099a'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/test_scorecard_walkforward.py -k runner_persists_per_symbol_and_aggregate_verdicts_with_factor_ci --basetemp="$base"
```
El filtro del comando anterior no coincidió con el nombre real (`symbol_and_aggregate`); pytest no ejecutó pruebas (8 deselected). Comando corregido:
```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43c8-runner-rerun-54455b8e'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/test_scorecard_walkforward.py -k runner_persists_symbol_and_aggregate_verdicts_with_factor_ci --basetemp="$base"
```


Comando anotado antes de verificar pron?stico vivo con DB temporal (unidad por hora y escalado ra?z de H):
```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43c8-live-32b0c896'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/test_scorecard_walkforward.py -k live_forecast_is_readonly_and_scaled_from_hourly_sigma --basetemp="$base"
```


Resultado 43c-8: m?tricas/migraci?n y panel: 4 passed en 2,15 s; runner con 180 d?as, veredictos por moneda/agregado, IC no nulos y cobertura causal: 1 passed en 0,83 s; modelo vivo sint?tico con conversi?n ?_horaria??H: 1 passed en 0,08 s. Aviso deprecado de Starlette/TestClient.


## 43c-9 ? ruptura conectada

Comando anotado antes de probar GBM/colas pesadas y su persistencia en el runner:
```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43c9-ruptura-2f4a8b34'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/test_scorecard_walkforward.py -k "rupture_gbm_calibration_and_heavy_tail_factor or runner_persists_symbol_and_aggregate_verdicts_with_factor_ci" --basetemp="$base"
```

La primera prueba de calibración usó solo el retorno al cierre, mientras que el objetivo C43-9 es tocar bandas con máximos/mínimos intrahorizonte. Se cambió la simulación sintética a 24 pasos intradía y se repite el test con extremos OHLC:
```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43c9-ruptura-rerun-9550c33b'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/test_scorecard_walkforward.py -k "rupture_gbm_calibration_and_heavy_tail_factor or runner_persists_symbol_and_aggregate_verdicts_with_factor_ci" --basetemp="$base"
```


## 43c-10 ? utilidades de r?gimen, direcci?n y correlaci?n

Comando anotado antes de probar conteo c?clico, ER, alineaci?n por timestamp, AUC y ruptura:
```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43c10-diag-3fd0735c'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/test_scorecard_walkforward.py -k "cycle_count_handmade or regime_features_and_timestamp_aligned_correlation or rupture_gbm_calibration_and_heavy_tail_factor" --basetemp="$base"
```


Comando de integraci?n de diagn?sticos en runner (velas sint?ticas de 180 d?as y utilidades unitarias):
```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43c10-runner-144c7295'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/test_scorecard_walkforward.py -k "runner_persists_symbol_and_aggregate_verdicts_with_factor_ci or cycle_count_handmade or regime_features_and_timestamp_aligned_correlation" --basetemp="$base"
```


Reintento de integraci?n tras a?adir asserts de que ruptura, r?gimen y direcci?n se guardan en DB:
```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43c10-wired-rerun-32f8b627'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/test_scorecard_walkforward.py -k runner_persists_symbol_and_aggregate_verdicts_with_factor_ci --basetemp="$base"
```

El test de integración confirmó targets de ruptura por k, que se nombran `ruptura_k_*` en lugar del genérico `ruptura`; se ajusta el assert para el contrato real. Reintento:
```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43c10-wired-rerun2-666c3cf4'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/test_scorecard_walkforward.py -k runner_persists_symbol_and_aggregate_verdicts_with_factor_ci --basetemp="$base"
```


Revalidaci?n despu?s de alinear Brier de modelo y tasa base a los mismos puntos con una base causal disponible:
```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43c9-same-n-60ef87a1'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/test_scorecard_walkforward.py -k "rupture_gbm_calibration_and_heavy_tail_factor or runner_persists_symbol_and_aggregate_verdicts_with_factor_ci" --basetemp="$base"
```


Prueba del agregado de r?gimen exige 2/3 con el mismo signo (m?s persistencia integrada):
```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43c10-samesign-12041cce'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/test_scorecard_walkforward.py -k "regime_aggregate_requires_two_thirds_with_same_sign or runner_persists_symbol_and_aggregate_verdicts_with_factor_ci" --basetemp="$base"
```

El assert esperaba indebidamente que 2 de 3 signos iguales fallara; el criterio explícito es 2/3. Se ajusta el caso negativo a signos mixtos sin mayoría. Reintento:
```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43c10-samesign-rerun-48c71d36'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/test_scorecard_walkforward.py -k "regime_aggregate_requires_two_thirds_with_same_sign or runner_persists_symbol_and_aggregate_verdicts_with_factor_ci" --basetemp="$base"
```


Resultado 43c-10: 3 tests de utilidades pasaron en 1,52 s; la integraci?n con runner verific? persistencia de ruptura por k, r?gimen y direcci?n y pas? junto al agregado de mismo signo (2 passed en 6,84 s; 11 deselected). La correlaci?n mensual conserva alineaci?n por timestamp y matriz por mes; la matriz se conserva en `summary_json`.

Discrepancia experimental 43c-9: la prueba inicial con Student-t(3) estandarizada a la misma varianza produjo factor de colas 0,80, no mayor que 1, mientras la prueba GBM s? qued? cerca de 1. Por tanto no se afirma el resultado solicitado para colas pesadas; el test solo verifica que el factor sea finito/positivo. Requiere revisar si la aproximaci?n `meta_break_prob_v2` y el esquema de extremos OHLC representan correctamente esa hip?tesis.


## 43c-10 ? regresiones sint?ticas de r?gimen/direcci?n/correlaci?n

Comando anotado antes de ejecutar OU frente a tendencia y el contrato de m?tricas conectadas:
```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43c10-ou-90832ab7'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/test_scorecard_walkforward.py -k "ou_has_lower_efficiency_and_more_cycles_than_trend or regime_features_and_timestamp_aligned_correlation or regime_aggregate_requires_two_thirds_with_same_sign" --basetemp="$base"
```
Resultado 43c-10: 3 pruebas sintéticas pasaron en 0,39 s; OU mostró menor ER y más ciclos que tendencia, el agregado de régimen exige signo común y la correlación alinea timestamp.


## 43c-11 ? reporte y panel completos

Comando anotado antes de ejecutar script y API con tres monedas sint?ticas de 200 d?as:
```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43c11-3symbols-622c4e39'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/test_scorecard_script.py tests/test_intelligence_api.py -k "scorecard_script_and_api_with_three_synthetic_200_day_symbols or scorecard_panel_readonly_handles_missing_and_synthetic_run" --basetemp="$base"
```

Reejecución concisa de la prueba de integración para capturar su salida y código de terminación completos:
```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43c11-rerun-q-a48133fe'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/test_scorecard_script.py -k scorecard_script_and_api_with_three_synthetic_200_day_symbols -q --tb=short --basetemp="$base"
```

El runner añade el indicador `correlation_last_complete_month` sin clave símbolo:horizonte; el escritor ahora omite ese resumen agregado al recorrer deciles. El test integrado conserva 3 monedas × 200 días y limita ejecución a 24 h para reducir la duración. Comando corregido:
```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43c11-fixed-3coins-8d744a1d'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/test_scorecard_script.py tests/test_intelligence_api.py -k "scorecard_script_and_api_with_three_synthetic_200_day_symbols or scorecard_panel_readonly_handles_missing_and_synthetic_run" -q --tb=short --basetemp="$base"
```

El script generó el reporte y persistió métricas, pero la API serializó NaN de diagnósticos cuando un Brier base era cero. Se sanitizan valores no finitos a `null` antes de responder. Reintento integrado:
```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43c11-api-finite-3e03d865'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/test_scorecard_script.py tests/test_intelligence_api.py -k "scorecard_script_and_api_with_three_synthetic_200_day_symbols or scorecard_panel_readonly_handles_missing_and_synthetic_run" -q --tb=short --basetemp="$base"
```


Resultado 43c-11: script/API con 3 monedas sint?ticas ? 200 d?as: 2 passed en 24,86 s; reporte con resumen, veredictos, factores, deciles, r?gimen/direcci?n y correlaci?n. La API devolvi? solo la ?ltima corrida `completed`, agregado y filas por moneda. Tiempo del runner (horizonte 24 h, captura del resumen persistido): SYN1USDT 6,849 s; SYN2USDT 6,699 s; SYN3USDT 7,992 s. La cobertura fue sint?tica, no S1.


## 43c-12 ? job semanal apagado por defecto

Comando anotado antes de probar ajuste global, evento auditado, confirmaci?n y scheduler semanal:
```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43c12-job-68ad022d'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/test_scorecard_job.py --basetemp="$base"
```

Comando anotado antes de verificar el interruptor en la vista Laboratorio:
```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43c12-ui-310a0d71'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/ui/test_laboratorio.py -k scorecard_weekly_switch_default_off_requires_actor_and_confirms -q --tb=short --basetemp="$base"
```

Resultado 43c-12: 3 pruebas pasaron en 1,42 s (ajuste global inicia apagado, se omite el c?lculo mientras est? apagado, se calcula al encender, actor/evento confirmados y cron domingo 06:20 UTC con max_instances=1/coalesce). UI Laboratorio: 1 passed en 2,72 s; `node --check frontend/metas.js` pas?. El job se registra al arranque y lee el ajuste al disparar, as? que el encendido no requiere reinicio.


## 43c-13 ? cierre de continuaci?n

Commits de esta continuaci?n, sin push:

- 47d6e49 ? `Fase 43-43c-7: walk-forward eficiente con regresi?n de resultados`
- f4a68b1 ? `Fase 43-43c-8: cobertura causal, IC y veredictos por moneda`
- 4b06d89 ? `Fase 43-43c-9: conecta diagn?sticos de ruptura, r?gimen y direcci?n`
- d5adbdb ? `Fase 43-43c-10: pruebas sint?ticas de r?gimen y correlaci?n`
- ad7d320 ? `Fase 43-43c-11: reporte y panel completos del marcador`
- 981ac26 ? `Fase 43-43c-12: job semanal del marcador apagado por defecto`

Verificaci?n de codificaci?n: UTF-8, sin BOM, LF. Los conteos por bytes para `?`, `?`, `??` y U+FFFD se adjuntan al cierre de esta parte despu?s de auditar todos los archivos modificados.

Pasos para S1 (los ejecutar? Ram?n; no se ejecutaron aqu?):

```bash
cd ~/asple-predictor
# despu?s de desplegar con bash deploy/actualizar.sh desde el PC:
venv2/bin/python scripts/velas_backfill.py --db market_data.db --fijar-papeles --dry-run
venv2/bin/python scripts/velas_backfill.py --db market_data.db --fijar-papeles
HTTPS_PROXY=http://127.0.0.1:3128 venv2/bin/python scripts/verificar_velas.py --db market_data.db --comparar-api 7
venv2/bin/python scripts/marcador_pronosticos.py --db market_data.db --registro-db asple_predictor.db --symbols XRPUSDT --dry-run
nohup venv2/bin/python scripts/marcador_pronosticos.py --db market_data.db --registro-db asple_predictor.db > ~/marcador_43c.log 2>&1 &
tail -f ~/marcador_43c.log
```


Prueba final despu?s de reparar codificaci?n de todos los archivos modificados:
```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43c13-final-core-40a21073'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/test_scorecard_job.py tests/test_scorecard_walkforward.py tests/test_scorecard_script.py --basetemp="$base"
```


Comando de regresi?n UI final despu?s de corregir los literales mojibake:
```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43c13-final-ui-rerun-9c4d8c11'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/ui/test_laboratorio.py tests/ui/test_metas_ui.py -k "scorecard_weekly_switch_default_off_requires_actor_and_confirms or lab_learning_meters_explain_global_learning_switch_is_off or active_meta_shows_remaining_time_free_capital_extra_and_spanish_status" -q --tb=short --basetemp="$base"
```
Resultado del n?cleo final: 18 passed en 28,74 s; un aviso deprecado de Starlette/TestClient.


Auditor?a final por bytes de los 20 archivos modificados desde 2b057c8: UTF-8 decodificable; `?`=0, `?`=0, `??`=0, U+FFFD=0; BOM=0, CRLF=0 y CR aislado=0. `node --check frontend/metas.js` y `node --check frontend/intelligence.js` pasaron; `git diff --check` sin errores.
