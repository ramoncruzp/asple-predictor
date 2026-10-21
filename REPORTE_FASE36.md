# Fase 36 — dirección como semáforo de compras

## Alcance y decisión

Se añadió `model_a_down`, con objetivo de caída estricta en cuatro velas, evaluación walk-forward mensual y uso económico como filtro exclusivo de compras nuevas Smart. El interruptor `smart_direction_filter_enabled` queda **apagado**. Las ventas, el rango, los niveles y los préstamos no reciben esta señal.

## X0 — Verificación a tiempo

- HECHO. `database/learning_engine.py:20-43,70-88` interpola el precio en `verify_at`, consulta el precio actual solo cuando la demora no excede una hora y etiqueta `unverifiable_late` cuando no encuentra la vela histórica.
- `database/db_manager.py:418-432,454-483` guarda `verify_at` a cuatro velas y marca los retrasos sin vela como no verificables. Para `model_a_down`, el resultado se evalúa contra retorno estrictamente negativo en `database/db_manager.py:454-467`.
- Pruebas: `tests/test_direction_verification.py:59-74` verifica el resultado bajista con un cambio negativo; las pruebas existentes cubren interpolación y retraso no verificable.

## X1 — Objetivo y walk-forward

- `config/models_config.py:11` define `TARGET_DOWN_HORIZON_CANDLES = 4`; `models/model_a_xgboost.py:238-259` añade `ModelADown` y etiqueta `close[t+4]/close[t]-1 < 0`, usando las mismas features y configuración XGBoost de Model A. Los modelos A/B/C existentes no cambian.
- `scripts/train_models.py:35-71` entrena por mes solo con etiquetas cuyo final es anterior al inicio del mes evaluado; el test contiene solo el mes siguiente. La CLI offline es `--down-walk-forward --candles ... --symbol XRPUSDT|PEPEUSDT|ADAUSDT` (`scripts/train_models.py:162-181,198-207`).
- `scripts/train_models.py:77-134` calcula precisión de signo, precisión y cobertura para θ 0,55/0,60/0,65, e IC95 por bootstrap circular de bloques. La serie OOS de dos columnas se guarda en `data/derived/model_a_down_{xrp,pepe,ada}_oos.csv`; los modelos completos y su JSON de métricas se guardan en `models/saved/model_a_down_{coin}_1h.{joblib,json}`.
- El corte temporal excluye el mes actual: prueba pura en `tests/test_fase36_direction.py:26-35`. Mutación `<` → `<=`: **rojo**, la etiqueta que terminaba exactamente en el inicio del mes entró al entrenamiento. SHA antes/restaurado `5524124E74F1645CD11547E60BC6FB9A0227D30A292540B4556E18628A73F505`; mutado `6D578AB080981AF385D6368316829B096C7F304FCEEFA0D357CCF40F16292AEB`; restauración idéntica.

### Métricas walk-forward OOS

Las observaciones se muestrean una vez cada cuatro horas para formar bloques no solapados. Precisión y cobertura corresponden a la condición `p_down >= θ`; los intervalos se estiman por bootstrap circular de bloques.

| Moneda | n bloques | Acierto de signo | θ=0,55 precisión / cobertura | θ=0,60 precisión / cobertura | θ=0,65 precisión / cobertura |
|---|---:|---:|---:|---:|---:|
| XRP | 4242 | 50,50 % [49,06; 51,91] | 51,08 % [48,57; 53,52] / 37,25 % | 51,61 % [48,69; 54,67] / 24,89 % | 50,07 % [46,26; 53,94] / 16,01 % |
| PEPE | 4135 | 51,10 % [49,65; 52,60] | 49,15 % [46,58; 51,68] / 35,77 % | 49,35 % [46,25; 52,38] / 24,35 % | 51,50 % [48,01; 55,39] / 16,15 % |
| ADA | 4135 | 49,99 % [48,39; 51,51] | 51,30 % [48,63; 53,93] / 34,46 % | 52,09 % [48,76; 55,21] / 22,56 % | 53,81 % [49,74; 58,00] / 13,66 % |

## X2 — Regla y banco económico

- `grid/policy.py:12-22` añade la regla pura `direction_gate(p_down, threshold)`. `grid/sim/runner.py:89-113,198-212,635-640` recibe una señal alineada con las velas, consulta la predicción anterior y cada tres velas de 5 min cancela compras abiertas antes de procesar fills; mientras la señal bloquee, no rearma compras. Las ventas siguen su camino habitual.
- `scripts/bench_smart_oos.py:172-212,310-354` añade `--signal-csv` y las variantes `smart_actual_dir@0.55`, `@0.60`, `@0.65`, alineadas estrictamente con la señal anterior. Corridas XRP, PEPE y ADA; centrado; 100 y 1000 USDT; ventana 30 días, paso 7 días, historial 90 días. Se guardaron seis resultados bajo `%TEMP%\fase36-bank`; cada corrida tuvo 88 ventanas válidas, 0 omitidas y 3000 réplicas bootstrap.
- No-lookahead: `tests/test_fase36_direction.py:56-72`. Mutación de `direction_values[i-1]` a `direction_values[i]`: **rojo**. SHA antes/restaurado `735B9B1D4C867B02DD880D3A934E0A41DC4B791B8D563648F0C5F17D1E591521`; mutado `ADBECEFF07E95AA26FF49C110ADA521D42D0E3C7A8D50DDE5EE4F5FA40085746`; restauración idéntica.

