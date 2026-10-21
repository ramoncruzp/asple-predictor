# Reporte Fase 34 — banco OOS y préstamos/compound

## Estado

Fase completada y commits separados en cinco partes. B4 se ejecutó en modo offline con la caché local, rango centrado y siete variantes. La corrida descubrió y corrigió una inicialización incompleta de los niveles que ADJUST agrega al simulador; el resto de cambios de producción de B1–B3 se mantuvo acotado a la fase.

## B1 — selección de niveles compartida

- `grid/structure.py:142`: nueva función pura `select_levels(...)`, con el cálculo de niveles por margen, separación por volatilidad y fallback del Advisor.
- `api/routes/grid_advisor.py:338`: el Advisor delega la selección a esa función.
- `tests/test_grid_structure.py:156`: cinco casos fijos con conteo y regla esperados.
- `tests/test_grid_advisor.py`: prueba de regresión del Advisor adaptada al selector compartido.
- Mutación de conteo de volatilidad incrementado en uno: **rojo**, 1 fallo y 4 aprobadas. SHA-256 mutado: `2DE62350E553C9CF677C373B1537FCB1164D0F8775853F4F66C7A5BBB4862332`. Archivo restaurado.

## B2 — benchmark walk-forward

- `scripts/bench_smart_oos.py:72`: el historial de cada ventana termina estrictamente antes del inicio de simulación.
- `scripts/bench_smart_oos.py:116`: ventanas walk-forward, rangos centrados o estructurales, niveles B1 y sigma EWMA 5m con calentamiento de nueve días. Escribe `results.json`, un JSON por ventana y `summary.md` en `--out`.
- `scripts/bench_smart_oos.py:79`: bootstrap pareado por bloques, 3000 réplicas, semilla fija e intervalo del 95 %.
- `scripts/bench_smart_oos.py:196`: resumen por variante con media, mediana, porcentaje positivo, p5, peor ventana, drawdown medio y tiempo activo.
- `tests/test_bench_smart_oos.py:16`, `:25`, `:44` y `:52`: corte causal, P&L conocido, determinismo y ejecución sintética walk-forward.
- Mutación que incluye la vela con timestamp igual al inicio de ventana: **rojo**, falla la prueba de lookahead. SHA-256 mutado: `EA2A26BA0866C8A5F6B63ED2446419BC008D7A900AE57E3D35CCA7720F903358`. Archivo restaurado.

## B3 — préstamos y compound en simulación

- `grid/sim/exchange.py:110`: guarda el P&L neto del ciclo completado para compound.
- `grid/sim/runner.py:159`: liquida la contabilidad de préstamos antes de ajuste/cierre; `:200` aplica `compound_amount`; `:548` emite `LOAN_REPAID`; `:585` emite `LOAN_CREATED`.
- `grid/sim/runner.py:82`: Smart acepta compound y loans; compound también está disponible en Simple. Loans en Simple se rechazan.
- `grid/sim/runner.py:414`: ADJUST crea niveles con `capital_base`, `capital_compound` y `capital_loan` inicializados en cero. B4 descubrió que los registros agregados omitían esos campos; una variante con préstamos fallaba con `KeyError: 'capital_loan'` tras ADJUST. El registro se completó y la suite enfocada volvió a pasar.
- `tests/test_grid_sim_runner.py:46` cubre compound; `:60` el límite `loan_min_margin`; `:84` compara prestatario e importe con `GridEngine.process_grid_loans` usando fakes.
- Mutación que ignora `remaining < minimum`: **rojo**, falla la prueba porque aparece `LOAN_REPAID`. SHA-256 mutado: `EAD95464EFA734E632BD2907B79254F99F8471AC45926077D861AFDC8EE84E17`. Archivo restaurado.

El replay ejecuta fills completos y deterministas por vela. No reproduce sagas persistentes PENDING, reintentos ante rechazos del exchange, fills parciales ni recuperación de órdenes interrumpidas del monitor real. Los préstamos simulados emiten eventos de creación, devolución y transferencia dentro del replay.

## Corrección de paridad tras Fase 33

- `tests/test_grid_sim_smart_fidelity.py:212`: `test_smart_adjust_preserves_existing_live_sell_order` fija `trapped_cap_pct=50.0`, `trapped_age_h=24` y `max_loss_pct=10.0`.
- `tests/test_grid_sim_smart_fidelity.py:269`: `test_smart_pause_resume_hysteresis_keeps_sells_and_does_not_rebuy` fija los mismos valores.
- Causa: los dos tests de paridad heredaban los defaults nuevos de D4 (tope atrapado 30 %, edad 0.25 h y pérdida máxima 7 %). Eso cambia la dinámica de la simulación y bloquea ADJUST o RESUME; los tests debían fijar explícitamente los parámetros previos para aislar la paridad del mecanismo.
- No se modificó el comportamiento de producción para corregir esta paridad. El ajuste de inicialización de niveles ADJUST indicado en B3 fue un defecto adicional observado durante B4.

## B4 — banco fuera de muestra XRP

Datos cacheados localmente: 5m desde `2024-09-28 17:15 UTC` hasta `2026-09-28 17:05 UTC`; 1h desde `2024-10-09 02:00 UTC` hasta `2026-10-09 00:00 UTC`. Ventanas de 30 días, paso 7 días, historial 90 días; 88 ventanas útiles, 0 omitidas en cada capital. Salidas bajo `%TEMP%\fase34-xrp-100` y `%TEMP%\fase34-xrp-1000`. Cada intervalo pareado usa bootstrap por bloques de 3000 réplicas y semilla fija.

### Capital 100 USDT

