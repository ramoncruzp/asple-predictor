# Fase 40b — Discrepancias y trabajo pendiente

La suite completa está verde, pero no demuestra el flujo integral de 40b. Estas diferencias permanecen abiertas y no se consideran aprobadas por el resultado de pytest.

1. **No se optimizan carteras de varias piezas.** El plan compara una pieza por alternativa y elige una sola. Respeta el tope por moneda de esa pieza, pero no explora combinaciones, cantidad de piezas, distribución total de capital ni el máximo de grids/bots para hallar la combinación más rápida.

2. **Préstamos no implementados por el operador.** Los topes editables de 30 % y 50 % quedan registrados en perfil, pero cada pieza se abre con `loans_pct: 0`; no se forman préstamos entre piezas ni se verifica el tope mediante una simulación de préstamo.

3. **Advisor sustituido parcialmente por datos de estructura del Scanner.** El margen se toma de `suggested_structure`; no se llama al endpoint/servicio de Advisor para obtener y persistir sus recomendaciones y simulaciones completas. Tampoco se generan alternativas de margen sobre el mínimo del perfil. Por eso la señal llamada `advisor` en el estimador es el margen neto de la estructura del Scanner, no un resultado completo del Advisor.

4. **Inteligencia no alineada históricamente.** Las señales actuales de volatilidad, `break_prob` y dirección ajustan el logit de la frecuencia histórica después del replay; no se incorporan como series causales a cada ventana histórica. La fórmula es explícita y trazable, pero sus pesos predeterminados de 1 no están calibrados ni validados OOS. La salida no debe interpretarse como una probabilidad calibrada.

5. **Pruebas de cobertura incompletas respecto a la aprobación.** Hay pruebas unitarias para métricas de replay, bot individual sin recompra, ranking común, exclusiones por perfil, regla de pérdida aceptable, expiración/deriva, bloqueo por `UNKNOWN`/75 % y ciclo de bot con reinicio. Faltan pruebas integradas con exchange falso para: crear meta 100→110 en ambos perfiles y comprobar apertura por API; análisis asíncrono y contenido completo de candidatas; apertura serial parcial; filtro duro durante dos ciclos; precio sobre techo y reapertura; rotación con pérdida y valores reales de comisión; comparación en sombra; y plan de control persistido. El criterio de bitácora solo se prueba indirectamente.

6. **Las revisiones no abren capital libre sin una retirada/cierre previa.** `review_once` compara alternativas y puede retirar/reabrir o cerrar/reasignar en los casos programados, pero no añade otra pieza mientras haya capital libre por debajo de los topes. La revisión de 15 minutos todavía no materializa todas las decisiones de tamaño y número de piezas aprobadas.

7. **Apertura de reemplazo tras cierre.** La decisión se toma con pérdida y comisión estimadas a precio BID más el margen fijo de 0,1 %. El cierre verificado puede deslizarse; si el cierre termina pero la reapertura falla, el operador registra el fallo y deja el efectivo libre. No se vuelve a ejecutar el optimizador después de ese fallo hasta la siguiente revisión.

8. **Tiempo de análisis.** El código mide `analysis_seconds` y ejecuta los análisis en un hilo consultable. No se midió una corrida real de análisis en S1 ARM; los tests offline no ejecutaron Scanner ni los replays con datos de mercado. El caché está limitado al proceso y al día, y el estado del trabajo/plan desaparece con un reinicio.

## Estado de entrega

40b **no está lista para iniciar operaciones autónomas en Testnet** hasta completar al menos las discrepancias 1–5 y probar el flujo integrado con exchange falso. Esta fase no hizo commit ni despliegue.

## 40b-2 - Estado actualizado

Esta seccion prevalece sobre el inventario historico de discrepancias anterior, que describe el arbol antes de 40b-2.

### Cerradas

