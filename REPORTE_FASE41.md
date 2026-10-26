# Reporte Fase 41a — Corrección de señales del Modo Meta

## Resultado

Se corrigieron directamente las señales de volatilidad y dirección del operador Meta, la disponibilidad causal de la señal de toque y la trazabilidad de señales combinadas. No se añadieron interruptores ni cambios de sombra; los pesos y los umbrales permanecen iguales.

## Cambios por parte

- **41a-1, unidades:** `grid/meta_operator.py` convierte `exp(pred_logvol_cal)` de sigma por hora a sigma de 24 h con `sqrt(VOL_FORECAST_HORIZON_H)`, y guarda ambos valores. `grid/meta_learning.py` aplica la misma conversión en `point_in_time_signals`, usado por la reconstrucción histórica. La señal compara sigma 24 h contra `sigma_24h_open` y `break_prob` recibe también sigma 24 h. El diagnóstico incluye la unidad y sigma por hora.
- **41a-2, dirección:** `grid/meta_learning.py` centraliza `normalize_down_probability`; `model_a_down.probability_up` se registra como P(baja) y se normaliza con signo no positivo. El umbral sigue en 0,60.
- **41a-3, toque:** `grid/meta_operator.py` extrae `_load_candles(symbol)`, reutiliza caché por símbolo y día y lo llama desde replay y desde la señal de toque. Si falla, la alternativa conserva el análisis y la señal se omite con `velas no disponibles: <error>`.
- **41a-4, aprendizaje y diagnóstico:** las muestras nuevas live e históricas se etiquetan `signals_version: 2`; la importación retrospectiva de bitácoras antiguas se marca v1. `extract_learning_features` ignora volatilidad, ruptura y dirección en v1 (incluidas muestras antiguas sin versión), porque quedaron constantes o con signo incorrecto; Scanner, Advisor y toque siguen disponibles. `scripts/diagnostico_senales_meta.py` cuenta señales por `normalized` aunque falte `value`, muestra cobertura por versión y valores normalizados distintos. Las carteras conservan valor ponderado por capital, valores por pieza y diagnósticos por pieza.

## Usos de volatilidad revisados

- `grid/meta_operator.py:_model_snapshot`: sigma horaria convertida a 24 h; ambos valores quedan disponibles para auditoría.
- `grid/meta_operator.py:_apply_intelligence`: la comparación de volatilidad y la llamada a `break_prob` consumen `sigma_24h`.
- `grid/meta_learning.py:point_in_time_signals` y `extract_learning_features`: reconstrucción histórica y normalización usan la misma sigma 24 h.
- Replays y precálculos del operador usan `_evaluate`, que llama al cargador compartido de velas; las magnitudes de volatilidad de entrada del Scanner ya están en `sigma_24h_open`.
- La pantalla Inteligencia presenta pronósticos guardados con su `horizon_h`; no calcula otra sigma. Las filas nuevas de aprendizaje contienen valores ya normalizados con la corrección.
- Fuera de Meta revisé `grid/volatility_provider.py` y `api/routes/volatility.py`: convierten sigma por hora multiplicando por `sqrt(24)` o `sqrt(horizonte)`. `grid/policy.py` recibe sigma 24 h. No encontré otro uso confirmado de sigma horaria tratada como sigma 24 h. `database/db_manager.py:get_vol_history` devuelve el porcentaje exponenciado asociado al horizonte solicitado sin etiquetarlo como sigma 24 h; no se cambió.

## Comparación controlada antes/después

Mismo escenario sintético: precio 108, rango 90–108, sigma por hora 0,04; `p_replay=0,50`; los valores de Scanner, Advisor y toque son iguales y neutrales para aislar los tres defectos. Para dirección se usa P(baja)=0,533, coherente con que en la muestra live observada no superaba 0,533.

| Señal | Antes (24f2ba7) | Después (41a) | Diferencia |
|---|---:|---:|---|
| Volatilidad normalizada (forecast 0,01/h; referencia 0,05/24 h) | −1,000 | −0,020 | +0,980 |
| Probabilidad de ruptura (caso sintético indicado) | 0,0094; señal +0,981 | 0,6070; señal −0,214 | la escala ya no satura al alza |
| Dirección, P(baja)=0,533 | 0,000 | 0,000 | ninguna supera 0,60 |
| Scanner / Advisor / toque | sin cambio | sin cambio | 0 |
| `probability_adjusted`, pesos 0,25 por señal, `p_replay=0,50` | 0,4988 | 0,4854 | −1,35 puntos porcentuales |

Con esas mismas señales sintéticas y `p_replay` entre 0,10 y 0,90, el cambio calculado de probabilidad ajustada está entre −1,35 y −0,47 puntos porcentuales. Si P(baja) llegara a 0,70, dirección añadiría −1 al valor normalizado; con peso 0,25 y base 0,50, el resultado sería 0,4235 (−7,53 puntos frente al antes). Es un ejemplo numérico, no una estimación de rendimiento.

## Pruebas

Corrida dirigida con `ASPLE_OFFLINE=1`, carpeta nueva bajo `%TEMP%`, sin S1, Testnet, `.env` ni BD real:

`tests/test_meta_operator.py tests/test_meta_learning.py tests/test_meta_signal_diagnostics.py` — **94 passed, 4 warnings, 4,35 s**.

Incluye conversión de escala horaria a 24 h en operador e histórico, normalización de volatilidad, entrada a ruptura, signo y umbral de dirección, carga de velas sin caché, features v1/v2, ponderación y diagnósticos de carteras, y diagnóstico normalizado sin valor crudo.

## Pendientes y límites

- Las filas live históricas ya guardadas no se reescriben. Las filas antiguas sin versión se interpretan como v1 y no aportan las señales de volatilidad, ruptura y dirección al entrenamiento.
- La medida de 1153 muestras live y los valores reportados desde S1 son antecedentes proporcionados por Ramón; no se consultó S1 para esta corrección.
- No se ejecutó la suite completa ni se modificaron pantallas, rutas de API, política global Smart o grids normales.
