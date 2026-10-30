# Reporte Fase 43

## 43a-1 — contrato y registro de motores

Comando ejecutado antes de la prueba:

```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43a1-93c51b2e'; pytest tests/test_meta_engines.py --basetemp="$base"
```

Resultado: 2 passed en 0,24 s.
