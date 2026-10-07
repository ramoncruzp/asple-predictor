# Fase 20E-1f — medición de causas de pérdida Smart

Medición offline y de solo lectura con los CSV cacheados `data/cache/vol_train/`; no se consultó la red ni la base de datos viva. Se reutilizan `frame_for`, `to_candles`, `run`, `compact_metrics` y `_simulation_window` del diagnóstico 20E-1e. Capital 1.000 USDT, comisión 0,1 %, 90 días disponibles, resync 3 salvo donde se indica.

## 1. Atribución de ventas y stop-loss

P&L neto por venta reconstruido del evento y del fill de compra asociado por celda; comisiones por clase incluyen la compra asociada y su venta, por lo que no suman comisiones de compras aún abiertas ni polvo residual. STOP_LOSS usa el cierre de esa vela (así lo hace el runner). Una recompra cuenta si hay BUY_FILLED de la misma celda en las 12 velas siguientes. Se reportan P&L realizado posterior al primer stop y P&L acumulado final de las celdas afectadas.

| Moneda | Estrategia | Clase de venta | Ventas | P&L neto USDT | Comisiones USDT |
|---|---|---|---:|---:|---:|
| ADA | smart | Grid normal (SELL_FILLED) | 326 | 111.52 | 19.53 |
| ADA | smart | STOP_LOSS (mercado) | 39 | -118.09 | 3.68 |
| ADA | smart | ADJUST/reconstrucción forzada | 0 | 0.00 | 0.00 |
| ADA | smart | Otras ventas a mercado (TARGET) | 0 | 0.00 | 0.00 |
| ADA | smart | STOP_LOSS: recompra misma celda ≤12 velas | 39 stops; 1 (2.56 %) | — | — |
| ADA | smart | Después del primer STOP_LOSS: P&L realizado en celdas afectadas | — | 7.90 | — |
| ADA | smart | P&L final acumulado de celdas afectadas | — | -31.59 | — |
| ADA | simple | Grid normal (SELL_FILLED) | 107 | 41.28 | 7.44 |
| ADA | simple | STOP_LOSS (mercado) | 0 | 0.00 | 0.00 |
| ADA | simple | ADJUST/reconstrucción forzada | 0 | 0.00 | 0.00 |
| ADA | simple | Otras ventas a mercado (TARGET) | 0 | 0.00 | 0.00 |
| ADA | simple | STOP_LOSS: recompra misma celda ≤12 velas | 0 stops; 0 (— %) | — | — |
| PEPE | smart | Grid normal (SELL_FILLED) | 238 | 81.59 | 11.14 |
| PEPE | smart | STOP_LOSS (mercado) | 38 | -112.99 | 3.57 |
| PEPE | smart | ADJUST/reconstrucción forzada | 0 | 0.00 | 0.00 |
| PEPE | smart | Otras ventas a mercado (TARGET) | 0 | 0.00 | 0.00 |
| PEPE | smart | STOP_LOSS: recompra misma celda ≤12 velas | 38 stops; 1 (2.63 %) | — | — |
| PEPE | smart | Después del primer STOP_LOSS: P&L realizado en celdas afectadas | — | 4.09 | — |
| PEPE | smart | P&L final acumulado de celdas afectadas | — | -44.03 | — |
| PEPE | simple | Grid normal (SELL_FILLED) | 195 | 72.97 | 10.26 |
| PEPE | simple | STOP_LOSS (mercado) | 0 | 0.00 | 0.00 |
| PEPE | simple | ADJUST/reconstrucción forzada | 0 | 0.00 | 0.00 |
| PEPE | simple | Otras ventas a mercado (TARGET) | 0 | 0.00 | 0.00 |
| PEPE | simple | STOP_LOSS: recompra misma celda ≤12 velas | 0 stops; 0 (— %) | — | — |

| Moneda | Estrategia | Ciclos | STOP_LOSS | P&L neto total | Comisiones total | DD máx. |
|---|---|---:|---:|---:|---:|---:|
| ADA | simple | 107 | 0 | 26.90 | 12.38 | 3.56 % |
| ADA | smart | 326 | 39 | -1.97 | 35.86 | 3.51 % |
| PEPE | simple | 195 | 0 | 52.29 | 21.14 | 12.82 % |
| PEPE | smart | 238 | 38 | -29.65 | 25.94 | 8.62 % |

`cycles_completed` cuenta únicamente `SELL_FILLED`: `grid/sim/exchange.py:105-111` incrementa `cell['cycles_completed']` al completar esa venta normal. `market_sell()` (`grid/sim/exchange.py:114-127`) registra P&L/comisión pero no incrementa ciclos; por eso STOP_LOSS y ventas de TARGET no son ciclos. `ADJUST` no vende posiciones: remapea/cancela órdenes (`grid/sim/runner.py:304-344`), por lo que no crea ventas forzadas. La categoría ADJUST/reconstrucción se conserva en tabla y debe dar cero si el motor no emitió ventas de ese tipo.

