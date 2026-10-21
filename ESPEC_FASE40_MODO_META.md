# Fase 40 (propuesta) — Modo Meta: operador automático por objetivo

**Proyecto:** ASPLE Predictor · **Autor de la idea:** Ramón Cruz · **Fecha:** 2026-10-21
**Estado:** especificación aprobada en conversación, decisiones D1–D5 cerradas (2026-10-21). La especificación describe el módulo final; su construcción se divide en 40a (núcleo), 40b (operador) y 40c (pantalla).
**Ámbito:** solo Binance Spot **Testnet**. Nada de dinero real.

---

## 1. Qué es

Un modo nuevo en el que Ramón da una **meta** y el sistema decide solo cómo llegar:

> "Tengo 100 USDT, genérame 10 para un total de 110."

El operador revisa el mercado y las monedas con las que puede operar, y decide **sin intervención**:

- con qué moneda (o monedas) operar;
- qué piezas abrir: **grid Smart**, **grid Simple** o **bots individuales** (compra a X, vende a Y);
- cuántos bots/niveles, piso y techo, espaciado y capital por pieza;
- cuándo ajustar, cerrar o abrir piezas nuevas;
- cuándo desactivarse.

Objetivo de diseño: llegar a la meta **lo más rápido posible dentro del perfil de riesgo elegido**.

Es un **experimento**: lo que se quiere saber es si el operador llega a la meta y cuánto tarda, comparado con un grid Smart normal con el mismo capital. No se asume que vaya a funcionar (ver §9).

---

## 2. Dos perfiles seleccionables

Ramón elige el perfil al crear la meta. Los números son **punto de partida**, no están probados; se ajustan con los resultados de las corridas. Deben ser editables (tabla de overrides / Laboratorio).

| Parámetro | **Prudente** | **Rápido** |
|---|---|---|
| Monedas elegibles | Volatilidad media, con estadísticas de modelo completas | También las más volátiles (siempre pasando Scanner y filtro de libro) |
| Capital máximo por moneda | 40 % del capital de la meta | 70 % del capital de la meta |
| Piezas simultáneas | Hasta 2 grids o 4 bots individuales | Hasta 4 grids u 8 bots individuales |
| Tipo de grid preferido | Smart | El que estime más rápido (Smart o Simple) |
| Margen por venta | El de siempre (0,7 % tras comisiones) o más | Puede bajarlo, respetando el paso mínimo existente (~0,30 %) |
| **Pérdida máxima total** | **−8 %** | **−18 %** |
| Aviso previo (75 % del límite) | −6 % | −13,5 % |
| Plazo máximo | 45 días | 20 días |
| Préstamos entre bots | Sí, tope 30 % | Sí, tope 50 % |

---

## 3. Cálculo del valor y de la pérdida

```
Valor actual = USDT libre de la meta
             + Σ (cantidad de cada moneda en mano × precio BID actual)
             − comisión estimada de venta (0,1 % de lo anterior)

Resultado %  = (Valor actual − Capital inicial) / Capital inicial × 100
```

- Incluye lo **realizado** (ventas ya hechas, comisiones pagadas) y lo **no realizado** (monedas en mano valoradas a lo que pagarían hoy).
- Precio: **bid del libro** (lo que realmente pagarían al vender), no el último precio.
- Si el libro de una moneda no tiene compradores (`BOOK_ONE_SIDED` o similar): el resultado se marca **DESCONOCIDO**, no se toma como cero. En ese estado: no abre piezas nuevas y avisa por Telegram.
- El capital de la meta está **aislado**: el cálculo usa solo saldos y órdenes de las piezas de esa meta, no el resto de la cuenta Testnet.

---

## 4. Ciclos de revisión

| Ciclo | Frecuencia | Qué hace |
|---|---|---|
| **Vigilancia de pérdida** | Cada **1 minuto** | Calcula el valor (§3). Si toca la pérdida máxima → cierre total (§6). Si toca el 75 % → alerta y bloqueo de piezas nuevas. |
| **Revisión del plan** | Cada **15 minutos** | Analiza mercado (Scanner, σ, modelos) y decide abrir / ajustar / cerrar piezas. |

La vigilancia de 1 minuto debe ser liviana (saldos + precios) y no competir con el monitor existente. Registrar en cada corrida cuánto se pasó del límite en el momento del cierre (deslizamiento).

---

## 5. Meta cumplida

