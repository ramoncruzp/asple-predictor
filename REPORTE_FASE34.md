# Reporte Fase 34 — banco OOS y préstamos/compound

## Estado

Fase 34 cerrada en sus commits; Fase 34b también se completó aquí con reversión de D4, variantes corregidas, filtros por moneda y banco OOS de tres monedas.

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

## Fase 34b - correcciones, filtros y B4

### A1 - revertir D4

- `grid/policy.py:12-23`: defaults Smart restaurados a `trapped_cap_pct=50.0`, `trapped_age_h=24` y `max_loss_pct=10.0`.
- `config/settings.py:132-136`: descripción de `smart_close_wait_enabled` actualizada; espera fuera de rango no cierra por pausa y solo la pérdida máxima de 10 % cierra.
- `frontend/app.js:374` y `frontend/grids.js:454`: rótulo `Capital atrapado (≥ 50 % del capital con más de 24 h)` en tarjetas Smart. La tarjeta de Simple no lo muestra.
- `tests/test_grid_engine.py:54-66`, `tests/test_grid_policy.py:36-150` y `tests/ui/test_grids_browser.py:247-253, 1301-1307`: defaults, lógica 50/24/10 y rótulos actualizados.
- Mutación `trapped_age_h=0.25`: **rojo**, 1 fallo. SHA-256 mutado `636f773727955ae2d90046b54f7bc2601cc838c17c174cba9b95bc9c0a2ab7cd`; restaurado byte por byte.

### A2 - variantes alineadas con los defaults

- `scripts/bench_smart_oos.py:20-40`: siete variantes: `simple_hoy` referencia, `simple_vol`, `smart_hoy`, `smart_actual`, `smart_actual_con_d4`, `smart_actual_loans` y `smart_actual_compound`.
- `smart_actual` usa interruptores coincidentes con `Settings`: ADJUST apagado, stop-loss apagado, espera encendida y espaciado vol encendido; `params={}` usa `DEFAULT_SMART_PARAMS`.
- `tests/test_bench_smart_oos.py:62-71`: verifica interruptores y defaults. Mutación ADJUST encendido para `smart_actual`: **rojo**, 1 fallo. SHA-256 mutado `b449eea3f56c72d4b0f81db184634b61908f6e1b4583bb5ba27ed97bfa288d14`; restaurado byte por byte.

### A3 - filtros de Binance por símbolo

- `scripts/bench_smart_oos.py:106-126`: carga `symbolInfo` o `exchangeInfo` mediante `SymbolFilters.from_symbol_info`, valida el símbolo y acepta base como `PEPE` o par como `PEPEUSDT`.
- `scripts/bench_smart_oos.py:143-151, 184-190, 298-316`: filtros pasan tanto a `select_levels` como a `run_simulation`. Busca `data/cache/{sym}_filters.json`; si falta usa `FILTERS` y lo informa en `summary.md`. Acepta `--filters-json`.
- `tests/test_bench_smart_oos.py:74-110`: tick `1e-8`, niveles positivos y propagación de filtros al benchmark. Mutación que vuelve a enviar `FILTERS` al runner: **rojo**, 1 fallo. SHA-256 mutado `c83214d03b9574a5359dda34144b62e8df18600ce2c34e78d1335e8b67c2e566`; restaurado byte por byte.

### A4 - datos y filtros públicos

- Descargas autorizadas de 730 días con `scripts/download_candles.py`: PEPE 5m 210239 velas y 1h 17519; ADA 5m 210240 y 1h 17519. Ambos intervalos cubren `2024-10-21` a `2026-10-21` UTC.
- ExchangeInfo público consultado en `api.binance.com` para PEPEUSDT y ADAUSDT; filtros guardados como `data/cache/pepe_filters.json` y `data/cache/ada_filters.json`. Tick PEPE `1e-8`. `data/cache/` está ignorado por Git.
- XRP local: 5m 210239 velas (`2024-09-28` a `2026-09-28` UTC), 1h 17519 (`2024-10-09` a `2026-10-09` UTC). XRP usa `FILTERS` por falta de `xrp_filters.json`; cada resumen lo advierte.

### A5 - banco OOS final

