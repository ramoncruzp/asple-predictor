# Fase 20E-1e - diagnóstico Smart vs Simple

Diagnóstico de solo lectura: usa CSV de `data/cache/vol_train/` y código. No consulta la base viva ni modifica producción.

## Datos de entrada

| CSV | Filas | Rango de fechas UTC | Filas en últimos 90 días |
|---|---:|---|---:|
| `data\cache\vol_train\ada_1h.csv` | 17519 | 2024-10-07T21:00:00+00:00 a 2026-10-07T19:00:00+00:00 | 2161 (2026-07-09T19:00:00+00:00 a 2026-10-07T19:00:00+00:00) |
| `data\cache\vol_train\ada_5m.csv` | 210239 | 2024-10-07T20:35:00+00:00 a 2026-10-07T20:25:00+00:00 | 25921 (2026-07-09T20:25:00+00:00 a 2026-10-07T20:25:00+00:00) |
| `data\cache\vol_train\xrp_1h.csv` | 17519 | 2024-10-07T00:00:00+00:00 a 2026-10-06T22:00:00+00:00 | 2161 (2026-07-08T22:00:00+00:00 a 2026-10-06T22:00:00+00:00) |
| `data\cache\vol_train\xrp_5m.csv` | 210239 | 2024-10-06T23:55:00+00:00 a 2026-10-06T23:45:00+00:00 | 25921 (2026-07-08T23:45:00+00:00 a 2026-10-06T23:45:00+00:00) |

Los CSV terminan en fechas distintas: ADA llega a 2026-10-07 y XRP a 2026-10-06. La ventana de simulación empieza en la primera vela horaria cuyo cierre entra estrictamente en el rango, según `api/routes/grid_advisor.py:50-69`.

- **ADA**: piso 0.233600, techo 0.276200, n=18; rango obtenido de: Advisor screen values supplied in prompt.
  Ventana 1 h: 2026-08-22T02:00:00+00:00 a 2026-10-07T19:00:00+00:00 (46.71 días, 1122 velas).
- **XRP**: piso 1.406842, techo 1.594000, n=13; rango obtenido de: recommend() on cached CSV; forecast deliberately unavailable offline.
  Ventana 1 h: 2026-08-21T09:00:00+00:00 a 2026-10-06T22:00:00+00:00 (46.54 días, 1118 velas).
  `recommend()` devolvió `range_mode=centrado`, precio actual 1.497500, fuente de sigma `realizada`; se hizo inaccesible el pronóstico para mantener la llamada sin DB y usar el fallback realizado. No equivale a una recomendación con artefactos/consenso en vivo.

## M0 - reproducción 1 h, `resync_candles=3`

| Moneda | Estrategia | Ciclos | P&L neto | Comisiones | Drawdown máx. | Comprar y mantener | P&L/ciclo | Comisión/ciclo |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| ADA | simple | 107 | 26.90 | 12.38 | 3.56% | 53.28 | 0.2514 | 0.1157 |
| ADA | smart | 326 | -1.97 | 35.86 | 3.51% | 53.28 | -0.0060 | 0.1100 |
| XRP | simple | 162 | 86.42 | 25.31 | 8.25% | 54.73 | 0.5334 | 0.1562 |
| XRP | smart | 261 | 6.26 | 36.86 | 8.84% | 54.73 | 0.0240 | 0.1412 |

Comparación con la pantalla de ADA: la pantalla reportó Simple 107 ciclos / P&L +24,97 / fees 12,44 / DD 3,56%; Smart 326 / -3,22 / 35,95 / 3,51%; buy-and-hold +49,15. La reproducción caché conserva 107/326 ciclos, pero los P&L y buy-and-hold difieren; el CSV llega más tarde (2026-10-07) y por eso cambian los cierres de la ventana. No se interpretan como divergencia del motor.

## M1-M2 - eventos y ciclos

`cycles_completed` es `sum(c['cycles_completed'] for c in cells)` (`grid/sim/metrics.py:20`), es decir, suma los contadores de ciclo por celda. El porcentaje same-candle empareja cada `SELL_FILLED` con el `BUY_FILLED` previo de la misma celda y compara sus timestamps; el denominador son los pares de fills reconstruidos.

| Moneda | Estrategia | BUY/SELL fills | STOP_LOSS | ADJUST / rechazado | PAUSE / RESUME | TARGET | MAX_DAYS | Otros | Ciclos same-candle | P&L/ciclo | Fees/ciclo |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| ADA | simple | 115/107 | 0 | 0 / 0 | 0 / 0 | 0 | 0 | 0 | 0.00% (0/107) | 0.2514 | 0.1157 |
| ADA | smart | 371/326 | 39 | 22 / 1 | 0 / 0 | 0 | 0 | 0 | 0.00% (0/326) | -0.0060 | 0.1100 |
| XRP | simple | 166/162 | 0 | 0 / 0 | 0 / 0 | 0 | 0 | 0 | 0.00% (0/162) | 0.5334 | 0.1562 |
| XRP | smart | 297/261 | 30 | 30 / 7 | 2 / 2 | 0 | 0 | 0 | 0.00% (0/261) | 0.0240 | 0.1412 |

