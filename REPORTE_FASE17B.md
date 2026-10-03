# Fase 17B — Acciones de control y cuenta Testnet

## Resultado

Implementé las rutas de control y la pantalla de cuenta en los archivos permitidos. El prerrequisito se verificó en Git: 9c9a86f contiene Fase 17A y 17A-4. No hice commit, push ni llamadas a Testnet real. Las pruebas usaron fakes con ASPLE_OFFLINE=1.

## Acciones y cifras del plan

| Acción | Método del motor | Plan presentado y fuente |
|---|---|---|
| Pausar | GridEngine.pause_grid, grid/engine.py:1635 | Cuenta órdenes BUY a cancelar y SELL que siguen activas desde get_open_orders; informa inventario, valor a bid, PnL no realizado y estado PAUSED. |
| Reanudar | GridEngine.resume_grid, grid/engine.py:1647 | Compara el bid Testnet con rango y niveles, con avisos si queda fuera o por debajo de todos. |
| Cerrar | GridEngine.close_grid, grid/engine.py:2456 | Exige modo. cancel informa órdenes canceladas e inventario retenido; repository separa celdas movibles y cantidad no gestionada; liquidate informa cantidad, PnL no realizado y comisión estimada de 0.1 %, calculada con cantidad por bid. Este modo exige confirm_text=LIQUIDAR. |
| Reubicar | preview_adjust y adjust_grid, grid/engine.py:1609,1310 | Usa el plan del motor con celdas movibles y atrapadas. Solo ACTIVE. |
| Barrer polvo | sweep_grid_dust, grid/engine.py:2125 | Calcula cantidad registrada menos polvo retenido en celdas DONE; plan_dust_sweep y los filtros del símbolo dan cantidad vendible, residual y proceeds netos. Si no hay bid o filtros fiables, responde 503. |
| Editar meta/plazo | validate_params, grid/policy.py:68; merge_grid_params, database/db_manager.py:1052 | Valida target_pct, target_usdt, target_basis, max_days y dust_sweep_threshold_pct; no reemplaza planes de cierre ni contadores. Los null explícitos eliminan la clave. |

La orquestación y el guard Testnet están en [control_service.py](/C:/APPS/ASPLE_Predictor/asple-predictor/grid/control_service.py:37); los seis endpoints cerrados con extra="forbid" y autorización compartida están en [grid_control.py](/C:/APPS/ASPLE_Predictor/asple-predictor/api/routes/grid_control.py:14). La ejecución requiere dry_run=false y confirm=true. La vista previa solo lee datos; la ejecución registra GRID_ACTION_API y los rechazos del motor GRID_ACTION_REJECTED.

## Concurrencia

GridMonitor comparte un threading.RLock alrededor de run_once en [monitor.py](/C:/APPS/ASPLE_Predictor/asple-predictor/grid/monitor.py:46). Las acciones y las vistas previas adquieren ese lock con límite de un segundo; al agotarse, devuelven 409. Si no existe monitor, el servicio usa un lock propio por aplicación. La prueba con hilos confirma que la acción espera a que finalice la pasada.

## Cuenta

GET /api/account/connection y GET /api/account/summary están en [grid_account.py](/C:/APPS/ASPLE_Predictor/asple-predictor/api/routes/grid_account.py:69). No escriben en base ni en exchange; usan el cliente y motor de request.app.state. Conexión y resumen tienen caché de diez segundos. El resumen limita a veinte las consultas de precio por petición.

El balance convierte cada activo con get_avg_price; USDT usa precio 1 y activos sin precio quedan en unvalued. La ganancia por grid reutiliza grid/status_view.py:239 (grid_summary), y el diario reutiliza daily_profit_view en grid/status_view.py:426. La conciliación compara free + locked del exchange con held_qty + dust_qty por activo, incluye repositorios y conserva separados grids del mismo símbolo. Diferencia positiva significa saldo no asignado; negativa significa inconsistencia.

La clave Testnet solo expone su sufijo de cuatro caracteres. Las excepciones se sanean antes de responder o escribir al log. Las claves de producción no se exponen. Las pruebas usan credenciales centinela y comprueban respuestas y caplog.

## UI y chip de API

El detalle activa pausar, reanudar, cerrar, reubicar, barrer y editar meta/plazo por estado. Cada acción muestra primero el plan de la API y exige una confirmación en un diálogo propio; una liquidación además requiere escribir LIQUIDAR. El token se conserva en memoria de pestaña. Capital, compuesto y préstamos entre celdas no se editan en vivo por las invariantes contables y de write-ahead del motor; la UI explica el límite.

La pantalla Cuenta muestra conexión, balance, activos sin valorar, ganancias realizadas y no realizadas, gráficos diarios de 7 y 30 días, grids separados y conciliación. El chip empieza como «Comprobando…» hasta el primer chequeo. Los scripts nuevos no modifican APP.apiHealthState ni llaman setOffline.

## Verificación

- Pruebas enfocadas: 28 passed.
- node --check: frontend/app.js, frontend/grids.js y frontend/cuenta.js pasaron.
- Mutaciones temporales: 18/18 detectadas, 10 de control y merge, 8 de cuenta. Incluyeron flags de ejecución, confirmación de liquidación, modo por defecto, autorización, lock, whitelist, estado de pausa, transacción separada, guard Testnet, fuga de clave y secreto, POST, mezcla por símbolo, omisión de no realizado, signo de conciliación y activo sin precio. Cada archivo se restauró y comparó byte por byte o mediante SHA-256.
- Suite offline completa final: **640 passed, 1 skipped, 18 deselected, 7 warnings en 47.84 s**.
- El diff de api/main.py es 4 líneas totales: 3 inserciones y 1 sustitución. Solo añade imports y dos include_router.
- Los archivos modificados y nuevos quedaron UTF-8 sin BOM, sin U+FFFD y con cero CR. No se modificó grid/engine.py.

### Estadística del diff

`git diff --stat` incluye los ocho archivos existentes modificados: api/main.py (3+, 1−), database/db_manager.py (38+), frontend/app.js (3+, 2−), frontend/grids.css (1+), frontend/grids.js (115+, 6−), frontend/index.html (5+, 3−), grid/monitor.py (5+) y tests/test_frontend_symbols.py (8+, 2−). Total: 178 inserciones y 14 eliminaciones.

Los archivos nuevos todavía no están en el índice, así que Git no los incluye en ese comando. Sus tamaños en líneas son: grid/control_service.py (192), api/routes/grid_control.py (75), api/routes/grid_account.py (202), frontend/cuenta.js (59), frontend/cuenta.css (1), tests/test_grid_control_service.py (54), tests/test_grid_control_api.py (180), tests/test_grid_params_merge.py (58), tests/test_grid_account_api.py (112), tests/ui/test_grids_control_ui.py (21), tests/ui/test_cuenta_ui.py (19), REPORTE_FASE17B.md (55) y DISCREPANCIAS_FASE17B.md (19).

## Límites

No ejecuté Playwright porque su smoke opcional depende de una instalación que no se presupone disponible. Los tests de UI ejecutados son comprobaciones de fuente y flujo. No se verificaron latencias ni balances reales de Testnet; los importes de vista previa se validaron con datos sintéticos. La comisión de 0.1 % es una estimación, ya que Testnet no cobra la comisión de mercado real.
