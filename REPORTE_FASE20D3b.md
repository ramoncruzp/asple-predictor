# Fase 20D-3b: separación y corrección de fallos

## D1. Línea base y comparación medida

Se exportó el commit 3117003 mediante git archive a %TEMP%\asple_head_3117003_20261007 (351 miembros). El HEAD actual al iniciar esta tarea era e49be89; la comparación usa 3117003 como se pidió. No se usaron stash, checkout, reset ni worktree. No se necesitaron archivos ignorados para correr la suite.

Listas finales calculadas desde los XML de pytest:

- NUEVOS al cierre: 0
  - (lista vacía)

- PREEXISTENTES: 3
  - tests/test_frontend_symbols.py::test_grid_symbol_registry_options_selection_fallback_and_escaping — ya fallaba en 3117003; causa probable: fallback de símbolos, ajeno a 20D-3.
  - tests/test_volatility_live.py::test_api_battle_hides_live_metrics_until_minimum_and_history_delegates — ya fallaba en la línea base; contrato legacy de Battle/historial, probable cambio API por símbolo (20B-2a-2), atribución no concluyente.
  - tests/test_volatility_live.py::test_frontend_volatility_contract_fields — ya fallaba en la línea base; contrato legacy de Battle/historial, probable cambio API por símbolo (20B-2a-2), atribución no concluyente.

- ARREGLADOS: 1
  - tests/test_grid_monitor.py::test_app_lifespan_starts_without_monitor_when_testnet_credentials_missing — DummyDB no implementaba los métodos que usa onboarding durante el arranque; se completó el doble de prueba sin debilitar la intención.

