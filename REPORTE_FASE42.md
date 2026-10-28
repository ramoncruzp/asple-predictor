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
