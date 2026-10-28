# Fase 42 — informe

## C34 — etiquetas para asignación ampliada Meta

- `grid/meta_operator.py:2430-2499,4255-4259` persiste con cada propuesta "agregar" las geometrías y capitales de la cartera base y de la cartera con la pieza propuesta. El job diario de aprendizaje etiqueta por separado 72 h y el plazo del perfil cuando cada horizonte ha madurado, y registra un evento enlazado por id a la propuesta. Los errores de geometría quedan terminales en esa etiqueta y no se reintentan cada día.
- `grid/meta_learning.py:774-800` compara ambas carteras con `replay_alternative_labels`, las mismas velas posteriores y filtros reales. `grid/readiness.py:123-163` calcula muestra, medias e intervalos de las diferencias en ambos horizontes. `api/routes/intelligence.py:99-101,545-555,655-661` enlaza eventos parciales de etiqueta y los publica en el medidor de asignación ampliada.
- Pruebas: `tests/test_meta_learning.py`, `tests/test_meta_operator.py`, `tests/test_intelligence_readiness.py`, `tests/test_intelligence_api.py`: **126 passed, 5 warnings, 0 failed, 15,41 s** (`ASPLE_OFFLINE=1`, `--basetemp` temporal nuevo). Los replays de etiqueta usan velas sintéticas; no se abrieron órdenes ni se cambiaron decisiones reales.
