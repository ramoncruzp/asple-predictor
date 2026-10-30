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
from collections import Counter
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from data.market_store import market_db_path
from data.exchange_filters import SymbolFilters
from grid.sim.data import CandleData
from grid.sim.meta_portfolio import evaluate_portfolio, pack_curve
from grid.sim.runner import run_simulation
from research.scenarios.fastsim import simulate_bot, simulate_simple
from grid.sim.meta_bot import run_meta_bot
from grid.sim.runner import FILTERS


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


def _gain_error(reference, fast_pnl, capital):
    """Compara la misma ganancia, liquidando el inventario final del replay."""
    reference_pnl = (float(reference["metrics"]["equity_final_after_repository_liquidation"])
        - float(capital))
    return abs(float(fast_pnl) - reference_pnl), reference_pnl


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
    mismatch_types = Counter()
    completed = 0
    invalid = Counter()
    attempts = 0
    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as conn, _RssPeak() as rss:
        symbols = [row[0] for row in conn.execute(
            "SELECT symbol FROM symbols WHERE active=1 AND role='operar' ORDER BY symbol")]
        if not symbols:
            raise RuntimeError("market_data.db no contiene símbolos activos role=operar")
        while completed < cases and attempts < cases * 5:
            case = completed + 1
            attempts += 1
            selected = _window(conn, rng.choice(symbols), rng, bars)
            if selected is None:
                invalid["sin ventana continua"] += 1
                continue
            symbol, data = selected
            initial = float(data.close[0])
            n_levels = rng.choice((4, 6, 10))
            capital = 1000.0 if completed % 2 == 0 else rng.uniform(20, 100)
            margin = rng.choice((.003, .005, .007))
            width = max(rng.uniform(.08, .24), margin * (n_levels - 1) * 2)
            low, high = initial * (1 - width), initial * (1 + width)
            loss_pct = rng.choice((None, 15., 20.))
            trace = []
            scenario = {name: np.asarray(getattr(data, name), np.float64)[None, :]
                for name in ("open", "high", "low", "close")}
            row_filter = conn.execute("SELECT raw_json FROM symbol_filters WHERE symbol=?", (symbol,)).fetchone() \
                if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='symbol_filters'").fetchone() else None
            if row_filter:
                filters = SymbolFilters.from_symbol_info(__import__("json").loads(row_filter[0]))
            else:
                filters = FILTERS
                print(f"PARIDAD aviso moneda={symbol} sin filtros guardados; se usa FILTERS")
            is_bot = completed % 4 == 3
            try:
                if is_bot:
                    offset = rng.choice((.005, .015, .03))
                    buy_price, sell_price = initial * (1 - offset), initial * (1 + offset)
                    capital = rng.uniform(10, 30)
                    reference_bot = run_meta_bot(data, capital=capital, buy_price=buy_price,
                        sell_price=sell_price, target_usdt=None, filters=filters)
                    fast_bot = simulate_bot(scenario, capital=capital, buy_price=buy_price,
                        sell_price=sell_price, filters=filters,
                        initial_timestamp=int(data.timestamp[0]))
                    checks = {
                        "llenados": ((reference_bot["bought"], reference_bot["sold"]),
                            (bool(fast_bot["buys"][0]), bool(fast_bot["sells"][0]))),
                        "ganancia": (reference_bot["pnl_usdt"], float(fast_bot["pnl_usdt"][0])),
                    }
                    for name, (ref_value, fast_value) in checks.items():
                        failed = (ref_value != fast_value if name == "llenados" else
                            abs(float(ref_value) - float(fast_value)) > max(.02, abs(float(ref_value)) * .005))
                        if failed:
                            mismatches += 1
                            mismatch_types[name] += 1
                            print(f"PARIDAD_DIFERENCIA caso={case} moneda={symbol} chequeo={name} referencia={ref_value} rápido={fast_value}")
                    completed += 1
                    continue
                reference = run_simulation(data, strategy="simple", n=n_levels, capital=capital,
                    low=low, high=high, params={}, fee_pct=.1, filters=filters,
                    meta_max_loss_pct=loss_pct, meta_portfolio_piece=True,
                    trace_callback=trace.append)
                fast = simulate_simple(scenario, n_levels=n_levels, capital=capital, low=low,
                    high=high, filters=filters, max_loss_pct=loss_pct, target_usdt=10,
                    initial_timestamp=int(data.timestamp[0]))
            except ValueError as exc:
                invalid[str(exc)] += 1
                continue
            events = reference["events"]
            fills = (sum(event["type"] == "BUY_FILLED" for event in events),
                sum(event["type"] == "SELL_FILLED" for event in events))
            expected = (int(fast["buys"][0]), int(fast["sells"][0]))
            pnl_error, reference_pnl = _gain_error(reference,
                fast["pnl_usdt"][0], capital)
            ref_loss = next((event for event in events if event["type"] == "META_LOSS_CUT"), None)
            cut_matches = bool(fast["loss_cut"][0]) == (ref_loss is not None)
            if ref_loss is not None:
                cut_matches = cut_matches and int(fast["loss_cut_candle"][0]) == (
                    int(ref_loss["ts"]) - int(data.timestamp[0])) // 300
            ref_curve = pack_curve([row["timestamp"] for row in trace],
                [float(row["balances"]["USDT"]) for row in trace],
                [float(row["equity_incl_dust"]) for row in trace], capital)
            reference_portfolio = evaluate_portfolio([ref_curve], capital_initial=capital,
                target_usdt=10, max_loss_pct=loss_pct)
            fast_reached = (
                int(fast["target_candle"][0]) >= 0 and
                (int(fast["loss_cut_candle"][0]) < 0 or
                 int(fast["target_candle"][0]) < int(fast["loss_cut_candle"][0])))
            target_matches = reference_portfolio["reached"] == fast_reached
            trace_matches = (len(trace) == fast["cash_curve"].shape[1] and
                np.allclose([float(row["balances"]["USDT"]) for row in trace], fast["cash_curve"][0],
                    rtol=0, atol=.02) and
                np.allclose([float(row["equity_incl_dust"]) for row in trace], fast["equity_curve"][0],
                    rtol=0, atol=.02))
            completed += 1
            checks = {
                "llenados": (fills, expected),
                "ganancia": (reference_pnl, float(fast["pnl_usdt"][0])),
                "corte": (bool(ref_loss), bool(fast["loss_cut"][0])),
                "meta": (reference_portfolio["reached"], bool(fast_reached)),
                "curva": (bool(trace_matches), True),
                "ciclos": (reference["metrics"]["cycles_completed"], int(fast["cycles"][0])),
            }
            for name, (ref_value, fast_value) in checks.items():
                failed = (ref_value != fast_value if name != "ganancia" else
                    abs(float(ref_value) - float(fast_value)) > max(.02, abs(float(ref_value)) * .005))
                if failed or (name == "corte" and not cut_matches) or (name == "meta" and not target_matches):
                    mismatches += 1
                    mismatch_types[name] += 1
                    print(f"PARIDAD_DIFERENCIA caso={case} moneda={symbol} chequeo={name} referencia={ref_value} rápido={fast_value}")
            if pnl_error > worst["pnl_abs_usdt"]:
                worst = {"pnl_abs_usdt": pnl_error, "symbol": symbol,
                    "n_levels": n_levels, "fills_reference": fills, "fills_fastsim": expected}
            if completed % 10 == 0:
                print(f"PARIDAD caso={completed}/{cases} discrepancias={mismatches}")
    print(f"PARIDAD resumen casos_solicitados={cases} casos_completados={completed} "
        f"discrepancias={mismatches} geometria_invalida={sum(invalid.values())} "
        f"motivo_comun={invalid.most_common(1)} diferencias_por_chequeo={dict(sorted(mismatch_types.items()))} "
        f"peor={worst}")
    print(f"PARIDAD rss_max_mb={rss.peak:.1f}")
    return 1 if mismatches else 0


if __name__ == "__main__":
    raise SystemExit(main())
