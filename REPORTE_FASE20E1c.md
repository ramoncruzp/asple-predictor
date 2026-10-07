# Fase 20E-1c - CandleData para el Advisor

## G3 y causa raíz

`grid/sim/runner.py` requiere el dataclass `CandleData` con arreglos NumPy y `gaps`; no acepta un `pandas.DataFrame`. La firma real en `grid/sim/data.py:17-24` es `timestamp, open, high, low, close, gaps`. Antes del arreglo, el Advisor pasaba el DataFrame tras reiniciar su índice y convertir timestamp a segundos. La simulación alcanzaba métricas, donde la Serie de pandas evaluaba `closes[-1]` como etiqueta y lanzaba `KeyError: -1`, precedido por `ValueError: -1 is not in range`.

El flush ejecutado sobre velas sintéticas con la forma real del Advisor obtuvo el mismo error completo para Simple y Smart. En cada traceback, la cadena final es `ValueError: -1 is not in range` -> `KeyError: -1`:

```text
Traceback (most recent call last):
  File "C:\APPS\ASPLE_Predictor\asple-predictor\venv\Lib\site-packages\pandas\core\indexes\range.py", line 414, in get_loc
    return self._range.index(new_key)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^
ValueError: -1 is not in range

The above exception was the direct cause of the following exception:

Traceback (most recent call last):
  File "<stdin>", line 21, in <module>
  File "grid/sim/runner.py", line 494, in run_simulation
    metric = calculate_metrics(equity, float(capital), cells, exchange, closes,
  File "grid/sim/metrics.py", line 25, in calculate_metrics
    "buy_hold_pnl_usdt": float(capital * (closes[-1] / closes[0] - 1)),
                                          ~~~~~~^^^^
  File "pandas/core/series.py", line 1040, in __getitem__
    return self._get_value(key)
  File "pandas/core/series.py", line 1156, in _get_value
    loc = self.index.get_loc(label)
  File "pandas/core/indexes/range.py", line 416, in get_loc
    raise KeyError(key) from err
KeyError: -1
```

Smart emitted the same complete call chain in the one-shot flush:
```text
Traceback (most recent call last):
  File "C:\\APPS\\ASPLE_Predictor\\asple-predictor\\venv\\Lib\\site-packages\\pandas\\core\\indexes\\range.py", line 414, in get_loc
    return self._range.index(new_key)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^
ValueError: -1 is not in range

The above exception was the direct cause of the following exception:

Traceback (most recent call last):
  File "<stdin>", line 21, in <module>
  File "grid/sim/runner.py", line 494, in run_simulation
    metric = calculate_metrics(equity, float(capital), cells, exchange, closes,
  File "grid/sim/metrics.py", line 25, in calculate_metrics
    "buy_hold_pnl_usdt": float(capital * (closes[-1] / closes[0] - 1)),
                                          ~~~~~~^^^^
  File "pandas/core/series.py", line 1040, in __getitem__
    return self._get_value(key)
  File "pandas/core/series.py", line 1156, in _get_value
    loc = self.index.get_loc(label)
  File "pandas/core/indexes/range.py", line 416, in get_loc
    raise KeyError(key) from err
KeyError: -1
```

This is the third symptom of the same type-contract bug: `KeyError: 0` on the old index, `TypeError` converting `Timestamp`, then this `KeyError: -1` after the prior fixes.

## Cambio y datos

`api/routes/grid_advisor.py:275` now creates the same `CandleData` shape as `grid/scan_service.py:129`: `timestamp` as NumPy `int64` Unix seconds; OHLC as NumPy `float64`; `gaps=0`. The redundant `map(lambda ...)` conversión is removed. The metadata is computed from the selected frame before converting it. `grid/sim/runner.py` and `resync_candles` were not changed.

The Advisor requests `ACTIVE_INTERVAL` at `api/routes/grid_advisor.py:178`; `config/models_config.py:11` sets it to `1h`. The regression fixture's DataFrame contains 420 hourly rows; after entering the requested range at row 30 it passes 390 candles (16.2083 days) to the simulation. **NO VERIFICADO:** the number of rows returned by the real historical client; it varies with available history. At the default 90-day request, a complete 1h series would contain about 2160 rows. The runner default remains `resync_candles=3` (`grid/sim/runner.py:80`), equivalent to three hourly candles on this route; it was only documented, not changed.

Synthetic TestClient output, request used for both ADAUSDT and XRPUSDT:
- ADAUSDT Simple: `cycles_completed=61`, `pnl_total_net_usdt=24.92849415702449`, `max_drawdown_pct=4.256602511562402`.
- ADAUSDT Smart: `cycles_completed=9`, `pnl_total_net_usdt=-0.17615370144460485`, `max_drawdown_pct=1.1465368230220823`.
- XRPUSDT Simple: `cycles_completed=61`, `pnl_total_net_usdt=24.92849415702449`, `max_drawdown_pct=4.256602511562402`.
- XRPUSDT Smart: `cycles_completed=9`, `pnl_total_net_usdt=-0.17615370144460485`, `max_drawdown_pct=1.1465368230220823`.

The TestClient regression `tests/test_grid_advisor.py:334` uses raw synthetic klines through `BinanceClient._klines_to_dataframe`, makes the price enter the range after the first 30 rows, does not mock `run_simulation`, and observes its arguments via Python profiling. It asserts four calls received `CandleData` with int64 timestamps/float64 OHLC, selected start after the first timestamp, numeric metrics for both strategies and symbols, and no `unavailable` exception.

## Other run_simulation callers

Search of Python call sites found no other DataFrame caller. `grid/scan_service.py:129` already constructs `CandleData`; `scripts/grid_sim.py` uses `load_candles`; `grid/sim/sweep.py` slices to `CandleData`; `target_study.py`, `structure_study.py`, and `calibration.py` likewise pass `CandleData` or slices derived from it. No out-of-scope files were changed.

## Verificación

Before conversión, the one-shot diagnostic printed the complete Simple and Smart tracebacks above. After conversión, the focused regression passed: `1 passed, 2 warnings in 1.84s`.

G3 mutation removed the `CandleData` construction and passed the DataFrame again. The regression failed on the behavioral assertion with four captured arguments of type `DataFrame`; mutation was detected and restored byte-for-byte.

No-UI command:
```powershell
.\venv\Scripts\python.exe -m pytest tests -m "not live" --ignore=tests/ui -q --basetemp .pytest_tmp/20e1c-final-no-ui
```
```text
939 passed, 1 skipped, 18 deselected, 38 warnings in 65.99s (0:01:05)
```

UI command, run after no-UI:
```powershell
.\venv\Scripts\python.exe -m pytest tests/ui -m "not live" -q --basetemp .pytest_tmp/20e1c-final-ui
```
```text
102 passed, 2 warnings in 73.61s (0:01:13)
```
Both results match the 20E-1b baseline: no new failures.

## NO VERIFICADO

Real ADA/XRP candle counts, real-market simulation metrics, and why the symptom appeared first for ADA were not checked; no network data was requested. No stage, commit or push.
