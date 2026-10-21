# Reporte Fase 32

Fecha: 21-oct-2026. Alcance: tres interruptores globales de Smart y paridad de stop-loss en simulación. No se ejecutó Testnet.

## Resultado por ítem

| Ítem | Estado | Implementación y evidencia |
|---|---|---|
| S1 — ADJUST automático | HECHO | `config/settings.py:48,127`, `grid/monitor.py:667-681`, `grid/sim/runner.py:84,214`. El monitor lee el ajuste global cada ciclo, bloquea ADJUST e IDLE_SHRINK y registra `ADJUST_SUSPENDED` una vez por episodio apagado. El runner omite ambas decisiones cuando `adjust_enabled_global=False`. Rojo: la mutación que ignoró el flag hizo fallar `test_monitor_global_adjust_off_skips_decision_and_emits_once`. Verde: apagado, evento único y reencendido verificados en monitor; bloqueo verificado en runner. |
| S2 — esperar fuera de rango | HECHO | `config/settings.py:49,132`, `grid/policy.py:51,134,461,743-775`, `grid/monitor.py:468-470`, `grid/sim/runner.py:97`, `api/routes/grid_advisor.py:449-454`, `grid/scan_service.py:157-159`. El modo global se inyecta en copias por ciclo; no se guarda en parámetros del grid. `wait` pausa al salir, conserva ventas, no expira por `pause_max_h`, exige volver estrictamente al rango para RESUME y respeta `max_loss_pct`. Rojo: permitir expiración por `pause_max_h` hizo fallar `test_close_wait_policy_pauses_and_resumes_only_inside_range`. Verde: política pura, monitor y replay pausa/reanudación; el modo `close` conserva la regresión. |
| S3 — espaciado por volatilidad | HECHO | `config/settings.py:50,144`, `grid/structure.py:142-154,194-196`, `grid/scan_service.py:95-128`, `api/routes/grid_advisor.py:337-365,520`, `frontend/app.js:378-380,393`. Advisor y Scanner usan sigma de 24 h, `K=0.9`, descenso por margen/celda y fallback a margen. La respuesta incluye `levels_rule`, `vol_spacing_k`, `n_vol` y `simulation_switches`; UI presenta la regla y los cuatro switches. Rojo: cambiar sigma24 por sigma4h produjo espaciado 1,20 % frente al mínimo esperado 2,70 % y falló `test_vol_spacing_structure_uses_sigma24_formula_only_when_enabled`. Verde: fórmula y ruta API/Scanner/UI verificadas; apagado mantiene snapshot de niveles y separación. |
| S4 — stop-loss en simulación | HECHO | `grid/sim/runner.py:84-85,174`, `api/routes/grid_advisor.py:452-454`, `grid/scan_service.py:157-159`. Advisor y Scanner pasan `settings.stoploss_enabled`; el runner omite candidatos de stop-loss si es falso. Rojo: ignorar el kwarg volvió a emitir `STOP_LOSS` y falló `test_stop_loss_sells_at_monitor_close_and_records_fee`. Verde: con el switch apagado no se emite ese evento; encendido conserva el caso actual. |

## Mutaciones

Cada cambio mutado se restauró byte por byte antes de continuar. El SHA de restauración coincidió con el SHA previo a esa mutación.

