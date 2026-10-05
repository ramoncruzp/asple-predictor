# Fase 18B-6 — Discrepancias y decisiones fuera del alcance

## Polvo que sigue condicionando selección o puntuación

El prompt prohíbe editar `suggest_structure`, `grid_advisor.py` y `grid/scanner.py`; se conserva su comportamiento y se deja la decisión para una fase posterior:

- `grid/structure.py:108-119,184-190`: `evaluate_levels` incluye dust en `required_spacing`, `net_edge_pct` y `spacing_ok`; `suggest_structure` selecciona el primer número de niveles que cumple `spacing_ok` y `cell_ok`.
- `api/routes/grids.py:171-180,208-210`: la ruta de scan llama a `suggest_structure` y devuelve esa estructura sugerida. Por ello la sugerencia del Scanner todavía puede no existir o ser inviable por polvo.
- `api/routes/grid_advisor.py:75-88,124`: el Advisor selecciona niveles y compara el objetivo con `net_edge_pct`; informa además el margen neto calculado.
- `grid/scanner.py:49-55,71-72`: elegibilidad dura depende de `suggest_structure.feasible` y de `net_edge_pct > 0`. `grid/scanner.py:97-109` puntúa el margen neto; `grid/scanner.py:123-125` añade aviso por margen neto bajo.
- `grid/auto_open.py:54-56,76-80`: auto-open conserva los filtros de elegibilidad/puntaje y la guarda de `suggested_structure.feasible`. Como esas decisiones llegan del Scanner intacto, una fila rechazada aguas arriba por polvo aún no llega a la rama de apertura. Se quitó únicamente el bloqueo local por `net_margin <= 0`, solicitado expresamente; por tanto no se afirma que se haya eliminado toda dependencia indirecta de polvo en la campaña automática.
- `api/routes/grid_structure.py:69-72,82-89`: neto, ciclos estimados y polvo permanecen como mediciones informativas; ya no deciden `feasible` ni el objetivo de margen de esa ruta.

## Pruebas cambiadas y motivo

- `tests/test_grid_structure_preview_api.py`: el caso de estructura sugerida ahora espera 15 niveles elegidos por margen bruto/celda, viabilidad aunque el neto estimado resulte negativo y `cycles_to_target=null` cuando el cálculo neto teórico no da ciclos positivos. Se añadió el caso sintético ADA y los límites de margen/celda.
- `tests/test_grids_api.py`: se reemplazó la expectativa anterior de rechazo por neto no positivo; el guard y el flujo dry-run + exchange fake prueban la nueva regla.
- `tests/test_grid_auto_open.py`: se reemplazó el rechazo por neto cero con apertura fake y evento informativo de polvo.
- `tests/ui/test_grids_browser.py`: el formulario espera el mínimo devuelto por el servidor en vez del neto estimado de la fila.

## Restricciones conservadas

No se modificaron `grid/structure.py`, `api/routes/grid_advisor.py` ni `grid/scanner.py`. La elegibilidad automática completa seguirá dependiendo de esos productores hasta que Ramón decida ampliar el alcance.
