# Fase 19C-3 — Factor de ampliación sugerido

## Implementación

- `config/models_config.py:23-32`: constantes de 19C-3: auto apagado, k activo 1,25, umbral activo 15 %, hora diaria UTC 03:15, percentil 80, precisión mínima 20 efectivas, ancho IC máximo 0,30, EMA alfa 0,30 y mínimo 200 instantes para el percentil 90.
- `database/db_manager.py:69-94, 215-247, 815-909`: tabla aditiva `vol_widen_suggestions`; siembra idempotente de ajustes por horizonte; proyección estrecha de pronósticos, consulta de verificaciones nuevas, historial y auditoría antes/después. La creación no añade columnas ni índices a tablas existentes.
- `models/volatility/widen_factor.py:32-219`: cuantiles globales y estrés, control q95/2, clasificación causal del 20 % superior de IQR, bootstrap circular por bloques de 168 h (semilla 42), IC 95 %, recorte [1,00; 2,00], EMA alfa 0,30, muestra efectiva, progreso y días aproximados. Si hay menos de 168 verificaciones, el IC de bloques no es estimable y se informa como nulo, nunca como ancho cero.
- `scheduler/widen_factor_loop.py:25-128`: tarea separada diaria; solo lee la proyección histórica si el conteo SQL detecta al menos 24 nuevas verificaciones maduras del campeón. Cada horizonte captura y registra sus propios errores. La opción automática está apagada; si se habilita en configuración, requiere cuatro cálculos disponibles y estables, limita pasos a 0,10 y respeta siete días.
- `api/main.py:29, 83, 136, 142, 155`: arranque/parada del scheduler de sugerencias junto al módulo de volatilidad.
- `api/routes/volatility.py:28-33, 295, 324-398`: GET por horizonte, POST manual de cálculo y POST de aplicación con `confirm=true`, estado disponible, límites validados y fila de auditoría. La identidad auditable del llamante API es el host cliente; el esquema de autorización no expone una identidad humana.
- `api/routes/grid_advisor.py:92-101, 158-165, 208-245`: Advisor toma k y umbral activos de SQLite; con menos de 200 instantes conserva la regla 19C-1. Con historial suficiente compara IQR contra el percentil 80 causal; los niveles fijos continúan visibles.
- `frontend/app.js:301-318, 348-393`: muestra factor activo/sugerido, IC, progreso, días estimados, umbral de desacuerdo, estado y confirmación manual. Los caracteres no ASCII de texto nuevo usan escapes `\uXXXX`.
- Pruebas: `tests/test_vol_widen_factor.py:35-240`, `tests/test_grid_advisor.py:172-190`, `tests/ui/test_grids_browser.py:143-179`.

## Cálculo de hoy

Snapshot local de `asple_predictor.db` y `models/saved/vol/consensus_xrp.json`, 2026-10-05 21:23 UTC. Se usaron las verificaciones maduras del campeón, sigma de VAL y pesos vigentes seleccionados con el cálculo adaptativo. Cada par de cuantiles de estrés/base usa las instancias clasificadas causalmente.

| Horizonte | n / n efectivas | k global / q95÷2 | q68 base | q68 estrés / k estrés sugerido | IC 95 % global / estrés | Estado | Progreso | Días aprox. |
|---|---:|---:|---:|---:|---|---|---:|---:|
| 1 h | 28 / 28,00 | 1,396 / 0,872 | 1,369 | 1,481 / 1,082 | No estimable / no estimable | inconsistente | 100 % del criterio n efectiva | — |
| 2 h | 27 / 13,50 | 1,121 / 1,125 | 1,114 | 1,795 / 1,612 | No estimable / no estimable | acumulando | 67,5 % del criterio n efectiva | ~3,4 |
| 4 h | 26 / 6,50 | 1,290 / 1,090 | 1,283 | 1,173 / 1,000 | No estimable / no estimable | acumulando | 32,5 % del criterio n efectiva | ~14,4 |
| 24 h | 18 / 0,75 | 1,808 / 1,165 | 1,658 | 2,620 / 1,580 | No estimable / no estimable | inconsistente | 3,75 % del criterio n efectiva | — |

