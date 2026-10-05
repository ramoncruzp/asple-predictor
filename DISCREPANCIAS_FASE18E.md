# Discrepancias ? Fase 18E

La prueba de contrato usa la alternativa prevista en el prompt: el harness Node valida las claves reales de ambos cuerpos enviados y la API confirma que `margin_target_pct` continúa rechazado; el body Node no se reenvía directamente al cliente ASGI.
`margin_target_pct` y `spacing_pct` siguen siendo parte de `/structure-preview`; la whitelist solo afecta a `/api/grids/open`.
No se modificaron `OpenRequest`, `engine.py`, `monitor.py`, `policy.py` ni `grid/levels.py`. La apertura real en Testnet no se verificó.
