#!/usr/bin/env python3
"""Calcula y guarda el marcador de pronósticos con las velas de mercado."""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from research.scorecard.runner import run_scorecard


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="market_data.db")
    parser.add_argument("--registro-db", default="asple_predictor.db")
    parser.add_argument("--symbols", nargs="*")
    parser.add_argument("--horizontes", nargs="+", type=int, default=[24, 72])
    parser.add_argument("--out", default="reports/marcador/")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args(argv)


def _lower_priority():
    if os.name == "posix":
        try:
            os.nice(10)
        except OSError:
            pass


def main(argv=None):
    args = parse_args(argv)
    _lower_priority()
    result = run_scorecard(args.db, symbols=args.symbols, horizons=args.horizontes,
        dry_run=args.dry_run, output_dir=args.out, registry_db_path=args.registro_db)
    out = Path(args.out)
    lines = ["# Marcador de pronósticos", "", f"Corrida: {result['run_id']}", "",
        f"Duración: {result['summary']['seconds']:.2f} s", "",
        f"Monedas con historia insuficiente: {', '.join(result['summary']['insufficient']) or 'ninguna'}", "",
        "## Métricas", "", "| Moneda | Horizonte | Modelo | Métrica | Valor |", "|---|---:|---|---|---:|"]
    for row in result["records"]:
        _, symbol, target, horizon, model, _k, n, metric, value, _low, _high = row
        lines.append(f"| {symbol} | {horizon} h | {model} | {metric} (n={n}) | {value:.6g} |")
    if not args.dry_run:
        out.mkdir(parents=True, exist_ok=True)
        date = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        path = out / f"marcador_{date}.md"
        path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    print(f"MARCADOR resumen={result['summary']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
