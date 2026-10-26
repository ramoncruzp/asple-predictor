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


## 40d-3A — Costo acotado del aprendizaje

### Implementado

- `grid/meta_learning.py:18-52,101-142,459-565` crea identidad estable por perfil, moneda, tipo, estrategia, niveles, piso/medio y techo/medio cuantizados en pasos 0,0025 y fracción de capital. El backfill comprueba la clave plantilla+corte antes de reconstruir y reusa resultados de replay por plantilla+inicio de ventana.
- Las ventanas históricas se anclan a lunes 00:00 UTC con paso de 7 días. El replay operativo sigue anclado a la fecha de análisis; el desfase de fase puede ser de hasta 7 días. No se cambió el método del replay operativo.
- `database/db_manager.py:367-371,406` agrega `meta_learning_replay_cache`. `grid/meta_operator.py:1134-1290` lee y persiste esa caché, limita a las 30 plantillas más recientes por corrida y a 20 minutos por defecto, guarda IDs pendientes en `meta_learning_runs.summary_json` y cede 10 ms cada tres cortes. Los límites se editan mediante `meta_profile_settings` y `PUT /api/metas/config/learning-limits` (`grid/meta.py:156-207`, `api/routes/metas.py:97-102`).
- `scripts/estimar_costo_aprendizaje_meta.py:1-100` estima plantillas, cortes, ventanas y tiempo usando una URL explícita, con conexión de solo lectura. No se ejecutó contra ninguna base ni mercado.

### Pruebas y resultado

- Corrida enfocada final de A: `tests/test_meta_learning.py tests/test_meta_core.py tests/test_meta_operator.py` — **95 passed, 5 warnings, 6,97 s**, salida 0. Una corrida previa tuvo un fallo únicamente en una aserción demasiado amplia del nombre de clave; ajustada y el archivo afectado quedó en **16 passed, 0,70 s** antes de la corrida final.
- Se cubren forma relativa con precios/capital escalados, cache hit entre cortes, igualdad del resultado con caché y sin caché, saltar cortes existentes, presupuesto con pendientes y continuación en la siguiente corrida.
- `py_compile` pasó para los módulos Python del núcleo y el estimador. No se consultó S1/Testnet, `.env` ni la BD real; no se corrió la suite completa.


## 40d-3B — Sombra, interruptor y vista

### Implementado

- `grid/meta_operator.py:199-245` resuelve el peso por perfil y señal: versión live aceptada con al menos 30 observaciones efectivas; si falta, versión histórica reconstruida aceptada; en otro caso conserva 0,25. Devuelve la procedencia, el motivo/freno, p ajustado por Holm y la versión usada. El ajuste del logit sigue limitado por `signal_logit_cap` (±1,0).
- `grid/meta_operator.py:747-760,895-925,962-1008,2131-2195` calcula junto a las alternativas existentes la selección por pesos aprendidos y Scanner+Advisor. Con el interruptor apagado, la elección real conserva la ruta de ranking previa; con él encendido se ordena por la probabilidad aprendida. Las compuertas de materialidad, cooldown, bloqueo, capital y revalidación permanecen en las rutas existentes. Cada revisión escribe las tres alternativas con `alternative_id` en `META_OPERATOR_REVIEW_DECISION`; la sombra Scanner+Advisor conserva sus pesos previos aunque el interruptor se active.
- `database/db_manager.py:378-382` crea `meta_learning_switch_events` con perfil, valor, actor, fecha y versión. `grid/meta_operator.py:247-275` exige confirmación, registra quién pidió el cambio y nunca activa el interruptor automáticamente. `grid/meta.py:16-57` lo define apagado por defecto en ambos perfiles.
- `api/routes/metas.py:114-122` ofrece `GET /api/metas/learning?profile=...` y `PUT /api/metas/learning/switch`; ambos requieren `X-API-Token` (o loopback según la regla API existente), y el cambio exige confirmación explícita y actor. La respuesta incluye pesos/fuentes/muestras/p Holm/motivos, validación frente a replay, alternativas reales/sombra/aprendida y última corrida.
- `grid/meta_operator.py:276-379` compara las alternativas elegidas cuando maduran las etiquetas ya existentes de 72 h y del plazo, sin generar simulaciones adicionales. Si el interruptor está encendido y dos versiones seguidas tienen log-loss y Brier peores que la elección real en 72 h, manda alerta; no desactiva el switch.
- `frontend/metas.js:489,540-599` y `frontend/metas.css:74-79` añaden la sección «Aprendizaje» en la Meta activa, con aviso «Experimento en sombra», tabla traducida al español, validación, comparación y acción confirmada. `tests/ui/test_metas_ui.py:273-288` prueba el estado inicial y el cambio confirmado.
- Corrección de seguridad de caché descubierta al cerrar A: `grid/meta_learning.py:18-39` también incorpora a la identidad las fracciones objetivo/capital, máximo de pérdida y margen, que afectan al resultado simulado. Siguen siendo relativas o parámetros del perfil; evita compartir etiquetas binarias entre objetivos distintos.

