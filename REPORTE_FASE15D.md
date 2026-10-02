# Fase 15D - registro y barrido del polvo residual por grid

## Implementación

- `database/db_manager.py:83,188-216,1050-1120`: agrega `grids.dust_qty`, default 0 y migración SQLite idempotente. Se persiste como texto decimal para conservar exactitud con SQLite. `grid_dust_ledger` evita sumar dos veces el mismo fill o residuo de celda. `record_grid_buy_fill_dust` persiste, en una transacción, la cantidad retenida y el polvo de ese BUY.
- `grid/engine.py:942-961`: el fill calcula `(gross_qty - base_fee) - held_qty` y lo registra con llave basada en el order id. Los cierres que marcan una celda DUST trasladan su `held_qty` al acumulador: `grid/engine.py:2188-2214`. Se conserva el `held_qty` del estado DUST existente; `sweep_grid_dust` lo excluye del monto vendible hasta que sea liquidable sin doble contar.
- `grid/policy.py:180-267,282-299`: `evaluate_target` acepta `dust=None` (por defecto conserva su comportamiento previo) y agrega proceeds estimados del polvo que pase filtros; `plan_dust_sweep` es puro y respeta step, minQty, minNotional y applyMinToMarket.
- `grid/engine.py:2117-2182,2488-2510,2634-2666`: barrido de solo la cantidad registrada; CID determinista `gS{grid}{seq}`; write-ahead en `grids.params`; recuperación con `find_order_by_client_id`; liquidación idempotente de cantidad e ingreso; eventos `DUST_SWEPT` y `DUST_SWEEP_DEFERRED`. Cierre de grid y cierre por objetivo invocan el barrido.
- `grid/monitor.py:352-381` y `scripts/grid_ctl.py:81-83,128-136,171-188`: objetivo incluye el polvo barrible, estado muestra el campo persistido, CLI ofrece `sweep-dust --grid N` en dry-run por defecto y `--execute` para enviar.
- `grid/sim/exchange.py:26-30,73-89,132-148`, `grid/sim/runner.py:50-76,181-190,241-309,427-438` y `grid/sim/target_study.py:38-57,103-139`: el simulador acumula y barre mediante la misma función pura, y expone proceeds, residual y comisiones del barrido.
- `tests/test_grid_dust.py:1-108`: prueba filtros, residual decimal, idempotencia, parity del proceeds neto entre motor y simulador, cantidad acotada al registro y recuperación después de perder la respuesta del exchange.

## Estudio 30 días

Comando: `ASPLE_OFFLINE=1 python scripts/grid_sim.py target-study --seed 42 --workers 4 --csv data/cache/xrp_5m.csv --stdout-only`. Sin artefactos escritos. CSV SHA-256 `d18d8f9253cf4a27fc91311b12b4106e3c3aadaef76d4d621cae63e8ca3a9b96`; 100 ventanas solapadas por estrategia, avance de 7 días; capital 100 USDT, 10 niveles, comisión 0.1%. La comparación siguiente usa objetivo sobre base cash. La columna «antes» reproduce los conteos del estudio 15C-2 en `REPORTE_FASE15C.md`; «después» es el nuevo estudio 15D.

| Meta | Simple antes → después | SMART antes → después | Δ equity medio simple / SMART (pp) | Comisión total sweep simple / SMART (USDT) |
|---:|---:|---:|---:|---:|
| 0.5% | 5/100 → 64/100 | 2/100 → 37/100 | -0.1104 / +0.1520 | 0.4609 / 0.6205 |
| 1% | 2/100 → 61/100 | 0/100 → 34/100 | -0.0782 / +0.2283 | 0.4624 / 0.6293 |
| 2% | 0/100 → 55/100 | 0/100 → 24/100 | -0.5679 / +0.2401 | 0.4709 / 0.6846 |
| 3% | 0/100 → 44/100 | 0/100 → 10/100 | -0.3737 / +0.1154 | 0.4896 / 0.7289 |

Δ equity es el cambio medio en equity final porcentual del grupo frente a su baseline 15D sin objetivo, no una estimación causal del aporte aislado del sweep. Los proceeds netos barridos acumulados en las 100 ventanas fueron, para 0.5/1/2/3%, simple: 460.46/461.96/470.39/489.15 USDT; SMART: 619.85/628.66/683.87/728.16 USDT. Cada ventana se solapa con otras, por lo que estos totales no son capital independiente.

El barrido sí cambia el resultado medido de alcanzabilidad: los conteos suben respecto a 15C-2 cuando proceeds de polvo vendible pasan a ser cash del objetivo. Esto describe la regla simulada con ejecución hipotética al bid; no demuestra rentabilidad ni ejecución real. El sweep fee total en los grupos es pequeño frente al proceeds barrido (0.1%); no elimina el costo original de redondeo: el costo medido de polvo por ciclo sigue cerca de 0.19 USDT simple y 0.30 SMART, frente a 0.09 USDT de spacing por celda. En las 100 ventanas, fee sweep promedio es aproximadamente 0.0046–0.0049 USDT simple y 0.0062–0.0073 SMART por ventana.

