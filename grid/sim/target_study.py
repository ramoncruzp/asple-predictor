"""Descriptive, overlapping-window study of grid profit targets."""
from __future__ import annotations

import concurrent.futures
import time

import numpy as np

from grid.sim.calibration import block_bootstrap_ci
from grid.sim.sweep import build_windows, _slice
from grid.sim.runner import run_simulation

TARGET_PCTS = (3, 5, 10, 18)
TARGET_PCTS_30_EXTRA = (0.5, 1, 2)
TARGET_BASES = ("cash", "equity")
TARGET_STRATEGIES = ("simple", "smart")
TARGET_DURATIONS = (30, 90)
_STUDY_CANDLES = None


def _study_worker(task):
    if len(task) == 6:
        candles, window, days, strategy, target_pct, basis = task
    else:
        window, days, strategy, target_pct, basis = task
        candles = _STUDY_CANDLES
    sample = _slice(candles, window["start_idx"], window["end_idx"])
    params = {"dust_sweep_enabled": True}
    if target_pct is not None:
        params.update(target_pct=target_pct, target_basis=basis)
    result = run_simulation(sample, strategy=strategy, n=10, capital=100, width_pct=9,
                            fee_pct=.1, resync_candles=3, params=params)
    event = next((row for row in result["events"] if row.get("type") == "TARGET_REACHED"), None)
    start_ts = int(sample.timestamp[0])
    metrics = result["metrics"]
    return {"window_id": window["window_id"], "days": days, "strategy": strategy,
            "target_pct": target_pct, "target_basis": basis,
            "reached": event is not None,
            "time_to_target_days": None if event is None else (int(event["ts"]) - start_ts) / 86400,
            "equity_final_pct": float(metrics["equity_final_after_repository_liquidation"]),
            "max_cash": float(metrics["max_cash"]),
            "max_usdt_balance": float(metrics["max_usdt_balance"]),
            "max_equity_cells": float(metrics["max_equity_cells"]),
            "max_equity_incl_dust": float(metrics["max_equity_incl_dust"]),
            "dust_qty_final": float(metrics["dust_qty_final"]),
            "dust_swept_usdt": float(metrics["dust_swept_usdt"]),
            "dust_sweep_fee_usdt": float(metrics["dust_sweep_fee_usdt"]),
            "dust_residual_qty": float(metrics["dust_residual_qty"]),
            "cash_final": float(metrics["cash_final"]),
            "base_final": float(metrics["base_final"]),
            "held_qty_final": float(metrics["held_qty_final"]),
            "pct_time_in_range": float(metrics["pct_time_in_range"]),
            "cycles": int(metrics["cycles"]), "realized_net": float(metrics["realized_net"]),
            "dust_cycle_count": int(metrics["dust_cycle_count"]),
            "dust_cycle_cost_usdt_total": float(metrics["dust_cycle_cost_usdt_total"]),
            "dust_cycle_cost_usdt_mean": float(metrics["dust_cycle_cost_usdt_mean"]),
            "dust_spacing_fraction_mean": float(metrics["dust_spacing_fraction_mean"]),
            "price_change_pct": (float(sample.close[-1]) / float(sample.close[0]) - 1) * 100,
            "equity_at_close": None if event is None else float(event["details"]["equity_total_at_close"]),
            "cash_at_close": None if event is None else float(event["details"]["cash_total"]),
            "csv_start": start_ts, "csv_end": int(sample.timestamp[-1])}


def _init_study_worker(candles):
    global _STUDY_CANDLES
    _STUDY_CANDLES = candles


