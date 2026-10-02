# Fase 15C-2 - diagnostico del estudio de objetivo

## Alcance y decision

`evaluate_target` no se cambio. Los usos mantienen la base cash existente y el objetivo del estudio de esta fase es explicar la discrepancia entre equity final y caja que pudo alcanzar el grid.

- `grid/sim/runner.py:50-72,136-138,373-402,408-432`: `max_cash` suma USDT y el valor neto de todas las celdas rentables/vendibles para formar la cota superior por vela; por separado `max_usdt_balance` mide la caja observada. Tambien calcula equity de celdas, equity con polvo, balances finales, tiempo en rango, ciclos, realized net y costo de polvo por ciclo.
- `grid/sim/target_study.py:14,71-75`: objetivos adicionales 0.5%, 1% y 2% para ventanas de 30 dias; ventanas de 90 dias conservan 3%, 5%, 10% y 18%.
- `grid/sim/target_study.py:88-90,105-125`: `max_cash` proyectado del baseline sin objetivo se usa como cota superior por ventana y se exportan sus conteos y agregados diagnosticos.
- `scripts/grid_sim.py:137-149`: el escritor del estudio exporta las nuevas columnas a `windows.csv` y conserva `baseline_windows.csv`. `--stdout-only` permite ejecutar sin escribir archivos de salida.
- `grid/sim/runner.py:221-229,301-310`: al vender una celda a mercado por objetivo tambien se cancela su SELL limite abierto. El estudio adicional descubrio que ese SELL podia quedar vivo y duplicar la venta; la cancelacion no cambia la seleccion ni el umbral de `evaluate_target`.

## Estudio reproducido

Comando ejecutado: `python scripts/grid_sim.py target-study --seed 42 --workers 4 --csv data/cache/xrp_5m.csv --stdout-only`. Exit 0, 308.386 s. SHA-256 del CSV: `d18d8f9253cf4a27fc91311b12b4106e3c3aadaef76d4d621cae63e8ca3a9b96`. 100 ventanas de 30 dias y 92 de 90 dias, solapadas con avance de 7 dias. Capital 100 USDT, 10 niveles, spacing 0.9%, stepSize 0.1. Semilla sin cambio: 42.

El criterio anterior `equity final >= meta` mezcla cash con inventario/polvo revalorizado. En las 100 ventanas de 30 dias simple, 39 terminan con equity simulada >=103; sin embargo **0/100 tienen `max_cash >=103`**. `max_cash` es USDT actual (incluidos BUYs abiertos/reservados) mas proceeds netos de todas las celdas que a ese cierre son rentables y pasan filtros de venta; se toma el maximo por vela del baseline sin objetivo. Es una cota superior simulada sin slippage/profundidad, no caja disponible observada ni una prueba de que las celdas pudieran ejecutarse simultaneamente en mercado real. Los objetivos >=3% quedan por encima de esa cota en la muestra de 30 dias.

Alcances observados y cota `max_cash` por ventana (las cifras fueron iguales para bases cash/equity en cada pareja):

| Ventana | objetivo | simple alcanzado / max_cash | SMART alcanzado / max_cash |
|---:|---:|---:|---:|
| 30d | 0.5% | 5/100 / 5/100 | 2/100 / 2/100 |
| 30d | 1% | 2/100 / 2/100 | 0/100 / 0/100 |
| 30d | 2% | 0/100 / 0/100 | 0/100 / 0/100 |
| 30d | 3%, 5%, 10%, 18% | 0/100 cada uno | 0/100 cada uno |
| 90d | 3% y 5% | 1/92 cada uno / 1/92 | 0/92 / 0/92 |
| 90d | 10% y 18% | 0/92 cada uno / 0/92 | 0/92 / 0/92 |

Para los dos unicos hits a 90 dias (simple, objetivos 3% y 5%), la mediana hasta objetivo es 64.375 dias; equity mediana al cierre 107.1376, y 114.8479 despues de liquidar repositorio hipoteticamente al bid final. El cambio medio vs baseline es 0.1239 pp (IC95 block [0, 0.3717]); el intervalo incluye cero. La cota `max_cash` del baseline supera 103 en 1/92 ventanas.

