"""Structure and market-regime diagnostics for historical grid simulations.

Ex-post labels describe the just-completed window. Causal features use only
candles strictly before that window and are suitable for prospective analysis.
"""
from __future__ import annotations

import concurrent.futures
import csv
import math
import time
from collections import defaultdict
from pathlib import Path

import numpy as np

from grid.policy import DEFAULT_SMART_PARAMS
from grid.sim.calibration import block_bootstrap_ci
from grid.sim.data import CandleData
from grid.sim.runner import FILTERS, run_simulation
from grid.levels import compute_lines, plan_cells

WINDOW_DAYS = 30
STEP_DAYS = 7
STRUCTURES = tuple((n, width) for n in (5, 8, 10, 15, 20) for width in (4, 6, 9, 12, 16))
BASE_STRUCTURE = (10, 9)
BLOCK_WINDOWS = 5
BOOTSTRAP_SAMPLES = 2000


def build_windows(candles, window_days=WINDOW_DAYS, step_days=STEP_DAYS):
    span, step = int(window_days * 86400), int(step_days * 86400)
    begin, end = int(candles.timestamp[0]), int(candles.timestamp[-1]) + 300
    windows = []
    cursor = begin
    while cursor + span <= end:
        lo = int(np.searchsorted(candles.timestamp, cursor, side="left"))
        hi = int(np.searchsorted(candles.timestamp, cursor + span, side="left"))
        if hi - lo >= 2:
            windows.append({"window_id": len(windows), "start": cursor,
                            "end_exclusive": cursor + span, "start_idx": lo, "end_idx": hi})
        cursor += step
    return windows


def hourly_closes(candles):
    """Return the last observed close in each UTC hour."""
    if not len(candles.timestamp):
        return np.asarray([], dtype=float)
    hours = candles.timestamp // 3600
    last = np.r_[hours[1:] != hours[:-1], True]
    return np.asarray(candles.close[last], dtype=float)


def efficiency_ratio(closes):
    values = np.asarray(closes, dtype=float)
    if len(values) < 2:
        return 0.0
    path = float(np.abs(np.diff(values)).sum())
    if path <= 0:
        return 0.0
    return float(min(1.0, abs(float(values[-1] - values[0])) / path))


def _features(candles):
    closes = hourly_closes(candles)
    if len(closes) < 2:
        return {"return_pct": None, "er": None, "realized_vol_ann_pct": None}
    returns = np.diff(np.log(closes))
    vol = float(np.std(returns, ddof=1) * math.sqrt(24 * 365) * 100) if len(returns) > 1 else 0.0
    return {"return_pct": float((closes[-1] / closes[0] - 1) * 100),
            "er": efficiency_ratio(closes), "realized_vol_ann_pct": vol}


def _slice(candles, lo, hi):
    return CandleData(candles.timestamp[lo:hi], candles.open[lo:hi], candles.high[lo:hi],
                      candles.low[lo:hi], candles.close[lo:hi], 0)


def causal_window_features(candles, window_start, days):
    """Measure a trailing interval [start-days, start); never read the window."""
    seconds = int(days * 86400)
    if window_start - seconds < int(candles.timestamp[0]):
        return {"return_pct": None, "er": None, "realized_vol_ann_pct": None}
    lo = int(np.searchsorted(candles.timestamp, window_start - seconds, side="left"))
    hi = int(np.searchsorted(candles.timestamp, window_start, side="left"))
    if hi - lo < 2:
        return {"return_pct": None, "er": None, "realized_vol_ann_pct": None}
    return _features(_slice(candles, lo, hi))


def assign_ex_post_regimes(features):
    returns = np.asarray([x["return_pct"] for x in features], dtype=float)
    ers = np.asarray([x["er"] for x in features], dtype=float)
    r1, r2 = np.quantile(returns, [1 / 3, 2 / 3])
    e1, e2 = np.quantile(ers, [1 / 3, 2 / 3])
    labels = []
    for ret, er in zip(returns, ers):
        if ret >= r2 and er >= e2:
            labels.append("bullish")
        elif ret <= r1 and er >= e2:
            labels.append("bearish")
        else:
            labels.append("sideways")
    return labels, {"return_terciles_pct": [float(r1), float(r2)],
                    "er_terciles": [float(e1), float(e2)],
                    "definition": "bullish: return>=Q67 and ER>=Q67; bearish: return<=Q33 and ER>=Q67; otherwise sideways"}