### Pruebas y resultado

- Corrida final enfocada, con `ASPLE_OFFLINE=1` y carpeta temporal nueva: `tests/test_meta_learning.py tests/test_meta_core.py tests/test_meta_operator.py tests/test_meta_learning_api.py tests/ui/test_metas_ui.py` — **115 passed, 11 warnings, 30,12 s**, exit code 0.
- `py_compile` pasó para los módulos Python tocados y el script de estimación. `node --check frontend/metas.js` pasó.
- Durante la implementación, una corrida enfocada inicial encontró dos fallos por incluir el booleano `use_learned_weights` en una validación `Decimal`; se excluyó del chequeo numérico y `tests/test_meta_core.py` pasó **29/29**. Una prueba del bloqueo del 75 % también detectó acceso a `plan=None` al guardar una revisión sin análisis; se protegió esa ruta y el archivo API nuevo pasó **7/7**. La corrida final completa enfocada quedó verde.
- No se ejecutó suite completa, script de estimación, S1, Testnet ni consulta/escritura de la BD real. No hubo despliegue ni push.

### Evidencia pendiente

La comparación solo crece a medida que maduran etiquetas y depende de que haya alternativas seleccionadas y pesos aceptados suficientes. No hay todavía muestra de producción verificada ni fundamento para recomendar activar los pesos. El interruptor sigue apagado por defecto.


## 40d-3C - Interruptor en Laboratorio y aprendizaje global

### Implementado

- `frontend/metas.js:554-638` muestra el aprendizaje como bloque global para Rápido y Prudente, aun sin meta activa. Conserva la tabla de señales, validación, comparación de las tres decisiones y última corrida; cada perfil se consulta al entrar o al pulsar Actualizar, no con el refresco periódico.
- `frontend/metas.js:596-637` y `frontend/lab.js:35-38` colocan dos interruptores en Laboratorio → Reglas globales. Cada uno presenta el estado, el último actor/fecha/versión o Nunca encendido, el aviso cuando falta evidencia y una confirmación dentro de la página con Quién confirma obligatorio. Ambos usan `PUT /api/metas/learning/switch`. Se quitaron el botón y el campo de actor de Metas.
- `frontend/metas.js:748-784` limita la recarga de 30 segundos a valoración, efectivo, piezas, reajustes recientes y bitácora. Mantiene la posición vertical; se pausa con foco en campos de texto/select, confirmación abierta, análisis en curso o plan visible.
- `frontend/metas.css:81` añade estilos del bloque global por perfil.
- Pruebas UI en `tests/ui/test_metas_ui.py:273-326` cubren la sección con ambos perfiles y sin control de edición en Metas, además del cambio confirmado en Laboratorio.

### Pruebas y resultado

