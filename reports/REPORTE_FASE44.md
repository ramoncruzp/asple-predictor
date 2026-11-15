# REPORTE FASE 44 — Torneo de estrategias

## Implementado y verificado en esta rama

- 44a: endpoint de ronda transaccional con snapshots `competidor`/`ronda`, activación conjunta y verificación del efectivo libre; Laboratorio permite seleccionar competidores y presenta el saldo previo.
- 44b: Cazador filtra caídas mayores a 1σ/24 h, conserva el requisito de profundidad y limita compra inicial a 40 % de la pieza.
- 44c–44d: cálculos puros probados para Escalera, Grid seguidor, recentrado y geometría Control.
- 44e: endpoint y tabla de marcador en Laboratorio con datos disponibles del ledger.
- Pruebas sintéticas enfocadas y aislamiento offline; sin S1, Testnet, BD real ni red.

## Estado operativo

Las fórmulas de 44c/44d/Control aún no están conectadas a la apertura/revisión/rotación del operador. Por ello, las metas tournament de esos competidores no ejecutan esas políticas, y el Cazador no está despachado como política exclusiva. El scoreboard no incluye días hasta objetivo ni generación automática del informe de cierre. No desplegar ni iniciar una ronda hasta completar esa integración.

## 44p — E2E de varios días (parcial, 2026-11-15)

Se agregó `tests/e2e/fake_exchange.py` con libro sintético, cruce LIMIT, fills MARKET con slippage, trades con `quoteQty` y comisión de 0,1 %. MARKET informa `cummulativeQuoteQty=0`. La prueba recorre tres días simulados por guion (subida, caída y oscilación), crea la ronda de cinco competidores por la API y una meta heredada, y ejecuta el vigilante cada paso de cinco minutos. Las pruebas de contrato del fake confirman la deducción quote + fee y el fill LIMIT al cruce.

La ejecución completa de `tests/e2e/test_torneo_multidia.py` terminó con 3 escenarios aprobados y 1 fallo en el contrato MARKET por una aserción float/Decimal del propio test; duración total **296,04 s**. Se corrigió esa aserción y los dos contratos del fake pasaron en su corrida posterior, junto con la regresión v1 contra `776eedf`; codificación `HALLAZGOS=0`. La E2E aún no queda HECHA: el scanner sintético no ofrece candidatos, así que no se abren grids, no hay fills ni PnL realizado, y no se ejercen los cinco comportamientos de competidor ni el corte −18 %. El presupuesto de revisión se fijó en cero para rotar una meta por turno, por lo que tampoco equivale a revisar todas las metas como en producción. El tiempo excede el límite de 180 s.

Comandos ejecutados:

```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP ('pytest-44e2e-final-'+[guid]::NewGuid().ToString('N')); python -m pytest tests/e2e/test_torneo_multidia.py -q --basetemp="$base"
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP ('pytest-44e2e-contracts-fix-'+[guid]::NewGuid().ToString('N')); python -m pytest tests/e2e/test_torneo_multidia.py::test_exchange_falso_market_publica_quote_cero_y_comision_real tests/e2e/test_torneo_multidia.py::test_exchange_falso_llena_limit_al_cruzar_y_libera_reserva -q --basetemp="$base"
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP ('pytest-44e2e-v1-'+[guid]::NewGuid().ToString('N')); python -m pytest tests/test_meta_own_grid_evaluation.py::test_v1_plan_y_review_son_identicos_a_776eedf -q --basetemp="$base"
python scripts/revisar_codificacion.py
```

Resultado observado a 3 días (cada meta queda sin operaciones; capital y equity permanecen en 50 USDT, 0,00 %):

| Guion | v1 | v2 | Cazador | Escalera | Control | Heredada |
|---|---:|---:|---:|---:|---:|---:|
| Subida +8 % | 0,00 % | 0,00 % | 0,00 % | 0,00 % | 0,00 % | 0,00 % |
| Caída −25 % | 0,00 % | 0,00 % | 0,00 % | 0,00 % | 0,00 % | 0,00 % |
| Oscilación ±4 % | 0,00 % | 0,00 % | 0,00 % | 0,00 % | 0,00 % | 0,00 % |

Estos resultados describen únicamente el estado inactivo del fixture; no comparan estrategias. La ronda 1 sigue sin estar lista para S1. No se despliega.