## 2. Barrido de `stop_loss_pct`

Cada fila cambia solo `params.stop_loss_pct`; las demás reglas Smart quedan activas. El runner comprueba stop-loss solo en cada resync, contra el cierre de la vela (`grid/sim/runner.py:153-168`), por lo que no es un stop intravela continuo. σ realizada: desviación estándar móvil de retornos logarítmicos con ventana de 4 o 24 velas horarias, expresada como fracción del precio (×100 para porcentaje). Movimiento adverso: mínimo low entre compra y venta por lote, relativo al precio de entrada; se muestran mediana/P90/máximo. Para PEPE, debido a que `SIM_FILTERS` del helper E1e tiene tick 0,0001 incompatible con este rango, se usó localmente un filtro sintético (tick 1e-9, step 1, minNotional 5 USDT); no es una lectura de filtros Binance y limita la fuerza de la comparación.

| Moneda | Stop % | Ciclos | P&L neto | Comisiones | DD máx. % | STOP_LOSS |
|---|---:|---:|---:|---:|---:|---:|
| ADA | 5 | 326 | -1.97 | 35.86 | 3.51 | 39 |
| ADA | 8 | 347 | 10.67 | 34.82 | 5.25 | 22 |
| ADA | 10 | 353 | 20.25 | 33.71 | 6.04 | 15 |
| ADA | 15 | 67 | 19.61 | 5.19 | 6.55 | 0 |
| ADA | 25 | 67 | 19.61 | 5.19 | 6.55 | 0 |
| PEPE | 5 | 238 | -29.65 | 25.94 | 8.62 | 38 |
| PEPE | 8 | 263 | -3.95 | 25.10 | 8.29 | 20 |
| PEPE | 10 | 265 | -12.78 | 24.83 | 9.01 | 18 |
| PEPE | 15 | 253 | -15.68 | 20.82 | 11.17 | 11 |
| PEPE | 25 | 68 | 24.96 | 6.86 | 9.46 | 0 |

| Moneda | σ realizada 4 h media / mediana | σ realizada 24 h media / mediana | EWMA interna 24 h media / mediana | Adverso mediana / P90 / máximo | Lotes medidos |
|---|---|---|---|---|---:|
| ADA | 0.78 % / 0.69 % | 0.85 % / 0.86 % | 9.18 % / 9.37 % (4 h media 3.75 %) | 1.24 / 6.00 / 13.25 % | 365 |
| PEPE | 0.90 % / 0.74 % | 1.00 % / 0.87 % | 11.10 % / 11.03 % (4 h media 4.53 %) | 1.34 / 6.99 / 16.32 % | 276 |

En ambas monedas el movimiento adverso mediano (1,24 % ADA; 1,34 % PEPE) queda por debajo de 5 %, pero el P90 (6,00 %; 6,99 %) lo supera: el 5 % cae en la cola de excursiones y su activación puede realizar pérdidas antes de la recuperación del grid.


## 3. XRP: colapso de ciclos Smart según cadencia

Rango fallback del análisis 20E-1e, no una predicción en vivo: piso 1,406842013993292; techo 1,5940000566479335; n=13. Para 5 m se usa la misma ventana horaria y se recorta al primer cierre dentro del rango, igual que 20E-1e. La línea temporal contiene los primeros diez eventos del registro y todos los eventos prioritarios (PAUSE/RESUME/CLOSE_REPOSITORY/ADJUST/ADJUST_REJECTED), sin duplicados.

| Escenario | Estrategia | Ciclos | P&L neto | Cierre / estado posterior / compras posteriores |
|---|---|---:|---:|---|
| 1h/resync=1 | simple | 200 | 114.66 | — |
| 1h/resync=1 | smart | 13 | 10.18 | 2026-08-22 04:00 UTC / CLOSED / 0 BUY_FILLED después |

**Eventos Smart — 1h/resync=1**