## Validación

- Suite enfocada `tests/test_grid_dust.py tests/test_grid_db.py tests/test_grid_engine.py tests/test_grid_target.py`: **78 passed** con `--basetemp data/cache/pytest_15D_focus5`. Dos warnings preexistentes.
- La corrida enfocada sin basetemp tuvo 2 errores de setup por `PermissionError: WinError 5` enumerando `pytest-of-ramon`; se repitió el mismo alcance en basetemp repo-local y pasó.
- Suite offline completa: **513 passed, 18 deselected, 7 warnings en 42.66 s**, comando con virtualenv y basetemp: `ASPLE_OFFLINE=1 .\venv\Scripts\python.exe -m pytest tests -p no:cacheprovider -q -m "not live" --basetemp data/cache/pytest_15D_final`.
- Batería grid: **386 passed, 145 deselected, 2 warnings en 35.89 s**, misma selección offline y `-k "grid"`.
- No se hizo campaña de mutación temporal de producción. Las aserciones de idempotencia, cantidad vendida y residual exacto cubren los tres fallos indicados, pero no se informa un conteo de mutantes ejecutados/eliminados.
- No hubo llamadas Testnet/live, commits ni push. Discrepancias y límites: `DISCREPANCIAS_FASE15D.md`.

## Fase 15D-2 — plazo máximo y reestudio mínimo

- `grid/policy.py:68-91,286-296`: `validate_params` acepta `max_days` solo si es finito y mayor que cero. `evaluate_max_days` calcula días UTC desde `created_at`, incluye cualquier pausa y vence en `age_days >= max_days`; sin el parámetro devuelve `expired=false`.
- `grid/monitor.py:268,403-405,481-525`: monitor evalúa el plazo después del cierre CLOSE y del plan TARGET, y antes de ADJUST/PAUSE/RESUME. Persiste `max_days_close_plan` antes del cierre y usa cierre a repositorio; `MAX_DAYS_REACHED` incluye edad, límite, celdas retenidas y estado/cantidad residual del polvo. La llamada de motor usa `grid/engine.py:2448-2530` y pasa `reason=max_days` al barrido automático.
- `grid/sim/runner.py:15,79-103,197-201,232-268,345-428,516-527`: el simulador acepta `max_days` también en simple y aplica el mismo helper. Retiene inventario (no lo vende a mercado), cancela BUYs y reporta celdas y PnL no realizado. El simulador simple evalúa el plazo en cada vela.
- `scripts/grid_ctl.py:57,144-145,250-270`: `open --max-days N` valida y guarda el valor para simple/SMART; `status` lo presenta.
- Tests añadidos/actualizados: `tests/test_grid_maxdays.py:1-64` verifica validación, umbral exacto, lapso pausado, ruta simple simulada y vencimiento del motor con fake. `tests/test_grid_target.py:42-49,78-99` comprueba polvo en proyección y que el cash de TARGET se fija tras el barrido.

### Resultados descriptivos

Máximo plazo: comando `ASPLE_OFFLINE=1 .\\venv\\Scripts\\python.exe scripts/grid_sim.py max-days-study --seed 42 --csv data/cache/xrp_5m.csv --stdout-only`; CSV SHA-256 `d18d8f9253cf4a27fc91311b12b4106e3c3aadaef76d4d621cae63e8ca3a9b96`. 92 ventanas solapadas de 90 días, paso 7 días, capital 100 USDT, 10 celdas, comisión 0.1%.

| Estrategia | Plazo | Equity final medio (USDT) | Celdas al repositorio medio | PnL no realizado medio al vencer (USDT) |
|---|---:|---:|---:|---:|
| simple | ninguno | 97.1146 | — | — |
| simple | 30 d | 100.1779 | 4.4457 | -5.3413 |
| simple | 60 d | 98.1013 | 4.7065 | -8.3223 |
| SMART | ninguno | 98.8461 | — | — |
| SMART | 30 d | 98.8000 | 2.0000 | -0.2738 |
| SMART | 60 d | 98.8461 | — | — |

Tabla de objetivos de 30 días, base cash, 100 ventanas, mismo CSV/seed; cambio porcentual de precio de cierre frente a inicio superior a 20%:

| Objetivo | Simple aciertos | Simple >20% alza | Simple Δ equity medio solo aciertos (pp) | SMART aciertos | SMART >20% alza | SMART Δ equity medio solo aciertos (pp) |
|---:|---:|---:|---:|---:|---:|---:|
| 0.5% | 64 | 11 | -0.1724 | 37 | 5 | +0.4107 |
| 1% | 61 | 9 | -0.1283 | 34 | 4 | +0.6714 |
| 2% | 55 | 8 | -1.0325 | 24 | 3 | +1.0005 |
| 3% | 44 | 8 | -0.8492 | 10 | 1 | +1.1537 |