Comandos: `scripts/bench_smart_oos.py --symbol {XRP,PEPE,ADA} --capital {100,1000} --range-mode centered --reference simple_hoy`; siete variantes, ventanas 30 d, paso 7 d, historial 90 d. Las seis salidas están bajo `%TEMP%\fase34b-a-{xrp,pepe,ada}-{100,1000}`. Cada corrida tuvo 88 ventanas útiles y 0 omitidas; IC pareados por bootstrap de bloques (3000 réplicas, semilla fija).

**La tabla anterior usaba variantes mal configuradas; D4 revertido en 34b.** En las tablas, delta e IC95 comparan con `simple_hoy`; medias y p5 son USDT.

#### XRP - capital 100 USDT

| Variante | Media USDT | Mediana | Positivas | p5 | Peor ventana | DD medio | Tiempo activo | Delta ref. (IC95%, wins) | Veredicto |
|---|---:|---:|---:|---:|---|---:|---:|---|---|
| simple_hoy | -0.48913093170454597 | 1.3426596700000033 | 59.09090909090909% | -14.3323501285 | 2026-01-09T17:15:00+00:00 (-24.549505699999997) | 11.292512369913128% | 100.0% | — | referencia |
| simple_vol | 0.5792212804545452 | 2.552410584999997 | 67.04545454545455% | -15.521264212499995 | 2026-01-09T17:15:00+00:00 (-29.757112210000003) | 11.785440408572782% | 100.0% | 1.0684 [0.4833, 1.5800], 68/88 | mejora |
| smart_hoy | -1.19474328534091 | -1.2082171499999959 | 34.090909090909086% | -6.2046809470000035 | 2025-10-10T17:15:00+00:00 (-20.46532845) | 4.995018803009971% | 99.67724116161617% | -0.7056 [-2.1342, 0.8986], 38/88 | sin diferencia demostrada |
| smart_actual | 0.35967625534090897 | 1.9856548699999976 | 57.95454545454546% | -13.088962691999997 | 2026-01-09T17:15:00+00:00 (-22.835597050000004) | 10.10655053807251% | 57.91627209595959% | 0.8488 [0.2757, 1.4135], 61/88 | mejora |
| smart_actual_con_d4 | -0.12204091522727238 | 0.856489890000006 | 60.22727272727273% | -10.337962363999994 | 2026-01-09T17:15:00+00:00 (-15.295672530000004) | 7.2627271535920235% | 40.80689709595959% | 0.3671 [-0.4755, 1.2096], 51/88 | sin diferencia demostrada |
| smart_actual_loans | 0.3730471881818181 | 2.1417056599999995 | 60.22727272727273% | -12.631564096499995 | 2026-01-09T17:15:00+00:00 (-24.229523549999996) | 9.974620538999712% | 54.003708964646464% | 0.8622 [0.2572, 1.4409], 58/88 | mejora |
| smart_actual_compound | 0.3381347667045457 | 1.8716667449999989 | 61.36363636363637% | -12.631564096499995 | 2026-01-09T17:15:00+00:00 (-23.158350350000006) | 9.61127225446429% | 53.3917297979798% | 0.8273 [0.2064, 1.4258], 57/88 | mejora |

#### XRP - capital 1000 USDT

