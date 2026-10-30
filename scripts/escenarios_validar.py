#!/usr/bin/env python3
"""Valida escenarios FHS contra realidad y baseline historico v1."""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from data.market_store import market_db_path
from research.scenarios.validate import validate_scenarios


def _rss_max_mb():
    if os.name == "posix":
        try:
            import resource
            value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            return value / 1024 if sys.platform == "darwin" else value / (1024 * 1024)
        except (ImportError, AttributeError):
            return None
    try:
        import psutil
        info = psutil.Process().memory_info()
        peak = getattr(info, "peak_wset", None)
        return peak / (1024 * 1024) if peak is not None else info.rss / (1024 * 1024)
    except ImportError:
        try:
            import ctypes
            from ctypes import wintypes
            class Counters(ctypes.Structure):
                _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]
            counters = Counters()
            counters.cb = ctypes.sizeof(counters)
            handle = ctypes.windll.kernel32.GetCurrentProcess()
            ok = ctypes.windll.psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb)
            return counters.PeakWorkingSetSize / (1024 * 1024) if ok else None
        except (AttributeError, OSError):
            return None


def render_report(result):
    lines = ["# Validación predictiva de escenarios v2 frente a v1", "",
        f"Horizonte: {result['horizon_days']} días. Fechas evaluadas: {len(result['pairs'])} pares.", "",
        "| Geometría | n | Cobertura P10–P90 | Brier meta v2 | Brier meta v1 | Δ Brier IC 95 % | PIT χ² | Veredicto |",
        "|---|---:|---:|---:|---:|---|---:|---|"]
    for name, row in result.get("summary", {}).items():
        ci = row.get("brier_target_delta_v2_minus_v1_ci95", [None, None])
        lines.append(f"| {name} | {row.get('n', 0)} | {row.get('coverage_p10_p90', 0):.3f} | "
            f"{row.get('brier_target_v2', 0):.4f} | {row.get('brier_target_v1', 0):.4f} | "
            f"[{ci[0]}, {ci[1]}] | {row.get('pit_chi2', 0):.3f} | {row.get('verdict', 'sin datos')} |")
    lines.extend(["", "La corrida es diagnóstica y no habilita decisiones de v2.", ""])
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True)
    parser.add_argument("--meses", type=int, default=12)
    parser.add_argument("--paso-dias", type=int, default=7)
    parser.add_argument("--symbols", nargs="*")
    parser.add_argument("--comision", type=float, default=.1)
    parser.add_argument("--escenarios", type=int, default=1000)
    parser.add_argument("--n-smart", type=int, default=200)
    parser.add_argument("--perfil", choices=("RAPIDO", "PRUDENTE"), default="RAPIDO")
    parser.add_argument("--capital", type=float, default=100.0)
    parser.add_argument("--meta", type=float, default=3.0)
    parser.add_argument("--out", default="reports/escenarios")
    parser.add_argument("--biblioteca", default="data/derived/scenario_lib")
    parser.add_argument("--semilla", type=int, default=43)
    args = parser.parse_args(argv)
    if args.meses < 1 or args.paso_dias < 1 or args.escenarios < 1 or args.comision < 0:
        parser.error("meses, paso y escenarios deben ser positivos; comisión no negativa")
    estimated_dates = int(args.meses * 30 / args.paso_dias)
    symbol_count = len(args.symbols or [])
    if not symbol_count:
        db = market_db_path(args.db)
        if db.exists():
            try:
                with sqlite3.connect(db.as_uri() + "?mode=ro", uri=True) as conn:
                    symbol_count = int(conn.execute(
                        "SELECT COUNT(*) FROM symbols WHERE active=1 AND role='operar'").fetchone()[0])
            except sqlite3.Error:
                symbol_count = 0
    estimated_cases = estimated_dates * symbol_count * 5
    estimated_seconds = estimated_cases * 2.0
    print(f"VALIDACION estimación_fechas_por_moneda={estimated_dates} monedas={symbol_count} geometrías=5 "
        f"casos_estimados={estimated_cases} segundos_estimados={estimated_seconds:.0f}")
    if estimated_seconds > 3 * 3600:
        print("VALIDACION aviso=la estimación supera 3 h; usar --paso-dias 14 o menos monedas")
    if os.name == "posix":
        try:
            os.nice(10)
        except OSError:
            pass
    started = time.perf_counter()
    result = validate_scenarios(db_path=args.db, months=args.meses, step_days=args.paso_dias,
        symbols=args.symbols, commission=args.comision, n_scenarios=args.escenarios,
        n_smart=args.n_smart, profile=args.perfil, capital=args.capital, meta=args.meta,
        library_dir=args.biblioteca, seed=args.semilla,
        progress=lambda done, total: print(f"VALIDACION progreso_fechas={done}/{total}", flush=True))
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M")
    report_path = out / f"validacion_{stamp}.md"
    json_path = out / f"validacion_{stamp}.json"
    report_path.write_text(render_report(result), encoding="utf-8", newline="\n")
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8", newline="\n")
    print(f"VALIDACION archivos={report_path},{json_path} segundos={time.perf_counter()-started:.3f} "
        f"rss_max_mb={_rss_max_mb()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
