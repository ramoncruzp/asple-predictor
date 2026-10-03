# Discrepancias — Fase 17B-2

## Resueltas

- Un cierre que devuelve CLOSING/errores ya no se comunica como acción completada.
- La pausa y los cierres cancel/repository no quedan bloqueados por una cotización usada solo para decorar el plan.
- El timeout del lock ahora es configurable a 15 s; como no se puede identificar cuál actor conserva el lock sin cambiar `grid/monitor.py` (fuera de alcance), el 409 usa un mensaje neutral.
- USDT ya no aparece como saldo no asignado por activo. Las celdas DONE no se suman dos veces al polvo. USDT libre continúa visible en el bloque de capital.
- `capital_share_pct` se basa en grids abiertos y repositorio; los grids CLOSED/ERROR devuelven null.
- Los rechazos de negocio y de validación FastAPI autorizados con grid existente dejan evento sin guardar el cuerpo. Los dry-run siguen sin eventos.
- El plan se presenta en texto y mantiene el detalle técnico plegado.
- Repository devuelve CLOSED al completar con éxito: `grid/engine.py:2452`. Los caminos de error de cancelación/lookup devuelven CLOSING en `grid/engine.py:2346,2384`.

## No verificado

- No se probó la UI en navegador: Playwright no corre en este entorno. Solo se validó sintaxis con Node.
- No se llamaron endpoints reales de Testnet.
- El mensaje de lock no diferencia una pasada del monitor de otra acción concurrente; distinguirlas requeriría instrumentación fuera de la lista de archivos permitidos.
- La suite conserva siete warnings de colección/entrenamiento; no se modificaron esos módulos.

## Estado de cambios

No se hicieron commit ni push. Se conservaron los artefactos no rastreados ajenos que ya estaban en el árbol.
