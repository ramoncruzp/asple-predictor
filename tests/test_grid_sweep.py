from __future__ import annotations

import numpy as np

from grid.sim.data import CandleData
from grid.sim.sweep import (STRUCTURES, _robust_stats, assign_ex_post_regimes,
                            block_bootstrap_interval, build_windows,
                            causal_window_features, efficiency_ratio,
                            execute_tasks, is_robust, rankdata,
                            run_sweep, structure_catalog)


def _candles(days=50, *, noisy=False):
    timestamps = np.arange(1_700_000_000, 1_700_000_000 + days * 86400, 300, dtype=np.int64)
    if noisy:
        rng = np.random.default_rng(1234)
        close = np.exp(np.cumsum(rng.normal(0, .001, len(timestamps))))
    else:
        close = np.ones(len(timestamps))
    return CandleData(timestamps, close, close * 1.001, close * .999, close, 0)


def test_windows_have_30d_span_7d_step_and_stay_inside_data():
    candles = _candles(50)
    windows = build_windows(candles)
    assert len(windows) == 3
    for window in windows:
        assert window["end_idx"] <= len(candles.timestamp)
        assert window["end_exclusive"] - window["start"] == 30 * 86400
    assert windows[1]["start"] - windows[0]["start"] == 7 * 86400


def test_causal_features_ignore_window_and_future_candles():
    candles = _candles(60)
    start = int(candles.timestamp[30 * 288])
    first_future = int(np.searchsorted(candles.timestamp, start))
    expected = {days: causal_window_features(candles, start, days) for days in (14, 30)}
    altered = _candles(60)
    altered.close[first_future:] *= 1.7
    for days in (14, 30):
        assert causal_window_features(altered, start, days) == expected[days]


def test_efficiency_ratio_and_ex_post_regime_thresholds_are_defined():
    assert efficiency_ratio([1, 2, 3, 4]) == 1.0
    assert efficiency_ratio([1, 2, 1]) == 0.0
    features = [{"return_pct": r, "er": e} for r, e in
                [(-9, .9), (-8, .8), (-7, .7), (-1, .2), (0, .3), (1, .4), (7, .7), (8, .8), (9, .9)]]
    labels, thresholds = assign_ex_post_regimes(features)
    assert labels[0] == "bearish" and labels[-1] == "bullish"
    assert set(labels) <= {"bullish", "bearish", "sideways"}
    assert "Q67" in thresholds["definition"] and thresholds["er_terciles"][0] <= thresholds["er_terciles"][1]


def test_structure_preflight_counts_rejected_combinations_and_reasons():
    candles = _candles(50)
    windows = build_windows(candles)
    valid, rejected = structure_catalog(candles, windows)
    assert set(valid) | {(x["n_levels"], x["width_pct"]) for x in rejected} == set(STRUCTURES)
    assert rejected
    assert any("GRID_MIN_STEP_PCT" in reason for row in rejected for reason in row["reasons"])
    assert any((row["n_levels"], row["width_pct"]) == (15, 4) for row in rejected)


def test_block_bootstrap_known_positive_symmetric_and_serially_clustered():
    assert block_bootstrap_interval([2.0] * 12, seed=3)[0] > 0
    symmetric = block_bootstrap_interval([-1.0, 1.0] * 10, seed=3)
    clustered = block_bootstrap_interval([-1.0] * 10 + [1.0] * 10, seed=4)
    assert symmetric[0] <= 0 <= symmetric[1]
    assert clustered[1] - clustered[0] > 1.2


def test_robust_rule_requires_every_s6_condition_independently():
    assert is_robust(1, .1, .5, .5, .8)
    assert not is_robust(0, .1, .5, .5, .8)       # mean
    assert not is_robust(1, 0, .5, .5, .8)        # lower CI99
    assert not is_robust(1, .1, 0, .5, .8)        # first chronological half
    assert not is_robust(1, .1, .5, 0, .8)        # second chronological half
    assert not is_robust(1, .1, .5, .5, 0)        # median


def test_ci99_gate_is_stricter_than_ci95_on_near_zero_edge():
    values = np.asarray([1.0] * 75 + [-1.0] * 25)
    ci95 = block_bootstrap_interval(values, seed=42, confidence=.95)
    stats = _robust_stats(values, seed=42)
    assert ci95[0] > 0
    assert stats["ci99_mean_block"][0] <= 0
    assert stats["verdict"] == "sin evidencia"


def test_synthetic_noise_has_no_robust_structure():
    rng = np.random.default_rng(911)
    returns = rng.normal(0, .7, 60)
    results = [_robust_stats(returns + rng.normal(0, .15, len(returns)), seed=i)
               for i in range(25)]
    assert not any(row["robust"] for row in results)


def test_flat_synthetic_market_is_not_labeled_robust_and_empty_causal_data_is_reported():
    result = run_sweep(_candles(37), seed=42, workers=2)
    assert result["summary"]["window_count"] == 2
    assert not any(row["robust"] for row in result["summary"]["structures"])
    assert all(row["n"] == 0 and row["spearman"] is None
               for row in result["summary"]["causal_predictability"])


def test_process_results_match_with_one_and_two_workers():
    candles = _candles(30)
    windows = build_windows(candles)
    tasks = [(0, 10, 9, "simple", .1), (0, 10, 9, "smart", .1)]
    one = execute_tasks(candles, windows, tasks, workers=1)
    two = execute_tasks(candles, windows, tasks, workers=2)
    assert one == two

