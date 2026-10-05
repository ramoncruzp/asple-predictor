# Fase 18B-3 — cierre de correcciones de UI

## Alcance y resultado

Se completaron L1–L10 con cambios en frontend, API de apertura, configuración y pruebas. No se tocaron pesos ni umbral del scanner. No se leyó `.env` ni se llamó a Binance/Testnet. Sin commit, stage ni push.

La suite terminó en **788 passed, 18 deselected, 0 skipped** (base indicada: 771 passed, 18 deselected). Se ejecutó con `ASPLE_OFFLINE=1` y `-m "not live"`; hubo 9 avisos preexistentes de colección/deprecación/modelos. La primera ejecución completa detectó dos fallos: precisión del formateador para indicadores pequeños y carrera en la carga del selector ADA; ambos se corrigieron. Las dos pruebas enfocadas pasaron y la suite completa se repitió en verde.

`node --check frontend/app.js` y `node --check frontend/scanner.js` finalizaron con código 0. `git diff` de `grid/engine.py`, `grid/monitor.py` y `grid/policy.py` está vacío.

## Cambios, pruebas y límites por punto

| Punto | Cambio y evidencia archivo:línea | Prueba principal | No verificado |
|---|---|---|---|
| L1 | El detector permanente de texto corrupto recorre los recursos del frontend; textos visibles de Dashboard/Battle corregidos. [tests/test_frontend_encoding.py:1], [frontend/app.js:234], [frontend/index.html:22] | `test_frontend_encoding`; render de rango de volatilidad en `tests/ui/test_frontend_format.py:119` | Revisión visual manual en todos los navegadores. |
| L2 | El cargador distingue `load() == False` por falta de PyTorch y deja pasar excepciones de versión/features. [models/shadow_loader.py:39] | `tests/test_shadow_model_loading.py:128`, `:134` | Carga de artefactos reales en otro entorno de PyTorch. |
| L3 | Precisión y `minMove` según precio de la serie; ticks temporales compactos; `formatIndicator` conserva cifras significativas sin ceros añadidos. [frontend/app.js:64], [frontend/app.js:216], [frontend/app.js:220] | `tests/ui/test_frontend_format.py:50`, `:151` | Gráfico visual real con la biblioteca externa: Playwright bloquea sus CDN; el fixture valida opciones/ticks del adaptador. |
| L4 | Volatilidad del símbolo seleccionado; modelo XRP solo en XRP; estado de volatilidad realizada identificada. Panel sombra marcado `XRPUSDT 1h` y atenuado para otras monedas. [frontend/app.js:282], [frontend/app.js:287], [frontend/app.js:288], [frontend/styles.css:15] | `tests/ui/test_frontend_format.py:198`, `:213`; Playwright `tests/ui/test_grids_browser.py:104` | Disponibilidad de velas/modelos para cada activo en producción. |
| L5 | El borrador del Advisor conserva `spacing_pct` y dispara la recalculación vinculada en Scanner. [frontend/app.js:264], [frontend/scanner.js:73] | `tests/test_frontend_scanner.py:224`; cobertura Playwright de prellenado en `tests/ui/test_grids_browser.py:69` | Interacción manual entre todas las variantes del formulario. |
| L6 | Vista previa muestra progreso y tiempo transcurrido, aplica deadline de 6 s y rehabilita el botón al terminar/error. [frontend/scanner.js:54], [frontend/scanner.js:72] | `tests/test_frontend_scanner.py:212`, `:219` | Latencia y timeouts de un backend desplegado. |
| L7 | Guarda usa `edge_gross_pct` (separación menos dos comisiones), mínimo configurable 0,7 con alias obsoleto; polvo solo avisa y neto estimado no positivo bloquea. Igual regla en apertura manual y auto-open. [config/settings.py:39], [grid/structure.py:112], [api/routes/grids.py:28], [api/routes/grids.py:36], [api/routes/grids.py:43], [grid/auto_open.py:82] | `tests/test_grids_api.py:338`; `tests/test_grid_auto_open.py:91`; alias en `tests/test_settings.py`; casos de preview en `tests/test_grids_api.py:187` | El polvo efectivo medido en Testnet y el filtro mínimo real de Binance. |
| L8 | Función pura con retorno lognormal, deriva cero, sigma constante y escala `sqrt(T)`; Advisor muestra probabilidades de toque y cota superior de salida, con fuente y supuestos. [grid/range_risk.py:1], [grid/range_risk.py:16], [api/routes/grid_advisor.py:101], [api/routes/grid_advisor.py:115], [frontend/app.js:262] | `tests/test_grid_range_risk.py:1`; resultado del Advisor en `tests/test_grid_advisor.py:45` | Validez predictiva/calibración de los supuestos, sobre todo en 72/168 h. |
| L9 | SVG limita etiquetas y usa formato compacto para precios pequeños; simulación fallida muestra símbolo y causa; volatilidad realizada ocupa una sola línea actualizable; aviso de precio a menos de 2% del piso/techo. [frontend/app.js:249], [frontend/app.js:258], [frontend/app.js:316], [frontend/app.js:320], [api/routes/grid_advisor.py:113] | `tests/ui/test_frontend_format.py:181`, `:198`; Playwright `tests/ui/test_grids_browser.py:104` | Datos de simulación históricos disponibles para todos los símbolos y medición visual en pantallas físicas. |
| L10 | Warning cita el mínimo de celda realmente aplicado; rechazo de preview fuera de rango se traduce en mensaje accionable; botón deshabilitado tiene gris y cursor no permitido. [grid/structure.py:33], [frontend/scanner.js:72], [frontend/styles.css:10] | `tests/test_grid_structure_preview_api.py:78`; `tests/test_frontend_scanner.py:230`; estilo en `tests/ui/test_frontend_format.py:219` | Confirmación visual manual en temas/navegadores no cubiertos por Playwright. |

