# Discrepancias ? Fase 18C

- El test previo afirmaba que Simple nunca deb?a componer; esa expectativa contradice el alcance de 18C. Se sustituy? por caso positivo de ciclo y casos ausente/desactivado en `tests/test_grid_compound_engine.py:148-171`.
- La comparaci?n inicial descubri? que `GridEngine.create_grid` tambi?n bloqueaba los par?metros compound de Simple antes de guardarlos (`grid/engine.py:291-307`). Se ampli? el allowlist solo con plazo y las tres claves compound; `validate_params` mantiene la validaci?n de valores.
- `tests/test_grid_compound_live.py` ejecuta ?rdenes Testnet reales y est? marcado `live`; no se ejecut? conforme al l?mite sin Testnet. Esa integraci?n sigue **NO VERIFICADA**.
- `tests/test_grid_engine.py -k simple` seleccion? cero casos (41 deselected); no se usa como evidencia de prueba.
