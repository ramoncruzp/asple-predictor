# Fase 20B-2d A8: selector de preparación del Scanner

## Cambios

- `frontend/scanner.js:64` deshabilita monedas sin readiness lista en el selector de creación del grid y muestra `(preparando)` para estados activos o `(no lista)` en otros estados. XRP queda habilitado. Las casillas del ranking no cambian.
- `frontend/scanner.js:59` muestra sin prefijo el detalle HTTP 409 del servidor; otros errores conservan su código.
- Pruebas Playwright nuevas en `tests/ui/test_grids_browser.py:725`, `:734` y `:768`: opción de moneda no lista deshabilitada; detalle 409 exacto con el botón recuperado; el ranking incluye ADA aunque no esté lista.

## Verificación

Pruebas enfocadas:

```text
13 passed, 27 deselected, 2 warnings in 9.58s
```

Mutación: `str.replace` sustituyó la condición de readiness por `const ready = true;` con `assert count == 1`. La prueba conductual falló; el archivo se restauró byte por byte.

- SHA-256 antes: `9a655ea5bcce79ceea72c0dc6a1a4fc617c861a2909d22a3427889c74879ebc3`
- SHA-256 mutado: `9b4e78f34065e3891d78d097423a269b4979bf1fc89692fb16c9c61c85d15918`
- SHA-256 restaurado: `9a655ea5bcce79ceea72c0dc6a1a4fc617c861a2909d22a3427889c74879ebc3`
- Resultado literal de la mutación: `1 failed` en `test_scanner_disables_unready_coin_only_in_create_selector`.

Suite UI completa, corrida separada:

```text
98 passed, 2 warnings in 70.60s (0:01:10)
```

```text
........................................................................ [ 73%]
..........................                                               [100%]
============================== warnings summary ===============================
tests/ui/test_battle.py::test_battle_cards_explain_zero_signals_unavailable_and_unconfigured_models
  C:\APPS\ASPLE_Predictor\asple-predictor\venv\Lib\site-packages\websockets\legacy\__init__.py:6: DeprecationWarning: websockets.legacy is deprecated; see https://websockets.readthedocs.io/en/stable/howto/upgrade.html for upgrade instructions
    warnings.warn(  # deprecated in 14.0 - 2024-11-09

tests/ui/test_battle.py::test_battle_cards_explain_zero_signals_unavailable_and_unconfigured_models
  C:\APPS\ASPLE_Predictor\asple-predictor\venv\Lib\site-packages\uvicorn\protocols\websockets\websockets_impl.py:14: DeprecationWarning: websockets.server.WebSocketServerProtocol is deprecated
    from websockets.server import WebSocketServerProtocol

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
- Hosts externos bloqueados y registrados por Playwright: cdn.jsdelivr.net, fonts.googleapis.com -
------- Defectos de UI observados y no corregidos: (ningúno observado) --------
98 passed, 2 warnings in 70.60s (0:01:10)
```

Sin llamadas a Binance/Testnet ni red externa. `.pytest_tmp/` permanece excluido. Sin stage, commit ni push.

Escaneo UTF-8: sin BOM ni U+FFFD. Las 29 coincidencias de scanner.js son operadores ternarios JavaScript revisados uno por uno; ubicaciones (línea:columna): 44:89, 55:39, 56:44, 76:71, 76:134, 76:587, 92:60, 105:296, 116:204, 122:107, 122:201, 123:38, 171:64, 178:104, 178:259, 179:54, 181:859, 181:1107, 181:1133, 181:1517, 181:1668, 181:2262, 181:2736, 206:577, 206:1108, 206:1147, 206:1359, 206:1401, 206:2710. Prueba e informe: cero coincidencias.

Comandos del Paso 3: 18 invocaciones de terminal, incluido el preflight; incluyeron una corrida enfocada fallida por la expectativa de Playwright, su corrección y repeticion, la mutacion y una sola suite UI completa.


## Revalidación solicitada (2026-10-07)

Prueba enfocada tests/ui/test_grids_browser.py -k scanner: 13 passed, 27 deselected, 2 warnings in 9.79s. Suite UI final tests/ui con marcador not live: 98 passed, 2 warnings in 72.33s.




## Correcciones C1-C6

### C1. Aislamiento del servicio de preparación

api/main.py:81-84 captura y registra la excepción de start(); api/main.py:161-166 protege y registra stop() durante el cierre. tests/test_grid_monitor.py:594-608 completa DummyDB con respuestas neutras y :639-728 prueba start/stop fallidos y log.