- Corrida enfocada final: `ASPLE_OFFLINE=1 python -m pytest tests/ui/test_metas_ui.py tests/test_meta_learning_api.py tests/ui/test_laboratorio.py tests/test_app_settings_api.py -q --basetemp <carpeta nueva en TEMP>` - **33 passed, 16 warnings, 46,86 s**, código de salida 0.
- `node --check frontend/metas.js`, `node --check frontend/lab.js` y `node --check frontend/global_flags.js` pasaron.
- No se ejecutó la suite completa. No se usaron S1, Testnet, `.env` ni la base real; no hubo despliegue.


## 40d-4A - Comparación consistente de decisiones

- `grid/meta_operator.py:57-72` comparte `rank_alternatives` entre real, Scanner + Advisor y aprendida: ventanas rodantes disponibles, probabilidad de cada decisión y los mismos desempates por avance, días y peor caída.
- `grid/meta_operator.py:321-342` solo agrega a la comparación eventos con `comparison_version=1`; conserva los eventos antiguos sin contarlos bajo el contrato nuevo. Las revisiones sin acción registran mantener para las tres decisiones, evitando diferencias ficticias por `alternative_id=None`.
- Pruebas enfocadas: `tests/test_meta_operator.py` y `tests/test_meta_learning_api.py`: **60 passed, 9 warnings, 0 failed**, 10,30 s. `ASPLE_OFFLINE=1`; carpeta temporal nueva. Sin suite completa, S1, Testnet, `.env` ni BD real.
- Alcance de la comparación activa: el registro compara acción y destino cuando la revisión produce una acción; el cálculo contrafactual de reajustes/rotaciones bloqueados por materialidad o persistencia sigue dependiendo de las alternativas evaluadas en esa revisión.


## 40d-4B - Pantalla Inteligencia

- `api/routes/intelligence.py:59` añade `GET /api/intelligence`, protegido por el token compartido y de solo lectura. Agrega pronósticos, predicciones, observaciones Meta, bitácora, muestras, etiquetas, exclusiones, replay/caché, versiones de regresión, decisiones, corridas, interruptores y banderas globales. Las listas de versiones, corridas y decisiones admiten `page` y `page_size`; no ejecuta replay ni ajuste de pesos.
- `api/routes/intelligence.py:24` y `grid/meta_operator.py:379` calculan el medidor por perfil con las cuatro condiciones acordadas. La respuesta no modifica el interruptor. `frontend/metas.js:554` y `frontend/metas.js:620` muestran el medidor en Metas y junto a los switches de Laboratorio; avisan si está encendido sin evidencia verde.
- `frontend/index.html:20`, `frontend/index.html:30`, `frontend/index.html:34`, `frontend/app.js:401` y `frontend/intelligence.js:14,31-38` añaden la pestaña Inteligencia, las secciones B1-B9, botón Actualizar, fecha relativa y enlaces a Modelos/Battle. `frontend/intelligence.css:1` añade estilos.
- La base de la aplicación no persiste velas. B1 lo informa; no se leen archivos de `data/` ni se descargan velas. La caché muestra entradas y aciertos registrados en la última evaluación, pero el contador de simulaciones nuevas y los deciles de calibración no están persistidos y se muestran como no disponibles.
- Pruebas: `tests/test_intelligence_api.py:12-` (token, agregados, solo lectura y medidor gris/amarillo/verde, con cada freno) y `tests/ui/test_intelligence_ui.py:7-` (navegación, lectura y actualización manual). Corrida enfocada: `ASPLE_OFFLINE=1 python -m pytest tests/test_meta_learning.py tests/test_meta_core.py tests/test_meta_operator.py tests/test_meta_learning_api.py tests/test_intelligence_api.py tests/ui/test_intelligence_ui.py -q --basetemp <carpeta nueva en TEMP>`: **108 passed, 10 warnings, 0 failed, 4,81 s**, código 0.
- `node --check` pasó para `frontend/app.js`, `frontend/metas.js` y `frontend/intelligence.js`. Sin suite completa, S1, Testnet, `.env` ni BD real.

