# Reporte Fase 41a — Corrección de señales del Modo Meta

## Resultado

Se corrigieron directamente las señales de volatilidad y dirección del operador Meta, la disponibilidad causal de la señal de toque y la trazabilidad de señales combinadas. No se añadieron interruptores ni cambios de sombra; los pesos y los umbrales permanecen iguales.

## Cambios por parte

- **41a-1, unidades:** `grid/meta_operator.py` convierte `exp(pred_logvol_cal)` de sigma por hora a sigma de 24 h con `sqrt(VOL_FORECAST_HORIZON_H)`, y guarda ambos valores. `grid/meta_learning.py` aplica la misma conversión en `point_in_time_signals`, usado por la reconstrucción histórica. La señal compara sigma 24 h contra `sigma_24h_open` y `break_prob` recibe también sigma 24 h. El diagnóstico incluye la unidad y sigma por hora.
- **41a-2, dirección:** `grid/meta_learning.py` centraliza `normalize_down_probability`; `model_a_down.probability_up` se registra como P(baja) y se normaliza con signo no positivo. El umbral sigue en 0,60.
- **41a-3, toque:** `grid/meta_operator.py` extrae `_load_candles(symbol)`, reutiliza caché por símbolo y día y lo llama desde replay y desde la señal de toque. Si falla, la alternativa conserva el análisis y la señal se omite con `velas no disponibles: <error>`.
- **41a-4, aprendizaje y diagnóstico:** las muestras nuevas live e históricas se etiquetan `signals_version: 2`; la importación retrospectiva de bitácoras antiguas se marca v1. `extract_learning_features` ignora volatilidad, ruptura y dirección en v1 (incluidas muestras antiguas sin versión), porque quedaron constantes o con signo incorrecto; Scanner, Advisor y toque siguen disponibles. `scripts/diagnostico_senales_meta.py` cuenta señales por `normalized` aunque falte `value`, muestra cobertura por versión y valores normalizados distintos. Las carteras conservan valor ponderado por capital, valores por pieza y diagnósticos por pieza.

## Usos de volatilidad revisados

- `grid/meta_operator.py:_model_snapshot`: sigma horaria convertida a 24 h; ambos valores quedan disponibles para auditoría.
- `grid/meta_operator.py:_apply_intelligence`: la comparación de volatilidad y la llamada a `break_prob` consumen `sigma_24h`.
- `grid/meta_learning.py:point_in_time_signals` y `extract_learning_features`: reconstrucción histórica y normalización usan la misma sigma 24 h.
- Replays y precálculos del operador usan `_evaluate`, que llama al cargador compartido de velas; las magnitudes de volatilidad de entrada del Scanner ya están en `sigma_24h_open`.
- La pantalla Inteligencia presenta pronósticos guardados con su `horizon_h`; no calcula otra sigma. Las filas nuevas de aprendizaje contienen valores ya normalizados con la corrección.
- Fuera de Meta revisé `grid/volatility_provider.py` y `api/routes/volatility.py`: convierten sigma por hora multiplicando por `sqrt(24)` o `sqrt(horizonte)`. `grid/policy.py` recibe sigma 24 h. No encontré otro uso confirmado de sigma horaria tratada como sigma 24 h. `database/db_manager.py:get_vol_history` devuelve el porcentaje exponenciado asociado al horizonte solicitado sin etiquetarlo como sigma 24 h; no se cambió.

## Comparación controlada antes/después

Mismo escenario sintético: precio 108, rango 90–108, sigma por hora 0,04; `p_replay=0,50`; los valores de Scanner, Advisor y toque son iguales y neutrales para aislar los tres defectos. Para dirección se usa P(baja)=0,533, coherente con que en la muestra live observada no superaba 0,533.

| Señal | Antes (24f2ba7) | Después (41a) | Diferencia |
|---|---:|---:|---|
| Volatilidad normalizada (forecast 0,01/h; referencia 0,05/24 h) | −1,000 | −0,020 | +0,980 |
| Probabilidad de ruptura (caso sintético indicado) | 0,0094; señal +0,981 | 0,6070; señal −0,214 | la escala ya no satura al alza |
| Dirección, P(baja)=0,533 | 0,000 | 0,000 | ninguna supera 0,60 |
| Scanner / Advisor / toque | sin cambio | sin cambio | 0 |
| `probability_adjusted`, pesos 0,25 por señal, `p_replay=0,50` | 0,4988 | 0,4854 | −1,35 puntos porcentuales |

Con esas mismas señales sintéticas y `p_replay` entre 0,10 y 0,90, el cambio calculado de probabilidad ajustada está entre −1,35 y −0,47 puntos porcentuales. Si P(baja) llegara a 0,70, dirección añadiría −1 al valor normalizado; con peso 0,25 y base 0,50, el resultado sería 0,4235 (−7,53 puntos frente al antes). Es un ejemplo numérico, no una estimación de rendimiento.

## Pruebas

Corrida dirigida con `ASPLE_OFFLINE=1`, carpeta nueva bajo `%TEMP%`, sin S1, Testnet, `.env` ni BD real:

`tests/test_meta_operator.py tests/test_meta_learning.py tests/test_meta_signal_diagnostics.py` — **94 passed, 4 warnings, 4,35 s**.

Incluye conversión de escala horaria a 24 h en operador e histórico, normalización de volatilidad, entrada a ruptura, signo y umbral de dirección, carga de velas sin caché, features v1/v2, ponderación y diagnósticos de carteras, y diagnóstico normalizado sin valor crudo.

## Pendientes y límites

- Las filas live históricas ya guardadas no se reescriben. Las filas antiguas sin versión se interpretan como v1 y no aportan las señales de volatilidad, ruptura y dirección al entrenamiento.
- La medida de 1153 muestras live y los valores reportados desde S1 son antecedentes proporcionados por Ramón; no se consultó S1 para esta corrección.
- No se ejecutó la suite completa ni se modificaron pantallas, rutas de API, política global Smart o grids normales.

## 25. Fase 41b — medición previa y consumidores (antes de producción)

### Tamaño de referencia

Medición en memoria con JSON UTF-8 compacto; no se abrió ninguna base de datos. El caso sintético contiene tres monedas, siete carteras (tres de una pieza, tres de dos y una de tres), recomendaciones completas de Advisor para tres márgenes, comparaciones Smart/Simple, ventanas de replay, señales y resultados. La revisión incluye el plan y alternativas dentro de `decision` y otra copia en `decision_choices.real_result`, como en el formato actual.

| Evento actual | Bytes | KiB | Duplicación identificada |
|---|---:|---:|---|
| `META_PLAN_EVALUATED` | 69.995 | 68,4 | 28.283 bytes de `advisor_recommendations` repetidas dentro de las alternativas; Advisor completo también aparece una vez por comparación |
| `META_OPERATOR_REVIEW_DECISION` | 150.743 | 147,2 | el plan anidado en `decision` ocupa 69.995 bytes; `real_result` vuelve a duplicar resultado, alternativa y plan |

El tamaño es una medición del fixture sintético documentado, no un promedio de producción. Se usa como línea base para comparar la serialización v2.

### Mapa de lecturas de la bitácora

| Lector | Archivo:línea | Datos consultados / compatibilidad requerida |
|---|---|---|
| Estado de aprendizaje y comparación | `grid/meta_operator.py:389`, `:409`, `:435` | Lee eventos de plan/revisión, `decision_choices` y versiones; conservar IDs, elecciones y resultados v1/v2. |
| Importación de muestras live desde planes históricos | `grid/meta_operator.py:1375`, `:1382` | Lee `META_PLAN_EVALUATED.alternatives`, `decision_id`, piezas, estimación y señales; se mantienen esas claves y su significado. |
| Construcción de alternativas y recomendaciones | `grid/meta_operator.py:820`, `:899` | Cada cartera adjunta `advisor_recommendations`; la respuesta de análisis debe continuar completa. |
| Persistencia del evento del plan | `grid/meta_operator.py:1211`, `:1212` | Escribe alternativas, decisiones, Advisor comparado, combinaciones, sombra, selección y bandas sigma. |
| Selección de elecciones de revisión | `grid/meta_operator.py:2419` | Usa alternativas del plan para calcular real, Scanner + Advisor y aprendida. No depende del evento ya persistido. |
| Escritura de revisión | `grid/meta_operator.py:2490`, `:2540`, `:2556` | El formato previo persistía decisión, valoración, sombra, `decision_choices.real_result` y toque; v2 conserva la elección referenciada sin duplicar el plan. |
| Cobertura y datos recientes de Inteligencia | `api/routes/intelligence.py:103-175`, `api/routes/intelligence.py:389-393` | Cuenta señales y devuelve `decision_choices` y el detalle de eventos; mantener compatibilidad con eventos antiguos y exponer claves v2. |
| Resultados de elecciones contra etiquetas | `api/routes/intelligence.py:316-330` | Cruza `decision_choices` con etiquetas usando `alternative_id`; debe seguir resolviendo versiones previas y nuevas. |
| Diagnóstico de omisiones de toque | `scripts/diagnostico_senales_meta.py:91-100`, `scripts/diagnostico_senales_meta.py:145-157` | Lee alternativas y `signals_omitted`; ambas se conservan en eventos v2. |
| Bitácora en Metas | `frontend/metas.js:402-430` | Renderiza tipo, símbolo/capital, pieza, decisión, motivo y sombra; no despliega alternativas completas. |
| Decisiones en Inteligencia | `frontend/intelligence.js:31`, `:76` | Muestra elecciones y permite expandir el objeto `details`; el visor JSON muestra el esquema compacto v2 enlazado al plan. |

No se encontró un renderizador de la lista completa de alternativas de la bitácora: el análisis HTTP continúa entregando el plan completo al operador/UI, mientras que el ledger genérico expone el JSON expandible. La v2 evita cambiar los campos visibles habituales y mantiene los campos que usa aprendizaje y diagnóstico.

Estado de esta parte: revisión y medición documentadas; todavía no se ha cambiado código de producción.

### Implementación 41b

- `grid/meta_operator.py:904` distingue planes de análisis (`analisis`) y de revisión (`revision`). La persistencia de `META_PLAN_EVALUATED` se prepara con `_plan_ledger_payload` (`:1242`) y usa `ledger_schema_version: 2`.
- Las alternativas del evento guardan `advisor_refs` con símbolo, margen y números de decisión (`grid/meta_operator.py:1219`); `advisor_comparisons` conserva la recomendación íntegra una sola vez por símbolo/margen. El plan de ejecución que devuelve el análisis conserva los asesores completos; la búsqueda, el ranking, los replays, las señales y las piezas abiertas no cambiaron.
- El evento v2 contiene `profile`, `target_usdt`, `deadline_days`, `capital_usage`, `selection_reason`, candidatos compactos (incluido precio si lo entrega Scanner), alternativas, `decision_choices`, combinaciones, sombra y bandas sigma (`grid/meta_operator.py:1242`). La sigma del Scanner se normaliza desde `sigma_24h_open` en ese mismo serializador. Se excluyen ventanas crudas ya resumidas y recomendaciones duplicadas.
- `META_OPERATOR_REVIEW_DECISION` guarda el resultado con campos decisorios y costos permitidos, `ledger_schema_version: 2`, y `plan_decision_id`; elimina el `real_result` duplicado de `decision_choices` (`grid/meta_operator.py:1268`, `:2492`, `:2530`, `:2548`). La respuesta de ejecución que recibe el llamador no se compacta.
- Las elecciones Real / Scanner + Advisor / Aprendida conservan `alternative_id`, decisión, símbolo y probabilidad. La API de Inteligencia sigue devolviendo detalles de eventos v1/v2; el diagnóstico de señales sigue leyendo `alternatives.signals_omitted`. Los campos consumidos se cubren en `tests/test_intelligence_api.py:38`, `tests/test_meta_learning_api.py:112` y `tests/test_meta_signal_diagnostics.py:72`.
- `grid/meta_operator.py:2211` diferencia el fallo al cargar velas (`velas no disponibles: …`) de la excepción del cálculo (`error al calcular toque: …`, línea `:2229`). La señal continúa siendo omitida en ambos casos.