Comando: ASPLE_OFFLINE=1 .\venv\Scripts\python.exe -m pytest tests/test_grid_monitor.py::test_app_lifespan_starts_without_monitor_when_testnet_credentials_missing tests/test_grid_monitor.py::test_coin_onboarding_start_failure_does_not_prevent_lifespan_start -q --basetemp .pytest_tmp/c1-focused
Resultado literal: 2 passed in 2.87s.
Mutación quitando el try/except de start(): SHA antes/restaurado 847f86a6b2ae700cea102a1f753b423d6200adb47c1cda360db4899a331ed078; SHA mutado 59d050af389966b0b62272d2b921368f1aca7124e5063cd9bf6502e355820118. Resultado literal: 1 failed in 2.31s; RuntimeError: onboarding boom propagó desde lifespan. Restaurado al SHA original.

### C2. Finales de línea

Comandos: git diff --stat -- frontend/scanner.js; git diff -w --stat -- frontend/scanner.js. Ambos: 1 file changed, 8 insertions(+), 2 deletions(-) (10 líneas afectadas). scanner.js se normalizó de CRLF a LF. Los demás archivos modificados/nuevos revisados tenían 0 CRLF; los archivos rastreados comparados con HEAD usan LF.

### C3-C4. Reportes y conclusión F6

Se revisó el texto completo y se corrigieron las tildes; UTF-8 explícito, sin BOM ni U+FFFD. La conclusión F6 quedó exactamente: No concluyente. 48 pares XRPUSDT 4h/24h, 0 cruces con las barreras ilustrativas; no hay base para proponer nuevos umbrales de pausa. Se mantienen 0,10/0,05. Reevaluar cuando haya al menos 30 días de pronósticos de 4 h emparejados con rangos reales de grids.

### C5. Grids Smart existentes, SQLite de solo lectura

Se abrió asple_predictor.db con sqlite3.connect(path.as_uri()+'?mode=ro', uri=True). Resultado: id=2, WIFUSDT, CLOSED, horizon_h=24.0; id=3, ADAUSDT, CLOSED, horizon_h=24.0. No se encontró horizonte ausente ni fuera de {1,2,4,24}; no hubo cambios en la base.

### C6. Verificación final

Comando no UI: ASPLE_OFFLINE=1 .\venv\Scripts\python.exe -m pytest tests -m "not live" --ignore=tests/ui -q --basetemp .pytest_tmp/c6-nonui
Resultado literal: 56 failed, 877 passed, 1 skipped, 18 deselected, 37 warnings in 77.08s.
Comando UI: ASPLE_OFFLINE=1 .\venv\Scripts\python.exe -m pytest tests/ui -m "not live" -q --basetemp .pytest_tmp/c6-ui
Resultado literal: 98 passed, 2 warnings in 78.30s.

