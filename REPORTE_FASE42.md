# Fase 42 — Inteligencia para grids fuera de Meta

Todos los cambios se validaron localmente con datos sintéticos. No se usaron S1, Testnet, red, `.env` ni la base de datos real. C34 y C35 ya estaban en commits anteriores.

## C34 — Etiquetas para asignación ampliada Meta

- Commit existente: `88ae452 Fase 42-C34: etiquetas para asignacion ampliada Meta`.
- Las propuestas de asignación ampliada quedan enlazadas a cartera base y cartera con pieza. El job diario etiqueta avance a 72 h y al plazo del perfil, sin abrir órdenes.
- Pruebas registradas antes de 42a: `tests/test_meta_learning.py`, `tests/test_meta_operator.py`, `tests/test_intelligence_readiness.py`, `tests/test_intelligence_api.py`: **126 passed, 5 warnings, 15,41 s**.

## C35 — Alertas horarias de evidencia

- Commit existente: `7397981 Fase 42-C35: evaluación horaria de alertas de evidencia`.
- `grid/intelligence_readiness.py:14-60` evalúa transiciones cada hora al minuto 37 UTC, con caché horaria y deduplicación. No toma `ops_lock`; usa AlertDispatcher.
- Pruebas registradas: `tests/test_alert_dispatcher.py`, `tests/test_intelligence_api.py`: **31 passed, 2 warnings, 11,37 s**.

## 42a — Recomendaciones en sombra

- `grid/grid_intelligence.py:24-77, 94-159, 214-437` define presupuesto de 60 s, umbrales, job horario al minuto 7 UTC, guard separado, motivos de exclusión, cachés acotadas, replay y persistencia. Las recomendaciones quedan con `would_apply=false`.
- Se analizan grids Smart y Simple `ACTIVE` que no pertenecen a Meta. Se excluyen repositorios, estados no activos, piezas Meta y estrategias diferentes, con motivo en el resumen.
- Candidatos: mantener; desplazar el rango un paso; cambiar niveles; agregar bot individual con objetivo neto de 0,95 %; pausar compras Smart por ruptura v2 o volatilidad alta. Se conservan las ventas/inventario existentes. Cada curva agrega el inventario retenido marcado al cierre de cada vela menos 0,1 % de comisión; el drawdown usa el patrimonio combinado.
- Recomendación solo si supera mantener por al menos 0,3 % del capital del grid y el drawdown no excede mantener +1 punto porcentual. Las alternativas descartadas guardan motivo.
- `database/db_manager.py:224-239, 430-432` define las tablas de recomendaciones/etiquetas e índices. La retención elimina recomendaciones y etiquetas de más de 30 días.
- `api/main.py:48, 148-220, 249-284` conecta e inicia/detiene el job como parte del ciclo de vida de la API. Usa scheduler/guard propios y no toma `ops_lock` para analizar.

## 42b — Etiquetas, vista y medidor

- `grid/grid_intelligence.py:531-608` etiqueta las recomendaciones al madurar siete días con velas posteriores; compara mantener con la rama recomendada. Para una recomendación "mantener", compara contra la mejor alternativa evaluada.
- `api/routes/intelligence.py:63-151, 774-829` resume recomendaciones de 24 h y resultados maduros por acción: diferencia media, intervalo bootstrap, tasa de victoria y preparación. "Listo para encender" exige 30 etiquetas, diferencia media positiva e intervalo enteramente por encima de cero. Los resultados por grid y acción llegan a C35 para alertas de transición deduplicadas.
- `frontend/intelligence.js:48-61` presenta recomendaciones, barra de avance, criterios y fecha estimada. `api/routes/grid_status.py:197-201` incluye el resumen al consultar el detalle del grid.
- `scripts/verificar_s1_meta.py:294-315` muestra recomendaciones, acciones, aplicaciones, etiquetas, duración del job y exclusiones. Abre SQLite en `mode=ro`.

## 42c — Aplicación opcional por grid

