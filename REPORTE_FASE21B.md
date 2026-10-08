# Fase 21b - cierre de pendientes

## Base y seguridad

HEAD inicial: `95fc6fa` (Fase 21 parcial). El preflight registró los cambios de D3/D4 y sus pruebas, además del reporte nuevo.

- Línea base no UI: `989 passed, 1 skipped, 18 deselected, 38 warnings in 153.96s (0:02:33)`.
- Línea base UI: `106 passed, 2 warnings in 149.13s (0:02:29)`.
- No se accedió a `.env`, `data/`, `models/saved/` ni a la base real; no hubo Testnet ni red. `grid/engine.py` y `grid/loans.py` siguen intactos.

## Estado por item

| Item | Estado | Evidencia | Pruebas y mutación |
|---|---|---|---|
| D3 | HECHO | `config/settings.py:21`; `grid/structure.py:34-49`; Smart en `api/routes/grids.py:319-365`, `api/routes/grid_structure.py:186-272`, `api/routes/grid_advisor.py:305,477`, `scripts/grid_ctl.py:301-320`, `grid/auto_open.py:103-150`; avisos UI en `frontend/app.js:280,290` y `frontend/scanner.js:53-58`. | Dirigidas: `68 passed`. Multiplicador 1.0 hizo fallar la aserción de 6.5 (el valor mutado era 5.5). SHA `config/settings.py`: antes/restaurado `0180a3b0dd6cd22fac06d1209b142357ebeabc65e00fd5d45af44d3357e9dc4c`; mutado `537488b7ecaffe79777cfd37b7e71aaead9a7b3793fbed47034bdadc20bfa3fd`. Simple conserva el piso anterior; una apertura manual bajo el piso funcional queda permitida y se avisa. |
| D4 | HECHO | `grid/adjust.py:132-181`; default y validación `grid/policy.py:26,178-180`; monitor `grid/monitor.py:584-608`; simulador `grid/sim/runner.py:315-320`; `tests/test_grid_adjust_shrink.py`. | Dirigidas: `72 passed, 1 warning`. La primera prueba dio ImportError por helper ausente. Mutación que desactivó el reintento: falló la prueba de 14 celdas libres. SHA `grid/adjust.py`: antes/restaurado `ec64deaf17d438fed97430e0cc316d6353f31c51440c1f6535364fa96d06dbb9`; mutado `67dca82e4b336569e0c9132aec25411b7327028f6525bb65b2e48eefec032061`. La prueba previa de rechazo se actualizó para exigir `CAPITAL_SHRINK` donde el caso antes se bloqueaba por capital. |
| A2 | HECHO | `tests/test_grid_monitor.py:736-774`; vigilancia usa el `horizon_h` guardado o 24 h de respaldo en `grid/monitor.py:402-408`; apertura valida 1, 2, 4 o 24 en `api/routes/grids.py:290-293`. | Prueba dirigida: `2 passed`. Reproduce la expresión retirada de Fase 21 y compara ADJUST/PAUSE. Al mutar el flujo para suprimir ADJUST: `1 failed, 1 passed`; falló `len(events) == 1`. SHA `grid/monitor.py`: antes/restaurado `9173ed2fb6a6190c00200022cf719ddd134445df0f6e011775abc0c474df9a27`; mutado `07fb05d1c08f5fb3ca2ded8a9bd2d27b4ef0ef4a4374781c8778a0fa67b23d0d`. No se cambió la validación de horizonte. |
| A3 | HECHO | Aviso visible en `frontend/battle.js:110`; prueba `tests/ui/test_battle.py:12-18`. Informa que A/B/C reponderan clases, que sus puntajes no son probabilidades calibradas y que el umbral 0,60 correspondió a cerca de 27 % real según el dato de auditoría aportado por la fase. | UI dirigida: `1 passed`. Mutación de ?no son probabilidades calibradas? a ?son probabilidades calibradas?: `1 failed`, la prueba detectó el cambio. SHA `frontend/battle.js`: antes/restaurado `8bd1fead5f817235297db519a1e106eb8eed450df42911a1f3fc8fca30d89ccf`; mutado `d0c754d7ec72ccdd912b2b2b4781871f15bc8df859a2af0b381a84ab431b335b`. |
| B4 | HECHO | Adaptador `grid/volatility_provider.py:153-177`; sigma de apertura `api/routes/grids.py:113-133,378`; Advisor `api/routes/grid_advisor.py:245-275,516`; UI `frontend/app.js:363-372` y `frontend/scanner.js:60-65,187`. | API dirigida: `68 passed, 2 warnings`; UI dirigida: `3 passed, 2 warnings`; adaptador: `1 passed`. Las tres superficies indican fuente y ventana: realizada 30 d, campeón 24 h y campeón de vigilancia 4 h. Valor ausente = `null` y motivo. Mutación quitó el campo de respuesta; falló con `KeyError: 'realized_30d'`. SHA `api/routes/grid_advisor.py`: antes/restaurado `81fc5c50fb49cae855c3f418260532dfbceda222fed075530783469e10f28e81`; mutado `13062bc668fc03e23811cf8e1f0177f0e0d1035a546bddfb032bc5f7e8cf3de5`. |
| B6 | PARCIAL | `api/routes/volatility.py:36-56,284-344`; Advisor llama solo H24 en `api/routes/grid_advisor.py:170-172`; prueba `tests/test_grid_advisor.py:622-629`. La consulta completa de estadísticas sigue preservando filas de dispersión para no cambiar adaptive/forward. | Prueba H24: `1 passed`; mutación `horizon=24` a `None`: `1 failed`. Comparación sintética con igual conjunto de 6,004 y 3,004 filas: cálculo completo `0.209464 s`, acotado `0.091960 s`; `n_verified` y forward n bajaron de 1,500 a 750, y `adaptive`/`forward` cambiaron. Se conserva el cálculo completo. Equivalencia en BD temporal del repo y tiempos antes/después de endpoint: NO VERIFICADOS. |