## 40d-5 - Señal de toque compra→venta en el aprendizaje

### Implementado

- `grid/meta_learning.py:14-17` agrega `touch_buy_to_sell` como sexta variable y define pesos base: 0,25 para las cinco anteriores y 0 para toque.
- `grid/meta_learning.py:173-225` lee el `sell_rate` histórico guardado en `point_in_time_signals`, lo normaliza con `2 × tasa − 1` a [-1, 1] y combina piezas por capital asignado. La extracción en vivo usa el mismo campo `normalized` y la misma máscara de presencia; una fila antigua sin la señal no suma muestra.
- `grid/meta_learning.py:388-400,470-485` hace que `point_in_time_signals` y el operador vivo llamen a `causal_touch_buy_to_sell`: `touch_stats` sobre hasta 30 días de velas con timestamp no posterior al corte y la geometría compra/venta de la pieza. El valor bruto persistido sigue siendo la tasa de venta; la normalización se aplica igual al extraer muestras históricas y vivas.
- `grid/meta_operator.py:793,929-934,2002-2018` calcula la señal viva desde la caché de velas usada por el análisis, agrega las seis señales de cartera ponderadas por capital y conserva por separado `touch_signal`/`meta_signal_observations` de 40b-5. No se usa el puntaje retrospectivo de 40b-5 para entrenar.
- `grid/meta_learning.py:239-345` incorpora seis columnas a la regresión, amplía Holm a seis contrastes y regulariza toque hacia 0 (las otras cinco variables hacia 0,25). Se mantienen el mínimo de 30, errores agrupados, límite de 0,05 por versión y tope total del logit de ±1.
- `grid/meta_operator.py:99-103,210-250,367-390` usa cero como prior de toque en ejecución y medidor. Con el interruptor apagado, la nueva señal no altera la probabilidad; encendido, solo aplica un peso que la resolución acepta con muestra suficiente.
- `api/routes/intelligence.py:25-50,150-185`, `frontend/metas.js:543-544,565-579,615-621` y `frontend/intelligence.js:14-16,22-29` muestran la fila traducida en Metas e Inteligencia. Inteligencia incluye toque en cobertura, coeficientes de regresión y tasas de resultado por valor alto/bajo; el medidor reconoce su base 0. No se borraron versiones anteriores y no se necesitó migración.
- La reconstrucción histórica previa ya guardaba `sell_rate` y procedencia causal desde `point_in_time_signals`, con la misma ventana y geometría. No se marcaron esas filas para recálculo ni se borraron. Las muestras previas sin la señal quedan neutrales al construir la matriz y no cuentan en el mínimo de toque.

### Pruebas y verificación

- Pruebas nuevas: `tests/test_meta_learning.py:87-106,310-315,330-353`; `tests/test_meta_operator.py:602-640`; `tests/test_meta_learning_api.py:26-44,78-81`; `tests/test_intelligence_api.py:38-62`; UI: `tests/ui/test_metas_ui.py:286,340-342`, `tests/ui/test_intelligence_ui.py:20-21`.
- Archivos ejecutados: `tests/test_meta_learning.py`, `tests/test_meta_operator.py`, `tests/test_meta_learning_api.py`, `tests/test_intelligence_api.py`, `tests/ui/test_metas_ui.py`, `tests/ui/test_intelligence_ui.py`.
- Corrida enfocada final, `ASPLE_OFFLINE=1`, con carpeta temporal nueva bajo `%TEMP%`: **99 passed, 12 warnings, 40,45 s**, código 0.
- Corrida inicial del mismo conjunto: **92 passed, 3 failed** por `LEARNING_SIGNALS` no importada dentro de `_build_plan`; se añadió el import local y `tests/test_meta_operator.py` pasó 53/53 en comprobación intermedia. Tras la última corrección al peso base cuando el switch está apagado, ese archivo pasó **54 passed, 4 warnings, 8,10 s**. Una corrida posterior tuvo una carrera de carga en `tests/ui/test_metas_ui.py:340-342` (selector de reajustes consultado antes de aparecer); se esperó explícitamente el contenedor, el caso aislado pasó y la corrida enfocada conjunta final quedó verde: **99 passed, 12 warnings, 40,45 s**.
- `node --check frontend/metas.js` y `node --check frontend/intelligence.js`: pasaron.
- No se ejecutó la suite completa. No se usaron S1, Testnet, `.env` ni la base real; no hubo despliegue ni red.

