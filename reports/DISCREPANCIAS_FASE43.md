# Discrepancias Fase 43

- En el trabajo previo se concluyó que los mensajes señalados estaban bien; esta revisión encontró dos literales con mojibake en la validación de asignación ampliada del operador. Se corrigieron las tildes, sin cambiar la condición ni la conducta.
- `--lista-inicial` resta las monedas activas del Coin Registry si `asple_predictor.db` existe; sin esa BD local emplea la lista fija de datos. No se abrió la BD real durante el desarrollo ni las pruebas.
- El presupuesto del sincronizador limita la espera del hilo de trabajo a 60 s; el cliente existente conserva su política de reintentos. Si una operación en curso excede el límite, su hilo daemon puede terminar después del resumen, y el cursor persistido permite retomar desde la última vela escrita.
- La verificación API/BD es una herramienta optativa que requiere acceso público a Binance cuando se ejecuta con `--comparar-api`; en esta fase solo se ejercitó con cliente falso.
- No se ejecutó en S1 ni se usaron Testnet, `.env`, red o BD real. El archivo `market_data.db` tampoco se generó ni se añadió al repositorio.
- D6 cambia por la decisión de Ramón del 2026-10-30, pero el interruptor queda por defecto `true` para mantener el comportamiento anterior hasta el cambio manual autorizado. No se modificó ninguna BD de usuario.
- El apagado impide comenzar nuevas corridas y evita escrituras/etiquetas en las rutas protegidas. No cancela una corrida que ya estuviera ejecutándose al momento del cambio.
- C43-0: el plan `--fijar-papeles` solo actualiza símbolos ya existentes en `market_data.db`; no inventa monedas ni requiere abrir la BD de registro en las pruebas. El modo `--dry-run` consulta el almacén en solo lectura. La etiqueta de preparación para reactivar pesos sigue disponible dentro del diálogo oculto; el medidor visible es el que reemplaza su estado por el aviso de aprendizaje apagado.
- 43c-1: no se conecta a la BD de registro vivo; la ruta viva era opcional y no puede validarse sin BD real, prohibida en esta ejecución. El marcador local trabaja con `CandleData` sintético o con `CandleStore`.
- 43c-2/3: el HAR se implementa como regresión expandible con componentes de RV diaria/semanal/mensual y reestimación cada 168 horas. La conexión opcional a pronósticos vivos se omite, dado que requeriría la BD principal prohibida por las reglas.
- 43c-4: el cálculo actual persiste métricas de volatilidad (simple/EWMA/HAR) y factores de calibración; la entrada viva opcional se omite. No se midió el rendimiento de una moneda con dos años en esta entrega y no se ejecutó con `market_data.db` de despliegue.
- 43c-5: se entrega el panel y endpoint de lectura. El job semanal y su interruptor global no se habilitan en este alcance parcial; no se registra ningún job automático ni se cambia la configuración global.

- 43c parcial: aún no se implementan los objetivos completos de ruptura, dirección, correlación mensual ni evaluación de régimen con intervalos por bloques. Los veredictos guardados se limitan a volatilidad.
- 43c-5: el panel de lectura sí está disponible, pero faltan el job semanal apagado por defecto y su interruptor global. No hay tarea semanal registrada.
- HAR es una aproximación funcional de regresión expansible; falta su validación con series sintéticas de volatilidad constante y cambiante. La cobertura del 80 % actual no está validada fuera de muestra.
- No se ejecutaron estudios reales ni benchmarks en S1; no se conoce el tiempo por moneda con dos años de velas.

- 43c-9: con trayectoria sintética Student-t(3), varianza igual a GBM, el factor de colas fue 0,80, no >1 como se esperaba. Se conservó el resultado empírico y no se ajustó el modelo para forzar el valor. La validación de colas pesadas no está satisfecha.
- 43c-10: el runner conecta y persiste métricas de ruptura, régimen y dirección; la correlación mensual se alinea por timestamp y queda en summary_json. No se probó todavía matriz completa 21×21 en una corrida de 3 monedas × 200 días.
- 43c-10: falta la prueba OU frente a tendencia exigida para comparar ER/ciclos. Las pruebas actuales cubren el ciclo unitario y el contrato de correlación.

