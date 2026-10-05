# Fase 18F — Corrección de textos de Grids

## Cambios

- `frontend/grids.js:342`: el tooltip de `Esperado por ciclo, todas las celdas` ahora usa `te\u00f3rica`, `despu\u00e9s` y `predicci\u00f3n`. Revisé el diff completo de 18C; los ejemplos de `Curva de equity`, `Sin datos aún` y el diálogo de compuesto ya usan escapes Unicode o no contienen acentos corruptos. El barrido no encontró otros textos dañados.
- `tests/ui/test_grids_control_ui.py:22-27`: la prueba ahora exige el rótulo `Inter\u00e9s compuesto` y la nota fuente `El capital asignado no se puede cambiar mientras el grid est\u00e1 abierto.`; comprueba además que `Capital y compuesto` y `Ciérralo y abre uno nuevo` están ausentes. El control `compound` también se añade a la lista de acciones esperadas.
- `tests/test_frontend_encoding.py:8-11`: agregué dos patrones de ternario compactos de `scanner.js` a la lista estrecha de falsos positivos (`spacing ? number`, `lastPreview ? lastPreview`). La primera ejecución del test, después de corregir `grids.js`, reveló esos operadores; no son texto de usuario ni acentos corruptos.

## Verificación

| Comando | Resultado |
|---|---|
| `ASPLE_OFFLINE=1 .\venv\Scripts\python.exe -m pytest tests/test_frontend_encoding.py -p no:cacheprovider --basetemp "$env:TEMP\pytest-18f" -q` | 1 failed in 0.06s; señaló el ternario `spacing ? number` en `frontend/scanner.js`, un falso positivo. |
| `ASPLE_OFFLINE=1 .\venv\Scripts\python.exe -m pytest tests/test_frontend_encoding.py tests/ui/test_grids_control_ui.py -p no:cacheprovider --basetemp "$env:TEMP\pytest-18f" -q` | 4 passed in 0.40s. |
| `ASPLE_OFFLINE=1 .\venv\Scripts\python.exe -m pytest tests/ui/test_grids_browser.py -k "compound or detail" -p no:cacheprovider --basetemp "$env:TEMP\pytest-18f" -q` | 2 passed, 30 deselected, 2 warnings in 10.08s. Playwright bloqueó y registró `cdn.jsdelivr.net` y `fonts.googleapis.com`. |
| `node --check frontend/grids.js` | código 0, sin salida. |

Se usaron cuatro comandos de verificación en total. El temporal `$env:TEMP\pytest-18f` se eliminó.

## Bytes y límites

Los archivos editados y este informe están en UTF-8 sin BOM y sin CR. Las cadenas corregidas de JS usan escapes `\uXXXX`. No usé Binance/Testnet ni leí `.env`.

**NO VERIFICADO:** aspecto visual/manual en distintos tamaños o navegadores; las pruebas Playwright cubren comportamiento, no una aprobación visual.

No hice stage, commit ni push.