### Límite conocido

La pantalla Inteligencia no tiene deciles de calibración persistidos (ya documentado en 40d-4B). Esta fase muestra toque en cobertura, regresión y tasas de acierto alta/baja disponibles; no inventa deciles ni escribe una migración nueva.


## 40d-6 - Contrafactual completo, calibración y conteos persistidos

- Se añadió counterfactual_policy_decision, función pura compartida. La decisión real la usa en el gate de rotación y en reajustes; conserva materialidad, dos revisiones, cooldown y límites diarios. Los cierres de pérdida siguen bajo el guardián de 40a.
- Las sombras aprendida y Scanner + Advisor evalúan por pieza con estado independiente guardado en META_OPERATOR_COUNTERFACTUAL_STATE; guardan persistencia, cooldown, contador diario, decisión y destino contrafactual.
- comparison_version pasa a 2. La agregación de Aprendizaje cuenta solo revisiones v2 y expone decision_comparison_since; la vista Inteligencia muestra desde cuándo se acumula.
- Cada corrida guarda por moneda cantidad, primera y última fecha, intervalo mediano y huecos de velas en meta_learning_runs.summary_json. No persiste velas.
- Cada versión de pesos conserva deciles de validación del modelo y de solo p_replay: probabilidad predicha media, tasa real de éxito y n.
- Las corridas registran ventanas replay simuladas, ventanas leídas de caché y cortes históricos procesados por separado. Los resúmenes existentes (summary_json) evitan una migración.
- Archivos: grid/meta_operator.py, grid/meta_learning.py, frontend/intelligence.js; pruebas en tests/test_meta_operator.py, tests/test_meta_learning.py, tests/test_meta_learning_api.py, tests/test_intelligence_api.py y tests/ui/test_intelligence_ui.py.
- Verificación final: 89 passed, 10 warnings; node --check frontend/intelligence.js pasó. Sin suite completa, S1, Testnet, .env ni base real.
- Sin discrepancias funcionales conocidas en el alcance de 40d-6.


## 40d-6B - Restauración del reajuste real

- `grid/meta_operator.py:2767` restaura la política de reajuste previa a 40d-6: materialidad compara `estimate["probability"]`; la persistencia por defecto es una revisión; el límite cuenta cuatro reajustes por Meta en 24 horas; y el enfriamiento usa 3600 segundos desde el último reajuste de la misma pieza.
- `grid/meta_operator.py:2326` aplica a las sombras aprendida y Scanner + Advisor los mismos umbrales, persistencia, enfriamiento y límite diario cuando evalúan un reajuste. Sus estados contrafactuales siguen separados; la edad de apertura no añade un freno que no existía en la decisión real. La política de rotación conserva sus parámetros anteriores.
- `grid/meta.py:20` y `grid/meta.py:217` añaden `reconfig_persistence_reviews` editable por perfil, con valor 1; un valor 2 exige dos revisiones consecutivas. `max_reconfigurations_per_24h` conserva el tope predeterminado de 4.
- `database/db_manager.py:413` completa el default en perfiles existentes. En la migración única de 40d-6B, si encuentra valor legado 2 y existe una Meta de ese perfil, lo cambia a 1 y escribe `META_PROFILE_SETTING_MIGRATED` en su bitácora. Si todavía no existe esa Meta, deja pendiente la migración para poder registrar el evento después. Un marcador global evita que un valor 2 configurado después se restablezca en cada arranque.
- Pruebas en `tests/test_meta_operator.py:296`, `tests/test_meta_operator.py:439`, `tests/test_meta_operator.py:1626` y `tests/test_meta_operator.py:1708`: materialidad con probabilidad ajustada, primer reajuste inmediato, secuencia de borde y salida de rango, enfriamiento antes/después de una hora, quinto reajuste bloqueado aunque sea otra pieza, persistencia configurable 1/2, sombras y migración con bitácora.
- Corrida autorizada: `ASPLE_OFFLINE=1 .\\venv\\Scripts\\python.exe -m pytest tests/test_meta_operator.py -q --basetemp <carpeta nueva bajo %TEMP%>`: **60 passed, 4 warnings, 3,28 s**, código 0. No se ejecutó la suite completa.
- Sin S1, Testnet, `.env` ni base real. No hubo cambios de rotación ni de grids normales.


