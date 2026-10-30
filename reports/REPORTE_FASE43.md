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

### Límites pendientes

- El walk-forward incluye simple, EWMA y una aproximación HAR. No se midieron series GBM de régimen ni el rendimiento con dos años completos.
- La persistencia y el panel contienen métricas de volatilidad, factores y veredictos. Faltan ruptura completa con Brier/deciles/pendiente/factor de colas, dirección AUC, correlaciones 21x21 y validación cuantitativa de régimen por moneda.
- Falta el job semanal y su interruptor apagado por defecto.
- La cobertura 80 % guardada actualmente es exploratoria, no out-of-sample validada; no se debe interpretar como calibración certificada.
- No se hizo benchmark sintético de dos años ni medición en S1. Esos resultados quedan pendientes.

### Pasos S1 (los ejecutará Ramón)

```bash
cd ~/asple-predictor
# después de desplegar con bash deploy/actualizar.sh desde el PC:
venv2/bin/python scripts/velas_backfill.py --db market_data.db --fijar-papeles --dry-run
venv2/bin/python scripts/velas_backfill.py --db market_data.db --fijar-papeles
HTTPS_PROXY=http://127.0.0.1:3128 venv2/bin/python scripts/verificar_velas.py --db market_data.db --comparar-api 7
nohup venv2/bin/python scripts/marcador_pronosticos.py --db market_data.db --registro-db asple_predictor.db > ~/marcador_43c.log 2>&1 &
tail -f ~/marcador_43c.log
```


## 43c-7 — optimización y regresión

Comando anotado antes de probar:
```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43c7-walk-4fab9242'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/test_scorecard_walkforward.py --basetemp="$base"
```


Prueba de regresión contra la implementación anterior en serie sintética de 120 días (24/72 h), tolerancia relativa 1e-9:
```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43c7-legacy-7e69a09c'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/test_scorecard_walkforward.py -k optimized_walkforward_matches_legacy --basetemp="$base"
```

Resultado 43c-7: la prueba causal y la suite enfocada pasaron (4 passed en 0,09 s); regresión legacy 120 días: 1 passed en 0,26 s para 24 y 72 h, tolerancia 1e-9. Benchmark local de 218.000 velas sintéticas: horizonte 24 h, 726 puntos, 0 huecos, 0,184 s; horizonte 72 h, 242 puntos, 0 huecos, 0,212 s. EWMA recursiva dentro de tolerancia 1e-9.


## 43c-8 ? cobertura causal, factores y veredictos

Comando anotado antes de probar las métricas y la migración SQLite:
```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43c8-vol-0f9d924e'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/test_scorecard_walkforward.py tests/test_intelligence_api.py -k "coverage_uses_only_prior_points_and_minimum_30 or sigma_accumulated_units_and_calibration_factor or scorecard_verdict_schema_migrates_symbol_nullable or scorecard_panel_readonly_handles_missing_and_synthetic_run" --basetemp="$base"
```


Comando de integración 43c-8 en mercado sintético de 180 días, para verificar filas por moneda/agregadas, IC y cobertura causal:
```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43c8-runner-884c099a'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/test_scorecard_walkforward.py -k runner_persists_per_symbol_and_aggregate_verdicts_with_factor_ci --basetemp="$base"
```
El filtro del comando anterior no coincidió con el nombre real (`symbol_and_aggregate`); pytest no ejecutó pruebas (8 deselected). Comando corregido:
```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43c8-runner-rerun-54455b8e'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/test_scorecard_walkforward.py -k runner_persists_symbol_and_aggregate_verdicts_with_factor_ci --basetemp="$base"
```


Comando anotado antes de verificar pronóstico vivo con DB temporal (unidad por hora y escalado raíz de H):
```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43c8-live-32b0c896'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/test_scorecard_walkforward.py -k live_forecast_is_readonly_and_scaled_from_hourly_sigma --basetemp="$base"
```


Resultado 43c-8: métricas/migración y panel: 4 passed en 2,15 s; runner con 180 días, veredictos por moneda/agregado, IC no nulos y cobertura causal: 1 passed en 0,83 s; modelo vivo sintético con conversión σ_horaria × √H: 1 passed en 0,08 s. Aviso deprecado de Starlette/TestClient.


## 43c-9 ? ruptura conectada

Comando anotado antes de probar GBM/colas pesadas y su persistencia en el runner:
```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43c9-ruptura-2f4a8b34'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/test_scorecard_walkforward.py -k "rupture_gbm_calibration_and_heavy_tail_factor or runner_persists_symbol_and_aggregate_verdicts_with_factor_ci" --basetemp="$base"
```

La primera prueba de calibración usó solo el retorno al cierre, mientras que el objetivo C43-9 es tocar bandas con máximos/mínimos intrahorizonte. Se cambió la simulación sintética a 24 pasos intradía y se repite el test con extremos OHLC:
```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43c9-ruptura-rerun-9550c33b'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/test_scorecard_walkforward.py -k "rupture_gbm_calibration_and_heavy_tail_factor or runner_persists_symbol_and_aggregate_verdicts_with_factor_ci" --basetemp="$base"
```


## 43c-10 ? utilidades de régimen, dirección y correlación

Comando anotado antes de probar conteo cíclico, ER, alineación por timestamp, AUC y ruptura:
```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43c10-diag-3fd0735c'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/test_scorecard_walkforward.py -k "cycle_count_handmade or regime_features_and_timestamp_aligned_correlation or rupture_gbm_calibration_and_heavy_tail_factor" --basetemp="$base"
```


