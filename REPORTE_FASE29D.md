# Fase 29d: legibilidad del diólogo “Preparar close”

## Cambios

- En `frontend/grids.js:109`, cada opción conserva exactamente su etiqueta, `name="close-mode"`, `value` y radio predeterminado (`repository`). Se añadió una consecuencia en español debajo de cada etiqueta.
- Las reglas nuevas de `frontend/grids.css:89-181` están bajo `.grid-close-modes`. Los radios recuperan su ancho natural, no tienen borde ni fondo, usan acento azul y cada etiqueta ocupa el ancho disponible. La tarjeta elegida se resalta y lleva la marca “Seleccionado”; `liquidate` usa tinte rojo. El teclado recibe un contorno `:focus-visible` visible.
- Consecuencias contrastadas con el comportamiento real:
  - `liquidate`: vende el inventario retenido a mercado y contabiliza el resultado. `grid/engine.py:2723` inicia cancelación y `grid/engine.py:2759-2769` ejecuta la venta; la vista previa calcula resultado y comisión en `grid/control_service.py:163-170`.
  - `profit_repository`: selecciona ventas por ganancia neta estimada y lleva las celdas restantes al repositorio. El plan se prepara en `grid/engine.py:2677-2708`; las ventas de `sell_cells` se ejecutan en `grid/engine.py:2894-2911`, y la fase de repositorio en `grid/engine.py:3039-3041`. La vista previa separa `sell_cells` y `repo_cells` en `grid/control_service.py:171-192`.
  - `repository`: cancela compras pendientes (`grid/engine.py:2480-2504`), mueve las celdas con ventas activas al grid de repositorio (`grid/engine.py:2555-2577`, `grid/engine.py:2612-2634`) y deja fuera el inventario sin orden de venta viva.
  - `cancel`: cancela órdenes y devuelve inventario no gestionado, sin venderlo (`grid/engine.py:2712-2719`).

## Pruebas y capturas

- Comando focalizado: `$env:ASPLE_OFFLINE='1'; .\\venv\\Scripts\\python.exe -m pytest tests/ui/test_grids_browser.py tests/ui/test_grids_control_ui.py tests/ui/test_fase29d_close_modes.py tests/test_frontend_encoding.py tests/ui/test_frontend_syntax.py -q --basetemp=.pytest-29d-final2 --tb=short` ? **59 passed**, 2 avisos deprecados de dependencias.
- Las pruebas nuevas cambian cada radio, comparan el `getComputedStyle` de la tarjeta seleccionada con las no seleccionadas, cuentan una sola marca de selección y verifican que el radio mida como máximo 24 px. La prueba visual produce capturas tras el cambio.
- Mutación: se quitó temporalmente `.grid-close-modes label:has(input:checked)` de `frontend/grids.css`. La prueba `test_close_mode_cards_highlight_one_selected_radio_and_keep_radios_compact` falló por no cambiar el estilo de la tarjeta elegida. CSS se restauró byte por byte (SHA-256: `0c96375b42ed31462880f22a12e754503de64e737a8cff6cf94f9eab2e16b1be`).
- Capturas antes: `.pytest-29d-shots/before/dialog-1440x1000.png` y `.pytest-29d-shots/before/dialog-390x844.png`.
- Capturas después: `.pytest-29d-shots/after/dialog-1440x1000.png` y `.pytest-29d-shots/after/dialog-390x844.png`.

## Auditoría

- Las pruebas existentes de cierre pasaron; comparación directa confirma que sus cuatro textos de opción, `name`, valores y radio predeterminado no cambiaron.
- `git diff --check`: sin errores. Los archivos nuevos/modificados son UTF-8 sin BOM, con LF y sin sustituciones literales `?` en las palabras añadidas.
- Se revisó el diff: solo cambia el HTML del fieldset en `frontend/grids.js`; las reglas visuales están limitadas al ámbito `.grid-close-modes`; no hay cambios de producción fuera del alcance autorizado.
