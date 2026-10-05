# Fase 18A — pruebas de UI con Playwright

## Alcance y entorno

Se añadieron pruebas de navegador real para la UI de grids y cuenta, sin modificar archivos de producción (`grid/`, `api/`, `frontend/`). La app de prueba corre en loopback con puerto dinámico, SQLite temporal y `FakeExchange`; las rutas externas se bloquean antes de navegar y se registran. Los hosts observados y bloqueados fueron `cdn.jsdelivr.net` y `fonts.googleapis.com`.

Entorno instalado para esta fase: Playwright 1.63.0, pytest-playwright 0.9.0 y Chromium revisión 1243 (FileVersion 153.0.8010.12). El repositorio tiene `requirements.txt`, pero no un archivo de dependencias de desarrollo; no se modificaron dependencias del proyecto. No se usó Binance/Testnet real ni se accedió a `.env`.

## Cambios

- [tests/ui/conftest.py](tests/ui/conftest.py#L32): fixtures del servidor temporal, navegador Chromium, bloqueo/registro de hosts externos, captura de errores de consola y screenshot de diagnóstico bajo `tmp_path`.
- [tests/ui/test_grids_browser.py](tests/ui/test_grids_browser.py#L27): nueve escenarios de navegador real: orden de opciones; liquidación con confirmación escrita y preview previo; signo visual de ganancia/pérdida; preview de repositorio y comisión; cancelar/Escape; autenticación 403 y reintento; escape HTML; pantalla de cuenta y refresco; error 500; navegación entre grids, scanner y cuenta sin errores JS. La prueba de liquidación parametrizada cubre dos casos.
- [tests/ui/test_grids_ui.py](tests/ui/test_grids_ui.py#L6): se actualizó una expectativa obsoleta de la fase 17B: el botón de pausa en un grid ACTIVE está habilitado cuando no hay diálogo activo. El segundo escenario mantiene la comprobación de estados vacío/error.

## Verificación

Comando enfocado: `ASPLE_OFFLINE=1 .\venv\Scripts\python.exe -m pytest tests/ui -v -rs -m "not live" --basetemp .pytest-18a-ui-main` — **17 passed, 0 skipped**, 2 warnings, 37.11 s. El registro final indicó `cdn.jsdelivr.net` y `fonts.googleapis.com` bloqueados y ningún defecto UI observado.

Tres ejecuciones consecutivas adicionales de `tests/ui` terminaron cada una con **17 passed**: 39.08 s, 37.83 s y 38.35 s. No hubo fallos intermitentes.

Suite offline completa: `ASPLE_OFFLINE=1 .\venv\Scripts\python.exe -m pytest -q -rs -m "not live" --basetemp tests/.pytest-18a-offline` — **733 passed, 18 deselected, 0 skipped, 9 warnings**, 153.97 s. Los warnings corresponden a dos avisos de colección de `TestnetOrderError`, advertencias existentes de XGBoost/PyTorch y dos avisos deprecados de websockets/Uvicorn.

Mutaciones temporales en `frontend/grids.js`, restauradas después de cada ejecución y comprobadas sin diff de producción:

| Mutación | Prueba que la detectó | Resultado |
|---|---|---|
| Retirar la confirmación escrita `LIQUIDAR` | `test_liquidate_requires_typed_confirmation_and_posts_preview_first[ganancia]` | Muerta |
| Invertir el orden de opciones de liquidación/repositorio | `test_close_dialog_options_are_visible_in_required_order` | Muerta |
| Fijar siempre la etiqueta `GANANCIA`, incluso con resultado negativo | `test_liquidate_requires_typed_confirmation_and_posts_preview_first[perdida]` | Muerta |

Resultado de mutación: **3/3 detectadas**. `git --no-pager --no-optional-locks diff --stat -- frontend/` no reportó cambios de producción.

## Límites

La evidencia cubre Chromium headless, la app local y las respuestas de los routers/fakes definidos en estas pruebas. No acredita comportamiento en otros motores de navegador, redes externas, Testnet/Binance, despliegue remoto ni latencia real. No se encontró un defecto UI que requiriera cambio de producción.

No se hizo stage ni commit. Se preservaron los archivos ajenos que ya estaban sin seguimiento en el checkout.
