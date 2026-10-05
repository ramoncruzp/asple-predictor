# Fase 18B-4 — Correcciones del Scanner

## Cambios y evidencia

| Punto | Implementación | Prueba |
|---|---|---|
| M1 | `frontend/scanner.js:23-37, 113, 177`: campos compactos (6 cifras significativas; espaciado hasta 3), valor original en `dataset.exact`, borrado al editar. `tests/ui/test_grids_browser.py:94-98` comprueba valor visible y exacto. | `tests/test_frontend_scanner.py:252-263` (Advisor, API conserva precisión y edición); Playwright del Advisor. |
| M2 | `frontend/scanner.js:33-35, 47-48`: valores positivos pequeños se expresan como límite; cero exacto conserva `$0,00`/`0,000 %`. | `tests/test_frontend_scanner.py:265-271`. |
| M3 | `frontend/scanner.js:81-85, 139`: motivo de celda y sugerencia de niveles/capital, junto a los demás motivos; la inviabilidad impide llamar al endpoint de apertura. El campo existente `minimum_cell_usdt` se entrega desde `api/routes/grid_structure.py:245-253`; no hubo cambio backend. | `tests/test_frontend_scanner.py:274-280`. |
| M4-M5 | `frontend/scanner.js:75, 147, 150`: metas opcionales; mínimo vigente y texto de margen vienen de `margin_guard.minimum_pct`, con etiqueta «mínimo exigido». | Prueba de diálogo en `tests/test_frontend_scanner.py:192-202`; firma reutilizada comprueba la ayuda vigente en `:282-288`. |
| M6 | `frontend/scanner.js:33-34, 175`: coma decimal y espacio antes de `%`; no altera los valores de API. | `tests/test_frontend_scanner.py:203-210`; `tests/ui/test_grids_browser.py:216-234, 237-263`. |
| M7 | `frontend/scanner.js:38, 95-107, 114-115, 178`: firma del payload, reutilización del resultado y espera del recálculo vinculado; una respuesta inviable no inicia apertura. El orden de carga deja el prellenado del Advisor como cálculo vigente. | `tests/test_frontend_scanner.py:282-296`. |
| M8 | Solo medición, sin optimización. La ruta está en `api/routes/grid_structure.py:161-258`; detalle abajo. | Script desechable con `FakeExchange`, 20 solicitudes ASGI locales. |
| M9-M10 | `frontend/scanner.js:5, 7-20, 95-107, 117-171`: un plazo de 30 s para estructura + apertura de vista previa, señal AbortController en ambas llamadas, aborto y descarte de resultado tardío. | `tests/test_frontend_scanner.py:298-310`. |

## Medición M8

`api/routes/grid_structure.py:166` consulta Coin Registry (`database/db_manager.py:659`). En `:172` solicita datos del servicio. `grid/scan_service.py:52-86` obtiene estado, estadísticas de 24 h, información de símbolo/filtros, libro, velas de 5 min y velas de 1 h. La ruta estima sigma directamente de los cierres 1 h (`api/routes/grid_structure.py:179-181`), calcula umbral y variantes (`:184-233`) y arma la respuesta (`:235-258`). No llama a un proveedor de volatilidad ni al simulador.

Medición de 20 llamadas con cliente falso basado en `tests/grid_fakes.py:23` y `FakeExchange`, datos sintéticos y DB temporal; mediana / P95 en ms:

| Etapa | Mediana | P95 |
|---|---:|---:|
| Solicitud ASGI completa | 2.2927 | 3.2155 |
| Registry SQLite | 0.2150 | 0.3291 |
| Estado / estadísticas / info / libro falsos | 0.0003 / 0.0003 / 0.0017 / 0.0014 | 0.0005 / 0.0005 / 0.0021 / 0.0023 |
| Velas falsas 5m / 1h | 0.1001 / 0.0438 | 0.1401 / 0.0526 |
| Sigma de cierres | 0.0386 | 0.0504 |
| Funciones de estructura | 0.1542 | 0.1776 |

Estos tiempos describen solo el fixture local; **NO VERIFICADO**: latencia real de Binance/Testnet, red o navegador. No se infiere que el tiempo observado por el usuario tenga el mismo cuello de botella.

## Verificación dirigida

- `$env:ASPLE_OFFLINE='1'; .\venv\Scripts\python.exe -m pytest -m "not live" -q tests/test_frontend_scanner.py --basetemp='.pytest-18b4-final3'` — **21 passed**.
- `$env:ASPLE_OFFLINE='1'; .\venv\Scripts\python.exe -m pytest -m "not live" -q tests/ui/test_grids_browser.py -k "scanner or prefill_scanner" --basetemp='.pytest-18b4-ui-final3'` — **6 passed, 21 deselected, 2 warnings**.
- `node --check frontend/scanner.js` — código 0.
- Mutaciones temporales, restauradas y comparadas byte a byte: **3/3 muertas**. M3 quitó el mínimo numérico (falló `test_node_infeasible_preview_shows_numeric_cell_reason_and_never_calls_open`); M7 hizo constante la firma (falló `test_node_new_signature_runs_one_structure_request_before_open`); M9/M10 quitó el aborto del deadline (falló `test_node_preview_deadline_aborts_request_and_discards_late_structure_response`).
- No se ejecutó la suite completa ni `tests/ui` completo.

## Estado de cambios

Archivos tocados en esta fase: `frontend/scanner.js`, `tests/test_frontend_scanner.py`, `tests/ui/test_grids_browser.py`, `REPORTE_FASE18B_4.md`, `DISCREPANCIAS_FASE18B_4.md`. No se modificó backend.

`git diff --ignore-cr-at-eol --numstat` muestra en este checkout acumulado `154 18 frontend/scanner.js` y `122 12 tests/test_frontend_scanner.py`; son del diff total local y no aíslan exclusivamente esta fase. `tests/ui/test_grids_browser.py` es un archivo untracked existente en el checkout y por eso no aparece en numstat.

Diff de `grid/engine.py`, `grid/monitor.py`, `grid/policy.py`: vacío. índice staged: vacío. Sin stage, commit ni push.

**No verificado:** apariencia en un navegador del usuario; APIs reales, Binance/Testnet y sus latencias; suite completa (omitida por instrucción).
