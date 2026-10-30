import numpy as np

from grid.sim.data import CandleData
from grid.sim.meta_portfolio import simulate_piece_curve
from grid.sim.runner import run_simulation
from research.scenarios.smart_exact import (START_TS, evaluate_smart_scenarios,
    paired_smart_simple)


def test_smart_exact_matches_direct_runner_and_keeps_restrictions_off():
    close = 100 * np.exp(np.cumsum(np.random.default_rng(43).normal(0, .002, 60)))
    open_ = np.r_[close[0], close[:-1]]
    high = np.maximum(open_, close) * 1.002
    low = np.minimum(open_, close) * .998
    scenario = {"open": open_[None], "high": high[None],
        "low": low[None], "close": close[None]}
    sigma = np.full((1, 1), .04)
    result = evaluate_smart_scenarios(scenario, n_levels=6, capital=1000,
        low=80, high=120, sigma_path=sigma, n_smart=1, workers=1)[0]
    candles = CandleData(START_TS + np.arange(len(close), dtype=np.int64) * 300,
        open_, high, low, close, 0)
    direct = simulate_piece_curve(candles, strategy="smart", n=6, capital=1000,
        low=80, high=120, sigma_values=np.full(len(close), .04), include_replay=True)["replay"]
    assert result["metrics"]["pnl_total_net_usdt"] == direct["metrics"]["pnl_total_net_usdt"]
    assert result["effective"] == direct["effective_params"]
    assert result["effective"]["loans_enabled"] is False
    assert result["effective"]["compound_enabled"] is False
    assert result["effective"]["out_of_range_mode"] == "wait"
    assert result["scenario_index"] == 0
    paired = paired_smart_simple([result], [result["curve"]], capital_initial=1000,
        target_usdt=3, max_loss_pct=18, replicas=20)
    assert paired["n"] == 1
    assert paired["pnl_usdt"]["mean"] == 0
