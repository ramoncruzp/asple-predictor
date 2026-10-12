# Reporte Fase 31 — Laboratorio de reglas experimentales

## A. Ruta y navegación — HECHO

- `frontend/index.html:19` incorpora Laboratorio a la navegación de escritorio y al menú móvil; el enlace usa `data-route`/`data-mobile-route` y conserva las rutas existentes. `frontend/app.js:386` registra `lab` y llama a `loadLabPage`. La prueba `tests/ui/test_laboratorio.py:124` verifica las nueve rutas móviles; `:53` verifica la ruta activa.
- `frontend/index.html:29` contiene el aviso fijo requerido, el área de estadísticas, el listado global y la nota de método.
- `frontend/lab.css:1` presenta tarjetas, distintivo de valor por defecto y distribución móvil.

## B. Interruptores globales — HECHO

- `frontend/global_flags.js:5-31` comparte la lectura y el renderizado entre Cuenta y Laboratorio. Escapa los valores de la API, enumera todas las flags devueltas, muestra estado, descripción, origen, valor por defecto y efecto/reinicio; marca las que difieren del default.
- `frontend/global_flags.js:38-70` realiza primero `dry_run=true`, enseña el plan en el diálogo y aplica después con `confirm=true` solo al encender. Los errores HTTP se muestran en el estado accesible y no desmontan la página. La respuesta `updated_at` se muestra en hora local.
- `frontend/cuenta.js:73,82-84` conserva la sección de Ajustes globales y delega el flujo al mismo helper. `tests/ui/test_cuenta_settings.py:4-54` verifica Cuenta y su orden de previsualización/aplicación.
- `tests/ui/test_laboratorio.py:53-98` verifica flags dinámicas (incluida `stoploss_enabled` y una futura), insignia de default, flujo de encendido/apagado, valor de confirmación, fecha y error 422 con la página utilizable.

## C. Estadística del stop-loss — HECHO PARCIAL

- `frontend/lab.js:16-33` consulta `/api/grids` para los estados existentes y `/api/grids/{id}/events` por grid. Cuenta `CELL_STOPLOSS` y `STOPLOSS_SUSPENDED` de los últimos siete días. Limita el cálculo a 50 grids y 500 eventos recientes por grid; la interfaz comunica ese límite. `tests/ui/test_laboratorio.py:53-80,111-122` cubre los recuentos de fixture y la degradación a “Sin datos” ante error.
- **NO HECHO: suma neta.** `api/routes/grid_status.py` devuelve `events_view`, que expone tipo y fecha, pero no el payload `details` que contiene el neto. No se modificó backend; la página indica que el neto no está disponible en ese endpoint.

## D. Método — HECHO

- `frontend/index.html:29` incluye el `<details id="lab-method" class="lab-method">` plegado con las cuatro reglas fijas solicitadas.

## Verificación

- `node --check` pasó en cada archivo de `frontend/*.js`: `app.js`, `battle.js`, `cuenta.js`, `global_flags.js`, `grids.js`, `lab.js`, `models.js` y `scanner.js`. En PowerShell, la invocación literal `node --check frontend/*.js` entregó el comodín sin expandir a Node; se comprobó cada archivo individualmente.
- Pruebas enfocadas: `$env:ASPLE_OFFLINE="1"; .\venv\Scripts\python.exe -m pytest tests/ui/test_laboratorio.py tests/ui/test_frontend_syntax.py tests/test_frontend_encoding.py tests/test_app_settings_api.py tests/ui/test_cuenta_settings.py tests/ui/test_grids_browser.py -q --basetemp=.pytest-31-focus` — **66 passed, 0 failed, 11 warnings**.
- Mutación de comportamiento: se sustituyó temporalmente la llamada de previsualización `dry_run=true` por un plan local sin petición. `test_lab_route_dynamic_flags_badges_stats_and_enable_disable_flow` falló en la aserción de secuencia de llamadas (faltaba la previsualización); la mutación se restauró byte por byte. SHA-256 antes: `e8a5592e991806c032497540841af6d45311349c3152db5e5ddc34551cff5c1b`; mutado: `8f871505273864510f8c0a7912fa69c5a17bd1595ad9798c43abc60144d51620`; restaurado: `e8a5592e991806c032497540841af6d45311349c3152db5e5ddc34551cff5c1b`.
- Capturas de fixture (sin credenciales ni datos reales), viewports exactos: `.pytest-31-shots/desktop-before-cuenta.png` y `desktop-after-laboratorio.png` (1440×1000); `mobile-before-cuenta.png` y `mobile-after-laboratorio.png` (390×844). En Laboratorio aparecen los dos contadores y las tarjetas de flags; no queda el estado de carga.
- Auditoría de los archivos modificados: `git diff --check` limpio; sin CR/CRLF, BOM, U+FFFD, `?` espurios ni secretos detectados. Las capturas y directorios `.pytest-*` no forman parte del commit.

## No verificado

- La suite completa no se ejecutó; corresponde a Ramón.
- Los recuentos son datos de fixture, no evidencia de S1 ni de ejecución real.
- La vista móvil se inspeccionó en navegador de escritorio con viewport 390×844; no en un dispositivo físico.

## Archivos incluidos

- `frontend/app.js`
- `frontend/cuenta.js`
- `frontend/index.html`
- `frontend/global_flags.js`
- `frontend/lab.js`
- `frontend/lab.css`
- `tests/ui/test_cuenta_settings.py`
- `tests/ui/test_laboratorio.py`
- `REPORTE_FASE31.md`