1. D1 - cartera acotada de una a tres piezas, con hasta tres monedas candidatas, capital en pasos del 10 %, limites por moneda/perfil, replay alineado y registro de combinaciones evaluadas.
2. D2 - apertura Smart Meta con prestamos habilitados y tope del perfil: 30 % Prudente o 50 % Rapido. El estimador declara que no simula el capital prestado.
3. D3 - llamadas directas al servicio Python Advisor por tres margenes; se guardan recomendaciones y comparaciones, y la senal Advisor procede del servicio.
4. D4 - peso predeterminado 0,25 por senal y tope configurable de 1,0 en logit; muestra por separado probabilidad de replay y ajustada. La calibracion queda para 40d.
5. D6 - la revision puede abrir piezas con efectivo libre, dentro de limites y tras comprobar bloqueos. Las decisiones de revision se anotan junto con la sombra.
6. Ventanas - 45 dias Prudente / 20 dias Rapido; se muestra el numero de ventanas independientes y la advertencia cuando la evidencia es escasa.
7. Retiro - dos revisiones sobre techo o fallo duro cancelan compras y dejan las ventas vivas mientras haya inventario; cuando queda sin inventario, puede cerrar y reemplazar.
8. D8 - agregado el medidor de analisis de solo lectura para S1. Pendiente de ejecutar por Ramon despues del despliegue; no se ejecuto en este entorno.
9. Compatibilidad - primera suite encontro que la apertura normal pasaba `meta_id=None`; `api/routes/grids.py:509` omite el argumento fuera de Meta. Prueba del archivo: 42 passed.

### Sigue abierto

- D5 solo esta cubierto parcialmente de forma integrada. Hay prueba de API asincrona -> iniciar -> abrir/enlazar con exchange falso para ambos perfiles, apertura serial parcial, cartera de dos monedas, limites, retiros tras dos revisiones, bloqueo por DESCONOCIDO/75 %, parametros de prestamos, persistencia del plan de control y registro sombra. La apertura por API usa un plan sintetico; no recorre Scanner, Advisor y replay reales en la misma prueba integrada.
- La rotacion usa el P&L y las comisiones efectivamente registradas despues del cierre verificado, y hay cobertura de unidad de aceptar/rechazar el umbral. Falta una prueba integrada de la ruta completa `review_once` para ambas decisiones hasta comprobar reapertura/no reapertura.
- La adicion de efectivo libre esta cubierta en el asignador y el bloqueo en el ciclo de revision; falta una prueba integrada que combine apertura real de la pieza añadida con exchange falso.
- D8 sigue sin datos de tiempos reales de S1. Debe medirse despues del despliegue.
- La estimacion de grids Smart no modela prestamos; el aviso lo declara. La cartera y las senales son una estimacion historica, no una garantia ni calibracion estadistica.

La suite final quedo verde: 1.450 passed, 1 skipped, 18 deselected, 55 warnings, 350,61 s; las 18 pruebas live se excluyeron. La primera suite tuvo un fallo de compatibilidad ya corregido. Por estos limites, la fase no afirma certificacion completa de D5 ni medicion S1. No hubo commit ni despliegue.

## 40b-3 — estado de discrepancias

### Cerradas

- Bloqueo: Scanner/Advisor/replay/cartera ya no ocupan `GridMonitor.ops_lock`; el guard separado impide análisis de revisión solapados y registra la revisión omitida. La acción vuelve a validar estado, valoración, freno de pérdida y precio bajo el lock.
- Selección: desempate histórico reproducible por progreso mediano, días y peor caída cuando empata la probabilidad de replay; el plan explica el caso sin aciertos y el capital libre.
- Errores observados en S1: cobertura de libro con la forma del cliente real y de ventanas cuyo precio histórico difiere más de 50 %.
- Cache persistente diario para Advisor/replay y reducción de consultas de Advisor por moneda.

### Abiertas

