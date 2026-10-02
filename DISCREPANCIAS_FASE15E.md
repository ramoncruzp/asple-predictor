# Discrepancias — Fase 15E

## 1. Archivo de referencia `Resultados_Estructura_Regimen_XRP.md` — corregido en 15E-2

**Versión original (incorrecta)**: se afirmó que el archivo "no existe" en el
repo, sin más contexto, tratándolo como un archivo simplemente perdido.

**Corrección (auditoría 15E-2)**: el archivo vive en el Project de Claude, no
en este repositorio — no es que no exista, es que vive fuera del repo y esta
sesión no tiene acceso a Projects. Más importante: según la auditoría, el mejor
punto de la fase 4b-4 que ese documento resume fue **"sin estructura robusta
(0/38)"** — ninguna de las 38 combinaciones evaluadas en 4b-4 pasó la regla S6
de forma consistente. Es decir, incluso si se hubiera podido leer el archivo,
**no había ningún punto "óptimo" que extraer de él para usar como A** — 4b-4
ya había concluido que ningún n×ancho fijo es robusto. Se mantuvo el respaldo
indicado en el plan original (n=10, ancho=9%) por ser el valor explícitamente
autorizado, pero el reporte (secciones 0, 4 y 5) ahora dice explícitamente que
A es un punto arbitrario sin pretensión de optimalidad, y la sección 4b agrega
un control contra las 25 estructuras fijas de 4b-4 para no depender de A como
única referencia.

## 2. GBM omitido como modelo de volatilidad en el estudio

El plan permitía omitir GBM "si no cabe en tiempo, decláralo". Se omitió:
reentrenar un modelo de árboles por cada una de 87 ventanas (×2 para calidad y
estructura) habría multiplicado varias veces el tiempo de cómputo, y el plan ya
autorizaba limitarse a Nexo-HAR + EWMA + persistencia. Nexo-HAR es el campeón
declarado para H=24h en `config.models_config.VOL_CHAMPIONS`, que es el horizonte
relevante para esta fase.

## 3. Definición de "EWMA" usada de forma distinta en vol_quality.py vs
   vol_series.py/structure_study.py

- En `grid/sim/vol_series.py` (usado por `structure_study.py` para las
  decisiones ADJUST en vivo de la variante B), `"ewma"` reinicia en cada
  ventana — fiel a lo que `run_simulation` ya hace hoy cuando no se le pasa
  `sigma_values`.
- En `grid/sim/vol_quality.py` y para el **dimensionamiento inicial** de B en
  `structure_study.py`, se usa una EWMA **continua** (sin reiniciar),
  calculada una sola vez sobre todo el historial de 5 minutos.

Esto no estaba especificado línea por línea en el plan; se decidió porque la
EWMA reiniciada vale exactamente 0 en la primera vela de cada ventana (su
varianza recursiva arranca en 0), lo que la vuelve inútil como lectura de
"volatilidad actual" para dimensionar una estructura al inicio, pero es
precisamente lo que ya hace el simulador para las decisiones de ADJUST
subsiguientes. Mantener ambos usos (reiniciada para ADJUST, continua para el
dimensionamiento inicial) refleja mejor lo que un despliegue real haría: al
arrancar un grid, el sistema de volatilidad ya lleva corriendo un tiempo.
Documentado en el módulo y en el reporte (sección 4).

## 4. `vol_quality.evaluate_quality` no reutiliza el caché JSON por ventana de
   `vol_series.hourly_forecast_for_window`

`evaluate_quality` llama `causal_train_predict` directamente (sin pasar por el
caché de `vol_series.py`) porque necesita todas las filas horarias con target
válido dentro de la ventana, no solo la porción recortada para difundir a 5
minutos que guarda el caché de `vol_series`. Se midió el costo: ~49s para las 87
ventanas × 3 modelos, aceptable sin caché adicional. No se implementó un segundo
esquema de caché para no multiplicar la complejidad sin necesidad real de
rendimiento.

## 5. 4 fallas de test preexistentes reportadas originalmente — ya no se reproducen