La alternativa completa sigue accesible en el evento de plan, junto con la recomendación completa dentro de `advisor_comparisons`; cada recomendación de alternativa se puede localizar por `symbol` y `margin_target_pct`. La pantalla Metas usa campos superiores que se conservaron. El visor JSON expandible de Inteligencia refleja el esquema v2 y el enlace `plan_decision_id`; los lectores de etiquetas y las comparaciones usan `decision_choices` y `alternative_id` conservados. No se cambió frontend ni lógica decisoria.

### Pruebas y resultado

Comando dirigido final, con `ASPLE_OFFLINE=1` y carpeta temporal nueva:

`$testBase = Join-Path $env:TEMP 'asple-fase41b-20261026-03'; New-Item -ItemType Directory -Path $testBase | Out-Null; $env:ASPLE_OFFLINE='1'; .\venv\Scripts\python.exe -m pytest tests/test_meta_operator.py tests/test_intelligence_api.py tests/test_meta_learning_api.py tests/test_meta_signal_diagnostics.py -q --basetemp $testBase`

Resultado final de la suite enfocada: **82 passed, 10 warnings, 0 failed**, 10,68 s. Verificación posterior del archivo del operador: **67 passed, 4 warnings**, 8,45 s. Una corrida inicial tuvo 80 passed y una falla de harness: el stub de `review_once` no aceptaba el argumento `plan_kind`; se corrigió solo el stub. No se ejecutó suite completa. La prueba sintética verifica el límite de 150 KiB para los dos eventos, el ID de plan, conservación de elecciones y campos, y la compatibilidad v1/v2 de API y diagnóstico.

### Commits locales

- `520766e` — `Fase 41b-1: medir y localizar el inflado de bitácora`.
- `38bcffa` — `Fase 41b-2: compactar eventos de planes`.
- `dcf6c3e` — `Fase 41b-3: compactar decisiones y enlazar planes`.
- `d1b3656` — `Fase 41b-4: distinguir fallos de señal de toque`.
- `1956a2d` — `Fase 41b-2: normalizar datos de candidatos` (seguimiento: sigma y precio compactos).

Sin push. No se usó S1, Testnet, `.env` ni base real.

## Continuación 41b — tiempos, eventos livianos y diagnóstico (2026-10-26)

### Medición previa al cambio

Se usaron 51.840 velas sintéticas de 5 minutos (180 días), con precios deterministas y tres geometrías representativas. El microbenchmark aislado de `causal_touch_buy_to_sell` tardó 0,0029 s para tres llamadas en la primera geometría (incluye calentamiento de importación), 0,0002 s y 0,0001 s en las siguientes; sus resultados fueron tasas 0,25, 0 y `None`, respectivamente. La función existe en `24f2ba7:grid/meta_learning.py` y la ruta histórica ya tenía el cálculo causal directo por alternativa.

| Paso solicitado | Resultado disponible antes del cambio |
|---|---:|
| Velas generadas | 51.840 |
| Toque causal, geometría 1 (3 llamadas) | 0,0029 s |
| Toque causal, geometría 2 (3 llamadas) | 0,0002 s |
| Toque causal, geometría 3 (3 llamadas) | 0,0001 s |
| `_load_candles`, replays, Advisor, `_build_plan` integrales | No medidos en un arnés comparable |
| Cliente artificial con espera de 2 s y descarga fallida | No se completó el conteo temporal comparativo antes/después |
| Comparación integral `24f2ba7` vs revisión actual | Pendiente; no se afirma paridad de rendimiento |

Por tanto, el microbenchmark no confirma que el toque explique la duración real observada de 16–17 minutos ni permite certificar el objetivo de revisión ≤2 minutos. Sí identifica que el toque aislado es barato en este fixture; los costes de Advisor/replay y el tratamiento de una descarga fallida requieren medir el plan completo en un arnés equivalente. Esta limitación queda abierta, no se sustituye por una cifra inventada.

### Cambios

- `grid/meta_operator.py:286-289, 1903-1930`: resultado causal de toque cacheado por moneda, día UTC y geometría cuantizada a 0,01 % relativo; fallos de descarga cacheados por moneda durante 15 minutos. La caché negativa conserva el error para el diagnóstico y vuelve a intentar al vencer.
- `grid/meta_operator.py:1267`: el payload v2 de `META_PLAN_EVALUATED` ahora conserva `timings_s`, incluidos tiempo de toque y aciertos de caché que el plan ya calculaba.
- `grid/meta_operator.py:2529-2595`: los `META_OPERATOR_NOOP` se compactan con `_compact_review_result`, valoración, `plan_decision_id` y esquema 2. Se mantienen `decision` y `reason` arriba para el lector actual de `frontend/metas.js:417-430`.
- `grid/meta_operator.py:2676-2683`: `META_OPERATOR_ROTATION_EVALUATED` conserva únicamente identificadores, símbolo, estrategia y estimación de las alternativas en vez de guardar planes completos. Los lectores de persistencia usan `alternative_key`, `material`, `streak` y `review_id`.
- `scripts/diagnostico_senales_meta.py:81,92,208`: muestras, bitácora y predicciones comparan fechas con `datetime(...)` de SQLite; acepta marcas con espacio o `T`.

### Pruebas

Comando final: `$testBase = Join-Path $env:TEMP 'asple-fase41b-continuacion-20261026-03'; New-Item -ItemType Directory -Path $testBase | Out-Null; $env:ASPLE_OFFLINE='1'; .\venv\Scripts\python.exe -m pytest tests/test_meta_operator.py tests/test_meta_signal_diagnostics.py tests/test_intelligence_api.py tests/test_meta_learning_api.py tests/ui/test_metas_ui.py -q --basetemp $testBase`

Resultado: **102 passed, 12 warnings, 0 failed, 29,66 s**. Una corrida previa detectó cinco fallos de harness/código nuevo; se corrigieron antes de la corrida final. Las pruebas nuevas cubren caché negativa y su vencimiento, igualdad de señal al reutilizar la caché, payload NOOP menor de 15 KiB, conservación de `timings_s` y timestamp del mismo día con formato espacio. No se ejecutó suite completa. No se usó S1, Testnet, `.env` ni la base real.

### Estado y limitaciones

Implementación y pruebas dirigidas: hechas. Comparación completa de `_build_plan` con `24f2ba7`, medición de llamadas con retraso artificial de 2 s y certificación del límite de 2 minutos: no hechas; deben medirse antes de considerar cerrado el objetivo de rendimiento.

## Fase 41b-11 — revisión resistente a una alternativa actual ausente (2026-10-27)

### Parte 1: conservar la decisión real

La evidencia de 39 fallos en S1 fue aportada por Ramón; no se consultó S1. En `_active_review_choices`, `current` puede ser `None` cuando el plan no contiene una alternativa de una pieza del símbolo y estrategia actuales. La expresión original llamaba igualmente a `current.get(...)` al preparar la comparación, después de tomar la decisión real y antes de escribirla.

- `grid/meta_operator.py:2473`: la comparación usa `(current or {}).get("alternative_id")`. Si falta la alternativa, agrega `current_alternative_missing: true` y `current_alternative_missing_reason`: motivo de exclusión/filtro del candidato, moneda ausente o ausencia de estructura para la estrategia actual.
- `grid/meta_operator.py:2554`: `_safe_active_review_choices` captura errores de comparación, registra el traceback con `logger.exception` y conserva la elección real junto con `decision_choices_error: "Tipo: mensaje"`. `_review_once_locked` (`:2612`) y `_record_review` (`:2635`) guardan el evento de revisión y el NOOP que corresponda con ese diagnóstico. No se modifican la asignación, las reglas ni el resultado real.
- Lectores revisados: `_review_choice_label` (`:2464`) ya acepta `None`; `_review_allocations` (`:2791`) ya protege `current_alternative` ausente al calcular el plazo; `_rotation_gate` (`:2697`) ya retorna antes de acceder a una alternativa sin estimación; `_consider_piece_reconfiguration` (`:2960`) selecciona `current` después de exigir al menos dos candidatas. Se conservan esas protecciones. En `_active_review_choices` también se protege un `estimate` explícitamente nulo al leer la probabilidad de la sombra.

Pruebas: `tests/test_meta_review_resilience.py` reproduce un grid JUP Smart cuyo plan solo contiene XRP Simple, verifica revisión y NOOP persistidos, y fuerza una excepción de comparación en ambas rutas de registro. También comprueba que una acción real ya decidida se conserva sin crear un NOOP y compara dos revisiones normales con la función original extraída del commit local `dd10892` (resultados y eventos completos idénticos). La prueba de rotación sin alternativa actual verifica la protección existente.

Comando: `$env:ASPLE_OFFLINE='1'; $testBase = Join-Path $env:TEMP ('asple-41b11-' + [guid]::NewGuid().ToString('N')); .\venv\Scripts\python.exe -m pytest tests/test_meta_review_resilience.py tests/test_meta_operator.py -q --basetemp $testBase`

Resultado: **77 passed, 4 warnings, 0 failed; 9,22 s**. Commit de la parte 1: `1c68a60`. Sin suite completa, JavaScript modificado, red, S1, Testnet, `.env` ni base real.

### Parte 2: evaluar la pieza propia sin autoconflicto

El plan comprobaba cada opción como apertura nueva; las ventas vivas y la plaza del grid propio volvían inviable su propia alternativa. `_build_plan` (`grid/meta_operator.py:1004`) obtiene los vínculos vigentes mediante `MetaCore._owned_grids` (tabla `meta_piece_links`), exige estado ACTIVE/PAUSED/OPENING/HOLDING y el mismo símbolo. Al comprobar esa opción (`:1031`) pasa `replacing_grid_id` y `include_held_proceeds=True`, como en la evaluación de cierre y reasignación. Si hay varias piezas propias del mismo símbolo, se evalúa como reemplazo solo la de menor ID; las demás siguen contando como conflicto y ocupación.

`option["testnet_openability"]` y el candidato del plan guardan `own_grid_excluded_from_check`; el dato también se conserva en la alternativa pública de una pieza (`grid/meta_operator.py:1101`) y en el candidato compacto de `META_PLAN_EVALUATED` (`:1267`). Los filtros de libro, notional, pasos, capital y conflictos ajenos siguen pasando por la misma función. El chequeo de ocupación (`:781`) descuenta una plaza únicamente si el estado del grid reemplazado forma parte de `count_open_grids`; HOLDING no libera una plaza porque la consulta ya lo excluye.

Se estima el producto neto de la eventual venta de inventario para evaluar un reemplazo; no se ejecuta una venta ni se acredita efectivo real en el análisis. Al actuar, las rutas de rotación vuelven a validar (`grid/meta_operator.py:2854`, `:2879`, `:2910`); retirar compras conservando ventas mantiene `include_held_proceeds=False`. Los otros llamados revisados son Iniciar (`:2399`) y agregar una pieza con efectivo libre (`:3132`): ambos abren una pieza adicional y deben seguir contando todos los grids existentes. No se les pasa un grid a excluir.

Pruebas nuevas: `tests/test_meta_own_grid_evaluation.py:99` usa el exchange falso con ventas JUP vivas y comprueba los cuatro estados propios; en ACTIVE/PAUSED ejecuta la revisión y verifica que la rotación recibe la estimación actual. Los casos de `:123` y `:132` conservan conflictos con grids de otra meta/manuales e incluso con una segunda pieza coincidente después de excluir la propia. Los de `:140` y `:151` cubren ocupación máxima, HOLDING y uso de efectivo estimado únicamente al evaluar liquidación. `:160` compara el plan completo (excepto duraciones), probabilidades y decisión con `_build_plan` del commit local `dd10892` cuando no hay pieza propia para excluir.

Comando final: `$env:ASPLE_OFFLINE='1'; $testBase = Join-Path $env:TEMP ('asple-41b11b-' + [guid]::NewGuid().ToString('N')); .\venv\Scripts\python.exe -m pytest tests/test_meta_own_grid_evaluation.py tests/test_meta_review_resilience.py tests/test_meta_operator.py -q --basetemp $testBase`

