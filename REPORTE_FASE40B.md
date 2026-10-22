# Fase 40b — Operador del Modo Meta

## Implementado

- `grid/meta_operator.py` añade el análisis asíncrono, estimación por replay, validación y vigencia de planes, inicio secuencial, revisiones y registro de decisiones.
- `api/routes/metas.py` añade `POST /api/metas/{id}/analysis`, `GET /api/metas/{id}/analysis/{job_id}` y `POST /api/metas/{id}/start` con `plan_id`.
- `api/main.py` conecta el operador y su revisión independiente cada 15 minutos. El operador y el monitor comparten `ops_lock` cuando el monitor está disponible.
- `grid/meta.py` amplía los ajustes editables de perfil y limita los grids ligados simultáneamente a los que siguen abiertos.
- `database/db_manager.py` añade la migración de `meta_runs.control_plan_json` y la identificación persistente de bots individuales Meta.
- `grid/engine.py` agrega la rama de un nivel exclusiva para `individual_bot_grid` Meta. Después de vender, la celda queda `DONE`; la identificación persiste para que el reinicio no la rearme. La apertura regular conserva el mínimo de cuatro niveles.
- `grid/sim/meta_bot.py` agrega un replay histórico de una compra y una venta, sin recompra. Aplica paso/notional, comisión de compra y venta y objetivo absoluto en USDT.
- `alerts/dispatcher.py` reconoce los eventos de apertura, retirada y cierre del operador.

## Modelo de estimación y selección

El análisis solicita al `GridScanService` las candidatas y los filtros duros, forma las bandas de sigma mediante los cuantiles editables `sigma_q33` y `sigma_q67` (mínimo tres candidatas), y compara Simple y Smart. Prudente requiere sigma en el tercil medio, readiness `lista` y al menos 50 verificaciones efectivas. Rápido no exige estadísticas de modelo ni restringe la banda, pero sí conserva los filtros duros del Scanner y el estado de libro bilateral.

Para cada alternativa elegible (grid Simple/Smart o bot individual cuando el capital no alcanza para cuatro celdas de al menos 5,5 USDT) se construyen ventanas históricas de 30 días con avance de 7 días, sobre hasta 180 días de velas de 5 minutos. La simulación usa como objetivo el incremento absoluto de la meta en USDT. La probabilidad de replay es `número de ventanas que alcanzaron el objetivo / ventanas evaluadas`. Los días al objetivo se resumen en mediana y percentiles 10 y 90; se informa peor drawdown, ventanas totales e independientes. Se cuentan como independientes `floor(N / ceil(30/7))`; la evidencia es suficiente con tres o más. Bajo 25 % se marca como poco probable. Las alternativas se ordenan por probabilidad y luego por mediana de días. El resultado se etiqueta como estimación histórica, no promesa.

Cuando hay evidencia suficiente, las señales se suman en el logit de la probabilidad base:

```text
p0 = clamp(probabilidad_replay, 0.01, 0.99)
logit(p_meta) = log(p0 / (1-p0)) + Σ (peso_i * señal_normalizada_i)
p_meta = 1 / (1 + exp(-logit(p_meta)))
```

Las señales se normalizan así y se recortan a `[-1, 1]`:

- Volatilidad: `log(mediana(sigma_24h de pronósticos frescos) / sigma_24h realizada)`.
- `break_prob`: `1 - 2 * break_prob`, usando el pronóstico de sigma fresco si existe y, si no, sigma realizada.
- Dirección: si `probability_up >= 0.60`, `(probability_up - 0.5) / 0.1`; si la señal fresca queda bajo el umbral, su valor observado es neutral (`0`).
- Scanner: `2 * score - 1`.
- Advisor: `net_edge_pct_per_cycle / 2`.

Los pesos son editables por perfil en `meta_profile_settings.signal_weights`. Pronósticos con más de dos horas se omiten y se anotan. La decisión en sombra aplica solo Scanner y Advisor. Si las ventanas independientes no alcanzan tres, la probabilidad ajustada queda en la probabilidad de replay y no se amplifica con señales.

