# Fase 35 — re-espaciado dinámico por volatilidad

## B1 — sigma de apertura

- `api/routes/grids.py:386-387,467-468`: al preparar apertura Smart guarda `sigma_24h_open` y `vol_spacing_k` en parámetros. El sigma procede de la volatilidad de 24 h del mercado calculada por el endpoint de apertura.
- `grid/scan_service.py:122-129,161-162`: el Scanner calcula y expone sigma 24 h para cada moneda. `grid/auto_open.py:95-97` lo conserva al crear un grid Smart.
- `grid/policy.py:52-53`: grids anteriores sin sigma de apertura retienen `None` y no se re-espacian (`no_sigma_open`).

## B2 — regla pura y bandera

- `config/settings.py:51,143-147`: `smart_respacing_enabled`, default `False`, descripción: «Hipótesis sin evidencia todavía; se evalúa con el banco OOS». El panel Laboratorio la muestra a partir del catálogo de flags; `frontend/grids.js:199` refleja su estado.
- `grid/policy.py:502-547`: `respace_decision` usa sigma 24 h; activa con `r > 1.5` o `r < 0.67`, cooldown de 24 h desde el último ajuste, capital atrapado bajo el límite, al menos `min_free_cells` y grid Smart ACTIVE. El rango se mantiene; calcula `n_levels` con `round(ln(high/low)/(k*sigma_now))`, acotado por `MIN_LEVELS`/`MAX_LEVELS`. Sin cambio de n no emite ajuste.
- La prueba pura cubre umbrales alto/bajo, cooldown, estado no activo, sigma de apertura ausente, falta de celdas libres, capital atrapado y rango fijo: `tests/test_grid_policy.py:345-365`.
- Mutación del argumento del monitor (`sigma_24h_now` — `sigma` de horizonte configurado): **rojo**, 1 fallo en `test_monitor_respaces_with_24h_sigma_even_when_adjust_is_disabled`. SHA antes/restaurado `998912bd3914bcf61871912a79988981183874f54f2f8e169b315f6300d2d78a`; mutado `941c0a91063288a27c90b85a30cf3d50c7a38368dd28fd7aa2ae76830205cb82`. Restauración byte idéntica.

## B3 — monitor y simulador

- `grid/monitor.py:682-708,854-857`: evalúa después del ADJUST normal, incluso con el interruptor ADJUST apagado, omite un ciclo en que ADJUST ya ocurrió, consulta expresamente provider `24`, limita `RESPACE_EVAL` a una vez cada 6 h por motivo y persiste el sigma tras un ajuste exitoso.
- El ajuste va por `plan_adjust_with_shrink` y `engine.adjust_grid`; se manda el rango actual junto con `source=RESPACE`, manteniendo las ventas de celdas con inventario mediante el plan compartido.
- `grid/sim/runner.py:88,150-152,309-322,428-430`: reproduce la regla con sigma 24 h de la simulación, inicializa `sigma_24h_open` desde el primer valor disponible y sigue `respacing_enabled`. `grid/scan_service.py:145-157` lo conecta al flag en la simulación del Scanner.
- Pruebas de integración: `tests/test_grid_monitor.py:979-1014`, `tests/test_grid_sim_runner.py:32-46`, `tests/test_grid_scan_service.py:130-151`.

## B4 — banco OOS

Se comparó `smart_actual_respace` con `smart_actual` como referencia en XRP, PEPE y ADA, rango centrado, capital 100 y 1000, 30 días por ventana, paso 7 días, historial 90 días, 88 ventanas útiles y 0 omitidas por corrida. Salidas bajo `%TEMP%\fase35-b4-{xrp,pepe,ada}-{100,1000}`. Las tablas muestran también `simple_vol` y `smart_hoy` para contexto. Delta e IC95 son pareados contra `smart_actual`.

### XRP — capital 100 USDT