Al comienzo de D1 se midieron 53 NUEVOS respecto a la base; todos quedaron corregidos. IDs completos:
- tests/test_frontend_scanner.py::test_node_partial_or_error_never_reports_open_and_cancel_resets_state[error] — corregido, véase D2.
- tests/test_grid_ctl_open.py::test_open_dry_run_reports_levels_params_and_calibrated_false — corregido, véase D2.
- tests/test_grid_guards.py::test_injected_engine_provider_blocks_smart_without_sigma — corregido, véase D2.
- tests/test_grid_maxdays.py::test_closed_grid_does_not_emit_max_days_again_after_restart — corregido, véase D2.
- tests/test_grid_maxdays.py::test_holding_repository_is_not_reprocessed_as_an_expiring_grid — corregido, véase D2.
- tests/test_grid_maxdays.py::test_max_days_engine_and_simulator_match_inventory_free_cash_and_dust — corregido, véase D2.
- tests/test_grid_maxdays.py::test_max_days_precedes_pause_resume_and_adjust[PAUSE-False] — corregido, véase D2.
- tests/test_grid_maxdays.py::test_max_days_precedes_pause_resume_and_adjust[RESUME-True] — corregido, véase D2.
- tests/test_grid_maxdays.py::test_policy_close_precedes_expired_max_days — corregido, véase D2.
- tests/test_grid_maxdays.py::test_real_monitor_expires_grid_through_repository_close_without_market_sale — corregido, véase D2.
- tests/test_grid_maxdays.py::test_restart_after_buy_cancellation_finishes_repository_close_without_repeating_actions — corregido, véase D2.
- tests/test_grid_maxdays.py::test_restart_after_repository_move_before_sweep_sweeps_once — corregido, véase D2.
- tests/test_grid_maxdays.py::test_restart_after_sweep_before_event_recovers_one_max_days_event — corregido, véase D2.
- tests/test_grid_maxdays.py::test_started_close_plan_resumes_after_restart_even_if_clock_moves_back — corregido, véase D2.
- tests/test_grid_maxdays.py::test_target_precedes_expired_max_days_on_same_monitor_pass — corregido, véase D2.
- tests/test_grid_monitor_adjust.py::test_monitor_adjust_cooldown_and_block_event_rate_limit — corregido, véase D2.
- tests/test_grid_monitor_adjust.py::test_monitor_adjust_failure_is_partial_and_records_failure — corregido, véase D2.
- tests/test_grid_monitor_adjust.py::test_monitor_adjusts_smart_grid_and_leaves_simple_grid_unchanged — corregido, véase D2.
- tests/test_grid_monitor_db.py::test_monitor_tables_have_exact_audit_columns — corregido, véase D2.
- tests/test_grid_monitor_loans.py::test_monitor_isolates_loan_failure_to_partial_grid_run — corregido, véase D2.
- tests/test_grid_monitor_loans.py::test_monitor_skips_loans_on_the_same_pass_as_a_successful_adjust — corregido, véase D2.
- tests/test_grid_monitor_loans.py::test_monitor_syncs_then_processes_enabled_smart_grid_loans — corregido, véase D2.
- tests/test_grid_monitor_policy.py::test_break_probability_pause_is_not_resumed_without_fresh_volatility — corregido, véase D2.
- tests/test_grid_monitor_policy.py::test_pause_for_non_volatility_reason_can_resume_without_volatility — corregido, véase D2.
- tests/test_grid_monitor_policy.py::test_smart_grid_auto_pauses_and_resumes_with_hysteresis — corregido, véase D2.
- tests/test_grid_monitor_policy.py::test_smart_out_of_range_auto_close_goes_to_repository_and_simple_is_untouched — corregido, véase D2.
- tests/test_grid_monitor_policy.py::test_vol_unavailable_event_is_rate_limited_to_six_hours — corregido, véase D2.
- tests/test_grid_policy_adjust.py::test_adjust_decision_obeys_cooldown_and_trapped_capital_guards — corregido, véase D2.
- tests/test_grid_policy_adjust.py::test_adjust_decision_triggers_near_edge_and_keeps_log_width_centered — corregido, véase D2.
- tests/test_grid_sim_smart_fidelity.py::test_adjust_rejections_match_engine_and_simulator[capital_per_cell_below_min_notional_margin-params2-600-True] — corregido, véase D2.
- tests/test_grid_sim_smart_fidelity.py::test_adjust_rejections_match_engine_and_simulator[cooldown-params0-12000-False] — corregido, véase D2.
- tests/test_grid_sim_smart_fidelity.py::test_adjust_rejections_match_engine_and_simulator[planned_cell_below_binance_minimum_margin-params3-600-False] — corregido, véase D2.
- tests/test_grid_sim_smart_fidelity.py::test_adjust_rejections_match_engine_and_simulator[trapped_capital_pct-params1-12000-True] — corregido, véase D2.
- tests/test_grid_sim_smart_fidelity.py::test_combined_360_candle_sequence_adjust_pause_resume_stoploss — corregido, véase D2.
- tests/test_grid_sim_smart_fidelity.py::test_pause_execution_cancels_buys_and_keeps_existing_sells — corregido, véase D2.
- tests/test_grid_sim_smart_fidelity.py::test_pause_max_duration_closes_inventory_to_repository — corregido, véase D2.
- tests/test_grid_sim_smart_fidelity.py::test_real_two_day_xrp_segment_has_smart_intervention — corregido, véase D2.
- tests/test_grid_sim_smart_fidelity.py::test_sigma_provider_and_simulator_use_current_pass_not_lagged_value — corregido, véase D2.
- tests/test_grid_sim_smart_fidelity.py::test_smart_adjust_preserves_existing_live_sell_order — corregido, véase D2.
- tests/test_grid_sim_smart_fidelity.py::test_smart_close_cancels_pending_buy_orders_and_records_each_cancellation — corregido, véase D2.
- tests/test_grid_sim_smart_fidelity.py::test_smart_close_values_repository_inventory_at_market — corregido, véase D2.
- tests/test_grid_sim_smart_fidelity.py::test_smart_monitor_clock_and_sigma_are_injected_at_each_replay_pass — corregido, véase D2.
- tests/test_grid_sim_smart_fidelity.py::test_smart_pause_resume_hysteresis_keeps_sells_and_does_not_rebuy — corregido, véase D2.
- tests/test_grid_sim_smart_fidelity.py::test_stoploss_executes_before_policy_and_matches_fee_and_cell_state — corregido, véase D2.
- tests/test_grid_sim_smart_fidelity.py::test_unavailable_sigma_is_reported_by_both_replays — corregido, véase D2.
- tests/test_grid_target.py::test_monitor_skips_target_when_loan_saga_is_pending_and_emits_throttled_event — corregido, véase D2.
- tests/test_grid_target.py::test_monitor_target_precedes_adjust_and_closes_smart_grid_with_both_totals — corregido, véase D2.
- tests/test_grid_target.py::test_paused_grid_can_reach_target_without_volatility — corregido, véase D2.
- tests/test_grid_target.py::test_risk_close_precedes_and_suppresses_profit_target — corregido, véase D2.
- tests/test_grid_target.py::test_synthetic_target_reaches_same_result_in_simulator_and_grid_monitor — corregido, véase D2.
- tests/test_volatility_provider.py::test_provider_returns_none_for_unsupported_symbol_or_missing_champion — corregido, véase D2.
- tests/test_volatility_provider.py::test_realized_sigma_cache_observes_30_minute_ttl — corregido, véase D2.
- tests/test_volatility_provider.py::test_realized_sigma_orders_agitated_above_calm_and_requires_100_bars — corregido, véase D2.