def run_target_study(candles, *, seed=42, workers=1):
    """Compare all target cases with a no-target baseline on matching windows."""
    started = time.perf_counter()
    windows_by_days = {days: build_windows(candles, days, 7) for days in TARGET_DURATIONS}
    tasks = []
    for days, windows in windows_by_days.items():
        for window in windows:
            for strategy in TARGET_STRATEGIES:
                tasks.append((window, days, strategy, None, "cash"))
                target_pcts = TARGET_PCTS + (TARGET_PCTS_30_EXTRA if days == 30 else ())
                for target_pct in target_pcts:
                    for basis in TARGET_BASES:
                        tasks.append((window, days, strategy, target_pct, basis))
    if workers <= 1:
        _init_study_worker(candles)
        raw = [_study_worker(task) for task in tasks]
    else:
        with concurrent.futures.ProcessPoolExecutor(max_workers=workers,
                initializer=_init_study_worker, initargs=(candles,)) as pool:
            raw = list(pool.map(_study_worker, tasks))
    baseline = {(row["days"], row["strategy"], row["window_id"]): row for row in raw
                if row["target_pct"] is None}
    rows = [row for row in raw if row["target_pct"] is not None]
    for row in rows:
        base = baseline[(row["days"], row["strategy"], row["window_id"])]
        row["baseline_equity_final_pct"] = base["equity_final_pct"]
        row["max_cash_upper_bound"] = base["max_cash"]
        row["max_cash_upper_bound_reaches_target"] = (
            base["max_cash"] >= 100 * (1 + float(row["target_pct"]) / 100))
        row["delta_vs_no_target_pct"] = row["equity_final_pct"] - base["equity_final_pct"]
    groups = {}
    for days in TARGET_DURATIONS:
        pcts = TARGET_PCTS + (TARGET_PCTS_30_EXTRA if days == 30 else ())
        for pct in pcts:
            for basis in TARGET_BASES:
                for strategy in TARGET_STRATEGIES:
                    group = sorted((row for row in rows if row["days"] == days
                        and row["target_pct"] == pct and row["target_basis"] == basis
                        and row["strategy"] == strategy), key=lambda row: row["window_id"])
                    reached = [row for row in group if row["reached"]]
                    missed = [row for row in group if not row["reached"]]
                    deltas = [row["delta_vs_no_target_pct"] for row in group]
                    ci = block_bootstrap_ci(deltas, seed=seed + days + int(round(pct * 100)), samples=2000)
                    upper_bound_count = sum(bool(row["max_cash_upper_bound_reaches_target"]) for row in group)
                    hit_uptrend = [row for row in reached if row["price_change_pct"] > 20]
                    total_dust_cycles = sum(row["dust_cycle_count"] for row in group)
                    total_dust_cost = sum(row["dust_cycle_cost_usdt_total"] for row in group)
                    total_dust_sweep_fee = sum(row["dust_sweep_fee_usdt"] for row in group)
                    total_dust_swept = sum(row["dust_swept_usdt"] for row in group)
                    spacing_usdt = 100 / 10 * .009
                    groups[(days, pct, basis, strategy)] = {
                        "days": days, "target_pct": pct, "basis": basis, "strategy": strategy,
                        "window_count": len(group), "reach_fraction": len(reached) / len(group) if group else 0,
                        "reached_count": len(reached),
                        "reached_with_price_gain_over_20pct": len(hit_uptrend),
                        "cash_upper_bound_reached_count": upper_bound_count,
                        "cash_upper_bound_reach_fraction": upper_bound_count / len(group) if group else 0,
                        "max_cash_median": float(np.median([r["max_cash"] for r in group])) if group else None,
                        "max_usdt_balance_median": float(np.median([r["max_usdt_balance"] for r in group])) if group else None,
                        "max_equity_cells_median": float(np.median([r["max_equity_cells"] for r in group])) if group else None,
                        "max_equity_incl_dust_median": float(np.median([r["max_equity_incl_dust"] for r in group])) if group else None,
                        "dust_qty_final_median": float(np.median([r["dust_qty_final"] for r in group])) if group else None,
                        "cash_final_median": float(np.median([r["cash_final"] for r in group])) if group else None,
                        "pct_time_in_range_mean": float(np.mean([r["pct_time_in_range"] for r in group])) if group else None,
                        "cycles_mean": float(np.mean([r["cycles"] for r in group])) if group else None,
                        "realized_net_mean": float(np.mean([r["realized_net"] for r in group])) if group else None,
                        "dust_cycle_count_total": total_dust_cycles,
                        "dust_swept_usdt_total": total_dust_swept,
                        "dust_sweep_fee_usdt_total": total_dust_sweep_fee,
                        "dust_cycle_cost_usdt_per_cycle": total_dust_cost / total_dust_cycles if total_dust_cycles else 0.0,
                        "dust_spacing_fraction_per_cycle": total_dust_cost / total_dust_cycles / spacing_usdt
                            if total_dust_cycles else 0.0,
                        "median_time_to_target_days": (float(np.median([r["time_to_target_days"] for r in reached]))
                                                       if reached else None),
                        "miss_final_equity_median": (float(np.median([r["equity_final_pct"] for r in missed]))
                                                      if missed else None),
                        "hit_equity_at_close_median": (float(np.median([r["equity_at_close"] for r in reached]))
                                                       if reached else None),
                        "hit_equity_after_repo_liquidation_median": (float(np.median([r["equity_final_pct"] for r in reached]))
                                                                     if reached else None),
                        "delta_vs_no_target_mean_pct": float(np.mean(deltas)) if deltas else None,
                        "hit_only_delta_vs_no_target_mean_pct": (float(np.mean(
                            [row["delta_vs_no_target_pct"] for row in reached])) if reached else None),
                        "delta_vs_no_target_ci95_block": ci,
                    }
    return {"method": "descriptive_overlapping_windows", "seed": seed, "workers": workers,
            "window_days": list(TARGET_DURATIONS), "step_days": 7,
            "target_pct_values": {"30": list(TARGET_PCTS_30_EXTRA + TARGET_PCTS),
                                  "90": list(TARGET_PCTS)},
            "bases": list(TARGET_BASES), "strategies": list(TARGET_STRATEGIES),
            "fee_pct": .1, "capital": 100, "n_levels": 10, "width_pct": 9,
            "windows": {str(days): len(wins) for days, wins in windows_by_days.items()},
            "duration_s": time.perf_counter() - started,
            "summary": list(groups.values()), "rows": rows, "baseline_rows": list(baseline.values())}