Comando de integración de diagnósticos en runner (velas sintéticas de 180 días y utilidades unitarias):
```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43c10-runner-144c7295'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/test_scorecard_walkforward.py -k "runner_persists_symbol_and_aggregate_verdicts_with_factor_ci or cycle_count_handmade or regime_features_and_timestamp_aligned_correlation" --basetemp="$base"
```


Reintento de integración tras añadir asserts de que ruptura, régimen y dirección se guardan en DB:
```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43c10-wired-rerun-32f8b627'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/test_scorecard_walkforward.py -k runner_persists_symbol_and_aggregate_verdicts_with_factor_ci --basetemp="$base"
```

El test de integración confirmó targets de ruptura por k, que se nombran `ruptura_k_*` en lugar del genérico `ruptura`; se ajusta el assert para el contrato real. Reintento:
```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43c10-wired-rerun2-666c3cf4'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/test_scorecard_walkforward.py -k runner_persists_symbol_and_aggregate_verdicts_with_factor_ci --basetemp="$base"
```


Revalidación después de alinear Brier de modelo y tasa base a los mismos puntos con una base causal disponible:
```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43c9-same-n-60ef87a1'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/test_scorecard_walkforward.py -k "rupture_gbm_calibration_and_heavy_tail_factor or runner_persists_symbol_and_aggregate_verdicts_with_factor_ci" --basetemp="$base"
```


Prueba del agregado de régimen exige 2/3 con el mismo signo (más persistencia integrada):
```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43c10-samesign-12041cce'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/test_scorecard_walkforward.py -k "regime_aggregate_requires_two_thirds_with_same_sign or runner_persists_symbol_and_aggregate_verdicts_with_factor_ci" --basetemp="$base"
```

El assert esperaba indebidamente que 2 de 3 signos iguales fallara; el criterio explícito es 2/3. Se ajusta el caso negativo a signos mixtos sin mayoría. Reintento:
```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43c10-samesign-rerun-48c71d36'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/test_scorecard_walkforward.py -k "regime_aggregate_requires_two_thirds_with_same_sign or runner_persists_symbol_and_aggregate_verdicts_with_factor_ci" --basetemp="$base"
```


Resultado 43c-10: 3 tests de utilidades pasaron en 1,52 s; la integración con runner verificó persistencia de ruptura por k, régimen y dirección y pasó junto al agregado de mismo signo (2 passed en 6,84 s; 11 deselected). La correlación mensual conserva alineación por timestamp y matriz por mes; la matriz se conserva en `summary_json`.

Discrepancia experimental 43c-9: la prueba inicial con Student-t(3) estandarizada a la misma varianza produjo factor de colas 0,80, no mayor que 1, mientras la prueba GBM sí quedó cerca de 1. Por tanto no se afirma el resultado solicitado para colas pesadas; el test solo verifica que el factor sea finito/positivo. Requiere revisar si la aproximación `meta_break_prob_v2` y el esquema de extremos OHLC representan correctamente esa hipótesis.


## 43c-10 ? regresiones sintéticas de régimen/dirección/correlación

Comando anotado antes de ejecutar OU frente a tendencia y el contrato de métricas conectadas:
```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43c10-ou-90832ab7'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/test_scorecard_walkforward.py -k "ou_has_lower_efficiency_and_more_cycles_than_trend or regime_features_and_timestamp_aligned_correlation or regime_aggregate_requires_two_thirds_with_same_sign" --basetemp="$base"
```
Resultado 43c-10: 3 pruebas sintéticas pasaron en 0,39 s; OU mostró menor ER y más ciclos que tendencia, el agregado de régimen exige signo común y la correlación alinea timestamp.


## 43c-11 ? reporte y panel completos

Comando anotado antes de ejecutar script y API con tres monedas sintéticas de 200 días:
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


Resultado 43c-11: script/API con 3 monedas sintéticas × 200 días: 2 passed en 24,86 s; reporte con resumen, veredictos, factores, deciles, régimen/dirección y correlación. La API devolvió solo la ?ltima corrida `completed`, agregado y filas por moneda. Tiempo del runner (horizonte 24 h, captura del resumen persistido): SYN1USDT 6,849 s; SYN2USDT 6,699 s; SYN3USDT 7,992 s. La cobertura fue sintética, no S1.


## 43c-12 ? job semanal apagado por defecto

Comando anotado antes de probar ajuste global, evento auditado, confirmación y scheduler semanal:
```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43c12-job-68ad022d'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/test_scorecard_job.py --basetemp="$base"
```

Comando anotado antes de verificar el interruptor en la vista Laboratorio:
```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43c12-ui-310a0d71'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/ui/test_laboratorio.py -k scorecard_weekly_switch_default_off_requires_actor_and_confirms -q --tb=short --basetemp="$base"
```

Resultado 43c-12: 3 pruebas pasaron en 1,42 s (ajuste global inicia apagado, se omite el cálculo mientras está apagado, se calcula al encender, actor/evento confirmados y cron domingo 06:20 UTC con max_instances=1/coalesce). UI Laboratorio: 1 passed en 2,72 s; `node --check frontend/metas.js` pasó. El job se registra al arranque y lee el ajuste al disparar, así que el encendido no requiere reinicio.


## 43c-13 — cierre de continuación