Resultado final: **87 passed, 4 warnings, 0 failed; 4,89 s**. Una primera corrida de esta parte dio 10 fallos nuevos por faltar `avg_price` en el contexto del exchange falso; se completó ese fixture y se repitió en otra carpeta temporal. Las 77 pruebas previas siguieron verdes. No se ejecutó suite completa ni se modificó JavaScript. Todo se guardó en UTF-8 sin BOM y LF; sin interrogaciones corruptas dentro de palabras en los documentos tocados.

## Fase 41c-0 — tiempo de selección de cartera (2026-10-27)

### Escenario y medición

`tests/meta_portfolio_benchmark.py` ejecuta `_build_plan` contra la implementación del commit local `a062e09` y contra la actual. Usa seis candidatas, cuatro elegibles, 51.840 velas sintéticas de 5 min por símbolo y latencia real de 50 ms por lectura del exchange/BD falsos (`time.sleep`, medida con `time.perf_counter`). Representa una revisión con Advisor y replay calientes, como la evidencia aportada: 135 aciertos de Advisor y 297 respuestas de replay representadas por el fixture. La descarga, las simulaciones y el servicio Advisor reales no se ejecutan en este benchmark; el toque causal vivo y la política de selección sí. El costo del Scanner real de S1 no está representado por este Scanner determinista.

El código conserva la lista corta de tres monedas y sus repartos: genera 100 carteras, 297 alternativas de pieza y 894 ajustes de probabilidad. Las «~950 combinaciones» del prompt no son 950 carteras finales bajo estos límites; no se amplió la lista corta ni se alteraron los repartos para forzar ese número.

Comandos de medición (salida JSON bajo `$env:TEMP`):

- `$env:ASPLE_OFFLINE='1'; .\venv\Scripts\python.exe -m tests.meta_portfolio_benchmark --revision a062e09 --latency 0.05 --output (Join-Path $env:TEMP 'asple41c-baseline-a062e09-20261027.json')`
- `$env:ASPLE_OFFLINE='1'; .\venv\Scripts\python.exe -m tests.meta_portfolio_benchmark --revision working --latency 0.05 --output (Join-Path $env:TEMP 'asple41c-optimizado-20261027.json')`

| Paso (segundos) | `a062e09` | Optimizado |
|---|---:|---:|
| Total `_build_plan` | 148,258523 | 2,240180 |
| `_testnet_openability` | 129,513010 | 0,736279 |
| `_candidate_alternatives` | 17,413347 | 0,194317 |
| `_apply_intelligence` (incluido en candidatas) | 15,028938 | 0,166104 |
| `allocation_vectors` | 0,000271 | 0,000120 |
| `combine_aligned_replays` | 0,029627 | 0,016055 |
| `combine_piece_signals` | 0,002646 | 0,001800 |
| `signal_adjusted_probability` (incluye llamadas anidadas) | 0,008431 | 0,002667 |
| `rank_alternatives` | 0,001655 | 0,000453 |
| Ensamblado de bucles, excluyendo etapas temporizadas | No separado internamente en v2 | 0,002156 |

Los tiempos inclusivos de etapas anidadas no deben sumarse. La decisión posterior tardó 0,151260 s en el escenario optimizado: análisis más decisión = **2,391440 s**, por debajo de 5 min. Esto verifica el objetivo en el escenario sintético de caché caliente; no certifica un tiempo de S1 ni una corrida con replays fríos.

| Lectura simulada (incluye la decisión posterior) | Antes | Después |
|---|---:|---:|
| Todas las lecturas exchange/BD instrumentadas | 2934 | 44 |
| `get_open_orders` | 324 | 3 |
| `get_balance` | 243 | 1 |
| `list_grids_by_status` | 1215 | 5 |
| `count_open_grids` | 243 | 1 |
| `get_grid_levels` | 82 | 2 |
| `get_latest_prediction` | 297 | 3 |

La causa confirmada en este escenario son las lecturas repetidas por opción, sobre todo los chequeos de apertura, seguidas por la predicción de dirección leída en cada alternativa. La aritmética de probabilidades y la combinación de ventanas no dominan el tiempo.

### Cambio y paridad

`grid/meta_operator.py:915` reutiliza lecturas por fuente, método y argumentos durante un solo plan; devuelve copias para evitar mutaciones del snapshot. `_build_plan` (`:938`) instala un `ContextVar` aislado por llamada/hilo y lo descarta en `finally`, también ante excepciones. Cubre saldo, órdenes, libros/contexto de mercado, grids por estado, niveles, conteo, grid reemplazado, snapshot de modelo y predicción por símbolo. Fuera del plan se llaman los proveedores de nuevo: las revalidaciones de ejecución no reutilizan ese snapshot.

`_plan_timed` (`grid/meta_operator.py:928`) mide etapas internas. `timings_s.portfolio_detail` (`:1253`) guarda armado residual de combinaciones (`:1152`), candidatas, comprobaciones de apertura, mezcla de replays/señales, probabilidades, ranking e inteligencia, además de lecturas por método. `plan_read_calls` y `plan_read_cache_hits` registran 29 lecturas únicas del contexto y 2890 reutilizaciones en la última medición; estos contadores no equivalen al total de llamadas internas de los proveedores. El bloque ya se persiste mediante `META_PLAN_EVALUATED` v2.

`tests/test_meta_portfolio_performance.py` compara el plan completo (excluyendo solo duraciones) con `a062e09`, usando IDs fijos: alternativas, probabilidades, orden, selección y decisión iguales. También comprueba menos lecturas, renovación entre planes y ejecución, y descarte del contexto tras una excepción. Las pruebas anteriores del operador, revisión y autoconflicto siguen verdes.

Comando dirigido: `$env:ASPLE_OFFLINE='1'; $testBase = Join-Path $env:TEMP ('asple-41c0-' + [guid]::NewGuid().ToString('N')); .\venv\Scripts\python.exe -m pytest tests/test_meta_portfolio_performance.py tests/test_meta_operator.py tests/test_meta_own_grid_evaluation.py tests/test_meta_review_resilience.py -q --basetemp $testBase`

Resultado: **90 passed, 4 warnings, 0 failed; 6,02 s**. Sin suite completa, JavaScript modificado, S1, Testnet, `.env` ni base real.

## Fase 41c-1 — curvas de cartera y corrección de C6 (2026-10-27)

### Criterio de evaluación

Ramón confirmó el criterio del núcleo (grid/meta.py:704-718): el primer éxito ocurre cuando el efectivo atribuible sumado de todas las piezas, más el capital inicial aún no asignado, alcanza capital_inicial + target_usdt. El inventario no cuenta para cumplir la meta. En paralelo, la curva de patrimonio suma efectivo e inventario valorado al cierre con una comisión estimada de venta del 0,1 %. Esa curva determina el corte por pérdida máxima, el avance mediano, el drawdown y la peor pérdida. La pérdida máxima se compara con el capital inicial y, si se toca antes del éxito o en el mismo punto, prevalece el corte. Se conservan por pieza ambas curvas de efectivo y patrimonio cada 15 minutos; no se guardan velas en la base de datos. Esta precisión corrige el criterio del punto 3 del prompt: el éxito es por efectivo atribuible, mientras el patrimonio mide riesgo y avance, según el núcleo vigente.

### Implementación y compatibilidad

grid/sim/meta_portfolio.py:8-65 empaqueta curvas float32 con base64 y agrega las piezas en marcas de tiempo comunes; el efectivo no asignado se incorpora como saldo constante. :68-108 simula grids y bots sin objetivo ni corte de pérdida por pieza y captura efectivo y patrimonio. El bot queda en DONE tras vender. grid/sim/runner.py habilita el trazado cada 15 minutos solo para este replay Meta; los grids normales conservan su ejecución. grid/sim/meta_bot.py permite el trazado sin detener la trayectoria al alcanzar un objetivo individual.

grid/meta_operator.py:2143-2158 usa el simulador de curva para las evaluaciones por pieza; combine_aligned_replays suma curvas alineadas y aplica el criterio conjunto a las carteras. La estimación no suma éxitos separados de ventanas o piezas.

grid/meta_learning.py cambia el método base a rolling_7d_v3_equity. La versión forma parte de la plantilla/hash de replay; resultados persistidos v2 sin curvas se recalculan y no se mezclan con v3. Las etiquetas y versiones históricas no se borran. Las muestras y modelos de aprendizaje con versiones incompatibles se excluyen del ajuste combinado. La simulación de aprendizaje usa el mismo criterio: efectivo para éxito, patrimonio para avance y corte por pérdida.

### Comparación sintética de cinco carteras

Velas sintéticas de 5 minutos, tres ventanas de 20 días por alternativa, paso de 7 días, filtros de símbolo ficticios y simuladores reales. «Avance mediano» es el avance de patrimonio como porcentaje del objetivo de 3 USDT; puede superar 100 % cuando el patrimonio avanza más que el objetivo. No es una promesa de rendimiento.

| Cartera | Método | Éxito | Avance mediano (% del objetivo) | Días medianos al éxito |
|---|---:|---:|---:|---:|
| JUP Smart 70 | v2 | 100 % | 122,50 % | 0,71 |
| JUP Smart 70 | v3 | 100 % | 3.392,65 % | 4,70 |
| JUP Smart 70 + ZRO Simple 30 | v2 | 100 % | 177,23 % | 1,36 |
| JUP Smart 70 + ZRO Simple 30 | v3 | 100 % | 5.068,21 % | 3,71 |
| JUP Smart 70 + bot XRP 20 | v2 | 0 % | 71,23 % | — |
| JUP Smart 70 + bot XRP 20 | v3 | 100 % | 3.398,69 % | 4,70 |
| XRP Simple 30 + JUP Smart 70 | v2 | 100 % | 143,28 % | 1,05 |
| XRP Simple 30 + JUP Smart 70 | v3 | 100 % | 4.835,25 % | 4,70 |
| JUP Smart 50 + ZRO Simple 30 + bot XRP 20 | v2 | 100 % | 136,31 % | 1,03 |
| JUP Smart 50 + ZRO Simple 30 + bot XRP 20 | v3 | 100 % | 4.099,16 % | 3,71 |

Artefacto reproducible: $env:TEMP\asple41c1-carteras-20261027.json. Harness: tests/meta_portfolio_curve_benchmark.py. La diferencia de avance entre v2 y v3 responde a los objetivos individuales frente a la evaluación conjunta sin objetivo por pieza. Estas trayectorias sintéticas verifican el método; no estiman resultados de mercado.

### Pruebas y resultado

Comando: $env:ASPLE_OFFLINE='1'; $testBase = Join-Path $env:TEMP ('asple-41c1-' + [guid]::NewGuid().ToString('N')); .\venv\Scripts\python.exe -m pytest tests/test_meta_portfolio_equity.py tests/test_meta_learning.py tests/test_meta_operator.py tests/test_meta_learning_api.py tests/test_grid_sim_runner.py tests/test_meta_own_grid_evaluation.py tests/test_meta_review_resilience.py tests/test_meta_portfolio_performance.py -q --tb=short --basetemp $testBase

Resultado: 143 passed, 9 warnings, 0 failed; 7,56 s. Cubre efectivo/patrimonio agregados, capital sin asignar, corte de pérdida antes de recuperación, timestamps comunes, curvas de grid cada 15 minutos, bot DONE, grid cada 15 minutos, migracion de version, rechazo de caché v2, versionado de plantilla/aprendizaje y regresiones dirigidas.

Medición sintética de plan con caché caliente: 6 candidatas, 4 elegibles, 100 combinaciones, 297 alternativas de pieza y 894 cálculos de probabilidad. Análisis 5,07 s, decisión 0,16 s, total 5,23 s con lecturas ficticias de 50 ms. No usa S1 ni red. Los 148,26 s de 41c-0 provienen de otro fixture caliente y se conservan como referencia separada.

No se cambió el cumplimiento real ni los límites de operación. La Fase 41c-2 queda fuera del alcance.


## Fase 41b-12 — estimación de la pieza actual con su geometría real (2026-10-27)

