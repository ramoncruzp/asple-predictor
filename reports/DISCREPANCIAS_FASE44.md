# DISCREPANCIAS FASE 44 — revisión tras 44l–44o

| Requisito ronda 1 | Estado | Motivo / evidencia |
|---|---|---|
| Cinco competidores v1, v2, Cazador, Escalera y Control | HECHO | Laboratorio muestra solo los cinco; Seguidor aparece deshabilitado como “no disponible (ronda 2)”; API rechaza Seguidor. Prueba verifica 250 USDT mínimos más reservas. |
| Despacho exclusivo durante revisión | HECHO | Prueba con espías recorre las cinco metas y confirma una política invocada por meta. v2 no consulta el switch ni llama Cazador; Cazador llama solo su flujo directo. Regresión v1 independiente pasa. |
| Escalera apertura y repricing | HECHO en el código | Abre cuatro bots y repone órdenes no compradas cada 4 h, con cancelación verificada y tope de seis por día. |
| Escalera rearme tras venta confirmada | PARCIAL | La ruta detecta SELL_FILLED, calcula ingreso neto quote-fee, elige primer escalón libre, cierra/libera bot viejo y abre uno nuevo con validaciones. Solo hay prueba unitaria de selección de escalón; falta test de compra/venta realista y contabilidad/equity, orden abierta y límite de bots tras el rearme. |
| Control | HECHO en el código | Abre grid fijo a ±2σ y no interviene luego. La simulación multi-día requerida no está hecha. |
| Cazador: primera cartera | PARCIAL | Sin piezas decide agregar desde efectivo libre, aplica persistencia de dos revisiones y los filtros de deriva/profundidad/40 %. Pruebas de persistencia sustituyen ejecución; falta validar apertura y fills MARKET/LIMIT con fees y contabilidad. |
| Inicio seguro ronda 1 | HECHO (creación) | La apertura diferida mediante vigilante/revisión queda aceptada por Nexo. Este E2E crea la ronda por API y confirma las cinco metas y la meta heredada separada; no confirma aún la apertura de piezas. |
| Aislamiento de efectivo y corte −18 % | PARCIAL | Hay prueba sintética previa de saldos aislados y watchdog aislado, pero no se recorre corte acumulado con fills para cada una de las cinco políticas. |
| Informe de ronda y Telegram semanal | HECHO con pruebas sintéticas previas | Informe automático al terminar las cinco metas de ronda 1; alerta semanal lunes 12:17 UTC. No se probó entrega externa Telegram. |
| Espías de políticas | HECHO | `tests/test_torneo_dispatch.py::test_revision_de_ronda_invoca_solo_el_competidor_asignado` pasó. También pasó prueba del vigilante que continúa tras error de una meta. |
| Harness offline de exchange LIMIT/MARKET | HECHO (contrato aislado) | `tests/e2e/fake_exchange.py` registra `quoteQty`, fee 0,1 % y respuesta MARKET con `cummulativeQuoteQty=0`; pruebas de contrato cubren deducción MARKET y fill LIMIT al cruce. |
| E2E 3 días × 3 guiones × cinco competidores | PARCIAL | Los tres guiones recorren 864 pasos y pasan por `MetaCore.run_once`; la ronda real por API y la meta heredada se crean. El scanner del fixture devuelve cero candidatos, así que no se abren grids ni se ejercitan fills, PnL realizado, pérdida −18 %, Escalera, Cazador, ni motores v1/v2. Tampoco se genera `market_data.db` con velas para σ24h. La ruta de revisión se invoca cada 15 min con `review_cycle_budget_s=0`, lo que rota una meta pendiente por turno y no reproduce una revisión completa de producción. Tiempo medido de la corrida del archivo: 296,04 s, supera el tope de 180 s. |
| Regresión v1 contra `776eedf` por parte | HECHO | Pasó en 44l, 44m, 44n y 44o con el comando exigido. |

## Seguidor

**Ronda 2.** Excluido de la ronda 1 y no seleccionable en API/UI. Falta integrar y probar recentrado, sesgo de inventario/efectivo y re-espaciado diario.

**No iniciar ronda 1 en S1 todavía.** El harness valida el ciclo contable inactivo y el contrato del exchange falso, pero no abre piezas ni prueba políticas/fills en la ronda y excede el límite de tiempo. Sigue pendiente la E2E multi-día completa y optimizada, con candidatos, velas σ24h, ejecución y verificación de los cinco competidores.
