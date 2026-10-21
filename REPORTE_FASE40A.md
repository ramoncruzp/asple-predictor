# Reporte — Fase 40a: núcleo del Modo Meta

## Alcance

Implementado el núcleo y sus endpoints de API. No se implementaron el operador que decide de 40b ni la pantalla Metas de 40c. De acuerdo con la aclaración de Ramón, las fases son el orden de construcción del módulo final descrito por D5; no se considera discrepancia con D5.

## Funcionalidad

- Interruptor `meta_mode_enabled`, apagado por defecto y persistido mediante el registro de flags existente: `config/settings.py:43,156`.
- Modelos persistentes `meta_goals`, `meta_runs`, `meta_piece_links`, `meta_ledger` y `meta_profile_settings`; semillas Prudente/Rápido y `MAX_METAS_ACTIVAS=1`: `database/db_manager.py:287-327,340-375`.
- Parámetros de perfil editables por API, objetivo fijo o porcentual normalizado a USDT, copia de parámetros al crear la meta, comprobación Testnet y de capacidad activa: `grid/meta.py:57-133,183-205`.
- Fórmula de efectivo atribuible, como se aprobó:

  `capital inicial − USDT entregado a piezas + USDT devuelto por piezas − comisiones pagadas`.

  La prueba con cifras fijas usa `100 − 60 + 70 − 1 = 109 USDT`. El valor añade el inventario propio de las piezas a BID y resta la comisión estimada de venta de 0,1 %: `grid/meta.py:26,254-325`; `tests/test_meta_core.py:31,59-82`.
- Reserva de inicio: consulta el USDT libre Testnet y resta capital de niveles `IDLE` de otros grids activos, pausados o cerrándose; rechaza si el neto es menor al capital de la meta. Las transacciones de creación/inicio serializan el recuento de capacidad con la fila global: `grid/meta.py:109-133,169-205`.
- Ligado de grids completos y exclusivos, con límites de perfil por moneda y cantidad. `individual_bot_grid` admite únicamente grids de un nivel para que 40b pueda construir esa pieza: `grid/meta.py:208-253`.
- Vigilancia cada minuto con una instancia; usa el `ops_lock` del monitor para pausar/cancelar compras o cerrar. Al 75 % del límite avisa y bloquea nuevas piezas; al límite o al vencimiento invoca `GridEngine.close_grid(..., "liquidate")`, conserva estado CLOSING cuando el cierre verificado queda pendiente y registra deslizamiento: `grid/meta.py:326-435,552-574`.
- `DESCONOCIDO` no se interpreta como cero. Se registra, bloquea nuevas piezas y el despachador Telegram vuelve a alertar con cooldown de 30 minutos: `grid/meta.py:254-325`; `alerts/dispatcher.py:169-215`.
- Al cumplir el objetivo realizado, pausa los grids ligados, cancela solo compras, conserva ventas y registra por separado la ganancia posterior. Cuando termina el inventario sobrante, cierra la espera: `grid/meta.py:434-515`.
- Cancelación presenta valoración y PnL estimado por pieza, emite un plan temporal y exige su ID más confirmación. Vender todo reutiliza el cierre verificado; mover al repositorio reutiliza `_close_repository` y anota origen META e ID en los eventos de traslado: `grid/meta.py:516-580`; `grid/engine.py:2141,2475,2674`.
- Endpoints montados en `/api/metas`: configuración y perfiles, crear/listar/consultar metas, iniciar, ligar piezas, valorar, leer bitácora, obtener plan y confirmar cancelación: `api/routes/metas.py:63-127`; montaje y vigilancia: `api/main.py:136-168,238`.

## Migración

`DBManager.metadata.create_all` crea las cinco tablas. La migración aditiva agrega los campos de linaje del repositorio y de reanudación de cierres en instalaciones con tablas Meta previas; las preferencias de perfil se siembran sin reemplazar valores existentes: `database/db_manager.py:326-375`. La migración se ejecuta automáticamente al inicializar `DBManager`; no se abrió ni migró una base real durante esta fase.

