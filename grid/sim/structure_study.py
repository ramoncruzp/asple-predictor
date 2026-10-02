"""Structure study: does a real sigma forecast improve grid sizing over the fixed
reference or over today's EWMA substitute?

Four structure variants are compared on the same rolling 30-day windows
(``grid.sim.sweep.build_windows``, filtered to windows with enough causal
training history for the volatility models):

- A) fixed reference structure (n=10, width=9%; the intended 4b-4 reference is a
  Claude Project document, not a repo file, and 4b-4's own conclusion was "no
  robust structure" -- so A is an arbitrary point, not an optimal baseline. See
  ``REPORTE_FASE15E.md`` sections 0 and 7).
- B) ``grid.structure.suggest_structure`` sized from today's EWMA substitute
  (``grid.sim.vol_series`` model ``"ewma"``), i.e. what the simulator already does.
- C) the same, sized from the causal Nexo-HAR forecast (model ``"nexo_har"``).
- B') width control: the same ``suggest_structure`` call as B, but with sigma
  fixed to the CONSTANT causal-median EWMA up to each window's start -- no
  information about the current moment's volatility. Isolates whether B's edge
  over A comes from the average structure size or from tracking volatility.

A 25-structure fixed-grid control (A', ``_fixed_grid_control``, the 4b-4
n in {5,8,10,15,20} x width in {4,6,9,12,16}% catalog, filtered to what is
feasible with 100 USDT via the unmodified ``grid.sim.sweep.structure_catalog``)
is also run on the same windows, to compare B against the best-performing fixed
structure chosen ex-post (an optimistic/cherry-picked bound, corrected for that
selection with a block-bootstrap of the maximum) and against the per-window
median of all 25 fixed structures.

The suggested structure is computed once per window, at the window's first
candle (``suggest_structure`` is not wired into the live ADJUST loop; see Phase
15E scope notes). Each variant is then run with both the ``simple`` and
``smart`` strategies via the unmodified ``grid.sim.runner.run_simulation``, using
the SAME causal sigma series for the smart policy's own ADJUST/PAUSE decisions as
for sizing (model "ewma" for B and B', "nexo_har" for C; A and A' keep the
simulator's default EWMA sigma, i.e. ``sigma_values=None``).
"""
from __future__ import annotations

import concurrent.futures
import time
from decimal import Decimal

import numpy as np

from grid.sim.data import ewma_sigma_24h
from grid.sim.runner import FILTERS, run_simulation
from grid.sim.sweep import STRUCTURES, _slice, block_bootstrap_interval, is_robust, structure_catalog
from grid.sim.vol_series import eligible_windows, vol_series_24h
from grid.structure import suggest_structure

FIXED_N, FIXED_WIDTH_PCT = 10, 9.0
STRATEGIES = ("simple", "smart")
VARIANTS = ("A", "B", "C", "Bprime")
CAPITAL = 100.0
FEE_PCT = 0.1
RESYNC_CANDLES = 3
MAX_DIFF_BOOTSTRAP_SAMPLES = 2000


