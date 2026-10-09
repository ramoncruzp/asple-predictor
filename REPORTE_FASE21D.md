# Fase 21d — pendientes menores

## Preflight y línea base
HEAD al iniciar: `4d7cf15` (Fase 21c); Fase 22 (`5618285`) está en el historial. Árbol limpio y sin stage. Línea base antes de editar: no-UI `1039 passed, 1 skipped, 18 deselected, 40 warnings in 78.22s (0:01:18)`; UI `109 passed, 2 warnings in 127.29s (0:02:07)`.

## Estado por ítem

| Ítem | Estado | Evidencia | Resultado / limitaciones |
|---|---|---|---|
| G1 | HECHO | `grid/loan_cohorts.py:33-53`; `tests/test_loan_cohorts_process.py:114-143` | Rechazo de archivo vacío reproducido antes; ahora inicializa el byte y adquiere. Dos procesos sobre archivo vacío no se pisan. Mutación restaurada; SHA abajo. |
| G2 | HECHO | `scripts/grid_ctl.py:167-176, 199-205, 230-250`; `tests/test_grid_loan_pairs.py:394-435`; reutiliza `api/routes/grids.py:569-650` | Rojo: `open-pair` desconocido. Verde: dry-run no crea, `--execute` sin `--confirm` retorna 2 y no abre, con ambos crea 2 grids simulados. `2 passed, 18 deselected`. Mutación de guarda detectada; SHA abajo. La ruta CLI invoca el handler compartido de API; no se llamó a Testnet ni a Binance durante las pruebas. |
| E4 | HECHO EN WINDOWS | `grid/loan_cohorts.py:56-82`; `tests/test_loan_cohorts_process.py:114-220` | `7 passed`. Dos procesos reales usaron `msvcrt` para esperar, liberar y agotar timeout; además cubrieron archivo vacío preexistente e inicialización concurrente. Rama POSIX (`fcntl`) NO VERIFICADA en esta máquina. |
| E1 | HECHO | `scripts/vol_consensus_eval.py:36-68, 205-216, 272, 360-370`; `tests/test_vol_consensus_eval.py:1-17`; `tests/test_vol_consensus_per_symbol.py:34-40` | Rojo: el helper anterior esperaba una ruta y trataba el dict como `Path`. Verde: `8 passed`. Rangos TEST y TRAIN disjuntos → `true`; solapados → `false`; faltantes → `null`; XRP permanece `false` por selección previa. Los rangos por horizonte quedan serializados en el resultado. Mutación devolver siempre `True` detectada; SHA abajo. |
| E2 | HECHO EN 21c | `database/learning_engine.py:108-122`; `tests/test_direction_verification.py:161-180` | Verificado con `git grep -n model_accuracy_by_condition -- . ':!models/saved' ':!data'`: no hay lectores; solo esquema/declaración, método desactivado, reportes y prueba. La prueba confirma cero consultas/escrituras. No se repitió la implementación. |
| E3 | HECHO, ya estaba aplicado | `api/routes/grid_advisor.py:328-344, 418-425`; `tests/test_grid_advisor.py:417-432` | El cálculo elige `grids` según capital/estructura y el simulador ya recibía `n=grids`, `capital=capital`. Con capital 100 recomienda 11 niveles (≤15); la simulación recibe ese n y 100 USDT, y `capital_per_grid` es 100/11. La prueba nueva cubre esos parámetros; la prueba existente conserva el contrato de claves principales (`tests/test_grid_advisor.py:217-224`). Prueba dirigida: `35 passed, 2 warnings`. Mutación a `n=20` detectada; SHA abajo. No se cambió el código de producción de Advisor porque el comportamiento pedido ya existía. |
| G3 | HECHO (diagnóstico) | Corrida final UI con `--durations=15` | UI: `109 passed, 2 warnings in 146.35s (0:02:26)`. De las 15 pruebas más lentas, solo `test_loan_pairs_panel_renders_conclusive_and_nonconclusive_without_verdict_colors` corresponde a la UI de pares de Fase 22; fue 1,37 s de setup. No se cambió ninguna prueba por lentitud. |

### Mutaciones y restauración

