# Reporte Fase 29a — pulido visual de Grids, Cuenta y Modelos

## Alcance y resultado

Se hicieron cambios de presentación en Grids, detalle de grid y Cuenta, además de formato compartido. No se cambió el motor ni se probaron Testnet, datos reales de S1 o un móvil físico.

## A–H

| Ítem | Estado | Implementación |
|---|---|---|
| A. Padding de tarjetas | HECHO | Una regla compartida aplica 16 px a `.loans-summary-line`, `.loan-pairs-panel`, `.level-adjust-panel`, `.measured-dust-block` y `.models-section` en [frontend/styles.css](frontend/styles.css:6). |
| B. Resúmenes de préstamos | HECHO | Tarjetas por cohorte y detalles plegables en [frontend/grids.js](frontend/grids.js:241) y [frontend/grids.js](frontend/grids.js:275). La fila de Vigilancia ahora tiene cinco celdas, igual que su cabecera, en [frontend/grids.js](frontend/grids.js:250). |
| C. Cuenta | HECHO | "Sin valorar" usa detalles cerrados y tabla ordenada; balances usan el formato USDT; `unassigned` se muestra neutral; credenciales y botones de orden tienen estilo en [frontend/cuenta.js](frontend/cuenta.js:46) y [frontend/cuenta.css](frontend/cuenta.css:4). |
| D. Acciones | HECHO | Cerrar conserva `data-grid-action="close"` con estilo de peligro; la guía del interruptor global conserva su texto en [frontend/grids.js](frontend/grids.js:362) y [frontend/grids.js](frontend/grids.js:390). |
| E. Formato numérico | HECHO | `formatUsdt` pone el signo antes del símbolo y normaliza negativos que redondean a cero en [frontend/app.js](frontend/app.js:63). Cuenta reutiliza ese formato. |
| F. Idioma y etiquetas | HECHO | `statusLabel` compartido, nuevos nombres de eventos y traducción de `policy_adjust` en [frontend/app.js](frontend/app.js:64), [grid/status_view.py](grid/status_view.py:88) y [frontend/grids.js](frontend/grids.js:347). Se corrigieron tildes en mensajes de grids. |
| G. Alineación y gráficas | HECHO | Iconos alineados, resumen diario compacto para hasta dos días, equity sin gráfica cuando faltan puntos y chips de escalera en [frontend/styles.css](frontend/styles.css:6), [frontend/cuenta.js](frontend/cuenta.js:36) y [frontend/grids.js](frontend/grids.js:296). |
| H. Antigüedad del dato | HECHO | Etiqueta en Grids y Ganancias de Cuenta; usa `as_of`, `updated_at` o `checked_at` cuando están disponibles y se actualiza cada minuto sin recargar en [frontend/app.js](frontend/app.js:66), [frontend/grids.js](frontend/grids.js:483) y [frontend/cuenta.js](frontend/cuenta.js:59). |

## Verificación

- `node --check` pasó en los seis archivos de `frontend/*.js`.
- `tests/ui/test_frontend_syntax.py` verifica cada archivo y confirma que `node --check` rechaza una mutación que elimina una llave.
- `tests/ui/test_frontend_format.py` verifica negativos, cero, redondeo a cero, nulos y `NaN`.
- Suite completa: `ASPLE_OFFLINE=1 .\\venv\\Scripts\\python.exe -m pytest tests -m "not live" -q --basetemp=.pytest-29a-final` — **1317 passed, 0 failed, 1 skipped, 18 deselected**.
- Comprobación enfocada posterior de sintaxis, formato y capturas: **13 passed**.
- Capturas antes/después en `.pytest-29a-shots/`, a 1440×1000 y 390×844. La captura posterior de Cuenta muestra conexión, balance, ganancias y grids; no se quedó en "Cargando cuenta...". Los datos son de fixture. En Modelos siguen visibles estados "Not Found"/"error de red" de la fixture y algunos paneles indican carga; esos datos no se consideran verificados.
- No se verificó en S1 con datos reales ni en un móvil físico.

## Textos de anclaje actualizados deliberadamente

| Archivo | Texto anterior | Texto nuevo |
|---|---|---|
| `frontend/grids.js` | `El precio esta bajo todos los niveles de venta con posicion abierta` | `El precio está bajo todos los niveles de venta con posición abierta` |
| `frontend/grids.js` | `perdida maxima` / `estan configurados` | `pérdida máxima` / `están configurados` |
| `frontend/grids.js` | `Ultimas operaciones` / `Duracion` | `Últimas operaciones` / `Duración` |
| `tests/ui/test_frontend_format.py` | Solo cubría un USDT positivo | Añade negativos, cero, nulo, `NaN` y negativo que redondea a cero |

## Archivos incluidos

- `frontend/app.js`
- `frontend/cuenta.css`
- `frontend/cuenta.js`
- `frontend/grids.js`
- `frontend/styles.css`
- `grid/status_view.py` (solo nuevas entradas de etiquetas de eventos)
- `tests/ui/test_frontend_format.py`
- `tests/ui/test_frontend_syntax.py`
- `REPORTE_FASE29A.md`