El plan expira a los cinco minutos. Al iniciar se comprueba el precio medio actual frente al del plan; la deriva máxima editable por perfil es 1 % por moneda. Se registra en `meta_runs.control_plan_json` el plan de control Smart, sin abrirlo. La apertura inicial es serial; cada fallo queda en la bitácora y, si no abre ninguna pieza, la meta vuelve a `DRAFT`.

La rama individual crea una sola compra y una venta, exige filtros mínimos de cantidad/notional y margen neto del perfil. Las revisiones bloquean movimientos ante valoración `UNKNOWN` o aviso del 75 %. Dos revisiones sobre techo sin inventario retiran y reabren centrado en el precio; dos fallos duros del Scanner pausan una pieza con inventario y conservan sus ventas. La rotación con inventario usa `close_grid(..., "liquidate")` y exige que el beneficio estimado supere la pérdida marcada y comisiones estimadas, además de una estimación P90 más rápida.

## Migración y pruebas

Migración aditiva: `meta_runs.control_plan_json TEXT` en SQLite y PostgreSQL. No se modifican datos de mercado ni bases reales.

Pruebas dirigidas, ejecutadas antes de la suite final:

```text
tests/test_meta_core.py
tests/test_grid_engine.py
tests/test_meta_operator.py
tests/test_app_settings_api.py
76 passed, 11 warnings, 7.85 s
```

Suite completa final, con `ASPLE_OFFLINE=1`:

```text
venv/Scripts/python.exe -m pytest tests -m "not live" -q --basetemp=.pytest_tmp
1430 passed, 1 skipped, 18 deselected, 53 warnings
pytest: 363.82 s; tiempo de pared: 367.11 s; exit code: 0
```

Los 18 tests `live` se excluyeron; no se accedió a Testnet ni a una BD real. Playwright informó cero hosts externos.

## Archivos

- `grid/meta_operator.py`
- `grid/meta.py`
- `grid/engine.py`
- `database/db_manager.py`
- `api/routes/metas.py`
- `api/main.py`
- `alerts/dispatcher.py`
- `tests/test_meta_operator.py`
- `grid/sim/meta_bot.py`
- `tests/test_grid_engine.py`
- `tests/test_meta_operator.py` cubre el replay del bot individual: compra, venta, estado DONE, sin recompra, objetivo absoluto y costos.
- Este reporte y `DISCREPANCIAS_FASE40B.md`.

## Estado

La implementación está **parcial**. Las diferencias funcionales y de cobertura que impiden considerar 40b terminada están detalladas en `DISCREPANCIAS_FASE40B.md`. No se hizo commit ni despliegue.

## 40b-2 - Correcciones y cierre de esta iteracion

Esta seccion sustituye las descripciones anteriores que indicaban cartera de una sola pieza, ventanas fijas de 30 dias, pesos de senales de 1 y ausencia de prestamos del perfil.

### Cerrado

