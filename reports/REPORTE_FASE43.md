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