| Variante | Media USDT | Mediana | Positivas | p5 | Peor ventana | DD medio | Tiempo activo | Delta ref. (IC95%, wins) | Veredicto |
|---|---:|---:|---:|---:|---|---:|---:|---|---|
| simple_hoy | -6.690255507159083 | 10.983979794999982 | 59.09090909090909% | -143.19073448900002 | 2026-01-09T17:15:00+00:00 (-230.22909965999997) | 10.60775785320056% | 100.0% | — | referencia |
| simple_vol | 6.435996660909091 | 24.620497419999992 | 67.04545454545455% | -146.38271001299998 | 2026-01-09T17:15:00+00:00 (-297.55831768999997) | 11.662865654968595% | 100.0% | 13.1263 [6.5771, 18.8698], 71/88 | mejora |
| smart_hoy | -34.203209719545455 | -29.06891505499999 | 21.59090909090909% | -94.65493552199996 | 2025-10-10T17:15:00+00:00 (-218.62083759999996) | 6.7402408317438045% | 99.57267992424241% | -27.5130 [-40.1287, -14.3153], 25/88 | empeora |
| smart_actual | 4.234325707613638 | 19.47921093499997 | 59.09090909090909% | -126.18069327799998 | 2026-01-09T17:15:00+00:00 (-228.01357012999995) | 9.98884737030104% | 57.87760416666666% | 10.9246 [5.8887, 16.1593], 63/88 | mejora |
| smart_actual_con_d4 | -1.1080107248863593 | 9.140348990000007 | 60.22727272727273% | -103.705422102 | 2026-01-09T17:15:00+00:00 (-152.42891695000003) | 7.225183444583709% | 40.956044823232325% | 5.5822 [-1.4933, 12.6544], 50/88 | sin diferencia demostrada |
| smart_actual_loans | 3.607961213181829 | 21.102533714999993 | 61.36363636363637% | -126.18069327799998 | 2026-01-09T17:15:00+00:00 (-242.13137311000003) | 9.922643747847646% | 53.41579861111111% | 10.2982 [5.1206, 15.4568], 63/88 | mejora |
| smart_actual_compound | 3.857339511590921 | 13.194515734999982 | 63.63636363636363% | -126.18069327799998 | 2026-01-30T17:15:00+00:00 (-146.8028349) | 9.019201907548812% | 57.68663194444445% | 10.5476 [4.9443, 16.4075], 59/88 | mejora |

#### PEPE - capital 100 USDT

| Variante | Media USDT | Mediana | Positivas | p5 | Peor ventana | DD medio | Tiempo activo | Delta ref. (IC95%, wins) | Veredicto |
|---|---:|---:|---:|---:|---|---:|---:|---|---|
| simple_hoy | -3.3564047515137503 | 0.9780577632499998 | 53.40909090909091% | -23.867925994361503 | 2026-01-11T07:20:00+00:00 (-36.10122293578) | 14.657524803495068% | 100.0% | — | referencia |
| simple_vol | -1.575609521875455 | 2.7662296255950025 | 57.95454545454546% | -26.458221846621 | 2026-01-11T07:20:00+00:00 (-33.81648388722) | 15.900194531386795% | 100.0% | 1.7808 [0.9301, 2.5821], 70/88 | mejora |
| smart_hoy | -4.419470092231251 | -4.280216199590001 | 25.0% | -11.280503976001997 | 2025-09-14T07:20:00+00:00 (-25.589776727049994) | 7.613683084879995% | 99.78653724747474% | -1.0631 [-3.8529, 1.9313], 32/88 | sin diferencia demostrada |
| smart_actual | -1.2280720754350003 | 2.507656535175002 | 59.09090909090909% | -21.014427835638998 | 2025-10-05T07:20:00+00:00 (-31.1041185239) | 13.450889362917392% | 54.242818813131315% | 2.1283 [1.1481, 3.0011], 64/88 | mejora |
| smart_actual_con_d4 | -0.8567076102159085 | 1.5721399378950025 | 54.54545454545454% | -16.112084224308997 | 2025-10-05T07:20:00+00:00 (-21.024944926680007) | 9.681333863208335% | 43.42921401515151% | 2.4997 [0.9052, 4.1530], 57/88 | mejora |
| smart_actual_loans | -1.2183155963547732 | 1.690267921630003 | 57.95454545454546% | -21.710576021910995 | 2025-10-05T07:20:00+00:00 (-31.235459223519996) | 13.697645822539812% | 52.4017518939394% | 2.1381 [1.1668, 3.0082], 63/88 | mejora |
| smart_actual_compound | -1.1358505881754548 | 1.477436005845 | 60.22727272727273% | -21.01962243611999 | 2025-10-05T07:20:00+00:00 (-31.543488052659995) | 12.483649624841338% | 52.58128156565656% | 2.2206 [1.2303, 3.1235], 63/88 | mejora |

#### PEPE - capital 1000 USDT