- C43-12 resuelve la ausencia del job semanal: ya se registra con comprobación dinámica de la opción GLOBAL apagada por defecto y evento con actor. La primera corrida manual validada sigue siendo requisito operativo antes de encenderlo.
- C43-9 conserva una discrepancia experimental: Student-t(3) de igual varianza produjo factor de colas 0,80; no se confirmó la expectativa >1. Ver la sección 43c-9 del reporte.
- No se midió el runner completo con dos años y 21 monedas. El benchmark de 218.000 velas mide solo `walk_forward` (24/72 h); la corrida integrada medida cubre tres monedas sintéticas de 200 días y horizonte 24 h.

## Fase 43e-0

HAR se mantiene como regresión sobre RV y se aplica un piso `max(HAR, 0,25 × simple, 1e-12)`. Se conserva el método existente y se evita que predicciones degeneradas contaminen QLIKE. Las correlaciones ausentes se conservan como no disponibles en JSON (`null`), y se excluyen de los resúmenes numéricos.

43e-1: se fija `scenario_fhs_v1`; la construcción local exige al menos 365 días válidos y omite fechas con huecos, pero no se ejecutó sobre `market_data.db` real. El centrado se hace con la media z_r total por moneda, desplazando también z_u/z_d para conservar orden.

43e-2: `generate` entrega un iterador de lotes, con fechas comunes y fallback de calibración 1. No se lee automáticamente `calibration_factors` todavía; el caller debe pasar el factor mediante `calibration_factors` o sigma calibrada. No se ha completado la validación estadística pedida para correlación y relajación de sigma.

## Estado inicial de 43e-3c

Esta anotación describe el alcance registrado antes de las continuaciones 43e-3d a 43e-5; no representa el estado actual.

## Fase 43e-2b

Se elevó la versión de biblioteca a `scenario_fhs_v2`. Los valores estandarizados quedan sin centrar en disco; `load_library(before=...)` calcula y resta la media solo sobre los días seleccionados. Los primeros 30 días no se publican como escenarios; días parciales quedan excluidos pero sus retornos válidos actualizan EWMA. La reconstrucción de precio y sigma usa float64. La primera prueba de precisión falló por reducción prematura de sigma a float32; tras corregirla, 5 pruebas pasan.


## Fase 43e-3a

La especificación observa dos convenciones distintas: `SimExchange` usa cruces estrictos y órdenes activas desde la vela siguiente; `run_meta_bot` usa cruces inclusivos y compra/venta única. `evaluate_portfolio` evalúa pérdida en equity muestreada, mientras el corte directo de `run_simulation` usa el low de la vela. `FASTSIM_SPEC.md` conserva esta distinción para impedir una paridad falsa.


## Fase 43e-3b

FastSim se vectoriza por escenarios y celdas y conserva bucle temporal/resincronización. La prueba inicial falló en 26 casos porque el capital de prueba era demasiado bajo para el filtro de step size real de `FILTERS`; al usar 1000 USDT en los casos de precio USD, el replay de geometría fue válido y la regresión completa pasó (44 pruebas). El benchmark sintético usa OHLC constantes y no mide el coste de fills frecuentes.


## Fase 43e-3c

El runner histórico se verificó con una SQLite temporal sintética (20 días), no con `market_data.db` real. Para conservar el contrato de replay usa `FILTERS` por defecto de `grid.sim.runner`; el almacén público no persiste filtros de exchange por símbolo y no se consultó red. En S1 se requiere revisar símbolos cuyo precio/filtros no sean compatibles con ese filtro; el script omite geometrías inválidas y falla si no completa todos los casos.


## 43e-3d a 43e-5 - alcance y pendientes

Se corrigieron los cortes por pérdida para comparar velas relativas, y el script de paridad muestra el control discrepante y separa ventanas/geometrías inválidas. Los filtros de exchange se obtienen desde Binance solo cuando se ejecuta `scripts/velas_filtros.py`; esta sesión no tuvo red y las pruebas usaron filtros sintéticos. En S1 se debe poblar `symbol_filters` antes de la paridad para que el comparador no use el fallback `FILTERS`.

