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
