"""Deterministic walk-forward search for provisional smart-grid parameters."""
from __future__ import annotations

import concurrent.futures
import hashlib
import json
import math
import random
import time
from dataclasses import asdict

import numpy as np

from grid.policy import DEFAULT_SMART_PARAMS, validate_params
from grid.sim.data import CandleData
from grid.sim.runner import run_simulation

SEARCH_KEYS = ("pause_enter_prob", "pause_exit_prob", "stop_loss_pct", "trapped_cap_pct",
               "adjust_trigger_z", "sigma_scale", "close_out_of_range_pct")
RANGES = {"pause_enter_prob": (.05, .25), "pause_exit_prob": (.01, .12),
          "stop_loss_pct": (2.0, 12.0), "trapped_cap_pct": (25.0, 80.0),
          "adjust_trigger_z": (.3, 1.5), "sigma_scale": (.7, 1.5),
          "close_out_of_range_pct": (2.0, 12.0)}


def generate_candidates(seed: int, count: int, n_levels: int):
    if count < 1 or count > 150:
        raise ValueError("candidate count must be between 1 and 150")
    base = {key: DEFAULT_SMART_PARAMS[key] for key in SEARCH_KEYS}
    candidates = [base]
    rng = random.Random(seed)
    invalid = 0
    attempts = 0
    while len(candidates) < count and attempts < count * 30:
        attempts += 1
        candidate = {key: rng.uniform(*RANGES[key]) for key in SEARCH_KEYS}
        try:
            validate_params(candidate, n_levels)
        except ValueError:
            invalid += 1
            continue
        if candidate not in candidates:
            candidates.append(candidate)
    invalid += max(0, count - len(candidates))
    return candidates, invalid


def make_folds(candles, train_days=90, test_days=30, step_days=30):
    train_seconds, test_seconds, step_seconds = (train_days * 86400, test_days * 86400, step_days * 86400)
    start, end = int(candles.timestamp[0]), int(candles.timestamp[-1]) + 300
    folds = []
    cursor = start
    while cursor + train_seconds + test_seconds <= end:
        train_start, train_end = cursor, cursor + train_seconds
        test_end = train_end + test_seconds
        train = slice(int(np.searchsorted(candles.timestamp, train_start)), int(np.searchsorted(candles.timestamp, train_end)))
        test = slice(int(np.searchsorted(candles.timestamp, train_end)), int(np.searchsorted(candles.timestamp, test_end)))
        if train.stop - train.start > 1 and test.stop - test.start > 1:
            folds.append({"index": len(folds), "train": train, "test": test,
                          "train_start": train_start, "train_end": train_end,
                          "test_start": train_end, "test_end": test_end})
        cursor += step_seconds
    return folds


def block_bootstrap_ci(differences, seed=42, samples=2000):
    values = np.asarray(differences, dtype=float)
    if not len(values) or samples < 2000:
        raise ValueError("at least one fold and 2000 bootstrap samples required")
    rng = np.random.default_rng(seed)
    n = len(values)
    block = max(1, int(round(math.sqrt(n))))
    means = np.empty(samples)
    for j in range(samples):
        indices = []
        while len(indices) < n:
            begin = int(rng.integers(0, n))
            indices.extend((begin + k) % n for k in range(block))
        means[j] = np.mean(values[np.asarray(indices[:n])])
    return [float(x) for x in np.quantile(means, [.025, .975])]


def decision_rule(win_fraction, ci, calibrated_dd, defaults_dd):
    return "validated_walk_forward" if (win_fraction >= .60 and ci[0] > 0
           and calibrated_dd <= defaults_dd + 2.0) else "defaults_kept"


def _subset(c, sl):
    return CandleData(*(getattr(c, name)[sl] for name in ("timestamp", "open", "high", "low", "close")), gaps=0)


_WORKER_DATA = None
_WORKER_OPTIONS = None


def _init_worker(candles, options):
    global _WORKER_DATA, _WORKER_OPTIONS
    _WORKER_DATA, _WORKER_OPTIONS = candles, options


def _evaluate(c, params, options):
    try:
        run_options = {**options, "sigma_scale": float(params.get("sigma_scale", 1.0))}
        result = run_simulation(c, strategy="smart", params=params, **run_options)["metrics"]
        return {"pnl_pct": result["pnl_total_net_usdt"] / options["capital"] * 100,
                "dd_pct": result["max_drawdown_usdt"] / options["capital"] * 100,
                "max_drawdown_pct": result["max_drawdown_pct"], "metrics": result}
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}", "pnl_pct": -float("inf"), "dd_pct": float("inf")}


def _evaluate_params(params):
    return _evaluate(_WORKER_DATA, params, _WORKER_OPTIONS)


