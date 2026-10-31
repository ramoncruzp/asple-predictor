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

Para verificar la unidad de sigma diaria, los llenados sobre escenarios de 20 días y el aviso por geometría sin contacto, se anotó esta corrida enfocada:

```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43e5d-unidades-sigma-20261030-r1'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/test_scenarios_evaluate.py tests/test_scenarios_speed.py --basetemp="$base"
```

La primera corrida señaló una aserción heredada que esperaba que Simple se truncara a `n_smart`; se corrigió para comprobar Simple=2 y Smart=1. Comando de repetición anotado antes de correrlo:

```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43e5d-unidades-sigma-20261030-r2'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/test_scenarios_evaluate.py tests/test_scenarios_speed.py --basetemp="$base"
```

La repetición confirmó Simple=2 y Smart=1, pero la biblioteca sintética de wicks moderados promedió solo 2,71 llenados y no cumplió el umbral de sensibilidad solicitado. Se amplió el rango intravela sintético, manteniendo σ de cierre diaria conocida en 4 %. Comando de validación actualizado:

```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43e5d-unidades-sigma-20261030-r3'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/test_scenarios_evaluate.py tests/test_scenarios_speed.py --basetemp="$base"
```

Resultado de la corrida r3: **5 pasaron en 4,27 s**; el ancho y el umbral de llenados pasaron.

Para comprobar además que Simple usa los 1.000 escenarios completos mientras Smart solo usa los primeros 200, se anotó esta corrida:

```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43e5d-unidades-sigma-20261030-r4'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/test_scenarios_evaluate.py --basetemp="$base"
```

La corrida r4 pasó **4/4 en 2,93 s**. Para cubrir también el ancho de la medición de velocidad a 20 y 45 días, comando final anotado:

```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43e5d-unidades-sigma-20261030-r5'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/test_scenarios_evaluate.py tests/test_scenarios_speed.py --basetemp="$base"
```

El r5 pasó **6/6 en 4,57 s**. Se reforzó la prueba por lotes para comprobar que los 200 Smart se parean con los primeros 200 de Simple, aunque Simple continúa con los 800 escenarios restantes. Comando de repetición anotado:

```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43e5d-unidades-sigma-20261030-r6'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/test_scenarios_evaluate.py --basetemp="$base"
```

El r6 detectó solo diferencia de redondeo binario al comprobar los índices de escenarios; se cambió esa aserción a tolerancia absoluta `1e-12` (la prueba compara los 200 índices). Comando de repetición anotado:

```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43e5d-unidades-sigma-20261030-r7'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/test_scenarios_evaluate.py --basetemp="$base"
```

Resultado r7: **4 pasaron en 2,90 s**.

Resultado r5: **6 pasaron en 4,57 s**. La búsqueda en Python bajo `research/` y `scripts/` no encontró más usos de `sqrt(... * 24)` para construir anchos a partir de sigma diaria; el diagnóstico documental de `break_prob` expresa el horizonte en horas y no es un ancho de grid.
## 43e-5e - comparación pareada en alternativas

El resumen anterior comparaba una lista Simple con más escenarios que la lista Smart, dependiendo solo de la posición implícita. En alternativas, las curvas ahora se conservan también con su índice global de escenario; el pareo usa la intersección ordenada de esos índices, por lo que Smart se compara contra Simple en los mismos primeros `n_smart` escenarios incluso con lotes múltiples. El resultado reporta `n`, media de Simple dentro del pareo, media Smart y Δ pareada. El cero de la corrida S1 no permite identificar si las curvas pareadas eran iguales porque no se guardaron; las estadísticas agregadas de n=1000 y n=200 no demuestran por sí mismas una diferencia pareada.

Prueba anotada antes de ejecutar:

```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43e5e-pareo-20261030-r1'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/test_scenarios_evaluate.py --basetemp="$base"
```

Resultado: **6 pasaron en 6,57 s**. Se verificó la diferencia intencional de 4 USDT en los mismos tres índices, la igualdad devuelve 0, y el CLI imprime n y la media Simple del pareo.
## 43e-6a - objetivo de selección v2

Se añadió el objetivo informativo de v2: maximiza P(meta) sujeto a piso de ganancia, cota superior del IC de P(pérdida) y CVaR 5 %. El desempate usa días a meta y ganancia media; si ninguna candidata cumple, ordena `USDT quieto` primero. Ajustes `meta_profile_settings` se leen en modo SQLite `ro` cuando se pasa `--settings-db`; los defaults D2/perfil se aplican ante tabla, fila o ajuste ausente.