| Fecha UTC | Evento | Motivo | Estado antes → después |
|---|---|---|---|
| 2026-08-21 09:00 UTC | ADJUST_REJECTED | vol_unavailable | ACTIVE → ACTIVE |
| 2026-08-21 10:00 UTC | ADJUST | — | ACTIVE → ACTIVE |
| 2026-08-21 10:00 UTC | BUY_FILLED | — | ACTIVE → ACTIVE |
| 2026-08-21 11:00 UTC | BUY_FILLED | — | ACTIVE → ACTIVE |
| 2026-08-21 11:00 UTC | BUY_FILLED | — | ACTIVE → ACTIVE |
| 2026-08-21 11:00 UTC | BUY_FILLED | — | ACTIVE → ACTIVE |
| 2026-08-21 12:00 UTC | SELL_FILLED | — | ACTIVE → ACTIVE |
| 2026-08-21 12:00 UTC | SELL_FILLED | — | ACTIVE → ACTIVE |
| 2026-08-21 13:00 UTC | SELL_FILLED | — | ACTIVE → ACTIVE |
| 2026-08-21 13:00 UTC | SELL_FILLED | — | ACTIVE → ACTIVE |
| 2026-08-22 00:00 UTC | ADJUST | — | ACTIVE → ACTIVE |
| 2026-08-22 02:00 UTC | ADJUST_REJECTED | cooldown | ACTIVE → ACTIVE |
| 2026-08-22 04:00 UTC | CLOSE_REPOSITORY | out_of_range | ACTIVE → CLOSED |

| 5m/resync=3 | simple | 247 | 144.95 | — |
| 5m/resync=3 | smart | 25 | 18.94 | 2026-08-22 04:45 UTC / CLOSED / 0 BUY_FILLED después |

**Eventos Smart — 5m/resync=3**

| Fecha UTC | Evento | Motivo | Estado antes → después |
|---|---|---|---|
| 2026-08-21 09:45 UTC | ADJUST_REJECTED | vol_unavailable | ACTIVE → ACTIVE |
| 2026-08-21 10:05 UTC | BUY_FILLED | — | ACTIVE → ACTIVE |
| 2026-08-21 10:15 UTC | ADJUST | — | ACTIVE → ACTIVE |
| 2026-08-21 10:20 UTC | BUY_FILLED | — | ACTIVE → ACTIVE |
| 2026-08-21 10:20 UTC | BUY_FILLED | — | ACTIVE → ACTIVE |
| 2026-08-21 10:55 UTC | SELL_FILLED | — | ACTIVE → ACTIVE |
| 2026-08-21 11:05 UTC | SELL_FILLED | — | ACTIVE → ACTIVE |
| 2026-08-21 11:30 UTC | BUY_FILLED | — | ACTIVE → ACTIVE |
| 2026-08-21 11:50 UTC | BUY_FILLED | — | ACTIVE → ACTIVE |
| 2026-08-21 12:00 UTC | BUY_FILLED | — | ACTIVE → ACTIVE |
| 2026-08-21 23:30 UTC | ADJUST | — | ACTIVE → ACTIVE |
| 2026-08-22 03:15 UTC | ADJUST_REJECTED | cooldown | ACTIVE → ACTIVE |
| 2026-08-22 04:30 UTC | PAUSE | break_prob | ACTIVE → PAUSED |
| 2026-08-22 04:45 UTC | CLOSE_REPOSITORY | out_of_range | PAUSED → CLOSED |

| 5m/resync=36 | simple | 152 | 85.08 | — |
| 5m/resync=36 | smart | 6 | 4.17 | 2026-08-22 03:45 UTC / HOLDING / 0 BUY_FILLED después |

**Eventos Smart — 5m/resync=36**

| Fecha UTC | Evento | Motivo | Estado antes → después |
|---|---|---|---|
| 2026-08-21 09:45 UTC | ADJUST_REJECTED | vol_unavailable | ACTIVE → ACTIVE |
| 2026-08-21 10:05 UTC | BUY_FILLED | — | ACTIVE → ACTIVE |
| 2026-08-21 12:45 UTC | ADJUST | — | ACTIVE → ACTIVE |
| 2026-08-21 12:50 UTC | BUY_FILLED | — | ACTIVE → ACTIVE |
| 2026-08-21 13:20 UTC | BUY_FILLED | — | ACTIVE → ACTIVE |
| 2026-08-21 13:45 UTC | SELL_FILLED | — | ACTIVE → ACTIVE |
| 2026-08-21 15:50 UTC | SELL_FILLED | — | ACTIVE → ACTIVE |
| 2026-08-21 15:50 UTC | SELL_FILLED | — | ACTIVE → ACTIVE |
| 2026-08-21 16:05 UTC | BUY_FILLED | — | ACTIVE → ACTIVE |
| 2026-08-21 19:00 UTC | BUY_FILLED | — | ACTIVE → ACTIVE |
| 2026-08-22 03:45 UTC | CLOSE_REPOSITORY | out_of_range | ACTIVE → HOLDING |

La causa medida de los recuentos bajos es el cierre temprano, no un ritmo de órdenes: 1h×1 cierra el 22-ago 04:00 UTC (CLOSED); 5m×3 cierra 04:45 UTC (CLOSED); 5m×36 cierra 03:45 UTC (HOLDING). En los tres escenarios hay cero BUY_FILLED después del cierre, mientras Simple completa 200/247/152 ciclos porque no aplica la decisión Smart CLOSE_REPOSITORY. `CLOSE_REPOSITORY` cancela compras y deja HOLDING solo para posiciones abiertas que esperan venta, o CLOSED si no queda inventario (`grid/sim/runner.py:225-267`); el bucle sigue procesando velas, pero no repone compras.