## 44q — E2E operativo del torneo (2026-11-15)

Se sustituyó el recorrido inactivo por una simulación aislada con `market_data.db` temporal, dos días de velas 5m previas, filtros de símbolo, cuatro mercados sintéticos y libro de profundidad; `DDDUSDT` carece de libro y Cazador lo excluye. La ronda abre cinco competidores por la API real: v1 usa la alternativa determinista de sus pruebas de regresión; v2 documenta `no_hacer_nada` porque no hay caché sintética compatible. El monitor, MetaCore y la revisión del operador recorren las cinco metas en los 192 pasos de quince minutos de cada guion.

La comprobación por paso confirma efectivo no negativo, efectivo atribuible no superior al saldo del exchange, `cash_coherent`, equity frente a capital + PnL realizado − comisiones con tolerancia de 0,01 USDT, y ausencia de `META_VALUACION_INCOHERENTE`. En oscilación Escalera obtiene ventas con PnL distinto de cero y rearma. En caída Cazador y Escalera alcanzan el corte de pérdida y terminan sin órdenes abiertas. La prueba de Cazador confirma que abre su cartera después de las dos revisiones de persistencia.

| Guion (2 días) | v1 | v2 | Cazador | Escalera | Control |
|---|---:|---:|---:|---:|---:|
| Subida (+8 %) | 0,00 % | 0,00 % (sin acci?n) | 0,00 % | 0,00 % | +0,19 % |
| Caída (−25 %) | −4,10 % | 0,00 % (sin acción) | −18,49 % (cortada) | −18,30 % (cortada) | −11,47 % |
| Oscilación (±4 %) | 0,00 % | 0,00 % (sin acción) | −3,63 % | +3,45 % | −0,77 % |

El comando completo terminó con **5 aprobadas en 614,78 s**. Tiempos por guion: subida 226,65 s, caída 169,23 s y oscilación 211,58 s. Se usó el recorrido mínimo de 2 días; subida y oscilación aún superan 180 s. La duración excedida queda como limitación medida del E2E, sin acortar los escenarios requeridos.

Defectos de producci?n hallados y corregidos durante la prueba:

- Las compras MARKET iniciales usan IDs `gI…`; la búsqueda del fill de compra para PnL asumía el ID convencional `BUY`. El quote acumulado MARKET también puede ser cero aunque los trades traigan quote y comisión. El motor ahora obtiene el ID inicial correcto y calcula el PnL con los trades tanto al vender como al liquidar.
- El detector de coherencia de efectivo no reconoc?a `GRID_COMPRA_INICIAL` como evento que explica el d?bito del efectivo.
- La reconciliación de equity sobrescribía el fill más reciente de un nivel con compras anteriores al recorrer eventos en orden inverso; ahora conserva el más reciente.
- Los objetivos de venta de Escalera para bots no comprados podían quedar por debajo del mid y sus grids eran rechazados. Ahora cada escalón queda por encima del mid y con precio de venta único.

Comandos finales ejecutados:

```powershell
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP ('pytest-44q-e2e-verified-'+[guid]::NewGuid().ToString('N')); python -m pytest tests/e2e/test_torneo_multidia.py -q -s --basetemp="$base"
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP ('pytest-44q-oscilacion-pass-'+[guid]::NewGuid().ToString('N')); python -m pytest 'tests/e2e/test_torneo_multidia.py::test_torneo_operativo_multidia_offline[oscilacion]' -q -s --basetemp="$base"
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP ('pytest-44q-units-final-'+[guid]::NewGuid().ToString('N')); python -m pytest tests/test_meta_core.py tests/test_grid_engine.py tests/test_torneo_policies.py -q --basetemp="$base"
$env:ASPLE_OFFLINE='1'; $base=Join-Path $env:TEMP ('pytest-44q-v1-'+[guid]::NewGuid().ToString('N')); python -m pytest tests/test_meta_own_grid_evaluation.py::test_v1_plan_y_review_son_identicos_a_776eedf -q --basetemp="$base"
python scripts/revisar_codificacion.py
```

Resultados: E2E completo **5 passed**; MetaCore + GridEngine + políticas de torneo **125 passed**; regresión v1 **1 passed**; codificación **HALLAZGOS=0**. Ejecución offline, sin S1, Testnet real, `.env`, BD real ni red.
