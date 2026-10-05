# Fase 19B-2 — correcciones de auditoría

## Cambios

- `models/volatility/model_stats.py:137-180`: una ordenación por `verified_at`, punteros monotónicos, deque de hasta 168 errores y suma cuadrática incremental reemplazan el escaneo de todo el pasado en cada timestamp.
- `database/db_manager.py:659-691`: `get_vol_model_stats_rows` limita las verificaciones maduras a las últimas `ROLLING_VERIFICATIONS + N_MIN` por modelo e incluye filas sin verificar y pronósticos aún inmaduros. `database/db_manager.py:693-744` conserva las métricas de todo el histórico mediante agregados SQL. La consulta secundaria estrecha de `database/db_manager.py:745-756` conserva las filas mínimas requeridas para buckets exactos, pesos causales y evaluación forward.
- `api/routes/volatility.py:23-48,252-287`: caché de 30 s por símbolo/horizonte; consulta `forecast_at` máximo y la invalida cuando cambia. La ruta usa los agregados históricos al formar el reporte.
- `models/volatility/model_stats.py:19,319-333`: la compuerta usa `n_efectivas = n / horizonte_h` y requiere 50. La API expone `n` y `n_efectivas` en `/model-stats` y `forward`.
- `frontend/app.js:294,298`: tabla “Sobre/Sub %” consume porcentajes 0–100 sin multiplicarlos otra vez; muestra n y n efectivas. `frontend/styles.css:17` da formato gris informativo a “Sesgo sostenido (revisar)”.
- `models/volatility/model_stats.py:214,263-270`: `bias_alert` solo es informativo; exige al menos 30 verificaciones, sesgo absoluto mayor que `0.5 × sigma_ref` y mismo signo en al menos 80 %.
- `REPORTE_FASE19B.md`: corregí 59 porcentajes sobre/sub que estaban multiplicados por 100.
- `tests/test_vol_model_stats.py:86-284`, `tests/ui/test_grids_browser.py:111-138`: snapshot numérico, ventana sintética de 5.000 timestamps/modelo, límite SQL con fila pendiente, compuerta efectiva, sesgo y caché; la prueba UI cubre columnas, alerta y n efectivo.

La regla de 50 muestras efectivas es una aproximación al traslape: no corrige autocorrelación completa. No relaja MSE P ≤ MSE del campeón ni la cobertura dentro de ±10 puntos porcentuales de 68 %/95 %.

## Medición y resultados

En el mismo conjunto determinista de 5.000 timestamps por cada uno de cuatro modelos (20.000 filas), el algoritmo de referencia anterior tardó **50.772 s** y el de ventana deslizante **0.088 s**: **578.4×** en esta máquina. Sin umbral de rendimiento impuesto. El test de snapshot compara el resultado numérico final con el recorrido anterior para el mismo conjunto pequeño.

- Primera corrida enfocada: 3 fallos en expectativas sintéticas (elegibilidad del benchmark, colocación de la fila pendiente y umbral de signo mixto); corregidos y repetida toda la selección. Resultado final: **34 passed, 2 warnings, 11.57s**.
- Mutación de filas no verificadas: falló `test_bounded_history_keeps_unverified_rows_and_limits_verified_tail` como se esperaba; bytes restaurados idénticos.
- Mutación de n bruto: falló `test_live_gate_uses_effective_sample_count_at_49_and_50` como se esperaba; bytes restaurados idénticos.
- Mutación de signo de `bias_alert`: falló `test_bias_alert_requires_large_persistent_same_sign_bias` como se esperaba; bytes restaurados idénticos.
- `node --check frontend/app.js`: exit 0.
- `compileall` de Python modificado: exit 0.

## No verificado

No se midió tráfico de producción ni historiales de meses en una base PostgreSQL. El benchmark es sintético/local; no prueba latencia de red o del resto del endpoint. El aviso no representa una conclusión estadística sobre regímenes futuros. No se usó Binance/Testnet, `.env` ni entrenamiento manual. No se ejecutó suite completa.

Archivos nuevos y editados guardados en UTF-8 sin BOM; los textos visibles nuevos de JavaScript usan escapes `\\uXXXX`. No hubo stage, commit ni push.

Verificación de escritura: caracteres UTF-8 conservados (á, é, í, ó, ú, ñ, ¿, ¡).