- **Cartera acotada:** `grid/meta_operator.py:97` y `grid/meta_operator.py:299` prueban hasta tres piezas, entre las tres mejores monedas elegibles, con repartos en pasos del 10 %, tope de capital por moneda y limites de piezas por perfil. Los resultados de replay se alinean por fecha y se suman antes de puntuar la combinacion (`grid/meta_operator.py:79`). Cada combinacion y sus metricas quedan en `combinations_evaluated` y en `META_PLAN_EVALUATED` (`grid/meta_operator.py:410`). No es una busqueda exhaustiva.
- **Ventanas por perfil:** `grid/meta_operator.py:476` usa 45 dias Prudente o 20 dias Rapido con avance de siete dias. La respuesta comunica el numero de ventanas independientes y advierte en lenguaje simple cuando hay menos de tres.
- **Advisor:** `grid/meta_operator.py:190` llama al servicio Python del Advisor, sin HTTP, para el margen minimo y +0,2/+0,4 puntos. Conserva recomendaciones, comparaciones Simple/Smart y senal Advisor procedente de la respuesta del servicio. El plan de control Smart se vuelve a pedir con el capital completo y se persiste sin abrirlo (`grid/meta_operator.py:369`, `grid/meta_operator.py:373`, `grid/meta_operator.py:636`).
- **Prestamos:** `grid/meta.py:39` y `api/routes/metas.py:132` activan prestamos Smart y fijan los topes de perfil, 30 % Prudente y 50 % Rapido. El replay no modela los prestamos; los planes Smart lo advierten. No se creo un simulador nuevo.
- **Senales:** los valores predeterminados son 0,25 por senal y el ajuste total se limita a 1,0 en logit, editable en los ajustes de perfil (`grid/meta.py:18`, `grid/meta_operator.py:68`, `grid/meta_operator.py:335`). El plan expone la probabilidad historica y la ajustada. Se mantiene sin calibrar; esa calibracion corresponde a 40d.
- **Revision y bitacora:** la revision puede abrir una pieza con efectivo atribuible libre y respeta el bloqueo por DESCONOCIDO/75 %, topes por moneda y cantidades del perfil (`grid/meta_operator.py:679`, `grid/meta_operator.py:815`). Dos revisiones con filtro duro perdido o precio sobre techo retiran compras; si hay inventario conservan ventas vivas (`grid/meta_operator.py:750`). La decision real y la decision en sombra se registran juntas en la bitacora (`grid/meta_operator.py:693`).
- **Comisiones de rotacion:** el cierre verificado devuelve comisiones de compra y venta y cotizaciones brutas observadas (`grid/engine.py:2477`). El operador vuelve a evaluar tras el cierre con el P&L realizado, que ya incluye dichas comisiones; si la ganancia prevista no compensa perdida y coste estimado de reapertura, no reabre (`grid/meta_operator.py:54`, `grid/meta_operator.py:784`).
- **Medicion S1:** se agrego `scripts/medir_analisis_meta.py`, que refleja la BD existente en modo de solo lectura, no crea metas ni envia ordenes y desglosa los tiempos. No se ejecuto aqui ni contra Testnet; esta listo para que Ramon lo corra en S1.
- **Compatibilidad de apertura normal:** `api/routes/grids.py:509` solo pasa `meta_id` en aperturas Meta. La apertura normal mantiene la firma anterior. La prueba que detecto la incompatibilidad fue `tests/test_grids_api.py::test_public_binance_string_book_shape_scans_dry_runs_and_reuses_plan`; tras corregirlo, el archivo afectado paso 42 pruebas.

### Suite completa

Ambas corridas usaron `ASPLE_OFFLINE=1` y `--basetemp=.pytest_tmp`; 18 pruebas marcadas live fueron excluidas.

| Corrida | Resultado |
|---|---|
| Primera, antes del arreglo de compatibilidad | 1 fallo (`test_public_binance_string_book_shape_scans_dry_runs_and_reuses_plan`), 1.449 passed, 1 skipped, 18 deselected, 55 warnings; 336,28 s |
| Segunda, final | 1.450 passed, 1 skipped, 18 deselected, 55 warnings, 0 fallos; 350,61 s |

Pruebas enfocadas finales antes de la suite: `tests/test_meta_operator.py`, `tests/test_grid_engine.py`, `tests/test_grid_close.py`: 87 passed, 4 warnings, 5,04 s. Prueba de compatibilidad luego del arreglo: `tests/test_grids_api.py`: 42 passed, 5,66 s.

### Limites abiertos

- La prueba de API de 100->110 en ambos perfiles usa exchange falso y plan sintetico para cubrir el tramo asincrono, inicio y enlace. No ejecuta el analisis completo del Scanner/Advisor contra historial simulado en ese flujo de API.
- La regla de reabrir tras rotacion se prueba con costes realizados en una prueba de unidad y el motor prueba la persistencia de comisiones; falta un test integrado de `review_once` que ejecute ambas ramas de rotacion (aceptar y rechazar) hasta la reapertura.
- La revision agrega una pieza por ciclo y usa las alternativas de cartera disponibles; no recompone de una vez toda la cartera libre.
- Los replays alineados por fecha usan una busqueda acotada; no prueban causalidad historica de las senales. El porcentaje ajustado no es una probabilidad calibrada.
- No hay medicion del tiempo real en S1 aun. El script queda sin ejecutar a proposito.