def _select(scores):
    feasible = [i for i, s in enumerate(scores) if not s.get("error") and s["dd_pct"] <= 15.0]
    pool = feasible or [i for i, s in enumerate(scores) if not s.get("error")]
    if not pool:
        return 0
    return max(pool, key=lambda i: (scores[i]["pnl_pct"], -scores[i]["dd_pct"], -i)) if feasible else min(
        pool, key=lambda i: (scores[i]["dd_pct"], -scores[i]["pnl_pct"], i))


def calibrate(candles, *, n=10, capital=100.0, width_pct=9.0, seed=42,
              candidate_count=150, workers=1, csv_hash=None):
    folds = make_folds(candles)
    if not folds:
        raise ValueError("history is too short for a 90d/30d walk-forward fold")
    candidates, invalid = generate_candidates(seed, candidate_count, n)
    started = time.perf_counter()
    options = {"n": n, "capital": capital, "width_pct": width_pct, "fee_pct": .1,
               "resync_candles": 3, "halflife_h": 72, "csv_hash": csv_hash}
    results, all_candidates = [], []
    for fold in folds:
        train = _subset(candles, fold["train"])
        test = _subset(candles, fold["test"])
        t0 = time.perf_counter()
        with concurrent.futures.ProcessPoolExecutor(max_workers=max(1, workers),
                initializer=_init_worker, initargs=(train, options)) as fold_pool:
            scores = list(fold_pool.map(_evaluate_params, candidates))
            chosen = _select(scores)
            defaults = _evaluate(test, candidates[0], options)
            calibrated = _evaluate(test, candidates[chosen], options)
            simple = run_simulation(test, strategy="simple", **options)["metrics"]
            bh = simple["buy_hold_pnl_usdt"] / capital * 100
            all_candidates.extend({"fold": fold["index"], "candidate": i, "params": p,
                                   **{k: v for k, v in score.items() if k != "metrics"}}
                                  for i, (p, score) in enumerate(zip(candidates, scores)))
            results.append({"fold": fold["index"], "train_start": fold["train_start"], "train_end": fold["train_end"],
                            "test_start": fold["test_start"], "test_end": fold["test_end"],
                            "candidate_index": chosen, "params": candidates[chosen],
                            "train_pnl_pct": scores[chosen]["pnl_pct"], "test_pnl_pct": calibrated["pnl_pct"],
                            "test_dd_capital_pct": calibrated["dd_pct"], "test_defaults_pnl_pct": defaults["pnl_pct"],
                            "test_defaults_dd_capital_pct": defaults["dd_pct"],
                            "simple_test_pnl_pct": simple["pnl_total_net_usdt"] / capital * 100,
                            "buy_hold_test_pnl_pct": bh, "duration_s": time.perf_counter() - t0,
                            "selection_scores": [{k:v for k,v in s.items() if k != "metrics"} for s in scores]})
    diffs = [r["test_pnl_pct"] - r["test_defaults_pnl_pct"] for r in results]
    ci = block_bootstrap_ci(diffs, seed=seed)
    wins = sum(x > 0 for x in diffs) / len(diffs)
    stability = {key: {"unique": len({round(float(r["params"][key]), 8) for r in results}),
                       "values": [r["params"][key] for r in results]} for key in SEARCH_KEYS}
    dd_delta = max(r["test_dd_capital_pct"] - r["test_defaults_dd_capital_pct"] for r in results)
    verdict = decision_rule(wins, ci, dd_delta, 0.0)
    final_params = dict(candidates[0])
    if verdict == "validated_walk_forward":
        with concurrent.futures.ProcessPoolExecutor(max_workers=max(1, workers), initializer=_init_worker,
                initargs=(candles, options)) as full_pool:
            full_scores = list(full_pool.map(_evaluate_params, candidates))
        final_params = candidates[_select(full_scores)]
    metrics = {"fold_count": len(results), "mean_test_pnl_pct": float(np.mean([r["test_pnl_pct"] for r in results])),
               "median_test_pnl_pct": float(np.median([r["test_pnl_pct"] for r in results])),
               "mean_defaults_test_pnl_pct": float(np.mean([r["test_defaults_pnl_pct"] for r in results])),
               "mean_delta_pct": float(np.mean(diffs)), "delta_ci95_block_bootstrap": ci,
               "win_fraction": wins, "max_test_dd_delta_pp": dd_delta,
               "parameter_stability": stability,
               "worst_fold": min(results, key=lambda x: x["test_pnl_pct"])["fold"],
               "candidate_count_valid": len(candidates), "invalid_combinations": invalid,
               "workers": max(1, workers), "duration_s": time.perf_counter() - started,
               "decision_rule": "win_fraction>=0.60 and CI95 lower>0 and every test fold DD increase <=2pp"}
    return {"method": "walk_forward_90d_30d_step30d", "seed": seed, "data_sha256": csv_hash,
            "data_start": int(candles.timestamp[0]), "data_end": int(candles.timestamp[-1]),
            "params": final_params, "metrics": metrics, "verdict": verdict,
            "folds": results, "candidates": all_candidates}