| Variante | Media USDT | Mediana | Positivas | p5 | Peor ventana | DD medio | Tiempo activo | Delta ref. (IC95%, wins) | Veredicto |
|---|---:|---:|---:|---:|---|---:|---:|---|---|
| simple_hoy | -36.426865593367715 | 8.489164372640005 | 53.40909090909091% | -244.280746738312 | 2026-01-11T07:20:00+00:00 (-362.20766626257) | 14.478814486664925% | 100.0% | — | referencia |
| simple_vol | -15.756092020335336 | 27.66228888671003 | 57.95454545454546% | -264.5822182021645 | 2026-01-11T07:20:00+00:00 (-338.16485850439994) | 15.900193967235918% | 100.0% | 20.6708 [10.8781, 30.3614], 70/88 | mejora |
| smart_hoy | -48.75508744177363 | -48.73833803757498 | 20.454545454545457% | -110.13688684575851 | 2025-09-14T07:20:00+00:00 (-286.99098323617) | 7.771186296396331% | 99.78653724747474% | -12.3282 [-39.6061, 16.8468], 30/88 | sin diferencia demostrada |
| smart_actual | -12.280716963725224 | 25.076576379094945 | 59.09090909090909% | -210.14429224048595 | 2025-10-05T07:20:00+00:00 (-311.041236022) | 13.450888706471698% | 54.242818813131315% | 24.1461 [14.4814, 32.2507], 67/88 | mejora |
| smart_actual_con_d4 | -8.567071924745338 | 15.721407111885014 | 54.54545454545454% | -161.120843866875 | 2025-10-05T07:20:00+00:00 (-210.24948122190006) | 9.681333124262611% | 43.42921401515151% | 27.8598 [12.7651, 43.5135], 60/88 | mejora |
| smart_actual_loans | -12.18315164035614 | 16.902693319855018 | 57.95454545454546% | -217.10577589091497 | 2025-10-05T07:20:00+00:00 (-312.35463419704) | 13.697645124816745% | 52.4017518939394% | 24.2437 [14.6109, 32.4773], 66/88 | mejora |
| smart_actual_compound | -11.358504516100448 | 14.774345748689996 | 60.22727272727273% | -210.19625487143847 | 2025-10-05T07:20:00+00:00 (-315.43492291843995) | 12.48365092277752% | 52.58128156565656% | 25.0684 [14.8669, 34.2311], 67/88 | mejora |

#### ADA - capital 100 USDT

| Variante | Media USDT | Mediana | Positivas | p5 | Peor ventana | DD medio | Tiempo activo | Delta ref. (IC95%, wins) | Veredicto |
|---|---:|---:|---:|---:|---|---:|---:|---|---|
| simple_hoy | -2.207279019886364 | 1.7627534699999998 | 57.95454545454546% | -20.484206198 | 2026-05-10T07:20:00+00:00 (-35.441498249999995) | 13.30631130881165% | 100.0% | — | referencia |
| simple_vol | -1.2281757045454549 | 2.553546054999998 | 62.5% | -23.438023621499998 | 2026-05-10T07:20:00+00:00 (-34.69975794) | 14.311247474890918% | 100.0% | 0.9791 [0.2180, 1.7321], 66/88 | mejora |
| smart_hoy | -2.9555604329545457 | -2.878636710000002 | 27.27272727272727% | -9.937825775500002 | 2025-10-05T07:20:00+00:00 (-20.38813254) | 7.333626052994734% | 99.63147095959597% | -0.7483 [-2.9396, 1.7120], 32/88 | sin diferencia demostrada |
| smart_actual | -1.276404681931818 | 2.482184414999999 | 61.36363636363637% | -18.359514883 | 2026-05-31T07:20:00+00:00 (-27.818424519999994) | 12.364184664459412% | 56.52777777777778% | 0.9309 [0.3748, 1.4935], 61/88 | mejora |
| smart_actual_con_d4 | -0.7550810255681824 | 1.6893717849999987 | 60.22727272727273% | -15.307039863000005 | 2026-05-31T07:20:00+00:00 (-18.96504376) | 8.757914955886823% | 43.1707702020202% | 1.4522 [0.1473, 2.8965], 47/88 | mejora |
| smart_actual_loans | -1.4332252196590909 | 2.322782475000004 | 60.22727272727273% | -19.0472958285 | 2026-05-10T07:20:00+00:00 (-28.080442129999994) | 12.66585469536115% | 54.39078282828282% | 0.7741 [0.1935, 1.3977], 55/88 | mejora |
| smart_actual_compound | -1.1984384512499995 | 1.8871051149999971 | 60.22727272727273% | -18.033155441999998 | 2026-05-31T07:20:00+00:00 (-27.818424519999994) | 11.835246324415372% | 53.76262626262626% | 1.0088 [0.2979, 1.7628], 58/88 | mejora |