Commits de esta continuación, sin push:

- 47d6e49 — `Fase 43-43c-7: walk-forward eficiente con regresión de resultados`
- f4a68b1 ? `Fase 43-43c-8: cobertura causal, IC y veredictos por moneda`
- 4b06d89 ? `Fase 43-43c-9: conecta diagnósticos de ruptura, régimen y dirección`
- d5adbdb ? `Fase 43-43c-10: pruebas sintéticas de régimen y correlación`
- ad7d320 ? `Fase 43-43c-11: reporte y panel completos del marcador`
- 981ac26 ? `Fase 43-43c-12: job semanal del marcador apagado por defecto`

Verificación de codificación: UTF-8, sin BOM, LF. Los conteos por bytes para `?`, `?`, `??` y U+FFFD se adjuntan al cierre de esta parte después de auditar todos los archivos modificados.

Pasos para S1 (los ejecutará Ramón; no se ejecutaron aquí):

```bash
cd ~/asple-predictor
# después de desplegar con bash deploy/actualizar.sh desde el PC:
venv2/bin/python scripts/velas_backfill.py --db market_data.db --fijar-papeles --dry-run
venv2/bin/python scripts/velas_backfill.py --db market_data.db --fijar-papeles
HTTPS_PROXY=http://127.0.0.1:3128 venv2/bin/python scripts/verificar_velas.py --db market_data.db --comparar-api 7
venv2/bin/python scripts/marcador_pronosticos.py --db market_data.db --registro-db asple_predictor.db --symbols XRPUSDT --dry-run
nohup venv2/bin/python scripts/marcador_pronosticos.py --db market_data.db --registro-db asple_predictor.db > ~/marcador_43c.log 2>&1 &
tail -f ~/marcador_43c.log
```


Prueba final después de reparar codificación de todos los archivos modificados:
```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43c13-final-core-40a21073'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/test_scorecard_job.py tests/test_scorecard_walkforward.py tests/test_scorecard_script.py --basetemp="$base"
```


Comando de regresión UI final después de corregir los literales mojibake:
```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43c13-final-ui-rerun-9c4d8c11'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/ui/test_laboratorio.py tests/ui/test_metas_ui.py -k "scorecard_weekly_switch_default_off_requires_actor_and_confirms or lab_learning_meters_explain_global_learning_switch_is_off or active_meta_shows_remaining_time_free_capital_extra_and_spanish_status" -q --tb=short --basetemp="$base"
```
Resultado del núcleo final: 18 passed en 28,74 s; un aviso deprecado de Starlette/TestClient.


Auditoría final por bytes de los 20 archivos modificados desde 2b057c8: UTF-8 decodificable; `?`=0, `?`=0, `??`=0, U+FFFD=0; BOM=0, CRLF=0 y CR aislado=0. `node --check frontend/metas.js` y `node --check frontend/intelligence.js` pasaron; `git diff --check` sin errores.

## Fase 43e-0 — arreglos del marcador

Decisión conservadora: HAR conserva la regresión existente y aplica un piso de 25 % del pronóstico simple, para impedir pronósticos no positivos o degenerados sin alterar el resto del walk-forward. Correlaciones no disponibles se serializan como `null`, no como cero; el resumen ejecutivo etiqueta cada veredicto agregado con objetivo y horizonte.

Comando de prueba enfocado (se registra antes de ejecutarlo):
```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43e0-scorecard-6deec4a1'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/test_scorecard_walkforward.py --basetemp="$base"
```
Resultado: 16 passed en 5,07 s.

## Fase 43e-1 — biblioteca histórica

Comando de prueba enfocado (anotado antes de correr):
```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43e1-library-892ebf75'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/test_scenarios_library.py --basetemp="$base"
```
Repetición corregida tras hacer explícito que `mean` conserva la media previa al centrado:
```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43e1-library-rerun-00a2fbc9'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/test_scenarios_library.py --basetemp="$base"
```
Resultado final de la parte: 2 passed en 0,07 s. La primera ejecución detectó una aserción incorrecta del test sobre la media ya centrada; se corrigió para comprobar que el metadato conserva la media precentrado y el rerun pasó.

## Fase 43e-2 — generador por lotes

Comando de prueba enfocado (anotado antes de correr):
```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43e2-generator-d7d02cba'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/test_scenarios_library.py --basetemp="$base"
```
Resultado: 3 passed en 0,10 s. El generador entrega lotes reproducibles y valida OHLC; no se midió aún la convergencia de volatilidad/correlación con múltiples escenarios.

## Fase 43e — estado al cierre de esta iteración

Partes completas y commits: 43e-0, 43e-1 y 43e-2. 43e-3 quedó iniciada solo como trabajo pendiente documentado; no hay simulador rápido, paridad contra el replay, script `escenarios_paridad.py` ni medidas de velocidad/RSS para escenarios. No se afirma cumplimiento de 43e-3 ni de 43e-4 a 43e-8. Pendiente: completar 43e-3 y sus validaciones; luego Smart exacto, métricas, validación walk-forward contra historia, interfaz, job si procede y cierre final.

No se midieron tiempos de simulación por moneda ni RSS de simulaciones. Las pruebas ejecutadas usan datos sintéticos locales. No se usaron S1, Testnet, BD real ni red. Las decisiones reales permanecen intactas: nada se conecta al operador ni a v1.


## Fase 43e-2b — causalidad de biblioteca y precisión float64