El delta es equity final del grupo con acierto menos baseline sin objetivo de las mismas ventanas que acertaron; no es estimación causal. El resto de ventanas no entra en esa media. La cantidad de aciertos en ventanas con alza >20% solo etiqueta coexistencia con una subida fuerte; no atribuye la meta al alza ni a los ciclos. Las ventanas se solapan y la simulación no acredita ejecución real.

La suite offline final y la campaña temporal de mutación se consignan tras su ejecución en esta fase.

### Mutación temporal 15D-2

Se ejecutaron mutaciones locales una por una, restaurando bytes originales en `finally`; targeted pytest debía fallar para matar el mutante. Resultado: **8/8 muertos**. (1) omitir débito de polvo, (2) tomar saldo XRP de cuenta en vez del registro, (3) sumar dos veces un BUY replayado, (4) excluir polvo de `evaluate_target`, (5) cambiar `>=` por `>`, (6) descontar días pausados, (7) vender inventario a mercado al vencer, (8) no barrer antes de fijar `cash_total` TARGET. Evidencia: aserciones dirigidas en `tests/test_grid_dust.py`, `tests/test_grid_target.py` y `tests/test_grid_maxdays.py`; para mutante 8 la prueba del target con polvo observado cayó de 1009.99 proyectado a 1000 cash real. Cada archivo de producción fue restaurado desde sus bytes anteriores al siguiente mutante. No se hizo commit.

Suite offline final: pendiente de ejecutar después de los cambios de reporte.

Suite offline completa requerida: `ASPLE_OFFLINE=1 .\\venv\\Scripts\\python.exe -m pytest tests -p no:cacheprovider -q -m "not live" --basetemp data/cache/pytest_15D2_final`: **522 passed, 18 deselected, 7 warnings, 40.96 s**. `git diff --check` no reportó errores. No se usaron llamadas Binance/Testnet/live; no hubo commit ni push.

Revalidación posterior al ajuste del resultado de cierre para exponer explícitamente polvo barrido y pendiente en `MAX_DAYS_REACHED`: suite offline completa **522 passed, 18 deselected, 7 warnings, 41.79 s** con `--basetemp data/cache/pytest_15D2_final2`. La salida de cierre contiene `dust_sweep`; los eventos de plazo incluyen `dust_swept` y `dust_pending`.

## Fase 15D-3 — cierre de `max_days` con reinicio

Las pruebas añadidas en `tests/test_grid_maxdays.py` cubren: paridad motor/simulador con la celda 2 en repositorio, BUYs abiertas canceladas, cash libre 1762.844 USDT, polvo barrido 10.0 y residual 0.05; TARGET y CLOSE antes de MAX_DAYS; MAX_DAYS antes de PAUSE/RESUME y ADJUST; reinicio tras plan STARTED, cancelación BUY, movimiento al repositorio y sweep previo al evento; ausencia de re-disparo en CLOSED/HOLDING; y equivalencia entre `created_at` naive/aware. `tests/test_grid_dust.py` conserva las aserciones de residuo e idempotencia.

La prueba de paridad encontró y permitió corregir en `grid/engine.py:278-285` que la creación de grids `simple` rechazaba `params`, aunque `max_days` ya estaba expuesto por CLI/simulador. Dos pruebas de caída encontraron y permitieron corregir en `grid/monitor.py:211-254,301-308,584-638` que el monitor ignoraba un plan STARTED si el reloj retrocedía y que un proceso caído tras el sweep podía dejar el evento MAX_DAYS sin persistir. El plan ahora guarda datos recuperables del evento y el monitor recupera el evento idempotentemente cuando encuentra el grid CLOSED/CLOSING.

Pruebas enfocadas: **24 passed** (`tests/test_grid_maxdays.py tests/test_grid_dust.py`, basetemp `data/cache/pytest_15D3_focus_final`). Campaña de mutación temporal, con bytes de producción restaurados después de cada caso: **4/4 muertos**: prioridad TARGET removida, STARTED ignorado al reiniciar, evento duplicado al reanudar y venta a mercado de inventario al vencer. La prueba de duplicación exige una segunda pasada del monitor tras el primer cierre.

Suite offline completa posterior a los cambios de esta fase: pendiente.

Suite offline completa Fase 15D-3: `ASPLE_OFFLINE=1 .\\venv\\Scripts\\python.exe -m pytest tests -p no:cacheprovider -q -m "not live" --basetemp data/cache/pytest_15D3_final`: **534 passed, 18 deselected, 7 warnings, 42.03 s**. No hubo llamadas Binance/Testnet, commits ni push.