def validate_structure(n, width_pct, price, capital=100.0):
    try:
        center = float(price)
        low = center * math.exp(-float(width_pct) / 200)
        high = center * math.exp(float(width_pct) / 200)
        lines = compute_lines(str(low), str(high), int(n), FILTERS)
        plan_cells(lines, str(capital),
                   {"bid_price": str(center * (1 - 1e-8)), "ask_price": str(center * (1 + 1e-8)),
                    "avg_price": str(center)}, FILTERS,
                   {"grid_min_step_pct": .003, "capital_max_por_nivel_pct": .30})
        return None
    except (ValueError, ArithmeticError) as exc:
        return f"{type(exc).__name__}: {exc}"


def structure_catalog(candles, windows, capital=100.0):
    valid, rejected = [], []
    for n, width in STRUCTURES:
        reasons = sorted({validate_structure(n, width, float(candles.close[w["start_idx"]]), capital)
                          for w in windows})
        reasons = [reason for reason in reasons if reason]
        if reasons:
            rejected.append({"n_levels": n, "width_pct": width, "reasons": reasons})
        else:
            valid.append((n, width))
    return valid, rejected


def execute_tasks(candles, windows, tasks, *, workers=1, capital=100.0, resync_candles=3):
    with concurrent.futures.ProcessPoolExecutor(max_workers=max(1, workers),
            initializer=_init_sweep_worker,
            initargs=(candles, windows, capital, resync_candles)) as pool:
        results = list(pool.map(_simulate_task, tasks))
    return results


def _block_indices(n, rng, block=BLOCK_WINDOWS):
    if n < 1:
        return np.asarray([], dtype=int)
    out = []
    while len(out) < n:
        start = int(rng.integers(0, n))
        out.extend((start + i) % n for i in range(max(5, block)))
    return np.asarray(out[:n], dtype=int)


def block_bootstrap_interval(values, *, seed=42, samples=BOOTSTRAP_SAMPLES,
                             confidence=.95, block=BLOCK_WINDOWS, statistic=None):
    arr = np.asarray(values, dtype=float)
    if arr.ndim == 1:
        arr = arr[np.isfinite(arr)]
    else:
        arr = arr[np.isfinite(arr).all(axis=tuple(range(1, arr.ndim)))]
    if not len(arr):
        return [None, None]
    rng = np.random.default_rng(seed)
    block_len = max(5, int(block))
    blocks_per_sample = int(math.ceil(len(arr) / block_len))
    starts = rng.integers(0, len(arr), size=(samples, blocks_per_sample))
    offsets = np.arange(block_len, dtype=int)
    indices = (starts[:, :, None] + offsets[None, None, :]) % len(arr)
    indices = indices.reshape(samples, -1)[:, :len(arr)]
    bootstrap = arr[indices]
    if statistic is None:
        reps = np.mean(bootstrap, axis=1)
    else:
        reps = np.asarray([statistic(sample) for sample in bootstrap])
    alpha = (1 - confidence) / 2
    return [float(x) for x in np.quantile(reps, [alpha, 1 - alpha])]


def rankdata(values):
    values = np.asarray(values, dtype=float)
    order = np.argsort(values, kind="mergesort")
    ranks = np.empty(len(values), dtype=float)
    i = 0
    while i < len(order):
        j = i + 1
        while j < len(order) and values[order[j]] == values[order[i]]:
            j += 1
        ranks[order[i:j]] = (i + j - 1) / 2 + 1
        i = j
    return ranks


def spearman(x, y):
    rx, ry = rankdata(x), rankdata(y)
    if len(rx) < 2 or np.std(rx) == 0 or np.std(ry) == 0:
        return None
    return float(np.corrcoef(rx, ry)[0, 1])