## Mutaciones temporales

Todas fueron restauradas byte a byte; la verificación final de los archivos mutados coincidió con el original. **26/26 mutaciones murieron**:

| Punto | Mutación | Prueba que la detecta |
|---|---|---|
| L1 | Sustituir la vocal acentuada de «máximo» por `?`; retirar acento de «Dirección» | `test_frontend_encoding` y render de Advisor |
| L2 | Mensaje genérico al fallar `load`; tragarse error de versión | `test_shadow_model_loading.py:128`, `:134` |
| L3 | Quitar `priceFormat`; volver a `toFixed(5)` | `tests/ui/test_frontend_format.py:50`, `:151` |
| L4 | Solicitar siempre XRP; retirar atenuación del panel sombra | `tests/ui/test_frontend_format.py:198`, `:213` |
| L5 | Omitir espaciado; omitir recálculo vinculado | `tests/test_frontend_scanner.py:224` |
| L6 | Quitar progreso; dejar botón bloqueado tras error | `tests/test_frontend_scanner.py:212`, `:219` |
| L7 | Guardar por neto tras polvo; permitir neto no positivo | `tests/test_grids_api.py:338`, `tests/test_grid_auto_open.py:91` |
| L8 | Usar sigma lineal en tiempo; omitir factor 2 | `tests/test_grid_range_risk.py` |
| L9 gráfico | Quitar notación compacta; mostrar etiqueta en cada nivel | `tests/ui/test_frontend_format.py:181` |
| L9 simulación | Quitar símbolo/causa; quitar causa del error | `tests/ui/test_frontend_format.py:181` |
| L9 volatilidad | Duplicar línea; no actualizar rango al recalcular | `tests/ui/test_grids_browser.py:104` |
| L9 cercanía | Suprimir warning; bajar umbral de 2% a 0,5% | `tests/test_grid_advisor.py:45` |
| L10 | Volver al mínimo fijo genérico; quitar mensaje de precio fuera de rango | `tests/test_grid_structure_preview_api.py:78`, `tests/test_frontend_scanner.py:230` |

## Archivos de esta fase

`api/routes/grid_advisor.py`, `api/routes/grids.py`, `config/settings.py`, `frontend/app.js`, `frontend/index.html`, `frontend/scanner.js`, `frontend/styles.css`, `grid/auto_open.py`, `grid/range_risk.py`, `grid/structure.py`, `models/shadow_loader.py`, `tests/test_frontend_encoding.py`, `tests/test_grid_advisor.py`, `tests/test_grid_auto_open.py`, `tests/test_grid_range_risk.py`, `tests/test_grid_structure_preview_api.py`, `tests/test_grids_api.py`, `tests/test_frontend_scanner.py`, `tests/test_settings.py`, `tests/test_shadow_model_loading.py`, `tests/ui/test_frontend_format.py`, `tests/ui/test_grids_browser.py`.
