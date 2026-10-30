#!/usr/bin/env python3
"""Mide generación y FastSim con una biblioteca FHS ya construida."""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from data.market_store import CandleStore
from grid.sim.runner import FILTERS
from research.scenarios.fastsim import simulate_simple
from research.scenarios.generator import generate


def _rss_mb():
    try:
        import psutil
        info = psutil.Process().memory_info()
        return getattr(info, "peak_wset", info.rss) / 1048576
    except ImportError:
        if os.name == "posix":
            import resource
            value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            return value / (1048576 if sys.platform == "darwin" else 1024)
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes
            class Counters(ctypes.Structure):
                _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.GetCurrentProcess.restype = wintypes.HANDLE
            process = kernel.GetCurrentProcess()
            psapi = ctypes.WinDLL("psapi", use_last_error=True)
            psapi.GetProcessMemoryInfo.argtypes = (wintypes.HANDLE,
                ctypes.POINTER(Counters), wintypes.DWORD)
            psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
            counters = Counters()
            counters.cb = ctypes.sizeof(counters)
            if psapi.GetProcessMemoryInfo(process, ctypes.byref(counters), counters.cb):
                return counters.PeakWorkingSetSize / 1048576
        return None


def benchmark(*, library_directory, symbol, price, sigma_today, sigma_long,
        n_scenarios=1000, batch_size=None, filters=None):
    results = []
    for days in (20, 45):
        started = time.perf_counter()
        fills = 0
        count = 0
        width = max(float(sigma_today), 1e-4) * np.sqrt(days * 24) * 2
        low, high = price * np.exp(-width), price * np.exp(width)
        for batch in generate([symbol], horizon_days=days, n_scenarios=n_scenarios,
                seed=43, price_now=price, sigma_today_24h=sigma_today,
                sigma_long_24h=sigma_long, batch_size=batch_size,
                library_directory=library_directory):
            candles = batch["symbols"][symbol]
            result = simulate_simple({name: candles[name] for name in ("open", "high", "low", "close")},
                n_levels=6, capital=1000, low=low, high=high, filters=filters or FILTERS,
                initial_timestamp=1_700_000_100, pack_curves=False)
            fills += int(result["buys"].sum() + result["sells"].sum())
            count += len(result["buys"])
        results.append({"days": days, "seconds": time.perf_counter() - started,
            "fills_per_scenario": fills / max(1, count), "scenarios": count,
            "rss_max_mb": _rss_mb()})
    return results


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--library-dir", default="data/derived/scenario_lib")
    parser.add_argument("--escenarios", type=int, default=1000)
    args = parser.parse_args(argv)
    if args.escenarios <= 0:
        parser.error("--escenarios debe ser positivo")
    library_path = Path(args.library_dir) / f"{args.symbol.upper()}.npz"
    if not library_path.is_file():
        raise FileNotFoundError(f"No existe {library_path}; construye antes la biblioteca FHS")
    store = CandleStore(args.db)
    data = store.load(args.symbol, "5m")
    if len(data.close) < 288 * 30:
        raise ValueError("se requieren al menos 30 días de velas")
    returns = np.diff(np.log(data.close))
    lam = .97 ** (1 / 12)
    variance = float(np.mean(returns[:30 * 288] ** 2))
    for value in returns[30 * 288:]:
        variance = lam * variance + (1 - lam) * float(value * value)
    sigma_today = float(np.sqrt(max(variance, 1e-16) * 288))
    days = np.asarray(data.timestamp // 86400)
    daily_sigma = []
    for day in np.unique(days)[-180:]:
        values = returns[1:][days[1:] == day]
        if len(values) == 287:
            daily_sigma.append(float(np.sqrt(np.sum(values * values))))
    sigma_long = float(np.median(daily_sigma)) if daily_sigma else sigma_today
    results = benchmark(library_directory=args.library_dir, symbol=args.symbol.upper(),
        price=float(data.close[-1]), sigma_today=sigma_today, sigma_long=sigma_long,
        n_scenarios=args.escenarios, filters=store.filters(args.symbol))
    for row in results:
        print(f"VELOCIDAD symbol={args.symbol.upper()} días={row['days']} "
            f"escenarios={row['scenarios']} segundos={row['seconds']:.3f} "
            f"fills_promedio={row['fills_per_scenario']:.3f} rss_max_mb={row['rss_max_mb']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