| Ítem | Archivo | SHA antes | SHA mutado | SHA restaurado | Resultado rojo |
|---|---|---|---|---|---|
| S1 | `grid/monitor.py` | `85FC409438681EC8F66FF638F6341EFE14C692653CD9B1565BD0C733011F19D6` | `2FFC40D423E48B80A0188E70DD734B3DF467CC10A68862DA747ED4E33F8B8F5D` | `85FC409438681EC8F66FF638F6341EFE14C692653CD9B1565BD0C733011F19D6` | prueba falló al intentar ADJUST con switch apagado |
| S2 | `grid/policy.py` | `543455F9919B1A554DA0C89C4F54D45674B67C297FEB3EA89D7015745620C42B` | `A01023FDF5425B86D69DDCA6B9B59745FB4030B98ACC482F63C6E23469EB032F` | `543455F9919B1A554DA0C89C4F54D45674B67C297FEB3EA89D7015745620C42B` | prueba recibió `CLOSE_REPOSITORY` por `pause_max_h` |
| S3 | `grid/structure.py` | `8D435163C1EC2BF7F62A2668BF6483C22534447DDAF0C814B32F3B8A4B8FDCF3` | `FCE1DDA4F458E6166665C3902BCE60BCD29672856D304041FCAB1773E7102D6F` | `8D435163C1EC2BF7F62A2668BF6483C22534447DDAF0C814B32F3B8A4B8FDCF3` | prueba de sigma24 falló con sigma4h |
| S4 | `grid/sim/runner.py` | `22195C8A3DD82C678314916E6D5B6D6F92323468889090CE6EA6298E597DD9B3` | `03745E8697D800876080F56152D3FD3B6633298A1E1C9B4B629E6B8C21242780` | `22195C8A3DD82C678314916E6D5B6D6F92323468889090CE6EA6298E597DD9B3` | prueba volvió a encontrar `STOP_LOSS` con switch apagado |

## Verificación final

- Preflight: árbol limpio; Fases 30 y 31 ya estaban commiteadas (`ad78d96`, `cd3bdba`, `17f78b5`).
- Baseline con el `venv` del repo: `1354 passed, 1 failed, 1 skipped, 18 deselected`. La falla era el harness de `test_naive_job_timestamps_are_utc_and_elapsed_is_local_timezone_safe`: cargaba `frontend/models.js` sin `window.ASPLEFormat`. Se añadió al harness un parser equivalente a `Date.parse` para la fecha ISO con zona horaria que usa el caso; la prueba aislada pasó.
- Suite final, comando `ASPLE_OFFLINE=1 python -m pytest tests -m "not live" -q --basetemp <directorio-nuevo-bajo-TEMP>` usando `venv`: **1365 passed, 1 skipped, 18 deselected, 51 warnings en 427.86 s**. Node: `node --check frontend/app.js` pasó.
- API/UI: cinco tarjetas globales visibles en Laboratorio y Cuenta; prueba de Laboratorio recorrió vista previa `dry_run`, confirmación y actualización.
- Después de la suite completa se reforzó y repitió la comprobación de cinco tarjetas en Cuenta: `tests/ui/test_laboratorio.py` + `tests/test_app_settings_api.py`, **10 passed**.
- Defaults: ningún valor existente de `DEFAULT_SMART_PARAMS` cambió. Se añadió solo `out_of_range_mode="close"`.
- Auditoría: `git diff --check` pasó; archivos cambiados en UTF-8 sin BOM y LF. `grid/engine.py`, `grid/adjust.py`, `grid/loans.py`, `data/` y `models/saved/` intactos. No se abrió `.env`, no se usó red ni se escribió en la BD real. En esa ejecución no se hizo stage, commit o push.

## No verificado

- Efecto operativo de S1–S4 en Testnet real.
- Interacción de S2 con préstamos o compound; no se alteró ni probó esa lógica.
- Las cifras de la investigación XRP son evidencia simulada fuera de muestra, no una afirmación de superioridad en vivo.

## Archivos para commits separados

- **S1:** `config/settings.py`, `grid/monitor.py`, `grid/sim/runner.py`, pruebas de S1.
- **S2:** `config/settings.py`, `grid/policy.py`, `grid/monitor.py`, `grid/sim/runner.py`, `api/routes/grid_advisor.py`, `grid/scan_service.py`, pruebas de S2.
- **S3:** `config/settings.py`, `grid/structure.py`, `grid/scan_service.py`, `api/routes/grid_advisor.py`, `frontend/app.js`, pruebas de S3.
- **S4:** `grid/sim/runner.py`, `api/routes/grid_advisor.py`, `grid/scan_service.py`, pruebas de S4.
- `config/settings.py`, `grid/sim/runner.py`, `api/routes/grid_advisor.py`, `grid/scan_service.py` y `tests/test_fase32_switches.py` contienen cambios compartidos; separar esos commits por hunks. `REPORTE_FASE32.md` puede ir con el commit documental.

