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

| Fase | Qué | Estado (2026-10-21) |
|---|---|---|
| 40a | Núcleo: datos, valoración, vigilancia, límites, cierres, Cancelar, API | Hecha, en main (`a5e6d1e`) y desplegada en S1 |
| 40b | Operador: análisis, plan, Iniciar, revisión cada 15 min, bots individuales, inteligencia completa | Parcial (2026-10-22): faltan cartera de varias piezas, préstamos, Advisor real, agregar piezas en la revisión y pruebas integradas → 40b-2 (hecho, en main). Medición real en S1 (2026-10-22): análisis de 25,6 min y bloqueo del monitor durante la revisión → 40b-3 |
| 40c | Pantalla Metas en la app, bitácora, historial, comparación con grupo de control | Pendiente |
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
- Hacer commit de: `deploy/`, `.gitattributes`, `DECISIONES_MODO_META.md` y los cambios de `ESPEC_FASE40_MODO_META.md`.
- Señales en la estimación (propuesto 2026-10-22, vale cuando Ramón envíe el prompt 40b-2): entran sumadas en el logit de la probabilidad histórica; peso por defecto 0,25 por señal y ajuste total máximo ±1,0 en logit, hasta que 40d aprenda los pesos. El plan muestra la probabilidad histórica y la ajustada por separado.
- Ventanas del replay = plazo del perfil (45 / 20 días).
- Propuesta para 40b/40c: avisar al crear un grid normal si hay una meta activa (el USDT de Testnet es compartido).

## 12. Medición real en S1 (2026-10-22)

- Perfil Rápido, 100 → 110: el análisis tardó **1538 s (~25,6 min)**; Advisor 609 s y replays 786 s. Objetivo: ≤ 2 min.
- **0 de 14 ventanas** de 20 días alcanzaron +10 USDT (probabilidad histórica 0 %; ajustada por señales 1,6 %). Coincide con la evidencia anterior: +10 % en 20 días no ocurrió en el historial con estas monedas.
- Plan elegido: una sola pieza, GRAMUSDT Simple con 70 USDT; 30 USDT sin usar.
- Errores encontrados con datos reales y ya corregidos por Nexo: cliente público sin get_book_ticker; rango del replay en precios de hoy aplicado a ventanas viejas.
- **Riesgo encontrado:** la revisión de 15 min mantiene tomado el bloqueo del monitor durante todo el análisis (~25 min), lo que frenaría el monitor de todos los grids y la vigilancia de pérdida. **No activar el Modo Meta en S1 hasta desplegar 40b-3.**
