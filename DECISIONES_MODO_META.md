# Modo Meta — Decisiones y rumbo

**Para qué es este archivo:** tener en un solo lugar qué estamos construyendo, qué se decidió y por qué, para no perder el camino. Si algo de otro documento o prompt choca con esto, manda este archivo (y la especificación `ESPEC_FASE40_MODO_META.md`).
**Última actualización:** 2026-10-21.

---

## 1. La idea (lo que Ramón quiere, desde el principio)

> "Te doy 100, quiero 110, usa Rápido." Eso es lo único que Ramón dice.

- La app hace todo lo demás, sola y de forma continua: revisa el mercado, decide con qué moneda(s), qué piezas abre (grid Smart, grid Simple o bots individuales de compra X / venta Y), cuántas, con cuánto capital, piso, techo y margen.
- Va monitoreando. Si ve que conviene cerrar un grid (aunque sea con una pérdida aceptable) para mover el capital a otra moneda que llega antes a la meta, lo hace.
- Usa **toda la capa de inteligencia** (modelos de volatilidad, consenso, dirección, Scanner, Advisor, estadísticas e historial).
- Guarda récord de cada decisión y, con el tiempo, **aprende** qué señales sirven y cuáles no (Fase 40d).
- Objetivo: **llegar a la meta lo más rápido posible.**

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
- No ajusta rangos de piezas abiertas: si una pieza ya no sirve, la retira o la cierra y abre otra.
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

- Medir el acierto de cada señal con lo que pasó después, en ventanas no solapadas.
- Recalcular el peso de cada señal; puede volverse negativo si falla de forma consistente (leerla al revés).
- Frenos: muestra mínima, prueba estadística contra el azar, cambios graduales.
- Primero en sombra; se activa cuando demuestre que habría llegado antes.
- Cuidado: "fallar" no siempre es "pasó lo contrario" (puede quedar plano), y en Spot no se vende en corto.

## 8. Fases

| Fase | Qué | Estado (2026-10-22) |
|---|---|---|
| 40a | Núcleo: datos, valoración, vigilancia, límites, cierres, Cancelar, API | Hecha, en main (`a5e6d1e`) y desplegada en S1 |
| 40b | Operador: análisis, plan, Iniciar, revisión cada 15 min, bots individuales, inteligencia completa | Parcial (2026-10-22): faltan cartera de varias piezas, préstamos, Advisor real, agregar piezas en la revisión y pruebas integradas → 40b-2 (hecho, en main). Medición real en S1 (2026-10-22): análisis de 25,6 min y bloqueo del monitor durante la revisión → 40b-3 (hecha, `9a49a2b`: el monitor ya no se bloquea; caché diario; desempate) |
| 40b-4 | A: valoración ejecutable por profundidad del libro. B: límite de pérdida en el replay, precálculo nocturno, medidor con caché, bot individual | Hecha y commiteada: A `e31ba8c`; B en esta entrega; suite completa offline verde |
| 40b-5 | Señal "toque compra→venta" en sombra (peso 0), estadísticas de `touch_event_analyzer.py` sin su puntaje | Hecha en este cambio; siguiente fase 40c |
| 40c | Pantalla Metas en la app, bitácora, historial, comparación con grupo de control; filtros reales en el replay, medidor ampliado, caché estable y Advisor reanclado al precio; pruebas de punta a punta pendientes de 40b-2 | Hecha (2026-10-24): commits `2d1b442` (B+C) y `897f0d1` (A); siguiente fase 40d |
| 40d | Aprendizaje continuo de pesos por señal | Pendiente |

Cada fase: diseño → OK de Ramón → código → pruebas → reporte → revisión → commit → despliegue.

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
- **Corrección (B4):** el rango relativo se cuantiza en pasos de 0,25 % del precio tanto en la clave como en la simulación (misma pieza relativa reutiliza su resultado durante movimientos intradía pequeños); el caché del Advisor se guarda en relativo (piso/techo sobre el precio de cuando se calculó) y se reancla al precio actual al leer, recalculando si el precio se movió más de 3 % o quedó fuera del rango original. El medidor fuerza UTF-8 en `stdout` (`sys.stdout.reconfigure`) para no depender de la configuración regional de la terminal (corrige "pr?stamos" en la consola de S1).
- Verificado con pruebas unitarias/integradas (no se corrió `--con-cache` dos veces seguidas en S1 con la corrección puesta): movimiento de 0,1 % reutiliza el mismo resultado cacheado; movimiento de 5 % recalcula; el rango usado siempre contiene el precio actual. **Pendiente:** confirmar en S1 que una segunda corrida con `--con-cache` tras esta corrección trae casi todos los replays desde caché.