def is_robust(mean, ci99_lower, first_half_mean, second_half_mean, median):
    return bool(mean is not None and ci99_lower is not None and first_half_mean is not None
                and second_half_mean is not None and median is not None
                and mean > 0 and ci99_lower > 0 and first_half_mean > 0
                and second_half_mean > 0 and median > 0)


_WORKER_CANDLES = None
_WORKER_WINDOWS = None
_WORKER_CAPITAL = 100.0
_WORKER_RESYNC = 3


def _init_sweep_worker(candles, windows, capital, resync_candles):
    global _WORKER_CANDLES, _WORKER_WINDOWS, _WORKER_CAPITAL, _WORKER_RESYNC
    _WORKER_CANDLES, _WORKER_WINDOWS = candles, windows
    _WORKER_CAPITAL, _WORKER_RESYNC = float(capital), int(resync_candles)


def _simulate_task(task):
    window_id, n, width, strategy, fee_pct = task
    win = _WORKER_WINDOWS[window_id]
    c = _slice(_WORKER_CANDLES, win["start_idx"], win["end_idx"])
    sigma_scale = float(DEFAULT_SMART_PARAMS["sigma_scale"]) if strategy == "smart" else 1.0
    result = run_simulation(c, strategy=strategy, n=n, capital=_WORKER_CAPITAL,
                            width_pct=width, fee_pct=fee_pct, resync_candles=_WORKER_RESYNC,
                            sigma_scale=sigma_scale, params=None, include_details=True)
    metrics = result["metrics"]
    trapped_value = 0.0
    for cell in result["details"]["cells"]:
        if cell.get("entry_price") is not None:
            trapped_value += float(cell.get("held_qty") or 0) * float(cell["entry_price"])
    counts = metrics.get("interventions_by_type", {})
    return {"window_id": window_id, "n_levels": n, "width_pct": width,
            "strategy": strategy, "fee_pct": fee_pct,
            "pnl_pct": float(metrics["pnl_total_net_usdt"] / _WORKER_CAPITAL * 100),
            "max_drawdown_pct": float(metrics["max_drawdown_pct"]),
            "cycles_completed": int(metrics["cycles_completed"]),
            "trapped_capital_pct_end": trapped_value / _WORKER_CAPITAL * 100,
            "fees_usdt": float(metrics["fees_usdt"]),
            "pause_count": int(counts.get("PAUSE", 0)),
            "adjust_count": int(counts.get("ADJUST", 0)),
            "stop_loss_count": int(counts.get("STOP_LOSS", 0)),
            "buy_hold_pnl_pct": float(metrics["buy_hold_pnl_usdt"] / _WORKER_CAPITAL * 100)}


def _robust_stats(values, seed, chronological_first_half=None):
    a = np.asarray(values, dtype=float)
    n = len(a)
    half = n // 2
    ci99 = block_bootstrap_interval(a, seed=seed, confidence=.99)
    mean = float(np.mean(a)) if n else None
    median = float(np.median(a)) if n else None
    if chronological_first_half is None:
        chronological_first_half = np.arange(n) < half
    chronological_first_half = np.asarray(chronological_first_half, dtype=bool)
    first_values, second_values = a[chronological_first_half], a[~chronological_first_half]
    first = float(np.mean(first_values)) if len(first_values) else None
    second = float(np.mean(second_values)) if len(second_values) else None
    robust = bool(n and is_robust(mean, ci99[0], first, second, median))
    return {"n": n, "mean_pnl_pct": mean, "median_pnl_pct": median,
            "worst_pnl_pct": float(np.min(a)) if n else None,
            "win_fraction": float(np.mean(a > 0)) if n else None,
            "first_half_mean_pct": first, "second_half_mean_pct": second,
            "ci99_mean_block": ci99, "robust": robust,
            "verdict": "robust" if robust else "sin evidencia"}


def _group_rows(rows, keys):
    groups = defaultdict(list)
    for row in rows:
        groups[tuple(row[k] for k in keys)].append(row)
    return groups


