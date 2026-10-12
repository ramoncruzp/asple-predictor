# Fase 28b — cierre verificado, alerta de polvo y reporte de órdenes huérfanas

Base verificada: `d6bfb33`; preflight inicial limpio. Esta continuación conserva los cambios de Fase 28b ya revisados y corrige la codificación del reporte.

## A. Cierre `liquidate`

**HECHO** — [grid/engine.py](grid/engine.py):2725. `close_grid(..., "liquidate")` incorpora cada `pending_order_id` a los errores, conserva `CLOSING` y deja los IDs en `fail_reason`; el nivel con esa orden no pasa a `DONE`. Se reutiliza `CLOSE_PENDING`, ya presente en `grid/monitor.py:346-351`, sin crear otro evento.

Pruebas: [tests/test_grid_close.py](tests/test_grid_close.py):35 cubre pendientes; :51 cubre error de cancelación; la prueba existente cubre cierre sin pendientes. Mutación: [tests/test_fase28b_mutations.py](tests/test_fase28b_mutations.py):38-61 elimina el uso de pendientes y reproduce el cierre incorrecto. El helper compara SHA-256 original/mutado y confirma que el archivo en disco conserva el SHA original.

## B. Cierre `repository`

**HECHO** — [grid/engine.py](grid/engine.py):2468-2545. Después de cancelar compras se consultan las órdenes abiertas del símbolo. Una compra propia identificada por ID de nivel o por client ID exacto del grid, o un fallo de consulta, deja el grid en `CLOSING`; una compra de otro grid no bloquea el cierre. Las ventas activas conservan el flujo de traslado al repositorio `HOLDING`.

Pruebas: [tests/test_grid_repository.py](tests/test_grid_repository.py):10-47 cubre compra viva de otro nivel, error de consulta y otro grid; las pruebas existentes cubren ausencia de compras vivas y traslado de ventas. Mutación: [tests/test_fase28b_mutations.py](tests/test_fase28b_mutations.py):63-77 omite la verificación y reproduce el cierre con compra viva; el helper compara SHA original/mutado/restaurado.

## C. Alerta `DUST_RECONCILIATION`

**HECHO** — [alerts/formatters.py](alerts/formatters.py):43-69,166-176 muestra activo, deriva compacta con signo, equivalente en USDT a dos decimales y diferencia compacta; si faltan datos usa el formato previo. [alerts/dispatcher.py](alerts/dispatcher.py):211-220 aplica cooldown por activo y conserva las claves de los demás eventos.

Pruebas: [tests/test_alert_formatters.py](tests/test_alert_formatters.py):79-105 valida texto exacto, helper y fallback; [tests/test_alert_dispatcher.py](tests/test_alert_dispatcher.py) cubre activos distintos, repetición del mismo activo y activo ausente. Mutaciones: [tests/test_fase28b_mutations.py](tests/test_fase28b_mutations.py):79-119 rompe el formato compacto y comparte cooldown; ambas comparan SHA original/mutado/restaurado.

## D. Reporte de órdenes huérfanas

**HECHO** — [scripts/orphan_orders_report.py](scripts/orphan_orders_report.py):14-16 antepone a `sys.path` la raíz calculada desde `__file__`; :23-29 valida y deduplica `--symbols`; :32-40 limita los diagnósticos a tipo, nombre del módulo faltante o código numérico. El script es de solo lectura.

Pruebas: [tests/test_orphan_orders_report.py](tests/test_orphan_orders_report.py):109-145 cubre parseo y errores seguros; :148-162 importa desde otro directorio sin `PYTHONPATH`; la prueba AST existente sigue incluida. Mutaciones: [tests/test_fase28b_mutations.py](tests/test_fase28b_mutations.py):122-178 cubre expresión regular débil, raíz ausente, módulo faltante y exposición del mensaje de API; verifica SHA y restauración.

## Verificación

- Pruebas dirigidas: **93 passed**.
- Suite completa offline, ejecutada una vez con `--basetemp=.pytest-28b-final`: **1307 passed, 1 skipped, 18 deselected, 0 failed** (exit code 0; 229.15 s).
- La ejecución anterior dio 911 passed y 396 errores de permisos al enumerar `pytest-of-ramon`; con la carpeta temporal nueva desapareció ese error.
- Auditoría final: codificación UTF-8 sin BOM, LF, sin U+FFFD ni interrogaciones espurias, `git diff --check` limpio y sin patrones de credenciales detectados.
- **NO VERIFICADO:** comportamiento contra el exchange real y entrega del mensaje real de Telegram; no se ejecutaron pruebas live.

## Archivos de la fase

`alerts/dispatcher.py`, `alerts/formatters.py`, `grid/engine.py`, `scripts/orphan_orders_report.py`, `tests/test_alert_dispatcher.py`, `tests/test_alert_formatters.py`, `tests/test_fase28b_mutations.py`, `tests/test_grid_close.py`, `tests/test_grid_repository.py`, `tests/test_orphan_orders_report.py`, `REPORTE_FASE28B.md`.

Commit: pendiente de auditoría final y publicación autorizada.
