# Modo Meta — Decisiones y rumbo

**Para qué es este archivo:** tener en un solo lugar qué estamos construyendo, qué se decidió y por qué, para no perder el camino. Si algo de otro documento o prompt choca con esto, manda este archivo (y la especificación `ESPEC_FASE40_MODO_META.md`).
**Última actualización:** 2026-10-27.

---

## 1. La idea (lo que Ramón quiere, desde el principio)

> "Te doy 100, quiero 110, usa Rápido." Eso es lo único que Ramón dice.

- La app hace todo lo demás, sola y de forma continua: revisa el mercado, decide con qué moneda(s), qué piezas abre (grid Smart, grid Simple o bots individuales de compra X / venta Y), cuántas, con cuánto capital, piso, techo y margen.
- Va monitoreando. Si ve que conviene cerrar un grid (aunque sea con una pérdida aceptable) para mover el capital a otra moneda que llega antes a la meta, lo hace.
- Usa **toda la capa de inteligencia** (modelos de volatilidad, consenso, dirección, Scanner, Advisor, estadísticas e historial).
- Guarda récord de cada decisión y, con el tiempo, **aprende** qué señales sirven y cuáles no (Fase 40d).
- Objetivo: **llegar a la meta lo más rápido posible.**

### Visión a largo plazo (Ramón, 2026-10-25)

> Llegar a una app que **vaya aprendiendo a leer el mercado para tomar decisiones**: que revise el mercado cada 15 minutos, simule cada decisión contra lo que el mercado hizo después y aprenda de cada una.

- Cada revisión del operador deja alternativas evaluadas con sus señales; días después se comparan contra las velas reales (qué habría pasado) y eso alimenta el aprendizaje (Fase 40d).
- El aprendizaje se mide a sí mismo: decisión real vs. decisión con pesos aprendidos vs. solo Scanner + Advisor. Si no mejora, se ve.
- Usar los pesos aprendidos lo decide Ramón con la evidencia a la vista; el sistema no se activa solo.

- **Después de 40d (decidido 2026-10-25): revisión completa de la capa de inteligencia** como un mini sistema de aprendizaje, de punta a punta: cómo recopila los datos, cómo los estudia, qué modelos estadísticos usa, cómo mide la volatilidad y la dirección, cómo almacena los datos, cómo aprende automáticamente de las decisiones, cómo corrige sus decisiones y cómo toma la decisión final.

## 2. Perfiles: solo cambian el riesgo, no el objetivo

- Los dos perfiles buscan lo mismo: llegar lo más rápido posible.
- El perfil solo fija **límites de riesgo**. La app decide todo dentro de esos límites, con el **mismo criterio** en ambos.
- No se impone Smart ni Simple por perfil. La app escoge la pieza que estima más rápida.

| Límite | Prudente | Rápido |
|---|---|---|
| Monedas admitidas | Volatilidad media, con estadísticas de modelo completas | También las más volátiles |
| Capital máximo por moneda | 40 % | 70 % |
| Piezas simultáneas | Hasta 2 grids o 4 bots | Hasta 4 grids u 8 bots |
| Margen mínimo por venta (la app decide por encima) | 0,7 % | 0,30 % |
| **Pérdida máxima total** | **−8 %** | **−18 %** |
| Aviso previo (75 % del límite) | −6 % | −13,5 % |
| Plazo máximo | 45 días | 20 días |
| Préstamos entre bots | Tope 30 % | Tope 50 % |

Los números son punto de partida y se ajustan con los resultados.

## 3. Reglas del dinero

- **Valor de la meta** = efectivo atribuible + monedas en mano × precio **bid** − comisión estimada de venta (0,1 %).
- **Efectivo atribuible** = capital inicial − USDT entregado a piezas + USDT devuelto por piezas − comisiones.
- **Pérdida** = (valor actual − capital inicial) / capital inicial. Incluye lo realizado y lo no realizado.
- Si el libro no tiene compradores, el valor es **DESCONOCIDO** (nunca cero): no abre piezas y avisa por Telegram (se repite cada 30 min).
- Vigilancia de pérdida **cada 1 minuto**. Revisión del plan (operador) **cada 15 minutos**.
- Al 75 % del límite: aviso y no abre ni mueve capital. Al límite: cierra todo. Plazo vencido: cierra todo.
- La meta se puede poner en USDT o en %.

## 4. Meta cumplida y cierres

- **Meta cumplida** cuando el USDT realizado llega al objetivo (ej. 110). Se desactiva: cancela compras y no recompra.
- **Bots sobrantes** (con venta viva): venden solo con ganancia, sin plazo y fuera del límite de pérdida. Lo que vendan es ganancia extra, reportada aparte. El monitor nunca los reactiva.
- **Botón Cancelar operación:** resumen + confirmación, con dos opciones: **Vender todo** (cierre verificado) o **Mover al repositorio**.
- **Repositorio** = el que ya existe (grids especiales donde los bots solo venden y al vender se desactivan). No se crea uno nuevo; los bots de una meta quedan marcados con origen META.
- **Una sola meta activa a la vez** (ajustable más adelante).

## 5. El operador

- Abre, retira (cancela compras, deja ventas) o **cierra cualquier pieza, con ganancia o con pérdida**, y mueve el capital, cuando la estimación dice que así llega antes a la meta.
- "Pérdida aceptable" = la ganancia esperada de la nueva asignación supera la pérdida realizada más comisiones.
- **Decisión de Ramón (2026-10-25):** el operador activo puede reajustar piso, techo, número de niveles/bots, margen y capital asignado de sus piezas cuando las simulaciones lo justifiquen. En bots individuales, solo mueve compra/venta antes de comprar; tras comprar conserva la venta viva. Se exige mejora histórica de al menos 5 puntos porcentuales, o empate dentro de ±2 puntos y mejora del avance mediano de al menos 10 %. Evalúa al quedar a un nivel del borde o tras dos revisiones fuera con desplazamiento superior a un nivel. Enfría cada pieza 1 h y limita a cuatro cambios por meta en 24 h. No actúa bajo bloqueo 75 %/DESCONOCIDO. Ejecución bajo `ops_lock` tras revalidación; no vende inventario con pérdida.
- **Monitor para piezas Meta:** mantiene PAUSE/RESUME por política Smart, stop-loss por celda, cierres de protección/40a y gestión de préstamos. Omite ADJUST, re-espaciado e idle-shrink autónomos porque el operador Meta decide los cambios de geometría.
- Bot individual = grid de un solo nivel; al vender queda DONE y no recompra.
- Antes de arrancar presenta estadísticas y el plan con el que propone comenzar; Ramón pulsa **Iniciar**.
- Todo queda en la bitácora, incluido "no hacer nada", con los números de las alternativas.

## 6. Inteligencia

- **Autorización escrita de Ramón (2026-10-21), solo para el operador Meta:** usa de forma continua toda la capa de inteligencia (volatilidad de todos los modelos y consenso, break_prob, dirección y filtro direccional, Scanner, Advisor). Los grids normales y sus reglas no cambian.
- Señal ausente o vieja: no se usa y se anota.
- **Decisión en sombra:** en cada decisión se guarda también qué habría decidido usando solo Scanner + Advisor, para medir si la inteligencia completa ayuda.
- La estimación siempre se muestra como estimación con rango, nunca como promesa. "Poco probable" si < 25 %.
- **Evidencia que hay que recordar:** +3 % en 30 días con XRP se cumplió en 0 de 100 ventanas; los modelos de dirección no mostraron ventaja; el consenso no batió al campeón offline. La meta de +10 % puede ser difícil; el experimento es para medirlo.

## 7. Aprendizaje (Fase 40d)