| Variante | Media | Mediana | Positivas | p5 | Peor ventana | DD medio | Activo | Delta ref. (IC95 %, wins) | Veredicto |
|---|---:|---:|---:|---:|---|---:|---:|---|---|
| simple_hoy | -0.4891 | 1.3427 | 59.09 % | -14.3324 | 2026-01-09 (-24.5495) | 11.29 % | 100.00 % | — | referencia |
| simple_vol | 0.5792 | 2.5524 | 67.05 % | -15.5213 | 2026-01-09 (-29.7571) | 11.79 % | 100.00 % | 1.0684 [0.4833, 1.5800], 68/88 | mejora |
| smart_hoy | -1.0822 | -0.4338 | 44.32 % | -6.6187 | 2025-10-10 (-20.4653) | 3.87 % | 94.81 % | -0.5931 [-2.0261, 0.9969], 34/88 | sin diferencia demostrada |
| smart_f33 | -1.0822 | -0.4338 | 44.32 % | -6.6187 | 2025-10-10 (-20.4653) | 3.87 % | 94.81 % | -0.5931 [-2.0261, 0.9969], 34/88 | sin diferencia demostrada |
| smart_f33_sin_d4 | -1.1947 | -1.2082 | 34.09 % | -6.2047 | 2025-10-10 (-20.4653) | 5.00 % | 99.68 % | -0.7056 [-2.1342, 0.8986], 38/88 | sin diferencia demostrada |
| smart_f33_loans | -1.0471 | -0.1750 | 48.86 % | -6.9050 | 2025-10-10 (-20.4653) | 4.09 % | 94.11 % | -0.5579 [-2.0007, 1.0664], 36/88 | sin diferencia demostrada |
| smart_f33_compound | -1.0822 | -0.4338 | 44.32 % | -6.6187 | 2025-10-10 (-20.4653) | 3.87 % | 94.81 % | -0.5931 [-2.0261, 0.9969], 34/88 | sin diferencia demostrada |

### Capital 1000 USDT

| Variante | Media | Mediana | Positivas | p5 | Peor ventana | DD medio | Activo | Delta ref. (IC95 %, wins) | Veredicto |
|---|---:|---:|---:|---:|---|---:|---:|---|---|
| simple_hoy | -6.6903 | 10.9840 | 59.09 % | -143.1907 | 2026-01-09 (-230.2291) | 10.61 % | 100.00 % | — | referencia |
| simple_vol | 6.4360 | 24.6205 | 67.05 % | -146.3827 | 2026-01-09 (-297.5583) | 11.66 % | 100.00 % | 13.1263 [6.5771, 18.8698], 71/88 | mejora |
| smart_hoy | -25.8406 | -22.9299 | 23.86 % | -74.0697 | 2025-10-10 (-218.6208) | 5.35 % | 88.71 % | -19.1503 [-32.5338, -4.1560], 25/88 | empeora |
| smart_f33 | -25.8406 | -22.9299 | 23.86 % | -74.0697 | 2025-10-10 (-218.6208) | 5.35 % | 88.71 % | -19.1503 [-32.5338, -4.1560], 25/88 | empeora |
| smart_f33_sin_d4 | -34.2032 | -29.0689 | 21.59 % | -94.6549 | 2025-10-10 (-218.6208) | 6.74 % | 99.57 % | -27.5130 [-40.1287, -14.3153], 25/88 | empeora |
| smart_f33_loans | -24.2017 | -23.5758 | 28.41 % | -75.6055 | 2025-10-10 (-218.6208) | 5.42 % | 87.30 % | -17.5115 [-31.5406, -1.6554], 31/88 | empeora |
| smart_f33_compound | -25.2479 | -22.0658 | 25.00 % | -70.2658 | 2025-10-10 (-218.7930) | 5.30 % | 87.70 % | -18.5576 [-31.8934, -3.1077], 25/88 | empeora |

La columna delta compara contra `simple_hoy`, conforme a los resúmenes del banco. Son resultados de simulación histórica; no prueban rentabilidad futura ni modifican flags de la aplicación.

## Decisiones pendientes de Ramón

- **Préstamos:** decidir si se habilitan. En 100 USDT, `smart_f33_loans` no muestra diferencia estadística frente a `simple_hoy`; en 1000 USDT el veredicto frente a esa referencia es “empeora”, aunque queda algo menos negativo que `smart_hoy`. La corrida por sí sola no decide habilitarlos.
- **Monedas a descargar:** la caché disponible contiene solo XRP. Decidir qué otros símbolos se evaluarán. Para cada símbolo, la descarga de velas 5m y 1h se puede hacer con `scripts/download_candles.py --symbol ADAUSDT --interval 5m --days 730 --out data/cache/ada_5m.csv` y repetir con `--interval 1h --out data/cache/ada_1h.csv`. No se descargaron monedas ni se usó la red en esta fase.

## Pruebas enfocadas

Suite Fase 34: `tests/test_grid_advisor.py`, `tests/test_grid_sim_runner.py`, `tests/test_grid_sim_exchange.py`, `tests/test_grid_sim_smart_fidelity.py`, `tests/test_grid_loans_pure.py`, `tests/test_grid_compound_pure.py`, `tests/test_grid_loans_engine.py`, `tests/test_grid_compound_engine.py`, `tests/test_grid_structure.py` y `tests/test_bench_smart_oos.py`.

Comando con `ASPLE_OFFLINE=1`, `-m "not live"` y `--basetemp` único bajo `%TEMP%`: **174 passed, 3 warnings**. Las warnings son una advertencia de colección y dos deprecaciones de dependencias. `git diff --check` pasó.

## Commits y publicación

Commits locales separados: B1 selección pura; B2 benchmark OOS; B3 préstamos/compound y campos para niveles creados por ADJUST; corrección de parámetros explícitos de paridad; reporte Fase 34. No se hizo push.
