# DISCREPANCIAS FASE 44 — cierre 44g–44k

| Requisito | Estado | Motivo / evidencia |
|---|---|---|
| Despacho exclusivo v1/v2/Cazador/Escalera/Seguidor/Control | PARCIAL | El selector resuelve las seis etiquetas y el flujo tiene ramas dedicadas; falta una prueba de revisión con espías que confirme que en una ronda cada política se invoca exactamente una vez y ninguna llama a otra. |
| Escalera: apertura y repricing de compras abiertas | PARCIAL | Abre cuatro bots y re-precia con `reposition_meta_bot`, cancelación verificada, intervalo 4 h y máximo seis eventos/día. Rearme tras venta en el escalón libre más alto pendiente: no implementado. |
| Seguidor: grid inicial y límite 40 % | PARCIAL | Abre ocho niveles, la compra inicial se limita al 40 %. Recentrado, cooldown operativo, transferencia de inventario, sesgo 60/80 % y re-espaciado diario: no implementados en la ruta del operador. |
| Control fijo | HECHO parcialmente | Abre grid simple de ocho niveles a ±2σ del símbolo elegible de mayor σ y luego no interviene. No hay prueba E2E de fills de Control. |
| Cazador exclusivo del torneo | PARCIAL | Despacho directo, sin caché v2 ni interruptor global; rotación/agregado restringen semilla inicial a 40 %. Sin pieza, no crea su grid inicial de arranque. |
| Inicio seguro de seis metas | PARCIAL | Confirma en Laboratorio, exige seis competidores, chequea saldo libre exchange + reservas, activa metas y registra `TORNEO_RONDA_INICIADA` por meta. Las piezas se abren luego por vigilante; no se ejecuta apertura secuencial dentro del endpoint ni hay garantía de órdenes iniciales antes de responder. |
| Aislamiento de efectivo | HECHO en prueba sintética de saldos vacíos | Cada meta conserva su capital lógico; test de seis metas verifica 50 USDT por meta y equity coherente. No simula fills por competidor. |
| Marcador | PARCIAL | Muestra equity reportada, PnL de niveles, caída máxima contra capital, ciclos, acciones, estado y días al cierre. P(meta) v2 al inicio no se obtiene de forma fiable. |
| Informe de ronda | HECHO con prueba sintética | Cuando las seis metas terminan, genera `reports/torneo/ronda_<n>.md` con tabla, SVG embebido y propuesta de reasignación manual. |
| Telegram semanal | HECHO con prueba del callback | Trabajo del scheduler lunes 12:17 UTC que invoca el alert dispatcher para cada ronda. No se probó entrega externa y no se usó red. |
| E2E de los seis competidores con fills y comisiones durante varios días | NO HECHO | Solo pruebas enfocadas de contabilidad existente, apertura/políticas aisladas, Cazador y GridEngine. No existe exchange falso común que recorra todas las políticas a través del ciclo del vigilante por varios días. No se declara acreditada la contabilidad integral solicitada. |
| Regresión v1 contra `776eedf` por parte | HECHO | El test obligatorio pasó en 44g, 44h, 44i, 44j y 44k. Ver comandos y resultados de la entrega. |
| Informe de cierre de Fase 43 | HECHO | Localizado y leído desde `Claude outputs/REPORTE_CIERRE_FASE43.md`. |

**No iniciar la ronda en S1 todavía.** La prueba integral por competidor está pendiente y Seguidor carece de mantenimiento operativo; Escalera carece de rearme. La ausencia de E2E es una limitación de evidencia, no una prueba de que la contabilidad sea incorrecta.

## Decisión de ronda 1 (44l)

HECHO: Laboratorio/API ofrecen por defecto v1, v2, Cazador, Escalera y Control; Seguidor aparece deshabilitado como “no disponible (ronda 2)” y el backend rechaza incluirlo. El límite mínimo de creación para ronda 1 es 250 USDT más reservas existentes.