| Variante | Media USDT | Mediana | Positivas | p5 | Peor ventana | DD medio | Tiempo activo | Delta ref. (IC95%, wins) | Veredicto |
|---|---:|---:|---:|---:|---|---:|---:|---|---|
| simple_vol | 0.5792212804545452 | 2.552410584999997 | 67.04545454545455% | -15.521264212499995 | 2026-01-09T17:15:00+00:00 (-29.757112210000003) | 11.785440408572782% | 100.0% | 0.2195 [-0.4872, 0.8087], 36/88 | sin diferencia demostrada |
| smart_hoy | -1.19474328534091 | -1.2082171499999959 | 34.090909090909086% | -6.2046809470000035 | 2025-10-10T17:15:00+00:00 (-20.46532845) | 4.995018803009971% | 99.67724116161617% | -1.5544 [-2.9118, -0.0398], 31/88 | empeora |
| smart_actual | 0.35967625534090897 | 1.9856548699999976 | 57.95454545454546% | -13.088962691999997 | 2026-01-09T17:15:00+00:00 (-22.835597050000004) | 10.10655053807251% | 57.91627209595959% | — | referencia |
| smart_actual_respace | 0.3447874307954541 | 1.9856548699999976 | 57.95454545454546% | -13.088962691999997 | 2026-01-09T17:15:00+00:00 (-22.835597050000004) | 10.124296258267224% | 58.21298926767676% | -0.0149 [-0.0860, 0.0524], 7/88 | sin diferencia demostrada |
| simple_vol | 4.230614 | 0.579221 | 6.375 |
| smart_hoy | -5.004005 | -1.194743 | 39.705 |
| smart_actual | 3.189324 | 0.359676 | 4.943 |
| smart_actual_respace | 3.157994 | 0.344787 | 5.205 |

Ciclos realizados netos frente al P&L total con inventario:

| Variante | Ganancia de ciclos (media USDT) | P&L total (media USDT) | Ciclos medios |
|---|---:|---:|---:|
| smart_actual | 3.189324 | 0.359676 | 4.943 |
| smart_actual_respace | 3.157994 | 0.344787 | 5.205 |

### XRP — capital 1000 USDT

| Variante | Media USDT | Mediana | Positivas | p5 | Peor ventana | DD medio | Tiempo activo | Delta ref. (IC95%, wins) | Veredicto |
|---|---:|---:|---:|---:|---|---:|---:|---|---|
| simple_vol | 6.435996660909091 | 24.620497419999992 | 67.04545454545455% | -146.38271001299998 | 2026-01-09T17:15:00+00:00 (-297.55831768999997) | 11.662865654968595% | 100.0% | 2.2017 [-4.9618, 8.1069], 36/88 | sin diferencia demostrada |
| smart_hoy | -34.203209719545455 | -29.06891505499999 | 21.59090909090909% | -94.65493552199996 | 2025-10-10T17:15:00+00:00 (-218.62083759999996) | 6.7402408317438045% | 99.57267992424241% | -38.4375 [-50.6954, -24.8543], 17/88 | empeora |
| smart_actual | 4.234325707613638 | 19.47921093499997 | 59.09090909090909% | -126.18069327799998 | 2026-01-09T17:15:00+00:00 (-228.01357012999995) | 9.98884737030104% | 57.87760416666666% | — | referencia |
| smart_actual_respace | 4.143532238636363 | 19.47921093499997 | 59.09090909090909% | -126.18069327799998 | 2026-01-09T17:15:00+00:00 (-228.01357012999995) | 10.004015655990719% | 57.9363952020202% | -0.0908 [-0.8151, 0.6170], 7/88 | sin diferencia demostrada |
| simple_vol | 52.759037 | 6.435997 | 6.341 |
| smart_hoy | 25.979142 | -34.203210 | 149.670 |
| smart_actual | 40.154694 | 4.234326 | 4.920 |
| smart_actual_respace | 40.461440 | 4.143532 | 5.193 |

Ciclos realizados netos frente al P&L total con inventario:

| Variante | Ganancia de ciclos (media USDT) | P&L total (media USDT) | Ciclos medios |
|---|---:|---:|---:|
| smart_actual | 40.154694 | 4.234326 | 4.920 |
| smart_actual_respace | 40.461440 | 4.143532 | 5.193 |

### PEPE — capital 100 USDT