La prueba sintética ampliada no cubre cada combinación posible de capital, filtro y bot; la corrida de 200 ventanas reales queda a cargo de S1. En local sintético: 20 días tomaron 1,815 s y 426,5 MB de pico; 45 días, 5,926 s y 483,1 MB. Smart con 200 escenarios x 20 días tomó 15,453 s y 166,4 MB de pico, bajo el umbral de 120 s. No se probaron filtros reales de Binance ni ventanas reales; el marcador y v2 siguen desconectados del operador.
## 43e-5b - resolución del replay Meta

`simulate_piece_curve` había reducido a 15 minutos todas las curvas, incluidas las llamadas de `grid/meta_operator.py` y `grid/meta_learning.py` usadas por v1. Se restauró por defecto la condición histórica del envoltorio de `6d565d9`: grids aceptan cada fila trazada por el runner y bots individuales cada 15 minutos, conservando primera y última fila. Solo `research/scenarios/smart_exact.py` solicita explícitamente 15 minutos.

La inspección de `grid/sim/runner.py` en el propio `6d565d9` mostró que, con `meta_portfolio_piece=True`, el runner ya limita las llamadas a `trace_callback` a cada 15 minutos para grids; por tanto la salida empaquetada de v1 en ese commit tiene esa resolución, aunque el filtro del envoltorio permitía cada fila recibida. La regresión compara contra esa salida observada, sin cambiar el runner ni forzar trazas adicionales.

Se cambió `REPLAY_METHOD_VERSION` de `rolling_7d_v3_equity` a `rolling_7d_v4_equity` para que cachés persistidas y etiquetas queden inequívocamente separadas después de corregir el contrato de muestreo; no se borran filas. Los comprobadores consideran v3 obsoleto y recalculan.

Comando de regresión anotado antes de ejecutarlo (14 pasaron, 125 fueron deseleccionadas):

```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43e5b-resolution-6d565d9-c3e6b128-r2'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/test_meta_portfolio_resolution.py tests/test_meta_portfolio_equity.py tests/test_meta_operator.py tests/test_meta_learning.py -k "curve_bytes_match_6d565d9 or meta_operator_replay_window_matches_6d565d9_outcome or explicit_research_sampling or real_grid_curve_keeps_runner_resolution_and_research_opt_in or old_process_or_persistent_replay_is_rejected or replay_window_cache_is_shared_without_changing_result or touch_bot_shadow_chooses_best_causal_distance_without_opening_grid or 72h_labels_use_proportional_target_and_keep_profile_horizon_separate or allocation_counterfactual_labels_base_and_added_piece_on_same_synthetic_candles or learning_invalid_geometry_is_terminal_and_has_diagnostics or daily_label_job_persists_72h_result_and_step_timings_without_exchange_calls or learning_migration_recalculates_old_base_but_not_current_base" --basetemp="$base"
```

Comprobación adicional del adaptador Smart exacto (1 pasada):

```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43e5b-smart-exact-6d565d9-c3e6b128-r1'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/test_smart_exact.py -k "smart_exact_matches_direct_runner_and_keeps_restrictions_off" --basetemp="$base"
```
## 43e-5c - correcciones de scripts de escenarios

La salida de paridad en S1 contaba falsos errores porque `ganancia` comparaba PnL antes de liquidar el inventario. El chequeo ahora usa `equity_final_after_repository_liquidation - capital`, la misma magnitud usada por `pnl_error`, e informa diferencias separadas por chequeo. Para volatilidad diaria, los timestamps de los retornos se alinean con `data.timestamp[1:]`.

La evaluación admite `alternativas` (predeterminado): cada pieza se evalúa sola con su capital declarado, el capital restante de la meta queda quieto y las métricas se miden sobre el capital total; se comparan diferencias pareadas sobre idénticos escenarios, priorizando Smart menos Simple para la misma geometría. `cartera` mantiene la suma actual de piezas y valida el total antes de leer datos.

Comando enfocado anotado antes de ejecutarlo:

```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43e5c-scripts-20261030-r1'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/test_scenarios_parity_script.py tests/test_scenarios_speed.py tests/test_scenarios_evaluate.py --basetemp="$base"
```

Después de añadir la cobertura de capital quieto y el pareo de alternativas sin geometría coincidente, se anotó esta segunda corrida enfocada:

```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43e5c-scripts-20261030-r2'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/test_scenarios_parity_script.py tests/test_scenarios_speed.py tests/test_scenarios_evaluate.py --basetemp="$base"
```