def _structure_for_variant(variant: str, candles, window: dict, *, frame=None, csv_hash=None,
                            continuous_ewma_at_start: float | None = None,
                            causal_median_sigma: float | None = None):
    mid = float(candles.close[0])
    if variant == "A":
        return {
            "range_low": None, "range_high": None, "n_levels": FIXED_N,
            "width_pct": FIXED_WIDTH_PCT, "width_pct_realized": FIXED_WIDTH_PCT,
            "sigma_model": None, "feasible": True,
            "reasons": ["estructura fija de referencia (n=10, ancho=9%)"],
        }
    if variant == "B":
        # A freshly window-restarted EWMA is exactly 0 at candle 0 (its recursive
        # variance always starts at 0) -- a simulation-harness artifact, not a
        # realistic "current volatility" reading. Use the continuously running
        # EWMA (computed once over the whole 5-minute history) instead, which is
        # what a live deployment would actually show at that point in time.
        sigma_at_start = continuous_ewma_at_start
        model = "ewma"
    elif variant == "Bprime":
        # Width control: same suggest_structure, but sigma is a single constant
        # (the causal historical median EWMA up to this window's start), carrying
        # no information about the *current* moment's volatility. If B ~= B', the
        # B-A gain comes from the average structure size, not from tracking vol.
        sigma_at_start = causal_median_sigma
        model = "ewma_causal_median"
    else:
        sigma_series = vol_series_24h(candles, "nexo_har", window, frame=frame, csv_hash=csv_hash)
        sigma_at_start = float(sigma_series[0]) if np.isfinite(sigma_series[0]) else None
        model = "nexo_har"
    result = suggest_structure(sigma_at_start, CAPITAL, mid, FILTERS, FEE_PCT)
    result["sigma_model"] = model
    result["sigma_24h_at_start"] = sigma_at_start
    if result["feasible"]:
        result["width_pct_realized"] = (
            (float(result["range_high"]) - float(result["range_low"])) / mid * 100.0
        )
    else:
        result["width_pct_realized"] = None
    return result


def _metrics_for_run(result: dict) -> dict:
    metrics = result["metrics"]
    return {
        "pnl_pct": float(metrics["pnl_total_net_usdt"] / CAPITAL * 100),
        "max_drawdown_pct": float(metrics["max_drawdown_pct"]),
        "cycles_completed": int(metrics["cycles_completed"]),
        "pct_time_in_range": float(metrics["pct_time_in_range"]),
        "dust_cycle_cost_usdt_total": float(metrics["dust_cycle_cost_usdt_total"]),
        "dust_spacing_fraction_mean": float(metrics["dust_spacing_fraction_mean"]),
        "buy_hold_pnl_pct": float(metrics["buy_hold_pnl_usdt"] / CAPITAL * 100),
    }


_WORKER_CANDLES = None


def _init_worker(candles):
    global _WORKER_CANDLES
    _WORKER_CANDLES = candles


def _run_fixed_task(task):
    """Run one (window, n, width, strategy) fixed-grid candidate for the A' control."""
    window_id, start_idx, end_idx, n, width, strategy = task
    candles = _slice(_WORKER_CANDLES, start_idx, end_idx)
    result = run_simulation(candles, strategy=strategy, n=n, width_pct=width, capital=CAPITAL,
                            fee_pct=FEE_PCT, resync_candles=RESYNC_CANDLES, sigma_values=None)
    return {"window_id": window_id, "n_levels": n, "width_pct": width, "strategy": strategy,
            "metrics": _metrics_for_run(result)}


def _run_task(task):
    (window_id, start_idx, end_idx, variant, strategy, structure, sigma_values) = task
    candles = _slice(_WORKER_CANDLES, start_idx, end_idx)
    common = dict(capital=CAPITAL, fee_pct=FEE_PCT, resync_candles=RESYNC_CANDLES, sigma_values=sigma_values)
    if structure["n_levels"] is None:
        return {"window_id": window_id, "variant": variant, "strategy": strategy,
                "feasible": False, "metrics": None}
    if variant == "A":
        result = run_simulation(candles, strategy=strategy, n=FIXED_N, width_pct=FIXED_WIDTH_PCT, **common)
    else:
        result = run_simulation(
            candles, strategy=strategy, n=structure["n_levels"],
            low=str(structure["range_low"]), high=str(structure["range_high"]), **common,
        )
    return {"window_id": window_id, "variant": variant, "strategy": strategy,
            "feasible": True, "metrics": _metrics_for_run(result)}


