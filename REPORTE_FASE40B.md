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

El análisis solicita al `GridScanService` las candidatas y los filtros duros, forma las bandas de sigma mediante los cuantiles editables `sigma_q33` y `sigma_q67` (mínimo tres candidatas), y compara Simple y Smart. Desde 40b-6A, Prudente requiere sigma en el tercil medio, readiness `lista` y el umbral de verificaciones efectivas editable por perfil (30 por defecto). Rápido no exige estadísticas de modelo ni restringe la banda, pero sí conserva los filtros duros del Scanner y el estado de libro bilateral.

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

## 40b-4A — valoración ejecutable por profundidad

### Implementado

- `data/testnet_client.py:302` consulta profundidad Spot con `limit=5`, convierte bids/asks a `Decimal` y devuelve `read_at`. Binance asigna peso 2 a `/api/v3/depth` para límites 1–100; cinco niveles permiten recorrer más que el mejor bid. [Documentación oficial de Binance Spot](https://github.com/binance/binance-spot-api-docs/blob/master/rest-api.md#order-book).
- `grid/meta.py:49` añade la función pura `executable_value`: recorre niveles, resta comisión de venta, calcula cantidad absorbida y remanente con haircut configurable y devuelve `KNOWN`, `PARCIAL` o `DESCONOCIDO`. Una lectura vieja, sin bids o inválida nunca produce valor cero válido.
- `grid/meta.py:354` agrega inventario por símbolo, comparte una lectura de profundidad por minuto y distribuye la valoración entre piezas. La respuesta expone `value_best_bid` y `value_executable`; el alias legado `value_usdt` y `result_pct` apuntan al ejecutable. El límite y el aviso de 75 % usan este último; la meta cumplida continúa basándose solo en efectivo realizado.
- `database/db_manager.py:322, 386` persiste estado, duración y cadencia de alertas por meta/moneda y añade `loss_limit_breach_count` a `meta_runs`. El cierre por pérdida necesita dos ciclos consecutivos; el contador sobrevive a reiniciar `MetaCore`. Al 75 % se bloquean piezas sin esperar confirmación. Los umbrales de haircut y avisos son editables por perfil.
- `grid/meta.py:472` registra cambios de estado; DESCONOCIDO alerta tras más de 10 min y repite cada 30 min; PARCIAL solo alerta tras más de 30 min y más del 25 % faltante, con repetición cada 6 h. La recuperación solo se notifica después de una alerta previa. No se cierra automáticamente con valoración DESCONOCIDO.
- `grid/meta.py:740` usa el valor ejecutable en la estimación de cancelación. `ESPEC_FASE40_MODO_META.md:47` documenta estados, fórmulas y reglas operativas.

### Pruebas y resultado

`tests/test_meta_core.py`, `tests/test_testnet_client.py`, `tests/test_app_settings_api.py` con `ASPLE_OFFLINE=1`, `-m "not live"` y carpeta temporal nueva bajo `$env:TEMP`: **46 passed, 1 deselected, 10 warnings, 2,74 s**. Incluye absorción completa/parcial, comisión, haircut no nulo, libro vacío/viejo, umbral y repetición de alertas, pérdida en dos ciclos tras reconstruir el núcleo, y lectura de profundidad Decimal. Una corrida anterior sin `-m "not live"` intentó el test marcado `live`; el socket fue denegado y no se accedió a Testnet.

### Límites

La valoración parcial es una estimación: el remanente usa el último bid absorbido menos haircut y comisión, no garantiza ejecución. El libro se refresca una vez por minuto por símbolo y las piezas de una moneda comparten liquidez disponible; no se consultan endpoints de profundidad fuera de las monedas con inventario. Sin profundidad válida la meta queda bloqueada y sin protección de pérdida hasta recuperar valoración.

## 40b-4B — límite del replay, precálculo y medidor

### Implementado

- `grid/sim/runner.py:83, 201, 664` acepta el parámetro opcional `meta_max_loss_pct` (sin alterar simulaciones normales cuando no se pasa). En cada vela compara efectivo más inventario al `low` menos comisión. Al cruzar el límite cancela órdenes, liquida el inventario simulado al `low`, registra `META_LOSS_CUT` / `CORTADA_POR_PERDIDA`, comisión y pérdida realizada, y termina esa ventana. `grid/meta_operator.py:631, 680, 700` entrega el tope del perfil, usa efectivo realizado para el progreso de las ventanas cortadas y expone `loss_cut_windows` por alternativa.
- La simulación histórica no tiene profundidad intravela: usar el `low` de cada vela con fee de venta es la aproximación conservadora documentada para el peor bid. El aviso del 75 % no se modela: el replay compara una alternativa fija y no añade piezas durante la ventana.
- `grid/sim/meta_bot.py:9, 51` aplica el mismo corte al bot de una celda, incluye la venta y su comisión, marca `CORTADA_POR_PERDIDA` y conserva `rebuys=0`. Para bots, `grid/meta_operator.py:305, 668` prueba X a 0,5 %, 1,5 % y 3 % por debajo del midpoint. Y = X × (1 + margen neto Advisor + 0,21 %), que cubre el 0,1 % aproximado de cada operación más un margen de redondeo, y respeta el mínimo del perfil. El criterio común del ranking decide. Las métricas del replay muestran `bot_windows_bought` y `bot_windows_sold`.
- En la corrida S1 previa, XRP tenía X=1,2191 y Y=1,3942: eran el piso/techo recomendados por el Advisor para aquella alternativa, no niveles optimizados por porcentaje de distancia. El replay de entonces no registraba compras/ventas por ventana, por lo que esos recuentos no existen en el artefacto disponible. No se ejecutó una consulta de mercado ni Testnet para reconstruirlos; el siguiente análisis expondrá esos conteos y comparará variantes cercanas/lejana.
- `grid/meta_operator.py:1152, 1225` programa el precálculo diario a las 08:00 UTC (cron UTC), verifica el interruptor al comenzar y durante la tarea, usa un solo hilo/worker (por debajo del máximo de dos), rebaja prioridad por hilo cuando el SO lo permite y cede CPU entre ventanas/perfiles. No adquiere `GridMonitor.ops_lock`. Precalienta ambos perfiles para 100→110 y para capital/objetivo de metas existentes; si faltan entradas, el análisis normal calcula el resto. Registra duración y número de entradas de caché.
- `scripts/medir_analisis_meta.py:60, 98` añade `--con-cache`: lee caché persistente en conexión reflejada de solo lectura, nunca escribe y reporta por separado aciertos persistentes y del proceso. Sin la opción mantiene la caché persistente apagada.

### Pruebas B y resultado

La corrida enfocada de `tests/test_meta_operator.py`, `tests/test_meta_core.py` y `tests/test_testnet_client.py` dio **74 passed, 1 deselected, 3 warnings, 2,96 s**. Tras endurecer el redondeo del margen X/Y y separar el evento de corte del objetivo, `tests/test_meta_operator.py` dio **35 passed, 3 warnings, 2,26 s**. Cubren corte de grid al mínimo intravela con fees realizados, corte del bot, actividad por ventana, comparación X/Y, switch/lock del precálculo y lectura persistente sin escritura. `scripts/medir_analisis_meta.py --help` confirma `--con-cache`; no se ejecutó el análisis real.

Suite completa final: `ASPLE_OFFLINE=1 .\venv\Scripts\python.exe -m pytest tests -m "not live" -q --basetemp="$env:TEMP\asple_40b4b_20261022_final3"` — **1.462 passed, 1 skipped, 18 deselected, 55 warnings, 299,58 s** (exit code 0). Playwright no reportó hosts externos; no se usó red, Testnet, `.env` ni BD real. No hubo push ni despliegue.

## 40b-5 — señal toque compra→venta en sombra

### Implementado

- `grid/sim/touch_stats.py:14` implementa `touch_stats` puro con NumPy: detecta compra con `low <= X`, venta en una vela posterior con `high >= Y`, demora de venta y excursión adversa. Una venta en la misma vela no se infiere porque OHLC no informa el orden intravela.
- Los toques consecutivos se agrupan hasta una venta o hasta que `high` se aleje de X al menos la mitad de X→Y. Se descartan del denominador los toques sin horizonte completo y se informa `censored_touches`. Sin toques elegibles, `sell_rate` queda `None`.
- `grid/meta_operator.py:784` reutiliza las velas 5m de 180 días, la lista de ventanas del replay y el mismo reescalado de X/Y al `open` histórico. Calcula 4 h, 24 h y el plazo del perfil (20/45 días). Los grids usan las líneas reales de `compute_lines` y agregan las celdas ponderando por toques; el bot individual evalúa su único par X/Y.
- Cada alternativa expone `touch_signal` con las estadísticas por horizonte y por celda, además del valor normalizado `2 × sell_rate − 1`. Lleva `weight: 0` y no se agrega a `signals_used`, el logit, el desempate ni el ranking. `tests/test_touch_stats.py:117` compara probabilidad, señales y orden con y sin el dato.
- `database/db_manager.py:325` crea `meta_signal_observations` al inicializar el esquema. Guarda por meta, `decision_id`, alternativa, moneda, tipo, celda, fecha de ventana, X/Y relativos, horizonte y estadísticas; el índice está en `database/db_manager.py:363`. El mismo `decision_id` y el resumen de señales se escriben en `META_PLAN_EVALUATED`; el evento de decisión de revisión referencia las alternativas en `grid/meta_operator.py:1044`.
- El resultado se guarda en `meta_analysis_cache` como `touch_signal`; por ello el análisis normal y `precompute_daily` usan el mismo cache. `timings_s.touch_signal` y sus aciertos se separan en `grid/meta_operator.py:542`; el medidor existente imprime el diccionario completo en `scripts/medir_analisis_meta.py:99-105`.
- La referencia `C:\APPS\ASPLE\src\signal_quality\touch_event_analyzer.py` se usó solo para la idea. No se portaron el filtro ni sus pesos/puntaje.

### Pruebas y resultado

- Pruebas nuevas en `tests/test_touch_stats.py:13-138`: venta posterior y demora; fallo y excursión; deduplicación; rearme tras alejamiento y venta; censura; tasa indefinida; misma vela; agregado ponderado; reescalado +50 %; cache; y ausencia de efecto en probabilidad/ranking.
- `tests/test_meta_core.py:42` verifica persistencia de la observación con sus IDs de meta, decisión y alternativa.
- Corrida enfocada: `ASPLE_OFFLINE=1 .\venv\Scripts\python.exe -m pytest tests/test_touch_stats.py tests/test_meta_operator.py tests/test_meta_core.py -m "not live" -q --basetemp="$env:TEMP\asple_40b5_focused_final_20261022"` — **64 passed, 3 warnings, 3,17 s**.
- Suite completa antes del commit: `ASPLE_OFFLINE=1 .\venv\Scripts\python.exe -m pytest tests -m "not live" -q --basetemp="$env:TEMP\asple_40b5_full_20261022"` — **1.473 passed, 1 skipped, 18 deselected, 55 warnings, 305,22 s**, exit code 0. Playwright no registró hosts externos; sin Testnet, red, `.env` ni BD real.

### Límite de medición

El análisis expone la duración real del paso `touch_signal` para que el medidor de S1 la compare con `replays`. Esta fase no ejecutó el análisis con mercado real ni en S1; por ello el aumento relativo respecto al replay y el objetivo aproximado de 5 % todavía no están certificados. El cache diario reduce el coste de alternativas repetidas y del precálculo, pero no sustituye esa medición.

## 40b-6A — Prudente con 30 verificaciones

### Implementado

- `grid/meta.py:19, 30` incorpora `min_effective_verifications: 30` a ambos perfiles. La configuración sigue siendo editable mediante `meta_profile_settings` y la API de ajustes del perfil (`grid/meta.py:158-190`); no se altera la validación global de estadísticas de modelo.
- `grid/meta_operator.py:135, 450-466, 736-743` usa el umbral guardado en la instantánea de perfil. La ventana de 24 h retiene `max(168, N_MIN × 24) = 720` filas base (`database/db_manager.py:1052-1054`); `720 / 24 = 30` verificaciones efectivas, así que el umbral de Prudente es alcanzable. La consulta conserva además filas de margen para maduración.
- `frontend/metas.js:42-59` presenta el conteo como “12,0 de 30 días verificados”, usa coma decimal y traduce `datos_insuficientes` y la ausencia de estado a “Datos insuficientes” y “Sin estado”.
- XRPUSDT puede mostrar “Sin estado” cuando no hay fila para él en `coin_readiness`: `database/db_manager.py:1298-1310` devuelve `None` en ese caso y `_model_snapshot` (`grid/meta_operator.py:696-725`) deja `readiness` en `None`. Esto no lo marca como modelo completo: el gate también requiere estado `lista` y pronóstico reciente. Es consistente con una moneda del Coin Registry aún no procesada por onboarding; no consulté BD local ni la de S1, así que no atribuyo a XRP una causa de ingestión más específica.
- `tests/test_meta_operator.py:265-272` comprueba la cota 720/24 y evita que el umbral por defecto supere la capacidad; `tests/test_meta_core.py:70-74` comprueba que se puede editar; `tests/ui/test_metas_ui.py:64-72, 318-319` verifica ambos textos traducidos.

### Pruebas

- Enfoque final: `tests/test_meta_operator.py tests/test_meta_core.py tests/ui/test_metas_ui.py -m "not live" -q` — **83 passed, 6 warnings, 32,92 s**.
- Suite completa: `ASPLE_OFFLINE=1 .\\venv\\Scripts\\python.exe -m pytest tests -m "not live" -q --basetemp <carpeta nueva en $env:TEMP>` — **1.504 passed, 1 skipped, 18 deselected, 56 warnings, 504,27 s**, código de salida 0.
- `node --check frontend/metas.js` y `git diff --check` pasaron. Playwright no registró hosts externos; sin Testnet, `.env` ni BD real.

## 40b-6B — Reajuste activo de piezas Meta

### Cambios

- `grid/meta_operator.py:1300-1344,1409-1517`: la revisión evalúa reajustes de piezas propias cuando el precio toca una franja de un nivel del piso/techo, o tras dos revisiones fuera del rango y un desplazamiento mayor a un nivel. El disparador solo habilita simulación; si mantener no es superado por materialidad, no reajusta ni retira por techo.
- `grid/meta.py:19-44`: cada perfil permite editar `reconfig_min_probability_delta_pp` (5), `reconfig_probability_tie_tolerance_pp` (2) y `reconfig_min_progress_delta_pct` (10). Solo se acepta mejora histórica ≥5 puntos porcentuales, o empate dentro de ±2 puntos con avance mediano ≥10 % mejor.
- `grid/meta_operator.py:1449-1509`: aplica enfriamiento de 1 hora por grid y máximo de 4 reajustes en 24 horas por meta. Registra valores viejos/nuevos, alternativas evaluadas, motivo, probabilidad y avance estimados, ejecución y decisión en sombra. El análisis sigue fuera de `ops_lock`; `review_once` revalida bloqueo, estado y movimiento de precio dentro del lock antes de ejecutar.
- `grid/engine.py:1489-1545`: un bot individual sin inventario cancela su BUY vigente y coloca un BUY nuevo X y SELL Y, verificando precio, margen mínimo del perfil, notional y cancelación confirmada. Si ya compró, la función rechaza el reajuste y conserva su SELL. Los grids de varios niveles usan `adjust_grid`, que conserva celdas con inventario y sus ventas. El parámetro opcional de capital solo funciona para grid Meta activo, valida efectivo atribuible y límite por moneda y redistribuye solo celdas libres.
- `database/db_manager.py:1536-1543`, `grid/monitor.py:607,728-773`: el monitor reconoce grids Meta por vínculo no liberado y no ejecuta ADJUST, re-espaciado ni idle-shrink en ellos, aunque los interruptores globales estén encendidos. Conserva PAUSE/RESUME Smart, cierres por pérdida máxima/plazo/filtros, stop-loss por celda según su interruptor, vigilancia 40a y transferencias de préstamos; el operador Meta es la autoridad única para rango, niveles y margen.
- `frontend/metas.js:432,477-496`: la meta activa presenta los cinco reajustes más recientes en español con pieza, cambio de piso/techo/niveles, motivo y mejoras estimadas, además de la bitácora existente.
- A5: `grid/meta_operator.py:1647-1680` guarda resultado por perfil/base y sigue con el otro perfil si no hay candidatas; responde `partial` cuando una base no pudo precalcularse. A6: `frontend/metas.js:11,148,166-176` conserva el objetivo escrito al volver a dibujar el formulario después de crear una meta.
- `DECISIONES_MODO_META.md:55-64,88-91,114-118` y `ESPEC_FASE40_MODO_META.md:72,106-110` registran la decisión de 2026-10-25 y el reparto de responsabilidades del monitor.

### Pruebas y resultado

- Cobertura añadida: `tests/test_meta_operator.py:222-333,600-635` (precálculo parcial, umbrales de materialidad, franja de un nivel, dos revisiones/desplazamiento, enfriamiento, máximo diario y no retirada automática por techo si no hay mejora); `tests/test_grid_engine.py:82-139` (bot antes/después de compra y límites de capital); `tests/test_grid_monitor_policy.py:110-141` (Meta Smart mantiene PAUSE y el monitor no invoca ajuste/re-espaciado); `tests/ui/test_metas_ui.py:208-222,255-272` (objetivo persistido y presentación de reajuste).
- Enfoque final de archivos afectados: **117 passed** (operator 47 + engine 50 + monitor policy 13 + UI Metas 7), advertencias de dependencias conocidas; corrida distribuida en dos invocaciones porque se corrigieron detalles detectados.
- Suite completa offline final: `ASPLE_OFFLINE=1 .\venv\Scripts\python.exe -m pytest tests -m "not live" -q --basetemp <carpeta nueva bajo $env:TEMP>` — **1509 passed, 1 skipped, 18 deselected, 56 warnings, 330,96 s**, código 0. `node --check frontend/metas.js` y `git diff --check` pasaron. Sin red externa, Testnet, `.env` ni BD real.

### Límites observados

- `adjust_grid` conserva inventario y órdenes SELL al cambiar geometría; si la nueva geometría no puede mapear celdas ocupadas y dejar las celdas libres mínimas, la alternativa se rechaza y la pieza se mantiene.
- El operador no liquida con pérdida durante un reajuste. Las ventas con pérdida siguen reservadas al cierre verificado iniciado por las protecciones de 40a o por Ramón.
