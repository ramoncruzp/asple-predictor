# Fase 20D-4 + 20D-2 — préstamos entre niveles y Scanner

## Alcance y resultado

Preflight: `HEAD 0d3ddc97b758556d316e88751768a017a313e703`, rama `main`, árbol limpio antes del trabajo. No leí `.env`, no abrí `models/saved/`, no usé Binance/Testnet ni hice stage, commit o push.

### 20D-4 — préstamos entre niveles

- Aperturas Smart: `config/settings.py:63` agrega `LOANS_CONTROL_EVERY_N` (predeterminado 3). `api/routes/grids.py:33-56` y `scripts/grid_ctl.py:39-61` asignan dos grids `loans` y el tercero `control`; los demás repiten la secuencia. `0` desactiva el grupo de control. `loans_enabled` explícito crea grupo `manual`; `simple` queda sin cohortes.
- El grupo `loans` fija `loan_lender_max_pct=70`; el control mantiene `loans_enabled=false` y no persiste límite de prestamista. Un límite explícito se conserva. Como `loans_group` es metadato que `validate_params` no admite, las rutas lo excluyen de la validación y lo guardan después de crear el grid (`api/routes/grids.py:278,405-414`; `scripts/grid_ctl.py:300-305,353-370`). Respuesta, evento, CLI y previsualización exponen grupo, bandera y límite.
- Apagar préstamos: `api/routes/grid_control.py:105-152` usa `_lock`/`_acquire_lock` (`grid/control_service.py:27-40,54-65`, reutiliza `grid_monitor.ops_lock`), exige Testnet confirmado, previsualización y confirmación explícita, rechaza préstamos `PENDING`, transfiere los `OPEN` con `GridEngine.transfer_loans` (`grid/engine.py:895-941`) y luego persiste `loans_enabled=false` y audita.
- Lectura/UI: `GET /api/grids/loans/summary` agrega grupos y métricas (`api/routes/grids.py:431-479`); `GET /api/grids/{grid_id}/loans` devuelve conteos, montos, duración media de devueltos y préstamos abiertos con nivel o reserva (`:482-508`). La UI muestra resumen, detalle y apagado con doble paso (`frontend/grids.js:209-226,297-316,409-435`). Los errores permanecen locales.
- Pruebas: `tests/test_grids_api.py:397-400,438-504`; `tests/test_grid_status_api.py:325-396`; `tests/ui/test_grids_browser.py:69-112`; `tests/ui/test_grids_control_ui.py:15-20`.
- Riesgos P1-5: el simulador rechaza `loans_enabled` y no simula préstamos (`grid/sim/runner.py:91-92`), así que las comparaciones Smart no estiman su efecto. 20E-1f advierte que `ewma_sigma_24h` conserva escala de 5 minutos con velas horarias (`REPORTE_FASE20E1f.md:146`). Con `resync_candles=3`, replay decide cada 3 h (`grid/sim/runner.py:80,153`); el monitor usa 900 s/15 min (`config/settings.py:40`): cadencia simulada 12 veces más lenta. No se puede atribuir causalidad al préstamo con ese P&L; no se cambió el simulador.

### 20D-2 — Scanner

- `config/settings.py:16,54` deja el peso `cost_headroom` en 0 y conserva el componente informativo. `config/settings.py:61-62` cambia umbral predeterminado de autoapertura de 0,7 a 0,538, redondeo de `(0,7 - 0,35) / 0,65` cuando `cost_headroom=1`. Autoapertura sigue apagada (`:58`).
- El ranking mantiene orden si todos los candidatos tienen `cost_headroom=1`: quitar una contribución constante produce una transformación afín monótona. Con holgura no saturada el orden puede cambiar; no se afirma equivalencia global.
- P2-2, filtros existentes conservados: registry activo, par USDT, estado `TRADING`, volumen 24 h, spread, estructura factible e historia de 30 días (`grid/scanner.py:64-76`). La estructura conserva margen y mínimo de celda (`grid/scanner.py:49-53`; `grid/structure.py:124-143,190-195`). `gross < 0,1` es aviso, no filtro (`grid/scanner.py:127-128`). No se añadió ningún filtro.
- Pruebas: `tests/test_grid_scanner.py:149-168`, `tests/test_scanner_spread_tick.py:35-45`, `tests/test_settings_phase17d.py:4-10`.