En la ventana 7 simple de 30 dias: equity final despues de liquidacion hipotetica **128.3409**, caja final **95.4221 USDT**, base final/polvo **13.1193 XRP**, held_qty **0**, `max_cash` **100**, `max_equity_cells` **100**, `max_equity_incl_dust` maximo durante la ventana **132.7734**, 144 ciclos, realized net **-6.0100 USDT** y 12.67% del tiempo dentro del rango. El valor final alto viene de marcar XRP no asignado a celdas; no es caja disponible ni un objetivo alcanzado.

## Costo de polvo por ciclo

Estimacion por ciclo cerrado: `(qty comprada - qty vendida) * precio de compra`, usando cantidades de eventos de fill. La referencia de spacing por celda es `10 USDT * 0.9% = 0.09 USDT`. Esto solo mide la diferencia de cantidad atribuible a polvo; no modifica el motor ni ajusta PnL.

| Grupo baseline | costo polvo medio/ciclo | fraccion del spacing $0.09 | polvo final mediano |
|---|---:|---:|---:|
| 30d simple | 0.2005 USDT | 2.227x (222.7%) | 5.1267 XRP |
| 30d SMART | 0.2968 USDT | 3.298x (329.8%) | 4.2303 XRP |
| 90d simple | 0.2036 USDT | 2.263x (226.3%) | 8.2116 XRP |
| 90d SMART | 0.2965 USDT | 3.295x (329.5%) | 4.2303 XRP |

La ventana 7 ilustra que el costo varia con el precio y los ciclos: **0.11186 USDT/ciclo**, 1.243x (124.3%) del spacing de 0.09 USDT. Medianas de max_equity incluyendo polvo fueron 104.0564 simple / 101.7805 SMART en 30d y 109.0111 / 102.3169 en 90d; medianas de max_cash y max_equity de celdas fueron 100 en esos grupos. Maxima equity con polvo tampoco es cash realizable.

Estos resultados son ventanas historicas solapadas y descriptivas. No prueban rentabilidad, independencia estadistica ni superioridad de una estrategia.

## Prueba sintetica cruzada

`tests/test_grid_target.py:87-123` construye las mismas velas y fills para el simulador y el monitor del motor SMART. Con BUYs aun abiertos, ambos alcanzan el objetivo, venden la celda 4, no dejan inventario en repositorio y terminan con el mismo cash total (**1013.79 USDT**). La prueba exige BUYs abiertos para cubrir el capital reservado dentro de cash.

Mutacion solicitada: se resto temporalmente `reserved_usdt` de `exchange.usdt` en el runner. El test sintetico fallo porque ya no eligio celdas para vender; se restauro la implementacion. El test de diagnosticos/ciclos esta en `tests/test_grid_target.py:126-140`; la regresion del SELL limite duplicado en `tests/test_grid_target.py:143-155`; y las columnas/export del estudio en `tests/test_grid_target.py:183-200`.

## Exportacion y limites de archivos

El writer normal de `scripts/grid_sim.py:137-149` exporta cada fila y sus nuevas columnas a `windows.csv`; baseline incluye las mismas metricas. Para respetar la lista cerrada de archivos de esta tarea, el estudio de validacion se ejecuto con `--stdout-only`: no se escribio ni sobrescribio CSV/JSON del estudio bajo `data/cache`. La suite creo unicamente su `--basetemp` de pytest en `data/cache/pytest_fase15c2_offline_final3` para el workaround de permisos. La suite live no se ejecuto. No se modifico `evaluate_target` ni se editaron archivos de codigo fuera de los permitidos.

## Validacion

Tests especificos: `tests/test_grid_target.py` -> 26 passed en 2.08 s; `py_compile` para los cuatro archivos permitidos y `git diff --check` terminaron con exit 0. La mutacion de cash que resta el saldo reservado por BUYs abiertos hizo fallar el test sintetico (no quedo ninguna celda de venta elegida) y fue revertida.

Suite offline solicitada: `python -m pytest tests -p no:cacheprovider -q -m "not live"`, con `ASPLE_OFFLINE=1`: **482 passed, 18 deselected, 7 warnings en 31.10 s**. La primera ejecucion sin `--basetemp` dio 444 passed y 38 errores de setup por `PermissionError` al enumerar `C:\Users\ramon\AppData\Local\Temp\pytest-of-ramon`; se repitio la suite completa con `--basetemp data/cache/pytest_fase15c2_offline_final3`, que paso. No se ejecuto ningun test live. Sin commit ni push.
