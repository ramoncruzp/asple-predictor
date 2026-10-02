# Fase 15E — Volatilidad pronosticada → estructura del grid

Estudio + función pura, **sin cablear** `suggest_structure` ni el pronóstico real en
`grid.engine`/`grid.policy`/`grid.monitor`. Todo lo descrito aquí vive en archivos
nuevos; ningún archivo prohibido fue modificado (ver sección "Scope y
discrepancias").

> **Fase 15E-2 (auditoría)**: las secciones 0, 4 y 5 fueron revisadas y
> ampliadas tras una auditoría que señaló que la comparación B−A original no
> estaba separada de "cualquier otra estructura fija distinta de A" — A (n=10,
> ancho 9%) es un punto arbitrario, no una referencia óptima. Se agregaron
> cuatro controles nuevos (sección 4b) que **degradan la conclusión original**.
> Las secciones 1–3 y 6 no cambiaron de contenido sustantivo.

## 0. Criterio (declarado antes de ejecutar el estudio con datos reales)

Fijado durante la implementación, antes de correr `vol_structure_study.py` sobre
el dataset completo:

- **Modelos**: Nexo-HAR (campeón H=24 en vivo), EWMA, Persistencia. GBM se omite
  por tiempo (ver discrepancias) — permitido explícitamente por el plan.
- **Embargo causal**: `train < window.start - 24h`. Mínimo de historia causal para
  que una ventana sea elegible: **90 días** antes del embargo (deja fuera las
  primeras ~13 de 100 ventanas rolling de 30 días/paso 7 días, por falta de
  historia al inicio del dataset).
- **Calibración**: igual que `scripts/train_vol_models.py` — 85% train / 15%
  calibración (offset por horizonte), `var_factor` causal por ventana.
- **Error peligroso**: subestimar sigma realizada en >20%
  (`sigma_pred < 0.8·sigma_real`), no el error simétrico. Un grid dimensionado con
  sigma subestimada es demasiado angosto y se sale de rango.
- **Regla de robustez (S6, reutilizada de 4b-4)**: media>0, IC99% bootstrap por
  bloques (block=5 ventanas) con cota inferior>0, primera mitad>0, segunda
  mitad>0, mediana>0. Se aplica tanto a cada variante (A/B/C) como a las
  comparaciones pareadas (B−A, C−A, C−B).
- **Pregunta central**: ¿el pronóstico causal real (C, Nexo-HAR) mejora la
  estructura del grid sobre lo que el simulador ya hace hoy (B, sustituto EWMA),
  no solo sobre una referencia fija ingenua (A)?

**Criterio agregado en la auditoría 15E-2** (declarado antes de ejecutar los
controles, mismo seed=42, mismas 87 ventanas elegibles):

- **Control de tamaño (A')**: las 25 estructuras fijas n∈{5,8,10,15,20} ×
  ancho∈{4,6,9,12,16}% de 4b-4, filtradas con `grid.sim.sweep.structure_catalog`
  (sin modificar) a las factibles con 100 USDT en cada ventana. Se compara B
  contra la MEJOR de las 25 elegida ex-post (cota optimista/con sesgo de
  selección) y contra la MEDIANA de las 25 por ventana.
- **Corrección por comparaciones múltiples**: para la comparación contra la
  mejor ex-post, bootstrap por bloques del **máximo** — en cada remuestreo se
  vuelve a elegir cuál de las 25 estructuras es la mejor dentro de ese
  remuestreo, antes de restarla de B. Esto mete el ruido de la selección dentro
  del intervalo, en vez de fijar de antemano "la" estructura ganadora.
- **Control de ancho (B')**: misma `suggest_structure`, pero con sigma
  constante = mediana causal de la EWMA continua hasta el inicio de cada
  ventana (sin información del momento). Si B ≈ B', la ganancia de B viene del
  tamaño medio elegido, no de seguir la volatilidad en el tiempo.
- **Sensibilidad de bloque**: recomputar el IC99% de B−A con bloque=5 y
  bloque=9 ventanas (las ventanas de 30 d/paso 7 d se solapan ~4 pasos).

## 1. Serie de sigma causal (`grid/sim/vol_series.py`)

- `"ewma"`: idéntica al sustituto actual — `ewma_sigma_24h` sobre los cierres de
  5 min **de la propia ventana** (reinicia en cada ventana, igual que
  `run_simulation` cuando `sigma_values=None`). `grid/sim/vol_series.py:164-172`.
- `"persistence"` / `"nexo_har"`: `models.volatility.{PersistenceModel,
  NexoHARModel}` sobre velas horarias (`data/cache/xrp_1h.csv` +
  features intradía de `data/cache/xrp_5m.csv` vía
  `aggregate_intraday_to_hourly`), reentrenado por ventana con
  `causal_train_predict` (`grid/sim/vol_series.py:123-156`): fit solo con filas
  `close_time < window.start - 24h`, calibración en el 15% final de esa misma
  porción (nunca toca la ventana de prueba), predicción vectorizada sobre todo el
  frame (seguro porque `build_volatility_frame` solo mira hacia atrás) y luego se
  recorta a la ventana.
- Difusión a 5 minutos: cada vela de 5 min usa el último pronóstico horario cuyo
  `close_time` ya cerró (`grid/sim/vol_series.py:195-209`), igual cadencia que
  `VolLoop` en producción.
- Caché por (modelo, ventana) en `data/cache/sim/vol_series/*.json` + el frame
  horario completo en `frame_<hash>_24.pkl`, ambos con hash del CSV
  (`grid/sim/vol_series.py:76-91,183-220`).

**Causalidad verificada por mutación** (`tests/test_vol_series_causal.py`):
mutar precios **después** del punto de corte deja sin cambios todos los valores
de sigma anteriores a ese punto, byte a byte (`assert_allclose(..., rtol=0,
atol=0)`), para `persistence` y `nexo_har`. Mutar la historia de entrenamiento
genuina (antes del embargo) sí cambia `var_factor` — confirma que el modelo
realmente usa esos datos y no un artefacto vacío. 8/8 tests pasaron.

## 2. Calidad del pronóstico (`grid/sim/vol_quality.py`)

87 ventanas elegibles, pool de 62 640 filas horarias con objetivo válido
(`target_logvol` a 24h, `build_volatility_frame`). EWMA aquí es una serie
**continua** (no reiniciada por ventana) para no penalizarla por un artefacto del
arnés de simulación — ver nota en el módulo.

| Modelo | R² (log-vol) | QLIKE | bias_log | var_ratio | % subestima >20% |
|---|---:|---:|---:|---:|---:|
| EWMA (continua) | 0.298 | 0.566 | 0.121 | 1.001 | 14.47% |
| Persistencia | 0.293 | 0.689 | −0.009 | 0.990 | 25.27% |
| **Nexo-HAR** | **0.432** | **0.522** | 0.100 | 1.025 | 14.85% |

Comparación pareada de pérdida QLIKE por fila (bootstrap por bloques, 2000
muestras, bloque=168h):

| Comparación | Δ media (ref−cand) | IC95% | ¿gana el candidato? |
|---|---:|---:|---:|
| Nexo-HAR vs EWMA | +0.0442 | [0.0201, 0.0694] | **Sí** (IC95% > 0) |
| Nexo-HAR vs Persistencia | +0.1667 | [0.1326, 0.2007] | **Sí** (IC95% > 0) |

**Lectura honesta**: Nexo-HAR es el mejor pronóstico por R²/QLIKE, con margen
estadísticamente robusto sobre ambos controles. Pero en la métrica que más
importa para dimensionar el grid — la fracción de subestimaciones peligrosas
(>20%) — **Nexo-HAR (14.85%) no mejora sobre la EWMA continua (14.47%)**; de
hecho es marginalmente peor. Solo bate claramente a la persistencia en ese
frente (25.27%). Un mejor R² global no se traduce automáticamente en menos
subestimaciones grandes.

## 3. `suggest_structure` (`grid/structure.py`)

Función pura, sin I/O, Decimal en precios/capital/celda:
`range = mid·exp(∓k_width·sigma_h)` con `sigma_h = sigma_24h·sqrt(horizon_h/24)`
(misma convención que `grid.policy.adjust_decision`); `k_width=2.0` por defecto
(~banda 2σ). `n_levels` parte de `floor(ancho/min_spacing_pct)` y baja hasta que
el espaciado por nivel sea ≥ `max(min_spacing_pct, 2·fee_pct + polvo_pct)`, donde
`polvo_pct = step_size·mid / cell_usdt·100` (costo esperado de redondeo a
`step_size` como % de la celda). `feasible=False` con motivo explícito si ni
`n_levels=4` cabe (celda bajo `min_notional` o espaciado imposible).
12/12 tests (`tests/test_grid_structure.py`): límites, infeasible (sigma
ausente/no-positivo, capital insuficiente), polvo restado del `net_edge`,
determinismo, no mutación de `filters`, centrado logarítmico en `mid`, escalado
por `horizon_h`/`k_width`, validación de argumentos.

## 4. Estudio de estructura (`grid/sim/structure_study.py`,
`scripts/vol_structure_study.py`)

87 ventanas elegibles (mismo filtro de 90 días de historia causal), `simple` y
`smart`, capital=100, fee=0.1%, resync=3 velas. **Nota de diseño**: la EWMA
reiniciada por ventana vale exactamente 0 en la primera vela (su varianza
recursiva arranca en 0) — un artefacto degenerado para dimensionar la estructura
inicial, no una lectura realista. Para B se usa en su lugar la EWMA **continua**
(toda la historia de 5 min, sin reinicio) evaluada en el candle de inicio de cada
ventana — lo que un despliegue real ya en marcha mostraría. Las decisiones de
ADJUST en vivo de A y B siguen usando el sustituto EWMA por defecto de
`run_simulation` (sin cambios); solo C alimenta el pronóstico Nexo-HAR también al
ADJUST, para representar "cablear el pronóstico real de punta a punta".

- **A**: estructura fija n=10, ancho=9%. `Resultados_Estructura_Regimen_XRP.md`
  vive en el Project de Claude, no en el repo (corrección de la discrepancia 1
  original, que decía erróneamente que el archivo "no existe" sin más
  contexto); de todos modos el mejor punto de 4b-4 fue **"sin estructura
  robusta (0/38)"** — ninguna de las 38 combinaciones evaluadas en esa fase pasó
  la regla S6 de forma consistente. Es decir, **A nunca fue una referencia
  óptima**, ni siquiera según la fase que la originó. Se usó el valor de
  respaldo indicado en el plan (n=10, ancho=9%) precisamente porque no hay un
  "mejor" punto fijo que leer.
- **B**: `suggest_structure` con sigma de la EWMA continua.
- **C**: `suggest_structure` con sigma de Nexo-HAR.
- **B'** (control de ancho, 15E-2): `suggest_structure` con sigma CONSTANTE =
  mediana causal de la EWMA continua hasta el inicio de la ventana.

Factibilidad: 100% en las cuatro variantes (87/87 ventanas cada una).

### Qué estructura eligió `suggest_structure` en realidad (tarea 1, 15E-2)

B y C casi nunca coinciden con A: **en las 87/87 ventanas**, tanto B como C
eligieron `n_levels` y/o ancho distintos de (10, 9%) (umbral: diferencia de
ancho > 0.5pp o `n_levels` distinto).

| Variante | ancho % (min / mediana / máx) | n_levels (min / mediana / máx) | spacing % (min / mediana / máx) | cell USDT (min / mediana / máx) | net_edge %/ciclo (min / mediana / máx) |
|---|---|---|---|---|---|
| B (EWMA continua) | 6.3 / **14.0** / 58.3 | 5 / **7** / 14 | 0.99 / 1.95 / 4.17 | 7.1 / 14.3 / 20.0 | 0.010 / 0.207 / 0.635 |
| C (Nexo-HAR) | 4.4 / **11.0** / 43.1 | 4 / **6** / 15 | 0.89 / 1.81 / 3.59 | 6.7 / 16.7 / 25.0 | 0.007 / 0.199 / 0.652 |
| B' (sigma constante) | 13.9 / **16.6** / 23.6 | 7 / **8** / 10 | 1.40 / 2.29 / 2.86 | 10.0 / 12.5 / 14.3 | 0.005 / 0.238 / 0.569 |

B y C eligen, en mediana, un ancho **más de 1.5× más ancho** que A (9%) y
**menos niveles** (7 y 6 contra 10). Esto es clave para interpretar la sección
4b: la mejora de B sobre A puede deberse simplemente a que B aterriza en una
región del espacio (n,ancho) que resulta ser mejor en este período, no a que
B "siga" la volatilidad correctamente.

### Resultados por variante (PnL % sobre capital, regla S6)

| Variante | Estrategia | media | mediana | IC99% bloques | ¿robusta? |
|---|---|---:|---:|---:|---:|
| A (fija) | simple | −0.78 | 1.36 | [−3.76, 1.92] | No |
| A (fija) | smart | −1.37 | −1.21 | [−2.46, −0.38] | No |
| B (EWMA) | simple | 0.01 | 1.73 | [−2.88, 2.57] | No |
| B (EWMA) | smart | −1.58 | −1.63 | [−3.08, 0.02] | No |
| C (Nexo-HAR) | simple | −0.22 | 1.57 | [−3.35, 2.50] | No |
| C (Nexo-HAR) | smart | −1.31 | −1.00 | [−2.97, 0.03] | No |

Ninguna de las seis combinaciones es rentable de forma robusta por sí sola en
este rango de datos (2024-09-28 a 2026-09-28) — consistente con que el PnL
absoluto de grid trading es sensible al régimen de mercado del período, no algo
que esta fase intente arreglar.

### Comparación pareada (la pregunta real de la fase)

| Comparación | Estrategia | Δ media (pp) | IC99% bloques | win% | ¿mejora robusta? |
|---|---|---:|---:|---:|---:|
| B − A | simple | **+0.78** | **[0.02, 1.63]** | 66.7% | **Sí** |
| B − A | smart | −0.21 | [−1.28, 0.91] | 40.2% | No |
| C − A | simple | +0.55 | [−0.06, 1.20] | 65.5% | No (roza el límite) |
| C − A | smart | +0.06 | [−1.13, 1.20] | 50.6% | No |
| **C − B** | simple | −0.23 | [−0.69, 0.20] | 50.6% | No |
| **C − B** | smart | +0.27 | [−0.72, 1.29] | 57.5% | No |

## 4b. Controles de la auditoría 15E-2

### Sensibilidad de bloque (tarea 4): ¿sobrevive B−A a bloque=5 vs bloque=9?

| Comparación | Estrategia | Bloque | IC99% | ¿robusta? |
|---|---|---:|---:|---:|
| B − A | simple | 5 | [0.019, 1.626] | Sí |
| B − A | simple | 9 | [0.048, 1.676] | Sí |
| B − A | smart | 5 | [−1.285, 0.914] | No |
| B − A | smart | 9 | [−1.306, 0.802] | No |

La conclusión **no cambia** con el tamaño de bloque: B−A sigue siendo robusta en
`simple` y sigue sin serlo en `smart`, con los dos bloques probados.

### Control de ancho (tarea 3): B vs B' (sigma constante)

| Comparación | Estrategia | Δ media (pp) | IC99% | ¿mejora robusta? |
|---|---|---:|---:|---:|
| B − B' | simple | −0.47 | [−1.21, 0.10] | No |
| B − B' | smart | −0.17 | [−0.96, 0.57] | No |

**B y B' son estadísticamente indistinguibles** (ambos IC99% cruzan cero, y el
signo de la diferencia es incluso levemente negativo para B). Esto confirma la
hipótesis de la auditoría: **la ganancia de B sobre A no viene de seguir la
volatilidad momento a momento** — una sigma constante (la mediana histórica
causal) produce resultados equivalentes. Lo que importa es el tamaño medio que
`suggest_structure` elige (ancho ~14-17%, n~7-8), no la variación en el tiempo.

### Control de tamaño (tarea 2): B vs las 25 estructuras fijas de 4b-4

19 de las 25 combinaciones n×ancho son factibles con 100 USDT en las 87
ventanas elegibles (se descartan 6: `n=20` con cualquier ancho excepto ninguno
pasa — celda bajo `min_notional`/`step_size` —, y `n=15, ancho=4%` por
espaciado bajo `GRID_MIN_STEP_PCT`; ver `fixed_grid_control.rejected_structures`
en la salida del script para el detalle exacto). **A (n=10, ancho=9%) termina
en el puesto 11/19 por PnL medio en `simple` y 14/19 en `smart`** — es decir,
por debajo de la mediana en ambas estrategias, consistente con que 4b-4 no
encontró una estructura robusta.

| Comparación | Estrategia | Δ media (pp) | IC99% (sin corregir) | IC99% (corregido por selección) | ¿mejora robusta? |
|---|---|---:|---:|---:|---:|
| B − mejor fija ex-post | simple | −0.79 | [−1.63, −0.05] | [−1.63, −0.05] | **No, B pierde** |
| B − mejor fija ex-post | smart | −0.93 | [−2.19, 0.44] | [−2.19, −0.09] | **No, B pierde** (tras corregir) |
| B − mediana de las 25 fijas | simple | +0.72 | [−0.06, 1.45] | — | No |
| B − mediana de las 25 fijas | smart | −0.38 | [−1.19, 0.54] | — | No |

La mejor estructura fija ex-post para `simple` es **n=5, ancho=16%** (PnL medio
+0.79%); para `smart` es **n=15, ancho=6%** (PnL medio −0.65%, la "menos mala").
**B no supera de forma robusta a ninguna de las dos**, y de hecho **pierde de
forma robusta contra la mejor fija ex-post** en ambas estrategias una vez
corregido el sesgo de haberla elegido entre 25 candidatas (IC99% enteramente
negativo). Contra la mediana de las 25 (un benchmark más conservador, sin sesgo
de selección), B tampoco gana ni pierde de forma robusta.

## 5. Conclusión honesta (revisada en 15E-2)

1. **La afirmación "dimensionar por volatilidad ayuda" no sobrevive a los
   controles y debe descartarse en esta forma.** B sí bate a A de forma robusta
   en `simple` (IC99% > 0, sobrevive a bloque=5 y bloque=9), pero:
   - B es estadísticamente indistinguible de **B'** (sigma constante, sin
     información del momento) — la ganancia no viene de seguir la volatilidad.
   - B **no** bate de forma robusta a la **mediana** de 25 estructuras fijas
     alternativas, y **pierde de forma robusta** contra la **mejor** de esas 25
     elegida ex-post, incluso corrigiendo por el sesgo de haberla elegido entre
     25 candidatas.
   - A (n=10, ancho=9%) resulta estar por debajo de la mediana del catálogo de
     25 estructuras fijas en ambas estrategias — consistente con que 4b-4 nunca
     encontró una estructura robusta (0/38). B parece mejor que A simplemente
     porque **A era un punto mediocre**, no porque B esté haciendo algo
     especial con la volatilidad.
