# Fase 19C-3b - Dirección del factor sugerido

## Cálculo y definición

Se resuelve por bisección la cobertura normal usando rho = exp(realizado_logvol - pred_logvol_cal): k_global para 68 % y k2 para 95 % con 2k. k_stress es la sugerencia suavizada, recortada a [1, 2]; k_raw conserva el cociente estrés/base sin recortar. Si k_raw < 1, el estado es no_aplicable. bias_log y exp(-bias_log) son informativos.

## Tabla antes/después

Antes reproduce el snapshot del 2026-10-05 21:23 UTC en REPORTE_FASE19C3.md. Después fue calculado el 2026-10-05 leyendo asple_predictor.db con SQLite mode=ro. Se consideraron verificaciones maduras por horizonte y campeón, pesos elegibles de VAL y adaptación causal cuando estaba disponible. No se abrió DBManager ni se escribió en la base.

| h | Antes k_global | Antes k_stress | Antes estado | Antes n / n efectiva | Después k_global | Después k_stress | k_raw | bias_log | exp(-bias_log) | estado | n | n efectiva |
|---:|---:|---:|---|---:|---:|---:|---:|---:|---:|---|---:|---:|
| 1 | 1,396 | 1,082 | inconsistente | 28 / 28,00 | 0.777716 | 1.000000 | 0.711676 | 0.238805 | 0.787569 | no_aplicable | 28 | 28.00 |
| 2 | 1,121 | 1,612 | acumulando | 27 / 13,50 | 0.775885 | 1.008786 | 1.008786 | 0.250959 | 0.778054 | acumulando | 27 | 13.50 |
| 4 | 1,290 | 1,000 | acumulando | 26 / 6,50 | 0.759062 | 1.069921 | 1.069921 | 0.269705 | 0.763605 | acumulando | 26 | 6.50 |
| 24 | 1,808 | 1,580 | inconsistente | 18 / 0,75 | 0.729250 | 1.000000 | 0.655252 | 0.310341 | 0.733197 | no_aplicable | 18 | 0.75 |

## Bytes, mutaciones y prueba dirigida

- Los seis archivos solicitados pasaron el control de bytes: sin BOM, CR, U+FFFD ni 0x3F entre letras; tildes presentes en UTF-8. El ternario de frontend/app.js qued? separado por espacios para pasar literalmente el criterio.
- Mutación a (abs(error)/sigma_ref): falló tests/test_vol_widen_factor.py::test_widen_signed_rho_solver_clipping_and_smoothing_are_fixed; exit 1. SHA-256 antes/después: 26cf3d40af621fd00d5292d3565101a81a6ec0ddc91e1792e6e73d1b4ea89cac.
- Mutación b (permitir k_raw < 1): falló tests/test_vol_widen_factor.py::test_widen_apply_rejects_accumulating_and_bad_range; exit 1. SHA-256 antes/después: ab50759cbad8e60853516e2100092866317a024704090777959bdf904e3ec39c.
- Mutación c (invertir exp(-bias_log)): falló tests/test_vol_widen_factor.py::test_coverage_solver_direction_bias_and_normal_coverage; exit 1. SHA-256 antes/después: 26cf3d40af621fd00d5292d3565101a81a6ec0ddc91e1792e6e73d1b4ea89cac.
- Mutación d (quitar recorte [1,2]): falló tests/test_vol_widen_factor.py::test_widen_signed_rho_solver_clipping_and_smoothing_are_fixed; exit 1. SHA-256 antes/después: 26cf3d40af621fd00d5292d3565101a81a6ec0ddc91e1792e6e73d1b4ea89cac.
- Tras cada mutación se restauraron los bytes originales; hash antes/después idéntico en las cuatro.
- Prueba dirigida final, después de restaurar todas las mutaciones: exit 0; 28 passed, 2 warnings in 3.61s.
- Comando: `$env:ASPLE_OFFLINE=1; \.venv\Scripts\python.exe -m pytest tests/test_vol_widen_factor.py tests/test_vol_model_stats.py tests/ui/test_grids_browser.py::test_widen_suggestion_ui_only_applies_available_after_confirmation tests/test_frontend_encoding.py -q -p no:cacheprovider --basetemp "$env:TEMP\pytest-19c3b"`.
- Sin stage, commit, push, Binance/Testnet ni lectura de .env.

## No verificado

Colas gruesas, estabilidad entre regímenes y efecto en aperturas.
