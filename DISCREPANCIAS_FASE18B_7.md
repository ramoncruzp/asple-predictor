# Fase 18B-7 — Discrepancias y límites

La selección de estructura, elegibilidad del Scanner, puntuación de margen y objetivo/ciclos del Advisor ya no dependen del polvo. El cambio conserva `evaluate_levels` y su campo diagnóstico `spacing_ok`, tal como exige la fase.

## Métricas con polvo aún presente

- `api/routes/grid_structure.py:66,71-74,91-95`: `cycles_to_target` conserva una estimación teórica de ciclos usando neto tras polvo; es informativa y no altera `feasible` ni el objetivo bruto. No se editó esa ruta, fuera del alcance de los cuatro cambios autorizados.
- `api/routes/grid_structure.py:68-69,86-88` y `grid/auto_open.py:82-91`: los umbrales de advertencia comparan polvo con margen bruto, pero solo producen avisos. Auto-open llega a abrir en la prueba con neto cero.
- `grid/structure.py:112-120`: el evaluador puro conserva `spacing_ok` con polvo para no cambiar `evaluate_levels`; la selección nueva no consume ese booleano.

## Pruebas existentes ajustadas

- `tests/test_grid_scanner.py::test_small_cell_margin_is_warning_not_a_fixed_large_capital_exclusion`: la expectativa exacta previa de 10 USDT dependía del nivel escogido antes del criterio bruto. Se reemplazó por la condición real autorizada: celda >= 5,5 USDT; el nuevo nivel da 5,714 USDT y sigue elegible.
- `tests/test_scanner_spread_tick.py::test_scanner_score_defaults_are_pinned_and_low_edge_warning_travels`: el fixture fija `edge_gross_pct` bajo y neto negativo, porque el aviso ahora se deriva del margen tras comisiones.
- `tests/test_grid_scanner.py::test_gross_margin_below_server_minimum_blocks_scanner` cubre explícitamente que margen bruto bajo el mínimo produce estructura inviable y fila no elegible.
- `tests/test_grid_auto_open.py::test_auto_open_allows_nonpositive_estimated_net_and_records_dust_warning` ya cubría neto cero, llegada a apertura y evento informativo; no fue necesario cambiar su lógica.

## Límites

La prueba de Advisor usa una evaluación sintética controlada para verificar la selección y el cálculo 0,742 % / 0,282 %; no certifica comportamiento de mercado. Las mutaciones comprobaron las dos regresiones solicitadas, no constituyen una campaña completa.

**NO VERIFICADO:** operación real en Binance/Testnet y renderizado del Advisor en navegador real; ningún servicio externo fue llamado. No se leyó `.env`, no se corrió la suite completa y no se hizo stage/commit/push.
