# Fase 23: reducción de niveles ociosos y vigilancia

## Estado por elemento

| Elemento | Estado | Evidencia | Rojo / verde / mutación |
|---|---|---|---|
| Q1 | HECHO | `grid/policy.py:12-35, 158-225, 495-590`; `config/settings.py:46`; `grid/monitor.py:59-69, 607-665`; `grid/sim/runner.py:80-83, 217-239`; pruebas `tests/test_grid_policy.py:132-192`, `tests/test_grid_monitor.py:291-348`, `tests/test_grid_sim_smart_fidelity.py:50-70` | Rojo inicial: import de `idle_shrink_decision` falló porque no existía. Verde final dirigido: `24 passed`. Mutación del interruptor global: assertion falló (`global_disabled` esperado; mutado produjo `not_due`). SHA `grid/policy.py`: antes/restaurado `e451cef71dd4f270a4e785364087c896b7d8797d22a376db875d43fbe93b4619`; mutado `901d49fb1d6f09612b589e4f2e798175df55982ea52411fbdaa5465d935433f1`. |
| Q2 | HECHO | `api/routes/grids.py:933-1025`; `tests/test_grid_level_adjust_summary.py:11-81` | Prueba del resumen verifica conteos por fuente, motivos, trayectoria temporal de P&L descriptiva y switch; ruta GET con TestClient devuelve 200. Mutación de conteo: prueba falló (`IDLE_SHRINK` esperado 1, mutado 0). SHA `api/routes/grids.py`: antes/restaurado `00446ec938a4ec654a5f02872c993794613f0ca07c5ddf3c3a189c87a16f5be3`; mutado `b190a8fb9f6d290a4bb96f5c98c9f06741bda6e46b976532f9c4aeb99e9383fa`. |
| Q3 | HECHO | `api/routes/grids.py:227-245, 514-681, 798-932`; `scripts/grid_ctl.py:167-176, 230-248`; `tests/test_grid_loan_pairs.py:114-201`, `tests/test_grid_ctl.py:61-66` | Los factores loans/idle_shrink/capital_shrink se separan; los pares viejos se interpretan como loans y su respuesta conserva las claves previas. Mutación que ignora `pair_factor`: prueba falló al incluir un par loans como brazo incompleto en idle_shrink. SHA `api/routes/grids.py`: antes/restaurado `00446ec938a4ec654a5f02872c993794613f0ca07c5ddf3c3a189c87a16f5be3`; mutado `e3896d26ebf526b841b98806b4c7bf366a12153b11b269843c176d83f342da36`. |
| Q4 | HECHO | `frontend/grids.js:206-274, 452-465`; `tests/ui/test_grids_browser.py:136-166` | Render de tres veredictos, switch, leyenda, motivos y pares excluidos: `1 passed`. Mutación que quita la leyenda: assertion Playwright `to_contain_text` falló. SHA `frontend/grids.js`: antes/restaurado `cf88dcdc465cbe6c41f556f9905711d0918348ec14ca3d16634077b5bc733632`; mutado `a5e2d906a36bfb1832f72aed2a405ffce879d74efe7c0178aef0f67f86f878be`. |

## Decisiones y límites

- Nivel 3 queda apagado en el default del grid y en el interruptor global (`grid/policy.py:27-33`; `config/settings.py:46`). El monitor requiere ambos habilitados; el simulador requiere además la opción explícita `idle_shrink_enabled`, cuyo default es `False` (`grid/sim/runner.py:80-83`).
- Q1 registra ciclos por nivel en eventos `IDLE_SHRINK_EVAL`; la primera evaluación establece línea base y una brecha mayor a dos ventanas restablece la instantánea sin reducir (`grid/policy.py:511-542`). El evento incluye `capital_per_cell`, P&L realizado y equity calculado con P&L realizado y no realizado (`grid/monitor.py:622-638`).
- Q2 estima P&L a 24/72 h antes/después con snapshots existentes y etiqueta cada diferencia como descriptiva, no causal (`api/routes/grids.py:975-1010`). Veredictos requieren al menos 20 pares y el signo del IC95 % (`api/routes/grids.py:935-944`); nunca cambian flags.
- Q3 añade `pair_factor`; si falta, el resumen usa `loans`. `idle_shrink` se rechaza con 422 cuando el interruptor está apagado. CLI `open-pair --factor` disponible (`api/routes/grids.py:594-596`; `scripts/grid_ctl.py:174`).
- Pruebas dirigidas finales antes de las suites: `53 passed, 4 warnings`.
- NO VERIFICADO: que `cycles_completed` y su instantánea persistan correctamente tras reinicio real; tiempo de ejecución de la ruta de resumen con muchos eventos; comportamiento de ejecución en Testnet. No se hicieron llamadas de red ni operaciones Testnet.

## Suites finales

