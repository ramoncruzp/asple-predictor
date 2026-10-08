# Fase 20E-2 — saneamiento final

| Punto | Estado | Evidencia y resultado |
|---|---|---|
| E1 | HECHO | `tests/ui/conftest.py:43-44`: Chromium/Playwright ahora tiene alcance por prueba. La corrida combinada UI → lifespan pasó: `2 passed, 2 warnings`; antes del cambio reprodujo el fallo al usar `asyncio.run` después de UI. |
| E2 | HECHO | `models/coin_onboarding.py:106,111,118,154`: hay 4 caracteres `;`; tokenizer Python encontró 0 operadores `;`. Son texto/comentarios, no sentencias; no se modificaron. |
| E3 | HECHO | `config/models_config.py:27-63`: caché por símbolo bajo lock; valida `mtime_ns` y tamaño. `tests/test_vol_per_symbol.py:96-121` afirma una lectura para dos consultas y recarga al cambiar el archivo. |
| E4 | HECHO | `scripts/train_vol_models.py:144-151`: el manifest conserva `champions` global solo para XRP; para ADA y otras monedas no escribe ese campo heredado. `tests/test_vol_per_symbol.py:178` lo comprueba. No se reescribieron manifests en disco. |
| E5 | HECHO, sin cambio | `api/routes/grid_control.py:101-102` deriva la ruta a `grid/control_service.py`; `grid/control_service.py:203-204` limita los parámetros admitidos y no incluye `loans_enabled`, y `:243-246` pasa esa misma lista permitida al merge. No puede activarse por esa ruta; el default de `loan_lender_max_pct` sigue en `grid/policy.py:39`. |
| E6 | HECHO | Búsqueda global: quedan dos referencias a `consensus_selection_note`: fixture sin uso funcional en `tests/ui/test_models_page.py:19` y afirmación histórica obsoleta en `REPORTE_FASE20D1.md:15`. No hay uso restante en frontend ni scripts. |
| E7 | HECHO | `frontend/models.js:110`: la serie aclara que se grafica σ/h y muestra la conversión `σ 4h = σ/h × √4`. UI: `tests/ui/test_models_page.py:262-274`. |
| E8 | HECHO | `frontend/models.js:153`; prueba UI explícita en `tests/ui/test_models_page.py:255`: quitada la nota visible `VOL_SOURCE: no expuesto por el endpoint`; la tarjeta conserva el estado de validación. |
| E9 | HECHO | `frontend/models.js:138`: espacio entre nombre del modelo y etiquetas Campeón/Consenso. Verificación de `inner_text` en `tests/ui/test_models_page.py:422-423`. |
| E10 | HECHO | `frontend/battle.js:105`: al alcanzar 100 filas aparece «Mostrando las últimas 100». `tests/ui/test_battle.py:139-150` llega al límite con 120 registros. |
| E11 | HECHO | `api/routes/grid_advisor.py:354-358`: ausencia normal de ventana horaria se registra en INFO sin traceback; otros fallos siguen en WARNING con traceback. Pruebas `tests/test_grid_advisor.py:392-400,421-431` cubren ambos casos con `caplog`. |

## Verificación

- Dirigidas: `pytest tests/test_vol_per_symbol.py tests/test_vol_training_pipeline.py tests/test_grid_advisor.py -q` → `46 passed, 3 warnings`.
- Caché y pruebas previas de consenso tras el ajuste de firma: `pytest tests/test_vol_consensus_per_symbol.py tests/test_vol_per_symbol.py::test_consensus_cache_reuses_json_until_mtime_changes -q` → `8 passed`.
- UI → lifespan: `pytest tests/ui/test_battle.py::test_battle_notes_when_history_reaches_the_100_row_limit tests/test_grid_monitor.py::test_app_lifespan_starts_without_monitor_when_testnet_credentials_missing -q -m "not live"` → `2 passed, 2 warnings`.
- Suite final no-UI: `$env:ASPLE_OFFLINE="1"; $bt=Join-Path $env:TEMP "pytest-20e2-no-ui-final"; .\venv\Scripts\python.exe -m pytest -m "not live" --ignore=tests/ui -q --basetemp $bt` → `955 passed, 1 skipped, 18 deselected, 38 warnings in 80.28s`.
- Suite final UI: `$env:ASPLE_OFFLINE="1"; $bt=Join-Path $env:TEMP "pytest-20e2-ui-final"; .\venv\Scripts\python.exe -m pytest tests/ui -m "not live" -q --basetemp $bt` → `106 passed, 2 warnings in 141.71s`.
- La primera suite no-UI finalizó con 1 fallo porque Windows conservó el mismo `mtime` en dos escrituras rápidas. Se reforzó la invalidación con el tamaño del archivo y se repitió; el resultado final es el indicado arriba.
- E12: sin cambios en valores por defecto, textos o pruebas de estrategia Smart.
- No se verificó visualmente un navegador externo con Lightweight Charts real; la etiqueta se comprobó con la librería simulada en Playwright. No se ejecutaron pruebas live ni llamadas a Binance/Testnet.
- Hallazgo ajeno observado y no corregido: el mensaje de símbolo inválido en `config/models_config.py` y su aserción tienen texto mojibake preexistente; queda fuera de estos ítems.

Archivos modificados: `api/routes/grid_advisor.py`, `config/models_config.py`, `frontend/battle.js`, `frontend/models.js`, `scripts/train_vol_models.py`, `tests/test_grid_advisor.py`, `tests/test_vol_per_symbol.py`, `tests/ui/conftest.py`, `tests/ui/test_battle.py`, `tests/ui/test_models_page.py`, y este reporte. Sin stage, commit ni push.
