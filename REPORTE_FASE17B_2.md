# Fase 17B-2 — Correcciones tras auditoría de 17B

## Resultado

Se corrigieron los hallazgos autorizados sin editar `grid/engine.py`, `grid/monitor.py`, `database/` ni `api/main.py`. No se hicieron llamadas reales a Testnet y no se ejecutaron acciones live.

## Cambios y evidencia

1. **Cierres parciales:** `grid/control_service.py:208-226` compara errores y estado final con el estado esperado. Devuelve `outcome`, `status_after` y `errors`; `GRID_ACTION_API` conserva el mismo resultado. La interfaz en `frontend/grids.js:131-135` muestra una alerta accesible para resultados parciales, con errores y la indicación de reintentar/revisar Testnet. `tests/test_grid_control_api.py:186-209` cubre cierre parcial con estado CLOSING y también errores aunque el estado final sea CLOSED.
2. **Precio no disponible:** `grid/control_service.py:102-109` permite continuar con pausa y cierre cancel/repository si falla el ticker, dejando valoración y PnL no realizado en `null` y una nota. Liquidate, sweep-dust, adjust y resume siguen requiriendo precio fiable. Test en `tests/test_grid_control_api.py:211-223`.
3. **Lock:** `grid/control_service.py:14,54-68` define `CONTROL_LOCK_TIMEOUT_SECONDS = 15.0`, lo usa en ambas adquisiciones y registra esperas >3 s en `asple.slow`. Se dejó un mensaje neutral porque el servicio no distingue el lock del monitor del lock de otra acción sin editar el monitor. Pruebas de timeout y espera resuelta en `tests/test_grid_control_service.py:41-72`.
4. **Conciliación:** `api/routes/grid_account.py:128-139,168-180,198` excluye grids CLOSED/ERROR del denominador, añade `capital_share_basis`, carga eventos una vez por grid, excluye USDT de la conciliación por activo y no vuelve a sumar `held_qty` de celdas DONE encima de `dust_qty`. La semántica se confirmó en `database/db_manager.py:1088-1131`: el ledger agrega deltas de polvo una sola vez y `dust_qty` refleja esas entradas; `grid/engine.py:2202,2215` añade el residuo de celdas DONE a ese registro. El barrido descuenta el polvo del registro en `database/db_manager.py:1132-1156`. Pruebas en `tests/test_grid_account_api.py:115-163`.
5. **Rechazos auditables:** `grid/control_service.py:82-86,230-234` escribe `GRID_ACTION_REJECTED` para rechazos de estado/negocio, sin parámetros del cuerpo. `api/routes/grid_control.py:14-35` registra de forma equivalente los errores de validación de request si el grid existe y la petición está autorizada. Los dry-run no escriben eventos. Cobertura en `tests/test_grid_control_api.py:225-258`.
6. **Plan entendible:** `frontend/grids.js:23-59,128-129` muestra resumen cuantificado por acción, escapa valores y deja JSON completo en «Detalle técnico». Los valores nulos se presentan como «no disponible». Se conserva el aviso de Testnet.
7. **Cierre repository y planes:** el cierre repository termina en CLOSED en `grid/engine.py:2452` (inspección read-only); por eso el plan muestra CLOSED en `grid/control_service.py:155`. La guarda del plan TARGET usa el criterio del monitor (`grid/monitor.py:213-214`: fase distinta de COMPLETE); el plan max-days se considera activo en STARTED (`grid/control_service.py:94-99`). El test utiliza MARKED, fase real de TARGET.

## Verificación

- Tests enfocados: **29 passed**.
- Suite offline final, `ASPLE_OFFLINE=1 .\venv\Scripts\python.exe -m pytest tests -p no:cacheprovider -q -m "not live" --basetemp=.pytest-tmp-17b2-final2`: **648 passed, 1 skipped, 18 deselected, 7 warnings; 75.22 s**.
- Mutaciones temporales de esta fase: **7/7 muertas**. Incluyeron outcome/errors, precio opcional en pausa, timeout configurable, USDT, doble conteo de polvo DONE, capital cerrado en denominador y evento de rechazo. Los bytes fuente se restauraron y compararon después de la campaña.
- `node --check`: `frontend/grids.js` y `frontend/cuenta.js` pasaron.
- `git --no-pager diff --check`: limpio. Los informes nuevos no aparecen en `git --no-pager diff --stat` porque son archivos todavía no rastreados.
- No se ejecutó Playwright ni se comprobó la presentación en un navegador. No se hizo commit ni push.