def _max_fixed_bootstrap(
    b_values: np.ndarray, fixed_matrix: np.ndarray, *, block: int = 5,
    samples: int = MAX_DIFF_BOOTSTRAP_SAMPLES, confidence: float = .99, seed: int = 42,
) -> dict:
    """Block-bootstrap of B minus the BEST fixed structure, re-selecting the best
    structure inside every resample (corrects for picking the ex-post winner out
    of 25 candidates: the comparison distribution carries that selection noise).
    """
    b = np.asarray(b_values, dtype=float)
    fixed = np.asarray(fixed_matrix, dtype=float)  # shape (n_windows, n_structures)
    n = len(b)
    if n == 0 or fixed.shape[0] != n:
        return {"n": 0, "verdict": "sin ventanas comparables"}
    rng = np.random.default_rng(seed)
    block_len = max(1, int(block))
    blocks_needed = int(np.ceil(n / block_len))
    offsets = np.arange(block_len)
    diffs = np.empty(samples, dtype=float)
    for sample in range(samples):
        starts = rng.integers(0, n, size=blocks_needed)
        idx = ((starts[:, None] + offsets[None, :]) % n).reshape(-1)[:n]
        best_fixed_mean = fixed[idx].mean(axis=0).max()
        diffs[sample] = b[idx].mean() - best_fixed_mean
    point_best_fixed_mean = float(fixed.mean(axis=0).max())
    alpha = (1 - confidence) / 2
    lo, hi = (float(x) for x in np.quantile(diffs, [alpha, 1 - alpha]))
    return {
        "n": n, "block": block, "samples": samples, "confidence": confidence,
        "point_mean_b": float(b.mean()), "point_best_fixed_mean_ex_post": point_best_fixed_mean,
        "point_mean_difference": float(b.mean()) - point_best_fixed_mean,
        "ci_low": lo, "ci_high": hi, "beats_best_ex_post_corrected": bool(lo > 0),
        "verdict": ("B robustamente mejor que la MEJOR fija ex-post, incluso corrigiendo por "
                    "haberla elegido entre 25 candidatas" if lo > 0 else
                    "sin evidencia robusta de mejora sobre la mejor fija ex-post (corregido)"),
    }


def _fixed_grid_control(candles, elig, *, workers=1) -> dict:
    """A' control: the 25 (n, width) fixed structures from 4b-4, run on the same
    eligible windows, filtered to those feasible with 100 USDT at every window's
    start price (``grid.sim.sweep.structure_catalog``, reused unmodified).
    """
    valid, rejected = structure_catalog(candles, elig, capital=CAPITAL)
    tasks = [(window["window_id"], window["start_idx"], window["end_idx"], n, width, strategy)
             for n, width in valid for strategy in STRATEGIES for window in elig]
    if workers <= 1:
        _init_worker(candles)
        rows = [_run_fixed_task(task) for task in tasks]
    else:
        with concurrent.futures.ProcessPoolExecutor(
            max_workers=workers, initializer=_init_worker, initargs=(candles,)
        ) as pool:
            rows = list(pool.map(_run_fixed_task, tasks))
    by_key = {(r["window_id"], r["n_levels"], r["width_pct"], r["strategy"]): r for r in rows}
    window_ids = [w["window_id"] for w in elig]
    matrices = {}
    per_structure_summary = {}
    for strategy in STRATEGIES:
        matrix = np.asarray([[by_key[(wid, n, width, strategy)]["metrics"]["pnl_pct"]
                              for n, width in valid] for wid in window_ids], dtype=float)
        matrices[strategy] = matrix
        per_structure_summary[strategy] = [
            {"n_levels": n, "width_pct": width,
             "mean_pnl_pct": float(matrix[:, i].mean()), "median_pnl_pct": float(np.median(matrix[:, i]))}
            for i, (n, width) in enumerate(valid)
        ]
    return {
        "valid_structures": [{"n_levels": n, "width_pct": width} for n, width in valid],
        "rejected_structures": rejected,
        "matrices": matrices, "per_structure_summary": per_structure_summary,
        "window_ids": window_ids,
    }


