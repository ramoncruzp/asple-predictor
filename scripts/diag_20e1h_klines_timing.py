from __future__ import annotations

import math
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from data.binance_client import BinanceClient

SYMBOL = "ADAUSDT"
INTERVAL = "5m"
ASSUMED_SECONDS_PER_PAGE = 0.4


def main() -> None:
    try:
        client = BinanceClient(None, None)
    except Exception as exc:
        print(f"NO MEDIDO | inicializaci\u00f3n cliente p\u00fablico: {type(exc).__name__}: {exc}")
        for days in (30, 90):
            pages = math.ceil(days * 24 * 12 / 1000)
            print(f"ESTIMACI\u00d3N no medida | {days}d | p\u00e1ginas={pages} | supuesto=0.4 s/p\u00e1gina | total={pages*ASSUMED_SECONDS_PER_PAGE:.1f} s | por p\u00e1gina=0.4 s")
        return
    for days in (30, 90):
        calls = 0
        original = client.client.get_klines
        def counted(*args, **kwargs):
            nonlocal calls
            calls += 1
            return original(*args, **kwargs)
        client.client.get_klines = counted
        started = time.perf_counter()
        try:
            frame = client.get_historical_klines(SYMBOL, INTERVAL, lookback_days=days)
        except Exception as exc:
            elapsed = time.perf_counter() - started
            expected_pages = math.ceil(days * 24 * 12 / 1000)
            print(f"NO MEDIDO | {days}d | llamadas observadas={calls} | error={type(exc).__name__}: {exc}")
            print(f"ESTIMACI\u00d3N no medida | p\u00e1ginas={expected_pages} | supuesto=0.4 s/p\u00e1gina | total={expected_pages*ASSUMED_SECONDS_PER_PAGE:.1f} s | por p\u00e1gina=0.4 s | tiempo hasta error={elapsed:.2f} s")
        else:
            elapsed = time.perf_counter() - started
            pages = calls
            print(f"MEDIDO | {days}d | p\u00e1ginas={pages} | velas={len(frame)} | total={elapsed:.3f} s | por p\u00e1gina={elapsed/pages if pages else 0:.3f} s")
        finally:
            client.client.get_klines = original


if __name__ == "__main__":
    main()
