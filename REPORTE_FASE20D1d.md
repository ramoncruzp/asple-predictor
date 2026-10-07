# Fase 20D-1d — aserciones de enlaces de Battle

## Cambio

Se reemplazó la aserción amplia del total de enlaces por dos verificaciones específicas en tests/ui/test_grids_browser.py:832:

- Las tarjetas dentro de #battle-content tienen tantos enlaces como modelos recibidos en el fixture y cada enlace dice «Ver estado y detalle en Modelos».
- El enlace del encabezado se localiza con #screen-battle > p.card.muted a[href="#models"], correspondiente al párrafo de frontend/index.html:23, y aparece una vez fuera de #battle-content.

El enlace de cada tarjeta se confirmó en frontend/battle.js:22. No se cambió el mock de page-context ni ninguna otra aserción.

## SHA-256

- Antes de editar la aserción: 116cad4eb375d33e570860059607a9a15c3d63ef4c6482926c984d04d8c997d8
- Después de editar: b4f6f0bd2a0e047eddd35e38d4369cdae1243f0f4ca153e3e4c919a4359e04d2
- Mutación (i), mock devuelve dos modelos y expectativa intacta: antes b4f6f0bd2a0e047eddd35e38d4369cdae1243f0f4ca153e3e4c919a4359e04d2; mutado 382035e7c096595dd638e42574c6a1d6b8f0fe692eb616a4bb29dabb6ee856e8; restaurado b4f6f0bd2a0e047eddd35e38d4369cdae1243f0f4ca153e3e4c919a4359e04d2.
- Mutación (ii), se quita el mock page-context: antes b4f6f0bd2a0e047eddd35e38d4369cdae1243f0f4ca153e3e4c919a4359e04d2; mutado d17f3ec6738cfdb4dc2034ed5f24048bc4c0b99f2f3300615a4e64106aff7385; restaurado b4f6f0bd2a0e047eddd35e38d4369cdae1243f0f4ca153e3e4c919a4359e04d2.
- Ambas mutaciones restauraron el archivo byte a byte al SHA posterior a editar.

Resultado literal de la mutación (i): «Locator expected to have count '3'». Falló en el conteo de enlaces de tarjetas, como se esperaba.

Resultado literal de la mutación (ii): «Actual value: No se pudo cargar Battle de Modelos: HTTP 404. Puedes reintentar cuando el servicio responda.»

## Pruebas

Prueba enfocada: 1 passed, 36 deselected, 2 warnings in 1.03s.

Comando de la suite UI final: .\venv\Scripts\python.exe -m pytest tests/ui -m "not live" -q --basetemp "$env:TEMP\pytest-20d1d-ui"

Resultado literal: 79 passed, 2 warnings in 32.52s.

No se ejecutaron pruebas marcadas live. Sin stage, commit ni push.