No hubo commit ni despliegue. Las pruebas live permanecen excluidas; no se accedio a Testnet.

### Archivos de 40b-2

Cambios funcionales y de prueba: `grid/meta_operator.py`, `grid/engine.py`, `grid/meta.py`, `api/routes/grids.py`, `api/routes/metas.py`, `tests/test_meta_operator.py`, `tests/test_grid_close.py`. Script añadido: `scripts/medir_analisis_meta.py`. El resto de archivos de 40b enumerados arriba pertenecen a la base de trabajo de esta fase.

## 40b-3 — bloqueo, rendimiento y desempate

### Bloqueo del monitor

`grid/meta_operator.py:829-896` ejecuta el análisis de revisión sin `ops_lock`: `review_once` usa un guard separado y no solapable. Si llega otra cadencia mientras el análisis sigue activo, devuelve no-op y registra `META_OPERATOR_REVIEW_SKIPPED`. `grid/meta_operator.py:861-883` vuelve a consultar meta y valoración bajo `ops_lock`, aplica otra vez los frenos de estado DESCONOCIDO/75 % y compara el precio con el plan antes de permitir acciones. La comparación cubre símbolos de todas las alternativas, no solo la cartera seleccionada (`grid/meta_operator.py:812-819`). La ejecución de acciones continúa dentro del lock. El scheduler permite una segunda invocación breve para que registre el skip (`grid/meta_operator.py:1111`).

Prueba integrada: `tests/test_meta_core.py:189` deja el análisis detenido, llama el wrapper `GridMonitor.run_once`, cambia el precio para cruzar la pérdida máxima y corre `MetaCore.run_once`; el cierre ocurre mientras el análisis sigue vivo. La siguiente revisión concurrente se salta y se anota. Resultado final del archivo junto con las pruebas del operador: 47 passed.

### Rendimiento

El cache diario persistente de Advisor y replay usa la tabla nueva `meta_analysis_cache` (`database/db_manager.py:321`) y operaciones tolerantes a base antigua/solo lectura (`grid/meta_operator.py:167-198`). La llave incluye día UTC y parámetros exactos; el replay usa capital, objetivo, niveles, plazo y ancho relativo del rango. No se escala por capital: los mínimos de notional, polvo, niveles y comisiones impiden asumir equivalencia sin medirlo. El cache no altera el resultado del simulador. Las lecturas de velas permanecen cacheadas por moneda y día.

El Advisor se consulta a capital máximo por moneda permitido, una vez por símbolo y cada uno de los tres márgenes; para cada asignación se simula con su capital real y se limita el número de niveles (`grid/meta_operator.py:254-316`, `grid/meta_operator.py:365-381`). Las recomendaciones repetidas en el plan se deduplican por moneda y margen (`grid/meta_operator.py:486-490`). El medidor de S1 sigue con cache desactivado para conservar solo lectura (`scripts/medir_analisis_meta.py:89`).

| Medición | Antes | Después de 40b-3 |
|---|---:|---:|
| Análisis completo en S1, RAPIDO 100→110 | 1.537,9 s (25,6 min) | Sin medición posterior: requiere desplegar/ejecutar el medidor en S1 |
| Scanner / Advisor / replay / selección / Advisor de control | 46 / 609 / 786 / 1.480 / 11 s | Cache persistente evita repetir Advisor y replay del mismo día; Advisor reduce llamadas a un máximo de 3 por moneda |
| Selección exclusiva estimada del dato anterior | 1.480 s reportados | ≈85 s: 1.480−609−786; el cronómetro anterior incluía Advisor y replay dos veces |
| Análisis en este PC | No corrido con mercado real | No medido; no se usó red ni BD configurada |
| Suite offline completa | — | 304,81 s (pytest); 307,55 s de pared |

