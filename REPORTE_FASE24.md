# Fase 24 — apertura con historial insuficiente

Sin stage, commit, push ni red. Preflight previo a los cambios: árbol limpio; HEAD `fdb22b9` (Fase 23c). No se accedió a `.env`, `data/`, `models/saved/` ni a la BD real; los tests usan SQLite temporal y fakes.

| Ítem | Estado | Evidencia |
|---|---|---|
| N1 — apertura excepcional | HECHO | `api/routes/grids.py:215,320-332` acepta `allow_unready_coin` estricto, falso por defecto, y solo pasa la compuerta con estado `datos_insuficientes`. `:438-441` añade aviso/días al plan; `:510-518` audita `unready_coin` y `history_days` y devuelve el aviso tras abrir. La ruta de pares conserva su compuerta en `:622-624`. |
| N2 - Advisor y formulario Scanner | HECHO | Advisor: `api/routes/grid_advisor.py:233-247,495-497`; aviso escapado `frontend/app.js:360`. Enmienda: `frontend/scanner.js:6-7,66-71,113-130,172,177,199-215` habilita solo `datos_insuficientes`, exige acuse para preview/confirmación, envía `allow_unready_coin` solo con acuse y muestra el aviso del API escapado. Pruebas Playwright: `tests/ui/test_grids_browser.py:875-977`. |
| N3 — σ realizada | HECHO en código/fixture; NO VERIFICADO en Testnet | `grid/monitor.py:441-446,501-520` pasa la σ del `VolView` al cálculo sin bloquear por `source`; acepta valor finito positivo, incluyendo `source="realized"`. Si falta o no es válido, registra `VOL_UNAVAILABLE` (`:501-507`) y la política recibe `None`; `grid/policy.py:723-724` deja `break_prob` sin calcular y `:451-453` devuelve `BLOCKED/vol_unavailable` para ADJUST. En ACTIVE siguen pudiendo operar reglas de celdas atrapadas/libres; para PAUSED, la falta de σ impide reanudar por `break_prob` (`:762-764`). `grid/monitor.py:604-607` llama al cálculo de ajuste con esa σ. Prueba nueva: `tests/test_grid_monitor.py:482-512`, proveedor falso con `source="realized"`, `stale=False`, σ finita: llega a política y no emite `VOL_UNAVAILABLE`. |

## Pruebas

Rojo inicial antes de la implementación: `7 failed, 1 passed, 70 deselected in 2.77s` en los casos nuevos de apertura/Advisor. La primera ejecución verde encontró que el fake de Testnet solo aceptaba XRP; se ajustó el fake del test para GRAM, sin tocar producción.

Resultados dirigidos verdes:

- `tests/test_grids_api.py tests/test_grid_advisor.py -k "data_insufficient_coin or flag_does_not_bypass or advisor_allows_data_insufficient" -q`: final `8 passed, 70 deselected in 2.25s`.
- Regresiones de apertura lista/no lista y rechazo del Advisor: `3 passed in 1.67s`.
- Pruebas de monitor `test_monitor_passes_fresh_realized_volatility_to_smart_policy` y `test_monitor_pause_shadow_requires_a_24h_champion_forecast`: final `2 passed in 0.83s`.
- Playwright `test_grid_advisor_explains_margin_risk_and_prefills_scanner`: final `1 passed, 2 warnings in 1.74s`.

## Mutaciones

| Comportamiento | SHA-256 antes | SHA-256 mutado | SHA-256 restaurado | Evidencia |
|---|---|---|---|---|
| N1: ignorar el estado y aceptar cualquier moneda no lista con el flag | `865608507e8795e734a30e6bdbc7275c9dd5207091ea71fb19f4b57dfe4d8910` | `2730d4e2241b524651139698610ac62b6f59eed10c2fd5140b5cdc70bb101bea` | `865608507e8795e734a30e6bdbc7275c9dd5207091ea71fb19f4b57dfe4d8910` | Detectada: `5 failed`; los estados pendiente/error/entrenando/descargando/consensuando devolvieron 200 en vez de 409. |
| N2 API: impedir análisis para `datos_insuficientes` | `ee658d2790814a462cbf6d3c338b9118da34a765cc853fc60951d01e1f660006` | `02dcf91b04a151ed33a7e6cc4e3270802a7552a4532d6e4df289a78eb912ffcc` | `ee658d2790814a462cbf6d3c338b9118da34a765cc853fc60951d01e1f660006` | Detectada: prueba falló con `KeyError: 'symbol'` porque el endpoint volvió a rechazar el análisis. |
| N2 presentación: suprimir el aviso del Advisor | `7af39f9fcec6e1c2b241caa140f3bbb82858517ebb9a83d70d505300631cec83` | `59e3d799c4573c7fefeecf281b4c918140990e056df03753f1388f8b74a9c071` | `7af39f9fcec6e1c2b241caa140f3bbb82858517ebb9a83d70d505300631cec83` | Detectada: la prueba Playwright falló al no encontrar visible `.unready-coin-warning`. |

En la ejecución inicial, antes de la enmienda que autorizó Scanner, no se pudo mutar ni probar su casilla. La enmienda cierra esa brecha; las tres mutaciones E1/E2 se ejecutaron y detectaron, con hashes abajo.

## Auditoría

