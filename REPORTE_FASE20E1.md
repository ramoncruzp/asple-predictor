# Fase 20E-1 — monedas no-XRP en Monedas, Modelos y Grid Advisor

## Preflight y límites

- HEAD de referencia: `f4fdb009f22442345848e5d001ba3d7f46657874`, rama `main`. El árbol estaba limpio antes de las ediciones iniciales; al reanudar ya había cambios parciales de esta misma tarea en archivos permitidos, que preservé.
- `ASPLE_OFFLINE=1`. Sin Binance/Testnet ni lectura de `.env`; sin stage, commit ni push.
- Línea base: `pytest tests -m "not live" --ignore=tests/ui -q --basetemp .pytest_tmp/20e1-baseline` → `934 passed, 1 skipped, 18 deselected, 37 warnings in 68.42s` (cero fallos).

## Cambios A-I

- **A — HECHO.** Campeón por símbolo y consenso provisional. Evidencia: `api/routes/coins.py:203`; `tests/test_coins_registry.py:1` (prueba `test_ada_champion_uses_consensus_without_battle_rows`).
- **B — HECHO.** Distingue falta de pronóstico (503), ausencia de widening no-XRP (404) y error de red. Evidencia: `frontend/models.js:418`; `tests/ui/test_models_page.py:618`.
- **C — HECHO.** El endpoint y la UI exponen el estado TEST por símbolo. Evidencia: `api/routes/volatility.py:348`; `frontend/models.js:420`.
- **D — HECHO.** Entrenamiento y contexto muestran símbolo/horizonte; los modelos de dirección se limitan a XRPUSDT. Evidencia: `frontend/models.js:201`; `frontend/index.html:24`.
- **E — HECHO.** Aviso de selección centralizado y sin duplicados. Evidencia: `frontend/models.js:360`; `tests/ui/test_models_page.py:499`.
- **F — HECHO.** No consulta widening XRP para otra moneda y presenta los campos como no calculados. Evidencia: `api/routes/grid_advisor.py:208`; `frontend/app.js:280`.
- **G ? PARCIAL EN 20E-1; CERRADO EN 20E-1b.** El 20E-1 reinici? el ?ndice; el 20E-1b normaliza el timestamp a segundos Unix. Evidencia: `api/routes/grid_advisor.py:271`; `tests/test_grid_advisor.py:334`.
- **H — HECHO.** La advertencia de posición solo se muestra con contenido. Evidencia: `frontend/app.js:280`; `tests/ui/test_grids_browser.py:1` (prueba `scanner-warning:empty`).
- **I — HECHO, YA ESTABA CABLEADO.** El bucle de volatilidad consulta símbolos listos y guarda pronósticos. Evidencia: `scheduler/vol_loop.py:107`; `tests/test_vol_per_symbol.py:279`.

## Mutaciones

| Punto | SHA-256 antes | SHA-256 mutado | SHA-256 restaurado |
|---|---|---|---|
| A | `b0fafdde9bf6abb064051f479451165f89edfe35ab18c2e4e07418769f9c6564` | `fd539ea818eaced3f8e44b82182279918aa391137a79d528e4b36e1beb405309` | `b0fafdde9bf6abb064051f479451165f89edfe35ab18c2e4e07418769f9c6564` (igual; detectada=True) |
| B | `c8ea161053a45cd993d6b70fab47aa74e9f61b2d3b1017471f867a1cb38730f9` | `72068fa8dfe58a90073f2985cc6b1f7548d20f09cd558d6289ed4879f8050621` | `c8ea161053a45cd993d6b70fab47aa74e9f61b2d3b1017471f867a1cb38730f9` (igual; detectada=True) |
| C | `c8ea161053a45cd993d6b70fab47aa74e9f61b2d3b1017471f867a1cb38730f9` | `bb3c42ae31f4d369779ece5cd1cb33c9a15321042836b67c955611361ccb5c7a` | `c8ea161053a45cd993d6b70fab47aa74e9f61b2d3b1017471f867a1cb38730f9` (igual; detectada=True) |
| F | `d8637f8cebabef1b169737bd0fcabe38c4fab25e5a903e1673b7e6853e5790ce` | `cb9ddcd6ae44609a69ed311ca622d0e2bcba78f2d3517f451ba10959fff8f915` | `d8637f8cebabef1b169737bd0fcabe38c4fab25e5a903e1673b7e6853e5790ce` (igual; detectada=True) |
| G | `d8637f8cebabef1b169737bd0fcabe38c4fab25e5a903e1673b7e6853e5790ce` | `58b1b8965301769fc2f09af298a2f4fc1a330c8ed9830b4b572db862f8326205` | `d8637f8cebabef1b169737bd0fcabe38c4fab25e5a903e1673b7e6853e5790ce` (igual; detectada=True) |

A volvió a consultar Battle; B anuló la rama 503; C mostró ADA virgen como no virgen; F activó widening XRP para ADA; G retiró reset de índice. Cada prueba falló con la mutación y los archivos volvieron a su SHA original.

## Resultados literales

No-UI:
```powershell
.\venv\Scripts\python.exe -m pytest tests -m "not live" --ignore=tests/ui -q --basetemp .pytest_tmp/20e1-final3-no-ui
```
```text
938 passed, 1 skipped, 18 deselected, 37 warnings in 95.74s (0:01:35)
```

UI:
```powershell
.\venv\Scripts\python.exe -m pytest tests/ui -m "not live" -q --basetemp .pytest_tmp/20e1-final3-ui
```
```text
101 passed, 2 warnings in 74.07s (0:01:14)
```

Comparación: baseline 934 pasadas y 0 fallos; la corrida final de 20E-1 tuvo 938 pasadas y 0 fallos no-UI, 101 pasadas y 0 fallos UI. Una corrida UI anterior usó una expectativa obsoleta; se actualizó y la corrida final pasó.

## Codificación y verificación pendiente

Escaneo UTF-8/BOM/CRLF/U+FFFD y `[A-Za-z]\?[a-z]` sobre archivos modificados. No se leyeron ni modificaron velas cache. **NO VERIFICADO:** historia real ADA de 46,8 días ni acceso real fuera de TestClient; no hubo red ni consulta a Binance.

Escaneo: CRLF=0, BOM=0, U+FFFD=0, regex `[A-Za-z]\?[a-z]`=1.
- Coincidencia: `tests/test_coins_registry.py:282:status, data = get(app, "/api/coins?include_inactive=true")` (query string).

Archivos con cambios al cierre:
- ` M api/routes/coins.py`
- ` M api/routes/grid_advisor.py`
- ` M api/routes/volatility.py`
- ` M frontend/app.js`
- ` M frontend/index.html`
- ` M frontend/models.js`
- ` M tests/test_coins_registry.py`
- ` M tests/test_grid_advisor.py`
- ` M tests/test_model_stats_api_cycle.py`
- ` M tests/test_vol_per_symbol.py`
- ` M tests/ui/test_grids_browser.py`
- ` M tests/ui/test_models_page.py`
- `?? REPORTE_FASE20E1.md`

`git diff --cached --name-only`: vacío