- `api/routes/grids.py:1089-1120` incorpora `PUT /api/grids/{grid_id}/intelligence-switch`, con actor, confirmación, auditoría, exclusión de piezas Meta y evidencia al encender. El valor inicial es apagado.
- `frontend/grids.js:430-445, 676-693` muestra estado, evidencia, quién/cuándo lo cambió; también permite apagar el interruptor con el grid pausado.
- `grid/grid_intelligence.py:451-523` exige la misma acción en dos recomendaciones dentro de dos horas, seis horas de enfriamiento, máximo dos aplicaciones por 24 h, grid ACTIVE, opt-in y valoración conocida.
- `api/main.py:151-205` vuelve a validar el efectivo antes de reservar celdas libres para el bot vinculado al grid origen. `grid/engine.py:272-395, 1415-1439` crea un bot Simple de un nivel que queda DONE sin recomprar y devuelve las celdas al grid origen. Rango/n pasa por `GridEngine.adjust_grid`; pausar cancela compras y el monitor las rearma cuando la condición desaparece.
- Con interruptor apagado, `_apply_if_eligible` no llama al aplicador. Los grids Meta no entran en evaluación ni en aplicación.

## Verificación local

- Comando final: `$env:ASPLE_OFFLINE='1'; .\venv\Scripts\python.exe -m pytest tests/test_alert_dispatcher.py tests/test_grid_intelligence.py tests/test_intelligence_api.py tests/test_grid_engine.py tests/test_grid_monitor_policy.py tests/test_meta_s1_diagnostic.py tests/test_script_entrypoints.py tests/ui/test_intelligence_ui.py tests/ui/test_grids_ui.py tests/ui/test_laboratorio.py -q --basetemp <carpeta-nueva-en-TEMP>`.
- Resultado: **112 passed, 6 warnings, 0 failed, 24,10 s**. Incluye replay sintético de siete días, etiqueta futura sintética, compuerta de aplicación, switch apagado, resumen/API y verificador.
- `node --check frontend/grids.js` y `node --check frontend/intelligence.js`: correctos. `py_compile` de los módulos Python tocados y `git diff --check`: correctos.
- No se ejecutó la suite completa ni el job con datos reales. La duración real y el agotamiento del presupuesto de 60 s quedan por medir en S1.

## Consulta de solo lectura para S1

Desde `cd ~/asple-predictor`, después de desplegar los commits, el job escribirá `GRID_INTEL_JOB_SUMMARY` cada hora al minuto 7 UTC. Consulta SQLite en modo solo lectura:

```bash
cd ~/asple-predictor
venv2/bin/python scripts/verificar_s1_meta.py --db ./asple_predictor.db --horas 24
```

Revisa el bloque "Grids Smart y Simple" en Inteligencia y el detalle de cada grid. No enciendas un interruptor hasta tener 30 etiquetas maduras de una acción, diferencia media positiva, intervalo que no cruce cero y aprobación de Ramón para ese grid.

## Commits

- `88ae452 Fase 42-C34: etiquetas para asignacion ampliada Meta`
- `7397981 Fase 42-C35: evaluación horaria de alertas de evidencia`
- `70bb961 Fase 42-42a: recomendaciones de grids en sombra`
- `a750418 Fase 42-42b: etiquetas y medidores de grids`
- `f45e365 Fase 42-42c: activación opcional por grid`
- `3328f6f Fase 42-42c: alertas C27 para grids`

## C38a — Instrumentación y medición sintética

- `api/routes/intelligence.py:162-173, 292, 399, 456, 490, 641, 774-788` instrumenta las secciones de la ruta. La respuesta incluye `timings_ms`; si el total supera 1 s, `asple.slow` registra el desglose y los hilos activos.
- `tests/test_intelligence_api.py:186-240` añade un escenario sintético con 15.000 muestras, 4.000 etiquetas, 5.000 eventos de bitácora y un grid.
- Comando: `$env:ASPLE_OFFLINE='1'; $env:PYTHONIOENCODING='utf-8'; $bt=Join-Path $env:TEMP ('asple-c38a-'+[guid]::NewGuid().ToString('N')); New-Item -ItemType Directory -Path $bt | Out-Null; .\venv\Scripts\python.exe -m pytest tests/test_intelligence_api.py -q -s --basetemp $bt`. Resultado: **3 passed, 3 warnings, 18,51 s**.
- Medición de la petición sintética: **1.089,07 ms**. Cinco tramos más lentos: lecturas SQL 493,99 ms; bootstrap/medidores 364,51 ms; muestras/señales 194,38 ms; estado de aprendizaje 97,34 ms; inteligencia de grids 36,09 ms.
- La prueba no reprodujo los 70–95 s de S1. Aunque coincide en cantidad de filas, usa JSON mínimo y no incluye la densidad de referencias de señales, predicciones ni etiquetas/recomendaciones de grids de la BD de S1. El costo de bootstrap y la lectura repetida de aprendizaje siguen siendo sospechosos y se resolverán con precálculo/cache en C38b.