No-UI: `1063 passed, 1 skipped, 18 deselected, 41 warnings in 72.85s`; UI: `110 passed, 2 warnings in 111.60s`. Línea base pre-cambio: no-UI `1045 passed, 1 skipped, 18 deselected`; UI `109 passed`.

## Archivos para commits separados

- Q1: `config/settings.py`, `grid/policy.py`, `grid/monitor.py`, `grid/sim/runner.py`, `tests/test_grid_policy.py`, `tests/test_grid_monitor.py`, `tests/test_grid_sim_smart_fidelity.py`.
- Q2: `api/routes/grids.py`, `tests/test_grid_level_adjust_summary.py`.
- Q3: `api/routes/grids.py`, `scripts/grid_ctl.py`, `tests/test_grid_loan_pairs.py`, `tests/test_grid_ctl.py`.
- Q4: `frontend/grids.js`, `tests/ui/test_grids_browser.py`.
- Este reporte: `REPORTE_FASE23.md`.

## Decisiones pendientes de Ramón

1. Elegir `width` o `spacing` para los primeros grids que se habiliten; el default por grid es `width`.
2. Decidir si se enciende el interruptor global y cuándo.
3. Elegir moneda y capital para los primeros pares; esta fase no abre pares reales.

## Correcciones de cierre R1-R2

| Punto | Estado | Evidencia y resultado |
|---|---|---|
| R1 | HECHO, el flujo ya preservaba la fuente correcta | `grid/monitor.py:639-653, 669-680, 742-746`; `tests/test_grid_monitor.py:291-319, 322-369`. Sin reducción adicional, `GRID_ADJUSTED` conserva `source=IDLE_SHRINK` y sus `n_from/n_to`; si el preflight reduce más por capital, conserva `source=CAPITAL_SHRINK` y el `n_to` final. Las pruebas de monitor simulan exchange/motor y verifican ambos caminos. Mutación de fuente forzada: SHA antes/restaurado `0965da13b683d369ffee9ef757a08e0771f6cb180d0b0624b8d2d9bd3c0a6252`; mutado `c0f8453b8a1937446a82371a91afa09e3b136d0a62b4a7d39525e78141468ddf`; la prueba falló al recibir `CAPITAL_SHRINK` donde esperaba `IDLE_SHRINK`. |
| R2 | HECHO | `grid/policy.py:496-519`; `grid/monitor.py:614-638`; `grid/sim/runner.py:221-239`; pruebas `tests/test_grid_policy.py:165-190`, `tests/test_grid_monitor.py:404-433`. Si `last_adjust_at > previous_eval_at`, devuelve `NONE`, `adjusted_since_baseline` y `should_record=True`; monitor y simulador comparten la función pura. La prueba integrada confirma que la evaluación registrada antes de `GRID_ADJUSTED` produce ese motivo en el ciclo siguiente. Mutación que ignora la comparación: SHA antes/restaurado `71acaf48a3bd2f4fb019ef50dead603f651cd05f4aae95815236734faca5792b`; mutado `40107f920322bbe1e8bd901180dd35d655786edb09ba44cf28fc7657330a733f`; la prueba falló con `cooldown` en vez de `adjusted_since_baseline`. |
| Cooldown | HECHO | `tests/test_grid_policy.py:193-216`. La primera suite no-UI expuso que la expectativa anterior no era alcanzable con los defaults tras R2; la prueba se dividió para probar la rama con `idle_every_h=2`, `adjust_cooldown_h=6`, evaluación previa hace 3 h y ajuste hace 5 h. Mutación de `<` por `>=`: SHA antes/restaurado `71acaf48a3bd2f4fb019ef50dead603f651cd05f4aae95815236734faca5792b`; mutado `ad9d378554fd6b3b701b888dabeb597a1bdf06e0092ae373436bd3e71ad02b95`; la prueba falló porque no devolvió `cooldown`. |

Con los defaults (`idle_every_h=24 >= adjust_cooldown_h=6`) el motivo cooldown no se alcanza por R2; la rama se conserva por defensa y se prueba con `idle_every_h < adjust_cooldown_h`.

### Verificación final de cierre

- No-UI: `$env:ASPLE_OFFLINE='1'; .\venv\Scripts\python.exe -m pytest -m "not live" --ignore=tests/ui -q --basetemp "$env:TEMP\pytest-f23-close-final-no-ui"` ? `1063 passed, 1 skipped, 18 deselected, 41 warnings in 72.85s`.
- UI: `$env:ASPLE_OFFLINE='1'; .\venv\Scripts\python.exe -m pytest tests/ui -m "not live" -q --basetemp "$env:TEMP\pytest-f23-close-final-ui"` ? `110 passed, 2 warnings in 111.60s`.
- Mutaciones R1, R2 y cooldown detectadas; archivos restaurados a su SHA anterior. `git diff --check` sin errores.