| Variante | Media USDT | Mediana | Positivas | p5 | Peor ventana | DD medio | Tiempo activo | Delta ref. (IC95%, wins) | Veredicto |
|---|---:|---:|---:|---:|---|---:|---:|---|---|
| simple_vol | -1.575609521875455 | 2.7662296255950025 | 57.95454545454546% | -26.458221846621 | 2026-01-11T07:20:00+00:00 (-33.81648388722) | 15.900194531386795% | 100.0% | -0.3475 [-1.3492, 0.6371], 28/88 | sin diferencia demostrada |
| smart_hoy | -4.419470092231251 | -4.280216199590001 | 25.0% | -11.280503976001997 | 2025-09-14T07:20:00+00:00 (-25.589776727049994) | 7.613683084879995% | 99.78653724747474% | -3.1914 [-5.7917, -0.4374], 24/88 | empeora |
| smart_actual | -1.2280720754350003 | 2.507656535175002 | 59.09090909090909% | -21.014427835638998 | 2025-10-05T07:20:00+00:00 (-31.1041185239) | 13.450889362917392% | 54.242818813131315% | — | referencia |
| smart_actual_respace | -1.2280720754350003 | 2.507656535175002 | 59.09090909090909% | -21.014427835638998 | 2025-10-05T07:20:00+00:00 (-31.1041185239) | 13.450889362917392% | 54.242818813131315% | 0.0000 [0.0000, 0.0000], 0/88 | sin diferencia demostrada |
| simple_vol | 7.013933 | -1.575610 | 5.932 |
| smart_hoy | 7.847790 | -4.419470 | 175.795 |
| smart_actual | 5.645552 | -1.228072 | 4.852 |
| smart_actual_respace | 5.645552 | -1.228072 | 4.852 |

Ciclos realizados netos frente al P&L total con inventario:

| Variante | Ganancia de ciclos (media USDT) | P&L total (media USDT) | Ciclos medios |
|---|---:|---:|---:|
| smart_actual | 5.645552 | -1.228072 | 4.852 |
| smart_actual_respace | 5.645552 | -1.228072 | 4.852 |

### PEPE — capital 1000 USDT

| Variante | Media USDT | Mediana | Positivas | p5 | Peor ventana | DD medio | Tiempo activo | Delta ref. (IC95%, wins) | Veredicto |
|---|---:|---:|---:|---:|---|---:|---:|---|---|
| simple_vol | -15.756092020335336 | 27.66228888671003 | 57.95454545454546% | -264.5822182021645 | 2026-01-11T07:20:00+00:00 (-338.16485850439994) | 15.900193967235918% | 100.0% | -3.4754 [-13.4919, 6.3709], 28/88 | sin diferencia demostrada |
| smart_hoy | -48.75508744177363 | -48.73833803757498 | 20.454545454545457% | -110.13688684575851 | 2025-09-14T07:20:00+00:00 (-286.99098323617) | 7.771186296396331% | 99.78653724747474% | -36.4744 [-63.3119, -7.5718], 24/88 | empeora |
| smart_actual | -12.280716963725224 | 25.076576379094945 | 59.09090909090909% | -210.14429224048595 | 2025-10-05T07:20:00+00:00 (-311.041236022) | 13.450888706471698% | 54.242818813131315% | — | referencia |
| smart_actual_respace | -12.280716963725224 | 25.076576379094945 | 59.09090909090909% | -210.14429224048595 | 2025-10-05T07:20:00+00:00 (-311.041236022) | 13.450888706471698% | 54.242818813131315% | 0.0000 [0.0000, 0.0000], 0/88 | sin diferencia demostrada |
| simple_vol | 70.139508 | -15.756092 | 5.932 |
| smart_hoy | 67.325125 | -48.755087 | 243.295 |
| smart_actual | 56.455672 | -12.280717 | 4.852 |
| smart_actual_respace | 56.455672 | -12.280717 | 4.852 |

Ciclos realizados netos frente al P&L total con inventario:

| Variante | Ganancia de ciclos (media USDT) | P&L total (media USDT) | Ciclos medios |
|---|---:|---:|---:|
| smart_actual | 56.455672 | -12.280717 | 4.852 |
| smart_actual_respace | 56.455672 | -12.280717 | 4.852 |

### ADA — capital 100 USDT

| Variante | Media USDT | Mediana | Positivas | p5 | Peor ventana | DD medio | Tiempo activo | Delta ref. (IC95%, wins) | Veredicto |
|---|---:|---:|---:|---:|---|---:|---:|---|---|
| simple_vol | -1.2281757045454549 | 2.553546054999998 | 62.5% | -23.438023621499998 | 2026-05-10T07:20:00+00:00 (-34.69975794) | 14.311247474890918% | 100.0% | 0.0482 [-0.9825, 0.9935], 35/88 | sin diferencia demostrada |
| smart_hoy | -2.9555604329545457 | -2.878636710000002 | 27.27272727272727% | -9.937825775500002 | 2025-10-05T07:20:00+00:00 (-20.38813254) | 7.333626052994734% | 99.63147095959597% | -1.6792 [-3.7043, 0.5094], 30/88 | sin diferencia demostrada |
| smart_actual | -1.276404681931818 | 2.482184414999999 | 61.36363636363637% | -18.359514883 | 2026-05-31T07:20:00+00:00 (-27.818424519999994) | 12.364184664459412% | 56.52777777777778% | — | referencia |
| smart_actual_respace | -1.2730754335227275 | 2.5617807300000024 | 61.36363636363637% | -18.359514883 | 2026-05-31T07:20:00+00:00 (-27.818424519999994) | 12.417536994211646% | 56.72979797979797% | 0.0033 [-0.0887, 0.1303], 4/88 | sin diferencia demostrada |
| simple_vol | 5.624123 | -1.228176 | 5.875 |
| smart_hoy | 2.986511 | -2.955560 | 95.966 |
| smart_actual | 4.290565 | -1.276405 | 4.580 |
| smart_actual_respace | 4.320399 | -1.273075 | 5.057 |