Al cierre, los nueve archivos previstos son los enumerados en la secci\u00f3n de commit posterior. No se tocaron engine, policy, monitor, adjust, loans, proveedor, onboarding ni pares de grids. Suites completas no ejecutadas. Comportamiento real en Testnet y aceptaci\u00f3n de \u00f3rdenes de GRAMUSDT: NO VERIFICADOS.

## Evidencia final adicional

La compuerta de pares permanece en `api/routes/grids.py:622-624`. Corrida final dirigida: API `8 passed, 70 deselected in 2.25s`; monitor `2 passed in 0.83s`; Playwright `1 passed, 2 warnings in 1.74s`. Auditoría final: `git diff --check` limpio; archivos UTF-8 sin BOM, LF y U+FFFD=0; regex `[A-Za-z]\?[a-z]` en líneas añadidas=0; secretos=0.

## Enmienda de Scanner (E1-E3)

**E1 - HECHO.** `frontend/scanner.js:6-7` incluye `allow_unready_coin` en `OPEN_REQUEST_KEYS`; `:66-71` habilita y etiqueta ` (sin modelo)` solo al estado exacto `datos_insuficientes`. `entrenando` y otros estados no listos siguen deshabilitados. En `tests/ui/test_grids_browser.py:875-884,887-935` quedan las aserciones para GRAM habilitada, ADA entrenando bloqueada e historial de 99 días.

**E2 - HECHO.** El mapa de readiness por moneda y el acuse condicional están en `frontend/scanner.js:107,113-130`; el cambio de símbolo actualiza y reinicia la casilla en `:172`. Preview queda bloqueado sin acuse y la confirmación se vuelve a bloquear si se desmarca (`:114-120,175-178,211-218`). La petición a `/api/grids/open` agrega el flag solo con acuse (`:199`); el aviso del plan usa `esc` (`:209`). La prueba `tests/ui/test_grids_browser.py:887-977` verifica historial faltante como `—`, 99 días, cambio GRAM/XRP y reinicio, payload dry-run y confirmación, ausencia del campo para XRP y escape del aviso (no crea un nodo `img`).

**E3 - HECHO.** `tests/ui/test_grids_browser.py:887-977` cubre los seis comportamientos solicitados con respuestas simuladas: opción `datos_insuficientes` disponible, acuse con historial, preview bloqueado hasta marcar, ambos payloads con el flag, moneda lista sin el campo, cambio de GRAM a XRP con reinicio, aviso escapado y `entrenando` bloqueada.

**Rutas previas del formulario.** `frontend/scanner.js` llama al endpoint de estructura y al scan. `api/routes/grid_structure.py:161-176` valida formato y actividad en Coin Registry y obtiene datos de mercado, pero no consulta readiness; `api/routes/grids.py:283-294` delega `/scan` al servicio de escaneo, que no aplica compuerta de readiness. No fue necesario cambiar endpoints ni `grid/*`.

**Pruebas de la enmienda.** Rojo antes de la implementación: `2 failed, 45 deselected, 2 warnings in 14.41s` (selector GRAM bloqueado y casilla ausente). Verde final: `3 passed, 44 deselected, 2 warnings in 3.81s`, incluyendo las dos pruebas nuevas/ajustadas y `test_scanner_coin_selector_fails_open_without_readiness_information`. Regresiones previas repetidas: API `8 passed, 70 deselected in 2.23s`; monitor `2 passed in 0.85s`.

**Mutaciones E1/E2.** Cada una se hizo con `str.replace` y `assert count == 1`; las tres fallaron en la aserción conductual esperada y se restauraron byte a byte. SHA-256 de `frontend/scanner.js` antes y restaurado en los tres casos: `70e9d12daaf0e1e808e8865ba6270ac34e1d198e10d959dedc18f1efe9b5cf6e`.

| Mutación | SHA-256 mutado | Fallo observado |
|---|---|---|
| Quitar `allow_unready_coin` de la lista de campos | `e7b472038a0fa993a3aa5c8d4256e2882123cf31058085f800e5b87a014a998b` | `KeyError: 'allow_unready_coin'` en el cuerpo del dry-run. |
| Quitar el bloqueo de preview sin acuse | `ebe65a227ef421374bc28807e309c6e2c47a644b8e0312ca9a86e063657497cf` | La aserción `to_be_disabled` falló: el botón Vista previa estaba habilitado. |
| Cambiar el texto obligatorio del acuse | `ad3219a1c5dad33e0032d74383c11592db6e20666c5d32d58f877af83723f2db` | Fallo la aserción del texto `Entiendo que esta moneda no tiene modelo`. |

No se ejecutaron suites completas. No hubo stage, commit ni push; no se usaron Testnet, red, `.env`, BD real, `data/` ni `models/saved/`.


## Lista exacta prevista para el commit posterior

- `api/routes/grid_advisor.py`
- `api/routes/grids.py`
- `frontend/app.js`
- `frontend/scanner.js`
- `tests/test_grid_advisor.py`
- `tests/test_grid_monitor.py`
- `tests/test_grids_api.py`
- `tests/ui/test_grids_browser.py`
- `REPORTE_FASE24.md`
- Umbrales verificados: reducción dura 1.1×min_notional (`grid/adjust.py:88-89`) < piso funcional 1.3× (`config/settings.py:21`, aplicado en `grid/structure.py:39`).
- Aviso usado: "Con este capital por celda el grid no podrá prestar ni añadir niveles; solo podrá reducirlos. Está bajo el mínimo funcional."