## D2. Clasificación y cambios

| Categoría | Cambios y evidencia |
|---|---|
| (a) Contrato intencional | tests/test_grid_ctl_open.py:32 actualiza el default Smart de 24 a 4 h. Los escenarios que específicamente prueban lógica de 24 h ahora fijan horizon_h=24 en tests/test_grid_monitor_adjust.py:20,33,45, tests/test_grid_policy_adjust.py:9 y tests/test_grid_sim_smart_fidelity.py:373.
| (a) Contrato intencional | Fakes aceptan get(symbol, horizon_h=24), igual que la firma real: tests/test_grid_guards.py:34, tests/test_grid_monitor_adjust.py:13, tests/test_grid_monitor_policy.py:16, tests/test_grid_monitor_loans.py:16, tests/test_grid_target.py:440 y tests/sim_replay_adapter.py:122. Mantienen el propósito de sus escenarios y toleran que el monitor pase horizonte.
| (a) Contrato intencional | tests/test_grid_monitor_db.py:35 espera sigma_monitor_h, monitor_horizon_h y source. tests/test_frontend_scanner.py:204-205 verifica que se muestre el detalle conflict sin prefijo 409, conforme al contrato intencional de A8 ya comprometido.
| (b) Defecto real | grid/volatility_provider.py:64-71 filtra la fila de forecast por símbolo para impedir reutilizar XRP al consultar otra moneda.
| (b) Defecto real | grid/volatility_provider.py:123 conserva la causa específica del fallback realizado, como insufficient_bars, en vez de reemplazarla por forecast_unavailable.
| (b) Defecto real | grid/volatility_provider.py:133-136 devuelve la instancia cacheada para 24 h y solo deriva vistas para otros horizontes, respetando identidad y TTL.
| (c) Sin clasificar | 0 NUEVOS al cierre; ningún traceback nuevo quedó sin clasificar.

Los tres arreglos de producción se cubren con tests/test_volatility_provider.py: símbolo incorrecto, causa de datos insuficientes e identidad/TTL del cache. Las pruebas ya existentes fallaban con la regresión y ahora pasan.

## D3. Fallos preexistentes

- tests/test_frontend_symbols.py::test_grid_symbol_registry_options_selection_fallback_and_escaping — presente también en 3117003; no se modificó.
- tests/test_volatility_live.py::test_api_battle_hides_live_metrics_until_minimum_and_history_delegates — presente también en 3117003; no se modificó.
- tests/test_volatility_live.py::test_frontend_volatility_contract_fields — presente también en 3117003; no se modificó.

El test de símbolos probablemente refleja el fallback XRP de la fase 20B-2d A8, ya committed después de 3117003 y ajeno a 20D-3. Los dos tests de volatility_live comprueban contratos legacy; la causa probable es el cambio de API por símbolo de 20B-2a-2, aunque la atribución exacta no se puede demostrar por el nombre. El test de lifespan fue el único preexistente que se arregló.

## D4. Comandos y resultados

Exportación de base: Python llamó git archive 3117003 y extrajo 351 miembros en %TEMP%\asple_head_3117003_20261007; correcta.

Base, desde la exportación (Python absoluto del venv porque el archive no contiene venv):
    $env:ASPLE_OFFLINE='1'; C:\APPS\ASPLE_Predictor\asple-predictor\venv\Scripts\python.exe -m pytest tests -m "not live" --ignore=tests/ui -q --tb=no --junitxml $env:TEMP\pytest-head-3117003.xml --basetemp $env:TEMP\pytest-head-3117003-temp
Resultado literal: 4 failed, 909 passed, 3 skipped, 18 deselected, 37 warnings in 72.74s.

