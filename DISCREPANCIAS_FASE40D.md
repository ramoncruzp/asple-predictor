# Discrepancias Fase 40d

## 40d-1

- **Muestra real no contada:** no se consultó la BD de S1 ni una copia real. El estado disponible solo confirma la Meta #3 descrita por Ramón; las cantidades de filas `meta_ledger`, `meta_signal_observations`, `vol_forecasts`, `predictions` y `outcomes` requieren una medición posterior en BD de prueba.
- **Scanner, Advisor y `break_prob` históricos:** no hay snapshots históricos fechados suficientes para reconstruirlos causalmente. El backfill los omite y registra esa omisión; solo reconstruye fuentes que sí tienen timestamp (velas, `vol_forecasts`, `predictions`) o derivación causal de las velas (toques y volatilidad realizada).
- **Alternativas históricas:** se evalúan como contrafactuales reanclando la geometría guardada y estimando `p_replay` solo con bloques anteriores al corte; no se afirma que Scanner/Advisor hubieran elegido esa alternativa en cada fecha histórica.
- **Medición de rendimiento:** el job registra tiempos por etapa y la prueba sintética verifica que queden disponibles. No se corrió el backfill de 180 días con mercado real ni S1; ese costo real sigue pendiente de medir en entorno de prueba.
- **Fuera de esta parte:** ajuste de pesos con regresión, errores agrupados, Holm, criterios de evidencia, sombra aprendida, interruptor y vista quedan para 40d-2/40d-3.

No se detectaron discrepancias de comportamiento en grids normales: el job solo lee velas/tablas y escribe las tres tablas nuevas de aprendizaje. La suite offline completa pasó.