## 40d-7A - Diagnóstico de señales saturadas

### Hallazgos y corrección propuesta (sin cambiar decisiones ni pesos)

- Volatilidad: `data/volatility.py:201-211` define `target_logvol` como logaritmo del RMS de la varianza horaria futura (24 barras), es decir, sigma por hora. El Scanner `scan_service.py:121-123` calcula `sigma_24h_open = std(log(retornos_1h)) × √24`, sigma acumulada de 24 h. `grid/meta_operator.py:2074-2078` compara directamente ambas magnitudes con `clip(log(forecast_sigma / sigma_24h_open), -1, 1)`. Ejemplo: 0,01 por hora frente a 0,05 en 24 h da `log(0,2) = -1,609`, recortado a −1; al expresar el pronóstico en el mismo horizonte (`0,01 × √24 = 0,04899`), el resultado sería −0,020. Propuesta para aprobación: convertir el forecast por hora a horizonte 24 h antes de compararlo.
- Ruptura: `grid/meta_operator.py:2090-2094` entrega a `break_prob` la misma mediana horaria sin convertirla. `grid/policy.py:659-671` espera sigma 24 h y la transforma a horizonte 4 h como `sigma_24h × 1,15 × √(4/24)`, por lo que el movimiento queda subestimado por un factor cercano a √24. Después, la señal normaliza `1 − 2 × break_prob`; si la probabilidad de toque cae a cero por precisión, queda en +1. Ejemplo sintético de prueba: probabilidad de toque 0,0094 y señal +0,981 con sigma horaria 0,04; usando la sigma 24 h equivalente (0,196), probabilidad 0,607 y señal −0,214. Propuesta: convertir a sigma de 24 h antes de llamar a `break_prob`.
- Dirección: `_DownFeatureEngineer` en `models/model_a_xgboost.py:239` define clase positiva como retorno futuro negativo; `ModelA.predict` llama `probability_up` a la probabilidad de la clase positiva (`:159-180`) y `ModelADown.predict_down` la expone como `p_down` (`:261`). Por tanto, en `model_a_down`, `probability_up ≥ 0,60` significa P(baja) ≥ 0,60. `grid/meta_operator.py:2104-2105` hoy normaliza esa probabilidad como señal positiva. Propuesta: documentarla/renombrarla como P(baja) e invertir su signo para que la señal bajista reduzca evidencia favorable a compras.
- Toque compra→venta: `grid/meta_operator.py:2123-2139` solo la agrega si hay velas disponibles en la caché y `causal_touch_buy_to_sell` devuelve una tasa no nula usando velas anteriores, geometría de compra/venta y hasta 30 días. El porcentaje 41/765 (5,4 %) comunicado por Ramón no se recalculó porque esta fase prohíbe consultar S1/BD real. El script lee los motivos `signals_omitted` que ya guarda la bitácora, además de cobertura y valores por muestra; si la bitácora antigua no conserva un motivo, lo indica como no disponible.
- `grid/meta_operator.py:2076-2081` agrega a la señal de volatilidad campos diagnósticos (sigma de referencia 24 h, horizonte y unidad) sin cambiar `value`, `normalized`, pesos ni probabilidad. Esto permite comparar ambas sigmas en muestras futuras.

