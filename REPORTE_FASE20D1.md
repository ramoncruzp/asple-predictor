# Fase 20D-1 — Auditoría Parte 2, bloques E, C y F

## Alcance y estado de entrada

HEAD del preflight: f721fcb. La fuente claude/Auditoria_Parte2_Inteligencia.md no existe en este checkout; trabajé con los requisitos específicos del prompt. En el preflight estaban modificados models/saved/metrics_xrp_1h.json y models/saved/vol/manifest_xrp.json, y aparecían .pytest_tmp/ y models/saved/metrics_xrp_1h.gru_2026-10-06.json.bak. Los JSON y el backup se conservaron sin leer ni escribir.

Incidente: la primera corrida amplia de pytest usó --basetemp .pytest_tmp/20d1-python, que creó/escribió ese subdirectorio. No se limpió ni se borró. Las corridas posteriores usaron %TEMP%. No se modificaron data/cache/, artefactos .joblib/.pt, .env, scripts de entrenamiento, policy ni lógica de grids.

## Bloque E — Estadística de modelos

| Punto | Estado | Evidencia |
|---|---|---|
| E1 / H9 | HECHO | models/volatility/model_stats.py:62 ya calcula var_ratio como cociente de varianza predicha/realizada. api/routes/volatility.py:278-279 lo expone junto a bias_log. frontend/models.js:137,139 lo presenta junto al sesgo y aclara el componente estructural. Pruebas API/UI. No se cambió k_active ni VOL_WIDEN_AUTO. |
| E2 / H10 | HECHO | models/volatility/model_stats.py:137,166 usa n/horizonte_h >= N_MIN. calculate_model_stats y forward_consensus_metrics entregan el horizonte. tests/test_vol_model_stats.py:50-65 prueba H2 con 59 filas y con 62 filas verificadas. |
| E3 / H11 | HECHO | api/routes/volatility.py:303 expone consensus_selection_note; frontend/models.js:139 presenta “TEST visto durante la selección”. No se modificó consensus_xrp.json ni scripts/vol_consensus_eval.py. La selección de consenso calibra condicionalmente en VAL y luego evalúa TEST (scripts/vol_consensus_eval.py:41-57,98-104); producción aplica la calibración en cada pronóstico (models/volatility/live.py:121). |

### Efecto de E2 con la base actual

Consulta a asple_predictor.db en modo de solo lectura, XRPUSDT. Para cada horizonte, los conteos son iguales para Persistence, EWMA, HAR, HAR_range, HAR_asym, GBM, NexoHAR y GARCH_t.

| H | Verificadas/modelo | n/H | Fuente antes; elegibles vivos | Fuente después |
|---:|---:|---:|---|---|
| 1 | 41 | 41,00 | vivo; HAR_range, GBM, NexoHAR | vivo; mismos elegibles |
| 2 | 40 | 20,00 | val; ninguno | val |
| 4 | 39 | 9,75 | vivo; HAR, HAR_range, HAR_asym, GBM, NexoHAR, GARCH_t | val; esos seis cambian de vivo a val |
| 24 | 32 | 1,33 | vivo; EWMA, HAR, HAR_range, HAR_asym, GBM, NexoHAR, GARCH_t | val; esos siete cambian de vivo a val |

En H1, los otros cinco no tenían peso vivo por elegibilidad/MSE, por lo que no se cuentan como transición. H24 requiere unas 720 observaciones horarias verificadas por modelo, aproximadamente 30 días, para alcanzar n/H=30. La consulta fue local y de solo lectura.

## Bloque C — Comunicación

| Punto | Estado | Evidencia |
|---|---|---|
| C1 / H3 | HECHO | frontend/app.js:263-265 muestra para el perfil seleccionado las cotas superiores de salida a 24/72 h y los toques de piso/techo “por lado”, desde range_risk. grid/range_risk.py:16-36. UI Advisor prueba visibilidad. |
| C2 / H4 | HECHO | api/routes/grid_advisor.py:14,274-278,355-357 calcula break_prob con DEFAULT_SMART_PARAMS y devuelve pause_enter_prob=0,10. frontend/app.js:262 muestra que el grid nacería pausable cuando se supera. Pruebas de fórmula API y aviso visible UI. grid/policy.py no se modificó. |
| C3 / H12 | HECHO | frontend/models.js:139 titula las medidas “Dentro de ±σ_ref” y “Dentro de ±2σ_ref” (error en log), y aclara que no es cobertura del precio. |
| C4 / H13 | HECHO | frontend/models.js:315 indica que BAJISTA acierta si el precio no sube más de 0,5 %. Prueba UI de texto y estilo calculado. No se redefine la métrica ni se cambian históricos. |

## Bloque F — Documentación