- `grid/meta_operator.py:1052, :1065-1141` recoge grids propios ACTIVE/PAUSED y evalúa cada pieza por separado con `_evaluate` (`:1087`), la geometría y capital actuales, replay `rolling_7d_v3_equity` y caché del plan. El Advisor se obtiene de la caché o del servicio (`:1094-1117`) y `_apply_intelligence` aplica las señales (`:1119`).. Esta ruta no consulta `_testnet_openability`.
- `grid/meta_operator.py:1328` incluye `current_pieces[grid_id]` en el plan; `:1405-1417` compacta rango, niveles, capital, estimación, probabilidades y método para `META_PLAN_EVALUATED` v2 (`:1441`). Un error de replay se conserva en el plan como error de esa pieza y deja disponible el fallback existente.
- `grid/meta_operator.py:2635-2648` adjunta la estimación a `decision_choices` y solo marca la alternativa actual como ausente si tampoco hay estimación propia. `:2856-2894` usa la geometría viva en `_rotation_gate`; `:2985-2991` la pasa a la comparación de revisión y `:3143-3155` la usa como base del reajuste. Las alternativas destino siguen filtradas por Testnet y su chequeo de apertura no cambia.
- La diferencia es deliberada: «pieza actual» es una simulación de mantener la geometría ya abierta, no una nueva apertura; «alternativa» es un posible destino y debe poder abrirse ahora en Testnet. Por eso un grid ajeno puede excluir JUP Smart como destino sin borrar la estimación del JUP Smart propio.
- `frontend/metas.js:439-445` presenta la estimación actual en la bitácora de Metas. `frontend/intelligence.js:34-37` la muestra en el detalle de decisiones de Inteligencia; la API ya transmite `decision_choices` y no requirió cambios.

### Pruebas y rendimiento

- `tests/test_meta_own_grid_evaluation.py:140-188`: escenario sintético JUP Smart propio, JUP ajeno HOLDING con ventas coincidentes y libro de Testnet unilateral para JUP. La pieza actual recibe estimación con geometría 90–110, cuatro niveles y 70 USDT; JUP Smart no aparece entre destinos y el chequeo de apertura conserva el conflicto. La revisión compara y registra la estimación sin `current_alternative_missing`.
- `tests/test_meta_portfolio_performance.py:67-74`: control de tiempo contra la referencia documentada 41c-0 de 2,39 s, con tolerancia máxima de 10 %.
- Pruebas focalizadas del operador y resiliencia: `tests/test_meta_own_grid_evaluation.py`, `tests/test_meta_operator.py`, `tests/test_meta_review_resilience.py`: **88 passed, 4 warnings, 0 failed; 5,47 s**.
- UI de Metas e Inteligencia: `tests/ui/test_metas_ui.py`, `tests/ui/test_intelligence_ui.py`: **16 passed, 2 warnings, 0 failed; 28,56 s**.
- Benchmark actual 41c-0, lectura ficticia de 50 ms, 100 combinaciones: plan **2,232 s**, decisión **0,152 s**, total **2,384 s**; replay de pieza propia incluido (**0,0002 s**). Frente a 2,39 s: **−0,26 %**, dentro del límite de +10 %. `tests/test_meta_portfolio_performance.py`: **4 passed, 0 failed; 3,56 s**.
- `node --check frontend/metas.js` y `node --check frontend/intelligence.js`: correctos. No se ejecutó suite completa ni se usaron S1, Testnet, `.env`, red o BD real.


Corrida final conjunta de cierre (operador, benchmark, Metas e Inteligencia): **108 passed, 6 warnings, 0 failed; 33,58 s**, con `ASPLE_OFFLINE=1` y basetemp nuevo bajo TEMP.

## Fase 41f — medición del orden intravela (2026-10-27)

- `scripts/medir_orden_intravela.py:82` compara el simulador existente a 5m y 1m para Smart, Simple y tres bots (compra a −0,5 %, −1,5 % y −3 %, venta a +0,95 %). Para la geometría de grid replica el rango centrado moderado del Advisor (σ 72 h, objetivo de toque 30 %, amplitud máxima 45 %) y llama al selector compartido `grid.structure.select_levels`, con capital 70 USDT y margen mínimo 0,3 %. La σ viene de velas 5m y en modo Binance los filtros son los públicos del símbolo; CSV sintético usa los filtros genéricos del runner. El flujo de medición está en `:170`; los conteos de ambigüedad/toque exacto en `:126`.
- La simulación usa `run_simulation` y `run_meta_bot` existentes. La comparación no cambia el simulador: al replay 1m se le pasa σ ausente explícitamente para permitir ese intervalo con el mismo motor; no se activan ADJUST, stop-loss ni cierres por tiempo. Los ciclos y PnL final marcado a mercado se miden sobre 70 USDT. La probabilidad de alcanzar +3 USDT se calcula en ventanas de 20 días, paso 7 días, sobre capital 100 USDT y el criterio cash/efectivo del runner.
- Sobre velas 5m, las velas ambiguas son contactos en una vela con compra pendiente y su venta ambas dentro de `[low, high]`; una vela cuenta una vez aunque abarque varios niveles. Los toques exactos cuentan por orden viva (`low == compra` o `high == venta`) y permanecen sin fill por la desigualdad estricta del simulador.
- CSV: guardar `SIMBOLO_5m.csv` y `SIMBOLO_1m.csv` con columnas `timestamp,open,high,low,close` e ISO-8601 UTC. Salida: `medicion_intravela.json` y `medicion_intravela.csv`.
- Ejecución posterior en S1 (datos públicos, sin claves): `python scripts/medir_orden_intravela.py --symbols JUPUSDT,ZROUSDT,XRPUSDT --days 30 --source binance --out ./medicion_intravela/`. El cliente público respeta `HTTPS_PROXY` del entorno. Como el wrapper del repo no incluye `1m` en su lista de intervalos, el script pagina ese intervalo por la misma instancia pública y su política de reintentos. Esta fase no ejecutó ese comando ni realizó solicitudes de red.
- Regla de lectura: si la diferencia absoluta de probabilidad 5m/1m es menor de 2 puntos porcentuales, se deja el simulador sin cambios; si supera 2 pp, se prepara un prompt separado para evaluar una corrección. El resultado de una sola trayectoria sintética no decide esa corrección.
- Corrida sintética local de 3 símbolos × 30 días, 15 geometrías, CSV: terminó en **37,83 s**, bastante menos que 10 min. Datos planos con excursiones repetidas: 30 días y 2 ventanas por geometría. Resultado ilustrativo, no evidencia de mercado: grids 64 ciclos por resolución, PnL 2,972/2,972 USDT y P(+3) 100 %/100 %; bots 1/1 ciclo, PnL entre 0,447 y 0,453 USDT, P(+3) 0 %/0 %. Cada geometría contó 4.320 velas ambiguas y 0 contactos exactos. Diferencia de probabilidad: 0 pp en este fixture.
- Pruebas finales: `tests/test_measure_intracandle_order.py:27,44,54,90,120`: **5 passed, 0 failed; 5,83 s**, `ASPLE_OFFLINE=1`, basetemp temporal nuevo. Cubren ciclo intravela 1m vs 5m, toque exacto sin fill, aislamiento de BD/`.env`, descarga 1m con cliente falso y ejecución CSV con escritura JSON/CSV. No se ejecutó la suite completa.

## Fase 41c-2a — restauración de capital_usage (2026-10-27)

`_plan_ledger_payload` conserva `capital_usage` en `META_PLAN_EVALUATED` v2. La prueba C15 comprueba explícitamente `initial_capital_usdt`, `used_usdt`, `unused_usdt`, `reason` y `best_higher_capital_alternative`. No se reprodujo la pérdida en el árbol de `ea6dc6d`: la proyección ya copiaba el bloque; esta parte agrega una regresión sin alterar el cálculo. Prueba original: `tests/test_meta_operator.py`, 72 passed, 4 warnings; 14,35 s.

## Fase 41c-2b — motor de asignación ampliada en sombra (2026-10-27)

- `grid/meta_allocation.py:7,23` contiene el desempate propio de la asignación y la selección pura de una pieza adicional. Compara curvas v3 sumadas de las piezas actuales con cada alternativa; exige +2 puntos de probabilidad, o empate dentro de ±2 puntos y +10 % de avance mediano. Evalúa capital desde el mínimo de apertura, en pasos del 5 % del capital inicial, con topes de efectivo, moneda, grids y bots.
- `grid/meta_operator.py:980,1048,1300,1377,1393,1429,1497` construye candidatos a partir de geometrías que pasaron el filtro de Testnet. Conserva el ID de la alternativa de origen, limita a tres geometrías por moneda y tipo y vuelve a comprobar una apertura adicional sin excluir grids propios. Las curvas se reproducen con `_evaluate` para cada monto; no se extrapola linealmente la curva de otro capital. Si falla el replay o el chequeo de apertura de un escalón, se omite ese escalón sin detener las demás monedas.
- El resultado queda fuera de `alternatives` como `allocation_shadow`: decisión, mejor opción, delta de probabilidad, avance, días, drawdown, top cinco y motivo. `META_PLAN_EVALUATED` v2 incluye la sombra y `timings_s.allocation_shadow`.
- `tests/test_meta_allocation.py` cubre +3 pp, +1 pp con avance suficiente, mejora insuficiente, empeoramiento, topes de efectivo/moneda/tipo de pieza, filtro de apertura y desempate. `tests/test_meta_own_grid_evaluation.py` verifica la correspondencia de IDs y el resultado del filtro, y mantiene la comparación exacta de los campos previos del plan.
- Benchmark sintético: capital inicial 100 USDT, grid actual de 70 USDT y 30 USDT libres; 36 alternativas, curvas v3, caché caliente y lecturas ficticias de 50 ms. Análisis: 3,355 s; decisión: 0,202 s; total: 3,558 s; asignación ampliada: 0,032716 s. La sombra seleccionó agregar. El coste medido queda bajo el límite de 30 s; no certifica el tiempo con datos de mercado.

## Fase 41c-2c — sombra, interruptor y pantallas (2026-10-27)

- `grid/meta.py:25,52` declara `use_extended_allocation=False` por perfil. `grid/meta_operator.py:406,427,449` registra cambios con actor y fecha; `api/routes/metas.py:133` expone `PUT /api/metas/allocation/switch`. `tests/test_meta_learning_api.py:78` verifica apagado inicial y auditoría.
- Con el interruptor apagado, `grid/meta_operator.py:3267` registra `META_OPERATOR_ALLOCATION_SHADOW` si hay efectivo atribuible suficiente y conserva intacta la ruta previa `_add_piece_from_free_cash`. La prueba de paridad usa `dd10892`: los campos previos se comparan exactamente salvo `timings_s`, que ya estaba excluido. Solo se permiten campos nuevos nombrados y afirmados; Testnet/openability conserva sus chequeos; se afirma que `allocation_shadow` no aparece dentro de `plan["alternatives"]`.
- Con el interruptor encendido, el operador usa la asignación ampliada, vuelve a validar Testnet y abre con `_open_replacement`; una prueba con exchange falso comprueba la pieza ligada y el motivo. La activación requiere confirmación y actor por perfil.
- `frontend/metas.js:626,675` muestra el control por perfil y la sombra reciente en Metas. `api/routes/intelligence.py:93,394` entrega las últimas diez decisiones y `frontend/intelligence.js:65` las presenta en Inteligencia.
- Verificación final dirigida: **122 passed, 12 warnings, 0 failed; 31,50 s**. `node --check frontend/metas.js`, `node --check frontend/intelligence.js` y `py_compile` de los módulos Python tocados finalizaron correctamente. No se ejecutó la suite completa ni se usaron S1, Testnet, red, `.env` o base real.

## Fase 41c-2d — geometría por monto y descartes de la asignación en sombra (2026-10-27)