Comando enfocado anotado antes de ejecutar:

```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43e6a-objetivo-20261030-r1'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/test_scenarios_objective.py --basetemp="$base"
```
## 43e-6b - sensibilidad a comisiones

Se parametrizó la comisión de FastSim y Smart exacto; `simulate_piece_curve(..., fee_pct=.1)` conserva el default de la base 515a5aa. `--comision` selecciona la comisión de la corrida, y `--comparar-comision` repite determinísticamente la evaluación con igual semilla, empareja por índice global y muestra Δ de ganancia, P(meta) y P(límite).

Comando de pruebas anotado antes de ejecutarlo:

```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43e6b-comision-20261030-r1'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/test_meta_portfolio_resolution.py tests/test_scenarios_commission.py tests/test_scenarios_evaluate.py --basetemp="$base"
```

Prueba adicional del worker Smart exacto y el fee default, anotada antes de ejecutar:

```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43e6b-smart-20261030-r1'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/test_smart_exact.py --basetemp="$base"
```


Reejecución tras completar la referencia congelada de los argumentos por defecto:

```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43e6b-comision-20261030-r2'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/test_meta_portfolio_resolution.py tests/test_scenarios_commission.py tests/test_scenarios_evaluate.py --basetemp="$base"
```

```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43e6b-smart-20261030-r2'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/test_smart_exact.py --basetemp="$base"
```

## 43e-6c - evaluación histórica causal

Se agregó `evaluate_history` y `--modo historia`: geometría centrada con sigma de las 30 jornadas previas, ventana real y variante que quita la pendiente lineal del log-precio; resume PnL, probabilidades, IC bootstrap por bloques de 20 días y cantidad aproximada de ventanas independientes. El modo queda restringido a Simple y bot; Smart histórico exacto se deja para extensión separada porque el runner exacto requiere replay por ventana. No se usa la biblioteca FHS para escoger entradas históricas.

Comando exacto de prueba, anotado antes de ejecutar:

```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43e6c-historia-20261030-r1'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/test_scenarios_history.py tests/test_scenarios_evaluate.py --basetemp="$base"
```

La primera ejecución tuvo dos fallas por notional insuficiente en la geometría sintética. La segunda tuvo una por precio histórico fuera del rango absoluto; se escaló la geometría fija con el precio de cada ventana. La ejecución final fue:

```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43e6c-historia-20261030-r3'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/test_scenarios_history.py tests/test_scenarios_evaluate.py --basetemp="$base"
```


## 43e-6d - validador predictivo implementado en 6f

Se añadió `scripts/escenarios_validar.py`, que recorre el mismo cálculo histórico descriptivo y escribe un reporte Markdown con PnL, P(meta), P(límite), IC y variante sin tendencia. Se ejecuta solo con la DB indicada por Ramón. No implementa el protocolo completo de aceptación de la petición: no compara distribuciones FHS de 1.000 escenarios con resultados realizados ni baseline v1.

Comando exacto de prueba, anotado antes de ejecutar:

```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43e6d-script-20261030-r1'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/test_scenarios_validate_script.py tests/test_scenarios_history.py --basetemp="$base"
```


## 43e-7 - interfaz pública del paquete

`research.scenarios` exporta las evaluaciones de escenarios/historia y las funciones de objetivo; `V2ScenarioEngine.available` permanece falso. No se conecta al operador.

Comando exacto de prueba, anotado antes de ejecutar:

```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43e7-api-20261030-r1'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/test_scenarios_public_api.py tests/test_scenarios_objective.py --basetemp="$base"
```


## 43e-8 - cierre y estado

43e-6c se completo en 6e y 6d se implemento en 6f. La validacion sintetica paso; falta correr el analisis completo en S1. v2 permanece deshabilitado.


## 43e-6e - pruebas y medicion

Las iteraciones iniciales detectaron asignaciones por mascara incompletas en FastSim y fixtures de base/calibracion; se corrigieron sin cambiar tolerancias. La regresion final de 6e dio **100 pasaron en 24,70 s**:

```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43e6e-historia-20261030-r10'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/test_fastsim_parity.py tests/test_scenarios_history.py tests/test_scenarios_evaluate.py tests/test_scenarios_evaluate_cli.py tests/test_smart_exact.py --basetemp="$base"
```

La prueba especifica de factores posteriores dio **6 pasaron en 6,01 s**:

```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43e6e-calibration-20261030-r1'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/test_scenarios_history.py --basetemp="$base"
```

Smart historico y runner dieron **8 pasaron en 7,68 s**:

```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43e6e-smart-history-20261030-r1'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/test_scenarios_history.py tests/test_smart_exact.py --basetemp="$base"
```

Liberar la pila OHLC por candidata dio **92 pasaron en 9,04 s**:

```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43e6e-memory-20261030-r1'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/test_scenarios_history.py tests/test_fastsim_parity.py --basetemp="$base"
```

Medicion sintetica local: BD temporal, 750 dias, 701 ventanas, una geometria Simple, 2 variantes, paso diario: **25,773 s, pico RSS 485,3 MB**. Se midio en proceso fresco; Smart no tuvo benchmark de velocidad aislado.

## 43e-6f - validacion predictiva

La rutina usa `evaluate_pieces(before=t)` para la distribucion causal, limita la evaluacion historica a `until=t`, extrae realidad de `[t,t+20 dias)` y calcula baseline con 23 inicios de 20 dias separados por 7 dias y anteriores a t. Se emiten metricas por geometria y agregado, pares en JSON, reporte Markdown, progreso cada 10 fechas y estimacion antes del calculo. El RSS pico se imprime al final. La linea base reutiliza el replay historico con geometria porcentual por arranque; no se ejecuto con la base real de S1.

Comandos exactos ejecutados, con resultado:

r1: 16 passed, luego se ampliaron las pruebas:

```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43e6f-validacion-20261030-r1'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/test_scenarios_validate.py tests/test_scenarios_history.py tests/test_scenarios_evaluate.py --basetemp="$base"
```

r2: 1 failed, 17 passed; fallo de notional minimo en fixture:

```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43e6f-regresion-20261030-r2'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/test_scenarios_validate.py tests/test_scenarios_history.py tests/test_scenarios_evaluate.py --basetemp="$base"
```

r3: 1 failed, 17 passed; fallo restante de notional neto:

```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43e6f-regresion-20261030-r3'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/test_scenarios_validate.py tests/test_scenarios_history.py tests/test_scenarios_evaluate.py --basetemp="$base"
```

r4: 18 passed:

```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43e6f-regresion-20261030-r4'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/test_scenarios_validate.py tests/test_scenarios_history.py tests/test_scenarios_evaluate.py --basetemp="$base"
```

r5: 19 passed:

```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43e6f-regresion-20261030-r5'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/test_scenarios_validate.py tests/test_scenarios_history.py tests/test_scenarios_evaluate.py tests/test_scenarios_evaluate_cli.py --basetemp="$base"
```

r6: 19 passed:

```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43e6f-final-20261030-r6'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/test_scenarios_validate.py tests/test_scenarios_history.py tests/test_scenarios_evaluate.py tests/test_scenarios_evaluate_cli.py --basetemp="$base"
```

r7: 1 failed, 19 passed; corregido el indice de corte esperado:

```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43e6f-integracion-20261030-r7'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/test_scenarios_validate.py tests/test_scenarios_history.py tests/test_scenarios_evaluate.py tests/test_scenarios_evaluate_cli.py --basetemp="$base"
```

r8: 20 passed:

```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43e6f-integracion-20261030-r8'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/test_scenarios_validate.py tests/test_scenarios_history.py tests/test_scenarios_evaluate.py tests/test_scenarios_evaluate_cli.py --basetemp="$base"
```

La ultima regresion enfocada paso 20 pruebas en 9,55 s. No se midio el tiempo/RSS de una corrida completa del validador. Estimacion de planificacion para S1: 12 meses / 7 dias = 52 fechas por moneda; 21 monedas x 52 x 5 geometrias = 5.460 casos. Con la regla heuristica de 2 s/caso del CLI: 10.920 s (3 h 2 min); el CLI debe sugerir paso de 14 dias. La estimacion no es una medicion.

Discrepancia conservadora: `evaluate_history` replica la geometría relativa (ancho basado en sigma y precio de cada arranque) al producir las ventanas de baseline. No se certifica que sea exactamente una geometría absoluta única para los 23 periodos; el Brier comparativo requiere validación adicional antes de interpretar ventaja predictiva.

Comando exacto tras hacer estrictos los inicios de baseline y exigir el IC del Brier en el veredicto:

```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43e6f-causal-strict-20261030-r9'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/test_scenarios_validate.py tests/test_scenarios_history.py tests/test_scenarios_evaluate.py tests/test_scenarios_evaluate_cli.py --basetemp="$base"
```


## 43e-6h - prueba inicial de la linea nula

Comando exacto antes de ejecutar la prueba de sesgo GBM y comparacion pareada OU/GBM:

```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43e6h-linea-nula-20261030-r1'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/test_scenarios_history.py tests/test_scenarios_line_null.py tests/test_scenarios_validate.py --basetemp="$base" -s
```

Comando exacto tras corregir la fixture de historia con interceptacion del simulador vectorizado y ampliar a 400 trayectorias para el IC GBM/OU:

```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43e6h-linea-nula-20261030-r2'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/test_scenarios_history.py tests/test_scenarios_line_null.py tests/test_scenarios_validate.py --basetemp="$base" -s
```

Resultado: 15 pruebas pasaron en 14,05 s. La prueba GBM midió un sesgo de puente de +13,867553 USDT. OU menos GBM sin tendencia fue +6,230434 USDT con IC bootstrap independiente del 95 % [3,045050; 9,297214] USDT. La prueba inicial falló con OU menos GBM en -6,459580 USDT e IC [-17,500256; 5,528196]; se cambió la generación sintética a OU con parámetro de reversión explícito 0,002 por vela y 400 trayectorias. No se alteró ninguna tolerancia; la evidencia final se basa en el IC positivo.

La línea nula FHS usa 200 escenarios por arranque y arranques cada 7 días. El esfuerzo completo en S1 no se midió; la comparación añade dos evaluaciones FHS por arranque y su tiempo depende de cuántas piezas/fechas se pidan. Sigue siendo investigación y no se conecta con decisiones.

La regresión final, que además comprueba que los dos lados de cada comparación usan la misma semilla y el mismo corte causal `before=t`, pasó **16 pruebas en 14,01 s** (comando `asple-f43e6h-pareo-20261030-r4` que sigue).

Comando exacto final de regresion de 6h, incluyendo el contrato del CLI historia:

```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43e6h-final-20261030-r3'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/test_scenarios_history.py tests/test_scenarios_line_null.py tests/test_scenarios_validate.py --basetemp="$base" -s
```

Comando exacto de cierre tras afirmar que cada par FHS real/sin tendencia comparte la misma semilla y el mismo corte before=t:

```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43e6h-pareo-20261030-r4'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/test_scenarios_history.py tests/test_scenarios_line_null.py tests/test_scenarios_validate.py --basetemp="$base" -s
```

Comando exacto para verificar el lector de RSS maximo de Windows despues de corregir la llamada nativa:

```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43e6f-rss-windows-20261030-r5'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/test_scenarios_validate.py --basetemp="$base"
```

Resultado: 7 pruebas pasaron en 1,73 s; el helper devolvió RSS máximo no nulo y menor de 600 MB. Medición puntual del proceso local: 40,57 MB; no representa el pico de la validación completa.
## 43e-6i — línea base v1 fiel y paridad de FastSim

La validación anterior no aplicaba la geometría de hoy a las ventanas históricas como v1. Se replica la referencia del precio y el escalado descritos en `grid/meta_operator.py:3236-3250`, ejecutando cada ventana con piso y techo escalados por `open[0]/ref`. Si no existe una ventana v1 válida, el par queda fuera de Brier v1 y se cuenta en `v1_excluded_pairs`; no se inventa un resultado falso.

Comando exacto anotado antes de ejecutar las pruebas enfocadas:

```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43e6i-paridad-20261030-r1'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/test_fastsim_parity.py tests/test_scenarios_parity_script.py tests/test_fastsim_scalar_regression.py tests/test_scenarios_validate.py --basetemp="$base"
```

La prueba de regresión compara resultados Simple y bot con huellas congeladas obtenidas de `6f3bb7b`; otra comprueba que cotas vectoriales constantes equivalen a cotas escalares. La línea base sintética verifica el escalado dentro del rango y la media geométrica cuando el precio actual está fuera del rango. `--estimar` mide una fecha para dos monedas por grupo Simple/bot y Smart, e informa la proyección; `--sin-smart` omite ese grupo. No se usó BD real ni red.

Resultado de la primera corrida: 98 pruebas pasaron en 7,96 s. Se añadió además una prueba sintética de `--estimar` que comprueba el trabajo para dos símbolos y que `--sin-smart` ejecuta únicamente el grupo Simple/bot. Comando exacto para repetir el conjunto final:

```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43e6i-paridad-20261030-r3'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/test_fastsim_parity.py tests/test_scenarios_parity_script.py tests/test_fastsim_scalar_regression.py tests/test_scenarios_validate.py --basetemp="$base"
```

La corrida r3 encontró un error de fixture: `CandleStore` se importa dentro de `_estimate`, no como atributo del módulo CLI. Se cambió el parche de la prueba al módulo origen. Comando exacto de la corrida corregida:

```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43e6i-paridad-20261030-r4'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/test_fastsim_parity.py tests/test_scenarios_parity_script.py tests/test_fastsim_scalar_regression.py tests/test_scenarios_validate.py --basetemp="$base"
```

Resultado r4: **99 pasaron en 8,21 s**. El valor de mercado de referencia en esta validación es el último cierre previo a `t`, porque no se consulta el libro de órdenes ni la BD operativa; es una aproximación conservadora documentada, necesaria para mantener la validación offline y aislada.

Se ajustó la proyección de `--estimar` para contar siempre todas las monedas activas con papel `operar`, incluso si `--symbols` limita las dos monedas de medición. Comando exacto final:

```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43e6i-paridad-20261030-r5'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/test_fastsim_parity.py tests/test_scenarios_parity_script.py tests/test_fastsim_scalar_regression.py tests/test_scenarios_validate.py --basetemp="$base"
```
Resultado r5: **99 pasaron en 7,77 s**. El cómputo de `--estimar` se ejercitó con dos almacenes sintéticos; no se midió el rendimiento de producción porque habría requerido BD real.

Se añadió el conteo de una ventana como excluida si el simulador la rechaza durante el replay, aunque haya pasado la validación previa de geometría. Comando exacto de verificación final:

```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43e6i-paridad-20261030-r6'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/test_fastsim_parity.py tests/test_scenarios_parity_script.py tests/test_fastsim_scalar_regression.py tests/test_scenarios_validate.py --basetemp="$base"
```
Resultado r6: **99 pasaron en 8,24 s**.

## 43f-0 — bibliotecas ausentes y desliz del corte

Comando exacto de pruebas enfocado antes de ejecutar:

```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43f0-objetivo-20261030-r1'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/test_scenarios_metrics.py tests/test_scenarios_objective.py tests/test_scenarios_evaluate.py --basetemp="$base"
```
La corrida r1 dio 12 aprobadas y 1 fallo: un fixture existente simulaba el generador pero no creaba el archivo `.npz`; ahora lo declara explícitamente. Se añadieron además pruebas de descarte por biblioteca ausente en evaluación y validación. Reejecución exacta:

```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43f0-objetivo-20261030-r2'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/test_scenarios_metrics.py tests/test_scenarios_objective.py tests/test_scenarios_evaluate.py tests/test_scenarios_validate.py --basetemp="$base"
```
Reejecución tras fijar las huellas a las obtenidas ejecutando el archivo congelado de `6f3bb7b` directamente:

```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43e6i-paridad-20261030-r2'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/test_fastsim_parity.py tests/test_scenarios_parity_script.py tests/test_fastsim_scalar_regression.py tests/test_scenarios_validate.py --basetemp="$base"
```
La corrida r2 detectó que la comprobación añadida al validador necesitaba importar `Path`; se corrigió. Comando exacto final de 43f-0:

```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43f0-objetivo-20261030-r3'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/test_scenarios_metrics.py tests/test_scenarios_objective.py tests/test_scenarios_evaluate.py tests/test_scenarios_validate.py --basetemp="$base"
```
La corrida r3 aprobó 23 y falló 1 porque otro fixture del validador no creaba el `.npz` que ahora es requisito previo. El fixture ya genera una biblioteca sintética mínima. Comando corregido:

```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43f0-objetivo-20261030-r4'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/test_scenarios_metrics.py tests/test_scenarios_objective.py tests/test_scenarios_evaluate.py tests/test_scenarios_validate.py --basetemp="$base"
```
Resultado r4: **24 pasaron en 7,32 s**. La métrica CVaR queda informativa; la factibilidad utiliza el desliz medio del corte frente al máximo configurable de 2 %.

Se conectó el tope configurable al ranking del CLI. Comando exacto de regresión de CLI y objetivo:

```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43f0-objetivo-20261030-r5'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/test_scenarios_metrics.py tests/test_scenarios_objective.py tests/test_scenarios_evaluate_cli.py --basetemp="$base"
```
Resultado r5: **7 pasaron en 0,22 s**.

## 43f-1 — generador de candidatos

Comando exacto previo a las pruebas:

```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43f1-candidatos-20261030-r1'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/test_scenarios_candidates.py tests/test_scenarios_objective.py --basetemp="$base"
```
La corrida r1 dio 6 pasaron y 1 fallo: tres geometrías Simple fueron descartadas correctamente por notional mínimo, por lo que el esperado de 18 candidatos no era válido. La prueba ahora espera 15 Simple válidos y comprueba los tres descartes. Comando corregido:

```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43f1-candidatos-20261030-r2'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/test_scenarios_candidates.py tests/test_scenarios_objective.py --basetemp="$base"
```
Resultado r2: **7 pasaron en 0,19 s**. Los candidatos Smart se generan solo cuando el llamador entrega las dos mejores Simple de la primera pasada, para no preseleccionar Smart antes de medir.

## 43f-2 — caché horaria de escenarios

Se añadió el almacenamiento de corridas y resultados en `market_data.db`, con retención de siete días, junto con `ScenarioCacheJob` horario al minuto 10 UTC. El ajuste GLOBAL `scenario_cache_enabled` queda apagado por defecto; el job lanza `scripts/escenarios_cache.py` en un subproceso, con prioridad baja, timeout de 600 s y límite de memoria virtual de 600 MiB en POSIX cuando el sistema permite aplicarlo. Un timeout registra `status=timeout`. La ruta del job importa solo el scheduler y SQLAlchemy, no numpy ni el evaluador de escenarios.

El script contempla metas activas y perfiles configurados para v2; los grids actuales se guardan como aproximaciones etiquetadas `mantener`. Las propuestas incluyen USDT quieto. Sin historia, filtros o biblioteca suficientes el resultado se conserva como omitido con motivo, sin inventar métricas. La caché aún es diagnóstica y no participa en decisiones de v1.

Comando exacto de pruebas enfocado antes de ejecutar:

```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43f2-cache-20261030-r1'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/test_scenario_cache_job.py --basetemp="$base"
```
Resultado r1: 3 pasaron en 0,83 s. Se agregó una prueba de corrida completa contra SQLite temporal; comando de verificación:

```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43f2-cache-20261030-r2'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/test_scenario_cache_job.py --basetemp="$base"
```
Resultado r2: **4 pasaron en 0,94 s**. La prueba completa valida escritura de ambas estrategias de resultado (Simple y USDT quieto) con un almacén de mercado temporal y un evaluador sintético; no se consultaron velas reales ni se llamó a red.

Límite pendiente de 43f-2: la caché de metas existentes aún aproxima capital y objetivo usando `capital_initial` y `target_usdt`, no el efectivo libre más el valor de liquidación actual ni el objetivo neto de la ganancia realizada. Tampoco aplica el límite de pérdida relativo al patrimonio actual. Por eso sus corridas no deben habilitar operación v2 hasta cerrar este cálculo con una fuente temporal validada.

## 43f-3 — motor v2 consumidor de caché

El motor v2 ya puede leer la corrida completa más reciente para la meta/perfil en modo SQLite solo lectura. Rechaza expresamente los interruptores apagados, las corridas ausentes y las cachés con más de dos horas; no deriva a v1. El global `v2_engine_enabled` inicia falso y solo puede activarse con confirmación después de activar `scenario_cache_enabled`. La configuración de perfil sigue predeterminada en v1; la selección v2 requiere el global encendido. La revisión de grids activos es conservadora y devuelve no-op en esta primera integración: la caché no reconfigura ni rota órdenes vivas.

Comando exacto de pruebas enfocado antes de ejecutar:

```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43f3-engine-20261030-r1'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/test_v2_scenario_engine.py tests/test_scenario_cache_job.py --basetemp="$base"
```
La corrida r1 aprobó 7 pruebas en 1,15 s. Se incluyó `capital` en los parámetros persistidos para conservar la fracción de presupuesto elegida, y la sombra v1 ahora solicita `_build_plan(record=False)`. Verificación r2:

```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43f3-engine-20261030-r2'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/test_v2_scenario_engine.py tests/test_scenario_cache_job.py --basetemp="$base"
```
Resultado r2: **8 pasaron en 1,09 s**. Discrepancia conservadora: la revisión v2 de metas ya activas aún no cambia grids ni órdenes; devuelve no-op con la corrida vigente porque la política de reconfiguración no está validada. La evaluación v1 en sombra usa `record=False`; no se agrega fallback cuando falla la caché v2.

La disponibilidad publicada en API se corrigió para depender de ambos interruptores globales y de una corrida `ok` vigente (<2 h). Comando exacto de comprobación r3:

```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43f3-engine-20261030-r3'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/test_v2_scenario_engine.py tests/test_scenario_cache_job.py --basetemp="$base"
```
Resultado r3: 8 pasaron y 1 falló porque el test usaba una fecha fija incompatible con el reloj efectivo. El fixture ahora toma la hora UTC actual; verificación r4:

```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43f3-engine-20261030-r4'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/test_v2_scenario_engine.py tests/test_scenario_cache_job.py --basetemp="$base"
```
Resultado r4: **9 pasaron en 1,15 s**. La API publica v2 disponible solo con ambos interruptores y caché reciente; no cambia el estado apagado predeterminado.

## 43f-3b — revisión v2 mantener/cambiar

El plan usa un único ranking `rank_alternatives_v2`, incluido USDT quieto; si este es primero, la asignación queda vacía y `start_plan` devuelve estado `USDT_QUIETO` sin iniciar una meta ni abrir piezas. Se agregó la evaluación identificable `mantener` para cada grid activo, preservando `grid_id` y geometría. La revisión requiere +5 puntos de P(meta) y ganancia neta tras comisiones/pérdida de cierre por encima del promedio de mantener menos un error estándar; solo después delega la ejecución al flujo v1 existente, que mantiene frenos de persistencia, cooldown, cortes y límites de rotación.

La sombra v1 corre `_build_plan(record=False)` en un hilo daemon con espera máxima de 30 s; vencido el presupuesto devuelve `sin_tiempo`. Comando exacto de pruebas antes de ejecutar:

```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43f3b-review-20261030-r1'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/test_v2_scenario_engine.py tests/test_scenario_cache_job.py --basetemp="$base"
```
La corrida r1 encontró que la lectura omitía la columna de descartes y fallaron 2 de 12; se corrigió el SELECT. Verificación r2:

```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43f3b-review-20261030-r2'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/test_v2_scenario_engine.py tests/test_scenario_cache_job.py --basetemp="$base"
```
Resultado r2: 11 pasaron y 1 falló porque el fixture no incluía el desliz que el objetivo exige para considerar una alternativa factible; el fixture se completó con las métricas del contrato. Verificación r3:

```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43f3b-review-20261030-r3'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/test_v2_scenario_engine.py tests/test_scenario_cache_job.py --basetemp="$base"
```
Resultado r3: **12 pasaron en 1,53 s**. Se agregaron casos explícitos para el bloqueo por pérdida/comisiones y vencimiento del presupuesto v1. Verificación final r4:

```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43f3b-review-20261030-r4'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/test_v2_scenario_engine.py tests/test_scenario_cache_job.py --basetemp="$base"
```
La corrida r4 dio 13 pasaron y 1 fallo: reemplazar `threading.Event` interfería con el mecanismo interno de `Thread`; el test ahora reduce la constante de presupuesto sin parchear threading. Verificación r5:

```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43f3b-review-20261030-r5'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/test_v2_scenario_engine.py tests/test_scenario_cache_job.py --basetemp="$base"
```
La corrida r5 mostró una carrera: la función falsa terminaba antes de expirar incluso con presupuesto cero. El test ahora bloquea y libera explícitamente el hilo. Verificación r6:

```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43f3b-review-20261030-r6'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/test_v2_scenario_engine.py tests/test_scenario_cache_job.py --basetemp="$base"
```
Resultado r6: **14 pasaron en 1,41 s**. Las pruebas cubren ranking con USDT quieto, plan sin asignación consumible, mantener por umbral/costo, delegación de cooldown al freno existente y timeout de sombra v1. No se comparó todavía la persistencia de todas las tablas antes/después; queda para el commit de regresión de v1.


## 43f-3c ? memoria y contexto financiero

Antes de las pruebas de 43f-3c, comando exacto (PowerShell; `ASPLE_OFFLINE=1`; `--basetemp` nuevo):

```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43f3c-memoria-20261030-r1'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/test_scenario_cache_job.py tests/test_v2_scenario_engine.py --basetemp="$base"
```

Alcance de la valoración: los eventos con importe/fee no reconstruibles detienen la valoración de esa meta y la caché queda `skipped`; no se inventa efectivo. Cuando la comisión histórica falta pero el quote sí es atribuible, se estima 0,1 %. El inventario se valora al último cierre almacenado menos comisión de salida estimada de 0,1 %, por lo que no sustituye una valoración ejecutable por libro.

La primera corrida enfocada de 43f-3c (r1) dio **17 pasaron en 1,82 s**. Tras añadir la lectura RSS nativa en Windows, se repitió con r2:

```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43f3c-memoria-20261030-r2'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/test_scenario_cache_job.py tests/test_v2_scenario_engine.py --basetemp="$base"
```

Resultado r2: **17 pasaron en 1,78 s**. Las pruebas verifican variables de entorno, ausencia de `preexec_fn`/`RLIMIT_AS`, marcado de estado `memoria`, capital libre + inventario y conservación de pruebas del motor.