def _structure_distribution(structures_by_window: dict, elig: list[dict], variant: str) -> dict:
    """Per-window distribution of what suggest_structure actually chose for ``variant``."""
    feasible_rows = [structures_by_window[w["window_id"]][variant] for w in elig
                     if structures_by_window[w["window_id"]][variant]["feasible"]]
    n_feasible = len(feasible_rows)
    n_total = len(elig)
    differs_from_a = sum(
        1 for row in feasible_rows
        if row["n_levels"] != FIXED_N or abs((row["width_pct_realized"] or 0) - FIXED_WIDTH_PCT) > 0.5
    )

    def _stats(key):
        values = np.asarray([row[key] for row in feasible_rows], dtype=float)
        if not len(values):
            return {"min": None, "median": None, "max": None}
        return {"min": float(values.min()), "median": float(np.median(values)), "max": float(values.max())}

    return {
        "variant": variant, "n_total_windows": n_total, "n_feasible": n_feasible,
        "feasible_fraction": n_feasible / n_total if n_total else None,
        "windows_differ_from_a_fixed_10_9pct": differs_from_a,
        "width_pct_realized": _stats("width_pct_realized"),
        "n_levels": _stats("n_levels"),
        "spacing_pct": _stats("spacing_pct"),
        "cell_usdt": {
            "min": min((float(row["cell_usdt"]) for row in feasible_rows), default=None),
            "median": (float(np.median([float(row["cell_usdt"]) for row in feasible_rows]))
                      if feasible_rows else None),
            "max": max((float(row["cell_usdt"]) for row in feasible_rows), default=None),
        },
        "net_edge_pct_per_cycle": _stats("net_edge_pct_per_cycle"),
    }


