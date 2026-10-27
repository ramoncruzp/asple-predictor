# Discrepancias Fase 41a

## Fase 41b — línea base previa

- La medición previa es sintética y se expresa como tamaño JSON compacto UTF-8. No representa un promedio de una base real: se documentan sus entradas para que el contraste sea reproducible sin datos reales.
- Los eventos v1 conservan su forma; el tamaño se corrige solo en nuevas filas con esquema 2. No se reescriben filas históricas.
- Los lectores de Inteligencia siguen devolviendo los detalles de eventos existentes. Los consumidores de alternativas usan claves retenidas en v2; las recomendaciones completas siguen una sola vez en `advisor_comparisons`.
- El JSON expandible de Inteligencia muestra ahora el formato compacto v2 para eventos nuevos: alternativas enlazan sus recomendaciones por símbolo/margen y revisiones enlazan el plan con `plan_decision_id`. Se conserva la información completa en el evento de plan y la pantalla Metas no cambió; las decisiones, sus probabilidades y los cruces por `alternative_id` se mantienen iguales.
- La prueba de tamaño usa un fixture sintético representativo (tres monedas, carteras de una a tres piezas, márgenes y recomendaciones completas); no se dispone de una medición agregada de una BD real y no se consultó ninguna.
- El stub de la prueba integrada `test_review_once_opens_a_real_piece_on_the_fake_exchange_using_free_cash` se amplió para aceptar el argumento `plan_kind`; la primera corrida dirigida detectó esta incompatibilidad de firma, no una regresión de producción.
- El serializador completa `sigma_24h` desde el campo que entrega el Scanner (`sigma_24h_open`) y conserva `price`/`current_price` si están disponibles; en el resultado de prueba la sigma sí está disponible, pero el precio depende de que el Scanner lo entregue.

## 41a

- Las muestras live anteriores a la corrección no se reescriben. Las importadas desde bitácoras históricas se marcan v1; las filas viejas sin versión también se consideran v1. Se excluyen del entrenamiento las señales de volatilidad, ruptura y dirección, pero se conservan para diagnóstico y para las otras señales.
- El inventario de volatilidad de la pantalla Inteligencia muestra registros guardados y su horizonte; no deriva una sigma nueva. Por eso no se cambió su API ni su presentación.
- `database/db_manager.py:get_vol_history` expone `pred_vol_pct` como exponencial del valor guardado para el horizonte solicitado, sin etiquetarlo como sigma de 24 h. No se identificó un uso como sigma 24 h y se dejó intacto.
- Las 1153 muestras live y el máximo observado de P(baja)=0,533 provienen del diagnóstico de S1 informado por Ramón; no se verificaron ni se consultaron S1 en esta ejecución.
- No se encontraron otros consumidores fuera del Modo Meta que traten de forma confirmada sigma horaria como sigma 24 h. El proveedor de volatilidad y la ruta `/api/volatility` ya convierten por horizonte.
- No se ejecutó la suite completa. Se corrieron solamente los tres archivos dirigidos solicitados.

## Continuación 41b (2026-10-26)

- La telemetría previa al cambio fue un microbenchmark del cálculo causal de toque con 51.840 velas; no se midieron por separado Advisor, replay, conversión de `_load_candles` ni el tiempo total de `_build_plan`. Tampoco se comparó el plan integral contra `24f2ba7` ni se cronometró el cliente artificial con 2 s de demora. No se atribuye la demora reportada en S1 exclusivamente al toque y no se afirma cumplido el objetivo ≤2 min.
- La caché de toque cuantiza geometría en pasos relativos de 0,01 % como permite el pedido. Una diferencia inferior a esa resolución puede reutilizar el último valor causal; queda pendiente medir en el arnés integral el impacto numérico agregado frente a una corrida sin caché.
- `META_OPERATOR_ROTATION_EVALUATED` conserva los datos decisorios necesarios para persistencia y auditoría breve, mientras que el detalle extenso queda representado por la evaluación vinculada. La pantalla no consumía sus alternativas completas.
- La suite completa no se ejecutó; la verificación fue solo sobre cinco archivos enfocados.

## Fase 41b-11 — parte 1 (2026-10-27)

