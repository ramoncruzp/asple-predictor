# Reporte Fase 37 — suite completa tras Fases 32–36

## Corridas

Las dos corridas usaron `ASPLE_OFFLINE=1` y `pytest tests -m "not live" -q` con un `--basetemp` nuevo bajo `%TEMP%`.

| Corrida | Resultado | Tiempo pytest | Tiempo de comando | Warnings |
|---|---|---:|---:|---:|
| Inicial | 1400 passed, 1 failed, 1 skipped, 18 deselected | 440.97 s | 444.79 s | 53 |
| Final | 1401 passed, 0 failed, 1 skipped, 18 deselected | 419.89 s | 423.79 s | 53 |

## Fallos y clasificación

| Prueba | Mensaje y causa | Categoría | Arreglo |
|---|---|---|---|
| `tests/test_frontend_encoding.py::test_frontend_user_text_has_no_question_mark_mojibake` | El detector `[A-Za-z]\\?[a-z]` interpretó el delimitador de query legítimo `summary?symbol=` en `frontend/global_flags.js:16` como posible texto dañado. | C — entorno/harness | Se añadió esa secuencia exacta a la lista estrecha de excepciones en `tests/test_frontend_encoding.py:13`. El detector y la prueba que muta una fuente para verificar que el guard rechaza corrupción permanecen activos. Archivo afectado: `tests/test_frontend_encoding.py:13`. |

Conteo por categoría: A 0, B 0, C 1, D 0. No hubo regresiones B; por ello no aplica mutación B ni SHA. No quedaron fallos sin clasificar.

Después del ajuste, `tests/test_frontend_encoding.py` pasó con **2 passed**. La corrida completa final pasó con los conteos indicados arriba.

## Sintaxis JavaScript

`node --check` se ejecutó individualmente en los ocho archivos `frontend/*.js`; los ocho pasaron: `app.js`, `battle.js`, `cuenta.js`, `global_flags.js`, `grids.js`, `lab.js`, `models.js` y `scanner.js`.

## Listo para desplegar

**Sí.** La corrida final especificada está verde y no quedan fallos categoría D que afecten al monitor, al engine o a la apertura de grids. Las 18 pruebas marcadas `live` fueron deseleccionadas por el comando requerido y no se ejecutaron.