## C38b - Instantánea, cálculo único y respuestas rápidas

- `database/db_manager.py:224-227` agrega una fila persistente para la última instantánea. `grid/intelligence_snapshot.py:18-153` la carga al iniciar, mantiene una sola referencia en memoria y ejecuta precálculos con single-flight. La caché auxiliar tiene TTL de 300 s y máximo de 50 claves.
- `api/main.py:245-263, 301` inicia el precálculo en segundo plano, solicita otro tras el job horario de C35 y libera el servicio al apagar. Inteligencia sirve la instantánea; si aún no existe responde `status=calculando`. `POST /api/intelligence/recalculate` agenda o comparte el cálculo y responde 202.
- `/api/metas/learning` reutiliza el bloque precalculado; `/api/grids/{id}` sirve el resumen de inteligencia desde la instantánea y `/api/direction/shadow/summary` usa caché auxiliar asíncrona. La lista `/api/grids` conserva la consulta de estado y efectivo vivos.
- `frontend/index.html:165` y `frontend/intelligence.js:1-80` agregan "Recalcular ahora", estado de cálculo y antigüedad visible.
- Pruebas enfocadas: `tests/test_intelligence_snapshot.py`, `tests/test_intelligence_api.py`, `tests/test_meta_learning_api.py`, `tests/test_grid_status_api.py`, `tests/test_grids_api.py`, `tests/ui/test_intelligence_ui.py`: **80 passed, 13 warnings, 6,82 s**. La prueba adicional del botón de recálculo también queda incluida. Se usó una carpeta nueva en `$env:TEMP` y `ASPLE_OFFLINE=1`.
- La sintética de 15.000 muestras midió 1.089,07 ms en cálculo directo. Pruebas de snapshot y recálculo lento verifican GET menor de 2 s y una sola tarea simultánea.

## C38c - Límites de memoria

- `grid/intelligence_snapshot.py:15-16, 119-124, 151-153` limita la instantánea a 16 MiB; un resultado mayor conserva la última versión válida. La caché auxiliar admite 50 entradas y vence a los 300 s; las cachés de MetaOperator tienen LRU de 500 entradas (`grid/meta_operator.py:366, 423-441`).
- `tests/test_intelligence_snapshot_memory.py:10-50` mide diez reemplazos con `tracemalloc`: instantánea sintética final 275.113 bytes; memoria retenida 0,95 MiB y pico 2,22 MiB sobre la línea base. También verifica que una instantánea mayor al límite no sustituye a la válida.
- Prueba `tests/test_intelligence_snapshot_memory.py`: **2 passed, 0 warnings, 1,04 s**.

## C38d - Paridad y concurrencia

- `tests/test_intelligence_snapshot.py:101-139` hace dos GET simultáneos, verifica que comparten el precálculo y que el POST de recálculo responde 202 con `shared=true`.
- `tests/test_intelligence_snapshot_concurrency.py:57-76` compara las claves del resumen directo y el servido desde la instantánea; solo se añaden estado/fecha/estado de recarga y `snapshot_total`. La restauración SQLite se prueba en `tests/test_intelligence_snapshot.py:15-57`.
- Prueba `tests/test_intelligence_snapshot_concurrency.py`, `tests/test_intelligence_snapshot.py`, `tests/ui/test_intelligence_ui.py`: **5 passed, 6 warnings, 1,35 s**. `node --check frontend/intelligence.js` correcto.

## C38e - Recomendación parcial y presupuesto por grid