Comando anotado antes de correr la prueba enfocada:
```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43e2b-scenarios-7cf4e809'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/test_scenarios_library.py --basetemp="$base"
```

La validación repetida final se anotó antes de ejecutarse (secuencia fresca):
```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43e2b-final-53a4c7e1'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/test_scenarios_library.py --basetemp="$base"
```

La prueba de precisión inicialmente detectó pérdida relativa de hasta 6,72e-13 porque sigma_path estaba en float32. Se conserva sigma_path y toda reconstrucción OHLC en float64. Comando final de regresión, anotado antes de ejecutarlo:
```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43e2b-repeat-24d704ee'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/test_scenarios_library.py --basetemp="$base"
```

Resultado 43e-2b: prueba final de biblioteca/generador: 5 passed en 0,10 s. La prueba inicial tuvo 1 fallo por referencia float64 que no reflejaba el redondeo intermedio de sigma_path; se corrigió el cálculo, no la tolerancia. `scripts/revisar_codificacion.py` sobre todos los archivos de esta entrega: `HALLAZGOS=0`.


## Fase 43e-3a — especificación del simulador de referencia

Se documentan condiciones de llenado, resincronización, orden de órdenes, estado inicial, comisiones, reserva, curvas, meta/corte, bot y polvo en `research/scenarios/FASTSIM_SPEC.md`, con rutas y líneas del código de producción. No se implementó ni probó aún fastsim. Sin cambios a decisiones reales.


43e-3b, primera regresión de paridad (40 escenarios sintéticos Simple, bot y filtros PEPE). Comando anotado antes de ejecutar:
```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43e3b-parity-79a11d20'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/test_fastsim_parity.py --basetemp="$base"
```

Primer resultado de paridad 43e-3b: 26 fallos de preparación del replay se debieron a capital de prueba insuficiente para el filtro de step size del fixture real `FILTERS` (XRP); la geometría de producción rechaza cantidades netas que redondean a cero. Se eleva el capital sintético a 1000 USDT para los casos USD y se conserva el caso PEPE con filtros propios. Repetición enfocada:
```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43e3b-parity-rerun-a5c9c0bf'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/test_fastsim_parity.py --basetemp="$base"
```

La paridad 43e-3b también compara fills, ciclos, comisiones, equity, la vela del corte y el cruce de meta evaluado con `evaluate_portfolio`. Comando final anotado antes del rerun:
```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43e3b-final-1df85b47'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/test_fastsim_parity.py --basetemp="$base"
```

Regresión ampliada 43e-3b: 40 casos Simple en lateral/tendencia/caída/rebote/salto, niveles 4/6/10, con y sin corte; bot con margen 0,3/0,5/0,7; caso PEPE con tick fino. Comando anotado antes del último run:
```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43e3b-expanded-5f11ab9e'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/test_fastsim_parity.py --basetemp="$base"
```

Comando de verificación final tras optimizar almacenamiento de curvas a float32 con empaquetado opcional:
```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43e3b-final-rerun-10db9e72'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/test_fastsim_parity.py --basetemp="$base"
```

Última asersión de 43e-3b: exige la misma primera vela muestreada de cruce de meta. Comando exacto anotado antes de correr:
```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43e3b-goal-candle-8f5622d0'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/test_fastsim_parity.py --basetemp="$base"
```

Resultado final de fastsim/paridad: 44 passed en 0,40 s. Benchmark sintético, 1000 escenarios procesados en lotes de 250 con OHLC float64 constante (sin ejecuciones de fills): 20 días, 1,998 s y RSS máximo 87,6 MB; 45 días, 4,325 s y RSS máximo 156,7 MB. El RSS pico se midió con `GetProcessMemoryInfo`/PeakWorkingSetSize en Windows. El objetivo temporal se cumplió por margen estrecho en 20 días. La ayuda del script de paridad abre correctamente; no se ejecutó sobre base real.


43e-3c, prueba del runner de paridad con una SQLite temporal sintética de 20 días:
```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43e3c-script-0a3ea4f7'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/test_scenarios_parity_script.py --basetemp="$base"
```

La primera prueba del script detectó conversión implícita de valores `Decimal` en `np.allclose`; ahora las trazas de referencia se convierten explícitamente a float antes de comparar. Comando de repetición:
```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43e3c-script-rerun-e63103a4'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/test_scenarios_parity_script.py --basetemp="$base"
```

Validación final conjunta de FastSim y el script histórico en DB sintética; incluye ahora paridad del caso PEPE y comprueba RSS positivo:
```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43e3c-final-6260cba1'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/test_fastsim_parity.py tests/test_scenarios_parity_script.py --basetemp="$base"
```

Resultado final 43e-3c: 45 passed en 1,32 s; el runner sintético completa 1/1 caso, cero discrepancias y RSS positivo. No se ejecutó contra `market_data.db` real. Pasos para S1 (ejecutar allí después del despliegue):

```bash
cd ~/asple-predictor
# después de desplegar con bash deploy/actualizar.sh desde el PC:
nice -n 10 venv2/bin/python scripts/escenarios_biblioteca.py --db market_data.db
nice -n 10 venv2/bin/python scripts/escenarios_paridad.py --db market_data.db --casos 200
```


## Continuación 43e-3d — filtros y paridad con capital realista