Worktree inicial:
    $env:ASPLE_OFFLINE='1'; .\venv\Scripts\python.exe -m pytest tests -m "not live" --ignore=tests/ui -q --tb=no --junitxml $env:TEMP\pytest-worktree-20d3b.xml --basetemp .pytest_tmp/d3b-worktree
Resultado literal: 56 failed, 877 passed, 1 skipped, 18 deselected, 37 warnings in 73.36s.

Enfoque tras actualizar contratos:
    $env:ASPLE_OFFLINE='1'; .\venv\Scripts\python.exe -m pytest tests/test_frontend_scanner.py tests/test_grid_ctl_open.py tests/test_grid_guards.py tests/test_grid_maxdays.py tests/test_grid_monitor_adjust.py tests/test_grid_monitor_db.py tests/test_grid_monitor_loans.py tests/test_grid_monitor_policy.py tests/test_grid_policy_adjust.py tests/test_grid_sim_smart_fidelity.py tests/test_grid_target.py tests/test_volatility_provider.py -q --basetemp .pytest_tmp/d3b-focused2
Resultado literal: 133 passed, 1 warning in 13.76s.

Verificación final no UI:
    $env:ASPLE_OFFLINE='1'; .\venv\Scripts\python.exe -m pytest tests -m "not live" --ignore=tests/ui -q --tb=no --junitxml $env:TEMP\pytest-worktree-20d3b-final.xml --basetemp .pytest_tmp/d3b-final-nonui
Resultado literal: 3 failed, 930 passed, 1 skipped, 18 deselected, 37 warnings in 77.13s. Los tres son exactamente PREEXISTENTES; NUEVOS=0.

UI (una corrida):
    $env:ASPLE_OFFLINE='1'; .\venv\Scripts\python.exe -m pytest tests/ui -m "not live" -q --basetemp .pytest_tmp/d3b-final-ui
Resultado literal: 98 passed, 2 warnings in 90.22s.

## D5. Estado

Conteos finales: NUEVOS 0; PREEXISTENTES 3; ARREGLADOS 1. git diff --cached --name-only quedó vacío. Sin stage, commit ni push.

Reporte escrito por Python con encoding=utf-8. Revisión manual de tildes completada. Conteo: 18 invocaciones de terminal, incluido un intento fallido de ruta relativa al venv desde la exportación. Los cuatro parches y las esperas de pytest no son comandos de terminal.


## 20D-3c ? saneamiento final

### C1. Finales de l?nea

La primera comprobaci?n recorri? los archivos de `git status --short`, excluyendo `models/saved/*`, `.pytest_tmp/` y respaldos. Se compararon bytes con `HEAD`: todos los archivos versionados revisados tienen LF en `HEAD`; solo `tests/test_grid_sim_smart_fidelity.py` estaba en CRLF y se convirti? a LF. `git diff --stat -- tests/test_grid_sim_smart_fidelity.py` y `git diff -w --stat -- tests/test_grid_sim_smart_fidelity.py` muestran una l?nea de contenido: el `horizon_h: 24` expl?cito. No queda diferencia de finales de l?nea.

Archivos revisados: `api/main.py`, `api/routes/grid_advisor.py`, `api/routes/grids.py`, `database/db_manager.py`, `frontend/app.js`, `frontend/scanner.js`, `grid/engine.py`, `grid/monitor.py`, `grid/policy.py`, `grid/volatility_provider.py`, `tests/sim_replay_adapter.py`, `tests/test_frontend_scanner.py`, `tests/test_frontend_symbols.py`, `tests/test_grid_advisor.py`, `tests/test_grid_ctl_open.py`, `tests/test_grid_engine.py`, `tests/test_grid_guards.py`, `tests/test_grid_monitor.py`, `tests/test_grid_monitor_adjust.py`, `tests/test_grid_monitor_db.py`, `tests/test_grid_monitor_loans.py`, `tests/test_grid_monitor_policy.py`, `tests/test_grid_policy.py`, `tests/test_grid_policy_adjust.py`, `tests/test_grid_sim_smart_fidelity.py`, `tests/test_grid_target.py`, `tests/test_grids_api.py`, `tests/test_volatility_live.py`, `tests/ui/test_grids_browser.py`, `REPORTE_FASE20D3.md`, `REPORTE_FASE20D3b.md`, `tests/test_vol_provider_per_symbol.py`.

### C2. Selectores de monedas

