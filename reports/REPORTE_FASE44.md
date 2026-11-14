# REPORTE FASE 44 — Torneo de estrategias

## Implementado y verificado en esta rama

- 44a: endpoint de ronda transaccional con snapshots `competidor`/`ronda`, activación conjunta y verificación del efectivo libre; Laboratorio permite seleccionar competidores y presenta el saldo previo.
- 44b: Cazador filtra caídas mayores a 1σ/24 h, conserva el requisito de profundidad y limita compra inicial a 40 % de la pieza.
- 44c–44d: cálculos puros probados para Escalera, Grid seguidor, recentrado y geometría Control.
- 44e: endpoint y tabla de marcador en Laboratorio con datos disponibles del ledger.
- Pruebas sintéticas enfocadas y aislamiento offline; sin S1, Testnet, BD real ni red.

## Estado operativo

Las fórmulas de 44c/44d/Control aún no están conectadas a la apertura/revisión/rotación del operador. Por ello, las metas tournament de esos competidores no ejecutan esas políticas, y el Cazador no está despachado como política exclusiva. El scoreboard no incluye días hasta objetivo ni generación automática del informe de cierre. No desplegar ni iniciar una ronda hasta completar esa integración.