- **40d-2 (modelo y frenos):** la etiqueta de 72 h entrena y la etiqueta del plazo valida en fechas posteriores. El replay histórico usa ventanas del plazo con paso de 7 días, solo cerradas antes del corte; `p_replay` suavizado es `(éxitos + 1) / (ventanas + 2)`.
- Pesos estimados con regresión logística, offset `logit(p_replay)`, regularización hacia 0,25, parámetros editables por perfil, tope por señal y ajuste total ±1,0. Las señales ausentes son neutrales y no cuentan para su muestra. Mínimo 30 muestras efectivas por señal, reducción por bloques no solapados, errores agrupados por fecha/moneda y corrección Holm.
- Una versión por fecha, perfil y origen (`live` o `histórical_reconstructed`) registra muestras, corte, métricas log-loss/Brier y frenos. El cambio máximo es 0,05 y solo se conserva si mejora ambas métricas frente a replay en validación posterior; el modelo sigue en sombra y no alimenta decisiones.
- **40d-3 (completada):** decisiones real/Scanner+Advisor/aprendida en bitácora, etiquetas contrafactuales existentes, interruptor manual apagado por defecto y vista de evidencia por señal.
- Pesos estimados con regresión logística, offset `logit(p_replay)`, regularización hacia 0,25, parámetros editables por perfil, tope por señal y ajuste total ±1,0. Las señales ausentes son neutrales y no cuentan para su muestra. Mínimo 30 muestras efectivas por señal, reducción por bloques no solapados, errores agrupados por fecha/moneda y corrección Holm.
- Una versión por fecha, perfil y origen (`live` o `histórical_reconstructed`) registra muestras, corte, métricas log-loss/Brier y frenos. El cambio máximo es 0,05 y solo se conserva si mejora ambas métricas frente a replay en validación posterior; el modelo sigue en sombra y no alimenta decisiones.
- **40d-3 (completada):** decisiones real/Scanner+Advisor/aprendida en bitácora, etiquetas contrafactuales existentes, interruptor manual apagado por defecto y vista de evidencia por señal.

- Medir el acierto de cada señal con lo que pasó después, en ventanas no solapadas.
- Recalcular el peso de cada señal; puede volverse negativo si falla de forma consistente (leerla al revés).
- Frenos: muestra mínima, prueba estadística contra el azar, cambios graduales.
- Primero en sombra; se activa cuando demuestre que habría llegado antes.
- Cuidado: "fallar" no siempre es "pasó lo contrario" (puede quedar plano), y en Spot no se vende en corto.

## 8. Fases

| Fase | Qué | Estado (2026-10-26) |
|---|---|---|
| 40a | Núcleo: datos, valoración, vigilancia, límites, cierres, Cancelar, API | Hecha, en main (`a5e6d1e`) y desplegada en S1 |
| 40b | Operador: análisis, plan, Iniciar, revisión cada 15 min, bots individuales, inteligencia completa | Parcial (2026-10-22): faltan cartera de varias piezas, préstamos, Advisor real, agregar piezas en la revisión y pruebas integradas → 40b-2 (hecho, en main). Medición real en S1 (2026-10-22): análisis de 25,6 min y bloqueo del monitor durante la revisión → 40b-3 (hecha, `9a49a2b`: el monitor ya no se bloquea; caché diario; desempate) |
| 40b-4 | A: valoración ejecutable por profundidad del libro. B: límite de pérdida en el replay, precálculo nocturno, medidor con caché, bot individual | Hecha y commiteada: A `e31ba8c`; B en esta entrega; suite completa offline verde |
| 40b-5 | Señal "toque compra→venta" en sombra (peso 0), estadísticas de `touch_event_analyzer.py` sin su puntaje | Hecha en este cambio; siguiente fase 40c |
| 40b-6A | Prudente con 30 verificaciones efectivas, umbral editable y presentación de readiness en español | Implementada y commiteada (`9797d9f`); pruebas enfocadas y suite completa offline verdes |
| 40b-6B | Operador activo: reajuste de piezas abiertas, materialidad, frenos y presentación de cambios | Implementada, pruebas enfocadas y suite completa offline verdes; commit de esta entrega |
| 40c | Pantalla Metas en la app, bitácora, historial, comparación con grupo de control; filtros reales en el replay, medidor ampliado, caché estable y Advisor reanclado al precio; pruebas de punta a punta pendientes de 40b-2 | Hecha (2026-10-24): commits `2d1b442` (B+C), `897f0d1` (A) y `3a513dc` (40c-3: defectos de presentación de la prueba de QA en S1); siguiente fase 40d |
| 40d-1 | Etiquetas 72 h/plazo, backfill causal y almacenamiento separado `live`/`histórical_reconstructed` | Implementada y verificada offline en esta entrega; sin S1 ni despliegue |
| 40d-2 | Regresión logística, errores agrupados, Holm y frenos de actualización | Implementada y verificada offline; pesos solo en sombra |
| 40d-3 | Caché acotada, decisiones en sombra, pesos efectivos y vista de evidencia | Implementada y verificada offline; sin despliegue |
| 40d-3C | Interruptores por perfil en Laboratorio, aprendizaje global en Metas y recarga parcial | Implementada y verificada offline; sin despliegue |
| 40d-4A | Comparación con ranking compartido y versión de contrato explícita | Cerrada en 40d-6: política contrafactual v2 con persistencia y cooldown propios |
| 40d-4B | Pantalla de solo lectura Inteligencia y medidor de evidencia | Cerrada en 40d-6: inventario de velas, calibración y conteos persistidos |

## Fase 43 — motor v2 (partes A y B)

El motor queda fijo en el `config_snapshot` de cada meta: las metas existentes sin campo usan v1. El motor v1 conserva el operador actual; v2 permanece no disponible. Una falla del motor activo produce no hacer nada y alerta, sin cambiar de motor. El motor contrario se evalúa en sombra con un límite de 30 s, fuera del candado de operación, y no puede ejecutar acciones.

Las velas públicas de 5 min se guardan en `market_data.db`, separada de la BD de trading, excluida de respaldos de despliegue y git. El backfill cubre 24 meses cerrados y los días cerrados del mes actual; la actualización horaria usa la API pública existente y no guarda velas en curso. La lista B3 de monedas de datos es editable en `scripts/velas_backfill.py`; se excluyen las monedas activas de operar cuando el Coin Registry está disponible. Ningún componente de Meta ni Fase 42 consume todavía este almacén. La entrega no altera decisiones reales.

| 43 | Interfaz de motor v1/v2 en sombra y almacén separado de velas públicas | Hecha y verificada offline; v2 no disponible, datos sin consumidores, sin red ni BD real |

### 43a-4 — cambio de D6 (2026-10-30)

Ramón decidió apagar el aprendizaje diario v1: sus medidores están en 0–6/30, la corrida demora alrededor de 90 minutos y retrasa Inteligencia. Se agrega un interruptor global auditable. Al apagarlo se bloquean el job de las 08:30 UTC, la corrida manual y las escrituras/etiquetas nuevas de aprendizaje; no se eliminan datos ni cambia la decisión v1. El apagado requiere que `use_learned_weights` esté apagado en Prudente y Rápido. El valor predeterminado queda `true` para conservar el comportamiento previo hasta que Ramón lo cambie en Laboratorio.

