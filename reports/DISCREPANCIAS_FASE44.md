# DISCREPANCIAS FASE 44

| Requisito | Estado | Evidencia / impacto |
|---|---|---|
| Competidores aislados por política | PENDIENTE | Snapshots identifican los tipos; el operador aún despacha v1/v2 y Cazador sigue acoplado a la rama v2. |
| Inicio transaccional con balance | PARCIAL | Crea y activa metas en BD juntas y comprueba saldo libre atribuible >= max(300, capital total); falta apertura atómica de grids/órdenes y un balance exchange puede cambiar tras el chequeo. |
| Escalera/Seguidor/Control | PARCIAL | Funciones geométricas deterministas y pruebas, sin órdenes reales en el ciclo del vigilante. |
| Cazador | PARCIAL | Filtros y tope en ruta de completado Cazador; falta despacho exclusivo por `competidor`. |
| Marcador | PARCIAL | Tabla resume eventos/valoraciones que existen; no calcula duración hasta meta ni P(meta) v2 fiable, no incluye generación de ronda_<n>.md/SVG. |
| Telegram semanal | PENDIENTE | No se agregó trabajo scheduler semanal del torneo. |
| E2E de los seis competidores con fills/fees | PENDIENTE | Solo se ejecutaron pruebas unitarias de ronda, políticas, Cazador, GridEngine y coherencia existente. |
| Regresión v1 vs 776eedf | NO EJECUTADA | No se debe afirmar paridad verificada en esta fase. |
| Reporte de cierre Fase 43 citado por el prompt | NO ENCONTRADO | `REPORTE_CIERRE_FASE43.md` no aparece en el inventario de archivos del checkout. |

La ronda API no equivale a un torneo operativo. No usarla en S1 hasta cerrar las discrepancias que afectan el despacho de estrategias y las órdenes.