- Sin cambios de reglas ni discrepancias funcionales en esta parte. Un fallo de comparación se registra en `decision_choices_error` tanto dentro de la comparación de respaldo como en el evento de revisión y el NOOP correspondiente; no se inventan decisiones de sombra cuando el cálculo falla.
- Las cifras de S1 provienen del prompt de Ramón; la reproducción y las pruebas son locales con dobles de prueba. La corrección del autoconflicto del grid propio corresponde a la parte 2 y al commit 41b-11b.
- Se usó una carpeta nueva bajo `$env:TEMP`, según el pedido específico, que tiene prioridad sobre la carpeta fija indicada en `AGENTS.md`. Solo se ejecutaron los dos archivos dirigidos: 77 pruebas verdes.

## Fase 41b-11b — evaluación de la pieza propia (2026-10-27)

- Sin cambios de reglas ni pendientes funcionales del alcance solicitado. Desde 41b-11 la Meta puede volver a reajustar/rotar una pieza que tenga ventas vivas, sujeta a los mismos frenos y a la revalidación antes de ejecutar.
- Si existen varias piezas propias del mismo símbolo, solo se excluye una (menor ID); no se ignoran en bloque los conflictos ni las plazas de las demás. Esto hace explícita la identidad del reemplazo evaluado en `own_grid_excluded_from_check`.
- `include_held_proceeds=True` permite estimar un reemplazo tras cierre; no autoriza usar ese dinero para una apertura adicional ni para retirar conservando inventario. Iniciar y agregar piezas conservan sus chequeos completos.
- La ocupación del grid reemplazado se descuenta solo si la consulta ya lo cuenta: HOLDING no genera una plaza libre ficticia. El límite global conserva su valor y su finalidad.
- La primera corrida detectó un fixture incompleto (`avg_price`); tras corregirlo, los tres archivos dirigidos quedaron verdes: 87 passed. Sin suite completa ni uso de S1, Testnet, `.env` o base real.

## Fase 41c-0 — medición y alcance (2026-10-27)

- El escenario mantiene los límites de `a062e09`: seis candidatas, cuatro elegibles, lista corta de tres, 100 carteras y 297 alternativas de pieza (894 ajustes de probabilidad). No se expandió la búsqueda a 950 carteras para evitar cambiar el resultado.
- La medición representa caché caliente: replay y Advisor se sustituyen por resultados deterministas, y las lecturas de exchange/BD falsos duermen 50 ms. No mide el Scanner real, un replay frío ni S1. El total del análisis baja de 148,26 a 2,24 s; análisis más decisión optimizados: 2,39 s. Los subtiempos de etapas anidadas se solapan.
- Se conservaron los resultados exactos en la regresión contra `a062e09`. Las lecturas se fijan al primer acceso durante el plan; se renuevan entre planes y al ejecutar. No se tocaron reglas, pesos, probabilidades, límites ni simuladores.

## Fase 41c-1 — decisiones y límites documentados (2026-10-27)

- Ramón confirmó: éxito por efectivo atribuible agregado más capital no asignado; pérdida máxima, avance, drawdown y peor pérdida por patrimonio agregado. Es el criterio del núcleo (grid/meta.py:704-718). El punto 3 del prompt queda precisado: el inventario no adelanta el cumplimiento, aunque su valor sí cuenta para riesgo y avance.
- Las curvas usan precio de cierre simulado y comisión estimada de venta del 0,1 %. No se integró profundidad histórica del libro; es una estimación reproducible con velas.
- Las piezas se agregan solo en timestamps comunes. Las curvas se serializan como float32; puede haber redondeo de precisión simple. No se guardan las velas.
- El runner Meta desactiva únicamente en la simulación de cartera el cierre de repositorio por pieza; el corte de pérdida pertenece a la cartera. El camino normal de grids no cambia. El bot se simula hasta el final de la ventana y conserva DONE sin recompra.
- Los cinco ejemplos sintéticos no son una comparación de mercado ni una afirmación de superioridad. El avance puede cambiar ampliamente al quitar objetivos individuales por pieza; no es una promesa.
- El método se versionó como rolling_7d_v3_equity. Las entradas v2 sin curva se recalculan; database/db_manager.py:517-521 compara con la versión vigente para no volver a marcar v3 en cada arranque. No se eliminaron filas ni versiones. Muestras y etiquetas incompatibles no se combinan.
- Pruebas dirigidas: 143 passed, 9 warnings, 0 failed. No se ejecutó la suite completa. No se usaron S1, Testnet, .env, red ni base real.
- Fase 41c-2 no implementada.


