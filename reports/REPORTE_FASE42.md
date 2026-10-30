# Reporte Fase 42 — C40

Fecha: 2026-10-29. Base verificada: `main`, `b8616b5` (`Fase 42-C39c`), árbol limpio.

## C40a — Retención de memoria

La evidencia recibida de S1 reporta RSS de 1,56 GB a los 17 minutos y 5,81 GB a las 7 h 16 min, sin errores registrados. Esos valores son evidencia externa de S1; esta sesión no accedió a S1 ni a su base de datos.

La caché de replay ya no conserva el resultado íntegro ni la lista `trace` de diccionarios. Conserva métricas, comisiones, timestamps `int64` y equity libre `float64`, en el mismo orden y resolución de cada vela. El cálculo sobre la curva compactada se compara exactamente con el cálculo sobre la traza original en una prueba. Las cachés de replay y velas comparten LRU y límite estimado de 200 MiB, además del límite de 512 entradas; los datos de replay se liberan al cambiar el día UTC. El resumen del job expone MB por caché, total, límite y RSS cuando `psutil` está disponible. `verificar_s1_meta.py` presenta la misma información del último job.

La prueba sintética recorre 2 grids × 25 ventanas × 6 candidatos × 8 ciclos horarios con `tracemalloc`: retuvo 34.240 bytes y dejó 32.896 bytes estimados en la caché frente a un límite de prueba de 65.536 bytes. Esta medición cubre las cachés de `GridIntelligenceJob` (replay y velas). C25d/MetaOperator limita cada uno de sus cinco mapas de proceso a 40 MiB y 500 entradas, con estimados en bytes; su máximo conjunto es 200 MiB. C38 mantiene snapshot de hasta 16 MiB y ahora también limita su caché auxiliar conjunta a 16 MiB y su cola a 50 builders.

## C40b — Base del inventario y capital afectado

El esquema real `grid_levels` contiene `entry_price`, `held_qty` y `fee_paid`; no contiene `entry_cost` ni `entry_fee_usdt`. El costo retenido ahora se calcula como `held_qty × entry_price` más `fee_paid` si es positivo; cuando no consta comisión pagada, se estima al 0,1 %. El capital de la simulación se limita al efectivo de celdas sin inventario en estado `IDLE`/`DONE` y sin orden asociada.

La versión metodológica actual es `grid_shadow_42c40_v3`. El resumen y verificador cuentan recomendaciones y etiquetas solo de esta versión; las filas v1/v2 siguen almacenadas.

## C40c — Presupuesto y equidad entre grids

El job se programa al minuto 7 UTC. El presupuesto total es 150 s y el máximo por grid 60 s. Un grid parcial o no procesado pasa al frente en la corrida siguiente. Las ventanas de mantener se reutilizan mediante la caché compartida de replay; los candidatos que coinciden en geometría y ventana evitan repetir simulación. El hilo del job baja a prioridad “below normal” en Windows y nice 5 en POSIX; el entorno de pruebas no sustituye la verificación de prioridad efectiva en S1.

## Validación local

Comando dirigido ejecutado con `ASPLE_OFFLINE=1`, rutas explícitas y `--basetemp` único bajo `%TEMP%`:

```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP ('asple_fase42_c40_release_' + [guid]::NewGuid().ToString('N')); .\venv\Scripts\python.exe -m pytest tests\test_grid_intelligence.py tests\test_intelligence_api.py tests\test_intelligence_snapshot_memory.py tests\test_meta_operator.py -k 'grid_intelligence or retained_inventory or compact_replay_cache or inventory_cost or hourly_shadow_job or synthetic_shadow or two_180_day_grids or budget_exhaustion or recommendation_requires or snapshot_replacement or oversized_snapshot or auxiliary_snapshot_cache or process_cache' -p no:cacheprovider --basetemp=$base -q -s
```

Resultado: 24 pasaron, 91 fueron omitidas por filtro; dos advertencias deprecadas de dependencias. No se ejecutó la suite completa ni hubo acceso a red, Testnet, `.env` o BD real. El test de dos grids sintéticos de 180 días terminó en 1,27 s, dentro de los 150 s, con 13 ventanas y métrica completa para ambas líneas base.

Regresión dirigida de política y decisiones del monitor:

```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP ('asple_fase42_c40_decisions_' + [guid]::NewGuid().ToString('N')); .\venv\Scripts\python.exe -m pytest tests\test_grid_monitor_policy.py tests\test_grid_monitor.py -k 'policy or decision or pause or adjust or future or timestamp' -p no:cacheprovider --basetemp=$base -q
```

Resultado: 18 pasaron, 33 omitidas por filtro. No cambian los módulos que deciden ni ejecutan órdenes; esta evidencia comprueba regresión de esos tests dirigidos, no reproduce tráfico de producción.

## Verificación pendiente en S1

Medir RSS una vez por hora durante al menos 8 horas, conservando timestamp y PID:

```powershell
while ($true) { $ts=Get-Date -Format 'yyyy-MM-dd HH:mm:ss zzz'; Get-Process -Name python,pythonw -ErrorAction SilentlyContinue | Select-Object @{n='timestamp';e={$ts}},Id,ProcessName,@{n='rss_mb';e={[math]::Round($_.WorkingSet64/1MB,1)}}; Start-Sleep -Seconds 3600 }
```

Consultar las recomendaciones v3 más recientes y sus diferencias pareadas (solo lectura):

```powershell
python -c "import json,sqlite3,sys; c=sqlite3.connect('file:'+sys.argv[1].replace('\\','/')+'?mode=ro',uri=True); c.row_factory=sqlite3.Row; rows=c.execute('SELECT grid_id,symbol,created_at,action,candidate_json,baseline_json FROM grid_intel_recommendations WHERE method_version=? ORDER BY created_at DESC LIMIT 20',('grid_shadow_42c40_v3',)); [print(r['grid_id'],r['symbol'],r['created_at'],r['action'],'ganancia_7d=',(json.loads(r['candidate_json'] or '{}').get('expected_gain_7d')),'base_7d=',(json.loads(r['baseline_json'] or '{}').get('expected_gain_7d')),'diferencia_pareada=',(json.loads(r['candidate_json'] or '{}').get('paired_diff_median')),'ventanas=',(json.loads(r['candidate_json'] or '{}').get('windows'))) for r in rows]" "C:\\ruta\\solo-lectura\\asple.db"
```

Auditar Markdown local para UTF-8 estricto, ausencia de BOM y marcadores comunes de corrupción:

```powershell
python -c "from pathlib import Path; files=[Path('reports/REPORTE_FASE42.md'),Path('reports/DISCREPANCIAS_FASE42.md')]; [(lambda b,p: (lambda t: print(p,'UTF8=OK','BOM=',b.startswith(bytes.fromhex('efbbbf')),'CRLF=',b'\r\n' in b,'mojibake=',any(x in t for x in (chr(0x00c3),chr(0x00c2),chr(0x00e2),chr(0xfffd)))))(b.decode('utf-8')))(p.read_bytes(),p) for p in files]"
```

La revisión actual de archivos locales no constituye una comprobación de S1.

## Commits locales

- `6d7f6d2` — `Fase 42-C40a-b: compacta replay y corrige accounting`
- `b831404` — `Fase 42-C40b-c: conecta accounting y rotación de parciales`
- `210643a` — `Fase 42-C40c: amplía presupuesto y completa dos grids`
- El reporte C40d se cerrará con el commit que contiene estos dos Markdown.
