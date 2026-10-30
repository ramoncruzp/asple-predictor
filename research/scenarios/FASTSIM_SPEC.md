# Especificación del replay de referencia

Esta nota describe el comportamiento observado en el código de producción; fija los
detalles que debe respetar el simulador rápido. Los números de línea se refieren a
los archivos de esta revisión y se deben actualizar si el replay cambia.

## Plan y estado inicial de las celdas

- `run_simulation` crea líneas con `compute_lines` y reparte el capital entre las
  celdas mediante `plan_cells` (`grid/sim/runner.py:127-142`). Cada celda contiene
  `price`, `sell_price`, cantidad, costo, inventario y contador de ciclos.
- `plan_cells` calcula capital/celda como capital dividido por número de espacios,
  redondeado hacia abajo a ocho decimales; cantidad = `round_qty_down(capital_celda /
  buy_price)`, y valida notional y cantidad neta estimada (`grid/levels.py:84-108`).
- No hay inventario inicial. Una celda cuyo precio de compra esté estrictamente bajo
  el bid sintético inicial puede empezar `BUY_OPEN`; si está al precio o sobre él,
  empieza `IDLE`. `BUY_OPEN` significa estado habilitado para colocar una orden, no
  que la orden ya exista (`grid/levels.py:77-82, 100-108`; `runner.py:134-142`).

## Llenados, actividad y orden intravela

- `SimExchange.process` recorre una copia de `orders.items()` ordenada por índice
  entero de celda. Compra solo si `low < price`; venta solo si `high > price`; la
  comparación es estricta (`grid/sim/exchange.py:64-70`).
- Una orden se ignora cuando `i < active_from` (`exchange.py:65-67`). El runner
  procesa órdenes antes de resincronizar y coloca órdenes nuevas con
  `active_from=i+1`, por lo que la orden nueva no participa en esa vela y puede
  llenarse desde la vela siguiente (`grid/sim/runner.py:216, 235, 642-650`).
- La resincronización sucede después de los fills cuando `i % resync_candles == 0`;
  el valor predeterminado es 3. Tras una compra, la venta se crea en ese mismo paso
  si la vela de compra es múltiplo de 3; en otro caso, en la siguiente resincronización
  (a más tardar dos velas después). La venta queda activa desde `i+1`. Tras vender,
  la celda vuelve a `IDLE`; la recompra sigue la misma regla de resincronización y
  solo se coloca si `buy_price < close` (`exchange.py:107-110`; `runner.py:642-650`).
- Si varias órdenes son tocadas por la misma vela, los fills ocurren en orden
  ascendente de índice de celda, antes de que el runner coloque las órdenes nuevas
  (`exchange.py:64-70`; `runner.py:216-235`). El replay no resuelve cuál extremo
  intravela ocurrió primero: aplica las condiciones al rango OHLC de esa vela.

## Cantidades, comisiones y efectivo reservado

- `plan_cells` redondea la cantidad comprable con `filters.round_qty_down` y también
  verifica que la cantidad neta estimada después de una comisión de 0,1 % siga
  cumpliendo filtros (`grid/levels.py:94-99`).
- `SimExchange` usa `Decimal`. Con `fee_asset=None` (el valor del replay de pieza),
  la comisión de compra se cobra en el activo base y la de venta en USDT
  (`grid/sim/exchange.py:25-32`). La comisión base se resta de la cantidad bruta,
  luego el inventario de la celda se redondea al step size; la diferencia se acumula
  como polvo (`exchange.py:73-93`). En la venta, la comisión se resta de proceeds
  USDT (`exchange.py:94-110`).
- La orden de compra reserva `price × qty` y, como `sell_fee_asset` es USDT en el
  modo predeterminado, añade además 0,1 % de reserva. Si reservas abiertas más la
  nueva superan el efectivo, se lanza `SimInsufficientFunds`; el runner registra
  rechazo y deja la celda `IDLE` (`exchange.py:41-55`; `runner.py:644-653`). Esto
  reserva comisión aunque el cargo efectivo predeterminado de compra sea en base.
- El polvo solo se vende cuando `plan_dust_sweep` lo considera vendible y el runner
  ejecuta explícitamente el sweep durante cierre, meta/corte u otras transiciones;
  no se barre en cada vela ordinaria (`exchange.py:130-144`; `runner.py:378-381,
  408-411, 664-668`).

## Curva de pieza y evaluación de cartera

- Para `strategy="simple"`, `simulate_piece_curve` conserva filas de trace y las
  empaca con `pack_curve` (`grid/sim/meta_portfolio.py:98-122`). En el runner de
  pieza, el trace se emite en velas alineadas a 15 minutos, más la primera y última
  vela. Guarda efectivo USDT, equity y timestamp (`runner.py:691-716`). La equity
  incluida en la curva es efectivo + activo retenido valorado al close con descuento
  de comisión (`runner.py:680-687, 712-716`). `pack_curve` conserva start, step/count,
  capital y arrays de efectivo/equity comprimidos como float32 (`meta_portfolio.py:9-31`).
- `evaluate_portfolio` alinea las curvas por timestamps comunes y suma efectivo e
  inventario, incluyendo como efectivo quieto el capital no asignado. La meta se
  cruza por **efectivo** acumulado (`cash >= capital + target`); el límite de pérdida
  se cruza por equity muestreada (`equity <= capital × (1-loss_pct/100)`), no por el
  low intravela. Si ambos ocurren, el corte gana cuando ocurre en la misma vela o
  antes de la meta. El resultado se trunca en el primer índice de pérdida; no liquida
  ni reescribe las curvas originales (`grid/sim/meta_portfolio.py:57-87`).
- El corte de `run_simulation(meta_max_loss_pct=...)` es otro mecanismo: usa el low
  de la vela, liquida inventario al low y cierra; la curva `simulate_piece_curve` no
  pasa ese argumento, por lo que el límite de Meta evaluado por `evaluate_portfolio`
  es el basado en puntos muestreados (`grid/sim/runner.py:654-668`; `meta_portfolio.py:98-122`).

## Bot individual

- `run_meta_bot` compra como máximo una vez cuando `low <= buy_price`, vende como
  máximo una vez cuando `high >= sell_price` en una vela posterior (`was_bought`),
  y nunca recompra (`grid/sim/meta_bot.py:33-44`). A diferencia de las órdenes de
  `SimExchange`, sus límites son inclusivos.
- Valida capital/precios/comisión, margen neto descontando dos comisiones, cantidad
  y notional mínimos para compra y venta neta; redondea cantidad con los filtros
  (`meta_bot.py:13-27`). La comisión es 0,1 % por lado en USDT para este replay.
- El corte del bot, si se solicita directamente, marca al low, realiza el inventario
  y termina en esa vela; el wrapper `simulate_piece_curve` no pasa límite de pérdida
  (`meta_bot.py:58-68`; `grid/sim/meta_portfolio.py:111-118`).

## Límites de esta especificación

El fastsim debe modelar Simple y el bot por separado: el primer caso usa fills
estrictos con resincronización diferida; el bot usa cruces inclusivos inmediatos.
No es equivalente inferir el orden intravela únicamente por la secuencia de high y
low: el replay solo consulta ambos extremos. Las comisiones, redondeo, reservas y
polvo dependen de `SymbolFilters` y no se aproximan como PnL porcentual al final.