- H6: grid/policy.py:444-450 conserva el ancho y n_levels en ADJUST. No se cambió; requiere simulación previa.
- H8: ninguna señal de dirección alimenta el cálculo del grid. api/routes/grid_advisor.py:260 obtiene prediction para presentación y :363 la devuelve separadamente como prediction_signal.
- H14: api/main.py:74,152 conecta VerificationLoop para dirección; :81,154 conecta VolLoop para volatilidad.
- H15: models/ensemble.py:30 define EnsemblePredictor; models/volatility/harq.py:13 define HARQModel. Model D, EnsemblePredictor y HARQ no están cableados al flujo de producción; se conservan.
- H16: grid/policy.py:14 fija sigma_scale=1,15; api/routes/grid_advisor.py:203-215 aplica k_active al rango y a sigma. Son factores independientes y no se cambiaron.
- H17: NO VERIFICADO en servidor activo. Ramón debe ejecutar GET /api/volatility/model-stats?symbol=XRPUSDT&horizon=1,2,4,24; GET /api/volatility/widen-factor; GET /api/volatility/forecast; GET /api/models/shadow-status.

## Pruebas y mutaciones de 20D-1

- Python focal: 35 passed, 2 warnings in 2.06s.
- Model stats, Advisor y todos los tests grid no live: 624 passed, 16 deselected, 4 warnings in 39.38s (ASPLE_OFFLINE=1, -m 'not live').
- UI focal final de 20D-1: 20 passed, 2 warnings in 11.62s.
- Corrida UI amplia anterior: 55 passed, 1 failed, 2 warnings in 40.44s. El fallo Battle por HTTP 404 se diagnostica en Cierre 20D-1b.
- Una primera corrida no filtrada intentó tests live y quedó bloqueada con 16 WinError 10013 hacia Testnet; no se estableció conexión ni se completó llamada. La suite se repitió offline y con -m 'not live'.

| Mutación | Resultado conductual | SHA antes | SHA mutado | SHA restaurado |
|---|---|---|---|---|
| E2: usar len(window) >= N_MIN | Falló: H2 con 59 filas se volvió vivo y rompió la aserción val | 46cd914c4f76b7f51d37e8d58d9c7370acc8bfddedc76e359b3af87828a49b1b | 49975d5b044bc3053059c775ee547c412353652b0e842e098f99780312abc480 | 46cd914c4f76b7f51d37e8d58d9c7370acc8bfddedc76e359b3af87828a49b1b |
| E1: retirar var_ratio del endpoint | Falló con KeyError: var_ratio | d2d7309323529e3683a8f3ca399f4fdf780950118b017e8934d3bae413694957 | 3353e4de72273aea7f35b4ff3a0c0158b8ee866b13b88f94f04a4b331724fa62 | d2d7309323529e3683a8f3ca399f4fdf780950118b017e8934d3bae413694957 |
| C2: ocultar aviso de pausa | Falló: aviso no visible | 94bf98564cddc54ffb5853b331d708d60af8c31edfe27a93625acc3e679a307f | 74c82437dc59b619e34d88de32a84b404e4a29260258da26cbc4040e042a2955 | 94bf98564cddc54ffb5853b331d708d60af8c31edfe27a93625acc3e679a307f |

## Archivos para el commit posterior

api/routes/grid_advisor.py; api/routes/volatility.py; frontend/app.js; frontend/models.js; frontend/styles.css; models/volatility/model_stats.py; tests/test_grid_advisor.py; tests/test_model_stats_api_cycle.py; tests/test_vol_model_stats.py; tests/ui/test_grids_browser.py; tests/ui/test_models_page.py; REPORTE_FASE20D1.md.

## Cierre 20D-1b

Las tres ejecuciones del test Battle se hicieron en orden con -m 'not live':
(a) prueba sola: 1 failed, 2 warnings in 6.12s;
(b) archivo tests/ui/test_grids_browser.py: 1 failed, 36 passed, 2 warnings in 25.89s;
(c) tests/ui/test_models_page.py seguido de tests/ui/test_grids_browser.py: 1 failed, 55 passed, 2 warnings in 54.53s.
La aserción esperaba No validado y recibió: No se pudo cargar Battle de Modelos: HTTP 404. Puedes reintentar cuando el servicio responda.
URL de (a): http://127.0.0.1:62940/api/models/page-context?symbol=XRPUSDT&interval=1h (puerto efímero). frontend/battle.js:121 solicita la ruta. tests/ui/conftest.py:112-119 no monta models_status, que define page-context en api/routes/models_status.py:102-108. El fallo aparece solo, en el archivo y tras Models; no es un problema exclusivo de orden. Los diffs de api/routes/volatility.py, api/routes/grid_advisor.py, frontend/app.js y frontend/models.js frente a HEAD muestran que 20D-1 no cambió Battle/page-context. No se corrigió fuera de alcance.

Contadores revisados: model_stats.py:218 estado por verificaciones; api/routes/volatility.py:282-283 requiere fuente vivo y elegibilidad para peso_actual. model_stats.py:246 mide muestra de dispersión. model_stats.py:268 guarda bias_alert. Ninguno conmuta por sí mismo val→vivo; no se cambiaron.
C3 ya existía en frontend/models.js:139. tests/ui/test_models_page.py:25 prueba el texto y computed style de la nota (display distinto de none, visibility visible). La corrida focal final fue 20 passed, 2 warnings in 11.72s.
NO VERIFICADO: page-context en servidor activo. No hubo tests live ni Testnet.


### Nota de mantenimiento 20F
La afirmación de que el endpoint expone `consensus_selection_note` quedó obsoleta desde 20E-1; se conserva arriba como registro histórico.
