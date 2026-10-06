# Reporte de cierre — Fase 20A

## Estado

## Seguimiento 20A-3 (2026-10-05)

## Seguimiento 20A-5 - Reparaciones de backend (2026-10-06)

## Seguimiento 20A-6 - Ciclo en model-stats (2026-10-06)

| Punto | Estado | Evidencia |
|---|---|---|
| S1 | HECHO | `models/volatility/model_stats.py:189` hace copia superficial de `snapshots[-1]` antes de adjuntar la lista. `forward_consensus_metrics` conserva el acceso a `states.get("snapshots")` en la línea 282 para su cálculo interno. |
| S2 | HECHO | La búsqueda en `frontend/*.js` no encontró referencias a `adaptive.snapshots`. `api/routes/volatility.py:298` elimina snapshots del objeto API después de completar el cálculo y sus métricas forward. |
| S3 | HECHO | `tests/test_model_stats_api_cycle.py:29` crea SQLite temporal con 36 verificaciones para cada modelo; TestClient obtiene HTTP 200, JSON serializable con `allow_nan=False`, y el recorrido detecta ciclos y valores no finitos. |

Mutación S1: restaurar `latest = snapshots[-1]` hizo fallar S3 por `cycle detected`; SHA original/restaurado `610f80e083c68e9119fb89fcbc42681a71834e05b952a0f233a37870b6326aca`, SHA mutado `56a4dceb74a37051e8eabe749c838358babfc55772774d66c80c2da19dc5650d`.

Prueba literal final (incluye B3, S3, pruebas de Models/encoding y todos los `test_vol_*.py` y `test_model_stats*.py`): **52 passed, 5 warnings in 7.65s**. `asple_predictor.db` no fue abierto ni modificado.

Resultado: B1-B3 hechos en base SQLite temporal; `asple_predictor.db` real no fue abierto ni modificado.

| Punto | Estado | Evidencia |
|---|---|---|
| B1 | HECHO | `database/db_manager.py:478`: join desde las tablas SQLAlchemy; conteos distinct por `predictions.id` y `outcomes.id`. SQLite con 33 predicciones y 29 outcomes devolvió total 33, verified 29, pending 4; page-context TestClient devolvió los mismos números. |
| B2 | HECHO | `database/db_manager.py:232`: migración introspecta todas las columnas declaradas en `vol_widen_suggestions`; tipa Float/Integer/String/Text/DateTime y agrega solo las ausentes, idempotentemente. La prueba antigua detectó y agregó exactamente `k_stress_q68`, `k_base_q68`, `stress_count`, `base_count`; filas previas y demás tablas quedaron iguales. |
| B3 | HECHO | `tests/test_db_manager_schema_migrations.py:20`: esquema SQLite viejo con dos filas, doble apertura, consultas de widening y TestClient. `page-context`, `model-stats` y `widen-factor` respondieron 200. |

Prueba literal: `10 passed, 4 warnings in 5.33s` al ejecutar `tests/test_models_page_context.py tests/ui/test_models_page.py tests/test_frontend_encoding.py tests/test_db_manager_schema_migrations.py -q` con `venv/Scripts/python.exe` y basetemp local.

Mutaciones B3 (cada una falló la prueba de esquema/conteos, restaurada byte a byte):

| Caso | SHA antes/restaurado | SHA mutado |
|---|---|---|
| a - volver a `p.outerjoin` | `a747954bb75a8f5154dfa75e5e1692168cbcd56cfcc4f624c0b08c78e64478c3` | `be8a7e17a17be3f508410d7c0772f7d7274c163c741b246343b0ba140b10686e` |
| b - omitir `k_stress_q68` | `a747954bb75a8f5154dfa75e5e1692168cbcd56cfcc4f624c0b08c78e64478c3` | `d76b84d1ef7f5a4baf6c485bfb8b713fc6b90dda0de7ff11ac81ccfdc7a1206b` |

No se amplió `_migrate_grid_columns` ni otra migración. No se hizo stage, commit ni push.

Estado: **PARCIAL; no declarar cerrado**. D0 no alcanzó las rutas: el Python del sistema falló al importar `api.main` con `ModuleNotFoundError: No module named 'ta'` (cadena: `api.main` -> `models.model_a_xgboost` -> `data.feature_engineer`). En `venv`, TestClient no pudo importarse porque falta `httpx`; no se instalaron dependencias. No hay status/body comprobados ni una excepción aislada de model-stats.