#### ADA - capital 1000 USDT

| Variante | Media USDT | Mediana | Positivas | p5 | Peor ventana | DD medio | Tiempo activo | Delta ref. (IC95%, wins) | Veredicto |
|---|---:|---:|---:|---:|---|---:|---:|---|---|
| simple_hoy | -24.102206122499993 | 13.442574799999988 | 57.95454545454546% | -203.06553523949995 | 2026-05-10T07:20:00+00:00 (-352.97240238999996) | 12.858705884585465% | 100.0% | — | referencia |
| simple_vol | -11.442787625000003 | 25.488096189999965 | 62.5% | -237.4398975145 | 2026-05-10T07:20:00+00:00 (-347.23319950999996) | 14.060693616600014% | 100.0% | 12.6594 [3.8081, 21.5900], 69/88 | mejora |
| smart_hoy | -43.86439000431819 | -41.60833053499999 | 18.181818181818183% | -120.86749894200001 | 2025-09-14T07:20:00+00:00 (-306.83643893) | 7.612248271405173% | 99.60029987373736% | -19.7622 [-41.8408, 4.7819], 25/88 | sin diferencia demostrada |
| smart_actual | -11.625528252272732 | 24.81288015000007 | 61.36363636363637% | -183.69972698499998 | 2026-05-31T07:20:00+00:00 (-278.25400135999996) | 12.091154510247303% | 56.57788825757576% | 12.4767 [7.1370, 17.7678], 64/88 | mejora |
| smart_actual_con_d4 | -7.317803274886374 | 16.836761765000006 | 60.22727272727273% | -153.14972711099998 | 2026-05-31T07:20:00+00:00 (-189.72019376000003) | 8.872970046908403% | 42.93047664141414% | 16.7844 [4.4424, 30.2120], 48/88 | mejora |
| smart_actual_loans | -13.12589943352272 | 23.26006394000001 | 60.22727272727273% | -190.39769569650002 | 2026-05-10T07:20:00+00:00 (-280.93090364) | 12.388155873552972% | 54.42471590909091% | 10.9763 [5.5491, 16.8066], 59/88 | mejora |
| smart_actual_compound | -12.084644780340911 | 19.67928425499997 | 62.5% | -184.48768833399996 | 2026-05-31T07:20:00+00:00 (-278.25400135999996) | 11.170426744666797% | 54.56833964646464% | 12.0176 [5.8013, 18.5889], 53/88 | mejora |

### Ganancia neta de ciclos completados y P&L total con inventario

La ganancia por ciclo suma únicamente ventas que completaron ciclos; es neta de las comisiones de compra y venta. P&L total incluye el inventario no realizado.

#### XRP

| Variante | Ciclos netos, 100 | P&L total, 100 | Ciclos netos, 1000 | P&L total, 1000 |
|---|---:|---:|---:|---:|
| simple_hoy | -8.858178 | -0.489131 | 20.514019 | -6.690256 |
| simple_vol | 4.230614 | 0.579221 | 52.759037 | 6.435997 |
| smart_hoy | -5.004005 | -1.194743 | 25.979142 | -34.203210 |
| smart_actual | 3.189324 | 0.359676 | 40.154694 | 4.234326 |
| smart_actual_con_d4 | 2.165898 | -0.122041 | 27.199720 | -1.108011 |
| smart_actual_loans | 3.266630 | 0.373047 | 40.541198 | 3.607961 |
| smart_actual_compound | 3.126577 | 0.338135 | 35.889336 | 3.857340 |

#### PEPE

| Variante | Ciclos netos, 100 | P&L total, 100 | Ciclos netos, 1000 | P&L total, 1000 |
|---|---:|---:|---:|---:|
| simple_hoy | 4.703381 | -3.356405 | 41.797871 | -36.426866 |
| simple_vol | 7.013933 | -1.575610 | 70.139508 | -15.756092 |
| smart_hoy | 7.847790 | -4.419470 | 67.325125 | -48.755087 |
| smart_actual | 5.645552 | -1.228072 | 56.455672 | -12.280717 |
| smart_actual_con_d4 | 4.033532 | -0.856708 | 40.335432 | -8.567072 |
| smart_actual_loans | 5.793804 | -1.218316 | 57.938194 | -12.183152 |
| smart_actual_compound | 5.036187 | -1.135851 | 50.362017 | -11.358505 |