## Corrida final y saneamiento

La primera suite no UI detectó tres fallos en `tests/test_grid_structure_preview_api.py`: una aserción de claves exactas no incluía el campo aditivo `functional_cell_warning`, y la llamada manual a `_custom_variant` omitía `min_spacing`, desplazaba argumentos y respondía 422. Se corrigieron la llamada y la expectativa; el archivo afectado pasó `5 passed`.

Resultados finales literales, con `ASPLE_OFFLINE=1` y `-m "not live"`:

- No UI: `1005 passed, 1 skipped, 18 deselected, 38 warnings in 179.76s (0:02:59)`.
- UI: `106 passed, 2 warnings in 167.92s (0:02:47)`. Playwright registró bloqueos externos de `cdn.jsdelivr.net` y `fonts.googleapis.com`; no observó defectos UI.

Comandos usados:

```powershell
$env:ASPLE_OFFLINE='1'; .\venv\Scripts\python.exe -m pytest -m "not live" --ignore=tests/ui -q --basetemp "$env:TEMP\pytest-21b-final"
$env:ASPLE_OFFLINE='1'; .\venv\Scripts\python.exe -m pytest tests/ui -m "not live" -q --basetemp "$env:TEMP\pytest-21b-final-ui"
```

No se hizo stage, commit ni push. Testnet real y `min_notional` real por símbolo: NO VERIFICADOS. No hubo escrituras en la BD real, `data/` ni `models/saved/`.

## Archivos para commits separados

- D3: settings, estructura, API de grids/Advisor/estructura, CLI, auto-open, frontend y pruebas de estructura/apertura.
- D4: ajuste, policy, monitor, simulador y pruebas de shrink/replay.
- A/B: monitor y prueba A2; Battle y prueba A3; endpoints, adaptador, UI y pruebas B4; prueba Advisor B6.

## Decisiones pendientes de Ramón

- B6: decidir si se mantiene la consulta completa de estadísticas para preservar exactamente adaptive/forward o se autoriza otra fase que demuestre una optimización equivalente.
- Quedan sin certificación Testnet real y los filtros `min_notional` por símbolo.
