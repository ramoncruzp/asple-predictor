# Fase 22 — Experimento pareado de préstamos

## Resultado

| Punto | Estado | Evidencia |
|---|---|---|
| P1 — apertura pareada | HECHO (API); CLI `open-pair` NO HECHO | `api/routes/grids.py:227,300,307,568-665`; `grid/loan_cohorts.py:161-169` |
| P2 — resumen pareado | HECHO | `api/routes/grids.py:774-881` |
| P3 — panel de lectura | HECHO | `frontend/grids.js:211-257,443-447`; `tests/ui/test_grids_browser.py:136-164` |

## Diseño y límites

- `POST /api/grids/pair` valida autorización, confirmación, moneda activa/lista, dos cupos y capital funcional Smart. El modo `dry_run` devuelve dos planes sin crear grids. La ejecución asigna aleatoriamente el primer brazo de forma reproducible con `pair_seed`; el segundo rango se desplaza y prueba offsets hasta 0,30 % para evitar conflictos de niveles de venta. Ambos conservan `pair_id`, semilla, brazo, orden y desplazamiento. Reutilizan la ruta común de apertura y sus guardas (`api/routes/grids.py:300-345,568-665`).
- La política y el motor no aceptan metadatos pareados como parte de la creación inicial; por eso se guardan mediante merge tras crear cada brazo (`api/routes/grids.py:300-305`). Si el segundo brazo no puede abrirse luego de crear uno o ambos grids, se marcan como huérfanos y se registra `PAIR_ORPHAN`; no se cierra ni revierte un grid ya abierto (`api/routes/grids.py:640-665`). La comprobación existente de conflicto de venta ocurre antes de crear/persistir el grid (`grid/engine.py:332-350`, solo inspección).
- La creación del par queda serializada con el lock de hilo y el lock de proceso compartidos (`grid/loan_cohorts.py:33-80,161-169`). El lock de proceso identifica la base por URL y usa un archivo temporal; cubre procesos de esta máquina que comparten esa identidad, no hosts distintos. Se corrigió la inicialización concurrente del archivo con creación exclusiva. La apertura real y su duración máxima en Testnet no se verificaron.
- El resumen solo analiza pares completos, cerrados y con P&L, capital, fechas y duración válidos. Calcula para cada brazo P&L realizado/capital/días abiertos (piso de un día), `d_i`, media, desviación muestral, t pareada cuando `n >= 4`, IC bootstrap percentil del 95 % con semilla fija, victorias, proporción con préstamos efectivos, razón de duraciones y exclusiones (`api/routes/grids.py:774-870`). Concluyente requiere al menos 15 pares, IC que no incluya cero y préstamos reales en al menos la mitad (`api/routes/grids.py:854-870`). Es evidencia observacional, no una estimación causal.
- El panel muestra estados por brazo, `d_i`, media, intervalo, razón de no conclusión, efecto mínimo detectable y huérfanos, sin colores que sugieran veredicto (`frontend/grids.js:211-257`). El histórico que no contiene préstamos reales mantiene la advertencia fija de `frontend/grids.js:257`.
- El CLI `open-pair` no se implementó: se dejó fuera porque su integración rebasaba el límite aproximado de 40 líneas indicado. La API pareada sí está implementada.

## Pruebas y mutaciones

- Pruebas enfocadas después de los cambios: `65 passed in 11.74s` para `tests/test_grid_loan_pairs.py`, `tests/test_grid_loan_summary_comparison.py`, `tests/test_grids_api.py` y `tests/test_loan_cohorts_process.py`.
- La línea base no-UI tuvo `1 failed, 1004 passed, 1 skipped, 18 deselected, 38 warnings in 152.05s (0:02:32)`. El fallo fue `tests/test_loan_cohorts_process.py::test_eight_processes_share_control_every_third_creation`: en Windows hubo una carrera al inicializar el archivo de lock vacío (`PermissionError`); el reparto quedó con un solo control. La inicialización exclusiva del lock la corrige; el test y una prueba multiproceso del par pasaron juntos: `2 passed in 4.79s`.
- Mutación P1: se quitó el desplazamiento del segundo brazo; la prueba integrada falló con el conflicto de nivel esperado (`MUTATION_EXIT=1`). SHA-256 antes y restaurado: `2782caddf54694b7efd185344cdb46df596d11c4af6277dabb4fb02d846355cd`; mutado: `bc51b48e3e553330a1eb74d4c4996b91113f8cdd9d2857409582fc9db39821a9`.
- Mutación P2: se anuló el requisito de proporción de pares tratados; `test_paired_summary_requires_minimum_n_and_real_loans` falló al clasificar como concluyente una muestra sin suficientes pares tratados (`MUTATION_EXIT=1`). SHA-256 antes y restaurado: `2782caddf54694b7efd185344cdb46df596d11c4af6277dabb4fb02d846355cd`; mutado: `a16eccd9aefb5de4a9768f0315f00fbfae28206c560ac57bc27143f1e103ec2a`.
- Mutación P3: se quitó la nota fija del panel histórico; la prueba UI falló al comprobar el aviso (`MUTATION_EXIT=1`). SHA-256 antes y restaurado: `232518e6d8271fee750fc3cfe3b2df26e7422342af3a84c21245916bfef12964`; mutado: `2c77aaeb393a24dafb5a6b5617d1f1b34f9d0901e64f0043c4a74b9c01aa3fd4`.

## Suites finales

- No-UI, con `ASPLE_OFFLINE=1` y `-m "not live"`: `1023 passed, 1 skipped, 18 deselected, 38 warnings in 152.10s (0:02:32)`.
- UI, con `-m "not live"`: `107 passed, 2 warnings in 1291.87s (0:21:31)`. Playwright informó bloqueo de hosts externos; no se observaron fallos de interfaz.

## Decisiones pendientes de Ramón

Elegir antes de abrir un experimento real: máximo de grids concurrentes suficiente para reservar dos cupos; capital por brazo; símbolo; y cantidad objetivo de pares. El CLI queda como trabajo separado si se desea.

## No verificado

- Ninguna apertura real en Testnet: aceptación de órdenes, latencia, deslizamiento y conflictos de precio/mercado.
- No hay evidencia aún de pares reales completos ni de que el tamaño muestral o la potencia estadística sean suficientes para una conclusión.
- El lock entre procesos se probó en el host actual; no coordina procesos en máquinas distintas.
- No se hizo stage, commit ni push. No se modificaron datos reales, `.env`, `data/` ni `models/saved/`.