| Capital | Moneda | Smart actual media / p5 / peor P&L | Dirección θ=0,60 media / p5 / peor P&L | Δ media [IC95], wins/88 | Veredicto |
|---:|---|---|---|---|---|
| 100 | XRP | 0,360 / -13,089 / -22,836 | 0,439 / -11,343 / -22,836 | +0,079 [-0,193; +0,466], 12 | Sin diferencia demostrada |
| 100 | PEPE | -1,228 / -21,014 / -31,104 | -1,222 / -21,523 / -25,665 | +0,006 [-0,424; +0,489], 9 | Sin diferencia demostrada |
| 100 | ADA | -1,276 / -18,360 / -27,818 | -1,098 / -18,293 / -27,818 | +0,178 [-0,174; +0,557], 15 | Sin diferencia demostrada |
| 1000 | XRP | 4,234 / -126,181 / -228,014 | 4,354 / -113,402 / -228,014 | +0,119 [-2,328; +2,984], 12 | Sin diferencia demostrada |
| 1000 | PEPE | -12,281 / -210,144 / -311,041 | -12,216 / -215,234 / -256,654 | +0,065 [-4,236; +4,889], 9 | Sin diferencia demostrada |
| 1000 | ADA | -11,626 / -183,700 / -278,254 | -10,118 / -182,995 / -278,254 | +1,508 [-2,348; +5,438], 16 | Sin diferencia demostrada |

## X3 — Sombra en vivo y resumen

- `scheduler/prediction_loop.py:63-105` evalúa cada artefacto disponible por moneda y persiste `model_a_down` con `p_down` y vencimiento a cuatro horas. El verificador existente procesa estas predicciones con la etiqueta bajista específica.
- `grid/monitor.py:84-126,510-515,596-602` registra `DIRECTION_SHADOW` como máximo una vez por grid y hora, también con el flag apagado. Guarda probabilidad, `would_block`, compras abiertas, precio medio y frescura. Con el flag encendido cancela solo compras; resuelve fills parciales y conserva las ventas.
- `api/routes/grid_status.py:32-90` expone `GET /api/direction/shadow/summary?symbol=` con precisión oportuna, umbral, bloques independientes y conteos descriptivos de fills/ciclos observados. `frontend/models.js:321-333` muestra tarjetas para XRP, PEPE y ADA; `frontend/global_flags.js:13-39,61-65` muestra esos veredictos y advierte en rojo en el diálogo si falta evidencia.
- Frescura obsoleta: `tests/test_fase36_direction.py:101-113`. Mutación del chequeo de edad a `fresh=True`: **rojo**. SHA antes/restaurado `7469D755EC1C9E58FF15738B4FE6BA508F1C1EA94E828C240D955AFDCE67B93B`; mutado `DB77E8D6C5841254F1D0ACAD44272452052EEF0DD87A3B7B4C4C67F71E22E4AC`; restauración idéntica.
- La clasificación de fills que la sombra habría bloqueado es **descriptiva**: se correlaciona por grid/nivel/tiempo y no prueba causalidad.

## X4 — Interruptor y veredictos

- `config/settings.py:52,149-157` registra `smart_direction_filter_enabled=False` y `DIRECTION_FILTER_THRESHOLD=0.60`; el flag aparece en el catálogo de Laboratorio. Datos faltantes o de más de dos horas no bloquean compras.
- `frontend/models.js:326-333` y `frontend/global_flags.js:13-28,61-65` marcan “Listo para probar” solo si precisión OOS ≥60 %, límite inferior IC95 >55 % y veredicto del banco “mejora”; en otro caso muestran “Sin evidencia”. Encender con evidencia insuficiente sigue permitido, con advertencia roja.
- Mutación del default a `True`: **rojo** en `test_direction_filter_flag_is_off_by_default_at_documented_threshold`. SHA antes/restaurado `563CF534DFB4D2F3F920DE150E3A3F7AA4DEB29F994778F709606FB2FC6043AA`; mutado `7912C8FDA9F38F01C06ED0B0CD87FBFDD52CF9047CD15D318DA5902A6BEFA14`; restauración idéntica.

## Resultado de evidencia

| Moneda | Precisión OOS θ=0,60 | Límite inferior IC95 | Δ banco θ=0,60, capital 100 | Veredicto |
|---|---:|---:|---:|---|
| XRP | 51,61 % | 48,69 % | +0,079 USDT, IC95 [-0,193; +0,466] | Sin evidencia |
| PEPE | 49,35 % | 46,25 % | +0,006 USDT, IC95 [-0,424; +0,489] | Sin evidencia |
| ADA | 52,09 % | 48,76 % | +0,178 USDT, IC95 [-0,174; +0,557] | Sin evidencia |

Ninguna moneda supera el umbral OOS y ningún intervalo del banco excluye cero. El flag queda apagado. **No verificado:** comportamiento de órdenes en Testnet/live; no se usó Testnet ni red en esta fase.

## Pruebas

- Línea base enfocada, antes de cambios: **44 passed, 5 deselected, 12 warnings**.
- Pase final enfocado: `tests/test_fase36_direction.py`, `tests/test_direction_verification.py`, `tests/test_train_models_metadata.py`, `tests/test_prediction_loop.py`, `tests/test_db_manager.py`, `tests/test_bench_smart_oos.py`, `tests/test_grid_sim_runner.py`, `tests/test_grid_monitor.py`, `tests/test_grid_monitor_live.py`, `tests/test_grid_monitor_policy.py`, `tests/test_app_settings_api.py`, `tests/test_models_status.py`, `tests/test_models_page_context.py`, `tests/test_grid_status_api.py`, `tests/ui/test_models_page.py`: **155 passed, 5 deselected, 15 warnings**. Ejecutado con `ASPLE_OFFLINE=1`; sin Testnet/live.

## X6 — Codificación del reporte anterior

- `REPORTE_FASE35.md`: se restauraron los acentos y signos dañados, en UTF-8 sin BOM y con LF. No se modificaron sus cifras ni conclusiones.
