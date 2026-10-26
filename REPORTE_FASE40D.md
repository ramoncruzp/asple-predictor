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

## 40d-2 — Modelo de pesos y frenos

### Implementado

- `grid/meta_learning.py:67-100` calcula el replay histórico con plazo de 20/45 días y ventanas rodantes cada 7 días. Solo acepta ventanas cuyo final sea anterior al corte. La procedencia incluye `step_days`, `window_count`, `independent_windows` y `method_version=rolling_7d_v2`. La probabilidad cruda sigue en `base_probability`; `base_probability_smoothed` usa `(éxitos + 1) / (ventanas + 2)`. La muestra se excluye del entrenamiento si hay menos de 3 ventanas efectivas independientes.
- `database/db_manager.py:340-385, 448-469` añade columnas para la probabilidad suavizada, los dos recuentos de ventanas, versión del método, marca de recálculo y motivo de exclusión; agrega `meta_learning_weight_versions` para registrar cada intento. La migración conserva filas y marca para recalcular las muestras históricas con el método anterior.
- `grid/meta_operator.py:1000-1128` recalcula las bases antiguas al correr el job diario y guarda una versión separada por perfil y procedencia (`live` / `historical_reconstructed`). No llama a órdenes ni toma `ops_lock`.
- `grid/meta_learning.py:102-131, 169-314` reduce la muestra por moneda con bloques no solapados, divide por fecha completa (70 % anterior para entrenar; el resto posterior para validar), usa regresión logística con `logit(p_replay suavizado)` como offset y regularización hacia 0,25. Las señales ausentes aportan cero al logit y no cuentan para el umbral de esa señal; las señales históricas disponibles se normalizan con las mismas fórmulas que en vivo y, en carteras, se ponderan por el capital de cada pieza. Scanner, Advisor y `break_prob` no se imputan en el histórico.
- El error estándar usa sandwich agrupado por fecha y moneda con inclusión/exclusión de las intersecciones. Los valores p contrastan coeficiente cero, se ajustan por Holm y se guarda el ajuste por señal. Cada peso tiene tope absoluto editable, variación máxima de 0,05 por actualización y el ajuste aplicado al logit se recorta a ±1,0. Solo se aceptan cambios si en validación posterior mejoran **log-loss y Brier** frente a solo `p_replay`; de lo contrario se persiste el intento y los pesos no cambian.
- `grid/meta.py:16-50` expone en los dos perfiles los parámetros editables: mínimo de 30 muestras efectivas, tope absoluto de peso 1,0, cambio máximo 0,05, regularización 1,0 y alfa 0,05. Se exige además un mínimo de 5 observaciones efectivas de validación como freno contra métricas con una muestra ínfima; queda documentado como decisión de implementación.
- Las versiones almacenan fecha, procedencia, pesos aceptados, muestras de entrenamiento/validación, corte temporal, log-loss/Brier y causa por señal (cambio, Holm, falta de evidencia o validación sin mejora). Ningún consumidor operativo lee estas versiones: los pesos vivos y decisiones siguen intactos hasta 40d-3.

### Pruebas y resultado

- Corrida inicial enfocada: **85 passed, 4 failed** por imports ausentes en cuatro pruebas nuevas; corregido únicamente el archivo de pruebas.
- Corrida enfocada final: `ASPLE_OFFLINE=1 .\venv\Scripts\python.exe -m pytest tests/test_meta_learning.py tests/test_meta_core.py tests/test_meta_operator.py -q --basetemp="$env:TEMP\asple_40d2_focused_20261026_final2"` — **92 passed, 4 warnings, 6,72 s**, exit code 0. Se usó una carpeta temporal nueva. No se ejecutó la suite completa por instrucción.
- Casos cubiertos: cadencia 7 días y corte estricto; probabilidades crudas 0/1 con suavizado y exclusión de base insuficiente; recálculo de una fila histórica antigua sin borrarla; muestras independientes y separación temporal; extracción histórica causal de volatilidad/dirección; señales ausentes; Holm; límite 0,05; cambio significativo de prueba; y peso que no mejora en validación y permanece igual.
- También pasó `py_compile` para los cuatro módulos modificados y `tests/test_meta_learning.py`. No se corrió S1, Testnet, `.env`, base real ni suite completa.

### Límites y discrepancias

- No se midió cuántas observaciones reales tienen Scanner/Advisor y otras señales; no se accedió a BD real. Las versiones actuales son experimento estadístico en sombra y no certifican beneficio futuro.
- La reconstrucción histórica incluye volatilidad y dirección solo cuando existen sus pronósticos/predicciones fechados; Scanner, Advisor y `break_prob` quedan neutrales y no suman muestra para esos pesos. Muestras antiguas `live` sin recuento de ventanas no son entrenables hasta contar con un snapshot completo nuevo.
- El piso adicional de 5 muestras efectivas en validación no estaba fijado numéricamente en el prompt; se agregó para evitar mover pesos con métricas de validación demasiado pequeñas. Sigue siendo un freno interno, no un parámetro global.
- La regresión usa las cinco señales de los perfiles actuales. La señal de toques permanece fuera del modelo hasta que se defina su inclusión en una fase posterior.
- No se implementaron sombra aprendida en decisiones, interruptor ni vista; corresponden a 40d-3. No se cambió el comportamiento de grids ni del operador.