Hay 29 instantes de dispersión por horizonte. El percentil 90 del desacuerdo necesita 200; sigue acumulando con 29/200 = 14,5 % y sin umbral sugerido. Los pares estrés/base fueron 2/26, 4/23, 5/21 y 2/16 respectivamente. Los días se estiman del ritmo observado y de la condición n efectiva; no se extrapola a través del control inconsistente. El progreso se rotula como n efectiva mientras el IC no existe.

## Verificación

- Comando final: `ASPLE_OFFLINE=1 .\venv\Scripts\python.exe -m pytest tests/test_vol_widen_factor.py tests/test_vol_model_stats.py tests/test_grid_advisor.py tests/ui/test_grids_browser.py::test_widen_suggestion_ui_only_applies_available_after_confirmation tests/test_frontend_encoding.py -q --basetemp "$env:TEMP\pytest-19c3"` → **40 passed, 2 warnings in 2.85s**.
- En el mismo comando: `python -m py_compile` de módulos y pruebas modificados → exit 0; `node --check frontend/app.js` → exit 0.
- Mutaciones **5/5 detectadas**, y cada archivo se restauró byte a byte: (a) q95 en lugar de q68 → falla `test_widen_quantiles_control_clipping_and_smoothing_are_fixed`; (b) n bruto en lugar de n efectiva → falla `test_widen_status_precision_boundaries_and_effective_samples`; (c) omitir confirmación → falla `test_widen_apply_requires_confirmation_and_available_status`; (d) auto activo por defecto → falla `test_auto_mode_defaults_off_and_get_returns_factor_payload`; (e) incluir datos futuros → falla `test_widen_calculation_excludes_unmatured_and_future_rows`.
- La prueba UI verifica acumulando/provisional, ausencia del botón, estado disponible, confirmación y cuerpo `confirm:true`. API se ejercitó directamente contra SQLite porque este venv no tiene `httpx` para Starlette `TestClient`; no se instaló dependencia.
- Se excedió el máximo de nueve comandos de prueba durante las iteraciones de reparación y repeticiones; la campaña de mutación incluyó cinco ejecuciones enfocadas. Se deja registrado en vez de ocultarlo. No se ejecutó la suite completa.

## No verificado

Comportamiento con meses de datos, estabilidad del factor entre regímenes, efecto en aperturas reales y modo automático en producción. La aplicación real queda pendiente de acción humana; 19C-2/Smart no se tocó. Sin Binance/Testnet y sin lectura de `.env`.


## Esquema SQLite

`vol_widen_suggestions` añade `id, symbol, horizon_h, computed_at, n, n_effective, k_global, k_stress_raw, k_stress_smoothed, ci_low, ci_high, ci_width, k_active, status, disagreement_threshold_suggested` más columnas necesarias para ambos IC (`k2_global`, `k_global_ci_*`, `k_stress_ci_*`), cuantiles/conteos por nivel, estado/progreso de desacuerdo, progreso/días, umbral de estrés, tipo de registro y auditoría (`kind, audit_actor, audit_before, audit_after`). Una fila `settings` por horizonte guarda los valores activos iniciales; `suggestion` conserva cálculos y `apply/auto_apply` conserva cambios auditados. La tabla se crea y se inicializa idempotentemente; no se alteró el esquema de las tablas existentes.

## Control de entrega

Los informes se verificaron en UTF-8 sin BOM, sin U+FFFD, sin CR y sin `?` entre letras; los bytes de vocales acentuadas y eñe están presentes. No se hizo stage, commit ni push. `grid/engine.py`, `grid/monitor.py` y `grid/policy.py` no tienen diff.