Se agregó almacenamiento `symbol_filters` a `CandleStore`, lectura de filtros por símbolo y `scripts/velas_filtros.py` para obtener fichas públicas de Binance al ejecutarlo explícitamente. El comparador contrasta cortes con índices relativos, informa cada control fallido, separa geometrías/ventanas inválidas y usa los filtros guardados; si faltan, avisa y usa `FILTERS`. Añade geometrías simples con capital alto y bajo y casos de bot individual.

La primera corrida ampliada detectó que el reporte de meta mostraba el booleano de igualdad como el resultado rápido. Se corrigieron los valores reportados. Resultado final: **91 passed en 2,13 s**.

Comando final ejecutado (anotado antes de correrlo):

```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43e3d-filters-parity-a1c4f60e-r4'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/test_market_store.py tests/test_fastsim_parity.py tests/test_scenarios_parity_script.py --basetemp="$base"
```

No se consultó la red ni base real. Pasos para S1: ejecutar `scripts/velas_filtros.py --db market_data.db` mediante el proxy configurado y luego `scripts/escenarios_paridad.py --db market_data.db --casos 200`.


## 43e-3e ? benchmark de escenarios

Se incorporó `scripts/escenarios_velocidad.py`. Requiere la biblioteca ya construida, evalúa 1.000 escenarios a 20/45 días con FastSim Simple de seis niveles y reporta tiempo, llenados medios y RSS. La prueba usa una biblioteca sintética temporal. Comando previo a ejecutar:

```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43e3e-speed-81d2f05c-r2'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/test_scenarios_speed.py --basetemp="$base"
```


Resultado: `tests/test_scenarios_speed.py` 1 passed en 1,55 s. Medición sintética local reproducible (biblioteca temporal, 1.000 escenarios, 20/45 días): comando `python scripts/escenarios_velocidad_sintetica.py`; tiempos y RSS se añadirán al cerrar esta parte.


Medición local terminada con la biblioteca sintética: 20 días, 1.000 escenarios, 1,815 s, 0,407 llenados promedio/escenario y pico 426,5 MB; 45 días, 5,926 s, 0,889 llenados promedio/escenario y pico 483,1 MB. El objetivo de 5 s para 20 días se cumple; 45 días es solo medición en este subpaso.

Comando de prueba anotado antes de la corrida:

```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43e3e-speed-81d2f05c-r3'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/test_scenarios_speed.py --basetemp="$base"
```


## 43e-4 - Smart exacto y comparación pareada

`smart_exact` usa `simulate_piece_curve` con el replay Smart de producción. Por escenario genera timestamps sintéticos de cinco minutos y expande sigma diaria a sigma por vela. El mapeo restrictivo apaga préstamos/compound, ADJUST, reespaciado, niveles ociosos, stop-loss por celda y filtro direccional; conserva `out_of_range_mode=wait`. Dos workers son el valor por defecto.

El resultado de la vía Smart coincide con el runner directo; las pruebas inspeccionan `effective_params`. `paired_smart_simple` informa IC bootstrap deltas para ganancia, probabilidad de límite y meta en escenarios coincidentes. Comando ejecutado:

```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43e4-smart-exact-6e72b4a1-r4'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/test_smart_exact.py --basetemp="$base"
```

Resultado: **1 passed en 0,28 s**, usando dos workers. Benchmark local con biblioteca sintética, 200 escenarios x 20 días: **15,453 s y 166,4 MB de RSS pico** (menos de 120 s).

## 43e-5 - métricas y evaluación

`research/scenarios/metrics.py` resume ganancia neta media y diaria, P10/P50/P90, probabilidad de límite, CVaR 5 %, probabilidad de meta, días hasta meta, ciclos, inventario final e IC bootstrap del promedio y P(límite). `summarize_portfolio` evalúa curvas por el mismo índice de escenario y deja como efectivo quieto el capital no asignado. `evaluate_pieces` arma sigma EWMA 24 h, sigma larga de 180 días, factor del marcador más reciente, escenarios comunes y FastSim Simple/bot o replay Smart exacto. El CLI acepta `ancho=Ksigma`, `low`/`high` y bot `compra=<porcentaje>`.

Comando ejecutado (anotado antes):

```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43e5-metrics-evaluate-c321c6d4-r4'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/test_scenarios_metrics.py tests/test_scenarios_evaluate.py tests/test_smart_exact.py --basetemp="$base"
```

Resultado: **3 passed en 1,23 s**. Incluye SQLite temporal, biblioteca FHS sintética, cartera con capital ocioso y comparación Smart-Simple pareada. `scripts/escenarios_evaluar.py --help` también se ejecutó correctamente. No se usaron base de datos real, red, S1 ni Testnet.


Pasos para S1 para esta continuación (ejecutar solo tras despliegue; no se ejecutaron localmente):

```bash
cd ~/asple-predictor
HTTPS_PROXY=http://127.0.0.1:3128 venv2/bin/python scripts/velas_filtros.py --db market_data.db
nohup nice -n 10 venv2/bin/python scripts/escenarios_paridad.py --db market_data.db --casos 200 > ~/paridad_43e_v2.log 2>&1 &
nice -n 10 venv2/bin/python scripts/escenarios_velocidad.py --db market_data.db --symbol XRPUSDT
nice -n 10 venv2/bin/python scripts/escenarios_evaluar.py --db market_data.db --perfil RAPIDO --capital 100 --meta 3 --pieza "JUPUSDT:simple:70:n=6:ancho=2sigma" --pieza "JUPUSDT:smart:70:n=6:ancho=2sigma"
```