#### ADA

| Variante | Ciclos netos, 100 | P&L total, 100 | Ciclos netos, 1000 | P&L total, 1000 |
|---|---:|---:|---:|---:|
| simple_hoy | 1.226582 | -2.207279 | 37.488841 | -24.102206 |
| simple_vol | 5.624123 | -1.228176 | 57.647777 | -11.442788 |
| smart_hoy | 2.986511 | -2.955560 | 60.815428 | -43.864390 |
| smart_actual | 4.290565 | -1.276405 | 44.189950 | -11.625528 |
| smart_actual_con_d4 | 3.167996 | -0.755081 | 32.489310 | -7.317803 |
| smart_actual_loans | 4.254684 | -1.433225 | 43.800329 | -13.125899 |
| smart_actual_compound | 4.092551 | -1.198438 | 38.733283 | -12.084645 |

### Efecto incremental de préstamos y compound frente a `smart_actual`

Comparación pareada adicional por ventanas, bloques de 5, 3000 réplicas, semilla 3401. Un intervalo que contiene cero no demuestra mejora.

| Moneda | Capital | Delta loans (IC95 %) | Delta compound (IC95 %) |
|---|---:|---:|---:|
| XRP | 100 | 0.0134 [-0.1623, 0.1962] | -0.0215 [-0.2157, 0.1809] |
| PEPE | 100 | 0.0098 [-0.1802, 0.2108] | 0.0922 [-0.4206, 0.6107] |
| ADA | 100 | -0.1568 [-0.3814, 0.0536] | 0.0780 [-0.1815, 0.3698] |
| XRP | 1000 | -0.6264 [-2.0259, 0.7432] | -0.3770 [-3.5263, 2.8348] |
| PEPE | 1000 | 0.0976 [-1.8021, 2.1083] | 0.9222 [-4.2059, 6.1070] |
| ADA | 1000 | -1.5004 [-3.6680, 0.5958] | -0.4591 [-4.8170, 4.0550] |

## Decisiones pendientes de Ramón

- **Préstamos:** recomendación del banco: dejarlos apagados. En las seis comparaciones directas `smart_actual_loans` vs `smart_actual`, los IC95 incluyen cero; ni PEPE ni ADA muestran beneficio medible.
- **Compound:** tampoco muestra beneficio incremental estadístico frente a `smart_actual` en estas seis corridas.
- **Monedas:** PEPE y ADA ya se descargaron para los datos cacheados de dos años. Se puede decidir si se añaden otros símbolos a una futura corrida.

## Pruebas de Fase 34b

Inicio de Parte A: `ASPLE_OFFLINE=1`, `-m "not live"`, 11 archivos obtenidos con `git grep` para los parámetros/interruptores y el benchmark; **192 passed, 14 warnings**.
Pase final de Parte A: los mismos 11 archivos más `tests/test_grid_sim_runner.py`; **203 passed, 14 warnings**. Incluyó `tests/test_app_settings_api.py`, `tests/test_bench_smart_oos.py`, `tests/test_fase32_switches.py`, `tests/test_grid_engine.py`, `tests/test_grid_maxdays.py`, `tests/test_grid_policy.py`, `tests/test_grid_policy_adjust.py`, `tests/test_grid_scan_service.py`, `tests/test_grid_sim_smart_fidelity.py`, `tests/test_grid_sim_runner.py`, `tests/ui/test_grids_browser.py` y `tests/ui/test_laboratorio.py`. `git diff --check` pasó.

Las pruebas se ejecutaron sin Testnet; fuera de las cuatro descargas de velas y dos consultas públicas `exchangeInfo` descritas en A4, no se usó red. No se abrió `.env`, no se tocó `models/saved/` ni una base de datos real.

## Commits de Parte A

Se hicieron cuatro commits selectivos en orden A1, A2, A3 y reporte/A5; sin push. Los artefactos del banco permanecen en `%TEMP%` y los datos están bajo `data/cache/`, ignorado por Git.