- No hay corrida posterior del medidor en S1, ni medición del análisis con mercado real en este PC. El objetivo de análisis frío ≤2 min y revisión ≤60 s no está demostrado. Una extrapolación condicional deja el análisis frío cerca de 17 min, dominado por replay; no se presenta como resultado medido.
- No se aplica escalado de replays por capital porque faltan mediciones de invariancia en mínimos de notional, polvo, comisiones y niveles.
- No se añadió precálculo diario, procesamiento paralelo, ni replay a 15 min. No hay benchmarks comparables; el runner rechaza velas de 900 s y no se altera su fidelidad.
- En el caso histórico de S1, los 30 USDT libres se explican por el límite de 70 % por moneda y la evaluación de carteras; falta reejecutar tras el nuevo desempate para saber si la segunda moneda empeora la puntuación recalculada.

La suite final está verde: 1.456 passed, 1 skipped, 18 deselected, 55 warnings. No se hizo commit ni despliegue.

## 40b-4A — discrepancias

### Cerrado

- El inventario ya no se valora multiplicando todo por el mejor bid. Se consume el libro hasta cinco niveles; el valor ejecutable incorpora comisión y se usa para el límite, el aviso del 75 % y el plan de cancelación. El valor del mejor bid también se conserva para mostrar ambos.
- Estados `KNOWN`, `PARCIAL` y `DESCONOCIDO`; estado y cadencias de Telegram persisten por meta y moneda. El umbral de pérdida requiere dos ciclos consecutivos y su contador persiste en `meta_runs`.
- La meta no cierra automáticamente cuando la valoración es DESCONOCIDO; bloquea nuevas piezas y alerta después del umbral configurado.

### Sigue abierto

- La estimación de inventario restante de un libro PARCIAL aplica haircut y comisión, pero no garantiza que esa cantidad pueda venderse al último nivel observado. La profundidad se mantiene en cinco niveles y se limita a una lectura por símbolo/minuto.
- B queda pendiente: pérdida máxima en replay, precálculo diario y lectura de caché persistente en modo solo lectura para el medidor.

## 40b-4B — discrepancias

### Cerrado

- El replay corta a pérdida máxima usando el low por vela, vende el inventario simulado con comisión y calcula el avance con pérdida realizada. Cada alternativa cuenta sus ventanas cortadas; en el bot, también cuenta compras y ventas.
- El optimizador compara X/Y a tres distancias de compra bajo el midpoint, con margen neto mínimo del perfil más 0,01 puntos de tolerancia de redondeo y el mismo criterio de selección.
- Precalculo UTC a las 08:00, switch validado, un solo worker de baja prioridad cooperativa y sin `ops_lock`. Se precargan ambos perfiles y las asignaciones que salen de objetivos estándar o metas existentes. Los misses se calculan en el análisis normal.
- El medidor puede leer la caché persistente en solo lectura con `--con-cache`; los aciertos del proceso y persistentes se separan.

### Sigue abierto

- No es posible informar retrospectivamente cuántas ventanas de la medición S1 anterior compraron y vendieron el bot XRP (X=1,2191; Y=1,3942): ese replay no guardó esos contadores. La nueva salida los expone, pero hay que ejecutar el próximo análisis en S1 para obtener los números reales.
- El replay aproxima la protección con el low de la vela y la comisión; no simula el spread/profundidad intravela. Tampoco simula el aviso del 75 %, porque no añade nuevas piezas dentro de una ventana.
- El precálculo no garantiza terminar un día antes de las 08:00 siguiente; la ejecución y el cache parcial permiten que el análisis complete los misses a demanda. El tiempo real y el ahorro deben medirse en S1.

Suite completa tras 40b-4B: 1.462 passed, 1 skipped, 18 deselected, 55 warnings, 299,58 s; la evidencia del bot XRP de S1 continúa pendiente porque la corrida anterior no guardó actividad por ventana.

## 40b-5 — señal toque compra→venta

### Cerrado

