"""Offline paired-window replay: no loans/reopen. sl=none uses stop_loss_pct=1,000,000 as a sentinel, not an off switch."""
from __future__ import annotations

import argparse
from itertools import combinations
import json
from math import exp, sqrt
from pathlib import Path
import re
import statistics
import sys

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from grid.policy import DEFAULT_SMART_PARAMS
from grid.sim.data import CandleData
from grid.sim.runner import FILTERS as SIM_FILTERS, run_simulation

HEADER = "\u03c3 = EWMA interna; sin pr\u00e9stamos; sin reapertura tras CLOSE_REPOSITORY; una moneda; resultados por ventana, no anualizados"
_CANDLES_PER_DAY_5M = 288
_LOOKBACK_HOURS = 30 * 24
_VARIANT_RE = re.compile(r"^smart:h=(4|24),sl=(none|[0-9]+(?:\.[0-9]+)?)$")


def parse_variant(value: str) -> dict:
    if value == "simple":
        return {"name": "simple", "strategy": "simple", "params": None}
    match = _VARIANT_RE.fullmatch(value)
    if not match:
        raise ValueError(f"invalid variant {value!r}; expected simple or smart:h=4|24,sl=<percent|none>")
    horizon, stop = match.groups()
    params = dict(DEFAULT_SMART_PARAMS)
    params["horizon_h"] = int(horizon)
    params["stop_loss_pct"] = 1000000.0 if stop == "none" else float(stop)
    name = f"smart:h={horizon},sl={stop}"
    return {"name": name, "strategy": "smart", "params": params}


def _normalise_frame(frame: pd.DataFrame) -> pd.DataFrame:
    required = {"timestamp", "open", "high", "low", "close"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"CSV missing columns: {', '.join(sorted(missing))}")
    result = frame.copy()
    result["timestamp"] = pd.to_datetime(result["timestamp"], utc=True, errors="raise")
    result = result.sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True)
    for field in ("open", "high", "low", "close"):
        result[field] = pd.to_numeric(result[field], errors="raise")
    if result.empty or not np.isfinite(result[["open", "high", "low", "close"]].to_numpy()).all():
        raise ValueError("CSV has no finite candle data")
    return result


