# Fase 15D - discrepancias y limites

- La capa DB concreta es `database/db_manager.py`; usa SQLAlchemy y migra columnas SQLite en el constructor. `database/schema.sql` no define la tabla operativa `grids`, por eso no se cambió. La migración de `dust_qty` es idempotente y el valor se conserva como texto decimal para evitar la conversión de SQLite NUMERIC a float; los lectores aritméticos deben convertir mediante `Decimal(str(...))`.
- El barrido se limita a la cantidad persistida del grid y cumple `step_size`, `min_qty`, `min_notional` y `apply_min_to_market`. No consulta ni utiliza el balance XRP de la cuenta para determinar su cantidad.
- La tarifa estimada por el monitor y por el CLI continúa fijada en 0.1%, como el cálculo de objetivos previo. El barrido real reconstruye la comisión a partir de las trades retornadas por el exchange. No se verificó ninguna ejecución real.
- Las pruebas automatizadas cubren cantidad vendida acotada por registro, replay idempotente, residual exacto y recuperación tras respuesta perdida. No se ejecutó una campaña de mutación alterando temporalmente producción; por tanto, se reportan las aserciones protectoras, no un número de mutantes eliminados.
- El parámetro `dust_sweep_threshold_pct` queda opcional y desactivado por defecto. El monitor lo evalúa únicamente cuando el objetivo no se ha alcanzado y no está en cierre por política. CLOSE y TARGET siguen usando sus rutas de cierre existentes.
- Una celda que entra en estado DUST conserva su `held_qty`, como hace el cierre existente. Esa cantidad también queda identificada en `dust_qty`, pero se resta para planificar el monto libre de barrido y evitar doble venta. Por ello `dust_qty` incluye inventario DUST aún referenciado por la celda y no equivale literalmente a `exchange.base - SUM(held_qty)` mientras esa celda siga registrada; el test sintético verifica residual y proceeds para polvo BUY no asignado.
- En el replay SMART, el snapshot del motor se toma antes de ejecutar la acción del tick; la traza simulada se toma después. La traza expone `fees` para las comisiones ordinarias y `fees_incl_dust_sweep` para el total posterior al sweep, evitando comparar instantes distintos. La suite offline y la batería grid pasan con esta separación.
- Las únicas verificaciones de exchange de esta fase usaron fakes y simulador con `ASPLE_OFFLINE=1`; no se llamó a Testnet ni a Binance live.

## Fase 15D-2 — límites de verificación

- No se encontró en este checkout el archivo citado `claude/Prompt_Codex_Fase15D.md`; la implementación se basó en la especificación detallada recibida en la solicitud 15D-2.
- Hay prueba sintética de expiración en el monitor real con exchange fake y prueba simple separada en el simulador, además de paridad TARGET motor/simulador preexistente. Falta aún una única prueba de paridad motor/simulador para `MAX_DAYS` que compare exactamente los mismos índices de celdas en repositorio y cash final.
- El plan se persiste antes del cierre y el motor conserva su WAL de cierre, CID y barrido. No se inyectó caída en cada frontera interna del cierre por plazo (cancelación, movimiento a repositorio, sweep y emisión del evento); la prueba previa de sweep sí cubre recuperación tras respuesta perdida.
- Las pruebas max-days comprueban helper y rutas de cierre, pero no prueban explícitamente la precedencia de TARGET y MAX_DAYS simultáneos mediante un caso integrado de monitor/simulador.
- El evento `MAX_DAYS_REACHED` informa `dust_qty_swept_or_pending` como saldo residual registrado tras el cierre; los proceeds/fee del sweep se consultan en el evento `DUST_SWEPT`. No se desglosan en el evento max-days mismo.
- El estudio de plazo es descriptivo y usa ventanas solapadas; “sin plazo” no crea un evento artificial de cierre/repositorio al final de ventana, por lo que celdas al repositorio y PnL no realizado de ese grupo quedan como no aplicables.

La última revisión añadió al evento de plazo la cantidad barrida explícita (`dust_swept`) además del residual pendiente (`dust_pending`); ambos se derivan de la respuesta del barrido y del registro persistido.

## Hallazgo 15D-3 previo al cambio de producción

La prueba nueva de paridad `test_max_days_engine_and_simulator_match_inventory_free_cash_and_dust` falló antes de entrar al monitor: `GridEngine.create_grid(..., strategy="simple", params={"max_days": 1})` rechaza todos los `params` para la estrategia simple (`grid/engine.py`, validación de creación). Esto contradice el soporte `grid_ctl open --max-days` para simple y hace imposible aplicar max_days en el motor simple. Se permite corregir producción únicamente esta validación y volver a ejecutar la misma prueba.

## Hallazgos de tests de frontera 15D-3 previos al cambio de monitor

Dos pruebas nuevas fallaron de forma reproducible: (1) si `max_days_close_plan.phase=STARTED` ya estaba persistido pero el proceso cae antes de `close_grid`, una nueva instancia con reloj por debajo del plazo no reanuda el cierre; el monitor vuelve a evaluar solo la edad. (2) si el grid llega a CLOSED y el proceso cae después del barrido pero antes de persistir `MAX_DAYS_REACHED`, los grids CLOSED sin `target_close_plan` no vuelven a entrar al monitor y el evento se pierde. Esto rompe la reanudación write-ahead y la frontera “sweep → evento”; se autoriza corregir solo `grid/monitor.py` y añadir los casos de prueba correspondientes.

## Resolución de hallazgos de Fase 15D-3

- Resuelto: `GridEngine.create_grid` acepta para estrategia simple el `max_days` ya validado, incluido el objeto de parámetros efectivo que construye `grid_ctl`; no abre parámetros smart arbitrarios para grids simples.
- Resuelto: el monitor reanuda `max_days_close_plan.phase=STARTED` aunque la edad recalculada ya no venza (p. ej. reloj corregido tras reinicio), conserva en el plan los datos del evento y recupera `MAX_DAYS_REACHED` si el proceso cae después del sweep. La recuperación revisa eventos persistidos antes de emitir para no duplicar.
- Las pruebas de paridad y de las cuatro fronteras pasan con fake local. No se hizo llamada real Binance/Testnet.