La entrega original de la Fase 15E reportó:

```
FAILED tests/test_grid_close.py::test_liquidate_dust_is_reported_and_retains_owned_quantity
FAILED tests/test_grid_policy.py::test_defaults_and_parameter_validation_rules
FAILED tests/test_grid_sim_smart_fidelity.py::test_smart_close_values_repository_inventory_at_market
FAILED tests/test_grid_sim_smart_fidelity.py::test_pause_max_duration_closes_inventory_to_repository
4 failed, 503 passed, 18 deselected in 43.12s
```

causadas por la Fase 15D modificando en paralelo `grid/engine.py`,
`grid/monitor.py`, `grid/policy.py`, `grid/sim/runner.py`,
`grid/sim/exchange.py`, `grid/sim/target_study.py`, `scripts/grid_ctl.py` y
`database/db_manager.py` (confirmado entonces con `git status`: exactamente la
lista de archivos prohibidos para esta fase, consistente con "Corre EN PARALELO
con la 15D").

**Al correr la suite de nuevo para esta auditoría 15E-2**, esos mismos archivos
siguen apareciendo modificados en el working tree (la Fase 15D sigue en
curso), pero las 4 fallas **ya no se reproducen**:

```
513 passed, 18 deselected, 7 warnings in 39.39s
```

Esto es consistente con que 15D avanzó su propio trabajo hacia un estado que
pasa sus tests; no se investigó más a fondo porque esos archivos siguen fuera
del scope permitido para esta fase. Ningún archivo de esta fase (ni los 9
originales ni los 6 tocados en 15E-2) depende de ese código de forma que
pudiera explicar el cambio: se verificó aislando
`tests/test_grid_structure.py` (18/18, incluye los 6 tests nuevos de 15E-2) y
`tests/test_vol_series_causal.py` (8/8), ambos en verde, y las corridas
completas de `run_structure_study` con los cuatro controles nuevos sobre datos
reales produjeron resultados numéricamente sensatos y reproducibles (ver
`REPORTE_FASE15E.md`).

## 6. Corrección por comparaciones múltiples: se implementó, no se degradó la conclusión por omisión

El plan de 15E-2 permitía "declarar que no se corrige y degradar la conclusión"
como salida válida si corregir resultaba demasiado costoso. Se optó por
implementar la corrección (bootstrap por bloques del máximo,
`_max_fixed_bootstrap` en `grid/sim/structure_study.py`): en cada remuestreo se
vuelve a seleccionar cuál de las 25 estructuras fijas es la mejor dentro de ese
remuestreo, antes de compararla con B, en vez de fijar de antemano la
estructura ganadora de la muestra completa. Esto es más conservador (el
intervalo de confianza resultante es más ancho) que comparar contra una única
estructura pre-elegida, y es lo que efectivamente determinó la conclusión
degradada de la sección 5: incluso con esta corrección, B pierde de forma
robusta contra la mejor estructura fija ex-post en ambas estrategias.

## 7. `grid/sim/vol_quality.py` apareció en el scope permitido de 15E-2 sin cambios de contenido

El plan de 15E-2 lista `grid/sim/vol_quality.py` entre los archivos
modificables, pero ninguna de las cinco tareas pedidas requería cambiarlo (son
todas sobre `structure_study.py`: distribución de estructuras elegidas,
control de 25 estructuras fijas, control de sigma constante, sensibilidad de
bloque). No se le hizo ningún cambio; se deja esta nota para que quede
explícito que la ausencia de diff en ese archivo es intencional, no un
olvido.

## 8. Caché en disco creada durante el desarrollo

`grid/sim/vol_series.py` escribió artefactos de caché bajo
`data/cache/sim/vol_series/` (el frame horario causal completo y los
pronósticos por ventana) durante las pruebas y corridas de este informe.
`data/cache/` está en `.gitignore`; no aparecen como archivos nuevos en
`git status`. Se dejan en disco porque son exactamente el mecanismo de caché
pedido por el plan (punto 1), no un efecto colateral a limpiar.
