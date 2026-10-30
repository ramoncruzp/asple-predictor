#!/usr/bin/env python3
"""Compara fastsim con el replay de producción en ventanas históricas."""
from __future__ import annotations

import argparse
import os
import random
import sqlite3
import sys
import threading
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from data.market_store import market_db_path
from grid.sim.data import CandleData
from grid.sim.meta_portfolio import evaluate_portfolio, pack_curve
from grid.sim.runner import run_simulation
from research.scenarios.fastsim import simulate_simple


class _RssPeak:
    def __init__(self):
        self.peak = 0.0
        self.stop = threading.Event()
        self.thread = None

    def _sample(self):
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
            psapi = ctypes.WinDLL("psapi", use_last_error=True)
            psapi.GetProcessMemoryInfo.argtypes = (wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD)
            psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
            handle = kernel.GetCurrentProcess()

            def current_peak():
                counters = Counters()
                counters.cb = ctypes.sizeof(counters)
                if not psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
                    return 0.0
                return counters.PeakWorkingSetSize / (1024 * 1024)
        else:
            import resource

            def current_peak():
                value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
                return value / (1024 * 1024) if os.uname().sysname == "Darwin" else value / 1024
        while not self.stop.is_set():
            self.peak = max(self.peak, current_peak())
            self.stop.wait(.01)

    def __enter__(self):
        self.thread = threading.Thread(target=self._sample, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *_):
        self.stop.set()
        self.thread.join()


def _window(conn, symbol, rng, bars):
    row = conn.execute("SELECT id FROM symbols WHERE symbol=? AND active=1 AND role='operar'",
        (symbol,)).fetchone()
    if row is None:
        return None
    count = conn.execute("SELECT COUNT(*) FROM candles WHERE symbol_id=? AND interval='5m'",
        (row[0],)).fetchone()[0]
    if count < bars:
        return None
    offset = rng.randrange(0, count - bars + 1)
    rows = conn.execute("SELECT open_ts,open,high,low,close FROM candles WHERE symbol_id=? "
        "AND interval='5m' ORDER BY open_ts LIMIT ? OFFSET ?", (row[0], bars, offset)).fetchall()
    if len(rows) != bars or any(int(b[0]) - int(a[0]) != 300 for a, b in zip(rows, rows[1:])):
        return None
    return symbol, CandleData(np.asarray([r[0] for r in rows], np.int64),
        *(np.asarray([r[i] for r in rows], np.float64) for i in range(1, 5)), 0)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True)
    parser.add_argument("--casos", type=int, default=200)
    parser.add_argument("--semilla", type=int, default=43)
    args = parser.parse_args(argv)
    if args.casos <= 0:
        parser.error("--casos debe ser positivo")
    if os.name == "posix":
        try:
            os.nice(10)
        except OSError:
            pass
    path = market_db_path(args.db)
    if not path.is_file():
        raise FileNotFoundError(path)
    rng = random.Random(args.semilla)
    cases = args.casos
    bars = 20 * 288
    worst = {"pnl_abs_usdt": 0.0}
    mismatches = 0
    completed = 0
    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as conn, _RssPeak() as rss:
        symbols = [row[0] for row in conn.execute(
            "SELECT symbol FROM symbols WHERE active=1 AND role='operar' ORDER BY symbol")]
        if not symbols:
            raise RuntimeError("market_data.db no contiene símbolos activos role=operar")
        for case in range(cases):
            selected = None
            for _ in range(20):
                selected = _window(conn, rng.choice(symbols), rng, bars)
                if selected is not None:
                    break
            if selected is None:
                continue
            symbol, data = selected
            initial = float(data.close[0])
            n_levels = rng.choice((4, 6, 10))
            width = rng.uniform(.08, .24)
            low, high = initial * (1 - width), initial * (1 + width)
            loss_pct = rng.choice((None, 15., 20.))
            trace = []
            scenario = {name: np.asarray(getattr(data, name), np.float64)[None, :]
                for name in ("open", "high", "low", "close")}
            try:
                reference = run_simulation(data, strategy="simple", n=n_levels, capital=1000,
                    low=low, high=high, params={}, fee_pct=.1, resync_candles=3,
                    meta_max_loss_pct=loss_pct, meta_portfolio_piece=True,
                    trace_callback=trace.append)
                fast = simulate_simple(scenario, n_levels=n_levels, capital=1000, low=low,
                    high=high, max_loss_pct=loss_pct, target_usdt=10,
                    initial_timestamp=int(data.timestamp[0]))
            except ValueError:
                continue
            events = reference["events"]
            fills = (sum(event["type"] == "BUY_FILLED" for event in events),
                sum(event["type"] == "SELL_FILLED" for event in events))
            expected = (int(fast["buys"][0]), int(fast["sells"][0]))
            pnl_error = abs(float(fast["pnl_usdt"][0]) -
                (reference["metrics"]["equity_final_after_repository_liquidation"] - 1000))
            ref_loss = next((event for event in events if event["type"] == "META_LOSS_CUT"), None)
            cut_matches = bool(fast["loss_cut"][0]) == (ref_loss is not None)
            if ref_loss is not None:
                cut_matches = cut_matches and int(fast["loss_cut_candle"][0]) == int(ref_loss["ts"]) // 300
            ref_curve = pack_curve([row["timestamp"] for row in trace],
                [float(row["balances"]["USDT"]) for row in trace],
                [float(row["equity_incl_dust"]) for row in trace], 1000)
            reference_portfolio = evaluate_portfolio([ref_curve], capital_initial=1000,
                target_usdt=10, max_loss_pct=loss_pct)
            target_matches = reference_portfolio["reached"] == (
                int(fast["target_candle"][0]) >= 0 and
                (int(fast["loss_cut_candle"][0]) < 0 or
                 int(fast["target_candle"][0]) < int(fast["loss_cut_candle"][0])))
            trace_matches = (len(trace) == fast["cash_curve"].shape[1] and
                np.allclose([float(row["balances"]["USDT"]) for row in trace], fast["cash_curve"][0],
                    rtol=0, atol=.02) and
                np.allclose([float(row["equity_incl_dust"]) for row in trace], fast["equity_curve"][0],
                    rtol=0, atol=.02))
            completed += 1
            if (fills != expected or pnl_error > max(.02, abs(reference["metrics"]["pnl_total_net_usdt"]) * .005)
                    or not cut_matches or not target_matches or not trace_matches
                    or int(fast["cycles"][0]) != reference["metrics"]["cycles_completed"]):
                mismatches += 1
            if pnl_error > worst["pnl_abs_usdt"]:
                worst = {"pnl_abs_usdt": pnl_error, "symbol": symbol,
                    "n_levels": n_levels, "fills_reference": fills, "fills_fastsim": expected}
            if (case + 1) % 10 == 0:
                print(f"PARIDAD caso={case+1}/{cases} discrepancias={mismatches}")
    print(f"PARIDAD resumen casos_solicitados={cases} casos_completados={completed} "
        f"discrepancias={mismatches} peor={worst}")
    print(f"PARIDAD rss_max_mb={rss.peak:.1f}")
    return 1 if mismatches or completed < cases else 0


if __name__ == "__main__":
    raise SystemExit(main())
