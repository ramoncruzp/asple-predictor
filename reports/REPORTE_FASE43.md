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
