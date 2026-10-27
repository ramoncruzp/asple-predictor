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