- La meta puede definirse en **USDT fijos** (ej. +10 USDT) o en **%** del capital (ej. +10 %). Internamente se guarda el objetivo en USDT.
- La meta se cumple cuando el **USDT libre realizado** de la meta ≥ capital inicial + meta (ej. ≥ 110 USDT).
- En ese momento el operador se **desactiva**: no abre compras nuevas ni piezas nuevas; cancela las órdenes de **compra** vivas.
- **Posiciones sobrantes (D1):** los bots que aún tengan monedas conservan su orden de **venta** y **venden solo cuando tengan ganancia** (al precio de su nivel de venta). No se venden con pérdida, no tienen plazo y el límite de pérdida de la meta ya no las cierra. Lo que se venda es **ganancia extra** y se reporta **aparte**; mientras no se venda, se muestra su valor actual a precio bid. Ramón puede cerrarlas en cualquier momento con el botón Cancelar (§6).

---

## 6. Cierres

| Causa | Acción | Resultado de la corrida |
|---|---|---|
| Meta cumplida | §5 | `CUMPLIDA` (con tiempo transcurrido) |
| Pérdida máxima tocada | Cancelar todas las órdenes y vender todo, usando el **cierre verificado** existente (no marcar DONE con órdenes vivas; CLOSING / `CLOSE_PENDING` si queda algo) | `CORTADA_POR_PERDIDA` |
| Plazo vencido | Igual que pérdida máxima | `VENCIDA` |
| Ramón la cancela | Botón **Cancelar operación** (ver abajo) | `CANCELADA` |

**Botón Cancelar operación** (disponible mientras la meta esté activa y también en la fase de posiciones sobrantes):

1. Muestra el resumen: valor actual, resultado %, ganancia/pérdida por pieza.
2. Ofrece dos opciones:
   - **Vender todo:** cancela todas las órdenes y vende todas las posiciones a mercado, con el cierre verificado. Muestra antes la ganancia/pérdida estimada.
   - **Mover al repositorio:** cancela las órdenes de compra y pasa los bots con monedas al **repositorio existente** (los grids especiales que ya se usan al cancelar un grid, `CLOSE_REPOSITORY`). Allí cada bot vende y, al vender, se desactiva, igual que con un grid normal. No se crea un repositorio nuevo; solo se marca el origen `META` y el id de la meta para poder rastrearlos. Las piezas de la meta quedan cerradas.
3. Pide confirmación antes de ejecutar.
4. Queda registrado en la bitácora y en la corrida (opción elegida, valor al cancelar).

Los stop-loss por celda de cada pieza siguen funcionando según el interruptor global (`STOPLOSS_ENABLED`); el límite de pérdida de la meta va **por encima** de ellos.

---

## 7. Registro y medición

- **Bitácora de decisiones:** cada decisión del operador (abrir, ajustar, cerrar, no hacer nada) con fecha, motivo, datos usados (σ, score del Scanner, precio) y perfil activo.
- **Cada meta es una corrida** con: perfil, capital, meta, monedas usadas, piezas abiertas, resultado (§6), tiempo, peor resultado % alcanzado, deslizamiento en el cierre, comisiones, ganancia extra.
- **Grupo de control:** por cada corrida, poder lanzar (o simular) un grid Smart normal con el mismo capital y fecha, para comparar.
- **Pregunta del experimento:** ¿el perfil Rápido llega antes de verdad, o solo pierde más cuando falla? ¿Alguno de los dos le gana al grid Smart normal?

---

## 8. Interfaz y alertas

Pantalla nueva **Metas**. Flujo para crear una meta:

1. **Definir:** capital (USDT) y meta (en USDT o en %).
2. **Elegir perfil:** Prudente o Rápido (con la tabla de límites a la vista).
3. **Análisis:** el sistema revisa el mercado y presenta:
   - monedas candidatas con sus estadísticas (σ, score del Scanner, estado del libro, estadísticas de modelo);
   - el **plan con el que propone comenzar**: qué piezas (Smart / Simple / bots individuales), en qué monedas, capital por pieza, piso/techo, n de bots, margen;
   - estimación de tiempo y de riesgo (pérdida máxima en USDT, plazo).
4. **Iniciar:** Ramón pulsa **Iniciar** y el operador arranca de forma autónoma. Sin pulsar Iniciar no se abre nada. Si el análisis tarda o el mercado cambia mucho antes de pulsar, el plan se recalcula.

Con la meta en marcha:

- Vista de la meta activa: valor actual, resultado %, barra hacia la meta y hacia el límite de pérdida, piezas abiertas, tiempo restante, ganancia extra.
- Bitácora de decisiones legible.
- Botón **Cancelar operación** (§6).
- Historial de corridas con su resultado y la comparación con el grupo de control.