Ciclos realizados netos frente al P&L total con inventario:

| Variante | Ganancia de ciclos (media USDT) | P&L total (media USDT) | Ciclos medios |
|---|---:|---:|---:|
| smart_actual | 4.290565 | -1.276405 | 4.580 |
| smart_actual_respace | 4.320399 | -1.273075 | 5.057 |

### ADA — capital 1000 USDT

| Variante | Media USDT | Mediana | Positivas | p5 | Peor ventana | DD medio | Tiempo activo | Delta ref. (IC95%, wins) | Veredicto |
|---|---:|---:|---:|---:|---|---:|---:|---|---|
| simple_vol | -11.442787625000003 | 25.488096189999965 | 62.5% | -237.4398975145 | 2026-05-10T07:20:00+00:00 (-347.23319950999996) | 14.060693616600014% | 100.0% | 0.1827 [-10.0579, 9.7262], 35/88 | sin diferencia demostrada |
| smart_hoy | -43.86439000431819 | -41.60833053499999 | 18.181818181818183% | -120.86749894200001 | 2025-09-14T07:20:00+00:00 (-306.83643893) | 7.612248271405173% | 99.60029987373736% | -32.2389 [-52.7855, -9.3655], 22/88 | empeora |
| smart_actual | -11.625528252272732 | 24.81288015000007 | 61.36363636363637% | -183.69972698499998 | 2026-05-31T07:20:00+00:00 (-278.25400135999996) | 12.091154510247303% | 56.57788825757576% | — | referencia |
| smart_actual_respace | -11.595658224431817 | 25.5835783 | 61.36363636363637% | -183.69972698499998 | 2026-05-31T07:20:00+00:00 (-278.25400135999996) | 12.14451962114653% | 56.77990845959596% | 0.0299 [-0.8836, 1.2880], 4/88 | sin diferencia demostrada |
| simple_vol | 57.647777 | -11.442788 | 5.852 |
| smart_hoy | 60.815428 | -43.864390 | 207.182 |
| smart_actual | 44.189950 | -11.625528 | 4.580 |
| smart_actual_respace | 44.752707 | -11.595658 | 5.057 |

Ciclos realizados netos frente al P&L total con inventario:

| Variante | Ganancia de ciclos (media USDT) | P&L total (media USDT) | Ciclos medios |
|---|---:|---:|---:|
| smart_actual | 44.189950 | -11.625528 | 4.580 |
| smart_actual_respace | 44.752707 | -11.595658 | 5.057 |

## Veredicto

No hay mejora estadística demostrada de `smart_actual_respace` frente a `smart_actual` en ninguna moneda ni capital; todos los IC95 del delta contienen cero. PEPE es idéntico en ambas variantes en las ventanas observadas. La hipótesis queda **apagada por defecto**. No se propone activarla.

## Pruebas enfocadas

Línea base al inicio de Parte B: 12 archivos, **211 passed, 13 warnings**. Pase final enfocado: `tests/test_app_settings_api.py`, `tests/test_bench_smart_oos.py`, `tests/test_fase32_switches.py`, `tests/test_grid_adjust_shrink.py`, `tests/test_grid_advisor.py`, `tests/test_grid_monitor.py`, `tests/test_grid_policy.py`, `tests/test_grid_scan_service.py`, `tests/test_grid_sim_runner.py`, `tests/ui/test_frontend_format.py`, `tests/ui/test_grids_browser.py` y `tests/ui/test_laboratorio.py`: **214 passed, 13 warnings**. Verificación adicional de aperturas: `tests/test_grid_auto_open.py` y `tests/test_grids_api.py`, **51 passed**. Total final en los 14 archivos: **265 passed, 13 warnings**. Sin Testnet ni acceso de red en Parte B.

Commits selectivos en orden B1, B2, B3 y B4. Sin push.