| 43a-4 | Interruptor global para suspender el aprendizaje diario v1 | Implementado y probado offline; debe apagarse manualmente tras desplegar, pues no se accedió a la BD real |
| 40d-5 | Señal toque compra a venta como sexta variable | Hecha y verificada offline |
| 40d-6 | Política contrafactual v2, inventario de velas, deciles de validación y conteos por corrida | Hecha; 40d-6B restaura los frenos de reajuste real previos al refactor |
| 40d-7A | Diagnóstico de saturación de señales | Correcciones de unidades, signo, cobertura y trazabilidad aplicadas directamente en Fase 41a |
| 40d-7B | Inteligencia legible, aciertos en sombra y defectos de Metas/Inteligencia | Pendiente |
| 40d | Aprendizaje continuo de pesos por señal | Partes 1-6 implementadas y verificadas offline; interruptor apagado por defecto |
| 41a | Corrección directa de señales Meta, trazabilidad y diagnóstico | Implementada y verificada offline; commit por partes, sin despliegue |
| 41b | Bitácora compacta v2 para planes y decisiones, recomendaciones guardadas una vez y decisiones ligadas por ID; lectores compatibles con v1 | Hecha localmente en `520766e`, `38bcffa`, `dcf6c3e`, `d1b3656`, `1956a2d`; 82 pruebas dirigidas verdes, sin push |
| 41b-11 | Revisión resistente a alternativas actuales ausentes y evaluación sin autoconflicto de la pieza propia | Ambas partes implementadas; comparación y plan verificados offline con 87 pruebas dirigidas, sin push |
| 41c-0 | Lecturas repetidas cacheadas por plan y subtiempos de selección | Hecha localmente; paridad con `a062e09`, 90 pruebas dirigidas verdes y medición sintética 148,26 → 2,24 s |
| 41c-1 | Curvas de cartera y corrección de C6 | Implementada: éxito por efectivo atribuible agregado; riesgo y avance por patrimonio agregado; 143 pruebas dirigidas verdes |
| 41c-2a | Preservar `capital_usage` en eventos de plan v2 | Contrato probado; no se reprodujo la pérdida reportada en S1 |
| 41c-2b | Asignación de efectivo libre con curvas v3 por monto | Motor en sombra; benchmark sintético con 30 USDT libres y 0,033 s de coste |
| 41c-2c | Interruptor por perfil y presentación en Metas/Inteligencia | Apagado por defecto; 122 pruebas dirigidas verdes; sin despliegue |
| 41c-2d | Asignación en sombra con geometría por monto, motivos de descarte y presupuesto de 30 s | Implementada y probada offline; decisión real comparada con HEAD; sigue en sombra |
| 41f | Medición del efecto del orden intravela entre replays 5m y 1m | Script y pruebas sintéticas; pendiente ejecutar con datos públicos en S1. Si Δ de probabilidad <2 pp, conservar simulador; si >2 pp, evaluar corrección en fase aparte |
| 41b-13 | Retención de cachés, resumen de planes de más de 14 días y herramienta manual dry-run/apply/VACUUM | Implementada y verificada offline; 127 pruebas dirigidas verdes; pendiente ejecución manual en S1 tras respaldo y detención del servicio |
| 41-C25a-h | Ruptura Meta v2 en sombra, poda de cachés, reetiquetado y replay activo aproximado | Implementadas y verificadas localmente; interruptor apagado; mediciones públicas y vista previa de BD pendientes en S1 |
| 41-C26a1-a10 | Herramientas S1, mediciones de replay, disparador manual, calibración, almacenamiento y asignación en sombra | Implementadas en commits separados; pruebas sintéticas/offline; sin uso de S1, Testnet, red ni BD real; decisiones reales sin cambios |

Cada fase: diseño → OK de Ramón → código → pruebas → reporte → revisión → commit → despliegue.


## Fase 41-C25 — Ruptura v2 y estudios (2026-10-28)

- La señal de ruptura v2 estima salida del rango a 72 horas con el modelo de dos barreras, margen 0 % y escala de sigma 1,0. El horizonte corto da una etiqueta temprana; las etiquetas maduras a 20/45 días sirven para validación posterior.
- La señal y su comparación quedan en sombra. El peso de ruptura permanece en 0,25 y `use_break_prob_v2` sigue apagado por defecto.
- Solo se recomendará activarla con al menos 30 etiquetas maduras, mejora simultánea de log-loss y Brier frente al modelo actual y terciles de riesgo ordenados. Ramón decide si la activa.
- C25h mide una política simplificada que liquida y reabre al salir del rango. No representa el operador Meta completo ni justifica cambiar una regla de producción por sí sola.



## 9. Otras decisiones del día (2026-10-21)

- **Repositorio unificado:** una sola tarjeta multimoneda en la pantalla; por dentro sigue un repositorio por moneda. 0.00 en gris en vez de −0.00 rojo.
- **Idioma:** `AGENTS.md` (Codex) y `CLAUDE.md` (Claude Code) piden todo en español.
- **Pruebas:** una sola carpeta temporal `--basetemp=.pytest_tmp`; capturas de evidencia en `reports/capturas/<fase>/`.
- **Actualizar S1:** `bash deploy/actualizar.sh` desde Git Bash (push + respaldo de BD + pull + reinicio + verificación). En S1: servicio `asple-predictor`, repo `/home/ubuntu/asple-predictor`, venv `venv2`, respaldos en `~/respaldos/`.

## 10. Errores que ya cometimos (para no repetirlos)

- Numeré el Modo Meta como Fase 37 sin ver que ya existía → pasó a **Fase 40**. Revisar `git log` antes de numerar.
- Traté los perfiles como "Prudente = perder poco, Rápido = ser rápido" e impuse Smart a Prudente → **corregido**: ambos buscan rapidez, el perfil solo pone límites.
- Prohibí que el operador vendiera con pérdida → **corregido**: puede cerrar con pérdida aceptable para mover capital.

## 11. Pendientes

- Prueba manual de 40a en Testnet (meta de 100 USDT, ligar un grid, valoración, Cancelar con cada opción).
- Revisar en S1 los tiempos del medidor, ejecutar el análisis con `--con-cache` y medir cuántas ventanas históricas de XRP compran/venden con el plan nuevo. Hecho (2026-10-24, ver §16): con caché, el replay seguía dominando el tiempo (799 s) porque la clave cacheaba el rango exacto redondeado a 8 decimales y casi nunca repetía; corregido en 40c (B4, cuantización a pasos de 0,25% y Advisor reanclado al precio actual). **Falta correr `--con-cache` dos veces seguidas en S1 con la corrección puesta, para confirmar que la segunda corrida reutiliza casi todos los replays.**
- Las señales aprobadas en 40b-2 entran en el logit de la probabilidad histórica con peso por defecto 0,25 y ajuste total máximo ±1,0; la probabilidad histórica y la ajustada se muestran por separado. La señal toque compra→venta queda fuera de ese ajuste, con peso 0, hasta que 40d la evalúe.
- Ventanas del replay = plazo del perfil (45 / 20 días).
- Prudente requiere por defecto 30 verificaciones efectivas de 24 h (N_MIN), editable por perfil en `meta_profile_settings`; la ventana actual permite alcanzar al menos ese umbral.
- A5: el precálculo debe continuar si un perfil no tiene candidatas; A6: conservar el objetivo introducido al volver a dibujar el formulario. Incluidos en 40b-6B.
- Propuesta para 40b/40c: avisar al crear un grid normal si hay una meta activa (el USDT de Testnet es compartido). Hecho en 40c (parte A5): aviso no bloqueante en Scanner y etiqueta "Meta #N" en Grids.

## 12. Medición real en S1 (2026-10-22)