def run_structure_study(
    candles, frame, *, csv_hash: str | None = None, seed: int = 42, workers: int = 1,
    min_train_days: int | None = None,
) -> dict:
    from grid.sim.sweep import build_windows
    started = time.perf_counter()
    windows = build_windows(candles)
    kwargs = {} if min_train_days is None else {"min_train_days": min_train_days}
    elig = eligible_windows(windows, frame, **kwargs)

    full_ewma = ewma_sigma_24h(candles.close, 72.0)

    structures_by_window: dict[int, dict] = {}
    tasks = []
    for window in elig:
        sample = _slice(candles, window["start_idx"], window["end_idx"])
        continuous_ewma_at_start = float(full_ewma[window["start_idx"]])
        # Causal: only EWMA values strictly before this window's start candle.
        causal_median_sigma = float(np.median(full_ewma[: window["start_idx"]]))
        structures_by_window[window["window_id"]] = {}
        for variant in VARIANTS:
            structure = _structure_for_variant(
                variant, sample, window, frame=frame, csv_hash=csv_hash,
                continuous_ewma_at_start=continuous_ewma_at_start,
                causal_median_sigma=causal_median_sigma,
            )
            structures_by_window[window["window_id"]][variant] = structure
            # A, B and B' all keep run_simulation's default internal EWMA for
            # ongoing ADJUST decisions (today's unchanged behavior); only C's
            # smart policy actually sees the real causal forecast throughout.
            sigma_values = None
            if variant == "C" and structure["n_levels"] is not None:
                sigma_values = vol_series_24h(sample, "nexo_har", window, frame=frame, csv_hash=csv_hash)
            for strategy in STRATEGIES:
                tasks.append((window["window_id"], window["start_idx"], window["end_idx"],
                              variant, strategy, structure, sigma_values))

    if workers <= 1:
        _init_worker(candles)
        rows = [_run_task(task) for task in tasks]
    else:
        with concurrent.futures.ProcessPoolExecutor(
            max_workers=workers, initializer=_init_worker, initargs=(candles,)
        ) as pool:
            rows = list(pool.map(_run_task, tasks))

    by_key = {(r["window_id"], r["variant"], r["strategy"]): r for r in rows}

    summaries = []
    for variant in VARIANTS:
        for strategy in STRATEGIES:
            vals = [by_key[(w["window_id"], variant, strategy)]["metrics"]["pnl_pct"]
                    for w in elig if by_key[(w["window_id"], variant, strategy)]["feasible"]]
            n = len(vals)
            if n == 0:
                summaries.append({"variant": variant, "strategy": strategy, "n": 0, "robust": False,
                                  "verdict": "sin datos (ninguna ventana factible)"})
                continue
            arr = np.asarray(vals, dtype=float)
            half = n // 2
            ci99 = block_bootstrap_interval(arr, seed=seed, confidence=.99)
            mean, median = float(np.mean(arr)), float(np.median(arr))
            first = float(np.mean(arr[:half])) if half else None
            second = float(np.mean(arr[half:])) if half else None
            robust = is_robust(mean, ci99[0], first, second, median)
            summaries.append({
                "variant": variant, "strategy": strategy, "n": n, "mean_pnl_pct": mean,
                "median_pnl_pct": median, "ci99_mean_block": ci99, "first_half_mean_pct": first,
                "second_half_mean_pct": second, "robust": robust,
                "verdict": "robust" if robust else "sin evidencia",
                "feasible_fraction": n / len(elig),
            })

    def _paired(variant: str, baseline: str, strategy: str, bootstrap_seed: int, *, block: int = 5) -> dict:
        common_windows = [w for w in elig
                          if by_key[(w["window_id"], variant, strategy)]["feasible"]
                          and by_key[(w["window_id"], baseline, strategy)]["feasible"]]
        if not common_windows:
            return {"variant": variant, "baseline": baseline, "strategy": strategy, "n": 0,
                    "block": block, "verdict": "sin ventanas comparables"}
        deltas = [by_key[(w["window_id"], variant, strategy)]["metrics"]["pnl_pct"]
                  - by_key[(w["window_id"], baseline, strategy)]["metrics"]["pnl_pct"]
                  for w in common_windows]
        arr = np.asarray(deltas, dtype=float)
        n = len(arr)
        half = n // 2
        ci99 = block_bootstrap_interval(arr, seed=bootstrap_seed, confidence=.99, block=block)
        mean, median = float(np.mean(arr)), float(np.median(arr))
        first = float(np.mean(arr[:half])) if half else None
        second = float(np.mean(arr[half:])) if half else None
        robust = is_robust(mean, ci99[0], first, second, median)
        return {
            "variant": variant, "baseline": baseline, "strategy": strategy, "n": n, "block": block,
            "mean_delta_pp": mean, "median_delta_pp": median, "ci99_delta_block": ci99,
            "first_half_mean_pp": first, "second_half_mean_pp": second,
            "win_fraction": float(np.mean(arr > 0)), "robust_improvement": robust,
            "verdict": (f"{variant} robustamente mejor que {baseline}" if robust
                       else "sin evidencia de mejora robusta"),
        }

    paired = [_paired(variant, "A", strategy, seed + 7)
              for variant in ("B", "C") for strategy in STRATEGIES]
    # The central Phase 15E question: does the real forecast (C) beat today's
    # already-wired EWMA substitute (B), not just the naive fixed baseline (A)?
    paired_c_vs_b = [_paired("C", "B", strategy, seed + 23) for strategy in STRATEGIES]
    # Width control (task 3): B (time-varying EWMA sizing) vs B' (constant causal
    # median sigma, same formula otherwise). If these are statistically
    # indistinguishable, the B-A gain is about average structure size, not about
    # tracking moment-to-moment volatility.
    paired_b_vs_bprime = [_paired("B", "Bprime", strategy, seed + 31) for strategy in STRATEGIES]
    # Block-size sensitivity (task 4) on the headline B-A result specifically:
    # windows overlap ~4 steps (30d window / 7d step), so block=5 and block=9 are
    # both defensible; report whether the robust verdict survives either choice.
    block_sensitivity_b_vs_a = [
        _paired("B", "A", strategy, seed + 7, block=block)
        for strategy in STRATEGIES for block in (5, 9)
    ]

    feasibility = {
        variant: float(np.mean([
            structures_by_window[w["window_id"]][variant]["feasible"] for w in elig
        ])) if elig else None
        for variant in VARIANTS
    }

    structure_distribution = {
        variant: _structure_distribution(structures_by_window, elig, variant)
        for variant in ("B", "C", "Bprime")
    }

    # A' control (task 2): the 25 fixed structures from 4b-4, same eligible windows.
    fixed_control = _fixed_grid_control(candles, elig, workers=workers)
    fixed_vs_b = {}
    for strategy in STRATEGIES:
        matrix = fixed_control["matrices"][strategy]
        window_ids = fixed_control["window_ids"]
        b_by_window = {wid: by_key[(wid, "B", strategy)]["metrics"]["pnl_pct"]
                       for wid in window_ids if by_key[(wid, "B", strategy)]["feasible"]}
        common_idx = [i for i, wid in enumerate(window_ids) if wid in b_by_window]
        if not common_idx:
            fixed_vs_b[strategy] = {"best_ex_post": None, "median_fixed": None, "max_corrected": None}
            continue
        b_arr = np.asarray([b_by_window[window_ids[i]] for i in common_idx], dtype=float)
        sub_matrix = matrix[common_idx, :]
        structure_means = sub_matrix.mean(axis=0)
        best_idx = int(np.argmax(structure_means))
        best_n, best_width = fixed_control["valid_structures"][best_idx]["n_levels"], \
            fixed_control["valid_structures"][best_idx]["width_pct"]
        median_series = np.median(sub_matrix, axis=1)

        def _pair_arrays(a, b_vals, bootstrap_seed, block=5):
            arr = np.asarray(a, dtype=float) - np.asarray(b_vals, dtype=float)
            n = len(arr)
            half = n // 2
            ci99 = block_bootstrap_interval(arr, seed=bootstrap_seed, confidence=.99, block=block)
            mean, median = float(np.mean(arr)), float(np.median(arr))
            first = float(np.mean(arr[:half])) if half else None
            second = float(np.mean(arr[half:])) if half else None
            robust = is_robust(mean, ci99[0], first, second, median)
            return {"n": n, "mean_delta_pp": mean, "median_delta_pp": median, "ci99_delta_block": ci99,
                   "first_half_mean_pp": first, "second_half_mean_pp": second,
                   "win_fraction": float(np.mean(arr > 0)), "robust_improvement": robust}

        fixed_vs_b[strategy] = {
            "best_ex_post_structure": {"n_levels": best_n, "width_pct": best_width,
                                       "mean_pnl_pct": float(structure_means[best_idx])},
            "b_vs_best_ex_post_naive": _pair_arrays(b_arr, sub_matrix[:, best_idx], seed + 41),
            "b_vs_best_ex_post_max_corrected": _max_fixed_bootstrap(
                b_arr, sub_matrix, block=5, seed=seed + 41),
            "b_vs_median_fixed": _pair_arrays(b_arr, median_series, seed + 43),
        }

    return {
        "method": "rolling_30d_step7d_structure_abc_study",
        "seed": seed, "workers": workers, "csv_sha256": csv_hash,
        "window_count_total": len(windows), "window_count_eligible": len(elig),
        "min_train_days": min_train_days,
        "variants": {
            "A": f"fixed n={FIXED_N} width_pct={FIXED_WIDTH_PCT}",
            "B": "suggest_structure sized from today's EWMA substitute (grid.sim.vol_series 'ewma')",
            "C": "suggest_structure sized from the causal Nexo-HAR forecast ('nexo_har')",
            "Bprime": "suggest_structure sized from a CONSTANT causal-median EWMA sigma (width control)",
        },
        "feasibility_fraction": feasibility,
        "summaries": summaries,
        "paired_vs_A": paired,
        "paired_c_vs_b": paired_c_vs_b,
        "paired_b_vs_bprime": paired_b_vs_bprime,
        "block_sensitivity_b_vs_a": block_sensitivity_b_vs_a,
        "structure_distribution": structure_distribution,
        "fixed_grid_control": {
            "valid_structures": fixed_control["valid_structures"],
            "rejected_structures": fixed_control["rejected_structures"],
            "per_structure_summary": fixed_control["per_structure_summary"],
        },
        "fixed_vs_b": fixed_vs_b,
        "rows": rows,
        "duration_s": time.perf_counter() - started,
    }
