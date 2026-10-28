# Fase 42 — informe

## C34 — etiquetas para asignación ampliada Meta

- `grid/meta_operator.py:2430-2499,4255-4259` persiste con cada propuesta "agregar" las geometrías y capitales de la cartera base y de la cartera con la pieza propuesta. El job diario de aprendizaje etiqueta por separado 72 h y el plazo del perfil cuando cada horizonte ha madurado, y registra un evento enlazado por id a la propuesta. Los errores de geometría quedan terminales en esa etiqueta y no se reintentan cada día.
- `grid/meta_learning.py:774-800` compara ambas carteras con `replay_alternative_labels`, las mismas velas posteriores y filtros reales. `grid/readiness.py:123-163` calcula muestra, medias e intervalos de las diferencias en ambos horizontes. `api/routes/intelligence.py:99-101,545-555,655-661` enlaza eventos parciales de etiqueta y los publica en el medidor de asignación ampliada.
- Pruebas: `tests/test_meta_learning.py`, `tests/test_meta_operator.py`, `tests/test_intelligence_readiness.py`, `tests/test_intelligence_api.py`: **126 passed, 5 warnings, 0 failed, 15,41 s** (`ASPLE_OFFLINE=1`, `--basetemp` temporal nuevo). Los replays de etiqueta usan velas sintéticas; no se abrieron órdenes ni se cambiaron decisiones reales.

## C35 — alertas horarias de evidencia

- `grid/intelligence_readiness.py:14-60` evalúa como job cron UTC al minuto 37, con candado propio, caché en memoria por hora y `max_instances=1`. La evaluación no toma `ops_lock`; solo encola estados en AlertDispatcher.
- `api/routes/intelligence.py:63-71` conserva la autorización de la ruta pública y separa la función interna de lectura. `api/main.py:172-181,211` conecta el evaluador del servicio e inicia/detiene el job durante el ciclo de vida de la API.
- `tests/test_alert_dispatcher.py:156-185` demuestra que el job produce una transición y una sola alerta sin acceder a una pantalla, y que la caché horaria evita recalcular dentro de la misma hora. Pruebas dirigidas: `tests/test_alert_dispatcher.py tests/test_intelligence_api.py`: **31 passed, 2 warnings, 0 failed, 11,37 s**.
- El cron C35 queda en el minuto 37 como se especificó para el chequeo de medidores. La evaluación horaria de grids de 42a se agenda en el minuto 7 y usa un guard independiente.