def _aggregate(windows, features, regime_labels, *, seed=42, workers=2, capital=100.0,
               fee_pct=.1, resync_candles=3):
    valid, rejected = structure_catalog(_WORKER_CANDLES, windows, capital)
    main_rows, duration_by_config = [], {}
    pool_args = (windows, capital, resync_candles)
    with concurrent.futures.ProcessPoolExecutor(max_workers=max(1, workers),
            initializer=_init_sweep_worker,
            initargs=(_WORKER_CANDLES, *pool_args)) as pool:
        for n, width in valid:
            started = time.perf_counter()
            for strategy in ("simple", "smart"):
                tasks = [(w["window_id"], n, width, strategy, fee_pct) for w in windows]
                main_rows.extend(pool.map(_simulate_task, tasks))
            duration_by_config[f"{n}/{width}"] = time.perf_counter() - started
        summary_by_key = {(r["window_id"], r["n_levels"], r["width_pct"], r["strategy"]): r
                          for r in main_rows}
        # Sensitivity fee reruns use the three structures with highest median PnL,
        # plus the fixed 10/9 reference. Main score uses the better strategy median.
        medians = []
        for n, width in valid:
            vals = [np.median([r["pnl_pct"] for r in main_rows
                               if r["n_levels"] == n and r["width_pct"] == width and r["strategy"] == st])
                    for st in ("simple", "smart")]
            medians.append((max(vals), n, width))
        top = [(n, width) for _, n, width in sorted(medians, reverse=True)[:3]]
        selected = list(dict.fromkeys(top + [BASE_STRUCTURE]))
        sensitivity_rows = []
        for cost in (.075, .2):
            for n, width in selected:
                for strategy in ("simple", "smart"):
                    tasks = [(w["window_id"], n, width, strategy, cost) for w in windows]
                    sensitivity_rows.extend(pool.map(_simulate_task, tasks))
    feature_by_window = {f["window_id"]: f for f in features}
    for rows in (main_rows, sensitivity_rows):
        for row in rows:
            row.update({k: feature_by_window[row["window_id"]].get(k)
                        for k in ("window_start", "window_end_exclusive", "regime_expost")})
    return {"valid_structures": valid, "rejected_structures": rejected,
            "main_rows": main_rows, "sensitivity_rows": sensitivity_rows,
            "duration_by_config_s": duration_by_config, "sensitivity_structures": selected}