- Perfil Rápido, 100 → 110: el análisis tardó **1538 s (~25,6 min)**; Advisor 609 s y replays 786 s. Objetivo: ≤ 2 min.
- **0 de 14 ventanas** de 20 días alcanzaron +10 USDT (probabilidad histórica 0 %; ajustada por señales 1,6 %). Coincide con la evidencia anterior: +10 % en 20 días no ocurrió en el historial con estas monedas.
- Plan elegido: una sola pieza, GRAMUSDT Simple con 70 USDT; 30 USDT sin usar.
- Errores encontrados con datos reales y ya corregidos por Nexo: cliente público sin get_book_ticker; rango del replay en precios de hoy aplicado a ventanas viejas.
- **Riesgo encontrado:** la revisión de 15 min mantiene tomado el bloqueo del monitor durante todo el análisis (~25 min), lo que frenaría el monitor de todos los grids y la vigilancia de pérdida. **No activar el Modo Meta en S1 hasta desplegar 40b-3.**
- **Después de 40b-3** (sin caché persistente): 1165 s (~19,4 min); Advisor bajó de 609 s a 92 s; replays 941 s siguen dominando. El monitor ya no se bloquea durante el análisis.
- Plan nuevo: JUPUSDT Simple 70 USDT + bot individual XRPUSDT 20 USDT; 0 % histórico, progreso mediano 39 % de la meta, peor caída histórica 20,4 %. Esa medición precede al límite de pérdida implementado en 40b-4B; no se recalculó con historial real en esta fase.

## 13. Decisiones del 2026-10-22 (valoración y orden)

- **Orden:** 40b-4 (valoración + replay/precálculo, fusionadas) → 40b-5 (señal en sombra) → 40c → 40d.
- **Valoración ejecutable:** lo que de verdad se recibiría vendiendo contra los niveles de bids, menos comisión. Tres estados: KNOWN (el libro absorbe todo), PARCIAL (absorbe una parte; lo faltante al último nivel absorbido menos 1 %, nunca cero), DESCONOCIDO (sin bids, libro viejo o error; nunca cero).
- **Qué usa cada cosa:** pérdida máxima → valor ejecutable (KNOWN o PARCIAL), confirmado 2 minutos seguidos; aviso 75 % → valor ejecutable sin confirmación; meta cumplida → solo USDT realizado; pantalla → los dos valores (mejor bid y ejecutable).
- **DESCONOCIDO deja la meta sin protección de pérdida:** si dura > 10 min, alerta fuerte por Telegram y repite cada 30 min. Sin cierre automático.
- **Alertas sin ruido:** solo al cambiar de estado; PARCIAL solo si dura > 30 min y falta > 25 % del inventario, máximo una cada 6 h.
- **Señal toque compra→venta (40b-5):** ya se registra en sombra (peso 0) por celda y ventana: toques, tasa de venta por horizonte, velas hasta vender, excursión adversa y censura; corrige toques repetidos y reescala X/Y. 40d medirá su utilidad.
- **Reglas de los prompts:** commit al final de cada fase sin push; pruebas de lo tocado durante el trabajo y suite completa antes del commit final; carpeta temporal nueva bajo $env:TEMP.

## 14. Fase 40b-4 (2026-10-22)

- Parte A está implementada y commiteada en `e31ba8c`: valoración por profundidad, estados `KNOWN`/`PARCIAL`/`DESCONOCIDO`, alertas persistidas, contador de pérdida a dos minutos, y estimación ejecutable en cancelación.
- Parte B está implementada y la suite completa queda verde: 1.462 passed, 1 skipped, 18 deselected, 55 warnings, 299,58 s. El replay corta al límite usando el `low` de la vela; el precálculo UTC diario comprueba el interruptor; `--con-cache` lee en solo lectura. El precálculo usa un solo worker y no toma `ops_lock`.
- El bot individual compara X a 0,5 %, 1,5 % y 3 % bajo el midpoint; Y se deriva del margen neto del Advisor con 0,21 puntos añadidos para las comisiones aproximadas de 0,1 % y un pequeño margen de redondeo. El replay reporta cuántas ventanas compran y venden.
- La medición S1 previa indicaba para XRP X=1,2191 e Y=1,3942 desde piso/techo Advisor. No se guardaron los recuentos de compra/venta en esa corrida y no se pueden reconstruir sin su historial; la nueva salida los incluirá en el próximo análisis. No se ejecutó análisis real en esta fase.

## 15. Medición en S1 después de 40b-4 (2026-10-24, sin caché persistente)

- Análisis completo **845,6 s (~14,1 min)**: scanner 42 s, Advisor 98 s, replays 616 s, selección 77 s, control 13 s.
- Plan: JUPUSDT Simple 70 USDT + XRPUSDT Simple 30 USDT (usa todo el capital; el bot de XRP fue reemplazado por un grid chico). Probabilidad histórica 0 % (0 de 23 ventanas), progreso mediano 35 %, peor caída 20,1 %.
- Por aclarar: si la "peor caída" se mide desde el máximo de la ventana (puede pasar del 18 % sin tocar el límite, que se mide contra el capital inicial). El medidor todavía no imprime ventanas cortadas por pérdida ni compras/ventas del bot.
- Siguiente: encender Modo Meta para que corra el precálculo de las 04:00 y medir con `--con-cache`.

## 16. Medición en S1 con `--con-cache` y corrección del caché (Fase 40c, parte B4, 2026-10-24)

- Resultado medido: análisis completo **950 s** (sin caché, 1082 s). El Advisor sí sirvió del caché (0 s; 135 aciertos en memoria del proceso, 9 persistentes), pero los replays tardaron **799 s** con solo 81 de ~170 aciertos persistentes, pese a que el caché se había llenado 2 horas antes con un análisis por la API.
- **Causa:** la clave del replay incluía el rango relativo (piso/precio actual, techo/precio actual) redondeado a 8 decimales; cualquier movimiento del precio cambia esa clave y el caché casi nunca servía. El caché del Advisor guardaba piso/techo como precios absolutos del momento en que se calculó, sin reanclarlos si el precio se movía.
- **Corrección (B4):** el rango relativo se cuantiza en pasos de 0,25 % del precio tanto en la clave como en la simulación (misma pieza relativa reutiliza su resultado durante movimientos intradía pequeños); el caché del Advisor se guarda en relativo (piso/techo sobre el precio de cuando se calculó) y se reancla al precio actual al leer, recalculando si el precio se movió más de 3 % o quedó fuera del rango original. El medidor fuerza UTF-8 en `stdout` (`sys.stdout.reconfigure`) para no depender de la configuración regional de la terminal (corrige "préstamos" en la consola de S1).
- Verificado con pruebas unitarias/integradas (no se corrió `--con-cache` dos veces seguidas en S1 con la corrección puesta): movimiento de 0,1 % reutiliza el mismo resultado cacheado; movimiento de 5 % recalcula; el rango usado siempre contiene el precio actual. **Pendiente:** confirmar en S1 que una segunda corrida con `--con-cache` tras esta corrección trae casi todos los replays desde caché.

## 17. Fase 40c-3 — defectos de presentación de la prueba de QA en S1 (2026-10-24)

Ramón probó la pantalla Metas en S1 con `4256f5e` puesto (el refresco automático ya no borra el análisis en curso); el análisis con caché tardó ~3 min, llegó el aviso de Telegram, y Iniciar/cuenta regresiva se vieron bien. Encontró 10 defectos de presentación (título ambiguo, sigma en blanco por una clave equivocada, estado del libro sin traducir, precio de PEPE truncado a 6 decimales, falta columna de préstamos, "cortadas por pérdida" en 0 por un bug real en `combine_aligned_replays`, avisos del plan no mostrados, tabla de límites cortada con scrollbar sin estilo, radios encima de su texto, perfil elegido sin resaltar), todos corregidos en `3a513dc`. Detalle completo en `REPORTE_FASE40C.md`.

De paso se encontró y corrigió una mojibake real en `grid/meta_operator.py` (bytes `?` literales en el aviso de préstamos, no un falso positivo del guardia de `frontend/`).

