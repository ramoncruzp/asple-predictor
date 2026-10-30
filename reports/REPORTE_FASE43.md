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

Resultado de repetición: 10 passed en 0,38 s. En la corrida inicial del comando conjunto, `tests/test_meta_operator.py` quedó con 92 passed; las dos fallas fueron únicamente los fixtures nuevos antes de corregir el umbral.
