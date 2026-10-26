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

## Continuaci?n 41b (2026-10-26)

- La telemetr?a previa al cambio fue un microbenchmark del c?lculo causal de toque con 51.840 velas; no se midieron por separado Advisor, replay, conversi?n de `_load_candles` ni el tiempo total de `_build_plan`. Tampoco se compar? el plan integral contra `24f2ba7` ni se cronometr? el cliente artificial con 2 s de demora. No se atribuye la demora reportada en S1 exclusivamente al toque y no se afirma cumplido el objetivo ?2 min.
- La cach? de toque cuantiza geometr?a en pasos relativos de 0,01 % como permite el pedido. Una diferencia inferior a esa resoluci?n puede reutilizar el ?ltimo valor causal; queda pendiente medir en el arn?s integral el impacto num?rico agregado frente a una corrida sin cach?.
- `META_OPERATOR_ROTATION_EVALUATED` conserva los datos decisorios necesarios para persistencia y auditor?a breve, mientras que el detalle extenso queda representado por la evaluaci?n vinculada. La pantalla no consum?a sus alternativas completas.
- La suite completa no se ejecut?; la verificaci?n fue solo sobre cinco archivos enfocados.
