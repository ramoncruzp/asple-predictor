# Reporte Fase 17D-2

## Resultado

Se completaron J1–J8 sin commits, push, llamadas a Binance/Testnet ni lectura de `.env`. Los cambios de cierre y conciliación se validaron con fakes y datos sintéticos. El cierre inteligente mantiene su plan en `params.target_close_plan` para reutilizar las fases de recuperación existentes; `reason=PROFIT_CLOSE` identifica el flujo y evita emitir `TARGET_REACHED`.

## Cambios por bloque

- **J1 — Compras parciales al cancelar:** `grid/engine.py:950-1040,2052-2157,2682-2708` separa la contabilización del fill de la colocación de SELL. `cancel_grid_orders` usa `place_sell=False`, conserva el inventario como no gestionado y lo devuelve; pausa, repositorio y cierre por objetivo mantienen SELL protector. También se contabiliza una ejecución parcial devuelta junto con CANCELED durante el cierre objetivo (`grid/engine.py:2690-2700`).
- **J2 — Etiquetas:** `grid/status_view.py:75-101,520-530` presenta los eventos nuevos y toma la severidad info/warning de `DUST_RECONCILIATION` desde sus detalles.
- **J3 — Token API:** `frontend/app.js:14-21` marca el rechazo de prompt para no repetirlo hasta recargar; al aceptar un token conserva un único reintento. La prueba Node comprueba rechazo repetido y retry.
- **J4 — Caché de volatilidad:** `grid/volatility_provider.py:4,38-43,95-130` protege la caché con `threading.Lock`, mantiene 30 minutos para resultados positivos y usa 90 segundos para resultados ausentes o fallidos.
- **J5 — Backups SQLite:** `scheduler/backup_loop.py:15-73` resuelve rutas relativas desde la raíz del repositorio, registra la ruta al iniciar, valida cada copia con `PRAGMA integrity_check` y elimina una copia inválida antes de aplicar retención.
- **J6 — Deriva de inventario:** `grid/reconciliation.py:1-24` comparte el cálculo de cantidades registradas; `api/routes/grid_account.py:13,169-180` mantiene el mismo formato de resumen. `grid/monitor.py:45-51,96-142,775` concilia cada activo una vez por hora, compara free+locked con el registro, crea baseline por combinación exacta de activo/grid_ids y alerta por deriva valorizada según umbral USDT o porcentaje del capital. Fallos de saldo se omiten sin interrumpir el monitor. Los balances ajenos constantes quedan absorbidos por el baseline; un cambio manual posterior sí genera deriva.
- **J7 — Informe y mutaciones:** este reporte enumera las pruebas modificadas y el resultado de la campaña temporal.
- **J8 — Cierre inteligente:** `grid/policy.py:286-331` agrega `plan_profit_close`, que vende todas las celdas con ganancia neta positiva que pasan filtros y envía las demás al repositorio con motivo y pérdida. `grid/engine.py:2509-2634,2636-2855` añade `profit_repository`, escritura previa, cancelación de BUYs, venta de ganadoras, recuperación por fases y repositorio de las restantes; el evento es `PROFIT_CLOSE`. `api/routes/grid_control.py:52-53` admite el modo; `grid/control_service.py:102,138-181` exige precio fiable y añade cifras a la vista previa. `frontend/grids.js:23-36,84,98,125-139` ofrece los modos en el orden solicitado, cifra ganancia/pérdida, avisa de exposición y mantiene LIQUIDAR para venta total.

## Pruebas añadidas o ampliadas

- `tests/test_grid_monitor.py:63-98` verifica cancelación final con compra parcial y BUY completada durante la cancelación: inventario contabilizado, CANCELLED, inventario no gestionado y ninguna SELL nueva. `tests/test_grid_target.py:398-411` verifica protección de un fill parcial durante el cierre por objetivo.
- `tests/test_grid_status_view.py:21-32` valida etiquetas y severidad dinámica.
- `tests/test_api_auth_phase17d.py:68-88` ejecuta `app.js` con Node para cancelación del prompt y un solo reintento.
- `tests/test_volatility_provider.py:118-145` valida TTL de fallos y acceso concurrente.
- `tests/test_backup_loop.py:48-80` valida rutas relativas y rechazo de copia corrupta sin borrar la anterior.
- `tests/test_grid_monitor.py:235-289` valida baseline, saldo ajeno constante, deriva, cambio de grid_ids, intervalo horario y fallo de lectura. `tests/test_grid_account_api.py` sigue pasando con el helper compartido.
- `tests/test_grid_profit_close.py:8-111` cubre ganadoras, perdedoras, celda no vendible, ganancia bruta que queda negativa tras comisión, paridad de selección con `evaluate_target`, venta mixta y reanudación en MARKED, BUYS_CANCELED, MARKET_SELLS_DONE y REPOSITORY.
- `tests/test_grid_control_api.py:117-137` cubre cifras de vista previa y falta de precio (503), además de dispatch confirmado sin frase adicional. `tests/ui/test_grids_control_ui.py:28-68` ejecuta `grids.js` con Node y comprueba orden de modos, escape HTML, texto y color de ganancia/pérdida, y dry-run antes de confirmación.
- Se actualizó `tests/test_grid_monitor.py:220-233`: el heartbeat ahora espera el evento global de baseline que J6 requiere, sin cambiar las aserciones de snapshots.

## Mutaciones temporales

Se aplicaron y restauraron nueve mutaciones; todas hicieron fallar una prueba (**9/9 muertas**). Se revisó el diff después de restaurar.

| Mutación temporal | Prueba que la detecta |
|---|---|
| J1: forzar SELL durante cancelación | `test_final_cancel_accounts_partial_buy_without_placing_sell` |
| J3: quitar la guarda de prompt rechazado | `test_app_js_declined_prompt_is_not_repeated_and_token_retries_once` |
| J4: usar 30 min para caché fallida | `test_realized_failure_cache_retries_after_90_seconds` |
| J5: omitir `integrity_check` | `test_corrupt_backup_is_removed_without_retention_deleting_older_files` |
| J6: alertar por diferencia absoluta en vez de deriva | `test_dust_reconciliation_uses_drift_to_ignore_constant_foreign_balance` |
| J8: `gain > 0` a `gain >= 0` | `test_profit_close_planner_all_losers_unsellable_and_gross_but_net_loss` |
| J8: vender cualquier celda que pase filtros | `test_profit_close_planner_all_losers_unsellable_and_gross_but_net_loss` |
| J8: emitir TARGET_REACHED al iniciar el modo inteligente | `test_profit_close_engine_sells_winner_and_repositories_loser_without_target_event` |
| J8: saltar fase de cancelación MARKED | `test_profit_close_engine_sells_winner_and_repositories_loser_without_target_event` y recuperación por fase |

## Verificación y límites

- Suite offline: `ASPLE_OFFLINE=1 .\venv\Scripts\python.exe -m pytest -q -m "not live" --basetemp .pytest_17d2_final` → **706 passed, 1 skipped, 18 deselected, 7 warnings**. Los avisos son dos `PytestCollectionWarning` ya conocidos y warnings de XGBoost/PyTorch.
- `node --check frontend/app.js` y `node --check frontend/grids.js`: correctos.
- La campaña usa fakes; no demuestra ejecución en Testnet ni liquidez real. El precio y filtros de la vista previa pueden diferir de la ejecución posterior. La venta a mercado solo se usa para celdas rentables elegidas por el plan; las demás siguen expuestas en el repositorio.