## P3 — pausa y sigma

La política existente entra en pausa a 0,10 y sale a 0,05 (`grid/policy.py:15-16,628-643`); escala sigma fraccional por horizonte (`:463-486`). En reportes/scripts 20E-1e/1f no encontré comparación certificada de sigma 4 h del campeón con esos umbrales ni evidencia para proponer otros. 20E-1f deja la sigma del campeón NO VERIFICADA (`REPORTE_FASE20E1f.md:146,154`). Se mantienen 0,10/0,05; no se cambió policy.

## Verificación

Todas las pruebas con `ASPLE_OFFLINE=1`; ninguna marcada `live` se ejecutó.

- Enfoque Python: `pytest tests/test_grids_api.py tests/test_grid_status_api.py tests/test_grid_scanner.py tests/test_scanner_spread_tick.py tests/test_settings_phase17d.py -m "not live" -q` → `71 passed in 4.59s`.
- Enfoque UI: `5 passed, 2 warnings in 1.62s`; navegador aislado `1 passed, 2 warnings in 1.34s`.
- Suite no-UI: `.\venv\Scripts\python.exe -m pytest -m "not live" --ignore=tests/ui -q --basetemp $env:TEMP\pytest-20d4-20d2-final-python4` → `944 passed, 1 skipped, 18 deselected, 38 warnings in 65.39s (0:01:05)`.
- Suite UI final: `.\venv\Scripts\python.exe -m pytest tests/ui -m "not live" -q --basetemp $env:TEMP\pytest-20d4-20d2-final-ui2` → `103 passed, 2 warnings in 75.21s (0:01:15)`; cero defectos de UI observados.

### Mutaciones (str.replace + assert count == 1; bytes restaurados)

| Parte | Mutación y fallo de comportamiento | SHA-256 antes / mutado / restaurado |
|---|---|---|
| 20D-4 | Forzar `loans_group=loans`; falló `test_smart_loan_cohort_defaults_and_manual_overrides` porque el tercero dejó de ser `control`. | `8c6fa9a4eaa1c19d22e9e53f189e90a99cb28f289e82360d18a192aef3dee6ca` / `a30ab4a3d5e9a49cbb82a2d0b84ffaf8a0e2e0413f72a98b51e998c42e4cfecb` / igual al original. |
| 20D-2 | Volver peso a 0,35; falló `test_scanner_score_defaults_are_pinned_and_low_edge_warning_travels` en la aserción del peso. | `e80fa92e433fd0a5e0f050040cd5dc99f560570ba0a235090468871def70f4ff` / `9eb908bf061a0e812641ede0c22427ffc28b04fa052ddd2d26043c77e90cab28` / igual al original. |

## Archivos para commits separados

**20D-4:** `api/routes/grids.py`, `api/routes/grid_control.py`, `scripts/grid_ctl.py`, `frontend/grids.js`, `tests/test_grids_api.py`, `tests/test_grid_status_api.py`, `tests/ui/test_grids_browser.py`, `tests/ui/test_grids_control_ui.py`.

**20D-2:** `config/settings.py`, `tests/test_grid_scanner.py`, `tests/test_scanner_spread_tick.py`, `tests/test_settings_phase17d.py`.

También se añadió este reporte: `REPORTE_FASE20D4_20D2.md`. No se modificaron `grid/engine.py`, `grid/policy.py`, `grid/sim/`, `grid/monitor.py`, `grid/scan_service.py`, `database/db_manager.py`, modelos ni datos.
