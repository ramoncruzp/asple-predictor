# Fase 18B-7 — Polvo informativo en selección y elegibilidad

## Cambios

- `grid/structure.py:124-145,188-222`: `suggest_structure` ahora elige con celda válida, espaciado mínimo y margen bruto tras comisiones >= `min_margin_after_fees_pct` (predeterminado 0,7 %). `evaluate_levels` y `spacing_ok` se conservaron; el polvo/neto se reporta informativamente.
- `grid/scanner.py:49-56,73-76,99-130`: transmite el mínimo configurado, usa `feasible` como guarda de estructura y margen bruto para `cost_headroom` y avisos. Neto/polvo continúan en `measured`.
- `grid/scan_service.py:92-96` y `api/routes/grids.py:211-217`: suministran el mínimo del servidor al selector.
- `api/routes/grid_advisor.py:72-90,122-130`: selección, objetivo y ciclos usan margen bruto; añade `net_per_cycle_after_dust_usdt` y conserva las mediciones de polvo/neto.
- `frontend/app.js:262`: el indicador del Advisor presenta margen bruto tras comisiones; el neto tras polvo queda rotulado como informativo.
- `tests/test_grid_structure.py:131-154`, `tests/test_grid_scanner.py:73-139`, `tests/test_grid_advisor.py:63-81`: regresiones ADA, elegibilidad con neto <= 0, margen bruto bajo y suelo de celda del exchange. `tests/test_scanner_spread_tick.py:42-46` fija el aviso al margen bruto. `tests/test_grid_auto_open.py:120-134` ya prueba que auto-open llega a la apertura con neto cero y registra polvo.
- `REPORTE_FASE18B_6.md` y `DISCREPANCIAS_FASE18B_6.md`: se restauraron tildes, eñes y flechas dañadas, preservando su contenido histórico.

## Casos y pruebas

| Caso | Prueba / resultado |
|---|---|
| ADA 100 USDT, 18 niveles: bruto 0,742 %, polvo 0,46 %, neto 0,282 % | `test_ada_100_usdt_18_levels_meets_gross_target_with_informational_dust`; estructura viable. `test_ada_100_usdt_structure_is_scanner_eligible_at_gross_margin_target`; Scanner elegible. `test_ada_100_usdt_advisor_target_uses_gross_margin_and_keeps_dust_informational`; Advisor `target_met=true` con 0,7 %. |
| Neto por polvo cero o negativo con celda válida y margen bruto suficiente | `test_feasible_structure_with_zero_net_edge_remains_eligible`, `test_nonpositive_dust_net_does_not_reduce_scanner_cost_headroom` y `test_auto_open_allows_nonpositive_estimated_net_and_records_dust_warning`; elegible, score descriptivo y rama auto-open alcanzada. |
| Margen bruto inferior al mínimo | `test_margin_after_fees_below_minimum_is_infeasible` y `test_gross_margin_below_server_minimum_blocks_scanner`; inviable y no elegible. |
| Celda menor al mínimo de exchange | `test_exchange_cell_floor_still_blocks_scanner_structure`; no elegible. |
| Expectativa anterior de celda fija | `test_small_cell_margin_is_warning_not_a_fixed_large_capital_exclusion` ahora comprueba el suelo 5,5 USDT: el selector bruto propone 5,714 USDT, no el valor histórico fijo de 10. |

## Verificación

- Suite dirigida Python: `ASPLE_OFFLINE=1 .\venv\Scripts\python.exe -m pytest tests/test_grid_structure.py tests/test_grid_structure_preview_api.py tests/test_grid_scanner.py tests/test_scanner_spread_tick.py tests/test_grid_advisor.py tests/test_grid_auto_open.py tests/test_grids_api.py -p no:cacheprovider --basetemp .pytest-18b7 -q` → **74 passed in 5.69s**.
- `ASPLE_OFFLINE=1 .\venv\Scripts\python.exe -m pytest tests/test_frontend_scanner.py -p no:cacheprovider --basetemp .pytest-18b7 -q` → **24 passed in 9.28s**.
- `node --check frontend/app.js` → código 0, sin salida.
- Mutación A: sustituir el criterio nuevo por `spacing_ok and cell_ok`; `test_structure_selection_uses_gross_floor_independent_of_net_dust` falló como se esperaba: mutante eligió 21 niveles frente a 18. Restauración comparada byte a byte: `True`.
- Mutación B: restaurar la condición `net_edge_pct_per_cycle > 0`; `test_nonpositive_dust_net_does_not_reduce_scanner_cost_headroom` falló en elegibilidad como se esperaba. Restauración comparada byte a byte: `True`. **2/2 mutaciones detectadas.**
- Hubo una invocación inicial sin pruebas porque PowerShell no expandió `tests/test_grid_structure*.py`; después se enumeraron y pasaron rutas explícitas. La primera suite explícita detectó una expectativa vieja de 10 USDT por celda; se corrigió para verificar el suelo configurado y la suite final quedó verde.
- Los dos informes 18B-6 y este informe están en UTF-8 sin BOM, CR ni U+FFFD; sin `?` en lugar de letra acentuada. En `frontend/app.js`, el cambio nuevo usa solo texto ASCII y no agrega caracteres no ASCII sin escape.

## Polvo que aún condiciona métricas

- `grid/structure.py:112-120`: `evaluate_levels` aún calcula `required_spacing`, neto y `spacing_ok` incluyendo polvo, como pidió el alcance; `suggest_structure` ya no utiliza `spacing_ok` para seleccionar ni para declarar viabilidad.
- `api/routes/grid_structure.py:66,71-74,91-95`: la estimación informativa de ciclos de esa otra ruta sigue calculándose con neto tras polvo y queda vacía si el neto no es positivo. No determina `feasible` ni el objetivo bruto.
- `api/routes/grid_structure.py:68-69,86-88` y `grid/auto_open.py:82-91`: polvo puede activar un aviso informativo; no es guarda de elegibilidad/apertura. El test auto-open con neto cero lo verifica.
- El nuevo campo `api/routes/grid_advisor.py:89,128` también informa el neto por ciclo tras polvo; selección, meta y ciclos objetivo usan margen bruto.

**NO VERIFICADO:** conducta de mercado real o de Testnet y renderizado del Advisor en navegador real. No se conectó a Binance/Testnet ni se leyó `.env`. No se ejecutó suite completa. No se hizo stage, commit ni push.
