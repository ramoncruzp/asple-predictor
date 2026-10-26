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
| Estado de aprendizaje y comparación | `grid/meta_operator.py:409-435` | Lee eventos de plan/revisión, `decision_choices` y versiones; conservar IDs, elecciones y resultados v1/v2. |
| Importación de muestras live desde planes históricos | `grid/meta_operator.py:1321-1368` | Lee `META_PLAN_EVALUATED.alternatives`, `decision_id`, piezas, estimación y señales; se mantienen esas claves y su significado. |
| Construcción de alternativas y recomendaciones | `grid/meta_operator.py:993-1077` | Cada cartera adjunta `advisor_recommendations`; la respuesta de análisis debe continuar completa. |
| Persistencia del evento del plan | `grid/meta_operator.py:1206-1218` | Escribe alternativas, decisiones, Advisor comparado, combinaciones, sombra, selección y bandas sigma. |
| Selección de elecciones de revisión | `grid/meta_operator.py:2361-2429` | Usa alternativas del plan para calcular real, Scanner + Advisor y aprendida. No depende del evento ya persistido. |
| Escritura de revisión | `grid/meta_operator.py:2432-2498` | Actualmente persiste decisión, valoración, sombra, `decision_choices.real_result` y toque; v2 debe conservar la elección referenciada sin duplicar el plan. |
| Cobertura y datos recientes de Inteligencia | `api/routes/intelligence.py:103-175`, `api/routes/intelligence.py:389-393` | Cuenta señales y devuelve `decision_choices` y el detalle de eventos; mantener compatibilidad con eventos antiguos y exponer claves v2. |
| Resultados de elecciones contra etiquetas | `api/routes/intelligence.py:316-330` | Cruza `decision_choices` con etiquetas usando `alternative_id`; debe seguir resolviendo versiones previas y nuevas. |
| Diagnóstico de omisiones de toque | `scripts/diagnostico_senales_meta.py:91-100`, `scripts/diagnostico_senales_meta.py:145-157` | Lee alternativas y `signals_omitted`; ambas se conservan en eventos v2. |
| Bitácora en Metas | `frontend/metas.js:402-430` | Renderiza tipo, símbolo/capital, pieza, decisión, motivo y sombra; no despliega alternativas completas. |
| Decisiones en Inteligencia | `frontend/intelligence.js` (bloque de decisiones y visor JSON) | Muestra elecciones y permite expandir el objeto `details`; el plan completo no es presentado como pantalla de alternativas ahí. |

No se encontró un renderizador de la lista completa de alternativas de la bitácora: el análisis HTTP continúa entregando el plan completo al operador/UI, mientras que el ledger genérico expone el JSON expandible. La v2 evita cambiar los campos visibles habituales y mantiene los campos que usa aprendizaje y diagnóstico.

Estado de esta parte: revisión y medición documentadas; todavía no se ha cambiado código de producción.
