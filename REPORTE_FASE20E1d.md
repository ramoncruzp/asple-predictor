# Fase 20E-1d — métricas de simulación del Advisor

## H1

- `api/routes/grid_advisor.py:356`: `recommend` devuelve ahora `capital` con el importe pedido; se conservan las demás claves.
- `frontend/app.js:271-276`: `simCard` calcula equity como capital + P&L neto, y presenta P&L neto, drawdown máximo, comisiones, comprar y mantener y ciclos. Los campos ausentes o no numéricos muestran «—» por separado; el equity no presenta `NaN`. Se conserva el mensaje de error y el aviso de historia disponible.
- `tests/test_grid_advisor.py:56` afirma que el endpoint conserva el capital pedido. `tests/ui/test_grids_browser.py:106` y el fixture de simulación verifican equity numérico y las cuatro métricas con nombres reales de `grid/sim/metrics.py`.

## Verificación

- Pruebas dirigidas: `2 passed, 2 warnings in 4.97s`.
- Mutación única: al quitar la clave `capital`, falló `test_recommendation_uses_editable_margin_and_named_risk_limits` con `KeyError: 'capital'`; restauración byte por byte confirmada (`RESTORE_BYTE_IDENTICAL=True`).
- Suite no UI inicial: `1 failed, 938 passed, 1 skipped, 18 deselected, 38 warnings in 184.59s (0:03:04)`. Falló `tests/test_frontend_encoding.py::test_frontend_user_text_has_no_question_mark_mojibake`: detectó el signo `?` de la expresión ternaria nueva de equity. Se sustituyó por una asignación condicional equivalente.
- Verificación posterior de esa corrección: `1 passed in 0.05s` (`tests/test_frontend_encoding.py`). NO VERIFICADO: repetir la suite no UI completa después de esta corrección; por eso no certifico aún el criterio 0 fallos frente a la referencia 939/1.
- Suite UI: `102 passed, 2 warnings in 104.40s (0:01:44)`; Playwright no observó defectos.

## Higiene de archivos

Revisión final en los cinco archivos tocados: UTF-8 sin BOM, cero CRLF, cero U+FFFD y cero coincidencias del patrón `[A-Za-z]\?[a-z]`. Sin stage, commit ni push.