**Metas simultáneas (D2):** una sola meta activa a la vez (`MAX_METAS_ACTIVAS = 1`, ajustable). Una meta en fase de posiciones sobrantes no cuenta como activa, pero su capital sigue ocupado hasta que se vendan.
- Telegram: meta creada, pieza abierta/cerrada por el operador, aviso al 75 % del límite, resultado DESCONOCIDO, meta cumplida, corte por pérdida, vencimiento.

---

## 9. Contexto y riesgos conocidos (no ignorar)

- Con XRP, un objetivo de **+3 % en 30 días se cumplió en 0 de 100 ventanas**. Una meta de +10 % puede ser difícil.
- Ningún modelo mostró **ventaja de dirección**; Smart vs Simple salieron **indistinguibles** en P&L.
- Optimizar por velocidad empuja a más volatilidad y más concentración → pérdidas mayores cuando falla. Por eso el límite de pérdida y el plazo son obligatorios.
- Testnet tiene libros de órdenes raros (unilaterales, poco volumen); los resultados de Testnet no garantizan nada en real.

---

## 10. Restricciones (respetar la lista de "no autorizado")

- Interruptor propio del Modo Meta, **apagado por defecto**. La apertura automática general de grids sigue apagada; este modo solo abre piezas **dentro de una meta** creada por Ramón.
- Solo Testnet. Bloqueo explícito si la configuración apunta a real.
- **No** cambiar los defaults globales Smart/Simple, ni umbrales `pause_*` / `adjust_trigger_z`, ni stop-loss por moneda/σ, ni GRU/TFT/umbral 0,60, ni nada más de la lista de no autorizado del contexto. Si el operador necesita algo de eso, se pide autorización escrita.
- Reusar piezas existentes (Scanner, Advisor, motor de grids, cierre verificado, préstamos, alertas). Si los **bots individuales** (compra a X / vende a Y) no existen como pieza, crearlos como tipo nuevo, mínimo y con sus pruebas.

---

## 11. Criterios de aceptación (para pruebas)

1. Crear una meta de 100 → 110 en cada perfil; el operador abre al menos una pieza dentro de los límites del perfil (capital por moneda, piezas simultáneas).
2. El valor y el resultado % coinciden con el cálculo manual de §3 (bid, comisión) en un caso de prueba con números fijos.
3. Con un precio simulado que lleva el resultado a −8 % (Prudente) / −18 % (Rápido): cierre total verificado, resultado `CORTADA_POR_PERDIDA`, alerta Telegram.
4. Al cruzar el 75 % del límite: alerta y bloqueo de piezas nuevas, sin cierre.
5. Libro sin compradores: resultado DESCONOCIDO, sin piezas nuevas, alerta.
6. Al llegar el USDT realizado a 110: desactivación, compras canceladas, ventas vivas conservadas; las sobrantes solo venden con ganancia y se reportan aparte como ganancia extra.
7. Plazo vencido: cierre verificado y resultado `VENCIDA`.
8. Bitácora: cada decisión tiene motivo y datos.
9. Con el interruptor apagado, no se puede crear ni ejecutar ninguna meta.
10. Ninguna pieza de una meta toca saldos u órdenes de grids que no son de la meta.
11. Sin pulsar **Iniciar** no se abre ninguna pieza; el análisis muestra estadísticas de las monedas candidatas y el plan propuesto.
12. Meta en USDT y meta en %: ambas se crean y se convierten bien al objetivo en USDT.
13. Cancelar → Vender todo: todo vendido con cierre verificado, resultado `CANCELADA`.
14. Cancelar → Mover al repositorio: posiciones en el repositorio con origen `META` e id de meta, órdenes de compra canceladas, piezas cerradas.
15. Con una meta activa, no se puede crear otra (mensaje claro).

---

## 12. Decisiones (cerradas 2026-10-21)

- **D1.** Posiciones sobrantes tras cumplir la meta: venden **solo cuando tengan ganancia**; sin plazo y fuera del límite de pérdida. Cancelables a mano (§5, §6).
- **D2.** **Una meta activa a la vez** al principio (recomendación aceptada como punto de partida; parámetro ajustable).
- **D3.** Meta en **USDT y en %**, ambas.
- **D4.** Préstamos en perfil Rápido: tope **50 %**.
- **D5.** Se construye el **módulo completo de una vez**: definir meta → elegir perfil → el sistema presenta estadísticas y el plan con el que propone comenzar → Ramón pulsa **Iniciar** → operación autónoma. Incluye botón **Cancelar operación** con *Vender todo* o *Mover al repositorio*.