**Decisión de Ramón:** el vencimiento del plan sube de 5 a 10 minutos (`plan_ttl_seconds=600`), editable por perfil igual que `plan_price_drift_pct`; la deriva de precio al Iniciar (1 %) no cambia.

Lección para fases futuras: un plan sintético bien formado en las pruebas de interfaz no garantiza que los nombres de campo coincidan con los que de verdad produce `_build_plan` (ej. `sigma_24h` vs. `sigma_24h_open`). Conviene, cuando sea práctico, construir los planes sintéticos de prueba copiando literalmente la forma de un plan real en vez de inventarla de memoria.

## 18. Fase 40c-4 y primera operación real (2026-10-25)

- Ramón informó que inició en Testnet S1 la primera Meta real: Meta #3, capital 100 USDT, objetivo 103 USDT, perfil Rápido. Grid #12 JUPUSDT Smart, 70 USDT, rango 0.2544–0.3185, 6 niveles; una compra se llenó y mantiene su venta. Es contexto informado por Ramón, no una verificación local ni una autorización para operar o desplegar en S1.
- Una pieza Meta no debe tener un objetivo de grid ni un plazo individual que compita con la Meta completa. El objetivo/plazo los decide MetaCore; los parámetros heredados de la Meta #3 serán ignorados por el monitor tras el despliegue de la corrección.
- A2 aprobado por Ramón (2026-10-25) e implementado: en piezas Meta, `CLOSE_REPOSITORY` de política Smart registra solicitud de revisión y alerta Telegram, y deja que Meta decida; no cierra ni envía al repositorio. Se conservan los cierres verificados propios de 40a. Avisos limitados a uno cada 15 minutos por pieza.
- A3 aprobado por Ramón (2026-10-25) e implementado: préstamos según `loans_enabled` y el tope establecido por perfil en cada grid. No hay interruptor global; la tarjeta de la pieza muestra estado y tope efectivos y explica el aviso global de reducción de niveles ociosos.
- A6 informado y aprobado por Ramón (2026-10-25): antes de rotar se comprueba el libro de Testnet y la alternativa debe pasar prevalidación actual de rango, filtros, notional y capital. Una alternativa fallida no cierra ni retira la pieza; el motivo queda en bitácora. El análisis inicial y el alta de piezas también excluyen/etiquetan libros Testnet unilaterales.
- A7: la rotación usa los mismos umbrales de materialidad histórica de los reajustes (5 pp, o empate ±2 pp y avance mediano +10 %), dos revisiones ganadoras, enfriamiento de una hora y máximo de dos rotaciones por meta cada 24 horas. Valores configurables por perfil en `meta_profile_settings`.
- A8: `free_before_usdt` se limita al menor de capital reservado no asignado y `cash_usdt` atribuible conforme a capital inicial − entregas + devoluciones − comisiones. El caso informado debe reflejar aproximadamente 99,92 USDT tras pérdida/comisiones, no 100.
- A1 comprobado con `target_usdt=3.0` heredado: el monitor ignora `TARGET` y `MAX_DAYS` en grid Meta; el estado permanece ACTIVE sin iniciar cierre individual (`tests/test_grid_monitor_policy.py`).
- Verificación local posterior A6–A8 (offline, sin Testnet): **1517 passed, 1 skipped, 18 deselected, 56 warnings, 497,29 s**. Trabajo aún sin commit ni despliegue.
- A4–A5 y B1–B4 de presentación/control se detallan en `REPORTE_FASE40C.md`, §18. La suite offline del trabajo fue 1512 passed, 1 skipped, 18 deselected.


### Fase 40d-3A — costo del backfill

- Plantillas de aprendizaje por forma relativa y caché de replay por inicio semanal UTC fijo. El replay vivo conserva el anclaje a la fecha actual; la diferencia máxima de anclaje es 7 días.
- Límites iniciales editables: 30 plantillas y 20 minutos por corrida. El job guarda trabajo pendiente y lo retoma en corridas posteriores; cede entre bloques de tres cortes.
- El script `scripts/estimar_costo_aprendizaje_meta.py` requiere una URL indicada explícitamente y solo lee la base; estima costo sin acceder al mercado ni escribir.

### Fase 40d-3 — límites y activación manual

- Backfill: huella relativa con cuantización 0,0025, ventanas históricas semanales ancladas a lunes 00:00 UTC y caché persistente por forma/inicio; límite editable 30 plantillas y 20 minutos, prioridad a trabajo pendiente y pausa entre bloques. El replay en vivo sigue anclado al momento de revisión, con hasta 7 días de diferencia de fase.
- Los pesos efectivos se eligen por señal y perfil (live suficiente, histórico aceptado, si no 0,25). El ajuste total al logit conserva el tope ±1,0. La selección en sombra no mueve órdenes.
- El switch «Usar pesos aprendidos» se cambia manualmente por perfil con token, confirmación y actor; queda apagado y no se activa automáticamente. Las protecciones operativas no cambian.
- La vista muestra evidencia disponible, etiquetas maduras y frenos. La muestra real aún es insuficiente para recomendar activación.


### Fase 40d-3C - ubicación del interruptor y vista global

- Los interruptores de pesos aprendidos se cambian por perfil en Laboratorio → Reglas globales; la confirmación exige quién confirma y no se bloquea por falta de evidencia.
- Metas muestra el aprendizaje de todas las metas del perfil, sin depender de una meta activa. La actualización es manual; el refresco de 30 segundos solo toca los datos vivos.

## 19. Fase 40d-5 - señal de toque en el aprendizaje

- `touch_buy_to_sell` es la sexta señal del modelo Meta. Usa `touch_stats.sell_rate` en una ventana causal de 30 días y la geometría de la alternativa evaluada. La misma función calcula el dato en vivo y en reconstrucción histórica.
- Normalización: `2 × sell_rate − 1`, limitada a [-1, 1], agregada por capital asignado en carteras. El valor neutral de una señal ausente no se imputa ni cuenta para el mínimo efectivo.
- Peso base y prior de regularización de toque: 0. Las otras cinco señales conservan 0,25. Holm incluye seis señales. Tope de logit, muestra mínima, errores agrupados y límite por actualización siguen sin cambios.
- El switch continúa apagado por defecto. Sin un peso de toque aceptado no influye aunque se encienda; las estadísticas sombra de 40b-5 continúan separadas.
- Las muestras históricas que ya guardaban la tasa causal se leen sin cambiar ni borrar versiones. Las filas antiguas que no la guardan no aportan muestra para toque.
- Pendiente heredado: Inteligencia aún no persiste deciles de calibración; las tasas de éxito por valor alto/bajo son descriptivas y no las reemplazan.

## 20. Fase 40d-6 - comparación completa y telemetría de aprendizaje

- La comparación activa usa contrato v2 y solo agrega registros v2; la pantalla indica desde cuándo hay datos bajo esa versión. Las sombras aprendida y Scanner + Advisor mantienen persistencia, cooldown y límite diario separados de la política real.
- La política pura compartida gobierna los gates reales de reajuste y rotación. El cierre de pérdida permanece en la protección de 40a.
- meta_learning_runs.summary_json guarda inventario por moneda de velas y conteos de ventanas simuladas, caché y cortes; las versiones de pesos guardan deciles de calibración para el modelo y p_replay. No se guardan velas ni se añaden tablas.
- Pendientes de 40d-6: ninguno conocido en el alcance. El aprendizaje sigue en sombra salvo activación explícita por perfil.

## 21. Fase 40d-6B - restaurar el comportamiento de reajuste