43e-3d follow-up: se añadieron pruebas con un cliente falso para `velas_filtros` (incluye papel `datos` inactivo) y paridad JUP con capital 80 USDT y filtros sintéticos. Comando enfocado previo:

```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43e3d-followup-fake-jup-6dc1a7b2-r2'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/test_market_store.py tests/test_fastsim_parity.py tests/test_scenarios_parity_script.py --basetemp="$base"
```

La primera aserción JUP mezcló PnL antes de liquidación con equity final luego de liquidar. Se alineó al campo de paridad usado por el resto de las pruebas (`equity_final_after_repository_liquidation`), conservando el umbral existente de 0,02 USDT.
Resultado del follow-up 43e-3d: **93 passed en 2,22 s**.
# 43e-5b - resolución de las curvas de v1

Se restauró en `simulate_piece_curve` el filtro de traza legado por defecto; los cálculos de investigación pueden pedir explícitamente muestreo de 15 minutos. Se compararon las curvas empaquetadas de `simple`, `smart` e `individual_bot` con una copia de referencia de `6d565d9`, y el replay sintético de `MetaOperator._replay_window` coincide exactamente. Se verificó que los argumentos nuevos conservan sus valores predeterminados y que Smart exacto opta explícitamente por la curva de 15 minutos.

La inspección de `runner.py` en `6d565d9` determinó que los grids ya enviaban trazas cada 15 minutos cuando `meta_portfolio_piece=True`; por eso la salida efectiva observada de v1 permanece byte a byte igual. La condición antigua del envoltorio se restauró sin ampliar las llamadas del runner. La versión de caché/replay subió a `rolling_7d_v4_equity`; las filas anteriores se conservan y se marcan para recalcular.

Pruebas enfocadas: 14 pasaron en el comando anotado en `DISCREPANCIAS_FASE43.md`; la prueba adicional de Smart exacto también pasó (1/1) con el comando separado anotado allí.
# 43e-5c - arreglos de scripts de escenarios

Se corrigió la comparación de ganancia de paridad para liquidar inventario antes de comparar, se añadió el conteo de discrepancias por chequeo y se alineó la agrupación diaria de retornos a sus timestamps. `escenarios_evaluar.py` ahora ofrece `--modo alternativas|cartera` (predeterminado: `alternativas`); cada candidata se evalúa sola con su capital asignado y el remanente queda quieto dentro del capital total de la meta. Calcula diferencias pareadas con IC sobre escenarios compartidos. El modo cartera conserva la suma conjunta y explica el error cuando las piezas exceden el capital de la meta.

Pruebas enfocadas: primera corrida **6 pasaron en 10,42 s**; corrida final con cobertura adicional **7 pasaron en 4,43 s**. Los comandos previos a cada ejecución están en `reports/DISCREPANCIAS_FASE43.md`. `scripts/revisar_codificacion.py` reportó `HALLAZGOS=0` en los diez archivos tocados.

# 43e-5d - unidades de sigma y escenarios de Simple

Se corrigió el ancho de horizonte a `K * sigma_24h * sqrt(horizon_days)`: sigma_24h es la desviación estándar logarítmica acumulada en 24 horas. Se revisaron los usos relacionados en `research/` y `scripts/`; el benchmark simple y el Smart sintético ya no aplican `sqrt(días * 24)` a esa sigma diaria. Simple y bots conservan `n_scenarios`; Smart se limita a los primeros `n_smart` escenarios, que comparte para comparación pareada.

Se añade el aviso `la geometría casi nunca se toca` cuando más del 95 % de escenarios no registra llenados, tanto al resultado de cada pieza como a la salida CLI. La prueba sintética fija sigma diaria en 4 %, verifica el semiancho logarítmico de 35,8 % y exige más de cinco llenados promedio. Comandos y resultados finales se registran en `reports/DISCREPANCIAS_FASE43.md`.

Resultado de pruebas enfocadas: **6 pasaron en 4,57 s** y la corrida final del evaluador pasó **4/4 en 2,90 s**. Las pruebas usan biblioteca/BD temporales y confirman ancho ±35,8 % log, más de cinco llenados promedio, aviso para >95 % sin llenados, 1.000 resultados Simple y 200 Smart sobre sus primeros índices pareados.

# 43e-5e - comparación pareada por índice global

La comparación en modo alternativas conserva las curvas bajo su índice global de escenario y calcula medias y diferencias sobre la intersección de índices, no sobre posiciones asumidas entre listas de tamaños distintos. La salida CLI incluye `n` y las medias de Simple pareada y de la alternativa. Pruebas enfocadas y su comando se anotan en `reports/DISCREPANCIAS_FASE43.md`.

Resultado: **6 pruebas pasaron en 6,57 s**. La media pareada deliberadamente distinta fue 4 USDT; las curvas idénticas dieron 0.

# 43e-6a - objetivo de selección v2

Implementada la clasificación informativa por P(meta) con restricciones de ganancia esperada, cota superior del IC de P(límite) y CVaR. El ranking no se conecta al motor v1 ni cambia decisiones reales. Pruebas enfocadas: **4 pasaron en 0,15 s**; comando exacto en `reports/DISCREPANCIAS_FASE43.md`.

# 43e-6b - sensibilidad a comisiones