## Fase 41b-12 — diferencias y límites (2026-10-27)

- Sin discrepancias funcionales conocidas: la estimación propia usa la geometría viva, curvas del replay `rolling_7d_v3_equity` y las señales aplicables; el bloqueo de Testnet se conserva únicamente para alternativas de destino.
- Si no hay velas/cache válida o falla el replay, el plan registra el error de esa pieza. Las rutas de revisión conservan el fallback a una alternativa equivalente cuando esté disponible; sin ambos resultados, la pieza se informa como no comparable.
- El benchmark usa datos y proveedor ficticios, replay cacheado y latencia simulada; el resultado de tiempo no certifica el rendimiento en S1. No se usaron S1, Testnet, red, `.env` ni BD real.

## Fase 41c-2a — diferencias

No se reprodujo la pérdida de `capital_usage` en el serializador local de `ea6dc6d`: el campo ya estaba incluido. Se añadió una prueba de contrato para el evento v2. No se consultó S1 ni una base real; el origen del `None` informado en S1 queda sin confirmar.

## Fase 41c-2b — diferencias y límites

- La medición usa replay, libros, Scanner y datos sintéticos con caché caliente y latencia de 50 ms. No demuestra el tiempo de producción o de S1.
- La apertura y el replay se vuelven a comprobar en cada escalón de capital. Si falla el filtro o replay de una variante, se omite esa variante y se conservan las otras.
- La nueva selección continúa en sombra por defecto. Con el perfil apagado, la ruta previa de efectivo puede seguir agregando como antes; el interruptor solo controla el motor ampliado.

## Fase 41c-2c — diferencias y límites

- C15 no requirió modificar el cálculo: el payload ya copiaba `capital_usage` en `ea6dc6d`; se agregó cobertura de contrato.
- La paridad adapta el esquema solo para cuatro campos nuevos permitidos: `current_pieces`, `allocation_shadow`, `use_extended_allocation` y `allocation_tie_tolerance_pp`. Cada uno se afirma explícitamente; todo campo previo conserva comparación exacta y un campo inesperado hace fallar la prueba.
- Benchmark y pruebas usan dobles locales. No se consultó S1 ni Testnet real.

## Fase 41f — diferencias y límites

- No se invoca el endpoint HTTP del Advisor ni un modelo persistido: el script replica la fórmula del rango centrado moderado y llama el selector compartido `grid.structure.select_levels` con σ realizada de las velas descargadas. Así la medición es reproducible y no necesita BD ni `.env`, pero no reproduce pronósticos guardados, widening, modo estructural ni filtros de disponibilidad del endpoint en cada fecha. La geometría queda identificada en la salida como procedencia; el resultado debe leerse como medición de esa estructura representativa.
- Para `--source csv`, los archivos deben ser continuos y alineados a 5m/1m; se usan los filtros genéricos que trae el runner. Para Binance se consulta el filtro público de cada símbolo. La comparación mide solo el orden/precisión de velas y no simula profundidad ni ejecución intravela real.
- El wrapper existente valida una lista de intervalos que omite `1m`; para esa resolución el script usa el mismo cliente público subyacente, sus reintentos y paginación `get_klines`. El adaptador se probó con un cliente falso, sin abrir conexión.
- `run_simulation` originalmente limita su cálculo interno de σ a 5m/1h. El script entrega un vector de σ ausente para 1m, manteniendo su motor de órdenes y fills. ADJUST, stop-loss y el cierre temporal están desactivados en ambos intervalos; este vector no constituye una nueva regla ni modifica el código del simulador.
- La métrica de “ciclos perdidos” expresa el aumento de ciclos observado a 1m frente a 5m, dividido por los ciclos 1m; vale cero cuando 1m no completa ciclos. Toques exactos se cuentan por orden vigente, y velas ambiguas por vela, no por nivel.
- La corrida de rendimiento usó velas sintéticas repetitivas, por lo que su resultado (37,83 s para 3 símbolos/30 días) verifica la escala del trabajo, no el tiempo de red ni la representatividad de mercado. El análisis Binance queda pendiente de la ejecución manual en S1.

## Fase 41c-2d — diferencias y límites

