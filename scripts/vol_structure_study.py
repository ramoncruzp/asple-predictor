#!/usr/bin/env python
"""Run the Phase 15E structure study (fixed vs EWMA-sized vs Nexo-HAR-sized grids),
including the Phase 15E-2 controls: the 25-structure fixed-grid benchmark (A'),
the constant-sigma width control (B'), and block-size sensitivity (5 vs 9)."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from grid.sim.data import CandleDataError, load_candles
from grid.sim.structure_study import run_structure_study
from grid.sim.vol_series import MIN_TRAIN_DAYS, build_full_frame, load_hourly_history


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--workers", type=int, default=2)
    p.add_argument("--csv", type=Path, default=ROOT / "data/cache/xrp_5m.csv")
    p.add_argument("--hourly-csv", type=Path, default=ROOT / "data/cache/xrp_1h.csv")
    p.add_argument("--allow-gaps", action="store_true")
    p.add_argument("--min-train-days", type=int, default=MIN_TRAIN_DAYS)
    p.add_argument("--out-dir", type=Path, default=ROOT / "data/cache/sim/structure_study")
    p.add_argument("--stdout-only", action="store_true", default=True,
                   help="print the study summary without writing output artifacts (default)")
    p.add_argument("--write-artifacts", dest="stdout_only", action="store_false",
                   help="also write summary.json and windows.csv under --out-dir")
    return p


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    try:
        candles = load_candles(args.csv, allow_gaps=args.allow_gaps)
        hourly, intraday, csv_hash = load_hourly_history(args.hourly_csv, args.csv)
        frame = build_full_frame(hourly, intraday, csv_hash=csv_hash)
        result = run_structure_study(
            candles, frame, csv_hash=csv_hash, seed=args.seed, workers=args.workers,
            min_train_days=args.min_train_days,
        )
    except (CandleDataError, OSError, ValueError, TypeError) as exc:
        print(f"structure study error: {exc}", file=sys.stderr)
        return 3
    summary = {key: value for key, value in result.items() if key != "rows"}
    if not args.stdout_only:
        args.out_dir.mkdir(parents=True, exist_ok=True)
        (args.out_dir / "summary.json").write_text(
            json.dumps(summary, sort_keys=True, indent=2, default=str), encoding="utf-8")
        import csv as csv_module
        rows = result["rows"]
        flat_rows = [{**{k: v for k, v in row.items() if k != "metrics"},
                      **(row["metrics"] or {})} for row in rows]
        with (args.out_dir / "windows.csv").open("w", newline="", encoding="utf-8") as stream:
            fieldnames = sorted({key for row in flat_rows for key in row})
            writer = csv_module.DictWriter(stream, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(flat_rows)
    print(json.dumps(summary, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