## M3 - cadencia en velas de 1 h

| Moneda | resync | Simple ciclos / P&L / fees | Smart ciclos / P&L / fees |
|---|---:|---|---|
| ADA | 1 | 162 / 49.47 / 18.51 | 462 / 8.22 / 51.17 |
| ADA | 3 | 107 / 26.90 / 12.38 | 326 / -1.97 / 35.86 |
| ADA | 6 | 78 / 14.87 / 9.15 | 240 / -36.47 / 27.67 |
| ADA | 12 | 55 / 5.80 / 6.64 | 159 / -31.09 / 18.76 |
| XRP | 1 | 215 / 113.53 / 33.64 | 13 / 7.23 / 1.88 |
| XRP | 3 | 162 / 86.42 / 25.31 | 261 / 6.26 / 36.86 |
| XRP | 6 | 110 / 59.48 / 17.21 | 219 / -7.00 / 29.34 |
| XRP | 12 | 68 / 34.59 / 10.72 | 146 / -4.30 / 20.21 |

## M4 - velas de 5 min en la misma ventana

La ventana UTC coincide con la de 1 h; por el requisito de inicialización del simulador, se recorta el prefijo hasta el primer cierre de 5 min estrictamente dentro del rango (diferencia máxima inferior a 5 min). `resync=3` equivale a 15 min y `resync=36` a 3 h.

| Moneda | resync 5m | Simple ciclos / P&L / fees | Smart ciclos / P&L / fees |
|---|---:|---|---|
Ventana ADA: 2026-08-22T02:25:00+00:00 a 2026-10-07T19:55:00+00:00, 13459 velas.
| ADA | 3 | 203 / 66.34 / 23.09 | 493 / -33.02 / 57.14 |
| ADA | 36 | 109 / 27.50 / 12.66 | 297 / 2.63 / 34.03 |
Ventana XRP: 2026-08-21T09:45:00+00:00 a 2026-10-06T22:55:00+00:00, 13407 velas.
| XRP | 3 | 277 / 149.75 / 43.21 | 25 / 14.12 / 3.68 |
| XRP | 36 | 156 / 82.21 / 24.46 | 6 / 3.34 / 0.87 |

## M5 - ablación de Smart, 1 h / resync=3

Solo cambia el parámetro mostrado. `pause_enter_prob=None` y `pause_exit_prob=None` desactivan las reglas probabilísticas de pausa; otras condiciones de política (capital atrapado/celdas libres) siguen activas. `stop_loss_pct` no admite `None` porque `validate_params` exige que sea >0 (`grid/policy.py:154-161`); se usa 1.000.000% como umbral centinela, NO como interruptor semántico.

| Moneda | Variante | Ciclos | P&L neto | Fees | Δ ciclos vs default | Δ P&L vs default | Eventos Smart |
|---|---|---:|---:|---:|---:|---:|---|
| ADA | default | 326 | -1.97 | 35.86 | 0 | 0,00 | {'ADJUST': 22, 'ADJUST_REJECTED': 1, 'BUY_FILLED': 371, 'SELL_FILLED': 326, 'STOP_LOSS': 39} |
| ADA | adjust_off | 1 | -9.22 | 0.44 | -325 | -7.24 | {'BUY_FILLED': 4, 'CLOSE_REPOSITORY': 1, 'PAUSE': 1, 'SELL_FILLED': 1, 'STOP_LOSS': 3} |
| ADA | stop_loss_1000000pct_sentinel | 67 | 19.61 | 5.19 | -259 | 21.58 | {'ADJUST': 2, 'ADJUST_REJECTED': 1, 'BUY_FILLED': 67, 'CLOSE_REPOSITORY': 1, 'PAUSE': 1, 'SELL_FILLED': 67} |
| ADA | pause_off | 326 | -1.97 | 35.86 | 0 | 0.00 | {'ADJUST': 22, 'ADJUST_REJECTED': 1, 'BUY_FILLED': 371, 'SELL_FILLED': 326, 'STOP_LOSS': 39} |
| XRP | default | 261 | 6.26 | 36.86 | 0 | 0,00 | {'ADJUST': 30, 'ADJUST_REJECTED': 7, 'BUY_FILLED': 297, 'PAUSE': 2, 'RESUME': 2, 'SELL_FILLED': 261, 'STOP_LOSS': 30} |
| XRP | adjust_off | 51 | -0.06 | 8.76 | -210 | -6.33 | {'BUY_FILLED': 57, 'CLOSE_REPOSITORY': 1, 'PAUSE': 2, 'RESUME': 1, 'SELL_FILLED': 51, 'STOP_LOSS': 6} |
| XRP | stop_loss_1000000pct_sentinel | 84 | 39.86 | 10.50 | -177 | 33.60 | {'ADJUST': 4, 'ADJUST_REJECTED': 7, 'BUY_FILLED': 84, 'CLOSE_REPOSITORY': 1, 'DUST_SWEPT': 1, 'PAUSE': 3, 'RESUME': 2, 'SELL_FILLED': 84} |
| XRP | pause_off | 281 | 11.73 | 39.19 | 20 | 5.46 | {'ADJUST': 35, 'ADJUST_REJECTED': 5, 'BUY_FILLED': 316, 'PAUSE': 1, 'RESUME': 1, 'SELL_FILLED': 281, 'STOP_LOSS': 32} |

