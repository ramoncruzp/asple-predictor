# Reporte Fase 20C-b

## Estado D1–D7 y X1

| Hallazgo | Estado | Evidencia |
|---|---|---|
| D1 | HECHO | `frontend/battle.js:10-22`: cero señales muestra texto, neutrales, verificadas y tasa base; no disponible conserva el motivo. |
| D2 | HECHO | `frontend/battle.js:25-43`, `frontend/styles.css:7`: matriz con vacías en `<details>`, primera columna sticky, scrollbar oscura, tooltips y celda ganadora. |
| D3 | HECHO | `frontend/battle.js:21,25,99-106`: cabeceras y etiquetas recuperadas con nombres localizados y acentos. |
| D4 | PARCIAL | `frontend/battle.js:71-90,96-108`: filtros, ocultar neutrales, límite cliente 30 a 100, detalle aclarado y CSV escapado. No se pidió una segunda página al API; falta verificarla si el endpoint ofrece un límite paginado. |
| D5 | HECHO | `frontend/battle.js:110`: rótulo fijo con símbolo e intervalo. |
| D6 | NO VERIFICADO visualmente: requiere comprobar en navegador con Lightweight Charts real | `tests/ui/test_battle.py:122-137` solo prueba `rightOffset` con una librería simulada; la fixture bloquea el CDN. |
| D7 | HECHO | Cambio previo conservado; `tests/ui/test_models_page.py::test_unconfigured_tft_and_ensemble_have_configuration_notice` pasó. |
| X1 | HECHO | Corrección anterior conservada; `tests/test_frontend_encoding.py` pasó. |

## Estado R1–R11

| Requisito | Estado | Evidencia |
|---|---|---|
| R1 | HECHO | `frontend/battle.js:21,36`: `modelHeader` y `validationStatusTag` en tarjeta y cabeceras de matriz. |
| R2 | HECHO | `frontend/battle.js:29-38`: tooltip de condición y `winner` solo entre celdas con verificaciones y precisión válida. |
| R3 | HECHO | `frontend/battle.js:104`: chip con `signalClass`, clase `mono` para probabilidad y `muted` para detalle. |
| R4 | HECHO | `frontend/styles.css:20`: grilla de cuatro columnas, adaptada a dos y una; prueba Playwright confirma `display:grid` y más de una columna en escritorio. |
| R5 | HECHO | `frontend/battle.js:98-100,147-151`: etiquetas Modelo/Señal/Resultado, valores `model_name`, nombres visibles y reinicio a 30 al cambiar filtros. |
| R6 | HECHO | `frontend/battle.js:71-74`: filtra `interval === '1h'` cuando el campo viene presente. `/api/predictions/history` no filtra por intervalo; el cliente aplica el filtro, sin cambiar API. |
| R7 | HECHO | `frontend/battle.js:84-94,116`: comentario del BOM para Excel y nombre de archivo generado por `csvFilename`; prueba fija del patrón de nombre. |
| R8 | HECHO | `tests/ui/test_battle.py:27-43`: aserciones de No validado, tooltip, winner y chip. |
| R9 | HECHO | Cinco mutaciones, todas fallaron en su prueba y se restauraron byte a byte; hashes registrados abajo. |
| R10 | HECHO | D6 se etiqueta exactamente como no verificado visualmente; no se atribuye verificación del chart real. |
| R11 | HECHO | Corrida focalizada con pruebas no UI primero y `tests/ui/test_models_page.py`, `tests/ui/test_battle.py` al final. Resultado literal: `83 passed, 1 skipped, 20 warnings in 23.65s`. |

## Pruebas y mutaciones

Comando de pruebas:

```powershell
$env:ASPLE_OFFLINE='1'; .\venv\Scripts\python.exe -m pytest tests/test_frontend_encoding.py tests/test_models_status.py tests/test_models_page_context.py tests/test_training_jobs.py tests/test_grid_monitor.py tests/test_train_models_metadata.py tests/ui/test_models_page.py tests/ui/test_battle.py -q --basetemp "$env:TEMP\pytest-20c-b-final"
```

También pasó aislada la nueva prueba R1–R3: `1 passed, 2 warnings in 1.57s`. La verificación de sintaxis `node --check frontend/battle.js` terminó con código 0.

Hashes SHA-256 de `frontend/battle.js` antes / mutado / restaurado; cada restaurado coincide con el valor antes:

| Mutación | Antes | Mutado | Restaurado | Salida literal |
|---|---|---|---|---|
| (a) guion sin señales | `35b4e5ea341e7be66515b9a90c094ac40aa01b8d67562d8160001522e0f72bbe` | `21480a842fea056cf52fc59d7cb7c92d85d61b0093e08963d9e55fe430ffba3c` | `35b4e5ea341e7be66515b9a90c094ac40aa01b8d67562d8160001522e0f72bbe` | `1 failed, 2 warnings in 1.32s` |
| (b) NEUTRAL en ranking | `35b4e5ea341e7be66515b9a90c094ac40aa01b8d67562d8160001522e0f72bbe` | `a8e09715b2732544942c8027501ad2688c1c67f788b16e4bc4c4a4a99f8ea9bc` | `35b4e5ea341e7be66515b9a90c094ac40aa01b8d67562d8160001522e0f72bbe` | `1 failed, 2 warnings in 1.35s` |
| (c) ignorar ocultar neutrales | `35b4e5ea341e7be66515b9a90c094ac40aa01b8d67562d8160001522e0f72bbe` | `60901bda7fcc2f7e35355df46ef37b5b954c439dca15934764f4ef27517ca9a9` | `35b4e5ea341e7be66515b9a90c094ac40aa01b8d67562d8160001522e0f72bbe` | `1 failed, 2 warnings in 1.66s` |
| (d) CSV sin escape de comillas | `35b4e5ea341e7be66515b9a90c094ac40aa01b8d67562d8160001522e0f72bbe` | `b268e6f47f4f7d449504a37de0b01c75b078a0a494aa614ada964e246c98df84` | `35b4e5ea341e7be66515b9a90c094ac40aa01b8d67562d8160001522e0f72bbe` | `1 failed, 2 warnings in 1.96s` |
| (e) quitar tag de validación de tarjeta | `35b4e5ea341e7be66515b9a90c094ac40aa01b8d67562d8160001522e0f72bbe` | `93d4a7fd19be92fc73533797ff4df3098c9136e8d8753ea6c9994056c794495f` | `35b4e5ea341e7be66515b9a90c094ac40aa01b8d67562d8160001522e0f72bbe` | `1 failed, 2 warnings in 1.48s` |

La corrida focalizada no tuvo fallos. No se comprobó D6 con Lightweight Charts real. Se preservaron cambios ajenos preexistentes y artefactos excluidos; no se modificaron `scripts/`, `models/`, `api/` ni `database/`. No hubo stage, commit ni push.
