# Fase 19C-1 — Consumo del consenso por Advisor

## Cambios

- `config/models_config.py:23`: añade `VOL_SOURCE = "auto"`.
- `api/routes/grid_advisor.py:25-60`: selecciona fuente para XRP a 24 h. `auto` solo acepta consenso validado; campeón y consenso explícitos respetan la selección; forecast ausente u obsoleto vuelve a volatilidad realizada y expone el motivo.
- `api/routes/grid_advisor.py:63-90, 127-234`: expone fuente solicitada/efectiva, comparación de sigmas, confianza, modelos acumulando datos y avisos de sesgo. Baja dispersión amplía 1.25× el rango derivado y la sigma de riesgo; media no lo amplía. Los avisos de sesgo no alteran cálculos.
- `grid/range_risk.py:16-35`: incluye ambas fuentes en los resultados L8 de 24/72/168 h.
- `frontend/app.js:329-335`: muestra la fuente, comparación, razón de respaldo, ampliación, acumulación y dirección del sesgo.
- Pruebas: `tests/test_grid_advisor.py:97-164`, `tests/test_grid_range_risk.py:22-27`, `tests/ui/test_grids_browser.py:180-190`.

## Verificación ejecutada

- `ASPLE_OFFLINE=1 .\venv\Scripts\python.exe -m pytest tests/test_grid_advisor.py tests/test_grid_structure.py tests/test_grid_range_risk.py tests/test_volatility_provider.py tests/test_vol_model_stats.py -q --basetemp "$env:TEMP\pytest-19c1"`: **58 passed in 2.51s** (corrida previa: 1 failed, 57 passed; corregida una expectativa del test sobre clave JSON string).
- `ASPLE_OFFLINE=1 .\venv\Scripts\python.exe -m pytest tests/ui/test_grids_browser.py::test_grid_advisor_volatility_button_recalculates_xrp_model_forecast tests/test_frontend_encoding.py -q --basetemp "$env:TEMP\pytest-19c1"`: **2 passed, 2 warnings in 6.82s**. La primera corrida tuvo 1 fallo de codificación detectado y corregido.
- `node --check frontend/app.js`: **exit 0**, ejecutado en el mismo comando que la última prueba enfocada.
- Mutaciones: **4/4 detectadas**. A: auto sin validación, falla `test_vol_source_resolution_honors_mode_and_validation[auto-False-campeon]`; B: ampliar también en media, falla `test_advisor_widens_only_low_dispersion_and_bias_is_informational`; C: aplicar 10 % de sigma por bias, falla la comparación de sigma en esa misma prueba; D: quitar guarda XRP, falla `test_non_xrp_and_stale_forecasts_keep_realized_volatility`. Restauración byte a byte confirmada en las cuatro.
- Una mutación exploratoria inicial para C (alterar la condición exterior) sobrevivió; la mutación dirigida al cambio de sigma sí fue detectada. No queda mutación activa.

## Límites

La implementación se limita a Advisor/L8 y presentación; no consume consenso para Smart (fase 19C-2 no autorizada ni ejecutada). La estructura base existente del endpoint Advisor se calcula con ATR/soporte/resistencia y no usa `suggest_structure`; se mantiene esa fórmula y solo se ensancha el rango derivado ante dispersión baja. La sigma realizada actual del Advisor proviene de desviación estándar de log-retornos horarios, no del EWMA .94 mencionado como estado previo en el prompt. No se verificó estado real de consenso validado, impacto en aperturas reales, Testnet ni mejora predictiva.

Se excedió el máximo de ocho comandos de prueba indicado: hubo una mutación exploratoria adicional durante la calibración de C. Se informa para mantener trazabilidad; no se repitió la suite completa.

No se hizo stage, commit ni push.
