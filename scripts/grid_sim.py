#!/usr/bin/env python
"""Run or compare deterministic historical grid simulations without network access."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from grid.sim.data import CandleDataError, load_candles
from grid.sim.runner import run_simulation


def _param(value):
    if "=" not in value:
        raise argparse.ArgumentTypeError("parameter must be k=v")
    key, raw = value.split("=", 1)
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        parsed = raw
    return key, parsed


def parser():
    root = argparse.ArgumentParser(description=__doc__)
    sub = root.add_subparsers(dest="command", required=True)
    for name in ("run", "compare"):
        p = sub.add_parser(name)
        p.add_argument("--start", required=True)
        p.add_argument("--end", required=True)
        p.add_argument("--n", type=int, required=True)
        p.add_argument("--capital", type=float, required=True)
        bounds = p.add_mutually_exclusive_group(required=True)
        bounds.add_argument("--low", type=float)
        bounds.add_argument("--width-pct", type=float)
        p.add_argument("--high", type=float)
        p.add_argument("--strategy", choices=("simple", "smart"), default="simple")
        p.add_argument("--param", action="append", type=_param, default=[])
        p.add_argument("--fee-pct", type=float, default=.1)
        p.add_argument("--resync-candles", type=int, default=3)
        p.add_argument("--halflife-h", type=float, default=72)
        p.add_argument("--sigma-scale", type=float, default=1)
        p.add_argument("--allow-gaps", action="store_true")
        p.add_argument("--csv", type=Path, default=ROOT / "data/cache/xrp_5m.csv")
        p.add_argument("--out", type=Path)
    return root


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.low is not None and args.high is None:
            raise ValueError("--high is required with --low")
        if args.width_pct is not None and args.width_pct <= 0:
            raise ValueError("width-pct must be positive")
        candles = load_candles(args.csv, args.start, args.end, args.allow_gaps)
    except (CandleDataError, OSError) as exc:
        print(f"data error: {exc}", file=sys.stderr)
        return 3
    except ValueError as exc:
        print(f"usage error: {exc}", file=sys.stderr)
        return 2
    digest = hashlib.sha256(args.csv.read_bytes()).hexdigest()
    common = dict(n=args.n, capital=args.capital, low=args.low, high=args.high,
                  width_pct=args.width_pct, fee_pct=args.fee_pct,
                  resync_candles=args.resync_candles, params=dict(args.param),
                  halflife_h=args.halflife_h, sigma_scale=args.sigma_scale, csv_hash=digest)
    try:
        if args.out is None:
            args.out = ROOT / "data/cache/sim" / f"{args.command}_{args.start}_{args.end}.json"
        if args.command == "run":
            result = run_simulation(candles, strategy=args.strategy, **common)
            output = json.dumps(result, sort_keys=True, separators=(",", ":"))
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(output + "\n", encoding="utf-8")
            print(json.dumps(result["metrics"], sort_keys=True))
        else:
            simple = run_simulation(candles, strategy="simple", **common)
            smart = run_simulation(candles, strategy="smart", **common)
            def flatten(metrics):
                result = {key: value for key, value in metrics.items() if key != "interventions_by_type"}
                result.update({f"interventions_{key}": value
                               for key, value in metrics.get("interventions_by_type", {}).items()})
                return result
            simple_metrics, smart_metrics = flatten(simple["metrics"]), flatten(smart["metrics"])
            metrics = sorted(set(simple_metrics) | set(smart_metrics))
            rows = {key: {"simple": simple_metrics.get(key, 0), "smart": smart_metrics.get(key, 0),
                          "smart_minus_simple": (smart_metrics.get(key, 0) - simple_metrics.get(key, 0))
                          if isinstance(smart_metrics.get(key, 0), (int, float))
                          and isinstance(simple_metrics.get(key, 0), (int, float)) else None}
                    for key in metrics}
            result = {"simple": simple, "smart": smart, "comparison": rows}
            output = json.dumps(result, sort_keys=True, separators=(",", ":"))
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(output + "\n", encoding="utf-8")
            print("metric | simple | smart | smart-simple")
            for key, row in rows.items():
                print(f"{key} | {row['simple']} | {row['smart']} | {row['smart_minus_simple']}")
            table = ["| Métrica | Simple | Smart | Smart - simple |", "|---|---:|---:|---:|"]
            table.extend(f"| {key} | {row['simple']} | {row['smart']} | {row['smart_minus_simple']} |"
                         for key, row in rows.items())
            args.out.with_suffix(".md").write_text("\n".join(table) + "\n", encoding="utf-8")
    except (ValueError, TypeError) as exc:
        print(f"usage error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
