# Discrepancias Fase 40d

## 40d-1

- **Muestra real no contada:** no se consultó la BD de S1 ni una copia real. El estado disponible solo confirma la Meta #3 descrita por Ramón; las cantidades de filas `meta_ledger`, `meta_signal_observations`, `vol_forecasts`, `predictions` y `outcomes` requieren una medición posterior en BD de prueba.
- **Scanner, Advisor y `break_prob` históricos:** no hay snapshots históricos fechados suficientes para reconstruirlos causalmente. El backfill los omite y registra esa omisión; solo reconstruye fuentes que sí tienen timestamp (velas, `vol_forecasts`, `predictions`) o derivación causal de las velas (toques y volatilidad realizada).
- **Alternativas históricas:** se evalúan como contrafactuales reanclando la geometría guardada y estimando `p_replay` solo con bloques anteriores al corte; no se afirma que Scanner/Advisor hubieran elegido esa alternativa en cada fecha histórica.
- **Medición de rendimiento:** el job registra tiempos por etapa y la prueba sintética verifica que queden disponibles. No se corrió el backfill de 180 días con mercado real ni S1; ese costo real sigue pendiente de medir en entorno de prueba.
- **Fuera de esta parte:** ajuste de pesos con regresión, errores agrupados, Holm, criterios de evidencia, sombra aprendida, interruptor y vista quedan para 40d-2/40d-3.

No se detectaron discrepancias de comportamiento en grids normales: el job solo lee velas/tablas y escribe las tres tablas nuevas de aprendizaje. La suite offline completa pasó.

## 40d-2

- **Validación mínima adicional:** el diseño pidió validar en el tramo temporal posterior, pero no dio un mínimo numérico para ese tramo. Se exige un mínimo de 5 observaciones efectivas de plazo; con menos, se guarda el intento como insuficiente y no se mueve ningún peso. Evita aceptar pesos con una validación demasiado pequeña.
- **Muestras `live` antiguas:** sus filas de 40d-1 no incluyen recuento de ventanas ni probabilidad suavizada; no se inventan. Se mantienen guardadas y excluidas por base insuficiente. Las muestras `historical_reconstructed` sí se marcan y recalculan con paso de 7 días.
- **Procedencia de señales:** la reconstrucción histórica genera volatilidad normalizada y dirección cuando hay fuentes fechadas equivalentes a las del operador. Scanner, Advisor y `break_prob` no existían causalmente en esas fechas; valen cero en el logit y no cuentan como evidencia para esos pesos. No se usan valores actuales para completar el pasado.
- **Medición y evidencia real pendientes:** no se contó muestra de BD real ni se midió el job en S1. El aprendizaje queda en sombra y no cambia decisiones; la suite completa no se corrió porque esta fase autorizó solamente los tres archivos enfocados más las pruebas nuevas.
- **Fuera del alcance:** sin vista ni interruptor de pesos aprendidos, sin cambiar decisiones del operador o grids normales, y sin incluir aún la señal de toques.


## 40d-3A

- La duración se mide como límite de pared de la fase de reconstrucción, sin interrumpir una simulación ya iniciada; el job termina esa ventana y guarda pendientes para la siguiente corrida.
- El estimador de costo usa un supuesto configurable de segundos por ventana (0,05 s por defecto) y 60 cortes de 72 h en 180 días; es una proyección previa, no una medición de S1. La ejecución del estimador no se realizó.
- El límite de tiempo y plantillas está editable por API autenticada; valor inicial 30 plantillas y 20 minutos.


## 40d-3 — Costo, sombra e interruptor

- La caché de ventana guarda el resultado binario de éxito. Para que el resultado sea reusable sin mezclar condiciones distintas, la huella estable añade la fracción objetivo/capital, el límite de pérdida del perfil y el margen además de la geometría pedida. No conserva precios ni capital absolutos.
- La alerta de deterioro usa las etiquetas contrafactuales existentes de 72 h para las alternativas real y aprendida. La versión se considera distinta cuando cambian los IDs de pesos efectivos guardados en la corrida diaria; si faltan etiquetas para ambas alternativas no se alerta. No desactiva el switch.
- El reporte de comparación de plazo solo tiene datos cuando existe la etiqueta madura para el horizonte del perfil. Ninguna simulación adicional se lanza para producir la vista.
- 40d-3A se committeó antes que 40d-3B como pidió Ramón; un ajuste de identidad de caché hallado al revisar la interacción quedó en el commit de 40d-3B y está descrito en `REPORTE_FASE40D.md`.


## 40d-3C

- El interruptor de pesos aprendidos vive en Laboratorio → Reglas globales y se confirma allí con actor obligatorio; hay un switch independiente por perfil. La ausencia de evidencia se informa, pero no bloquea la acción.
- Metas muestra aprendizaje global de ambos perfiles, con carga al entrar y actualización manual. La recarga periódica solo actualiza los datos vivos y se pausa mientras se edita un campo o hay confirmación, análisis o plan visible.
- Estado: implementada y verificada offline con las pruebas enfocadas; sin despliegue.


## 40d-4A - Comparación de decisiones