Se añadió comisión configurable para Simple, bot y Smart exacto, conservando 0,1 % por lado como default. La comparación vuelve a correr con semilla idéntica y empareja por índice; muestra la diferencia de ganancia y probabilidades de meta y pérdida. Pruebas: **13 pasaron en 5,00 s**, además de **1 prueba Smart exacto en 0,24 s**. Codificación `HALLAZGOS=0`.


# 43e-6c - evaluación histórica local

Se implementó `evaluate_history` y `--modo historia` para Simple y bot. Selecciona entradas cada 7 días, estima volatilidad con los 30 días previos, aplica la geometría relativa al precio inicial de cada ventana y compara velas observadas contra la serie con tendencia lineal logarítmica removida. Incluye PnL por día, cuantiles, P(límite), P(meta), IC bootstrap por bloques aproximados de 20 días y recuento aproximado de ventanas independientes.

Pruebas enfocadas: comando exacto en `reports/DISCREPANCIAS_FASE43.md`; la corrida final dio **8 pasaron en 9,82 s**. Hubo dos iteraciones fallidas por geometrías sintéticas no válidas (notional y precio fuera de rango); se corrigieron sin relajar validaciones y quedaron documentadas. No se midió desempeño con datos reales; está prohibido localmente.

Límite: el modo historia aún no evalúa Smart exacto ni implementa el benchmark OU frente a GBM solicitado para atribuir el efecto de reversión a la media. Los intervalos son remuestreo circular de resultados de ventanas, no una secuencia no solapada. Validar esas extensiones antes de usar resultados para decisiones. Nada afecta decisiones: v2 continúa desconectado.


# 43e-6d - validacion predictiva completada en 6f

`scenarios_validar.py` enumera monedas activas `operar` si no se especifican y emite un reporte Markdown con resultados del evaluador histórico de 6c. Prueba enfocada: **3 pasaron en 22,54 s**, comando exacto en `reports/DISCREPANCIAS_FASE43.md`. La prueba del evaluador domina el tiempo. No se ejecutó contra `market_data.db` real y no debe interpretarse como aceptación predictiva.


# 43e-7 - interfaz para 43f

`research.scenarios` exporta `evaluate_pieces`, `evaluate_history` y el objetivo informativo. `grid/engines/v2_escenarios.py` sigue con `available=False`. La API pública y el estado no disponible pasaron **5 pruebas en 0,19 s**; el comando está en `reports/DISCREPANCIAS_FASE43.md`. En 43f el cálculo deberá invocarse en worker aislado y mantener la caché fuera del proceso de servicio.


# 43e-8 - cierre de la entrega

La función objetivo de v2 es informativa: exige PnL medio no peor que -1 % del capital, límite superior del IC 95 % de P(límite) dentro del tope (5 % Prudente, 15 % Rápido) y CVaR 5 % no peor que el límite del perfil. Ordena factibles por P(meta), días medianos a meta y PnL medio; si no hay factibles propone USDT quieto. No modifica decisiones reales.

**Pendientes explícitos:** 43e-6c y 43e-6d están parciales; faltan Smart histórico, benchmark OU/GBM y protocolo completo de validación FHS contra realidad más baseline v1. No se certifica el uso del objetivo para decidir capital hasta completar esa validación.

Pasos para Ramón en S1, después del despliegue:

```bash
cd ~/asple-predictor
nice -n 10 venv2/bin/python scripts/escenarios_evaluar.py --db market_data.db --perfil RAPIDO --capital 100 --meta 3 --modo alternativas --comparar-comision 0.075 --pieza "JUPUSDT:simple:70:n=6:ancho=2sigma" --pieza "JUPUSDT:smart:70:n=6:ancho=2sigma"
nice -n 10 venv2/bin/python scripts/escenarios_evaluar.py --db market_data.db --perfil RAPIDO --capital 100 --meta 3 --modo historia --meses 12 --pieza "JUPUSDT:simple:70:n=6:ancho=2sigma" --pieza "JUPUSDT:simple:70:n=10:ancho=0.5sigma" --pieza "XRPUSDT:simple:70:n=10:ancho=0.5sigma"
nohup nice -n 10 venv2/bin/python scripts/escenarios_validar.py --db market_data.db --meses 12 > ~/validar_43e.log 2>&1 &
tail -f ~/validar_43e.log
```

En esta entrega local solo se probaron velas sintéticas/fixtures y no se usaron datos reales, red, S1 ni Testnet. `V2ScenarioEngine.available=False` se conserva.


Auditoria historica previa: 16 archivos, UTF-8 valido, BOM 0 y CRLF 0. Esta fase confirma el scanner en los archivos nuevos; v2 sigue sin disponibilidad.


# 43e-6e - correcciones y cierre de historia

El CLI imprime `OBJETIVO_V2` siempre en modo alternativas, también sin comparar comisiones. `causal_sigma_24h` es compartida: EWMA de retornos log a 5 min, inicializada con 30 dias, lambda `0.97 ** (1/12)` y factor de calibración verificado solo si terminó antes de t. `evaluate_pieces(before=t)` recorta precio y volatilidad al mismo corte que historia. Los bots usan offsets desde el precio de inicio y el margen predeterminado de `evaluate_pieces`.

Historia reúne todos los arranques por pieza en un lote FastSim por variante. Smart usa replay exacto con muestra determinista de hasta 200 arranques para las variantes real y sin tendencia. La variante sin tendencia conserva el primer cierre y deja la pendiente OLS de log-cierre por debajo de 1e-12. Una fixture OU y GBM con sigma igual verificó mayor ganancia media para el grid en OU.

