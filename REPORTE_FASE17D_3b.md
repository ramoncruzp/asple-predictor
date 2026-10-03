# Fase 17D-3b — cierre de pendientes de auditoría

## Cambios por bloque

- **L1 — Preview de comisiones (`grid/engine.py:32,448-482`; `grid/control_service.py:170-184`; `grid/policy.py:56-85`; `frontend/grids.js:23-28`)**: caché LRU por `client_order_id`, TTL de 120 s y máximo de 512 registros, usada solo por el preview; el deadline de preview se inyecta desde el servicio y deja las celdas restantes con comisión estimada cuando vence. La ejecución sigue consultando trades frescos. El conteo `estimated_fee_cells` llega al plan y se muestra solo si es positivo. La conversión de comisión aplica el costo y fee reales juntos solo después de convertir con éxito; ante error conserva ambos valores estimados.
- **L2 — Reintento de `PROFIT_CLOSE` (`grid/policy.py:46-53`; `grid/engine.py:2627-2628,2712-2719,2772-2776,2869-2879`; `grid/monitor.py:725-739`)**: backoff exponencial con tope de 900 s, estado persistido (`retry_count`, `last_retry_at`), agotamiento tras ocho reintentos con evento único y reanudación manual explícita. El éxito limpia el estado de reintento.
- **L3 — cancelación de compras y campo muerto (`grid/engine.py:2180-2200`; `tests/test_grid_engine.py:477-481`)**: el resumen de fills parciales solo se captura cuando la liquidación parcial se asentó correctamente; se eliminó `has_sell`, sin consumidores encontrados.
- **Pruebas (`tests/test_grid_profit_close.py:115-192,259-305`; `tests/test_grid_monitor.py:140-158`; `tests/ui/test_grids_control_ui.py:32-55`; `tests/test_grid_control_api.py:42-55`)**: cubren caché frente a ejecución fresca, deadline y contador, comisión estimada, fallo de conversión atómico, backoff, éxito en tercer intento, agotamiento/reanudación, captura condicional y salida Node del plan. El harness Node decodifica explícitamente UTF-8 para comparar los textos con tildes.

## Mutaciones temporales

Se ejecutaron cinco mutaciones requeridas. Cada archivo mutado se restauró en `finally`; las pruebas enfocadas y luego la suite final se ejecutaron con el código restaurado.

| Mutación | Resultado | Prueba que detectó la mutación |
|---|---|---|
| Desactivar caché de preview | Muerta | `test_profit_preview_caches_trades_but_execution_reads_fresh_fees` |
| Desactivar las dos comprobaciones del deadline | Muerta | `test_profit_preview_deadline_marks_remaining_cells_as_estimated_without_sleep` |
| Desactivar backoff del monitor | Muerta | `test_profit_retry_backoff_and_third_attempt_success_clears_retry_state` |
| Desactivar conjuntamente los topes de monitor y engine | Muerta | `test_profit_retry_exhaustion_emits_once_stops_monitor_and_manual_close_resets` |
| Capturar fill aunque falle el asentamiento parcial | Muerta | `test_cancel_summary_only_captures_partial_buy_when_settlement_succeeds[partial_response]` |

**Mutaciones detectadas: 5/5.** No quedaron mutaciones temporales aplicadas.

## Verificación

- Suite offline completa: `ASPLE_OFFLINE=1 .\venv\Scripts\python.exe -m pytest -q -rs -m "not live" --basetemp .pytest-17d3b-offline-final` → **720 passed, 1 skipped, 18 deselected, 7 warnings**, 45.10 s. El skip es `tests/ui/test_grids_ui.py` porque no está instalado `playwright`.
- Pruebas enfocadas: **103 passed**, 1 warning de colección de `TestnetOrderError`.
- `node --check frontend/grids.js` y `node --check frontend/app.js`: ambos terminaron con código 0.
- Los dos comandos de nombres de diff requeridos devolvieron 28 nombres cada uno. Diferencia `git diff --name-only` menos `git diff --ignore-cr-at-eol --name-only`: **lista solo-CRLF vacía (`[]`)**. No se modificaron archivos para normalizar EOL.
- Sin stage ni commit.

## Límites de verificación

No se hicieron llamadas reales a Testnet/Binance. La latencia se verificó con reloj inyectado, no con red real. El frontend se comprobó con Node y DOM stub, no en un navegador con DOM real. El skip de Playwright limita la verificación visual/interactiva.
