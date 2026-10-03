# Fase 17C-2 — cierre de tres pendientes

## Cambios

1. **Reapertura de la vista previa tras abrir.** En `frontend/scanner.js:39`, el éxito muestra “Cerrar”. El handler oculta el diálogo, pone `dialogOpen=false`, rehabilita “Vista previa” y actualiza el aviso de grid existente. El `finally` conserva el botón deshabilitado mientras el diálogo siga activo. “Cancelar” limpia ese estado; el resultado parcial deja el modal abierto, muestra el aviso y rehabilita la confirmación para reintentar; los errores hacen lo mismo con el mensaje local. La prueba Node cubre cerrar y comenzar una segunda apertura, además de cancelar tras parcial/error.

2. **Comisión en filas no elegibles.** `api/routes/grids.py:94-102` añade `fee_pct` a cada fila del resultado de scan, tomado de la configuración del servicio que calculó el score y usando el fallback de settings existente. Copia las filas al enriquecer la respuesta; no modifica score, elegibilidad ni pesos. En `frontend/scanner.js:40`, el margen bruto usa `spacing_pct - 2 * fee_pct`; si falta comisión o separación, muestra “no disponible”. El test Node cubre una fila no elegible con comisión (bruto 0.500%) y sin comisión (no disponible), y el test Python verifica que el endpoint expone la fee sin cambiar score/eligibilidad.

3. **Pruebas de comportamiento con Node.** `tests/test_frontend_scanner.py:47-150` implementa un stub DOM pequeño y ejecuta el archivo real `frontend/scanner.js` en Node VM, sin dependencias nuevas. Casos: orden dry-run antes de confirmación y campos explícitos; cierre exitoso y segunda apertura; parcial/error nunca anuncia “Grid abierto” y cancelar restablece el estado; fee presente/ausente; escape de un símbolo `<script>`. El test de la ruta de scan está en `:188-199`. Si Node no está instalado, los casos conductuales hacen skip con motivo.

## Verificación

- Mutaciones temporales requeridas: quitar reset de `dialogOpen`, omitir `fee_pct`, saltar dry-run previo y quitar `esc`. **4/4 murieron**; cada archivo fue restaurado y comparado byte por byte con su respaldo.
- Tests enfocados: **22 passed**.
- `node --check frontend/scanner.js`: correcto.
- Suite offline con `ASPLE_OFFLINE=1` y `-m "not live"`: **663 passed, 1 skipped, 18 deselected, 7 warnings en 43.25 s**. Son seis tests passed más que la base de 657.
- También ejecuté `python -m pytest -q` con `ASPLE_OFFLINE=1`, como se pidió. Resultado: **663 passed, 2 skipped, 17 failed en 52.83 s**. Los 17 fallos fueron pruebas de red pública/Testnet que intentaron sockets y recibieron `PermissionError/WinError 10013`; no hubo conexión completada. Por la restricción de no usar esos servicios, la verificación completa válida es la corrida excluyendo `live`, reportada arriba.
- Pruebas de UI realizadas en Node con DOM stub; no prueban layout, foco real, accesibilidad en navegador ni integración con un servidor activo. No se hicieron llamadas exitosas a Binance/Testnet ni aperturas reales.
- No se modificaron pesos/umbrales, no se stageó ni se hizo commit/push. Se conservaron los demás archivos existentes y el ruido de CRLF sin tocarlos.

## Archivos de esta corrección

- `frontend/scanner.js`: cierre de modal, estado de botones y lectura de comisión.
- `api/routes/grids.py`: campo `fee_pct` por resultado.
- `tests/test_frontend_scanner.py`: pruebas Node y de la respuesta de scan.
- `REPORTE_FASE17C_2.md`: este informe.