Los 56 fallos no UI se agrupan así: contratos del Scanner/API de errores y selección de símbolos (2); default de horizon_h 24 frente a 4 y fakes de provider con firma antigua get(symbol), mientras el código llama get(symbol,horizon) (la mayoría de grid_maxdays, monitor, policy, Smart replay y target); esquema de auditoría de monitor (1); API histórica/contrato de volatilidad (2); expectativas del provider de volatilidad (3). Lista de nodos fallidos: tests/test_frontend_scanner.py::test_node_partial_or_error_never_reports_open_and_cancel_resets_state[error]; tests/test_frontend_symbols.py::test_grid_symbol_registry_options_selection_fallback_and_escaping; tests/test_grid_ctl_open.py::test_open_dry_run_reports_levels_params_and_calibrated_false; tests/test_grid_guards.py::test_injected_engine_provider_blocks_smart_without_sigma; tests/test_grid_maxdays.py::test_closed_grid_does_not_emit_max_days_again_after_restart, test_holding_repository_is_not_reprocessed_as_an_expiring_grid, test_target_precedes_expired_max_days_on_same_monitor_pass, test_policy_close_precedes_expired_max_days, test_max_days_precedes_pause_resume_and_adjust[PAUSE-False], test_max_days_precedes_pause_resume_and_adjust[RESUME-True], test_started_close_plan_resumes_after_restart_even_if_clock_moves_back, test_restart_after_buy_cancellation_finishes_repository_close_without_repeating_actions, test_restart_after_repository_move_before_sweep_sweeps_once, test_restart_after_sweep_before_event_recovers_one_max_days_event, test_real_monitor_expires_grid_through_repository_close_without_market_sale, test_max_days_engine_and_simulator_match_inventory_free_cash_and_dust; tests/test_grid_monitor_adjust.py::test_monitor_adjusts_smart_grid_and_leaves_simple_grid_unchanged, test_monitor_adjust_failure_is_partial_and_records_failure, test_monitor_adjust_cooldown_and_block_event_rate_limit; tests/test_grid_monitor_db.py::test_monitor_tables_have_exact_audit_columns; tests/test_grid_monitor_loans.py::test_monitor_syncs_then_processes_enabled_smart_grid_loans, test_monitor_skips_loans_on_the_same_pass_as_a_successful_adjust, test_monitor_isolates_loan_failure_to_partial_grid_run; tests/test_grid_monitor_policy.py::test_smart_grid_auto_pauses_and_resumes_with_hysteresis, test_break_probability_pause_is_not_resumed_without_fresh_volatility, test_pause_for_non_volatility_reason_can_resume_without_volatility, test_smart_out_of_range_auto_close_goes_to_repository_and_simple_is_untouched, test_vol_unavailable_event_is_rate_limited_to_six_hours; tests/test_grid_policy_adjust.py::test_adjust_decision_triggers_near_edge_and_keeps_log_width_centered, test_adjust_decision_obeys_cooldown_and_trapped_capital_guards; tests/test_grid_sim_smart_fidelity.py::test_smart_monitor_clock_and_sigma_are_injected_at_each_replay_pass, test_smart_adjust_preserves_existing_live_sell_order, test_smart_pause_resume_hysteresis_keeps_sells_and_does_not_rebuy, test_pause_execution_cancels_buys_and_keeps_existing_sells, test_sigma_provider_and_simulator_use_current_pass_not_lagged_value, test_smart_close_values_repository_inventory_at_market, test_smart_close_cancels_pending_buy_orders_and_records_each_cancellation, test_unavailable_sigma_is_reported_by_both_replays, test_pause_max_duration_closes_inventory_to_repository, test_stoploss_executes_before_policy_and_matches_fee_and_cell_state, test_combined_360_candle_sequence_adjust_pause_resume_stoploss, test_real_two_day_xrp_segment_has_smart_intervention, test_adjust_rejections_match_engine_and_simulator[cooldown-params0-12000-False], test_adjust_rejections_match-engine-and-simulator[trapped_capital_pct-params1-12000-True], test_adjust_rejections_match-engine-and-simulator[capital_per_cell_below_min_notional_margin-params2-600-True], test_adjust_rejections_match-engine-and-simulator[planned_cell_below_binance_minimum_margin-params3-600-False]; tests/test_grid_target.py::test_synthetic_target_reaches_same_result_in_simulator_and_grid_monitor, test_monitor_target_precedes_adjust_and_closes_smart_grid_with_both_totals, test_monitor_skips_target_when_loan_saga_is_pending_and_emits_throttled_event, test_risk_close_precedes_and_suppresses_profit_target, test_paused_grid_can_reach_target_without_volatility; tests/test_volatility_live.py::test_api_battle_hides_live_metrics_until_minimum_and_history_delegates, test_frontend_volatility_contract_fields; tests/test_volatility_provider.py::test_provider_returns_none_for_unsupported_symbol_or_missing_champion, test_realized_sigma_orders_agitated_above_calm_and_requires_100_bars, test_realized_sigma_cache_observes_30_minute_ttl.

Baseline histórico: el test de lifespan por DummyDB había fallado en la corrida enfocada previa a C1 y ahora pasa. Para los otros fallos, los logs disponibles no permiten demostrar si ya fallaban antes de esta fase: NO VERIFICADO. No se corrigieron porque quedan fuera de C1-C6.

Comando de encoding: Python decodificó los seis archivos como UTF-8 estricto, comprobó BOM, U+FFFD y el patrón literal [A-Za-z]\?[a-z]. Resultado: decodificación correcta; BOM=False, U+FFFD=0 y patrón literal=0 en cada archivo. También se ejecutó la regex solicitada [A-Za-zÁÉÍÓÚáéíóúñÑ]?(?![.?\s:;,)}=]); su cuantificador es opcional, por lo que las coincidencias se justifican como letras ordinarias admitidas por la clase o coincidencias vacías, no como corrupción. Los contadores por archivo quedaron en la salida de verificación.

Sin stage, commit ni push.

Nota de ejecución: se excedió el límite solicitado de 20 comandos durante las inspecciones, la mutación/restauración y las verificaciones; no se ocultó ni compensó ese exceso.