- `grid/meta_operator.py:873-978` permite que el generador de alternativas compruebe un plazo límite opcional usado únicamente por la sombra. `:980-1090` ordena candidatos por probabilidad base, solicita `_candidate_alternatives` para cada monto, conserva la geometría, niveles y margen que devuelve el Advisor para ese capital y vuelve a comprobar apertura en Testnet. El contexto del plan comparte las cachés de lectura, Advisor y replay existentes.
- El presupuesto de sombra es 30 s. Se comprueba antes y después de llamadas al Advisor/replay y antes de comenzar cada monto/alternativa. Al agotarse se detiene el trabajo pendiente y se anota el conteo no evaluado. Una llamada síncrona individual ya iniciada no puede cancelarse; su duración puede causar un sobrepaso puntual del límite.
- Cada descarte agrega símbolo, tipo de pieza, monto, etapa y motivo. `allocation_shadow.descartes` resume conteos por motivo, hasta 10 ejemplos y `no_evaluados`; las excepciones de Advisor, Testnet y replay quedan registradas. El payload de `META_OPERATOR_ALLOCATION_SHADOW` incluye el resumen y el efectivo libre real.
- `frontend/metas.js:300` identifica el uso de capital como perteneciente a la mejor cartera propuesta desde cero. En la meta activa, `:493` y `:799` identifican aparte el efectivo libre real; los detalles de asignación sombra muestran ambas referencias.
- La sombra sigue sin seleccionar aperturas reales cuando el interruptor por perfil está apagado. La prueba `tests/test_meta_own_grid_evaluation.py` compara `_review_allocations` con la implementación de HEAD; también cubre geometría generada para los montos de 5,5–30,5 USDT, causa del mínimo de celda y agotamiento del presupuesto. `tests/test_meta_allocation.py` conserva las reglas de selección de cartera.
- Verificación dirigida final: `tests/test_meta_own_grid_evaluation.py tests/test_meta_allocation.py`: **23 passed, 0 failed; 1,07 s**. `tests/ui/test_metas_ui.py`: **15 passed, 2 warnings, 0 failed; 25,15 s**. `node --check frontend/metas.js` sin errores. El primer pase de Python antes del ajuste de expectativas tuvo dos fallos: cambió a agregar la variante sombra de monto compatible, y el test de paridad llamó la firma del método con `valuation` duplicado; ambos quedaron corregidos y pasaron en la corrida final.
- No se ejecutó la suite completa ni se usaron S1, Testnet, red, `.env` o base real. La revisión de descartes y el diseño por monto permanecen en sombra.

## Fase 41b-13 — mantenimiento de la base de datos Meta

### 13a — Retención automática de cachés

- `grid/meta_operator.py:291,326,876` versiona Advisor (`advisor_relative_v1`) y toque (`touch_signal_v2`); replay conserva `rolling_7d_v3_equity`. La lectura persistida rechaza versiones viejas antes de que corra el limpiador.
- La retención por defecto es de 7 días y se edita junto con `ledger_plan_detail_days` (14) en `GET/PUT /api/metas/config/maintenance` (`grid/meta.py:164,179`; `api/routes/metas.py:128`). El limpiador corre al iniciar y cada día a las 09:30 UTC. Se omite si revisión, precálculo o aprendizaje están ocupados. Conserva cachés del día actual; elimina versiones obsoletas y entradas vencidas mediante lotes de 100.
- `meta_learning_replay_cache` no guarda la versión: se infiere reconstruyendo IDs de plantilla con `stable_template`. Si aún no hay muestras, solo se aplica la edad. Los resultados se registran en el log y como `META_MAINTENANCE` ligado a la última meta; sin metas se conserva solo el log.

### 13b — Resumen de planes antiguos

- `grid/meta_operator.py:431,479` transforma planes v2 de más de 14 días a esquema 3 (`ledger_detail=resumido`), en transacciones de hasta 20 filas. Retiene campos decisorios, diez alternativas mejor clasificadas y todas las referenciadas por decisiones, selección o muestras; quita `advisor_comparisons` y ejemplos de descartes.
- Antes de compactar, importa de forma idempotente las muestras legacy. Guarda `alternatives_total` para Inteligencia (`api/routes/intelligence.py:231`) y `signals_omitted_summary` para que el diagnóstico conserve las mismas cuentas (`scripts/diagnostico_senales_meta.py:150`).

### 13c — herramienta manual

- `scripts/mantenimiento_bd_meta.py:19-430` exige `--db` y usa SQLite explícito. Sin `--apply` abre en modo de solo lectura, informa ahorros estimados por evento/tabla y confirma SHA-256 sin cambios. `--apply` compacta eventos v1, importa muestras pendientes, aplica retención y registra el mantenimiento; cada lote de hasta 20 eventos o 100 cachés se confirma de forma atómica.
- `--vacuum` exige `--apply` en la misma llamada, respaldo del día o `--backup-ok`, servicio parado según `systemctl is-active asple-predictor`, e `integrity_check` correcto antes y después. La bandera `--i-know-service-is-stopped` solo se usa en pruebas.
- Procedimiento para S1 (no ejecutado): desde `/home/ubuntu/asple-predictor`, detener `asple-predictor` y comprobar estado inactivo; establecer `DB="$PWD/asple_predictor.db"` solo tras verificar la ruta real sin abrir `.env`; copiar con `mkdir -p "$HOME/respaldos"; cp -a "$DB" "$HOME/respaldos/asple_predictor_$(date +%F).db"`; comparar ambos hashes con `sha256sum`; inspeccionar con `./venv2/bin/python scripts/mantenimiento_bd_meta.py --db "$DB"`; aplicar con `--apply`; revisar el resultado y ejecutar `--apply --vacuum --backup-ok`; iniciar el servicio, verificar estado activo y ejecutar `sqlite3 "$DB" "PRAGMA integrity_check;"`. Si dry-run/apply falla, no continuar con VACUUM.
- Pruebas finales: `tests/test_meta_db_maintenance.py`, `tests/test_meta_operator.py`, `tests/test_meta_signal_diagnostics.py`, `tests/test_intelligence_api.py`, `tests/test_app_settings_api.py` y `tests/test_meta_core.py`: **127 passed, 14 warnings, 0 failed; 8,68 s**. No se ejecutó la suite completa ni se usaron S1, Testnet, red, `.env` o BD real.



## Fase 41-G1a — compresión exacta de caché v3

grid/sim/meta_portfolio.py:7-50 versiona las curvas como formato 2 y comprime bytes float32 con zlib y byte shuffle. Conserva marcas cada 15 minutos y cada bit de efectivo/patrimonio. El lector admite el formato anterior. grid/meta_operator.py:2582-2585 regenera las curvas persistidas antiguas; no borra filas. Una curva sintética de 180 días/17.280 puntos mide 84.350 bytes JSON frente a 138.240 bytes de arrays sin comprimir. tests/test_meta_portfolio_equity.py:21-53 compara bits, timestamps, métricas y lectura legacy: 13 passed, 0 failed, 0,37 s.

## Fase 41-G1b — medición intravela con geometría común

scripts/medir_orden_intravela.py:136-148,255-305 calcula con velas 5m y sigma causal una geometría por simulación/ventana y la aplica igual a 1m. Exporta same_geometry; discrepancias son error. Bots usan el precio 5m inicial. tests/test_measure_intracandle_order.py:27-59,151-158 valida ciclo ambiguo/geometría: 8 passed, 0 failed, 5,52 s.

## Fase 41-G1c — valoración desconocida y concurrencia

grid/meta.py conserva símbolo, estado y motivo disponibles; grid/meta_operator.py registra diagnóstico en bitácora/log. Inteligencia agrega conteos de 24 h/7 días y causas (api/routes/intelligence.py:170-192,385; frontend/intelligence.js:74-77). La regla de cancelar y no mover capital permanece.

La evidencia adicional de S1 de Ramón muestra lecturas KNOWN junto a revisiones clasificadas como desconocidas; la concurrencia del watchdog es hipótesis. El operador separa Meta inactiva de valoración desconocida; ante valor no KNOWN espera 10 s y reintenta una vez. Los estados, símbolo y motivos de ambos intentos quedan en META_OPERATOR_REVIEW_DECISION y META_OPERATOR_NOOP, y en el log. Si persiste UNKNOWN no opera. Primera revisión a +80 s del arranque: unos 20 s después del primer tick de MetaCore a 60 s; intervalo posterior de 15 min. Se conserva ops_lock.

Pruebas de API/UI/MetaCore: 110 passed, 5 warnings, 11,61 s. Operador/resiliencia: 86 passed, 4 warnings, 22,38 s; repetición final del operador: 80 passed, 4 warnings, 20,36 s. No se consultó S1.

## Fase 41-G1d — cobertura activa de dirección

API, Inteligencia y scripts/diagnostico_senales_meta.py separan cobertura activa P(baja) ≥0,60 de cobertura general. El aprendizaje conserva la semántica: una predicción menor es señal neutral 0, sigue presente y cuenta en la muestra; no se altera extractor ni decisión. Pruebas de diagnóstico/API/UI: 10 passed, 2 warnings, 0,42 s. node --check frontend/intelligence.js correcto.

## Fase 41-G1e — XRP «sin estado»

grid/meta_operator.py:2448-2485 recibe None cuando no existe fila; complete requiere estado lista y pronóstico reciente. database/db_manager.py:1412-1425 no crea fila; Coin Registry la inicia al consultar/registrar moneda (api/routes/coins.py:83-86). «Sin estado» indica registro de onboarding ausente, no defecto estadístico; Prudente debe excluirlo. No se cambió comportamiento.

## Fase 41-G1f — verificador local para S1

scripts/verificar_s1_meta.py abre SQLite mode=ro con query_only=ON, sin DBManager ni .env. Resume HEAD/hora, metas y piezas, valoración persistida, current_pieces, decisiones/rotación/asignación, timings, tamaños/dbstat/cache, corrida de aprendizaje, señales y opcionalmente journalctl. tests/test_meta_s1_diagnostic.py usa esquema sintético, verifica salida y rechazo de escrituras: 1 passed, 0 failed, 0,17 s.

El precálculo diario no deja evento persistido; se informa no registrado y la fecha de caché solo como referencia. Comando S1: venv2/bin/python scripts/verificar_s1_meta.py --db asple_predictor.db --horas 2 --journal. No se ejecutó allí ni contra BD real.

## Bloque 2 — estudios con replay histórico (G2)

Nuevo scripts/estudios_meta.py guarda JSON+CSV bajo --out y ejecuta lecturas SQLite mode=ro. Calibración de dirección: predictions/outcomes para model_a_down 1h, interpretando probability_up como P(baja); calcula Brier, log-loss, AUC, deciles, acierto @0,60 y mejor umbral por símbolo. Calibración de grid carga datos públicos 5m/1m con BinanceClient público y HTTPS_PROXY del entorno, lee grid/levels/events en mode=ro y aplica una única geometría de la BD a ambas resoluciones.

La prueba tests/test_estudios_meta.py valida métricas con datos sintéticos, aislamiento de escritura, salida JSON/CSV y grid con cliente simulado: **4 passed, 0 failed, 0,98 s**. No se ejecutó ningún subcomando contra red o BD real.

Límite de 41e/41h: el runner existente es por lotes y no expone un estado serializable/reanudable para conservar órdenes e inventario mientras se recentra cada 15 minutos. El subcomando replay-activo informa el replay pasivo como referencia y marca active_status=not_modeled; persistencia marca cada valor not_modeled. No se fabrican resultados activos a partir de corridas segmentadas que reiniciarían capital e inventario. Para completarlos hace falta un adaptador de simulación estado-continuo dentro del alcance de estudio, sin alterar simuladores de producción.

Comandos de S1, después de copiar datos/archivos de salida al directorio previsto:
- Replay pasivo de referencia y estado activo explícito: HTTPS_PROXY=http://127.0.0.1:3128 venv2/bin/python scripts/estudios_meta.py replay-activo --symbols JUPUSDT,ZROUSDT --days 180 --profile RAPIDO --source binance --out ./estudios_meta/
- Calibrar grid 13 con mercado público: HTTPS_PROXY=http://127.0.0.1:3128 venv2/bin/python scripts/estudios_meta.py calibrar-grid --grid-id 13 --db asple_predictor.db --out ./estudios_meta/
- Calibrar dirección: venv2/bin/python scripts/estudios_meta.py calibrar-direccion --db asple_predictor.db --days 30 --out ./estudios_meta/