- La decisión real vuelve a la semántica de 374cec4: compara la probabilidad ajustada `estimate["probability"]`, permite actuar en la primera revisión material por defecto, limita a cuatro reajustes por Meta en 24 horas y aplica una hora de enfriamiento por pieza desde el último reajuste.
- `reconfig_persistence_reviews` es editable por perfil: 1 reproduce el comportamiento original y 2 exige dos revisiones materiales consecutivas para la misma alternativa. La rotación conserva sus frenos actuales.
- Scanner + Advisor y la sombra aprendida evalúan reajustes con los mismos umbrales de materialidad, persistencia, cooldown y límite diario que la decisión real; cada política conserva su estado separado.
- Si la migración encuentra el valor 2 persistido, lo restablece a 1 y registra el cambio en la bitácora de la Meta más reciente del perfil.
- Estado: implementada y verificada con `tests/test_meta_operator.py` (60 passed); commit 40d-6B pendiente de esta entrega.

## 22. Fase 40d-7A - diagnóstico de señales

- Causa probable de Volatilidad y Ruptura: el pronóstico de volatilidad `horizon_h=24` es RMS por barra horaria (`exp(pred_logvol_cal)`), mientras que el Scanner y `break_prob` esperan sigma acumulada de 24 horas. Falta convertir por `√24`; un ejemplo (0,01/h frente a 0,05/24 h) lleva la señal actual a −1 y quedaría en −0,020 con horizonte comparable.
- En `model_a_down`, `probability_up` se calcula sobre la clase positiva que representa retorno negativo; equivale a P(baja). La señal actual la normaliza como positiva. Se propone invertir el signo para reflejar evidencia bajista, pero requiere aprobación porque cambia decisiones.
- El toque se omite si no hay velas previas disponibles o la estadística causal de 30 días no ofrece tasa. `scripts/diagnostico_senales_meta.py` muestra los motivos persistidos y la cobertura en una BD SQLite de solo lectura. Ejecutarlo en S1 queda pendiente; no se consultó S1 ni la BD real.
- No se modificaron fórmulas de señales ni pesos; los campos añadidos a volatilidad son telemetría sin uso decisorio. Pruebas locales focalizadas: 63 passed.

## 23. Fase 40d-7B - observabilidad legible y resultados en sombra

- Inteligencia presenta actividad y replay en lenguaje claro; sus tablas técnicas se despliegan a petición. Las decisiones muestran lado a lado Real, Scanner + Advisor y Aprendida, con filtro por meta y páginas. El JSON de una decisión permanece bajo expansión manual.
- B10 resume evidencia observada para dirección, volatilidad, toque compra→venta, p_replay y alternativas elegidas. La muestra menor de 30 se identifica como escasa; son métricas descriptivas y no cambian pesos ni decisiones.
- Metas lista solo piezas vivas y expresa el avance como fracción del objetivo; el backend expone el estado del grid junto a la valoración.
- Estado: implementación y pruebas focalizadas terminadas; commit de 40d-7B pendiente. S1 y despliegue no forman parte de esta fase.

## 24. Fase 41a — corrección directa de señales (2026-10-26)

- Arreglo directo por decisión de Ramón: los defectos se corrigen sin sombra ni interruptor nuevo.
- Los pronósticos por hora se expresan como sigma 24 h antes de comparar volatilidad o llamar a `break_prob`; la reconstrucción histórica comparte la conversión.
- `probability_up` de `model_a_down` se interpreta como P(baja); su señal normalizada nunca es positiva.
- Las alternativas usan el cargador de velas causal también para toque, y las señales combinadas conservan valor y diagnóstico por pieza.
- Las muestras nuevas llevan `signals_version: 2`; las muestras v1 (o sin versión) no entrenan volatilidad, ruptura ni dirección porque esos valores estaban defectuosos.

## Actualización Fase 41b — rendimiento, NOOP y diagnóstico

- El operador cachea resultados de toque por símbolo, día UTC y geometría relativa de 0,01 %, y fallos al descargar velas por símbolo durante 15 minutos. Los fallos dejan de generar una llamada por alternativa en el mismo intervalo.
- Las nuevas filas de `META_OPERATOR_NOOP` guardan los campos visibles de decisión en el nivel superior, la valoración, `plan_decision_id` y esquema 2. La evaluación de rotación no duplica planes completos.
- El diagnóstico de muestras, eventos y predicciones convierte los timestamps a `datetime` de SQLite antes de comparar.
- Pendiente de certificación: medir Advisor, replay, descarga lenta y `_build_plan` completos frente a `24f2ba7`, con el escenario sintético especificado. Las cifras de toque aislado no representan el tiempo de una revisión.

## Fase 41b-11 — conservación de decisiones de revisión (2026-10-27)

- La falta de una alternativa de una pieza para el grid actual se anota en la comparación con su motivo conocido. Un error de la comparación en sombra deja traceback y `decision_choices_error`; la decisión real y el NOOP correspondiente se guardan igualmente.
- No se cambian materialidad, empate, persistencia, enfriamientos, límites, pesos ni reglas del operador.
- Desde 41b-11 la Meta puede volver a reajustar/rotar una pieza que tenga ventas vivas. Al evaluar el reemplazo se excluye solo su propio grid del mismo símbolo, identificado en el plan por `own_grid_excluded_from_check`; las demás piezas propias, de otras metas o manuales siguen contando. Antes de actuar se repiten las validaciónes existentes.
- Iniciar y agregar piezas nuevas no excluyen grids existentes. La estimación de efectivo de un posible cierre no se usa como efectivo libre para aperturas adicionales ni para retiradas con ventas vivas.

## Fase 41c — rendimiento y coherencia del replay (2026-10-27)

- 41c-0 reutiliza lecturas de exchange/BD solo durante cada plan, con contexto aislado y descarte al terminar. Las ejecuciones vuelven a consultar los datos; las cachés no se comparten entre revisiones. No cambia números ni reglas en el escenario de regresión fijado a `a062e09`.
- Desde 41c-1 la probabilidad de cada alternativa se mide con la trayectoria agregada de la cartera. El éxito exige efectivo atribuible agregado más capital no asignado >= capital inicial + objetivo, igual que grid/meta.py:718; el inventario no cuenta para cumplir. El patrimonio agregado, neto de comisión estimada de venta del 0,1 %, mide avance, drawdown y pérdida máxima. Se guardan curvas cada 15 minutos; el método rolling_7d_v3_equity separa cachés y etiquetas incompatibles.
- 41c-2 amplía en sombra la asignación de efectivo libre y ofrece un interruptor explícito por perfil, apagado por defecto. No altera la ruta antigua mientras permanece apagado; solo Ramón puede activarlo.


## Fase 41b-12 — estimación comparable de piezas propias (2026-10-27)

- La pieza actual se estima por su símbolo, estrategia, rango, niveles, capital y margen reales mediante `rolling_7d_v3_equity` y la caché del plan. No requiere ser abrible de nuevo en Testnet. Las alternativas de rotación, apertura o reajuste siguen pasando el filtro de Testnet.
- El resumen propio se guarda bajo `current_pieces[grid_id]` y se adjunta a la bitácora de revisión. Se presenta en Metas e Inteligencia con probabilidades histórica y ajustada, rango, niveles, capital y avance mediano.
- Pruebas focalizadas: 108 passed acumuladas en operador/resiliencia, interfaz y benchmark, con 6 warnings; benchmark caliente 2,384 s frente a 2,39 s documentados en 41c-0 (−0,26 %). Sin suite completa ni validación en S1.



## Fase 41-G1a — compresión de replay

- Curvas formato 2 con zlib/byte shuffle; float32 y marcas cada 15 min. Legacy legible; caché antigua se regenera. Sin cambios de métricas/decisiones.

## Fase 41-G1b — comparación intravela

- Geometría calculada con 5m al inicio y reutilizada en 1m; se informa same_geometry. No cambia simuladores/decisiones.

## Fase 41-G1c — diagnóstico de valoración

