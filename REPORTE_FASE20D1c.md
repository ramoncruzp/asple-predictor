# Fase 20D-1c — prueba de Battle

## Cambio realizado

Se añadió a test_battle_shows_direction_validation_and_models_link_without_live_volatility una respuesta simulada para /api/models/page-context con la forma de contexto ejercitada en tests/ui/test_battle.py: symbol, interval y models vacío. Se conservaron todas las aserciones y no se modificó código de producción.

Ubicación: tests/ui/test_grids_browser.py:824.

## SHA-256 y mutación

- Archivo antes de la edición: bf55c30aaf00fd29f9175c1db17017fe3b3f582d81218252747df0163adfbc27
- Archivo después de la edición: 116cad4eb375d33e570860059607a9a15c3d63ef4c6482926c984d04d8c997d8
- Mutación final, quitando el route: 116cad4eb375d33e570860059607a9a15c3d63ef4c6482926c984d04d8c997d8 antes 116cad4eb375d33e570860059607a9a15c3d63ef4c6482926c984d04d8c997d8; mutado bf55c30aaf00fd29f9175c1db17017fe3b3f582d81218252747df0163adfbc27; restaurado 116cad4eb375d33e570860059607a9a15c3d63ef4c6482926c984d04d8c997d8.
- Resultado literal de pytest con la mutación: FAILED tests/ui/test_grids_browser.py::test_battle_shows_direction_validation_and_models_link_without_live_volatility; 1 failed, 36 deselected, 2 warnings in 5.95s. Falló en la aserción "No validado", como consecuencia de que Battle no pudo cargar el contexto.
- NO VERIFICADO: la salida de pytest no imprimió el código HTTP 404; no se capturó el status directamente.

## Pruebas

Prueba enfocada (route restaurado): falló en la aserción preexistente del enlace. Resultado literal: 1 failed, 36 deselected, 2 warnings in 6.32s; esperaba 1 enlace #models y encontró 4.

Corrida UI final: .\venv\Scripts\python.exe -m pytest tests/ui -m "not live" -q --basetemp "$env:TEMP\pytest-20d1c-ui-final"

Resumen literal:

    ....... [ 91%]
    .......                                                                  [100%]
    FAILED tests/ui/test_grids_browser.py::test_battle_shows_direction_validation_and_models_link_without_live_volatility
    1 failed, 78 passed, 2 warnings in 46.24s

Fallo literal: AssertionError: Locator expected to have count '1'; Actual value: 4, en tests/ui/test_grids_browser.py:832. Las comprobaciones previas de contenido, rango y ausencia de elementos de volatilidad pasaron. battle.js genera enlaces a Modelos en las tarjetas; no cambió la aserción porque se pidió conservarla.

NO VERIFICADO: pruebas fuera de tests/ui. No se hicieron llamadas a Testnet, stage, commit ni push.