def load_frames(symbol: str, data_dir: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    base = symbol.strip().upper().replace("/", "")
    if base.endswith("USDT"):
        base = base[:-4]
    stem = base.lower()
    paths = (data_dir / f"{stem}_5m.csv", data_dir / f"{stem}_1h.csv")
    missing = [str(path) for path in paths if not path.is_file()]
    if missing:
        raise FileNotFoundError("missing cached candle CSV: " + ", ".join(missing))
    frames = []
    for path in paths:
        frames.append(_normalise_frame(pd.read_csv(path, parse_dates=["timestamp"])))
    return frames[0], frames[1]


def build_windows(frame_5m: pd.DataFrame, frame_1h: pd.DataFrame,
                  window_days: int) -> tuple[list[dict], int]:
    if int(window_days) < 7:
        raise ValueError("window-days must be at least 7")
    five, hourly = _normalise_frame(frame_5m), _normalise_frame(frame_1h)
    duration = pd.Timedelta(days=int(window_days))
    step = pd.Timedelta(minutes=5)
    last_exclusive = five["timestamp"].iloc[-1] + step
    cursor = five["timestamp"].iloc[0]
    windows, discarded = [], 0
    while cursor + duration <= last_exclusive:
        end = cursor + duration
        segment = five.loc[(five["timestamp"] >= cursor) & (five["timestamp"] < end)].reset_index(drop=True)
        lookback_start = cursor - pd.Timedelta(days=30)
        prior = hourly.loc[(hourly["timestamp"] >= lookback_start)
                           & (hourly["timestamp"] < cursor)]
        closes = prior["close"].to_numpy(dtype=float)
        if len(prior) < _LOOKBACK_HOURS or len(closes) < 2 or np.any(closes <= 0):
            discarded += 1
        elif segment.empty:
            discarded += 1
        else:
            sigma24 = float(np.std(np.diff(np.log(closes)), ddof=1) * sqrt(24.0))
            if not np.isfinite(sigma24) or sigma24 <= 0:
                discarded += 1
            else:
                center = float(segment["close"].iloc[0])
                windows.append({"start": cursor, "end": end, "frame": segment,
                                "sigma24": sigma24, "low": center * exp(-2.0 * sigma24),
                                "high": center * exp(2.0 * sigma24)})
        cursor = end
    return windows, discarded


def paired_statistics(variant_values: dict[str, list[float]]) -> list[dict]:
    results = []
    for left, right in combinations(variant_values, 2):
        a, b = variant_values[left], variant_values[right]
        if len(a) != len(b):
            raise ValueError("paired variants must have equal window counts")
        differences = np.asarray(a, dtype=float) - np.asarray(b, dtype=float)
        n = len(differences)
        mean_difference = float(np.mean(differences)) if n else None
        sample_sd = float(np.std(differences, ddof=1)) if n > 1 else None
        paired_t = (mean_difference / (sample_sd / sqrt(n))
                    if n >= 2 and sample_sd is not None and sample_sd > 0 else None)
        results.append({"left": left, "right": right, "n": n,
                        "mean_difference": mean_difference, "sample_sd": sample_sd,
                        "paired_t": paired_t,
                        "wins": int(np.sum(differences > 0)),
                        "losses": int(np.sum(differences < 0)),
                        "ties": int(np.sum(differences == 0))})
    return results


def _to_candles(frame: pd.DataFrame) -> CandleData:
    stamps = pd.to_datetime(frame["timestamp"], utc=True).astype("int64").to_numpy() // 1_000_000_000
    deltas = np.diff(stamps)
    return CandleData(timestamp=stamps.astype(np.int64),
        open=frame["open"].to_numpy(dtype=float), high=frame["high"].to_numpy(dtype=float),
        low=frame["low"].to_numpy(dtype=float), close=frame["close"].to_numpy(dtype=float),
        gaps=int(np.sum(deltas != 300)))


def analyze_frames(symbol: str, frame_5m: pd.DataFrame, frame_1h: pd.DataFrame,
                   window_days: int, variants: list[str]) -> dict:
    parsed = [parse_variant(value) for value in variants]
    if not parsed:
        raise ValueError("at least one variant is required")
    windows, discarded = build_windows(frame_5m, frame_1h, window_days)
    if len(windows) < 4:
        raise ValueError(f"fewer than 4 valid windows: {len(windows)} valid, {discarded} discarded")
    values = {item["name"]: [] for item in parsed}
    window_results = []
    for index, window in enumerate(windows):
        candles = _to_candles(window["frame"])
        row = {"index": index, "start": window["start"].isoformat(),
               "end": window["end"].isoformat(), "sigma24": window["sigma24"],
               "range_low": window["low"], "range_high": window["high"],
               "variants": {}}
        for variant in parsed:
            result = run_simulation(candles, strategy=variant["strategy"], n=20,
                capital=1000.0, low=window["low"], high=window["high"], fee_pct=0.1,
                resync_candles=3, params=variant["params"], filters=SIM_FILTERS)
            pnl = float(result["metrics"]["pnl_total_net_usdt"])
            values[variant["name"]].append(pnl)
            row["variants"][variant["name"]] = pnl
        window_results.append(row)
    summaries = []
    for name, series in values.items():
        worst_index = int(np.argmin(series))
        summaries.append({"variant": name, "n": len(series), "mean": float(statistics.mean(series)),
            "median": float(statistics.median(series)), "worst_window": window_results[worst_index]["index"],
            "worst_start": window_results[worst_index]["start"], "worst_pnl": float(series[worst_index])})
    return {"header": HEADER, "symbol": symbol.upper(), "window_days": int(window_days),
            "valid_windows": len(windows), "discarded_windows": discarded,
            "variant_summary": summaries, "paired_comparisons": paired_statistics(values),
            "windows": window_results}


def main(argv: list[str] | None = None) -> dict:
    parser = argparse.ArgumentParser(description=HEADER)
    parser.add_argument("--symbol", default="XRPUSDT")
    parser.add_argument("--window-days", type=int, default=30)
    parser.add_argument("--variants", nargs="+", default=["simple", "smart:h=4,sl=5", "smart:h=24,sl=none"])
    parser.add_argument("--out", required=True, help="JSON output path")
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data" / "cache")
    args = parser.parse_args(argv)
    if args.window_days < 7:
        parser.error("--window-days must be at least 7")
    try:
        five, hourly = load_frames(args.symbol, args.data_dir)
        payload = analyze_frames(args.symbol, five, hourly, args.window_days, args.variants)
    except (FileNotFoundError, ValueError) as exc:
        parser.error(str(exc))
    out = Path(args.out)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(HEADER)
    for row in payload["variant_summary"]:
        print(f"{row['variant']}: n={row['n']}, mean={row['mean']:.4f}, median={row['median']:.4f}, worst={row['worst_pnl']:.4f}")
    for row in payload["paired_comparisons"]:
        print(f"{row['left']} - {row['right']}: mean={row['mean_difference']:.4f}, sd={row['sample_sd']:.4f}, t={row['paired_t']}, wins={row['wins']}/{row['n']}")
    return payload


if __name__ == "__main__":
    main()