Para repetir el estudio intravela de 90 días tras G1b: HTTPS_PROXY=http://127.0.0.1:3128 venv2/bin/python scripts/medir_orden_intravela.py --symbols JUPUSDT,ZROUSDT,XRPUSDT --days 90 --source binance --out ./medicion_intravela/. Si la diferencia absoluta de probabilidad 5m/1m es <2 puntos porcentuales en 11 ventanas, dejar el simulador igual; si excede 2 pp, preparar evaluación separada. No se descargaron velas durante esta fase.

## Bloque 3 — señales con sentido y bots de toque en sombra (G3)

### 3a — calibración de `model_a_down`

El subcomando `scripts/estudios_meta.py calibrar-direccion` lee `predictions` y `outcomes` en SQLite `mode=ro` para `model_a_down` 1h. Por símbolo informa distribución de P(baja), Brier, log-loss, AUC, deciles, acierto en 0,50/0,60 y mejor umbral empírico. Mantiene la advertencia de uso Spot: la señal puede servir para evitar compras o gestionar inventario; no implica ventas cortas. No se ejecutó contra una BD real, así que la conclusión de rendimiento queda pendiente en S1. Prueba sintética incluida en G2.

### 3b — Scanner y Advisor centrados

`grid/meta_operator.py` agrega `centered_signal_snapshot`: por revisión, para cada señal usa mediana y MAD escalada por 1,4826, con piso de escala 0,1; centra y limita a [-1,1]. Calcula `signals_centered` y `probability_adjusted_centered` en campos separados, conserva intactos `signals_used` y la probabilidad ajustada real, y persiste ambas en `meta_learning_samples.signals_json`. Inteligencia compara log-loss y Brier de ambas probabilidades solo cuando madura la etiqueta. `grid/meta.py` agrega `use_centered_signals=False` por perfil; `use_touch_bots=False` se reserva igualmente por perfil. Son indicadores de configuración/sombra, no activan decisiones reales en esta fase.

### 3c — bots de toque

En cada revisión, con monedas presentes en alternativas elegibles, se prueban compras a 0,5; 0,75; 1; 1,5; 2; 2,5 y 3 % por debajo del precio. La venta se fija para dejar 0,95 % neto tras 0,1 % de comisión en compra y venta. La tasa se calcula con `causal_touch_buy_to_sell` y velas causales de los 30 días anteriores; para la estimación 72 h se usa `touch_stats` con ese horizonte. La mejor tasa de 24 h selecciona la propuesta y se guardan ambas tasas en `META_BOT_TOUCH_SHADOW`. Nunca se llama al motor de apertura.

El job diario guarda `META_BOT_TOUCH_SHADOW_LABEL` cuando madura cada horizonte 24/72 h. La etiqueta requiere compra y una venta posterior, en vela distinta, y registra tiempos. Inteligencia presenta propuestas y tasas esperadas frente a las observadas. Es evidencia basada en velas OHLC; no identifica orden de movimientos dentro de una misma vela de 5 minutos.

### Regresión y verificación

Comparé con `5710a9f` los cuerpos de `signal_adjusted_probability`, `_apply_intelligence`, `_review_allocations`, `_consider_piece_reconfiguration` y `_rotation_gate`: los cinco son idénticos. La prueba de centrado conserva `signals_used`, probabilidad real y resultado del ajuste real; la prueba del bot cubre selección de tasa/objetivo neto y etiqueta buy→sell con velas sintéticas.

Comando final dirigido, `ASPLE_OFFLINE=1`, basetemp temporal nuevo: `tests/test_estudios_meta.py tests/test_meta_learning.py tests/test_meta_operator.py tests/test_intelligence_api.py tests/ui/test_intelligence_ui.py tests/test_meta_core.py`: **147 passed, 5 warnings, 0 failed; 49,53 s**. `node --check frontend/intelligence.js` y `py_compile` de Python terminaron sin errores. No se ejecutó suite completa.

### Operación y límites pendientes

Comandos manuales para S1, desde `/home/ubuntu/asple-predictor`; requieren proxy público y se deben ejecutar allí solo cuando Ramón lo decida:

- Dirección: `venv2/bin/python scripts/estudios_meta.py calibrar-direccion --db asple_predictor.db --days 30 --out ./estudios_meta/`.
- Grid 13: `HTTPS_PROXY=http://127.0.0.1:3128 venv2/bin/python scripts/estudios_meta.py calibrar-grid --grid-id 13 --db asple_predictor.db --out ./estudios_meta/`.
- Replay pasivo de referencia: `HTTPS_PROXY=http://127.0.0.1:3128 venv2/bin/python scripts/estudios_meta.py replay-activo --symbols JUPUSDT,ZROUSDT --days 180 --profile RAPIDO --source binance --out ./estudios_meta/`. La salida marca `active_status=not_modeled`; no debe leerse como replay activo.
- Persistencia 1/2 queda `not_modeled` hasta construir adaptador con estado continuo.
- Intravela 90 días: `HTTPS_PROXY=http://127.0.0.1:3128 venv2/bin/python scripts/medir_orden_intravela.py --symbols JUPUSDT,ZROUSDT,XRPUSDT --days 90 --source binance --out ./medicion_intravela/`. Diferencia <2 pp con 11 ventanas conserva el simulador; mayor diferencia requiere una fase de evaluación separada.

No se usaron S1, Testnet, red, `.env` ni BD real durante esta implementación. El Bloque 2 queda parcial por el replay activo y el estudio de persistencia no modelados; la calibración empírica de dirección y los datos públicos intravela requieren corrida manual en S1.

### Seguimiento 1c — serialización y motivo por moneda (C17, 2026-10-27)

La inspección confirmó que `MetaCore.run_once` conserva `MetaCore._lock` y puede solicitar `monitor.ops_lock` al cumplir meta o cortar por pérdida/plazo. La revisión, a su vez, conserva `ops_lock`. La serialización inicial de `valuation` con `MetaCore._lock` añadía el ciclo inverso; C17b la sustituye por `_depth_read_lock`, dedicado a lectura del libro y caché.

Los estados desconocidos ahora devuelven `reason` agregado por símbolo y `symbol_valuations` con causa, edad de lectura, niveles bid/ask y mejores precios; las piezas conservan el motivo de su moneda. Un libro sin bids queda explícito como “libro sin compradores”. El calendario de MetaCore queda fijo en cada minuto UTC, segundo 0; el operador, cada 15 minutos UTC, segundo 20, por lo que el desfase no depende de la hora de reinicio. No se cambió la regla: una valoración UNKNOWN sigue bloqueando acciones y requiere los dos intentos ya implementados.

Pruebas de regresión: dos llamadas concurrentes de valoración consultan el libro una vez y ambas terminan KNOWN; una profundidad sin bids registra causa por moneda; las pruebas verifican los segundos 0/20 de ambos jobs. Archivos afectados `grid/meta.py`, `grid/meta_operator.py`, `tests/test_meta_core.py` y `tests/test_meta_operator.py`. La comparación con `5710a9f` para las cinco funciones decisorias de G3 sigue idéntica.

Verificación C17 dirigida de `tests/test_meta_core.py tests/test_meta_operator.py`: **115 passed, 4 warnings, 0 failed; 51,42 s**. El primer intento del conjunto final encontró un mock de prueba que no aceptaba un argumento posicional; corregido el mock, el archivo y el conjunto final pasaron. No fue un fallo de producción.

### Seguimiento 1c — C17b: candado hoja para valoración

`MetaCore.valuation` ya no adquiere `MetaCore._lock`. `_depth_read_lock` protege solo la lectura/actualización de `_depth_cache` y la llamada del cliente al libro; se libera antes de actualizar estado o registrar eventos, y bajo él no se solicita `ops_lock` ni `core._lock`. Se conservan los motivos por moneda, el reintento único a los 10 s y el desfase de jobs UTC (:00 para MetaCore, :20 para revisión).

**Caminos con varios candados revisados:** (1) `MetaOperator._review_once_locked` toma `ops_lock` solo durante la revalidación y llama a `core.valuation`, que usa únicamente `_depth_read_lock`; no toma `core._lock`. (2) `MetaCore.run_once` conserva `core._lock`; la valoración usa y libera `_depth_read_lock`, y el camino de objetivo/pérdida/vencimiento puede luego adquirir `monitor.ops_lock` (`_target_met`, `_close_owned`, `_resume_close`). En los cierres, la lectura final de deslizamiento ocurre después de liberar `ops_lock`, aunque todavía dentro de `run_once`. (3) `MetaCore.cancel` y `GridMonitor.run_once` toman `ops_lock` sin `core._lock`. No se mantiene `_depth_read_lock` al entrar en esos caminos. La regla para nuevas anidaciones es `ops_lock → core._lock → _depth_read_lock`; la ruta existente del watchdog conserva `core._lock` mientras toma `ops_lock`, por lo que debe permanecer sin una arista inversa `ops_lock → core._lock`.

La prueba nueva fuerza el entrecruzamiento descrito: hilo A conserva `ops_lock` y valora; hilo B entra a `run_once`, conserva `core._lock` y espera `ops_lock` al alcanzar el cierre por objetivo. Se repite 50 veces con pausas aleatorias; ambos hilos deben terminar dentro de 5 s. Comando: `$env:ASPLE_OFFLINE='1'; .\venv\Scripts\python.exe -m pytest tests/test_meta_core.py tests/test_meta_operator.py -q --tb=short --basetemp "$env:TEMP\asple-f41-c17b-20261027-184500"`: **116 passed, 4 warnings, 0 failed; 17,95 s**.

### Seguimiento 1c — C17c: edad de lectura de la caché de profundidad

La causa raíz informada de C17 era la diferencia entre la edad de la entrada cacheada y la edad real del libro: `executable_value` conserva su tope de 60 s, pero el caché podía reutilizar un libro casi en ese mismo límite. Ahora `DEPTH_CACHE_TTL_S = 20` y la reutilización se decide con `now - book.read_at`; `cached[0]` ya no determina vigencia. Si falta `read_at`, es futuro o tiene 20 s o más, se vuelve a leer. El límite de valoración `max_age_s=60`, el candado hoja, el reintento y los horarios quedan iguales.

Pruebas: libro con `read_at` de 59,9 s se consulta de nuevo y devuelve KNOWN; libro de 10 s se reutiliza y devuelve KNOWN. Comando dirigido: `$env:ASPLE_OFFLINE='1'; .\venv\Scripts\python.exe -m pytest tests/test_meta_core.py tests/test_meta_operator.py -q --tb=short --basetemp "$env:TEMP\asple-f41-c17c-20261027-194200"`: **118 passed, 4 warnings, 0 failed; 18,87 s**.


### Fase 41-G1g1 — filtros reales y aislamiento de muestras

`grid/meta_learning.py:628` acepta `filters_by_symbol` y lo propaga a `simulate_piece_curve`; `grid/meta_operator.py:2175` obtiene filtros mediante `_symbol_filters` y los entrega a reconstrucción, recálculo de bases y etiquetado diario (`grid/meta_operator.py:2309`). Si no están disponibles, se registra el fallback al filtro genérico y su símbolo.

La geometría inválida se guarda como etiqueta terminal `invalid_geometry`, con símbolo, low, high, niveles, tick usado y error (`grid/meta_learning.py:680`, `grid/meta_operator.py:2155`). El entrenamiento omite esas muestras (`grid/meta_operator.py:2252`). Cada muestra del ciclo diario está protegida individualmente; el fallo incrementa `samples_failed`, guarda hasta 20 detalles y el bucle continúa (`grid/meta_operator.py:2512`).

Pruebas: `tests/test_meta_learning.py` y `tests/test_meta_operator.py`; comando `$env:ASPLE_OFFLINE='1'; .\venv\Scripts\python.exe -m pytest tests/test_meta_learning.py tests/test_meta_operator.py -q --tb=short --basetemp "$env:TEMP\asple-f41-g1g1-20261028-091000"`: **112 passed, 4 warnings, 0 failed; 21,97 s**. Una corrida anterior tuvo un fallo de fixture al asumir una muestra única; el selector quedó limitado por `sample_key` y la corrida final pasó. La regresión fijada a a25818b conserva decisión y probabilidad. Sin S1, Testnet, red, `.env` ni BD real.