## Decisiones pendientes de Ramón

Recomendación: encender S3 primero. Luego probar S1 apagado y S2 encendido juntos, midiendo grids pareados. El orden y los switches que se activarán quedan a decisión de Ramón.

## Fase 32b — corrección de S2

Fecha: 21-oct-2026. Alcance limitado al vencimiento de pausa en modo `wait`.

- **Corrección:** `grid/policy.py:747-749` omite `pause_max_h` siempre que `out_of_range_mode="wait"`, con independencia de las razones almacenadas al pausar. `max_loss_pct` sigue cerrando; en modo `close`, `pause_max_h` mantiene su comportamiento.
- **Descripción:** `config/settings.py:134` indica que el grid en espera no se cierra por tiempo de pausa y que solo la pérdida máxima del grid (10 %) lo cierra.
- **Prueba pura:** `tests/test_fase32_switches.py:57-71` verifica pausa inicial por `break_prob`, espera de 49 h con precio de vuelta al rango y ausencia de cierre en `wait`; el mismo caso en `close` produce `CLOSE_REPOSITORY [pause_max_h]`.
- **Prueba runner:** `tests/test_fase32_switches.py:152-165` reproduce la secuencia sintética dentro del runner con 5 niveles, ADJUST global apagado y stop-loss apagado. El runner comparte `evaluate_grid`; emite PAUSE por `break_prob` y no emite `CLOSE_REPOSITORY` en modo `wait`.
- **Mutación roja:** se restauró temporalmente la condición previa, que solo eximía `pause_max_h` cuando `pause_reasons` incluía `out_of_range_wait`. Falló la prueba pura al recibir `CLOSE_REPOSITORY [pause_max_h]`. SHA mutado: `9C16156CE0B2B1F079F7FB509D8B473743CC883DBD5FAC6E47D06CE986E5E7E7`. Tras restaurar, SHA de `grid/policy.py`: `48D5DAA842FFFF4B19F60EE7B78E8EAE168B5454BD7CFBCF6946244DC213E13D`, idéntico al SHA previo a la mutación.
- **Verde dirigido:** `tests/test_fase32_switches.py`, **10 passed**. Suite offline única: `ASPLE_OFFLINE=1 python -m pytest tests -m "not live" -q --basetemp <directorio-nuevo-bajo-TEMP>` — **1367 passed, 1 skipped, 18 deselected, 51 warnings en 435.94 s**.
- **Límites:** sin red, Testnet, `.env` ni BD real. El runner usa el mismo `evaluate_grid`; no requirió cambios adicionales.

## Agrupación para commits

- Las declaraciones de configuración de los tres interruptores, sus metadatos contiguos y `ADVISOR_VOL_SPACING_K` comparten hunks; quedaron en el commit S1, el primero que necesita esos flags. La descripción de espera de 32b quedó dentro de ese hunk común.
- `tests/test_fase32_switches.py` nació como un único hunk y cubre S1, S2, S3, S4 y 32b; quedó en S2 por ser el bloque centrado en esperar fuera de rango. La prueba de regresión 32b está en ese módulo.
- Los hunks de paso de interruptores desde Advisor y Scanner al runner agrupan S1, S2 y S4; se asignaron a S3 junto con el flujo de simulación y el espaciado. El cambio específico de stop-loss del runner sí quedó aislado en S4.
- `tests/test_training_jobs.py` se incluye en el commit del reporte: su harness Node aislado ahora define `ASPLEFormat.parseApiTime`, dependencia que `frontend/models.js` consulta al cargar.