## Pruebas

- Enfocadas: `tests/test_meta_core.py`, `tests/test_alert_dispatcher.py`, `tests/test_grid_monitor.py`, `tests/test_app_settings_api.py` — **88 passed**, 10 warnings. El harness de procesos afectado, `tests/test_loan_cohorts_process.py`, pasó aparte: **7 passed**.
- Suite final: `$env:ASPLE_OFFLINE='1'; .\venv\Scripts\python.exe -m pytest tests -m "not live" -q --basetemp=.pytest_tmp` — **1420 passed, 1 skipped, 18 deselected, 53 warnings**, pytest reportó **299.57 s**; cronómetro externo **302.35 s**; exit code **0**.
- `compileall` y `git diff --check`: correctos. No se usó Testnet ni una BD real; el núcleo se probó con exchange falso y precios deterministas.
- Durante el desarrollo, dos corridas intermedias tuvieron fallos de fixtures de lifespan que no definían el nuevo atributo de configuración; se adaptó la inicialización para tratar un atributo ausente como apagado. Una corrida posterior encontró un fallo de permiso Windows en un lock bajo `tmp_path`; el fixture ahora usa la ruta temporal compartida del mecanismo de lock y el archivo afectado pasó. La suite final completa quedó verde.

## Archivos de esta fase

`alerts/dispatcher.py`, `api/main.py`, `api/routes/metas.py`, `config/settings.py`, `database/db_manager.py`, `grid/engine.py`, `grid/meta.py`, `tests/test_app_settings_api.py`, `tests/test_loan_cohorts_process.py`, `tests/test_meta_core.py`, `tests/test_grid_monitor_policy.py`, `REPORTE_FASE40A.md` y `DISCREPANCIAS_FASE40A.md`.

Los cambios preexistentes en `frontend/grids.css` y `frontend/grids.js` se conservaron sin modificarlos. No se hizo commit ni despliegue.


## Revisión aprobada y correcciones de numeración y excedentes

- Se renombraron los documentos a `ESPEC_FASE40_MODO_META.md`, `REPORTE_FASE40A.md` y `DISCREPANCIAS_FASE40A.md`. El título de la especificación indica Fase 40 y aclara el orden: 40a núcleo, 40b operador y 40c pantalla. Se actualizaron las referencias antiguas de subfase en los documentos de esta fase; `REPORTE_FASE37.md` sigue perteneciendo al harness anterior.
- Protección de grids ligados a una meta en `SURPLUS_WAIT`: la consulta persistente `database/db_manager.py:1486-1499` busca la relación vigente y el estado de la meta. `grid/monitor.py:920-930` evita solicitar RESUME por `hysteresis_cleared`; `grid/engine.py:1772-1776` aplica la misma prohibición en el punto común usado por la API y cualquier otro llamador. La sincronización PAUSED existente conserva `rearm=False` (`grid/engine.py:1360-1390`), y el estado/vínculo persistidos mantienen el bloqueo al reiniciar.
- Prueba de ciclo más reinicio: `tests/test_grid_monitor_policy.py:71-107`, `test_fulfilled_meta_surplus_grid_stays_paused_through_resume_cycle_and_restart`. Volatilidad baja simula la condición que normalmente reanuda; verifica estado PAUSED, cero compras vivas y ausencia de evento GRID_RESUMED con dos instancias de monitor. Prueba enfocada: **8 passed en 1.62 s**.
- Suite completa offline: `$env:ASPLE_OFFLINE='1'; .\venv\Scripts\python.exe -m pytest tests -m "not live" -q --basetemp=.pytest_tmp` - **1421 passed, 1 skipped, 18 deselected, 53 warnings en 304.36 s**; cronómetro externo **307.17 s**; exit code **0**.
- `git diff --check`: correcto. Sin commit ni despliegue.