## M6 - recorrido del precio en la ventana 1 h

| Moneda | Inicio / fin | Mín. / máx. cierre | Fuera del rango | Salidas / reentradas |
|---|---|---|---:|---:|
| ADA | 0.2421 / 0.255 | 0.1912 / 0.2792 | 64.62% (725/1122) | 4 / 4 |
| XRP | 1.4198 / 1.4975 | 1.2618 / 1.6628 | 40.34% (451/1118) | 28 / 28 |

## M7 - sigma interna

| Moneda | Media / mín. / máx. sigma 24 h | Primeras seis | NaN iniciales | Comparación campeón/consenso |
|---|---|---|---:|---|
| ADA | 0.091800 / 0.000000 / 0.119294 | 0.000000, 0.003560, 0.020008, 0.046959, 0.047016, 0.047280 | 0 (cero iniciales: 1) | NO VERIFICADO: no se consultó forecast, artefactos de modelo ni DB. |
| XRP | 0.091220 / 0.000000 / 0.105421 | 0.000000, 0.011926, 0.015375, 0.017542, 0.021358, 0.022124 | 0 (cero iniciales: 1) | NO VERIFICADO: no se consultó forecast, artefactos de modelo ni DB. |

EWMA interna: halflife 72 h; `run_simulation` no recibe `sigma_values`, construye la sigma de las velas recortadas (`grid/sim/runner.py:126-131`, `grid/sim/data.py:77-86`). Así, no hay NaN pero sí un cero inicial y calentamiento desde el inicio de la ventana.

## Respuestas

1. Con los inputs medidos, ADA Smart completa 219 ciclos más que Simple. XRP da 162 Simple y 261 Smart; NO reproduce la captura anterior de 142/126. Se usó un rango obtenido por recommend() con fallback realizado offline, así que la diferencia vieja no queda explicada por esta reproducción. M1/M5 sí muestran mecanismos bajo este rango medido.
2. Los ciclos no se explican por compras y ventas del mismo timestamp: M2 informa el porcentaje observado. Son fills emparejados entre velas. M3 y M4 muestran la sensibilidad a la frecuencia de evaluación; como varía con la cadencia, no se puede atribuir toda la diferencia a la política sin fijar esa cadencia.
3. Stop-loss centinela: ADA P&L -1.97 a 19.61 y ciclos 326 a 67; XRP 6.26 a 39.86 y ciclos 261 a 84. Desactivar ajuste reduce el P&L ADA a -9.22 y XRP a -0.06; quitar pausa deja XRP en 11.73. Los efectos son ablaciones individuales, no aditivos. Las fees explican parte del coste, pero el P&L neto por ciclo y los STOP_LOSS muestran que el resultado no se atribuye solo a comisiones. Stop-loss no admite apagado estricto por params.
4. La sigma interna empieza en cero, sin NaN, y converge causalmente dentro de la ventana recortada; su calentamiento inicial puede alterar decisiones Smart de las primeras horas. La comparación cuantitativa con campeón/consenso queda NO VERIFICADA.
5. Los parámetros Smart de Advisor vienen de `DEFAULT_SMART_PARAMS` al no pasar `params` (`api/routes/grid_advisor.py:295-296`, `grid/sim/runner.py:79-99`, `grid/policy.py:12-44`). La apertura Smart también aplica esos defaults y fija `horizon_h=4` si no se envía (`api/routes/grids.py:184-190`). Diferencia material: Advisor usa `SIM_FILTERS` salvo que su servicio de scan aporte filtros de símbolo (`api/routes/grid_advisor.py:239-246`); en esta reproducción offline quedó en `SIM_FILTERS`. No se prueba aquí la equivalencia operacional completa con el monitor en vivo.
6. **Parcial**: sirve para comparar resultados históricos del mismo simulador y los escenarios medidos, pero no basta para decidir que Smart sea superior; la cadencia altera resultados, las reglas de ajuste/stop-loss generan actividad adicional y la sigma de modelo para la recomendación XRP no se verificó.

## Limitaciones y no verificado

- El XRP se obtuvo invocando la función real `recommend()` con el CSV cacheado, parámetros Moderado/90 días y el pronóstico sustituido por respuesta vacía para impedir accesos a DB; la sigma cae al fallback realizado. No representa el artefacto campeón/consenso usado por una instancia viva.
- La reproducción ADA conserva piso/techo/n entregados por Ramón; no rederiva esos valores de una recomendación.
- Los CSV son snapshots distintos en fecha y no se ejecutó una suite de pruebas; esta fase solo mide.
- `M2` same-candle es un emparejamiento por nivel y timestamp de fills; porcentaje = pares same-candle / ventas emparejadas.
- El stop-loss no posee interruptor por parámetros; resultado M5 usa un umbral centinela y está marcado como tal.
- No se usó ni modificó la base viva, ni se consultó Binance/Testnet.