2. **El pronóstico causal real (Nexo-HAR) NO demuestra una mejora robusta
   adicional sobre lo que el simulador ya hace hoy (B)**. La comparación directa
   C−B es la pregunta central de esta fase y el resultado es: deltas pequeños,
   de signo inconsistente entre `simple` (−0.23pp) y `smart` (+0.27pp), ambos con
   IC99% que cruzan cero. Esta conclusión **no cambia** con los controles de
   15E-2 (C y B comparten el mismo problema de fondo frente a los benchmarks
   fijos).
3. En calidad de pronóstico aislada, Nexo-HAR sí es mejor (R², QLIKE, con
   evidencia robusta) — pero **no reduce la tasa de subestimación de vol >20%**,
   que es el error que de verdad perjudica el dimensionamiento del grid.
4. **Respuesta a la pregunta de la fase, con el nivel de confianza que los
   datos realmente sostienen**: no hay evidencia, en este backtest, de que
   cablear el pronóstico Nexo-HAR (C) o incluso el sustituto EWMA (B) en el
   motor de grid produzca una ventaja robusta sobre una selección razonable de
   estructuras fijas. El resultado positivo original (B robustamente mejor que
   A) era real pero **engañoso como evidencia a favor de "usar volatilidad"**:
   reflejaba sobre todo que A era una referencia débil, no que B esté
   capturando información útil del mercado. No se recomienda cablear
   `suggest_structure` ni el pronóstico de volatilidad en el motor de grid con
   la evidencia actual; sería necesario, como mínimo, comparar sistemáticamente
   contra el conjunto completo de estructuras fijas razonables (como aquí) antes
   de considerar cualquier cambio en producción.