- El límite de 30 s se verifica en puntos seguros antes/después de cada alternativa, llamada al Advisor/replay y monto. Como el flujo es síncrono, una llamada que ya comenzó no es cancelable y podría rebasar el plazo; las siguientes evaluaciones sí se detienen y quedan contadas como pendientes.
- La geometría se vuelve a generar por capital mediante el mismo `_candidate_alternatives` que el análisis. Se mantienen solo las alternativas del tipo candidato original y hasta tres opciones generadas por monto; esto limita la búsqueda y no pretende optimizar exhaustivamente cada monto.
- Las pruebas usan exchange falso y replay local. No miden rendimiento con datos de mercado ni prueban un libro real de Testnet.
- La prueba de paridad del flujo real lo compara con `_review_allocations` de HEAD con el interruptor de asignación apagado; la nueva información y los descartes solo amplían la sombra.

## Fase 41b-13 — diferencias y límites

- `meta_learning_replay_cache` no tiene columna de versión; la vigencia se estima comparando sus IDs deterministas con los reconstruidos desde las muestras actuales. Sin muestras, se conserva la caché reciente y se aplica solo la antigüedad.
- `META_MAINTENANCE` requiere `meta_id` por la clave foránea de `meta_ledger`. Cuando no hay metas, el resultado queda en el log de aplicación sin crear registros ficticios.
- El script solo admite SQLite y no crea respaldos: requiere uno del día o una confirmación explícita `--backup-ok`. La comprobación `systemctl` y el ahorro físico de VACUUM quedan pendientes de ejecución manual en S1.
- La estimación de espacio para `meta_learning_replay_cache` usa 96 bytes por fila y es aproximada. Ninguna base real se abrió y ningún VACUUM se ejecutó en esta fase.


## Fase 41-G1a ? discrepancias

No se eliminan las entradas de cach? antiguas durante el cambio: si contienen curvas de formato anterior, se consideran no reutilizables al leerlas y se regeneran; el borrado f?sico queda a cargo de la retenci?n ya existente. La validaci?n de tama?o usa una trayectoria sint?tica suave de 180 d?as; otras trayectorias con mayor entrop?a podr?an comprimir menos y deben observarse mediante tama?os reales al correr el mantenimiento en S1.


## Fase 41-G1b ? discrepancias

La geometr?a com?n se deriva de los cierres 5m y de sigma 5m causal, aunque 1m puede mostrar otro recorrido dentro de la primera vela. Esa es la comparaci?n solicitada y evita que cada resoluci?n ajuste su propia estrategia. Las m?tricas 1m no constituyen una reconstrucci?n del orden real dentro de cada vela.


## Fase 41-G1c ? discrepancias

Los errores de excepci?n del proveedor quedan vinculados a la moneda del grid cuando MetaCore puede identificarla. Los libros con estado DESCONOCIDO registran el motivo ofrecido por `executable_value`; no se infiere la causa si el proveedor no la devuelve. Inteligencia limita el detalle a 20 motivos y el c?lculo a 5.000 decisiones recientes en siete d?as. No se cambi? la condici?n de cancelaci?n.


## Fase 41-G1d ? discrepancias

La cobertura activa se calcula sobre filas de muestra con se?al `direction` guardada y usa `p_down` (con fallback al valor crudo); su porcentaje usa el total de muestras Meta consultadas como denominador para distinguirla de la cobertura general del extractor. El aprendizaje mantiene la sem?ntica existente: P(baja) < 0,60 aporta 0 neutral y cuenta como observaci?n presente.


## Fase 41-G1e ? XRP sin estado

Se conserva el estado ausente: crear filas autom?ticamente durante an?lisis Meta ser?a una escritura ajena al alcance de lectura y podr?a alterar el flujo de Coin Registry. La explicaci?n es la falta de un registro de onboarding, y el perfil Prudente sigue exigiendo readiness lista.


## Fase 41-G1f ? discrepancias

MetaOperator no guarda un evento durable al completar su prec?lculo, as? que un script de solo lectura no puede reportar con certeza la hora/estado de ese job. Se informa la ausencia de registro y la ?ltima escritura de cach? como referencia expresamente no equivalente. `dbstat` y `journalctl` dependen de soporte del SQLite compilado y de que el script se ejecute en el host Linux; sus fallos se muestran como no disponibles.