def run_sweep(candles, *, seed=42, workers=2, capital=100.0, fee_pct=.1,
              resync_candles=3, csv_sha256=None):
    started = time.perf_counter()
    windows = build_windows(candles)
    if not windows:
        raise ValueError("history is shorter than one 30-day sweep window")
    features = []
    for w in windows:
        ex = _features(_slice(candles, w["start_idx"], w["end_idx"]))
        prev14 = causal_window_features(candles, w["start"], 14)
        prev30 = causal_window_features(candles, w["start"], 30)
        features.append({"window_id": w["window_id"], "window_start": w["start"],
                         "window_end_exclusive": w["end_exclusive"],
                         "expost_return_pct": ex["return_pct"], "expost_er": ex["er"],
                         "expost_realized_vol_ann_pct": ex["realized_vol_ann_pct"],
                         **{f"prev14_{k}": v for k, v in prev14.items()},
                         **{f"prev30_{k}": v for k, v in prev30.items()}})
    labels, thresholds = assign_ex_post_regimes(
        [{"return_pct": f["expost_return_pct"], "er": f["expost_er"]} for f in features])
    for f, label in zip(features, labels):
        f["regime_expost"] = label
    global _WORKER_CANDLES
    _WORKER_CANDLES = candles
    aggregated = _aggregate(windows, features, labels, seed=seed, workers=workers,
                            capital=capital, fee_pct=fee_pct, resync_candles=resync_candles)
    main_rows, sensitivity_rows = aggregated["main_rows"], aggregated["sensitivity_rows"]
    base = {(r["window_id"], r["strategy"]): r["pnl_pct"] for r in main_rows
            if (r["n_levels"], r["width_pct"]) == BASE_STRUCTURE}
    structures = []
    for (n, width, strategy), rows in sorted(_group_rows(main_rows, ("n_levels", "width_pct", "strategy")).items()):
        rows.sort(key=lambda r: r["window_id"])
        vals = [r["pnl_pct"] for r in rows]
        diffs = [r["pnl_pct"] - base[(r["window_id"], strategy)] for r in rows]
        stat = _robust_stats(vals, seed + n * 100 + width,
                             [r["window_id"] < len(windows) / 2 for r in rows])
        structures.append({"n_levels": n, "width_pct": width, "strategy": strategy,
                           **stat, "delta_vs_base_mean_pp": float(np.mean(diffs)),
                           "delta_vs_base_ci95_block": block_bootstrap_ci(
                               diffs, seed=seed + 17 + n * 100 + width, samples=BOOTSTRAP_SAMPLES)})
    regimes = []
    for (regime, n, width, strategy), rows in sorted(_group_rows(main_rows, ("regime_expost", "n_levels", "width_pct", "strategy")).items()):
        ordered_rows = sorted(rows, key=lambda r: r["window_id"])
        vals = [r["pnl_pct"] for r in ordered_rows]
        regimes.append({"regime": regime, "n_levels": n, "width_pct": width,
                        "strategy": strategy, **_robust_stats(
                            vals, seed + n * 100 + width,
                            [r["window_id"] < len(windows) / 2 for r in ordered_rows])})
    smart_simple = []
    for (n, width), rows in _group_rows(main_rows, ("n_levels", "width_pct")).items():
        byid = {(r["window_id"], r["strategy"]): r["pnl_pct"] for r in rows}
        common = sorted({r["window_id"] for r in rows})
        diffs = [byid[(i, "smart")] - byid[(i, "simple")] for i in common]
        smart_simple.append({"regime": "all", "n_levels": n, "width_pct": width,
                             "mean_smart_minus_simple_pp": float(np.mean(diffs)),
                             "median_smart_minus_simple_pp": float(np.median(diffs)),
                             "ci95_block": block_bootstrap_interval(diffs, seed=seed + 47 + n + width)})
    for (regime, n, width), rows in _group_rows(main_rows, ("regime_expost", "n_levels", "width_pct")).items():
        byid = {(r["window_id"], r["strategy"]): r["pnl_pct"] for r in rows}
        common = sorted({r["window_id"] for r in rows})
        diffs = [byid[(i, "smart")] - byid[(i, "simple")] for i in common]
        smart_simple.append({"regime": regime, "n_levels": n, "width_pct": width,
                             "mean_smart_minus_simple_pp": float(np.mean(diffs)),
                             "median_smart_minus_simple_pp": float(np.median(diffs)),
                             "ci95_block": block_bootstrap_interval(diffs, seed=seed + 53 + n + width)})
    predictability = _predictability(features, main_rows, seed)
    comparison_counts = {"robust_structure_strategy": len(structures),
                         "robust_regime_structure_strategy": len(regimes),
                         "structure_vs_base_ci": len(structures),
                         "smart_vs_simple": len(smart_simple),
                         "causal_feature_associations": len(predictability),
                         "cost_sensitivity_cells": len(_sensitivity(
                             sensitivity_rows, aggregated["sensitivity_structures"]))}
    comparison_counts["total"] = sum(comparison_counts.values())
    summary = {"method": "rolling_30d_step7d_structure_regime_sweep",
               "seed": seed, "csv_sha256": csv_sha256,
               "data_start": int(candles.timestamp[0]), "data_end": int(candles.timestamp[-1]),
               "window_count": len(windows), "overlapping_windows": True,
               "valid_structure_count": len(aggregated["valid_structures"]),
               "rejected_structures": aggregated["rejected_structures"],
               "comparisons_total": comparison_counts["total"],
               "comparison_counts": comparison_counts,
               "workers": max(1, workers), "fee_pct_main": fee_pct,
               "duration_total_s": time.perf_counter() - started,
               "duration_by_config_s": aggregated["duration_by_config_s"],
               "sensitivity_structures": [{"n_levels": n, "width_pct": width}
                                           for n, width in aggregated["sensitivity_structures"]],
               "regime_thresholds": thresholds,
               "structures": structures, "regimes": regimes,
               "smart_minus_simple_by_regime": smart_simple,
               "causal_predictability": predictability,
               "cost_sensitivity": _sensitivity(sensitivity_rows, aggregated["sensitivity_structures"]),
               "rules": {"robust": "mean>0, block-bootstrap 99% CI lower>0, first-half mean>0, second-half mean>0, median>0",
                         "bootstrap_samples": BOOTSTRAP_SAMPLES, "bootstrap_block_windows": BLOCK_WINDOWS,
                         "regimes": thresholds["definition"]}}
    return {"summary": summary, "windows": windows, "features": features,
            "main_rows": main_rows, "sensitivity_rows": sensitivity_rows}


