# DISCREPANCIAS FASE 44 — revisión tras 44l–44o

| Requisito ronda 1 | Estado | Motivo / evidencia |
|---|---|---|
| Cinco competidores v1, v2, Cazador, Escalera y Control | HECHO | Laboratorio muestra solo los cinco; Seguidor aparece deshabilitado como “no disponible (ronda 2)”; API rechaza Seguidor. Prueba verifica 250 USDT mínimos más reservas. |
| Despacho exclusivo durante revisión | HECHO | Prueba con espías recorre las cinco metas y confirma una política invocada por meta. v2 no consulta el switch ni llama Cazador; Cazador llama solo su flujo directo. Regresión v1 independiente pasa. |
| Escalera apertura y repricing | HECHO en el código | Abre cuatro bots y repone órdenes no compradas cada 4 h, con cancelación verificada y tope de seis por día. |
| Escalera rearme tras venta confirmada | PARCIAL | La ruta detecta SELL_FILLED, calcula ingreso neto quote-fee, elige primer escalón libre, cierra/libera bot viejo y abre uno nuevo con validaciones. Solo hay prueba unitaria de selección de escalón; falta test de compra/venta realista y contabilidad/equity, orden abierta y límite de bots tras el rearme. |
| Control | HECHO en el código | Abre grid fijo a ±2σ y no interviene luego. La simulación multi-día requerida no está hecha. |
| Cazador: primera cartera | PARCIAL | Sin piezas decide agregar desde efectivo libre, aplica persistencia de dos revisiones y los filtros de deriva/profundidad/40 %. Pruebas de persistencia sustituyen ejecución; falta validar apertura y fills MARKET/LIMIT con fees y contabilidad. |
| Inicio seguro ronda 1 | PARCIAL | Crea las cinco metas en transacción y valida saldo exchange ≥250 + reservas. Activa metas; la apertura de piezas ocurre luego mediante el vigilante, no dentro de la solicitud. Falta prueba de orden inicial en ronda realista. |
| Aislamiento de efectivo y corte −18 % | PARCIAL | Hay prueba sintética previa de saldos aislados y watchdog aislado, pero no se recorre corte acumulado con fills para cada una de las cinco políticas. |
| Informe de ronda y Telegram semanal | HECHO con pruebas sintéticas previas | Informe automático al terminar las cinco metas de ronda 1; alerta semanal lunes 12:17 UTC. No se probó entrega externa Telegram. |
| Espías de políticas | HECHO | `tests/test_torneo_dispatch.py::test_revision_de_ronda_invoca_solo_el_competidor_asignado` pasó. También pasó prueba del vigilante que continúa tras error de una meta. |
| E2E 3 días × 3 guiones × 5 competidores con LIMIT/MARKET y comisiones | NO HECHO | No existe harness común de exchange falso que ejecute las rutas de los cinco competidores junto con el vigilante, fills, fees, cierre por pérdida y evolución multi-día. Los tests de políticas actuales no sustituyen esa validación. |
| Regresión v1 contra `776eedf` por parte | HECHO | Pasó en 44l, 44m, 44n y 44o con el comando exigido. |

## Seguidor

**Ronda 2.** Excluido de la ronda 1 y no seleccionable en API/UI. Falta integrar y probar recentrado, sesgo de inventario/efectivo y re-espaciado diario.

**No iniciar ronda 1 en S1 todavía.** El rearme de Escalera y la primera cartera Cazador no tienen prueba de fills/fees; además falta la E2E multi-día completa. La ronda no cumple todos los criterios HECHO.
