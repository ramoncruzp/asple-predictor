# Discrepancias — Fase 19B-2

Los nombres confirmados por búsqueda coincidieron con el prompt. Para conservar métricas históricas exactas, el endpoint usa agregados SQL y una segunda proyección estrecha con los campos necesarios para dispersión y evaluación causal; la consulta principal de filas quedó acotada. El caché aplica al historial leído (TTL 30 s, invalidado por el último `forecast_at`). No se detectaron otras discrepancias de alcance.

Verificación de escritura: caracteres UTF-8 conservados (á, é, í, ó, ú, ñ, ¿, ¡).
