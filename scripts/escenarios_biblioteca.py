#!/usr/bin/env python3
"""Construye bibliotecas históricas FHS desde CandleStore."""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from research.scenarios.library import build_library


def _rss_max_mb():
    try:
        import psutil
        return psutil.Process().memory_info().rss / (1024 * 1024)
    except ImportError:
        return None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="market_data.db")
    parser.add_argument("--symbols", nargs="*")
    parser.add_argument("--out", default="data/derived/scenario_lib")
    args = parser.parse_args(argv)
    if os.name == "posix":
        try:
            os.nice(10)
        except OSError:
            pass
    manifest = build_library(args.db, symbols=args.symbols, output_dir=args.out)
    print(f"ESCENARIOS biblioteca monedas={len(manifest['symbols'])}")
    rss = _rss_max_mb()
    if rss is not None:
        print(f"ESCENARIOS rss_max_mb={rss:.1f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