## 4. Cadencia

`grid_monitor_interval` tiene default de 900 s (15 min): `config/settings.py:40`; `grid/monitor.py:70-82` programa la pasada periódica y una pasada inicial. Cada pasada obtiene precios y niveles, ejecuta STOP_LOSS antes de sincronizar, sincroniza según ACTIVE/PAUSED/HOLDING, obtiene σ, evalúa la política Smart y puede pausar, ajustar, reanudar o cerrar (`grid/monitor.py:391-488`; aplicación de decisiones en las líneas siguientes). En simulación, el mercado/fills procesa cada vela (`grid/sim/runner.py:145-152`), pero evaluación de política y colocación/recolocación de órdenes ocurre en cada múltiplo de `resync_candles` (`:153-184`, `:431-444`). Cadencias equivalentes: 1 h×1=1 h; 5 m×3=15 min; 5 m×36=3 h. La corrida principal ADA/PEPE usa 1 h×3=3 h, doce veces más lenta que el monitor configurado; aun así, el monitor real opera por tiempo y recibe actualizaciones de mercado, no se equipara exactamente a velas históricas.

## 5. Unidades de σ

`ewma_sigma_24h` (`grid/sim/data.py:77-86`) calcula retornos logarítmicos, alpha para 72 h bajo 12 velas/h y devuelve `sqrt(variance×288)`: fracción, no porcentaje, anualizada a 24 h suponiendo velas de 5 min. `evaluate_grid` reenvía `sigma_24h` a `break_prob` (`grid/policy.py:576-602`); `break_prob` espera sigma fraccional de 24 h y la escala a `sigma_h = sigma_24h × sigma_scale × sqrt(horizon_h/24)` (`:459-486`). En estas corridas horarias, el promedio EWMA medido es 9,18 % ADA y 11,10 % PEPE, frente a σ realizada rolling 24 h de 0,85 % y 1,00 %. La causa principal del desacople es que la función fija 288 barras/día y alpha de 12 barras/h: con datos horarios, el alpha implica half-life real de 864 velas = 36 días, y el factor 288 en vez de 24 puede inflar la escala hasta √12 para varianza comparable. Por tanto no se comparan directamente como estimadores calibrados. El sigma del campeón queda NO VERIFICADO: este análisis solo lee CSV y código, no artefactos/DB.

## 6. Veredicto (cinco líneas)

1. **Stop-loss 5 %:** parcial: PEPE Smart -29.65 vs Simple 52.29; 8 % mejora 25.70 USDT y 25 % mejora 54.61, pero baja de 238 a 68 ciclos.
2. **ADA:** 5→10 % mejora 22.22 USDT; aun así el barrido no demuestra una regla universal (PEPE 10 % empeora frente a 8 %).
3. **Cambio candidato, no aplicado:** evaluar `stop_loss_pct` configurable por símbolo/régimen, sin subirlo globalmente; 25 % elimina estos stops pero cambia radicalmente los ciclos y no prueba seguridad.
4. **Cadencia del Advisor:** sí, comparar 5m×3 (15 min, como monitor) con 1h×3 (3 h) antes de usar Smart para decidir; la propia XRP muestra sensibilidad de 25 a 6 ciclos.
5. **Límites:** filtros sintéticos de PEPE, σ del campeón y equivalencia exacta con la captura UI NO VERIFICADOS; resultados de PEPE son indicativos, no certificados.

## No verificado y alcance

- No se comparó contra la captura exacta de PEPEUSDT de UI (piso/techo, capital y configuración sí se conservaron, pero no se confirmó que la ventana/data coincidan).
- No se consultó la σ del campeón de volatilidad: requeriría artefactos/manifest o API/DB, fuera del alcance de CSV solamente.
- La comisión por venta STOP_LOSS se reconstruye con qty neta × close × 0,1 %; el evento STOP_LOSS solo guarda P&L, no detalla la comisión por separado.
- La atribución por celda empareja FIFO por `level_idx`; si un ADJUST reusa una celda con lotes previos no resueltos, ese emparejamiento puede no reflejar una identidad económica distinta. Se reportan los eventos ADJUST para contextualizarlo.
- Para PEPEUSDT no se consultaron filtros de exchange; se usó precisión sintética suficiente para el rango de precio proporcionado, por lo que la simulación no certifica redondeo/notional reales.
- No se ejecutaron pruebas; no se editó código de producción/pruebas, no se tocó la base viva ni se consultó la red.