### Herramienta y pruebas

- `scripts/diagnostico_senales_meta.py:1` es una herramienta de solo lectura: requiere `--db` como ruta SQLite explícita, abre `mode=ro` y activa `query_only`; no lee `.env`, no importa configuración de conexión y no usa red. Imprime por moneda/señal min, p10, mediana, p90 y max crudo y normalizado, porcentaje en −1/+1, cobertura, motivos guardados de omisión y ambas sigmas de volatilidad lado a lado. Ejecución prevista en S1: `python scripts/diagnostico_senales_meta.py --db <ruta SQLite> --days 1` (no ejecutada aquí).
- Pruebas `tests/test_meta_signal_diagnostics.py:1`: ejemplo numérico del desajuste de horizonte, saturación de ruptura, cuantiles/colas y lectura SQLite sin escritura.
- Corrida focalizada: `tests/test_meta_signal_diagnostics.py` y `tests/test_meta_operator.py`: **63 passed, 4 warnings, 8,56 s**, código 0, `ASPLE_OFFLINE=1`, basetemp nuevo bajo `%TEMP%`.
- Sin S1, Testnet, `.env`, red ni BD real. La corrección de unidades queda propuesta y pendiente de aprobación; no se alteró la decisión real.

## 40d-7B - Inteligencia legible, aciertos en sombra y correcciones de Metas

- `api/routes/intelligence.py:63-379` agrega un resumen verbal calculado con alternativas de hoy, monedas, modelos de volatilidad y última corrida; B3 entrega diez intervalos con p_replay crudo y suavizado, soporte de ventanas y una interpretación breve.
- B1 muestra sus tablas dentro de bloques cerrados por defecto. B6 presenta fecha, meta, evento y las tres decisiones legibles; diferencia filas, permite filtrar por meta y paginar, y deja el JSON solo dentro del detalle expandible.
- B10 calcula, al consultar, acierto de dirección con intervalo Wilson 95 % (por modelo/moneda, ventanas de 7 y 30 días), MAE de volatilidad en log y porcentaje dentro de ±20 %, calibración de toque y p_replay contra etiquetas maduras, y tasa de éxito de las decisiones real y sombra en 72 h y plazo. Filas con n < 30 aparecen como pocas muestras en gris. Se usan únicamente resultados ya persistidos; no se añadieron tablas ni trabajos de fondo.
- `grid/meta.py:442-484` incluye el estado real del grid en cada pieza valorada. `frontend/metas.js:371-389` muestra el avance fraccional hacia el objetivo (por ejemplo, 0,37 USDT de 3 = 12,3 %) y omite las piezas cerradas de la lista viva.
- `frontend/intelligence.js:13-77` y `frontend/intelligence.css:1-4` implementan el resumen, gráficos, bloques desplegables, paginación y aciertos. La consulta de observaciones ahora cuenta cada fila como `touch_buy_to_sell`, en vez de interpretar las claves del JSON como señales; B5 ya informa ese conteo. El inventario muestra su fecha o «Aún no hay corrida».
- Archivos de prueba: `tests/test_intelligence_api.py:38-100`, `tests/ui/test_intelligence_ui.py:7-30` y `tests/test_meta_core.py:162-186`. Corrida final focalizada con `ASPLE_OFFLINE=1` y basetemp nuevo bajo `%TEMP%`: `tests/test_intelligence_api.py tests/ui/test_intelligence_ui.py tests/ui/test_metas_ui.py tests/test_meta_core.py` -> **47 passed, 4 warnings, 37,48 s**, código 0. `node --check frontend/intelligence.js` y `node --check frontend/metas.js`: correctos.
- No se ejecutó la suite completa ni se usaron S1, Testnet, `.env`, red o la base real. 40d-7B queda verificada localmente; commit pendiente del comando de publicación solicitado.