| Ítem | SHA antes y restaurado | SHA mutado | Resultado |
|---|---|---|---|
| G1, volver a rechazar archivo vacío | `grid/loan_cohorts.py` `a662d7acf8e7573f3970e00fc4073d6e5fcebb10f4bb5ed6a4e538f1fe64071c` | `aa993a076231b81ee983779d03ea9de1ce64ce95790996d8add89a07eac162e2` | Falló la prueba de adquisición con archivo vacío como se esperaba. |
| G2, ignorar la guarda `--confirm` | `scripts/grid_ctl.py` `7bb65fd475b7671f563d2ad43787c59f6b5ef37d9119d24756f66a6b7291fe16` | `b1404f9a9142ac33c5bab0e6005eb7675d10a4f3a02540dcf16acbf773e81e97` | Falló: API rechazó con 422 y CLI devolvió 1 en vez de la prevalidación 2. Se restauró antes de añadir la opción explícita `--dry-run` y el encabezado de token. |
| E1, devolver siempre `True` | `scripts/vol_consensus_eval.py` `cad919fa0b8bd4a43630f746ce657709717a3dcf4917be40c2a2820fe29797d8` | `95a3f68821458422ee3d55eef7d8b194bb8cfdd913354efbfc06c924b359083a` | Falló la aserción del intervalo solapado. |
| E3, ignorar niveles recomendados y fijar 20 | `api/routes/grid_advisor.py` `81fc5c50fb49cae855c3f418260532dfbceda222fed075530783469e10f28e81` | `f54ac9b2f89f7878e7b6321dc708ce342424b8b9e00de60addb10e56316a6f05` | Falló: simulación recibió 20 frente a 11 recomendados. |

## Resultados de suites finales

No-UI, ejecutada una vez con `ASPLE_OFFLINE=1`:

```text
1045 passed, 1 skipped, 18 deselected, 40 warnings in 107.04s (0:01:47)
```

UI, ejecutada una vez al final con `--durations=15`:

```text
109 passed, 2 warnings in 146.35s (0:02:26)
```

Tiempos más lentos (el resto fueron menores):

```text
10.60s call     tests/ui/test_coins_browser.py::test_coin_polling_stops_when_readiness_becomes_complete
5.64s call     tests/ui/test_coins_browser.py::test_coin_polling_stops_when_leaving_coins
2.60s call     tests/ui/test_models_page.py::test_late_xrp_response_is_ignored_after_switching_to_ada
1.37s setup    tests/ui/test_grids_browser.py::test_loan_pairs_panel_renders_conclusive_and_nonconclusive_without_verdict_colors
1.22s setup    tests/ui/test_coins_browser.py::test_grid_advisor_preserves_not_ready_conflict_detail
1.14s setup    tests/ui/test_grids_browser.py::test_dashboard_symbol_uses_active_registry_and_resets_removed_selection
1.10s setup    tests/ui/test_battle.py::test_battle_score_cards_use_responsive_desktop_grid
1.10s setup    tests/ui/test_models_page.py::test_vol_training_has_own_card_and_volatility_specific_dialog
1.09s setup    tests/ui/test_grids_browser.py::test_grids_scanner_account_navigation_has_no_js_errors_and_preserves_health_chip
1.07s setup    tests/ui/test_grids_browser.py::test_grid_advisor_can_calculate_realized_volatility_for_selected_other_symbol[error-amable]
1.07s setup    tests/ui/test_models_page.py::test_consensus_persistence_note_and_xrp_history_selection_header
1.02s setup    tests/ui/test_grids_browser.py::test_scanner_testnet_price_guard[outside]
0.97s setup    tests/ui/test_grids_browser.py::test_grid_advisor_volatility_button_recalculates_xrp_model_forecast
0.94s setup    tests/ui/test_models_page.py::test_training_summary_uses_selected_symbol_and_selected_horizon
0.90s setup    tests/ui/test_models_page.py::test_volatility_chart_label_explains_hourly_sigma_and_four_hour_conversion
```

## Cierre y límites

Pruebas ejecutadas con `ASPLE_OFFLINE=1`; las aperturas fueron simuladas en fixtures SQLite temporales. No se leyó ni escribió la BD real, `data/`, `models/saved/` ni `.env`; no hubo llamadas de red. El índice quedó vacío y no se hizo stage, commit ni push. Cambios limitados a `grid/loan_cohorts.py`, `scripts/grid_ctl.py`, `scripts/vol_consensus_eval.py`, cinco archivos de prueba y este reporte. `api/routes/grid_advisor.py` solo tuvo una mutación temporal, restaurada con SHA idéntico. `git status --short`: ocho archivos modificados y este reporte nuevo; índice vacío.

## Archivos para commits separados

- G1/E4: `grid/loan_cohorts.py`, `tests/test_loan_cohorts_process.py`.
- G2: `scripts/grid_ctl.py`, `tests/test_grid_loan_pairs.py`.
- E1: `scripts/vol_consensus_eval.py`, `tests/test_vol_consensus_eval.py`, `tests/test_vol_consensus_per_symbol.py`.
- E3: `tests/test_grid_advisor.py`; la producción ya tenía el comportamiento requerido.
- Evidencia: `REPORTE_FASE21D.md`.

## Decisiones pendientes de Ramón

- Ninguna para cerrar esta lista. La rama POSIX del candado queda NO VERIFICADA porque el entorno actual es Windows; no bloquea el criterio de humo Windows solicitado.
