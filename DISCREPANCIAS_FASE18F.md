# Fase 18F — Discrepancias

La prueba `test_grid_controls_show_non_supported_capital_and_compound_change` conservaba la expectativa previa a 18C: que editar compuesto no estuviera soportado. La UI actual sí permite editarlo en un grid abierto. La sustituí por `test_grid_controls_show_compound_editable_and_capital_note`, que exige el botón y la nota real `El capital asignado no se puede cambiar mientras el grid está abierto`, y verifica que ya no aparezcan `Capital y compuesto` ni `Ciérralo y abre uno nuevo`.

El barrido de `frontend/*.js` y `frontend/index.html` no encontró más tildes dañadas. Para que la prueba de codificación no trate dos ternarios compactos de `scanner.js` como texto corrupto, se añadieron únicamente esos patrones exactos a su whitelist; la búsqueda de texto de usuario permanece activa.

**NO VERIFICADO:** revisión visual manual. No se usé Binance/Testnet ni se leyó `.env`; no se hizo stage, commit ni push.
