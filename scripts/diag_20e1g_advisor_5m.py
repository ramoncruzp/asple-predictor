from __future__ import annotations

import time
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
from decimal import Decimal
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
CACHE = ROOT / "data" / "cache" / "vol_train"
from data.exchange_filters import SymbolFilters
PEPE_FILTERS = SymbolFilters(Decimal("0.000000001"), Decimal("0.000000001"),
    Decimal("1000000"), Decimal("1"), Decimal("1"), Decimal("1000000000000000"),
    Decimal("5"), True, 200)
CAPITAL = 1000.0
FEE_PCT = 0.1
DAYS = 90
RANGES = {
    "ADAUSDT": (0.2336, 0.2762, 18),
    "PEPEUSDT": (0.000003741, 0.000004450, 19),
}

from api.routes import grid_advisor, volatility as volatility_route
from api.routes.grid_advisor import _simulation_window
from grid.sim.data import CandleData, ewma_sigma_24h
from grid.sim.runner import FILTERS, run_simulation


def read_window(symbol: str, interval: str) -> pd.DataFrame:
    frame = pd.read_csv(CACHE / f"{symbol.lower().removesuffix('usdt')}_{interval}.csv", parse_dates=["timestamp"])
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True)
    frame = frame.sort_values("timestamp").reset_index(drop=True)
    cutoff = frame.timestamp.iloc[-1] - pd.Timedelta(days=DAYS)
    return frame.loc[frame.timestamp >= cutoff].reset_index(drop=True)


def candle_data(frame: pd.DataFrame) -> CandleData:
    timestamps = pd.to_datetime(frame.timestamp, utc=True).astype("int64").to_numpy() // 1_000_000_000
    return CandleData(timestamps.astype(np.int64), *(frame[name].to_numpy(dtype=float)
                      for name in ("open", "high", "low", "close")), gaps=0)


def measure_pair(symbol: str, low: float, high: float, n: int) -> list[dict]:
    hourly = read_window(symbol, "1h")
    five = read_window(symbol, "5m")
    hourly_window, _ = _simulation_window(hourly, low, high, DAYS)
    five_window, _ = _simulation_window(five, low, high, DAYS)
    if hourly_window is None or five_window is None:
        raise RuntimeError(f"No hay ventana dentro del rango de {symbol}")
    sigma_full = ewma_sigma_24h(five["close"].to_numpy(dtype=float), halflife_h=72)
    sigma_window = sigma_full[five_window.index.to_numpy(dtype=int)]
    outputs = []
    for label, window, resync, sigma in (
        ("Anterior 1h", hourly_window, 3, None),
        ("Nueva 5m", five_window, 3, sigma_window),
    ):
        candles = candle_data(window)
        for strategy in ("simple", "smart"):
            filters = PEPE_FILTERS if symbol == "PEPEUSDT" else FILTERS
            result = run_simulation(candles, strategy=strategy, n=n, capital=CAPITAL,
                low=low, high=high, fee_pct=FEE_PCT, resync_candles=resync,
                sigma_values=sigma, filters=filters)
            metrics = result["metrics"]
            outputs.append({
                "symbol": symbol, "route": label, "strategy": strategy,
                "candles": len(candles.close),
                "cycles": metrics["cycles_completed"],
                "pnl": metrics["pnl_total_net_usdt"],
                "fees": metrics["fees_usdt"],
                "drawdown": metrics["max_drawdown_pct"],
                "buy_hold": metrics["buy_hold_pnl_usdt"],
                "stop_loss": sum(event.get("type") == "STOP_LOSS" for event in result["events"]),
            })
    return outputs


class CachedClient:
    def __init__(self, frames: dict[str, pd.DataFrame], fail_5m: bool = False):
        self.frames = frames
        self.fail_5m = fail_5m

    def get_historical_klines(self, symbol, interval, lookback_days=90):
        if interval == "5m" and self.fail_5m:
            raise RuntimeError("benchmark del fallback horario")
        return self.frames[symbol, interval].copy()


def endpoint_seconds(symbol: str, frames: dict, fail_5m: bool) -> tuple[float, str]:
    app = FastAPI()
    app.include_router(grid_advisor.router, prefix="/api/grid")
    app.state.client = CachedClient(frames, fail_5m)
    app.state.settings = SimpleNamespace(scanner_fee_pct=FEE_PCT,
        scanner_timeout_seconds=5, grid_monitor_interval=900)
    class CachedScan:
        settings = app.state.settings
        def clock(self):
            return 1.0
        def _market(self, requested_symbol, capital, deadline):
            return {}, (PEPE_FILTERS if requested_symbol == "PEPEUSDT" else FILTERS)
    app.state.grid_scan_service = CachedScan()
    app.state.prediction_loop = SimpleNamespace(latest={})
    old_forecast = volatility_route.forecast
    volatility_route.forecast = lambda *_args, **_kwargs: {"forecasts": []}
    try:
        with TestClient(app) as client:
            started = time.perf_counter()
            response = client.get("/api/grid/recommend", params={
                "symbol": symbol, "capital": CAPITAL, "risk": "medium", "days": DAYS,
                "range_mode": "estructural"})
            elapsed = time.perf_counter() - started
            if response.status_code != 200:
                raise RuntimeError(f"/recommend devolvi\u00f3 {response.status_code}: {response.text[:300]}")
            payload = response.json()["simulations"]
            return elapsed, payload["resolution"]
    finally:
        volatility_route.forecast = old_forecast


def main() -> None:
    results = []
    frames = {}
    for symbol, (low, high, n) in RANGES.items():
        hourly, five = read_window(symbol, "1h"), read_window(symbol, "5m")
        frames[symbol, "1h"], frames[symbol, "5m"] = hourly, five
        results.extend(measure_pair(symbol, low, high, n))
    timings = {}
    for symbol in RANGES:
        old_seconds, old_resolution = endpoint_seconds(symbol, frames, True)
        new_seconds, new_resolution = endpoint_seconds(symbol, frames, False)
        timings[symbol] = (old_seconds, old_resolution, new_seconds, new_resolution)
    for row in results:
        print("| {symbol} | {route} | {strategy} | {cycles} | {pnl:.2f} | {fees:.2f} | {drawdown:.2f}% | {buy_hold:.2f} | {stop_loss} |".format(**row))
    for symbol, (old, old_res, new, new_res) in timings.items():
        print(f"TIMING | {symbol} | 90 d\u00edas | ruta anterior={old:.3f}s ({old_res}) | ruta nueva={new:.3f}s ({new_res})")
    ada = {row["strategy"]: row["pnl"] for row in results if row["symbol"] == "ADAUSDT" and row["route"] == "Nueva 5m"}
    pepe = {row["strategy"]: row["pnl"] for row in results if row["symbol"] == "PEPEUSDT" and row["route"] == "Nueva 5m"}
    print(f"CONCLUSION | ADA Smart-Simple={ada['smart']-ada['simple']:.2f} USDT; PEPE Smart-Simple={pepe['smart']-pepe['simple']:.2f} USDT")
    print("COMPARACION_20E1E | ADA Smart -33.02 / Simple +66.34 USDT")


if __name__ == "__main__":
    main()