- `grid/grid_intelligence.py:19-29, 94-102, 125-248` establece 45 s por grid y mantiene 60 s total. Un grid que agota el presupuesto recibe una recomendación parcial `mantener`, guardada con `partial=true`; los grids elegibles restantes también reciben fila parcial si el límite total terminó.
- `grid/grid_intelligence.py:337-353, 465-497` usa ventanas de siete días separadas por 14 días, prepara los recortes de velas una vez por grid y registra cantidad de ventanas y segundos por candidato. Velas y curvas usan las cachés LRU existentes (512 entradas en esta clase).
- El resumen incluye id/símbolo incluidos y excluidos, motivos, resultado por grid, ventanas disponibles, candidatos y duración por candidato. `tests/test_grid_intelligence.py:96-191` cubre 180 días y escenarios de 1/3 grids.
- Medición sintética de 180 días: 13 ventanas por candidato, job de un grid en **2,30 s**. Tiempos: mantener 0,44 s; mover rango -1 paso 0,67 s; mover rango +1 paso 0,81 s; bots 0,18/0,08/0,08 s. Cambiar niveles se descartó antes del replay por el planificador.
- Prueba `tests/test_grid_intelligence.py`: **14 passed, 2 warnings, 5,14 s**. Con 1 y 3 grids se guardó una recomendación por grid; con presupuesto vencido, registros parciales.

## Cierre C38

- No se ejecutó suite completa ni se usaron S1, Testnet, red, `.env` o la base real. No se afirma el objetivo menor de 2 s para `/api/grids`, que conserva las lecturas vivas.
- Comando de observación en S1: `journalctl -u asple-predictor -o short-iso | grep asple.slow`.




## C39a: Inventario reescalado y comparación pareada

- `grid/grid_intelligence.py:22,45-76,80-105,458-479` valora el inventario de cada ventana histórica por el camino relativo de sus cierres aplicado al precio de referencia actual. Las etiquetas posteriores conservan precios reales: la llamada de `grid/grid_intelligence.py:725` no pasa referencia ni reescala.
- Cada candidato guarda `expected_gain_7d`, `expected_gain_7d_baseline`, `paired_diff_median` y `paired_diff_p10`; la selección compara diferencias por ventana y exige que la mediana pareada alcance el 0,3 % del capital. `api/routes/intelligence.py:67-119` limita el medidor a `method_version=grid_shadow_42c39_v2` y expone ambas diferencias; las filas anteriores permanecen almacenadas y no se cuentan.
- La prueba confirma que una ventana histórica 2× del precio actual se escala antes de marcar inventario, que geometrías iguales dan diferencia 0 y que una variación común del inventario se cancela al parear. El mecanismo de aplicación continúa detrás del interruptor existente y las recomendaciones persisten con `would_apply=false`.
- Comando exacto: `$env:ASPLE_OFFLINE='1'; $env:PYTHONIOENCODING='utf-8'; $bt=Join-Path $env:TEMP ('asple-c39a-'+[guid]::NewGuid().ToString('N')); New-Item -ItemType Directory -Path $bt | Out-Null; .\venv\Scripts\python.exe -m pytest tests/test_grid_intelligence.py tests/test_intelligence_api.py -q --basetemp $bt`. Resultado: **20 passed, 4 warnings, 5,05 s**.

## C39b: Edad del libro y desfase del reloj

- `grid/meta.py:88-128` devuelve `age_s` también cuando la lectura es inválida y diferencia un libro futuro por reloj de una lectura de profundidad vieja. `grid/meta.py:573-579,590-600` toma de nuevo el reloj después de obtener el libro y registra edad/motivo por símbolo. El límite efectivo de antigüedad sigue en 60 s.
- La revisión del uso de `executable_value` confirma que el cálculo central de `MetaCore` es quien lo invoca para valorar el inventario. La revisión de operador utiliza su propia lectura/validación de profundidad y conserva su reintento, desfase y motivos C17.
- Pruebas en `tests/test_meta_core.py`: lectura simulada que tarda 5 s queda `KNOWN`; lectura de 61 s queda `DESCONOCIDO`, informa `age_s=61` y mantiene bloqueadas piezas nuevas y reinicia el contador de pérdida. Una edad futura negativa tiene motivo explícito. Comando exacto: `$env:ASPLE_OFFLINE='1'; $env:PYTHONIOENCODING='utf-8'; $bt=Join-Path $env:TEMP ('asple-c39b-'+[guid]::NewGuid().ToString('N')); New-Item -ItemType Directory -Path $bt | Out-Null; .\venv\Scripts\python.exe -m pytest tests/test_meta_core.py -q --basetemp $bt`. Resultado: **40 passed, 5,09 s**.
- No se usaron S1, Testnet, red, `.env` ni la base real. No se ejecutó la suite completa.