El conteo de etiquetas históricas cuyo símbolo tiene tick distinto de 0,0001 no se pudo obtener sin consultar la BD real, expresamente excluida. En este checkout no existe comando de re-etiquetado con `--dry-run`; no se propone uno.

### Fase 41-G1g2 — motivos de bloqueos transitorios

`grid/meta.py:747` guarda en `META_PIECES_BLOCKED` el diagnóstico de cada símbolo, incluido error de lectura, estado, edad y si se reutilizó la caché (`grid/meta.py:593`). Una excepción al consultar profundidad queda convertida en diagnóstico por moneda. Al recuperar KNOWN, si el último bloqueo fue `valuation_unknown`, se registra `META_PIECES_UNBLOCKED` con duración (`grid/meta.py:755`). El contador de pérdida sigue reiniciándose mientras la valoración es DESCONOCIDO y no se permite abrir piezas.

`scripts/verificar_s1_meta.py:72` resume en modo de solo lectura los bloqueos de las últimas 24 h y agrupa motivos. Pruebas: `tests/test_meta_core.py` y `tests/test_meta_s1_diagnostic.py`; comando `$env:ASPLE_OFFLINE='1'; .\venv\Scripts\python.exe -m pytest tests/test_meta_core.py tests/test_meta_s1_diagnostic.py -q --tb=short --basetemp "$env:TEMP\asple-f41-g1g2-20261028-100500"`: **37 passed, 0 failed; 8,51 s**. DESCONOCIDO bloquea y registra causa; KNOWN restaura ACTIVE y registra duración de 60 s. Sin S1, Testnet, red, `.env` ni BD real.

### Fase 41-G1g3 — formato de curvas y caché durable compacta

`grid/sim/meta_portfolio.py:7` sube `CURVE_FORMAT_VERSION` a 3. `pack_curve` codifica sin pérdida cada float32 como XOR de sus bits uint32 consecutivos antes de zlib; `unpack_curve` conserva lectura de formatos 1 y 2 y reconstruye v3 bit a bit (`grid/sim/meta_portfolio.py:10`, `:33`).

Medición sintética: 180 días × 96 marcas/día, 11 ventanas alineadas de 15 min. La entrada formato 2 ocupó 505.634 bytes: 504.310 curvas y 1.324 bytes de resto JSON (493,8 KiB). El formato 3 completo ocupó 636.326 bytes: 635.122 bytes de curvas y 1.204 bytes de resto. Delta XOR es reversible, pero para esta geometría no comprime mejor por sí solo. Se aplica retención: `_persistent_cache_set` conserva métricas y omite curvas al escribir caché replay durable (`grid/meta_operator.py:962`); entrada resultante 1.127 bytes (1,10 KiB), bajo 100 KB. Con 883 filas como referencia, la estimación ilustrativa es 0,95 MB si ese volumen representa las siete jornadas retenidas. Las curvas completas permanecen en memoria durante el análisis para `combine_aligned_replays` (`grid/meta_operator.py:182`, `:1643`) y `evaluate_extended_allocation` (`grid/meta_allocation.py:30`). Al recuperar una entrada durable sin curvas, se invalida y se vuelve a simular; no se pierden datos ni cambian métricas, a costa de repetir replay tras reinicio.

No se aplicó deduplicación porque no se garantizó igualdad exacta en ventanas realistas. `meta_learning_samples.parameters_json` guarda perfil y geometría reducida (`grid/meta_operator.py:1988`); `provenance_json` guarda conteos y resumen de `base_replay`, no curvas (`grid/meta_operator.py:2012`). Un ejemplo sintético de esos tres JSON sumó 1.261 bytes. No se pudo atribuir ni medir los 61 MB reportados en la BD de S1; no se compactaron muestras.

Pruebas: `tests/test_meta_portfolio_equity.py` y `tests/test_meta_operator.py`; comando `$env:ASPLE_OFFLINE='1'; .\venv\Scripts\python.exe -m pytest tests/test_meta_portfolio_equity.py tests/test_meta_operator.py -q --tb=short --basetemp "$env:TEMP\asple-f41-g1g3-20261028-111500"`: **99 passed, 4 warnings, 0 failed; 21,64 s**. Igualdad bit a bit v2/v3, métricas, decisión de asignación en sombra y lectura v1 verificadas.

### Fase 41-G1g4 — descartes y tiempos de asignación en sombra

`grid/meta_operator.py:1314` garantiza un motivo para entrada vacía y alternativas sin piezas/ventanas; se cuentan descartes en mínimos, topes, límites del perfil, preparación, Advisor, replay y Testnet, con hasta 10 ejemplos. El generador pasa callback de diagnóstico al Advisor y replay (`grid/meta_operator.py:1183`). `grid/meta_allocation.py:20` mide orden y combinaciones de curvas y respeta un deadline compartido de 30 s. Si se agota, el estado es `budget_exceeded`, con pendientes y ejemplos; `allocation_shadow.descartes.timings_s` separa carga/orden, variantes, Testnet, combinación y total.

Pruebas: `tests/test_meta_own_grid_evaluation.py`, `tests/test_meta_allocation.py`, `tests/test_meta_operator.py`; comando `$env:ASPLE_OFFLINE='1'; .\venv\Scripts\python.exe -m pytest tests/test_meta_own_grid_evaluation.py tests/test_meta_allocation.py tests/test_meta_operator.py -q --tb=short --basetemp "$env:TEMP\asple-f41-g1g4-20261028-132000"`: **109 passed, 4 warnings, 0 failed; 17,24 s**. Entrada vacía con motivo, deadline, regeneración por monto, resultado sombra sin cambios y decisión/probabilidades contra a25818b.

Límite: la sombra aún se invoca sincrónicamente al final de `_build_plan`; el deadline limita el trabajo, pero no demuestra que una sombra lenta nunca demore la revisión. Separarla exige un contrato de resultado pendiente y no cambiar la disponibilidad inmediata de `allocation_shadow` en esta fase.

### Fase 41-G1g5 — salida resumida del verificador

`scripts/verificar_s1_meta.py:40` conserva el modo SQLite `ro`, resume cada señal con mínimo, mediana, máximo, valores distintos y nulos; `--detalle` imprime la lista completa. Incluye última corrida, fallos e `invalid_geometry`, bloqueo transitorio, tamaños por versión de caché, bytes por campo JSON de muestras de aprendizaje y descartes/tiempos de asignación en sombra en 24 h.

Prueba con BD sintética: `tests/test_meta_s1_diagnostic.py`; comando `$env:ASPLE_OFFLINE='1'; .\venv\Scripts\python.exe -m pytest tests/test_meta_s1_diagnostic.py -q --tb=short --basetemp "$env:TEMP\asple-f41-g1g5-20261028-140500"`: **1 passed, 0 failed; 0,22 s**. La salida por defecto queda bajo 150 líneas, `--detalle` conserva los valores y la BD no cambia.


### Fase 41-G1g6 — cierre y auditoría de codificación

La sección existente `Fase 41-G1a` en `REPORTE_FASE41.md:358-360` ya estaba correctamente escrita en UTF-8, sin BOM y sin signos `?` dentro de palabras; no hizo falta modificarla. La misma comprobación se ejecutó sobre los tres Markdown de fase, incluido el texto nuevo. El verificador de codificación lee UTF-8, falla ante BOM o `?` entre letras y terminó con 0 fallos. No se ejecutaron nuevas pruebas de código en G1g6.

Commits de esta continuación, en orden:

1. `Fase 41-G1g1: filtros reales y aislamiento de muestras`
2. `Fase 41-G1g2: diagnóstico de bloqueos transitorios`
3. `Fase 41-G1g3: curvas v3 y caché compacta`
4. `Fase 41-G1g4: descartes y tiempos de la sombra`
5. `Fase 41-G1g5: resumen del verificador Meta`
6. `Fase 41-G1g6: cierre y auditoría de codificación`

Comandos de consulta local en S1 (el script fuerza SQLite `mode=ro` y `PRAGMA query_only=ON`; no usa `.env`, API ni Testnet):

```sh
cd ~/asple-predictor
venv2/bin/python scripts/verificar_s1_meta.py --db asple_predictor.db --horas 24
venv2/bin/python scripts/verificar_s1_meta.py --db asple_predictor.db --horas 24 --detalle
```

No hay comando de re-etiquetado con `--dry-run` en este checkout. Las etiquetas anteriores a los filtros por símbolo podrían requerir recálculo para muestras de monedas cuyo tick difiere de 0,0001; el número no se midió porque la BD real está excluida. No borrar ni recalcular etiquetas hasta implementar y revisar un modo de previsualización realmente de solo lectura.

| Parte | Archivos y referencias | Prueba dirigida | Resultado | Decisión real con las mismas entradas |
|---|---|---|---|---|
| G1g1 | `grid/meta_learning.py:628-711`; `grid/meta_operator.py:2155-2252,2309-2512` | `tests/test_meta_learning.py`, `tests/test_meta_operator.py` | 112 passed; geometría inválida terminal; ciclo sigue con otras muestras | No inmediata; las etiquetas corregidas pueden afectar versiones de pesos futuras |
| G1g2 | `grid/meta.py:593,747-767`; `scripts/verificar_s1_meta.py:72-93` | `tests/test_meta_core.py`, `tests/test_meta_s1_diagnostic.py` | 37 passed; motivo por símbolo y duración | No |
| G1g3 | `grid/sim/meta_portfolio.py:7-48`; `grid/meta_operator.py:962-981` | `tests/test_meta_portfolio_equity.py`, `tests/test_meta_operator.py` | 99 passed; igualdad exacta, caché durable 1,10 KiB sintética | No; puede recalcular replay tras reinicio |
| G1g4 | `grid/meta_operator.py:1183-1439,1888-1928`; `grid/meta_allocation.py:20-150` | `tests/test_meta_own_grid_evaluation.py`, `tests/test_meta_allocation.py`, `tests/test_meta_operator.py` | 109 passed; paridad de probabilidades y campos decisorios contra a25818b | No en la comparación; la sombra sigue síncrona y un deadline agotado puede dejar evaluación parcial |
| G1g5 | `scripts/verificar_s1_meta.py:40-245` | `tests/test_meta_s1_diagnostic.py` | 1 passed; salida por defecto menor de 150 líneas | No |
| G1g6 | `REPORTE_FASE41.md`, `DISCREPANCIAS_FASE41.md`, `DECISIONES_MODO_META.md` | Control Python UTF-8/BOM/interrogación incrustada | 0 fallos | No |

### Fase 41-C25a — Probabilidad de ruptura Meta v2

`grid/meta_learning.py:22-49` añade `meta_break_prob_v2`, separada de `grid.policy.break_prob`: horizonte 72 h, barreras en los límites del rango, escala de volatilidad 1,0 y versión de método 1. Precio fuera del rango devuelve 1; volatilidad cero dentro devuelve 0. No cambia el cálculo ni la decisión actuales.

Prueba: `tests/test_meta_learning.py` → **29 passed**.

### Fase 41-C25b — Ruptura v2 en sombra y ajuste por perfil

`grid/meta_operator.py:2980-3008,3078-3090` guarda `break_prob_v2` y recalcula `probability_adjusted_break_v2` con las mismas señales y pesos, sustituyendo únicamente `break_prob`. Por defecto `use_break_prob_v2=False` en ambos perfiles (`grid/meta.py:28,57`); la configuración por API usa `PUT /api/metas/config/profile` (`api/routes/metas.py:109-111`) y Laboratorio permite cambiarlo (`frontend/lab.js:11-42`). La alternativa y su procedencia se guardan en muestras live (`grid/meta_operator.py:2088-2103`).

Medición de fórmula: 100.000 evaluaciones sintéticas en 0,0573 s (0,57 microsegundos por llamada). Pruebas dirigidas: **129 passed, 7 warnings**; UI tras ampliar la prueba del interruptor: **4 passed, 2 warnings**. `node --check frontend/lab.js` pasó.

### Fase 41-C25c — Comparación madura de ruptura v2

