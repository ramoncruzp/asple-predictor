# Fase 21c — Verificación de dirección y pausa sombra

## Resultado

| Ítem | Estado | Evidencia y verificación |
|---|---|---|
| C1 | HECHO | `database/learning_engine.py:26-88` selecciona la vela 1h que contiene `verify_at` cuando el retraso supera 1h; `database/db_manager.py:307-326, 429-466, 471-630, 843-886` añade estado/retraso nulables, excluye `unverifiable_late` de métricas y lo cuenta aparte. La UI lo muestra en `frontend/battle.js:24`, `frontend/models.js:197` y las rutas en `api/routes/models_status.py:130-138`. Pruebas: `tests/test_direction_verification.py:42-158`; migración dos veces, columnas nuevas nulables y datos previos intactos. Rojo anterior: 4 fallos por estado/migración y uso del precio actual; foco verde anterior: 55 passed. Mutación: precio actual para todas las demoras hizo fallar la prueba de vela (esperaba 101.25, obtuvo 120.0). SHA antes/restaurado `f187787ce8f04bbcbb6287e4a95acba85fa2bdb3193226217c5d1aa2ecf20402`; mutado `d8bd89a7d47f66b7eb35a351458fba00f4e6640896e3f9e2b1816fc857359e20`. |
| C2 | HECHO | `scripts/rescore_direction.py:1-227` implementa `--db`, `--candles` (default `data/cache/xrp_1h.csv`), dry-run de solo lectura y `--apply` con confirmación explícita de copia. Solo re-puntúa outcomes existentes con vela coincidente; las filas sin vela no se escriben. Conserva `verified_at` y calcula el retraso histórico entre ese instante y `verify_at`. El apply valida columnas nuevas y no vuelve a escribir filas ya iguales. Pruebas temporales en `tests/test_rescore_direction.py:55-107`: hash estable en dry-run, `--apply` actualiza resultado/retraso, filas sin vela intactas e idempotencia por hash. Rojo: módulo ausente durante primera colección; verde: 3 passed en foco C2/E2 y 12 passed, 3 warnings en foco integrado final. Mutación que fuerza el apply durante dry-run: falló la aserción del hash de la base temporal. SHA `scripts/rescore_direction.py`: antes `3ff1728ebeab6fd082cb74f8e6afde8be02878678d1fb5ebbeff5515e8df6b60`, mutado `3ec15a53549ebb9b5b81c6dd45043d5c88e3947317ed068ac5afeff6742a68b1`, restaurado `3ff1728ebeab6fd082cb74f8e6afde8be02878678d1fb5ebbeff5515e8df6b60`. No se ejecutó contra la BD ni el CSV real. |
| F1 | HECHO | `database/db_manager.py:204-213, 1607-1673` añade tabla nullable, escritura observacional y resumen de ventanas completas de 4h/24h, estratificado por PAUSE real y pausa sombra de 24h; excluye ventanas con `RUN_GAP`, informa n, salidas, salidas >1,9%, excursión y `muestra insuficiente`. `grid/monitor.py:15, 72-97, 647-665` calcula ambos horizontes con `grid.policy.break_prob` y sus vistas de volatilidad; 24 h solo se guarda cuando la vista tiene `source="model"` (líneas 657-660). El guardado va tras elegir la acción, está aislado con `try/except` y no cambia el objeto de decisión. `api/routes/grids.py:888-891` expone GET protegido por `_authorize`. Pruebas en `tests/test_pause_shadow.py:27-103` y `tests/test_grid_monitor.py:341-360`: PAUSE/ADJUST no alterados, error de escritura aislado, exclusión RUN_GAP, endpoint, migración idempotente y vista 24 h no-campeón como no disponible. Mutación `paused_actual = would_pause_24h`: falló para ADJUST (registró 1, esperaba 0). SHA `grid/monitor.py`: antes/restaurado `207cbadf3526d3c37ab94f902efb224fd7bfc5aced06620f329d891b27bd31af`; mutado `82db61aaa9037e2f978cb6e064dd32c578d63fe9310b199a0b2cbbb6de480260`. No se cambió `grid/policy.py` ni ningún default de Smart. |
| E2 (hallazgo no consumido) | HECHO | `database/learning_engine.py:108-111` conserva `update_condition_accuracy(model_name)` pero no ejecuta consultas ni escrituras: `model_accuracy_by_condition` no tiene lector en el repo según `git grep -n model_accuracy_by_condition -- . ':!models/saved' ':!data'`. La prueba `tests/test_direction_verification.py:160-180` mide 12 sentencias SQL y 6 escrituras antes; ahora exige 0 consultas/escrituras y tabla sin filas. |

## Estado previo del monitor

`monitor_runs` conserva inicio/fin, trigger, estado, contadores y duración (`database/db_manager.py:192-202`). `grid_snapshots` ya guardaba precio (`market_mid`), `break_prob` y `sigma_24h`, pero no los límites de rango (`database/db_manager.py:235-259`, escritura en `grid/monitor.py:223-289`). El nuevo registro añade los límites junto al precio y las dos probabilidades.

El criterio queda documentado en la respuesta de `/api/grids/pause-shadow/summary`: considerar que 4h es mejor solo con al menos 20 salidas observadas, y si las salidas >1,9% fuera del rango sin pausa real no superan las de la pausa sombra 24h. El endpoint no calcula ni emite veredicto. Los datos son observaciones discretas del monitor; pueden omitir excursiones entre pasadas.

## Verificación final

- Base comunicada antes de esta fase: no-UI `1023 passed, 1 skipped, 18 deselected`; UI `107 passed`.
- Foco final tras exigir campeón de 24 h: `12 passed, 3 warnings in 8.61s`
- Suite no-UI final: `1035 passed, 1 skipped, 18 deselected, 40 warnings in 173.31s (0:02:53)`
- Suite UI final: `109 passed, 2 warnings in 159.61s (0:02:39)`; Playwright registró hosts externos bloqueados (`cdn.jsdelivr.net`, `fonts.googleapis.com`) y ningún defecto observado.
- `git diff --check`: limpio. Índice: vacío.
- No verificable sin leer datos prohibidos: cuántas filas reales quedarán `unverifiable_late`; fin real del CSV de velas. La información recibida dice que el caché llega al 27-sep; no se inspeccionó.
- Dry-run recomendado para Ramón: `python scripts/rescore_direction.py --db <copia.sqlite> --candles data/cache/xrp_1h.csv`. Solo tras revisar el JSON, `--apply --i-know-this-is-a-copy` sobre una copia cerrada. No se ejecutó `--apply` en esta tarea.

## Archivos para commits separados

- C: `database/learning_engine.py`, `database/db_manager.py`, `api/routes/models_status.py`, `frontend/battle.js`, `frontend/models.js`, `tests/test_models_page_context.py`, `tests/test_direction_verification.py`, `tests/ui/test_battle.py`, `tests/ui/test_models_page.py`, `scripts/rescore_direction.py`, `tests/test_rescore_direction.py`.

- F: `database/db_manager.py`, `grid/monitor.py`, `api/routes/grids.py`, `tests/test_pause_shadow.py`, `tests/test_grid_monitor.py`.

## Efectos que Ramón verá

Al re-verificar, Battle y Modelos excluirán y contarán aparte las verificaciones tardías sin vela correspondiente. Las migraciones aditivas se aplican al iniciar la aplicación. El script C2 es dry-run por defecto; Ramón debe revisar primero su informe sobre una copia de la BD.