| Punto | Estado | Evidencia / límite |
|---|---|---|
| D0 | NO HECHO | TestClient no alcanzó rutas; faltan `ta` y `httpx`. |
| M1 | PARCIAL | `frontend/models.js:137` usa forecast_at/pred_vol_pct/realized_vol_pct; Playwright no arrancó. |
| M2 | PARCIAL | `frontend/models.js:179` separa bloques con Promise.allSettled; prueba UI bloqueada. |
| M3 | PARCIAL | `api/routes/models_status.py:64`, `database/db_manager.py:464`; conteos reales sin comparar. Corregí el test directo pasando Query defaults en `tests/test_models_page_context.py:27`. |
| M4 | PARCIAL | `frontend/models.js:178` muestra aviso limitado a XRPUSDT y no consulta volatilidad para otro símbolo; dirección y conteo activo sin verificar. |
| M5 | PARCIAL | `frontend/models.js:96` agrega MSE vivo, coberturas, sobre/sub, sesgo, peso, estado y bias_alert; rango bajo el mínimo usa em dash/Pocos datos. No probado. |
| M6 | NO HECHO | Altura de Consenso y eje X en viewport estrecho no implementados/verificados. |

Pruebas requeridas: `python -m pytest tests/test_models_page_context.py tests/ui/test_models_page.py tests/test_frontend_encoding.py -q` con `ASPLE_OFFLINE=1` dio **1 failed, 1 passed, 1 warning, 7 errors in 4.69s**. El fallo inicial fue la invocación directa de page_context con `Query(XRPUSDT)`; se corrigió pasando symbol/interval, pero no se repitió. Los siete errores UI fueron `PermissionError: [WinError 5]` bajo `C:\Users\ramon\AppData\Local\Temp\pytest-of-ramon`. `venv_experimental` carece de Playwright; `venv_old` carece de pytest. Mutaciones SHA-256 de 20A-3: **NO HECHAS**.

Se completó el traslado de la comparación de volatilidad y las adiciones solicitadas. No se hizo stage, commit ni push. No se leyó .env; no se llamó a Binance/Testnet.

## Antes y después de V1–V6

| Versión | Antes | Después |
|---|---|---|
| V1 — Traslado | Battle de Volatilidad estaba dentro de #screen-battle; loadVolBattle se ejecutaba al abrir Battle y cambiar #vol-horizon-select. | Se quitó la tarjeta de Battle; conserva Dirección y enlaza a Modelos ([frontend/index.html:23](frontend/index.html:23)). El render de tabla, cobertura/historial y lógica central del gráfico Lightweight Charts están en [frontend/models.js:11](frontend/models.js:11), [frontend/models.js:46](frontend/models.js:46), [frontend/models.js:76](frontend/models.js:76) y [frontend/models.js:168](frontend/models.js:168). loadBattle solo carga Battle de Dirección ([frontend/app.js:320](frontend/app.js:320)). |
| V2 — Histórico/en vivo | No había selector, ranking ni Δ de puesto. | Selector Histórico (TEST)/En vivo. Tablas y columnas separadas; encabezados ordenan asc/desc con ▲/▼. Incluye Puesto; Δ puesto vs histórico solo con al menos 30 verificadas. Un único aviso de muestra por horizonte en vista viva ([models.js:11](frontend/models.js:11), [models.js:51](frontend/models.js:51)). |
| V3 — Cobertura y gráfico | Cobertura solicitada como 4h con cualquier horizonte; gráfico de campeón sin ventana ni CSV. | Cobertura se muestra a 4h; para 1/2/24h se declara “Cobertura disponible solo a 4h”, pues el endpoint recibe interval y no horizon. Selector Campeón/Consenso/Modelo y ventanas 7d/30d/Todo; CSV escapa comas y duplica comillas. Todo solicita el máximo del endpoint (1000). Nota fija “TEST de XRP no es virgen” ([models.js:46](frontend/models.js:46), [models.js:72](frontend/models.js:72), [models.js:76](frontend/models.js:76)). |
| V4 — QLIKE | HAR y HAR_asym del manifiesto 4h no se imprimían ni se comparaban. | HAR 0.3134569593664547 → 0.313457; HAR_asym 0.31353397434507996 → 0.313534. No son iguales ([models.js:62](frontend/models.js:62); models/saved/vol/manifest_xrp.json). |
| V5 — Codificación | frontend/index.html no estaba explícito en la ruta del test. | Se añadió a la lista explícita y siguen escaneándose otros HTML/JS ([tests/test_frontend_encoding.py:13](tests/test_frontend_encoding.py:13)). |
| V6 — Pruebas | Cuatro guardas de render estaban agrupadas. | Renderizaciones por comportamiento y Playwright ampliado; seis mutaciones conductuales. La prueba que dependía de #vol-battle-table se adaptó en tests/ui/test_frontend_format.py::test_shadow_cards_validation_labels_and_volatility_detail_are_explicit; no se eliminó. tests/ui/test_grids_browser.py::test_battle_shows_validation_and_prediction_time_volatility_range sigue cubriendo Battle de Dirección por #battle-content ([tests/ui/test_models_page.py:12](tests/ui/test_models_page.py:12), [tests/ui/test_models_page.py:83](tests/ui/test_models_page.py:83), [tests/ui/test_frontend_format.py:119](tests/ui/test_frontend_format.py:119)). |