def _predictability(features, rows, seed):
    base = {(r["window_id"], r["strategy"]): r["pnl_pct"] for r in rows
            if (r["n_levels"], r["width_pct"]) == BASE_STRUCTURE}
    reports = []
    fields = [f"prev{days}_{field}" for days in (14, 30)
              for field in ("return_pct", "er", "realized_vol_ann_pct")]
    for strategy in ("simple", "smart"):
        for field in fields:
            pairs = [(f[field], base[(f["window_id"], strategy)]) for f in features
                     if f[field] is not None and (f["window_id"], strategy) in base]
            x = np.asarray([p[0] for p in pairs]); y = np.asarray([p[1] for p in pairs])
            corr = spearman(x, y)
            ci = _spearman_ci(x, y, seed + len(reports))
            q1, q2 = np.quantile(x, [1 / 3, 2 / 3]) if len(x) else (None, None)
            buckets = []
            masks = (("low", x <= q1), ("middle", (x > q1) & (x <= q2)), ("high", x > q2)) if len(x) else (
                ("low", np.zeros(0, dtype=bool)), ("middle", np.zeros(0, dtype=bool)),
                ("high", np.zeros(0, dtype=bool)))
            for label, mask in masks:
                bucket_y = y[mask]
                buckets.append({"tercile": label, "n": int(len(bucket_y)),
                                "mean_next_window_pnl_pct": float(np.mean(bucket_y)) if len(bucket_y) else None,
                                "median_next_window_pnl_pct": float(np.median(bucket_y)) if len(bucket_y) else None})
            reports.append({"strategy": strategy, "feature": field, "n": len(x),
                            "spearman": corr, "spearman_ci95_block": ci,
                            "feature_terciles": [float(q1), float(q2)] if q1 is not None else None,
                            "conditional_pnl": buckets})
    return reports


def _spearman_ci(x, y, seed):
    if len(x) < 2 or spearman(x, y) is None:
        return [None, None]
    arr = np.column_stack([x, y])
    return block_bootstrap_interval(arr, seed=seed, confidence=.95,
                                    statistic=lambda sample: spearman(sample[:, 0], sample[:, 1]))


def _sensitivity(rows, structures):
    out = []
    for (n, width, strategy, fee), group in sorted(_group_rows(rows, ("n_levels", "width_pct", "strategy", "fee_pct")).items()):
        vals = [r["pnl_pct"] for r in group]
        out.append({"n_levels": n, "width_pct": width, "strategy": strategy, "fee_pct": fee,
                    "window_count": len(vals), "mean_pnl_pct": float(np.mean(vals)),
                    "median_pnl_pct": float(np.median(vals)),
                    "is_top3_or_base": (n, width) in structures})
    return out


def write_artifacts(result, outdir):
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    fields = sorted({k for row in result["main_rows"] + result["sensitivity_rows"] for k in row})
    with (outdir / "windows.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader(); writer.writerows(result["main_rows"] + result["sensitivity_rows"])
    feature_fields = sorted({k for row in result["features"] for k in row})
    with (outdir / "features.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=feature_fields)
        writer.writeheader(); writer.writerows(result["features"])
    (outdir / "summary.json").write_text(__import__("json").dumps(result["summary"], sort_keys=True, indent=2), encoding="utf-8")