- Se implementaron las estadísticas puras solicitadas con deduplicación, censura, separación de velas para compra/venta y excursión adversa. X/Y se reescalan con el inicio de cada ventana del replay.
- La señal se calcula por celda y horizonte, se agrega ponderada por toques, se conserva con peso cero y se enlaza tanto a la bitácora como a `meta_signal_observations`. Se persiste también en `meta_analysis_cache` y forma parte del precálculo porque este ejecuta el mismo análisis.
- Se registran tiempos separados en `timings_s.touch_signal`. Las pruebas enfocadas y la suite offline completa están verdes.

### Sigue abierto

- Falta medir con el medidor en S1 cuánto suma `touch_signal` al tiempo de replay frío y caliente. No se usaron datos reales ni Testnet en esta implementación; por tanto, el límite aproximado de 5 % no se declara certificado.
- La señal permanece fuera de toda probabilidad y decisión (peso cero); aprender/calibrar su utilidad corresponde a 40d.

Suite completa: 1.473 passed, 1 skipped, 18 deselected, 55 warnings, 305,22 s. Próxima fase: 40c.

## 40b-6A — Prudente con 30 verificaciones

### Cerrado

- El gate de Prudente ya no exige 50 verificaciones; usa 30 por defecto, editable en `meta_profile_settings`. La capacidad mínima de la ventana vigente es 720 filas / 24 = 30 verificaciones efectivas.
- La pantalla presenta el conteo requerido con decimal español y traduce los estados `datos_insuficientes` y readiness ausente.
- Un readiness ausente significa que `get_readiness(symbol)` no encontró fila en `coin_readiness`, no que el modelo esté listo. `complete` sigue exigiendo estado `lista` y pronóstico reciente; la exclusión de Prudente permanece activa hasta cumplirlo.

### Pendiente

- No se inspeccionó una BD para certificar la historia concreta de onboarding de XRPUSDT. La causa observable en el código es que no existe una fila de readiness cuando la API devuelve `None`; el origen de esa ausencia requiere evidencia de la BD pertinente.
## 40b-6B — Reajuste activo de piezas Meta

### Cerrado

- El diseño aprobado se implementó: materialidad 5 pp / empate ±2 pp y avance +10 %, editable por perfil; un nivel del borde o dos revisiones fuera con desplazamiento mayor a un nivel habilitan evaluación; cooldown de 1 h por grid y máximo de cuatro reajustes en 24 h por meta.
- Bots individuales sin inventario mueven compra y venta en el mismo grid; tras comprar conservan su venta viva. El motor normal de ajuste retiene celdas con inventario y sus ventas. Capital se cambia únicamente en grids Meta, con comprobación de saldo libre atribuible y límite de capital por moneda.
- Las piezas Meta quedan excluidas del ajuste, re-espaciado e idle-shrink autónomos del monitor. PAUSE/RESUME, cierre Smart de seguridad, stop-loss por celda según interruptor, vigilancia/cierre de 40a y gestión de préstamos siguen activos.
- El disparador de techo deja de cerrar/reabrir automáticamente una pieza sin mejora de simulación; el gate duro del Scanner en dos revisiones conserva su retirada sin liquidar inventario.
- A5 precalcula por perfil/base y continúa cuando una de las combinaciones no tiene candidatas. A6 mantiene el objetivo escrito tras crear una meta.

### Límites explícitos

- No se cambia capital cuando el grid está pausado/no activo, no tiene celdas libres suficientes o conserva préstamos abiertos que impidan redistribuir; el operador espera otra revisión. El motor no fuerza reanudación para aplicar cambios.
- El replay es una estimación; los cambios de capital usan la asignación de la pieza elegida por el mismo plan y no crean una simulación de préstamos nueva.
- Sin discrepancias de alcance conocidas tras la suite completa offline. La medición operativa en S1 y cualquier despliegue siguen pendientes de Ramón.