`grid/meta_learning.py:748-770` añade a cada etiqueta de replay la salida del rango por pieza, comprobada con máximos y mínimos estrictamente fuera del rango en velas futuras. `api/routes/intelligence.py:387-448` compara etiquetas maduras de 72 h con instantánea v2: log-loss y Brier de probabilidad ajustada con ruptura actual frente a v2; también agrupa salidas por terciles y devuelve intervalo de fechas y estado de muestra. Con menos de 30 indica «sin datos suficientes». `frontend/intelligence.js:65-70,103` presenta las tablas. `scripts/verificar_s1_meta.py:246-277` resume mínimo, mediana, máximo, distintos y nulos de ambas señales.

La etiqueta usa máximos/mínimos futuros y cruce estricto (`< piso` o `> techo`); cuenta cada pieza del replay en el tercil de su alternativa. Pruebas dirigidas: **119 passed, 5 warnings**; `node --check frontend/intelligence.js` pasó.

### Fase 41-C25d — Poda de cachés del proceso

`grid/meta_operator.py:363-481` introduce `PROCESS_CACHE_MAX_ENTRIES=500`, lectura LRU y poda por día UTC para análisis, Advisor, velas y toque; la caché de fallos se limpia por expiración de su TTL de 15 min. `_build_plan` abre/cierra una región activa (`:1532`); no se poda mientras haya planes en curso. El plan incluye filas y KB estimados por caché (`:1952`) y `scripts/verificar_s1_meta.py:160-167` los muestra.

Medición sintética: 3 días × 15 alternativas por día. Antes de podar: análisis 45 filas/586,76 KB; Advisor 45/49,04 KB; velas 45/7.082,31 KB; fallos 15/3,63 KB; toque 45/36,17 KB. Tras podar al tercer día: análisis 15/195,59 KB; Advisor 15/16,35 KB; velas 15/2.360,77 KB; fallos 15/3,63 KB; toque 15/12,06 KB. Son estimaciones del fixture, no tamaños medidos en S1.

Pruebas dirigidas: **88 passed, 4 warnings**. Se cubrieron poda diaria, LRU, no podar durante el plan, estadísticas guardadas y probabilidad idéntica tras retirar caché obsoleta.

### Fase 41-C25e — Caché durable de replay descartada

`grid/meta_operator.py:1052-1054` deja de escribir entradas durables de `replay` sin curvas. En lectura, solo suma acierto cuando el candidato conserva curvas y formato vigente; las filas métricas antiguas se descartan, aumenta `replay_persistent_cache_discarded` y el replay se recalcula (`grid/meta_operator.py:2854-2879`). Los aciertos de caché de Advisor y toque siguen su ruta.

Prueba: `tests/test_meta_operator.py` → **87 passed, 4 warnings**. Las filas durables viejas sin curvas no se cuentan como hit y las curvas válidas sí.

### Fase 41-C25f — Vista previa y marcado de etiquetas antiguas

`scripts/reetiquetar_meta.py:14-160` abre la base en `mode=ro` para la vista previa (predeterminada), cuenta etiquetas con `labeled_at` anterior al corte de G1g1 y compara el tick por símbolo cuando aparece en `provenance_json` o `parameters_json`. Si falta, el estado queda desconocido; `--exchange-info` consulta filtros públicos con el cliente sin credenciales. `--apply` requiere respaldo fechado hoy o `--backup-ok`, conserva la etiqueta anterior en el JSON y solo marca `superseded`; nunca recalcula ni elimina.

`grid/meta_operator.py:2319-2340,2425-2428,2525-2531,2644-2650` vuelve pendientes las etiquetas superseded para el job diario, reemplaza su contenido al recalcular y las excluye del entrenamiento. `api/routes/intelligence.py:229-233,458-464` y `grid/meta_operator.py:845-846` no las cuentan en métricas ni comparaciones.

Pruebas: `tests/test_meta_relabel.py tests/test_meta_learning.py tests/test_meta_core.py tests/test_intelligence_api.py` → **72 passed, 2 warnings**. Cubre hash idéntico en dry-run, bloqueo de apply sin respaldo, marcado selectivo y reemplazo diario. No se inspeccionó una BD real; no hay tick persistido en los campos consultados del esquema local.

### Fase 41-C25h — Replay activo aproximado y persistencia de reajuste

`scripts/meta_active_study.py:1-150` implementa un adaptador aislado de estudio que encadena segmentos de `run_simulation`, decide solo en cierres de 15 minutos, liquida el tramo con comisión estimada de venta del 0,1 % y pasa el efectivo al siguiente. Mantiene el ancho relativo y los niveles, y aplica cooldown y máximo diario tomados de `grid/meta.py:DEFAULT_PROFILES`. `scripts/estudios_meta.py:127-218,319-378` reemplaza `not_modeled` en `replay-activo` y agrega `persistencia-reajuste` (N=1/N=2), CSV, JSON y resumen de probabilidad de +3 USDT con `evaluate_portfolio` (éxito por efectivo; pérdida y avance por patrimonio).

La salida declara siempre la limitación: política simplificada por salida de rango; reajuste = liquidar y reabrir; no modela rotación a otra moneda ni decisión por probabilidad. Los comandos de S1 son solo instrucciones y no se ejecutaron.

Prueba: `tests/test_estudios_meta.py` → **6 passed**. Casos: sin salida de rango activo idéntico al pasivo, efectivo continuo tras liquidación, cooldown y máximo de cuatro por 24 h, N=2 igual o posterior a N=1. Sin red ni base real.

Lectura del estudio: si la probabilidad activa N=1 supera a la pasiva en al menos 2 pp sin peor drawdown, vale la pena modelar el reajuste real; si N=2 no pierde frente a N=1, conservar persistencia 2.

### Fase 41-C25g — Cierre y comandos de consulta

La ruptura v2 estima la probabilidad de salir del rango a 72 h con barreras en el rango (margen 0 %) y escala de volatilidad 1,0. Las 72 h ofrecen una etiqueta temprana; las etiquetas maduras a 20/45 días sirven para validación posterior. El método queda aislado de `grid.policy.break_prob`, y el peso `break_prob` permanece en 0,25. Para recomendar activación se requieren al menos 30 etiquetas maduras, mejora tanto de log-loss como de Brier frente al cálculo actual y terciles de salidas ordenados. El sistema no activa el interruptor.

Comandos preparados para S1; no ejecutados desde este entorno:

```bash
cd ~/asple-predictor
venv2/bin/python scripts/verificar_s1_meta.py --db ./asple_predictor.db --horas 24
venv2/bin/python scripts/reetiquetar_meta.py --db ./asple_predictor.db --dry-run
HTTPS_PROXY=http://127.0.0.1:3128 venv2/bin/python scripts/estudios_meta.py replay-activo --symbols JUPUSDT,ZROUSDT,XRPUSDT --days 180 --profile RAPIDO --source binance --out ./estudios_meta/
HTTPS_PROXY=http://127.0.0.1:3128 venv2/bin/python scripts/estudios_meta.py persistencia-reajuste --valores 1,2 --symbols JUPUSDT,ZROUSDT,XRPUSDT --days 180 --profile RAPIDO --source binance --out ./estudios_meta/
```

Los dos primeros scripts abren SQLite con `mode=ro`. `reetiquetar_meta.py` queda en vista previa; `--apply` requiere respaldo del día o `--backup-ok` tras verificarlo. Los estudios públicos descargan velas y escriben resultados en `./estudios_meta/`.

Commits de C25: `eaa9906` (a), `3a550ce` (b), `09cb68b` (c), `6efdc97` (d), `7e71e71` (e), `8f51cee` (f), `f493add` (h) y este commit (g). Sin push.

| Parte | Archivos principales | Verificación | Resultado | Cambia decisiones reales |
|---|---|---|---|---|
| C25a | `grid/meta_learning.py:22-49` | `tests/test_meta_learning.py` | 29 passed | No; cálculo aislado |
| C25b | `grid/meta_operator.py:2980-3090`, `grid/meta.py:28,57`, API/UI | pruebas operador, core, Inteligencia y Laboratorio; `node --check frontend/lab.js` | 129 passed; UI 4 passed | No; interruptor permaneció apagado |
| C25c | `grid/meta_learning.py:748-770`, `api/routes/intelligence.py:387-448`, UI/verificador | pruebas aprendizaje, operador, API, diagnóstico y UI; node check | 119 passed, 5 warnings | No |
| C25d | `grid/meta_operator.py:363-481,1532,1952`, verificador | pruebas operador y diagnóstico | 88 passed, 4 warnings | No; paridad verificada |
| C25e | `grid/meta_operator.py:1052-1054,2854-2879` | pruebas de operador | 87 passed, 4 warnings | No; caché solamente |
| C25f | `scripts/reetiquetar_meta.py:14-160`, `grid/meta_operator.py:2319-2650`, lectores | reetiquetado, aprendizaje, core e Inteligencia | 72 passed, 2 warnings | No; marca para recálculo futuro |
| C25h | `scripts/meta_active_study.py:1-137`, `scripts/estudios_meta.py` | `tests/test_estudios_meta.py` | 6 passed | No; estudio aislado |
| C25g | reportes y `DECISIONES_MODO_META.md` | auditoría UTF-8/BOM/LF/interrogaciones | 0 hallazgos | No |

## Fase 41-C25i — Lanzamiento manual del aprendizaje Meta

- `api/routes/metas.py:140-149`: `POST /api/metas/learning/run` responde 202 y agenda `meta_learning_manual`; si `_learning_guard` está ocupado, responde 409. La respuesta incluye la hora UTC agendada. El job admite retraso de hasta seis horas. `GET /api/metas/learning/last` devuelve la última corrida.
- `grid/meta_operator.py:953-983,2514,4469-4471`: el job manual invoca `run_learning_daily(trigger="manual")`; la corrida cron conserva el valor predeterminado `scheduled` y el cron existente de 08:30 UTC. El GET incluye muestras fallidas resumidas (hasta 20), geometrías inválidas y tiempos. Corridas anteriores sin `trigger` se muestran como `scheduled`. Si el job manual sigue activo a las 08:30 UTC, el job programado sale `skipped` por el guard existente; el cron no cambia.
- `frontend/index.html:30` y `frontend/intelligence.js:119-147`: la confirmación indica que tarda alrededor de 90 minutos y no ejecuta órdenes. La interfaz muestra HTTP 202/409, hora agendada y el estado de la última corrida; **Actualizar estado** consulta el GET.
- Pruebas: comando exacto: `$env:ASPLE_OFFLINE='1'; $bt=Join-Path $env:TEMP ('fase41-c25i-rev-'+[guid]::NewGuid().ToString('N')); .\venv\Scripts\python.exe -m pytest tests/test_meta_learning_api.py tests/ui/test_intelligence_ui.py -q --basetemp $bt` — 13 passed, 10 warnings. `node --check frontend/intelligence.js`: correcto.
- Uso en S1: abrir Inteligencia, pulsar **Correr aprendizaje ahora**, revisar el aviso y confirmar; usar **Actualizar estado** para consultar la corrida. Alternativa desde la máquina del servicio, con el token guardado en una variable de entorno sin imprimirlo: `curl -i -X POST http://127.0.0.1:8000/api/metas/learning/run -H "X-API-Token: ${GRID_API_TOKEN}" -H "Content-Type: application/json" -d '{}'`. No se ejecutó en S1.

## Fase 41-C26a1 — Scripts ejecutables desde cualquier carpeta

- `scripts/reetiquetar_meta.py:13`, `scripts/estudios_meta.py:18`, `scripts/medir_orden_intravela.py:16`, `scripts/verificar_s1_meta.py:15`, `scripts/mantenimiento_bd_meta.py:13` y `scripts/meta_active_study.py:12`: agregan la raíz del repo a `sys.path` con el mismo patrón antes de importar módulos propios. `meta_active_study.py:147` acepta `--help` como herramienta autónoma sin iniciar un estudio.
- Prueba `tests/test_script_entrypoints.py`: cada uno de los seis scripts se ejecuta con `--help`, desde la raíz y desde una carpeta temporal externa, sin `PYTHONPATH`; **1 passed**.
