# Reporte Fase 30: interruptor global del stop-loss por celda

## A. Interruptor global

**HECHO.** Se añadió `Settings.stoploss_enabled`, con `STOPLOSS_ENABLED` y valor predeterminado `True` (`config/settings.py:47`). `APP_FLAG_DEFINITIONS` expone “Stop-loss por celda”, explica que la regla vende a mercado por debajo del umbral configurado (5 % por defecto), que al apagarla la posición espera su venta normal y la pérdida no realizada puede crecer, que no altera otras protecciones, y que el cambio se aplica en el próximo ciclo sin reiniciar (`config/settings.py:112-123`). La persistencia usa sin cambios `apply_app_flag_overrides` (`config/settings.py:128-133`).

**Compatibilidad verificada.** La ruta de flags enumera las definiciones y sus valores actuales (`api/routes/app_settings.py:20-35`); el POST permite apagar sin `confirm` y exige confirmación para encender (`api/routes/app_settings.py:38-62`). Cuenta dibuja cada fila y el botón según el valor actual (`frontend/cuenta.js:75-105`). No hizo falta modificar API ni frontend. La prueba confirma apagado sin confirmación, rechazo al encender sin confirmación, aceptación al confirmar y persistencia del apagado tras construir Settings nuevo y reaplicar el override (`tests/test_app_settings_api.py:152-185)).

## B. Monitor y evento

**HECHO.** Con la protección habilitada se conserva la ruta existente que llama una vez a `stoploss_cell` por candidata. Con `stoploss_enabled=False`, no se llama a esa acción y se emite `STOPLOSS_SUSPENDED` con razón `interruptor_apagado` y los detalles solicitados: nivel, precio de entrada, porcentaje, precio actual y `would_have_sold_at` (`grid/monitor.py:488-535`). La deduplicación en memoria usa la pareja (grid, nivel), se limpia cuando la candidata deja de cruzar el umbral y permite un nuevo evento al volver a cruzarlo (`grid/monitor.py:61,488-535`). Como el estado es en memoria, un reinicio del proceso puede repetir una vez el evento, tal como permite el prompt.

**Inventario de disparadores.** `git grep -n -E 'stoploss_cell|stoploss_candidates' -- grid` encontró: `grid/monitor.py:488,497`, donde el monitor obtiene candidatas y ejecuta `self.engine.stoploss_cell`; `grid/engine.py:1775`, definición de la acción; `grid/policy.py:682`, definición de la selección; y `grid/sim/runner.py:172`, evaluación de candidatas dentro del simulador. La única llamada operativa del monitor a `stoploss_cell` está en `grid/monitor.py:497`; el simulador es un recorrido de simulación separado. La implementación de `grid/policy.py` y `grid/engine.py` no se modificó.

## C. Etiqueta

**HECHO.** Se añadió la etiqueta “Stop-loss omitido (interruptor apagado)” en `grid/status_view.py:53`. No se añadió el evento a `SEVERITY_BY_EVENT`; `events_view` conserva su severidad predeterminada `info`, comprobada en `tests/test_grid_status_view.py:21-32`.

## Pruebas y mutaciones

La búsqueda `git grep -ln -E 'stoploss|app_flag|APP_FLAG_DEFINITIONS|settings/flags' -- tests` encontró exactamente: `tests/sim_replay_adapter.py`, `tests/test_app_settings_api.py`, `tests/test_grid_compound_engine.py`, `tests/test_grid_ctl_open.py`, `tests/test_grid_loans_engine.py`, `tests/test_grid_monitor.py`, `tests/test_grid_policy.py`, `tests/test_grid_policy_live.py`, `tests/test_grid_sim_smart_fidelity.py`, `tests/test_grid_status_api.py`, `tests/test_grid_status_view.py`, `tests/test_grid_stoploss.py` y `tests/ui/test_cuenta_settings.py`.

**Pruebas enfocadas ejecutadas** con `ASPLE_OFFLINE=1` y `--basetemp=.pytest-30-focus`:

1. Antes de corregir las dos expectativas señaladas, los siete archivos enumerados a continuación dieron **114 passed, 2 failed, 11 warnings**. Los fallos fueron únicamente las aserciones de texto y severidad descritas abajo.
2. Después de corregirlas, el mismo comando y los mismos archivos dieron **116 passed, 0 failed, 11 warnings** en 7.28 s:
   - `tests/test_stoploss_global_flag.py`
   - `tests/test_app_settings_api.py`
   - `tests/test_grid_monitor.py`
   - `tests/test_grid_policy.py`
   - `tests/test_grid_stoploss.py`
   - `tests/test_grid_status_view.py`
   - `tests/ui/test_cuenta_settings.py`
3. Mutación de la condición del monitor, sustituyendo `if stoploss_enabled:` por `if True:`: `tests/test_stoploss_global_flag.py::test_disabled_stoploss_is_suspended_once_per_episode` falló como se esperaba al observar llamadas a `stoploss_cell` y no el evento suspendido. SHA-256: antes y restaurado `3a08263451bd10d0cf321ea88e84d88ea4b0211211680f6cb9e44b0443d71357`; mutado `be2dd5d410b7f5aadf76ce5370209483961cf781097108aedb83373337861470`.
4. Mutación del valor predeterminado de Settings de `True` a `False`: `tests/test_app_settings_api.py::test_get_lists_allowlisted_flag_with_default_origin` falló como se esperaba porque `current` dejó de ser `True`. SHA-256: antes y restaurado `0a50468d9a555d6e1df3d90369a969e8de830b2a5fb0efecdfcfc82e1a3858a3`; mutado `816a1ca0c33f554f201d1d4d1de42c3f989cd52e4654f362591d20ec1c4ddcb7`. En ambos casos la restauración fue byte idéntica.

**Correcciones de aserciones solicitadas:**

- `tests/test_app_settings_api.py:55`. Antes: `"la regla no se ejecuta"`. Después: `"esa regla no se ejecuta"`, que coincide con el texto real de `config/settings.py`.
- `tests/test_grid_status_view.py:32`. Antes: severidad esperada `"warning"`. Después: `"info"`, el valor predeterminado de `events_view`. No se tocó `SEVERITY_BY_EVENT`.

## Auditoría y límites

`git diff --check` pasó. Los archivos de texto modificados están en UTF-8 sin BOM, con LF y sin U+FFFD; la inspección de las líneas añadidas no encontró signos de interrogación espurios ni patrones de secretos. Los únicos archivos de producción modificados son `config/settings.py`, `grid/monitor.py` y `grid/status_view.py`; las pruebas y este reporte completan el alcance autorizado.

**NO VERIFICADO:** no se ejecutó la suite completa (la ejecuta Ramón); no se probó contra Binance real ni con S1. En ejecución, apagar el interruptor evita la venta por stop-loss desde el siguiente ciclo del monitor; las posiciones permanecen expuestas a pérdidas no realizadas que pueden crecer.

## Archivos de la fase

- `config/settings.py`
- `grid/monitor.py`
- `grid/status_view.py`
- `tests/test_stoploss_global_flag.py`
- `tests/test_app_settings_api.py`
- `tests/test_grid_status_view.py`
- `tests/test_grid_monitor.py` (cubierto por la prueba enfocada; sin cambios)
- `tests/test_grid_policy.py` (cubierto por la prueba enfocada; sin cambios)
- `tests/test_grid_stoploss.py` (cubierto por la prueba enfocada; sin cambios)
- `tests/ui/test_cuenta_settings.py`
- `REPORTE_FASE30.md`
