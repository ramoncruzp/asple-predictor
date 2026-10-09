# Fase 24b: arreglo del acuse en Scanner

Preflight: HEAD `4f83150`, sobre `836ff9e`; árbol limpio. Se trabajó solo en los tres archivos autorizados. No hubo stage, commit ni push.

| Punto | Estado | Evidencia |
|---|---|---|
| 1. Selección programática | HECHO | `frontend/scanner.js:134` define `setSymbol` para asignar la moneda y llamar a `renderUnreadyAck`; `:240` lo usa el botón "Usar" y `:242` el prefill `advisorDraft`. |
| 2. Pista de acuse | HECHO | `frontend/scanner.js:105` añade la pista junto a "Vista previa"; `:119` la muestra solo si la moneda requiere acuse y la casilla no está marcada. |
| 3. Pruebas UI | HECHO | `tests/ui/test_grids_browser.py:904`, `:928` y `:948` cubren "Usar" con GRAM, prefill del Advisor y "Usar" con moneda lista. La prueba existente de gating queda en `:962`. |

## Verificación

La primera corrida sin `--basetemp` no llegó a ejecutar las pruebas: pytest recibió `PermissionError: [WinError 5]` al enumerar `C:\Users\ramon\AppData\Local\Temp\pytest-of-ramon`. Repetí el mismo alcance en un directorio temporal nuevo dentro del repositorio.

Rojo antes del cambio: `pytest tests/ui/test_grids_browser.py -k "scanner_use_symbol_shows_ack_hint or scanner_advisor_prefill_shows_ack_hint" -q --tb=short --basetemp .pytest_tmp/pytest-24b-red2-20261009` dio `2 failed, 48 deselected`. Ambos fallos indicaron que `#sc-unready-ack` no existía después de la selección por "Usar" y Advisor.

Verde dirigido, incluye las tres pruebas nuevas, el selector y gating existentes, e `idle_shrink_button_states`:

```text
.\venv\Scripts\python.exe -m pytest tests/ui/test_grids_browser.py -k "scanner_use_symbol_shows_ack_hint or scanner_advisor_prefill_shows_ack_hint or scanner_use_ready_symbol_does_not_show_ack or scanner_disables_unready_coin_only_in_create_selector or scanner_data_insufficient_ack_gates_preview_and_confirmation or idle_shrink_button_states" -q --tb=short --basetemp .pytest_tmp/pytest-24b-green-20261009
6 passed, 44 deselected, 2 warnings in 6.78s
```

Mutación: cambié temporalmente el manejador de "Usar" para asignar el valor sin `setSymbol`. La prueba de "Usar" falló en la aserción de visibilidad de la casilla (`1 failed, 49 deselected`). Restauración byte a byte confirmada:

| Estado | SHA-256 de `frontend/scanner.js` |
|---|---|
| Antes | `51a804a86d2b4c62c22257ad94e4dfdaebf0b3e75d5bfaa1299daf986c990267` |
| Mutado | `37de9adc1239042aee240bac6b71a1c302f0ce7ec3bddc44949bc62ee4be3383` |
| Restaurado | `51a804a86d2b4c62c22257ad94e4dfdaebf0b3e75d5bfaa1299daf986c990267` |

NO VERIFICADO: comportamiento en el navegador real; Playwright usó respuestas simuladas.

Archivos para el commit posterior: `frontend/scanner.js`, `tests/ui/test_grids_browser.py`, `REPORTE_FASE24B.md`.