def run_max_days_study(candles, *, seed=42):
    """Compare 90-day simple/SMART windows with no deadline, 30 days, and 60 days."""
    started = time.perf_counter()
    windows = build_windows(candles, 90, 7)
    rows = []
    for window in windows:
        sample = _slice(candles, window["start_idx"], window["end_idx"])
        for strategy in TARGET_STRATEGIES:
            for max_days in (None, 30, 60):
                params = {"dust_sweep_enabled": True}
                if max_days is not None:
                    params["max_days"] = max_days
                result = run_simulation(sample, strategy=strategy, n=10, capital=100,
                    width_pct=9, fee_pct=.1, resync_candles=3, params=params)
                metrics = result["metrics"]
                rows.append({"window_id": window["window_id"], "days": 90,
                    "strategy": strategy, "max_days": max_days,
                    "equity_final": metrics["equity_final_after_repository_liquidation"],
                    "repository_cells": metrics["repository_cells_at_max_days"],
                    "unrealized_pnl_at_close": metrics["unrealized_pnl_at_max_days"],
                    "max_days_reached": metrics["max_days_reached"]})
    summaries = []
    for strategy in TARGET_STRATEGIES:
        for max_days in (None, 30, 60):
            group = [row for row in rows if row["strategy"] == strategy and row["max_days"] == max_days]
            summaries.append({"strategy": strategy, "max_days": max_days, "window_count": len(group),
                "equity_final_mean": float(np.mean([row["equity_final"] for row in group])),
                "repository_cells_mean": (float(np.mean([row["repository_cells"] for row in group
                    if row["repository_cells"] is not None])) if any(
                        row["repository_cells"] is not None for row in group) else None),
                "unrealized_pnl_mean": (float(np.mean([row["unrealized_pnl_at_close"] for row in group
                    if row["unrealized_pnl_at_close"] is not None])) if any(
                        row["unrealized_pnl_at_close"] is not None for row in group) else None)})
    return {"method": "descriptive_overlapping_windows", "seed": seed, "days": 90,
        "step_days": 7, "windows": len(windows), "duration_s": time.perf_counter() - started,
        "summary": summaries, "rows": rows}