`frontend/app.js` y `frontend/scanner.js` solo deshabilitan una moneda si `ready === false` o existe `readiness.state` distinto de `lista`; sin informaci?n, queda habilitada. XRPUSDT y las entradas string permanecen habilitadas. Se conservan `(preparando)` y `(no lista)`. La prueba previa `test_grid_symbol_registry_options_selection_fallback_and_escaping` qued? intacta. Se a?adieron casos de comportamiento para `ready: false`, `entrenando`, `lista` y ausencia de informaci?n; la prueba UI del Scanner verifica tambi?n los atributos `disabled` de las opciones.

Mutaci?n de `frontend/app.js` que restaura el criterio anterior: `test_grid_symbol_readiness_fails_open_without_explicit_unready_state` fall? con `AssertionError` al no encontrar `UNKNOWNUSDT` habilitada. SHA-256 original/restaurado: `577c119d2e08a486075c4f8ca41dd63ca8400adf4e44b8b05660a7fc3381ad97`; mutado: `28aa6c8e5b0e7242d602146fcb446b9dd23b7ce8f98e6a1852bf4654e9895bd9`. Restauraci?n byte por byte confirmada.

### C3. Origen de las pruebas de volatilidad

| Commit | Resultado de los dos tests |
|---|---|
| `a2bbf8c` | `2 passed in 1.60s` |
| `0712180` | `2 failed in 1.74s` |
| `45d51d8` | `2 failed in 1.77s` |
| `3117003` | `2 failed in 1.76s` |

El primer commit con fallo es `0712180`. El traceback de ambos tests fue `TypeError: history() missing 1 required positional argument: 'response'`. La prueba estaba desactualizada por el contrato intencional: la ruta requiere `Response`, devuelve la lista de historial y a?ade `X-Vol-Selection` (`provisional` o `consensus`). Las pruebas ahora conservan las aserciones de payload y comprueban esa cabecera. No se cambi? c?digo de producci?n.

### C4. Verificaci?n final

No UI, comando ejecutado con `ASPLE_OFFLINE=1`: `C:\APPS\ASPLE_Predictor\asple-predictor\venv\Scripts\python.exe -m pytest tests -m "not live" --ignore=tests/ui -q --basetemp .pytest_tmp/d3c-final-nonui`

Resultado literal: `934 passed, 1 skipped, 18 deselected, 37 warnings in 65.08s`.

UI, comando ejecutado despu?s de no UI con `ASPLE_OFFLINE=1`: `C:\APPS\ASPLE_Predictor\asple-predictor\venv\Scripts\python.exe -m pytest tests/ui -m "not live" -q --basetemp .pytest_tmp/d3c-final-ui2`

Resultado literal:
```text
........................................................................ [ 72%]
...........................                                              [100%]
============================== warnings summary ===============================
tests/ui/test_battle.py::test_battle_cards_explain_zero_signals_unavailable_and_unconfigured_models
  C:\APPS\ASPLE_Predictor\asple-predictor\venv\Lib\site-packages\websockets\legacy\__init__.py:6: DeprecationWarning: websockets.legacy is deprecated; see https://websockets.readthedocs.io/en/stable/howto/upgrade.html for upgrade instructions
    warnings.warn(  # deprecated in 14.0 - 2024-11-09

tests/ui/test_battle.py::test_battle_cards_explain_zero_signals_unavailable_and_unconfigured_models
  C:\APPS\ASPLE_Predictor\asple-predictor\venv\Lib\site-packages\uvicorn\protocols\websockets\websockets_impl.py:14: DeprecationWarning: websockets.server.WebSocketServerProtocol is deprecated
    from websockets.server import WebSocketServerProtocol

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
- Hosts externos bloqueados y registrados por Playwright: cdn.jsdelivr.net, fonts.googleapis.com -
------- Defectos de UI observados y no corregidos: (ninguno observado) --------
99 passed, 2 warnings in 69.76s (0:01:09)
```

Estado final respecto a la l?nea base 20D-3b: NUEVOS=0; los tres fallos preexistentes quedaron resueltos (uno por la regla abierta del selector y dos por actualizar el test al contrato de `Response`). Escaneo de 32 archivos sin exclusiones: anomal?as CRLF/BOM/U+FFFD = `[]`. Revisi?n manual de tildes completada; UTF-8 estricto, sin BOM ni U+FFFD. `git diff --cached --name-only`: `vac?o`. Sin stage, commit ni push.

Comandos de terminal de la tarea: 20 invocaciones.
