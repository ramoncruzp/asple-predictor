# Reporte Fase 40d

## 40d-1 — Etiquetas y almacenamiento

### Implementado

- `database/db_manager.py:340-364, 387` añade tres tablas aditivas creadas por `metadata.create_all`: `meta_learning_samples` (snapshot puntual por alternativa), `meta_learning_labels` (resultado separado por horizonte, clave única por muestra/horizonte) y `meta_learning_runs` (estado, resumen y duración por etapa). Se añadieron índices por moneda/fecha y horizonte. No se reescriben tablas anteriores.
- `grid/meta_operator.py:847-901` congela las señales y parámetros de cada alternativa evaluada como origen `live`. El importador idempotente recupera también eventos `META_PLAN_EVALUATED` previos a 40d.
- `grid/meta_learning.py:23-35, 88-179` alinea cortes a bloques UTC no solapados de 72 h; reconstruye volatilidad realizada, toques, pronósticos por modelo/horizonte y dirección por modelo/intervalo usando solo registros/velas con fecha menor o igual al corte. Los pronósticos y predicciones futuros se descartan. Toques usan `touch_stats` con el horizonte ya completo, sin usar la estadística retrospectiva de 40b-5 como señal de entrada.
- `grid/meta_learning.py:67-85, 180-255` calcula el `p_replay` histórico desde simulaciones de ventanas completas anteriores al corte; conserva el resultado y su cantidad de ventanas en procedencia. La geometría de la alternativa se trata como plantilla contrafactual y se reancla al precio de cada fecha.
- `grid/meta_learning.py:256-344` reproduce la cartera con el simulador actual. La etiqueta de 72 h incluye avance neto, máximo avance, tiempo de cruce y si cruzó la parte proporcional del objetivo (`objetivo × 72 h / plazo del perfil`). La etiqueta de plazo (20/45 días) comprueba el objetivo completo. Si no hay velas hasta el horizonte, queda inmadura y no se guarda como resultado.
- `grid/meta_operator.py:987-1118, 2304-2312` ejecuta el etiquetado una vez al día a las 08:30 UTC, después del precálculo de las 08:00 UTC. Usa un guard propio, no `ops_lock`; no abre ni modifica órdenes. Registra tiempos de importación, lectura de mercado, reconstrucción histórica, simulación y persistencia.
- La reconstrucción histórica tiene origen `historical_reconstructed` y se conserva separada de `live`. Solo usa hasta 180 días de velas. Scanner, Advisor y `break_prob` quedan ausentes en el histórico si no hay snapshots fechados disponibles; no se rellenan con valores actuales.
- `grid/sim/meta_bot.py:10-12, 42-45` añade un callback opcional de trazas para medir avance de cartera dentro del horizonte sin cambiar el resultado normal del simulador.

### Muestra y límites de evidencia

- No se abrió la BD real ni se consultó S1. El único dato real confirmado para esta entrega es la corrida Meta #3 indicada por Ramón; el número de filas de bitácora, observaciones y pronósticos reales queda sin contar.
- Las pruebas usan SQLite temporal y velas sintéticas. No se ejecutó el job contra mercado ni se certificó el tiempo de backfill de 180 días. El job dejará esos tiempos en `meta_learning_runs`; deben medirse al correrlo en el entorno de prueba autorizado.
- El histórico reconstruido evalúa configuraciones como contrafactuales. La selección antigua de alternativas de Scanner/Advisor no se reconstruye porque el proyecto no guardó esas series históricas; sus señales se marcan omitidas. Dirección y volatilidad sí se reconstruyen solo desde sus tablas con timestamps.
- Este commit no ajusta pesos, no cambia decisiones, no añade la sombra aprendida ni interruptor/vista; pertenecen a 40d-2 y 40d-3.

### Pruebas

- Enfocadas: `tests/test_meta_learning.py`, `tests/test_meta_core.py`, `tests/test_meta_operator.py` — **84 passed, 4 warnings, 5,36 s**.
- Suite completa: `ASPLE_OFFLINE=1 .\venv\Scripts\python.exe -m pytest tests -m "not live" -q --basetemp="$env:TEMP\asple_40d1_full_20261025_job"` — **1523 passed, 1 skipped, 18 deselected, 56 warnings, 339,00 s**, exit code 0.
- Casos añadidos: umbral proporcional conocido; bloques de 72 h sin solape; descarte de velas, pronósticos y predicciones futuras; replay sintético de bot con cruce conocido; persistencia de muestra/etiqueta y deduplicación; job diario sintético que guarda etiquetas y tiempos sin llamadas al exchange.
- No se usó S1, Testnet, `.env` ni base real. No se hizo despliegue.