## 6. Suite offline y tiempos

- `python -m pytest tests -p no:cacheprovider -q -m "not live"` (con
  `ASPLE_OFFLINE=1`), corrida final de esta auditoría: **513 passed, 0 failed,
  18 deselected** (39.4s). Las 4 fallas preexistentes reportadas en la entrega
  original de la Fase 15E (en `grid/policy.py`/fidelidad del simulador, código
  de la Fase 15D en paralelo) ya no se reproducen — ver discrepancias.
- Tests nuevos de esta fase, aislados: `tests/test_grid_structure.py` **18/18**
  (12 originales + 6 nuevos de 15E-2 para `_max_fixed_bootstrap` y
  `_structure_distribution`) y `tests/test_vol_series_causal.py` 8/8 —
  **26/26 pasando**.
- `build_full_frame` (frame causal horario completo, 17 519 filas, cacheado):
  ~7s la primera vez, instantáneo con caché.
- `evaluate_quality` (87 ventanas × 3 modelos, sin caché de
  `causal_train_predict` reutilizada entre llamadas): ~49s.
- `run_structure_study` **con los controles de 15E-2** (87 ventanas × 4
  variantes [A,B,C,B'] × 2 estrategias, más el control de 19 estructuras fijas
  × 2 estrategias × 87 ventanas = 522 + 3 306 ≈ 3 828 simulaciones,
  `workers=8`): **~120s** de extremo a extremo (incluye construir el catálogo
  de estructuras factibles y los dos bootstraps de máximo, 2000 muestras cada
  uno).

## 7. Scope y discrepancias

Ningún archivo prohibido fue modificado, ni en la entrega original ni en esta
auditoría 15E-2 (que además restringe el scope a solo 6 de los 9 archivos
originales: `grid/sim/structure_study.py`, `scripts/vol_structure_study.py`,
`grid/sim/vol_quality.py` [sin cambios de contenido, solo queda en el scope
permitido], `tests/test_grid_structure.py`, `REPORTE_FASE15E.md`,
`DISCREPANCIAS_FASE15E.md`). `grid/structure.py`, `grid/sim/vol_series.py` y
`tests/test_vol_series_causal.py` no se tocaron en esta auditoría. Los archivos
prohibidos (`grid/engine.py`, `grid/monitor.py`, `grid/policy.py`,
`grid/sim/runner.py`, `grid/sim/exchange.py`, `grid/sim/target_study.py`,
`scripts/grid_ctl.py`, la capa DB de grids) siguen apareciendo modificados en el
working tree — la Fase 15D sigue en curso en paralelo — pero ya no causan
fallas de test (ver `DISCREPANCIAS_FASE15E.md`).