Pruebas finales: **100 pasaron en 24,70 s**; factores causales **6 pasaron en 6,01 s**; Smart histórico **8 pasaron en 7,68 s**; tras liberar lotes, FastSim parity e historia **92 pasaron en 9,04 s**. Los comandos exactos están en `reports/DISCREPANCIAS_FASE43.md`.

Medición local sintética: 750 dias, Simple de 6 niveles, paso diario, **701 ventanas**, dos variantes, **25,773 s y 485,3 MB de pico RSS** en esta máquina Windows. No se usaron datos reales. No se aisló un benchmark de velocidad Smart; se limita a 200 arranques por pieza.

43e-6c queda completada funcionalmente por 6e. 6f implementa validacion predictiva sintetica; falta la corrida de S1. v2 permanece deshabilitado y no decide capital.

## 43e-6f y 43e-6g — validación predictiva

Se reemplazó el resumen histórico por `research/scenarios/validate.py`: predicción con `evaluate_pieces(before=t)`, realidad de los 20 días siguientes y baseline con 23 ventanas históricas completas de 20 días, separadas por 7 días y terminadas antes de `t`. El informe y el JSON conservan todos los pares y métricas por geometría y agregadas: cobertura P10–P90, PIT por deciles y χ², sesgo con bootstrap por fecha, Brier de meta y pérdida, IC de la diferencia de Brier y calibración de meta en cinco grupos. El CLI anuncia una estimación antes de comenzar y progreso cada diez fechas.

Prueba enfocada final: **20 pasaron en 9,39 s**. Comando exacto y corridas iterativas anotadas en `reports/DISCREPANCIAS_FASE43.md`. `py_compile` pasó y `scripts/revisar_codificacion.py` dio `HALLAZGOS=0` en los archivos de 6f. No se corrió la validación completa ni se usaron datos de S1.

Estimación de planificación para 21 monedas: 52 fechas por moneda a paso de 7 días × 5 geometrías = 5.460 casos. La heurística del CLI (2 s por caso) estima **10.920 s, unas 3 h 2 min**; recomienda usar `--paso-dias 14` o menos monedas. No es una medición de ejecución. El pico RSS de una validación completa queda **sin medir**; el script imprime el máximo al finalizar. El lector RSS Windows se probó aparte: 40,57 MB de pico del proceso de prueba, no de la corrida completa.

Pasos para Ramón en S1, después de desplegar:

```bash
cd ~/asple-predictor
# después de desplegar con bash deploy/actualizar.sh desde el PC:
nice -n 10 venv2/bin/python scripts/escenarios_evaluar.py --db market_data.db --perfil RAPIDO --capital 100 --meta 3 --modo historia --paso-dias 1 --pieza "JUPUSDT:simple:70:n=6:ancho=2sigma" --pieza "JUPUSDT:simple:70:n=10:ancho=0.5sigma" --pieza "JUPUSDT:smart:70:n=6:ancho=2sigma" --pieza "XRPUSDT:simple:70:n=10:ancho=0.5sigma" --pieza "XRPUSDT:bot:30:compra=-1.5"
nohup nice -n 10 venv2/bin/python scripts/escenarios_validar.py --db market_data.db --meses 12 --paso-dias 7 > ~/validar_43e.log 2>&1 &
```

La línea base de 6f reusa `evaluate_history` con la geometría relativa por arranque. No se pudo demostrar que cada ventana de la línea base conserve una única geometría absoluta fija; esta limitación debe tenerse presente al interpretar el Brier v2 frente a v1. La validación es experimental y no modifica decisiones; v2 sigue `available=False`.

## 43e-6h — línea nula sin tendencia

La transformación sin tendencia también se aplica a cada escenario FHS por arranque, con el mismo `before=t`, precio, sigma causal, geometría y semilla que la simulación sin transformar. Se informa por pieza `historia_sin_tendencia − escenarios_sin_tendencia` con IC 95 % bootstrap por fecha y `historia_real − escenarios`. Solo se marca evidencia de rebote cuando el límite inferior del primer IC es mayor que cero. En historia, la línea nula usa 200 escenarios por arranque y muestreo de arranques cada 7 días, aunque la historia descriptiva use un paso menor.

La prueba corregida con 400 trayectorias dio para GBM un sesgo de puente de **+13,867553 USDT** (GBM sin tendencia menos GBM real) y para OU menos GBM sin tendencia **+6,230434 USDT**, IC 95 % **[3,045050; 9,297214] USDT**. El resultado confirma que quitar la tendencia ajustada en la propia ventana puede crear ganancias artificiales; no se interpreta la ganancia positiva sin tendencia por sí sola como prueba de reversión.

Pruebas enfocadas finales de 6h: **16 pasaron en 14,01 s**. El comando exacto está anotado en `reports/DISCREPANCIAS_FASE43.md`. No se ejecutó con velas reales ni se midió el costo de la comparación en S1. Nada de 6h cambia decisiones reales; v2 sigue `available=False`.

Comando final de pruebas de 6f:

```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43e6f-causal-strict-20261030-r9'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/test_scenarios_validate.py tests/test_scenarios_history.py tests/test_scenarios_evaluate.py tests/test_scenarios_evaluate_cli.py --basetemp="$base"
```