La reducción de Advisor es una estimación condicional, no una medición: si el caso anterior llamó al Advisor para seis tamaños de asignación por moneda, el coste bajaría aproximadamente de 609 a 102 s. Con scanner 46 s, replay frío 786 s, selección exclusiva 85 s y control 11 s, el análisis frío se estimaría en ≈1.030 s (17,2 min). Por eso el objetivo frío de ≤2 min **no queda demostrado ni se espera cumplir con estos cambios solos**. En una revisión caliente el cache quita replay y Advisor repetidos, pero quedan el Scanner y la selección de cartera; tampoco hay medición que certifique ≤60 s.

No se añadió precálculo al iniciar el servicio ni paralelismo de procesos: no se midió su impacto y el prompt pide dejar margen a las 2 OCPU de S1. No se pasó a velas de 15 min: el simulador actual rechaza intervalos de 900 s (`simulation candles must have 300-second or 3600-second spacing`), y por tanto no existe comparación válida contra 5 min. Se conserva 5 min. Las siguientes mediciones deben ser el medidor de solo lectura en S1, una vez desplegado, en frío y luego caliente.

### Desempate y capital libre

`grid/meta_operator.py:45-52` ordena ambos perfiles por probabilidad histórica de replay, progreso mediano (ganancia neta mediana por ventana / objetivo), menor mediana de días y menor peor caída. El progreso se calcula al combinar ventanas alineadas (`grid/meta_operator.py:21-42, 99-101`). El plan señala literalmente cuando ninguna alternativa alcanza la meta e informa el avance mediano de la más cercana (`grid/meta_operator.py:453-461`).

Cada combinación evaluada registra capital total usado, probabilidad, progreso, plazo y peor caída (`grid/meta_operator.py:433-440`). `capital_usage` explica el efectivo libre y muestra la mejor cartera probada con mayor capital (`grid/meta_operator.py:445-479`). Se siguen probando combinaciones de monedas/piezas y el filtro común decide; no se fuerza a invertir el remanente. El caso S1 anterior dejó 30 USDT porque el perfil Rápido limitaba cada moneda al 70 % y el resto de combinaciones no superó a la seleccionada según el criterio anterior; no se puede afirmar si la segunda moneda empeoraba la nueva puntuación sin reejecutar el análisis posterior.

### Pruebas y resultado

Pruebas añadidas:

- `tests/test_meta_operator.py:49,64`: replay todo-cero y precedencia de probabilidad histórica.
- `tests/test_meta_operator.py:74`: forma real de `BinanceClient` con `.client.get_orderbook_ticker`.
- `tests/test_meta_operator.py:88,116`: capital de referencia del Advisor y persistencia del cache.
- `tests/test_meta_operator.py:407`: ventanas con precio histórico 50 % arriba/abajo del precio actual.
- `tests/test_meta_core.py:189`: GridMonitor y cierre de pérdida durante análisis lento.

Corrida enfocada final: `tests/test_meta_operator.py tests/test_meta_core.py` — 47 passed, 3 warnings, 2,71 s.
Corrida completa final: `ASPLE_OFFLINE=1 .\venv\Scripts\python.exe -m pytest tests -m "not live" -q --basetemp="$env:TEMP\asple_pytest_40b3_20261022_final"` — 1.456 passed, 1 skipped, 18 deselected, 55 warnings, 304,81 s (pytest; 307,55 s de pared). La ruta solicitada `$TEMP\asple_pytest` estaba bloqueada por Windows incluso para listar; se usó una carpeta nueva bajo `$TEMP`. Live excluidas; ningún Testnet, red, `.env` ni BD real.

### Estado

40b-3 cierra la carrera de bloqueo, el desempate, el texto de capital libre y las pruebas de los errores observados en S1. El rendimiento queda parcialmente abierto: no hay medición post-cambio de S1 ni medición de análisis de mercado en este PC y las metas de 2 min/60 s no se certifican. No hubo commit ni despliegue.