- Inactividad y valoración desconocida tienen motivos separados. Una lectura no KNOWN espera 10 s y se reintenta una vez; ambos intentos quedan auditados. Revisión inicial ~20 s tras primer tick de MetaCore. Si persiste, no hay acción.
- Seguimiento C17: las valoraciones del watchdog y la revisión comparten `MetaCore._lock`; MetaCore corre al segundo 0 de cada minuto UTC y el operador al segundo 20 cada 15 minutos. Motivos de valoración DESCONOCIDA incluyen símbolo, libro y edad disponibles.

## Fase 41-G1d — cobertura de dirección

- Inteligencia diferencia cobertura activa P(baja) ≥0,60. La señal neutral 0 sigue contando como muestra; no se altera el modelo ni política real.

## Fase 41-G1e — readiness ausente

- Meta no inicializa coin_readiness; ausencia no satisface requisito Prudente.

## Fase 41-G1f — diagnóstico S1

- Verificador abre SQLite mode=ro. No afirma hora de precálculo sin evento persistido; caché es referencia.

## Fase 41-G2 — estudios históricos

- scripts/estudios_meta.py abre SQLite mode=ro. Calibración de dirección usa P(baja) y métricas de clasificación/calibración; no cambia señales.
- Calibración de grid compara geometría real fija con velas públicas 5m/1m.
- Replay activo y persistencia 1/2 se mantienen como no medidos hasta contar con un adaptador que conserve estado entre reajustes; replay pasivo no se presenta como política activa.
- Para estudio intravela de 90 días: diferencia <2 pp con 11 ventanas conserva simulador; diferencia mayor se evalúa aparte.

| 41-G2/G3 | Estudios Meta de solo lectura, calibración de dirección, Scanner/Advisor centrados y bots de toque en sombra | G2: calibración/grid implementados; replay activo y persistencia pendientes. G3: centrado y bots en sombra; interruptores apagados. Sin resultados de S1 |

## Fase 41-G3 — señales y bots en sombra

- La calibración de dirección de `model_a_down` usa `scripts/estudios_meta.py calibrar-direccion`; no se ejecutó contra una base real, por lo que no se concluye que supere el azar.
- `use_centered_signals` y `use_touch_bots` se guardan por perfil en falso. El centrado Scanner/Advisor y la propuesta de bot se calculan y guardan como sombra; ninguna de las dos opciones modifica la probabilidad, el ranking ni las acciones reales.
- Las etiquetas del bot usan velas OHLC y exigen que la venta aparezca en una vela posterior a la compra.
- Pendiente manual: ejecutar los estudios públicos/calibración en S1, inspeccionar métricas y decidir si se prepara otra fase.


| 41-G1g1 | Etiquetado con filtros reales y aislamiento de muestras inválidas | 112 pruebas dirigidas pasaron; el conteo de etiquetas históricas que requerirían recálculo queda pendiente de una lectura segura de BD |
| 41-G1g2 | Diagnóstico de bloqueos transitorios por valoración desconocida | Se mantiene la regla; el ledger guarda causa por moneda y duración de recuperación. El verificador consulta solo lectura las últimas 24 h |
| 41-G1g3 | Formato de curvas v3 y reducción de caché durable | Lector v1/v2 preservado; la caché durable guarda métricas sin curvas para quedar bajo 100 KB. Tras reinicio puede recalcular replay |
| 41-G1g4 | Descartes, cronómetros y deadline de sombra | Se conserva decisión real contra a25818b. La sombra continúa síncrona y puede esperar hasta el deadline |
| 41-G1g5 | Verificador resumido y bloques de diagnóstico | Salida sintética menor de 150 líneas; `--detalle` conserva la lista completa |

| 41-G1g6 | Cierre, comandos de consulta S1 y auditoría UTF-8 | Se documentan los cinco resultados y los límites: etiqueta histórica pendiente de dry-run; sombra síncrona hasta 30 s |
| 41-C25i | Lanzamiento manual del aprendizaje Meta | Endpoint autenticado agenda una corrida de fondo; el estado y resumen de la última corrida son de solo lectura. Confirmación en Inteligencia. Sin cambio a decisiones del operador. |

## Fase 41-C27 — medidores informativos de evidencia

Todos los medidores son informativos; Ramón decide y el sistema nunca enciende un interruptor por sí solo. Mínimo común: 30 casos maduros. Los intervalos comparativos usan bootstrap pareado, 2.000 remuestreos y semilla fija.

| Interruptor | Evidencia para «Listo para encender» |
|---|---|
| Pesos aprendidos, por perfil | Reutiliza los criterios del aprendizaje: 30 efectivos, Holm < 0,05 con peso distinto del base, validación ≥ 5 y log-loss/Brier mejores que solo p_replay. |
| Señales centradas, por perfil | ≥ 30 etiquetas 72 h con ambas probabilidades; log-loss y Brier mejores; IC 95 % de Δ log-loss completamente bajo cero. |
| Ruptura v2, por perfil | ≥ 30 etiquetas 72 h; log-loss y Brier mejores; tasas de salida del rango ordenadas por tercil; IC de Δ log-loss completamente bajo cero. |
| Bots de toque, por perfil | ≥ 30 propuestas etiquetadas a 72 h; venta observada a ±10 puntos de la esperada; ganancia neta estimada positiva con IC por encima de cero. |
| Asignación ampliada, por perfil | ≥ 30 decisiones «agregar» con etiquetas maduras a 72 h y al plazo; avance mayor que sin la pieza con IC que no cruce cero. Hasta enlazar esas etiquetas, permanece recolectando. |
| Recomendaciones para grids no Meta (Fase 42) | Interruptor independiente por grid, apagado por defecto. Requiere 30 etiquetas maduras de la acción, media de aplicar frente a mantener positiva, intervalo que no cruce cero y confirmación de Ramón por grid. |

C27a define los estados compartidos y C27b conecta datos y presentación. «No conviene» solo se emite con evidencia suficiente y resultado adverso significativo. La evaluación no reemplaza la confirmación manual.

### C27 — alertas de evidencia

Cuando una evaluación entra en “Listo para encender” o “No conviene”, se genera una alerta por transición mediante AlertDispatcher. Las alertas obedecen la configuración y el límite diario existentes; si no se pueden entregar, quedan pendientes. La confirmación y activación siguen siendo manuales.


### C27d — elegibilidad de datos y entrega de alertas

Los medidores de evidencia cuentan solo muestras del método vigente y admitidas para entrenamiento. Las alertas de preparación conservan eventos encolados durante envíos concurrentes. Ninguno de estos cambios activa interruptores ni altera decisiones del operador.

## Fase 43, parte C — marcador de pronósticos

- El marcador mide volatilidad walk-forward con velas públicas de 5 minutos; en cada origen solo usa observaciones anteriores al instante pronosticado. La sigma se expresa como raíz de la suma de retornos logarítmicos al cuadrado acumulada en el horizonte de 24 o 72 horas.
- Un modelo se considera útil solo si QLIKE mejora al menos 5 % frente a `simple` y el intervalo bootstrap por bloques al 95 % de la diferencia excluye cero. Ruptura requiere Brier skill score de al menos 0,05 y pendiente de calibración entre 0,8 y 1,2. Los diagnósticos no autorizan cambios de política.
- El diagnóstico de régimen (ER, varianza relativa y ciclos) pertenece a la medición 43c. La versión implementada aún no calcula toda la validación por monedas requerida; sus resultados no son evidencia para habilitar modelos.
- El job semanal se define para permanecer apagado por defecto hasta que Ramón valide una corrida manual. Esta entrega no registra el job ni expone su interruptor.
- Los arreglos C43-1 a C43-5 corrigen el orden y los papeles del inventario de velas, comparan API hasta la última vela guardada, muestran el estado de aprendizaje apagado y representan la decisión de operador estructurada. Ninguno de estos arreglos cambia decisiones reales.
- Todo el marcador y su panel son de observación. No conectan resultados al motor v1 ni a decisiones reales.