## 43f-3d ? regresión congelada de v1

Comando exacto antes de correr las pruebas (PowerShell, carpeta r1 nueva y modo offline):

```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43f3d-v1-baseline-20261030-r1'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/test_meta_own_grid_evaluation.py tests/test_meta_engines_regression.py tests/test_v2_scenario_engine.py --basetemp="$base"
```

La prueba nueva carga `_build_plan` y `_review_allocations` directamente del commit `776eedf`, y compara ambos resultados con la implementación actual usando dos operadores aislados y fixture sintético determinista. Los casos v2 disponible apagado/encendido y sombra se cubren con las pruebas enfocadas ya existentes del motor y su integración.


La corrida r1 dio 36 pasaron y 1 falló: el plan incluía `allocation_shadow`, una medición informativa ampliada desde 776eedf. La regresión ahora compara el plan operativo (quitando solo ese bloque informativo) y mantiene plan y revisión operativa congelados. Repetición con basetemp nuevo:

```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43f3d-v1-baseline-20261030-r2'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/test_meta_own_grid_evaluation.py tests/test_meta_engines_regression.py tests/test_v2_scenario_engine.py --basetemp="$base"
```

Resultado r2: **37 pasaron en 2,27 s**. La regresión confirma que el plan operativo y la acción de revisión de v1 coinciden con los métodos congelados de `776eedf`; solo se excluye `allocation_shadow`, que es informativo.


## 43f-4 ? persistencia de decisiones sombra (parcial)

Comando exacto antes de pruebas (modo offline, basetemp r1 nueva):

```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43f4-shadow-20261030-r1'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/test_v2_scenario_engine.py tests/test_meta_engines_regression.py --basetemp="$base"
```

La parte guarda decisiones activas/sombra en `engine_shadow_decisions` y la bitácora existente conserva el detalle. El marcador que simula la alternativa sombra contra velas reales y llena `shadow_outcome_json` no está implementado en este commit; el campo queda nulo.

Resultado r1: **19 pasaron en 1,64 s**. El evento de decisión se persiste y el esquema contiene el campo pendiente `shadow_outcome_json`.


## 43f-5 ? panel y controles v2

Comando exacto antes de pytest (PowerShell, offline y basetemp nuevo):

```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43f5-panel-v2-20261030-r1'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/test_v2_scenario_engine.py tests/test_meta_engines.py tests/test_meta_engines_regression.py --basetemp="$base"
```

Se agregaron dos interruptores globales con actor/confirmación, estado de la última corrida, panel de decisiones sombra y bloqueo de selección de v2 si no está disponible.


La corrida r1 de 43f-5 dio **19 pasaron y 3 fallaron en 2,27 s**. Los tres casos conservaban la expectativa antigua de v2 permanentemente no disponible. Se actualizaron para comprobar el bloqueo por falta de interruptor/contexto y el nuevo mensaje; la capacidad del código queda activable solo mediante interruptor global y caché vigente. Repetición r2:

```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43f5-panel-v2-20261030-r2'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/test_v2_scenario_engine.py tests/test_meta_engines.py tests/test_meta_engines_regression.py --basetemp="$base"
```

La r2 tuvo **21 pasaron y 1 falló** porque la prueba invocaba `review` con `operator=None`, fuera del contrato del motor. Se cambia a un operador sintético válido sin grids y se repetira con r3:

```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43f5-panel-v2-20261030-r3'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/test_v2_scenario_engine.py tests/test_meta_engines.py tests/test_meta_engines_regression.py --basetemp="$base"
```

Resultado r3: **22 pasaron en 1,86 s**; `node --check frontend/metas.js` y `node --check frontend/lab.js` terminaron correctamente.


## 43f-6: recomendación de grids aislada

Comando exacto antes de pytest (offline, basetemp r1 nueva):

```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP 'asple-f43f6-recommend-20261030-r1'; if (Test-Path $base) { throw "basetemp ya existe: $base" }; pytest tests/test_scenarios_recommend.py --basetemp="$base"
```

La función vive en `research/scenarios/recommend.py` y no se conecta a grids, Fase 42 ni al operador. Resultado r1: **2 pasaron en 0,18 s**. Se cubren ranking por probabilidad/ganancia, geometría logarítmica y offsets del bot.

La recomendación es una interfaz de investigación reutilizable. No se conecta a la caché Meta, no lee la base de la aplicación ni cambia la disponibilidad del motor. 43f-4 sigue parcial: falta el job diario que mida los resultados de la decisión sombra y complete `shadow_outcome_json`.
