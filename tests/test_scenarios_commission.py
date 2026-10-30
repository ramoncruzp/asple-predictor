import numpy as np

from grid.sim.runner import FILTERS
from research.scenarios.evaluate import paired_fee_sensitivity
from research.scenarios.fastsim import simulate_simple


def test_zero_fee_grid_is_not_worse_than_point_one_percent_without_trend():
    count = 20 * 288
    index = np.arange(count, dtype=np.float64)
    close = 100 + 18 * np.sin(index * 2 * np.pi / 96)
    open_ = np.r_[close[0], close[:-1]]
    scenarios = {"open": open_[None, :], "high": (np.maximum(open_, close) + .2)[None, :],
        "low": (np.minimum(open_, close) - .2)[None, :], "close": close[None, :]}
    common = {"n_levels": 6, "capital": 1000, "low": 80, "high": 120,
        "filters": FILTERS, "target_usdt": 3}

    free = simulate_simple(scenarios, **common, fee_pct=0)
    standard = simulate_simple(scenarios, **common, fee_pct=.1)

    assert free["fees_usdt"][0] == 0
    assert standard["fees_usdt"][0] > 0
    assert free["pnl_usdt"][0] >= standard["pnl_usdt"][0]


def test_fee_sensitivity_pairs_same_scenario_ids_and_returns_requested_deltas():
    base = {"fee_pct": .1, "pieces": [{"scenario_results": [
        {"scenario_index": 0, "pnl_usdt": -1, "reached": False, "loss_cut": False},
        {"scenario_index": 1, "pnl_usdt": 2, "reached": True, "loss_cut": False},
        {"scenario_index": 2, "pnl_usdt": -3, "reached": False, "loss_cut": True}]}]}
    comparison = {"fee_pct": .075, "pieces": [{"scenario_results": [
        {"scenario_index": 0, "pnl_usdt": 0, "reached": False, "loss_cut": False},
        {"scenario_index": 1, "pnl_usdt": 3, "reached": True, "loss_cut": False}]}]}

    row = paired_fee_sensitivity(base, comparison, seed=43)[0]

    assert row["scenario_indices"] == [0, 1]
    assert row["n"] == 2
    assert row["pnl_usdt"]["mean"] == 1.0
    assert row["p_target"]["mean"] == 0.0
    assert row["p_loss"]["mean"] == 0.0