## Inteligencia para grids no Meta — Fase 42

Smart y Simple activos ajenos a Meta reciben evaluación horaria en sombra. La métrica es ganancia neta a siete días (PnL realizado mós inventario a bid neto de comisión), con drawdown como límite. Se permiten mover el rango sin vender inventario, cambiar niveles, agregar un bot de un nivel con efectivo libre o pausar compras.
La aplicación requiere opt-in independiente por grid, 30 etiquetas maduras de la acción, ganancia media de aplicar frente a mantener positiva e intervalo de confianza enteramente sobre cero, y confirmación explícita de Ramón. Tras encender: dos recomendaciones horarias iguales, seis horas de enfriamiento, máximo dos cambios diarios, grid ACTIVE y valoración conocida. El sistema no enciende interruptores por sí solo. Las transiciones a estado listo o adverso generan alertas por grid y acción mediante el job horario C35, con deduplicación.



## Fase 43c, continuación - estado del marcador

- C43-7 optimiza el cálculo causal; benchmark de 218.000 velas: 0,184 s para 24 h y 0,212 s para 72 h.
- C43-8 guarda cobertura causal, IC de factores, pronóstico vivo con escala raíz de H cuando hay SQLite explícita, veredictos por símbolo y agregados.
- C43-9/10 conectan al runner los objetivos de ruptura, régimen, dirección y correlación mensual. Los resultados son métricas de investigación; no se conectan al motor v1.
- El panel muestra veredictos agregados y detalle por moneda. El job semanal C43-12 se registra al arranque, comprueba el interruptor GLOBAL al disparar y permanece apagado por defecto hasta validar una corrida manual.


- C43-12: el job semanal queda bajo `scorecard_weekly_enabled=false` por defecto; solo un actor confirmado puede cambiarlo. Cambiarlo en caliente no requiere reiniciar. Evento auditado en `scorecard_switch_events`.

### Fase 43e — decisiones de escenarios

El marcador mantiene correlaciones ausentes como NaN internamente y null al serializar; HAR usa un piso conservador del 25 % del pronóstico simple para evitar degeneración. Estas correcciones de investigación no alteran decisiones reales.

### Fase 43e - decisiones experimentales y estado

E1: evaluar Simple, bots individuales y Smart. E2: tendencia cero en escenarios. E3: cada moneda conserva sus propios bloques históricos, con fechas sincronizadas entre monedas de la cartera. E4: cálculos fuera del proceso de servicio. E5: 1.000 escenarios y plazo según perfil; Smart exacto sobre un subconjunto de hasta 200. E6: sin préstamos ni interés compuesto.

El replay Smart experimental apaga préstamos, compound, ADJUST, reespaciado, niveles ociosos, stop-loss por celda y filtro direccional; mantiene esperar fuera de rango. Los resultados de FHS, FastSim y Smart se usan solo para investigación. La prueba sintética local midió 15,453 s y 166,4 MB para 200 escenarios Smart de 20 días. Nada cambia decisiones reales; v2 permanece `available=False`.

43e-6a fija el objetivo informativo: PnL medio >= -1 % del capital, cota superior del IC de P(límite) <= 5 % Prudente / 15 % Rápido y CVaR 5 % dentro de la pérdida maxima del perfil. Ordena por P(meta), mediana de días a meta y PnL; si todo falla, mantiene USDT quieto como alternativa factible.

43e-6c quedó completada en 43e-6e: historia usa EWMA causal con factor verificado anterior a t, geometría compartida con escenarios, bot por offsets y Smart exacto sobre hasta 200 inicios; se verificaron OU/GBM y la variante sin tendencia. 43e-6d se completó en 43e-6f con aceptación predictiva contra realidad y baseline v1. 43e-7 exporta la API pública de investigación. La validación predictiva local usa sintéticos; falta la corrida de S1 antes de aceptar científicamente el modelo. Nada cambia decisiones reales; v2 permanece `available=False`.



### Fase 43f - decisión de v2 y estado

- **F1:** el criterio CVaR 5 % se sustituye por el desliz del corte: media ≤ 2 % del capital de la meta, configurable por perfil. Se conserva CVaR como métrica informativa.
- **F2:** el motor queda fijado por meta. En una meta nueva, v2 solo puede operar si el interruptor global **Motor v2 disponible** está encendido, el perfil ya tiene `decision_engine=v2` y la caché contextual está vigente. La configuración inicial conserva v1 y los dos interruptores globales parten apagados. El motor activo no recurre al otro si falla.
- **F3 / D7:** `recommend_grid` queda como función experimental reutilizable para 43l; no se conecta a grids de Fase 42 en esta entrega.
- **Mantener:** se aproxima evaluando una pieza nueva con las mismas líneas desde el precio actual; las comisiones de salida de la pieza actual se descuentan al comparar alternativas.
- La caché horaria corre fuera del servicio, con prioridad baja, límite de 10 minutos y tope de 600 MiB donde el sistema permite imponerlo. Su capital contextual usa fills, reservas y valoración al último cierre con comisión estimada; los casos de atribución incompleta se omiten.
- La prueba congelada de `_build_plan` y `_review_allocations` contra `776eedf` confirma que plan operativo y decisión v1 permanecen iguales. Las metas actuales siguen en v1.
- **43f-4b:** el job diario completa `shadow_outcome_json` con simulaci\u00f3n sobre velas reales y est\u00e1 apagado por defecto; Laboratorio muestra el resultado al lado de la decisi\u00f3n real. No se encendi\u00f3 el marcador.
- 43f-6 exporta la recomendación experimental aislada. No cambia decisiones reales. No habilitar v2 hasta revisión de Nexo y aprobación de Ramón.


### Fase 43f-3e, 43f-4b y 43f-8

La caché cuenta geometrías evaluadas y descartadas con sus motivos, y descarta aisladamente una candidata cuando FastSim detecta filtros inválidos. El marcador registra la alternativa sombra contra velas reales cuando vence su ventana; mantener/no hacer nada se compara con el resultado reconstruido de la meta y USDT quieto tiene ganancia cero. Su job diario y su interruptor de Laboratorio están apagados por defecto.

La validación deja las geometrías sin variación predictiva y observada en su fila con veredicto «sin información» y no las usa en el agregado. El resultado consolidado comunicado es empate de v2 y v1 en Brier de P(meta), con cobertura P10-P90 de 0,88-0,90. La regresión v1 contra `776eedf` conserva plan y revisión; no se habilita v2 aquí.


### 43f-3f - límites operativos de caché

La validación de candidatos Simple verifica compra y venta neta con redondeo de cantidad, comisión de 0,1 %, `min_qty` y `min_notional`; el bot usa los filtros del simulador. Los escenarios se generan una vez por moneda y se reutilizan para lotes de hasta seis piezas; el evaluador no devuelve curvas cuando la caché solo necesita métricas. La bisección opera dentro del lote y reutiliza los escenarios.

El proceso aplica `--max-rss-mb=600` y `--max-segundos=540` por defecto, mide RSS cada 2 s, guarda resultados completados y devuelve 3 (`memoria`) o 4 (`timeout`). Las corridas `running` mayores de 30 minutos se marcan `abandonada`. Medición sintética: cinco monedas, 1.000 escenarios, 15 días, `ok`, 204,09 MB y 223,71 s. Los interruptores de caché, motor v2 y marcador de sombra continúan apagados por defecto; la regresión conserva la decisión v1 de `776eedf`.