## Pruebas ejecutadas

- node --check frontend/app.js — correcto.
- node --check frontend/models.js — correcto.
- ASPLE_OFFLINE=1 .\venv\Scripts\python.exe -m pytest tests/test_frontend_encoding.py tests/test_models_page_context.py tests/ui/test_frontend_format.py tests/ui/test_models_page.py -q --basetemp "$env:TEMP\pytest-20a" — **19 passed, 2 warnings, 5.82 s**. No se ejecutó suite completa.
- git diff --check — sin errores de whitespace. Git mostró avisos generales de conversión LF/CRLF de otros archivos del checkout.
- Los archivos frontend modificados pasaron UTF-8 sin BOM, sin CR, sin U+FFFD ni patrón [A-Za-z]\?[a-z]. La prueba incluye frontend/index.html.

### Mutaciones

Base SHA-256 antes de mutar: 96c4c40deaab7021066899f11838f762e66bb1fb865769dd04141f2831f97bf8. Cada mutación falló por aserción de comportamiento; tras cada caso se restauraron bytes y SHA base.

| Caso | Prueba | SHA-256 mutado | SHA-256 restaurado |
|---|---|---|---|
| a — Activo con n<30 | test_models_page_renders_accumulating_below_minimum | 57d7fadce68cbf9208ee1eba6f3311b9f05cc5b370c9c544b8fe1c4319192efd | 96c4c40deaab7021066899f11838f762e66bb1fb865769dd04141f2831f97bf8 |
| b — mezclar moneda/horizonte | test_models_page_renders_symbol_and_horizon_mismatch_without_mixing | ea49cd3f61843126d4f84bcc5e2daf30e347961d0d92e58799346675a7e039c8 | 96c4c40deaab7021066899f11838f762e66bb1fb865769dd04141f2831f97bf8 |
| c — ocultar aviso sin modelos | test_models_page_renders_no_model_notice | 886032238bf2c69c206022324a211377d82b2a7a1875e254cc3357e97d8be18b | 96c4c40deaab7021066899f11838f762e66bb1fb865769dd04141f2831f97bf8 |
| d — quitar sin evidencia/muestra efectiva baja | test_models_page_renders_no_signal_evidence_and_low_effective_sample | ada5518df694fa1a137c620b3b35ad2426ed7efc2d66d98983d44a5caf651a98 | 96c4c40deaab7021066899f11838f762e66bb1fb865769dd04141f2831f97bf8 |
| e — mezclar columnas históricas/vivas | test_models_page_separates_historical_and_live_columns_and_shows_ranks | 0dd06a87e2b7c655c1b23bdabc55bc1b8a6692911aa52ab8123d35ddd5392eb5 | 96c4c40deaab7021066899f11838f762e66bb1fb865769dd04141f2831f97bf8 |
| f — ignorar horizonte de cobertura | test_models_page_coverage_horizon_qlike_and_csv_escape | a4c42d48789aad35af997e83de5a762dc6630f3047d65ea4f8cfbb7db54d7cac | 96c4c40deaab7021066899f11838f762e66bb1fb865769dd04141f2831f97bf8 |

## Endpoints y límites

GET /api/models/page-context (agregador local de solo lectura ya añadido), /api/models/status, /api/models/shadow-status, /api/volatility/model-stats, /api/volatility/forecast, /api/volatility/widen-factor, /api/volatility/battle, /api/volatility/history y /api/predictions/volatility-coverage. Aplicar conserva POST /api/volatility/widen-factor/apply con confirmación.

- El historial no ofrece serie de consenso. El selector la muestra y declara que no está expuesta; no la sustituye por campeón.
- CDN externa cdn.jsdelivr.net fue bloqueada en Playwright. Se verificaron controles, filtrado, consulta de modelo, errores y descarga CSV, pero no se verificó visualmente el gráfico Lightweight Charts cargado por CDN.
- Todo está limitado a 1000 filas del endpoint, no es ilimitado.
- **NO VERIFICADO:** datos reales de producción para otras monedas y meses de resultados. No se hicieron consultas de mercado en vivo.
- Observaciones_UI_Modelos.md no estaba en el checkout.
- Cobertura disponible solo a 4h por el alcance del endpoint existente; no se creó ni cambió endpoint.

Los cambios están locales y sin staging. No hubo commit ni push.