- La comparación nueva usa `comparison_version=1`; los registros anteriores se conservan y no se incorporan al conteo bajo el esquema nuevo.
- El ranking comparte las mismas claves y desempates entre las decisiones real, Scanner + Advisor y aprendida. Las revisiones sin acción registran las tres como mantener.
- Límite pendiente: las revisiones activas que sí ejecutan una alternativa comparan acción y destino registrados, pero todavía no reproducen de manera independiente todos los frenos de materialidad, persistencia y enfriamiento para una acción contrafactual que la política real rechazó. La comparación de diferencias para esas decisiones no debe interpretarse como evaluación completa de política.


## 40d-4B - Inteligencia

- No hay tabla persistente de velas en el esquema de la aplicación. La pestaña informa que ese inventario no está disponible desde la BD y no escanea `data/` ni consulta la red.
- Cerrado en 40d-6: versiones guardadas incluyen deciles de calibración y las corridas incluyen inventario de velas y conteos separados de replay/caché. La vista sigue siendo de solo lectura.
- El API limita a 10.000 filas la muestra de aprendizaje usada para estadísticas descriptivas; los totales por perfil/origen/moneda y los conteos de exclusiones se agregan en SQL.
- Cerrado en 40d-6: política pura compartida para reajustes/rotaciones y estado independiente de persistencia y cooldown para cada sombra.
- Listo para encender: el medidor usa la última versión guardada por perfil, pero la pantalla no propone activar automáticamente el interruptor.

## 40d-5

- Sin discrepancia en el cálculo causal: el backfill ya usaba `point_in_time_signals` y persistía la tasa bruta de `touch_stats`; se refactorizó esa rutina a `causal_touch_buy_to_sell` y ambos caminos usan ahora la normalización `2 × sell_rate − 1`. No se marcaron muestras para recálculo porque las existentes con el campo ya corresponden al mismo método.
- Las muestras antiguas sin `touch_buy_to_sell` permanecen guardadas, neutrales y ausentes de la máscara de presencia; su conteo efectivo para esa señal es cero.
- Límite cerrado en 40d-6: ahora se persiste calibración por deciles para el modelo y solo p_replay, en el conjunto de validación.
- No se consultó la BD productiva para medir cuántas filas históricas incluyen la señal; la verificación fue con pruebas locales y los caminos de código.
- Ajuste del harness de UI: la prueba de reajuste reciente asumía que el contenedor ya estaba en el DOM. Se espera explícitamente el selector y se conserva la misma aserción de texto; la corrida enfocada final pasa.


## 40d-6B

- Se corrigió la discrepancia de comportamiento detectada en 40d-6: el reajuste real había adquirido persistencia de dos revisiones y usaba `probability_replay`, y su contador de cuatro acciones filtraba por pieza. Ahora conserva la decisión de 374cec4: `estimate["probability"]`, una revisión por defecto, cuatro reajustes por Meta cada 24 horas y cooldown fijo de una hora por pieza.
- Las sombras usan los mismos frenos de reajuste con estado propio. La rotación mantiene su política separada, incluido su plazo de persistencia y su tope de acciones.
- La migración heredada corre una sola vez. Si el perfil guarda 2 y todavía no existe una Meta de ese perfil a la que ligar el evento, deja pendiente esa migración; un arranque posterior la aplicará y anotará. El marcador impide que un valor 2 elegido después se revierta en cada arranque.
- Pruebas enfocadas: `tests/test_meta_operator.py`, 60 passed, 4 warnings, 3,28 s. Sin suite completa, S1, Testnet, `.env` ni BD real.


## 40d-7A

- Las causas de saturación se derivaron del contrato de datos y del código local, sin medir las 765 muestras de S1. La diferencia probable de volatilidad por hora vs sigma de 24 h explica el −1; el mismo factor √24 llega a `break_prob`. La semántica del campo `probability_up` de `model_a_down` confirma que representa probabilidad de baja, mientras que el operador actual lo usa con signo positivo.
- Para toque compra→venta, el runtime condiciona su presencia a velas previas disponibles y tasa no nula. La frecuencia exacta de cada motivo para las 724 muestras sin señal requiere correr el script en S1; la fase no accedió a S1 ni a una BD real. La bitácora puede proporcionar motivos guardados si existen para el período consultado.
- La conversión de unidades y la inversión de signo de dirección se entregan como propuestas; ninguna se aplicó. Solo se añadió metadata de diagnóstico que no participa en la probabilidad real.
- Verificación local: 63 passed, 4 warnings; no se ejecutó la suite completa.



## 40d-6

- Cerrados los límites de comparación incompleta y falta de observabilidad. La comparación v2 corre la política compartida para reajustes y rotaciones, con materialidad, persistencia, enfriamiento y límite diario; cada sombra conserva su propio estado.
- Inventario y calibración se guardan como agregados/resúmenes, nunca como velas. Las corridas separan ventanas simuladas, aciertos de caché y cortes procesados.
- No se encontraron otras discrepancias dentro del alcance. Verificado solo offline con las pruebas focalizadas; S1, Testnet y la BD real no se usaron.
