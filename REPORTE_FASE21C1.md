# Fase 21c-1 — Precio en verify_at y alcance del dry-run

## K1 — Precio único en verify_at: HECHO

- `database/learning_engine.py:20-47`: `price_at(verify_at, candles)` es una función pura compartida. Usa el intervalo horario semiabierto `[timestamp, timestamp + 1 h)`, interpola linealmente entre `open` y `close`, y documenta que es una aproximación porque OHLC de 1 h no revela la trayectoria intrahoraria.
- `database/learning_engine.py:58-69`: la verificación tardía llama a la función compartida. El umbral de hasta 1 h y el estado `unverifiable_late` no cambiaron.
- `scripts/rescore_direction.py:16,46-47`: la re-puntuación histórica importa la misma función; `_close_at` delega en ella.
- Pruebas sintéticas: apertura, mitad, justo antes del cierre, límite horario con `close_time = apertura + 1 h - 1 ms`, ausencia de vela, hueco y falta de `open`. La ruta viva y el CSV temporal dan el mismo precio (101.0).
- Expectativa C1: el precio esperado sigue siendo 101.25. Cambió el fixture: antes el objetivo coincidía con el fin horario y el cierre era 101.25; ahora la vela abre 30 min antes, abre a 100.0 y cierra a 102.5, por lo que la interpolación a mitad de vela sigue dando 101.25. Se conserva el resultado y la prueba ahora distingue interpolación de cierre.
- Rojo inicial: la prueba dirigida no pudo recolectar porque aún no existía `price_at` (`ImportError`). Verde dirigido posterior: `11 passed, 2 warnings`. Prueba de consistencia adicional: `1 passed`.

### Mutación K1

Mutación por reemplazo exacto del cálculo interpolado por el cierre de la vela. La prueba falló en la aserción de apertura: esperaba 100.0 y obtuvo 110.0. Restauración byte por byte verificada.

| Archivo | SHA-256 antes | Mutado | Restaurado |
|---|---|---|---|
| `database/learning_engine.py` | `4af65c1a01cb1cc0aa4cc2ac9c46e24c5cf59201f9447db930b6331686a88bf0` | `2c202b17350d8c0864abd1ffb1fc1536a6223d791a967f78f5fb4b7cf74168e2` | `4af65c1a01cb1cc0aa4cc2ac9c46e24c5cf59201f9447db930b6331686a88bf0` |

## K2 — Filas tardías fuera del CSV: HECHO

- `scripts/rescore_direction.py:151-161,201-214`: el dry-run cuenta filas con retraso mayor de 1 h sin vela, informa primera y última fecha de `verify_at`, fecha/hora de la última vela y el mensaje explícito: “estas filas no se corrigen con este CSV; actualiza el caché de velas y vuelve a correr el dry-run”.
- Prueba con SQLite y CSV temporales: cuenta 1 fila ausente, fecha primera/última `2026-01-04T00:30:00`, última vela `2026-01-01T00:00:00`.
- `--apply` continúa dejando sin cambios las filas sin vela; no las marca `unverifiable_late`.

## Verificación

- Pruebas enfocadas K1/K2 antes del retoque ortográfico: `11 passed, 2 warnings`; consistencia: `1 passed`; tras corregir “caché” en el mensaje, `tests/test_rescore_direction.py`: `4 passed`.
- No-UI literal: `1039 passed, 1 skipped, 18 deselected, 40 warnings in 79.90s (0:01:19)`. Esta corrida precedió solo a la corrección ortográfica del literal `caché`; después pasó el archivo afectado completo (`4 passed`).
- UI literal: `109 passed, 2 warnings in 154.67s (0:02:34)`; sin defectos de UI observados.
- Offline: `ASPLE_OFFLINE=1`; las suites se ejecutaron con `-m "not live"`. Sin red, sin lectura/escritura de base real, `data/` o `models/saved/`. No stage, commit ni push; índice vacío.
- Archivos modificados de esta continuación: `database/learning_engine.py`, `scripts/rescore_direction.py`, `tests/test_direction_verification.py`, `tests/test_rescore_direction.py`, este reporte.
- Alcance adicional aceptado de 21c: se desactivó el refresco de `model_accuracy_by_condition` tras confirmar que no tiene lectores; no se repitió ese cambio.

## Archivos para el commit único 21c + 21c-1

`api/routes/grids.py`, `api/routes/models_status.py`, `database/db_manager.py`, `database/learning_engine.py`, `frontend/battle.js`, `frontend/models.js`, `grid/monitor.py`, `tests/test_grid_monitor.py`, `tests/test_models_page_context.py`, `tests/ui/test_battle.py`, `tests/ui/test_models_page.py`, `REPORTE_FASE21C.md`, `scripts/rescore_direction.py`, `tests/test_direction_verification.py`, `tests/test_pause_shadow.py`, `tests/test_rescore_direction.py`, `REPORTE_FASE21C1.md`.

## No verificado

No se consultó un servicio real ni datos de mercado; la interpolación solo se verificó con datos sintéticos/temporales. El precio intrahorario real no puede comprobarse a partir de velas horarias.
