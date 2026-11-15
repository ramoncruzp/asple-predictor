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
