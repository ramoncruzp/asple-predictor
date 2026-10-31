import numpy as np

from grid.sim.meta_portfolio import pack_curve
from research.scenarios.metrics import summarize, summarize_portfolio


def _curve(capital, cash, equity):
    return pack_curve([0, 900], cash, equity, capital)


def test_piece_and_portfolio_metrics_are_paired_and_reproducible():
    first = [_curve(40, [40, 43], [40, 42]), _curve(40, [40, 38], [40, 37])]
    second = [_curve(30, [30, 31], [30, 31]), _curve(30, [30, 30], [30, 29])]
    first_metrics = summarize(first, capital_initial=40, target_usdt=2,
        max_loss_pct=10, horizon_days=1, cycles=[1, 0], inventory_qty=[0, 2],
        inventory_value=[0, 20], seed=43, replicas=100)
    assert first_metrics["cvar_5_usdt"] <= first_metrics["pnl_p10_usdt"]
    assert first_metrics == summarize(first, capital_initial=40, target_usdt=2,
        max_loss_pct=10, horizon_days=1, cycles=[1, 0], inventory_qty=[0, 2],
        inventory_value=[0, 20], seed=43, replicas=100)
    portfolio = summarize_portfolio([first, second], capital_initial=100,
        target_usdt=3, max_loss_pct=10, horizon_days=1, seed=43, replicas=100)
    assert portfolio["capital_unused_usdt"] == 30


def test_cut_slippage_measures_overshoot_only_on_loss_cut_scenarios():
    curves = [_curve(40, [40, 40], [40, 32]),
        _curve(40, [40, 40], [40, 38])]
    result = summarize(curves, capital_initial=40, target_usdt=2,
        max_loss_pct=10, horizon_days=1, replicas=20)
    assert result["cut_slippage_mean_usdt"] == 4.0
    assert result["cut_slippage_p90_usdt"] == 4.0
