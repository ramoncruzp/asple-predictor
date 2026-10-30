"""Métricas descriptivas por escenario y por cartera."""
from __future__ import annotations

import math

import numpy as np

from grid.sim.meta_portfolio import evaluate_portfolio


def summarize(curves, *, capital_initial, target_usdt, max_loss_pct=None,
        horizon_days, cycles=None, inventory_qty=None, inventory_value=None,
        seed=43, replicas=1000):
    """Resume resultados pareados. Cada curva corresponde a un escenario."""
    count = len(curves)
    if count < 1:
        raise ValueError("se requiere al menos una curva")
    outcomes = [evaluate_portfolio([curve], capital_initial=capital_initial,
        target_usdt=target_usdt, max_loss_pct=max_loss_pct) for curve in curves]
    pnl = np.asarray([item["pnl_usdt"] for item in outcomes], dtype=float)
    losses = np.asarray([item["loss_cut"] for item in outcomes], dtype=float)
    reached = np.asarray([item["reached"] for item in outcomes], dtype=float)
    days = np.asarray([item["days"] for item in outcomes if item["days"] is not None], dtype=float)
    tail_count = max(1, int(math.ceil(count * .05)))
    rng = np.random.default_rng(seed)
    samples = rng.integers(0, count, size=(int(replicas), count))
    pnl_means = pnl[samples].mean(axis=1)
    loss_rates = losses[samples].mean(axis=1)
    return {"n": count, "pnl_mean_usdt": float(pnl.mean()),
        "pnl_per_day_mean_usdt": float(pnl.mean() / float(horizon_days)),
        "pnl_p10_usdt": float(np.quantile(pnl, .1)),
        "pnl_p50_usdt": float(np.quantile(pnl, .5)),
        "pnl_p90_usdt": float(np.quantile(pnl, .9)),
        "p_loss": float(losses.mean()), "cvar_5_usdt": float(np.sort(pnl)[:tail_count].mean()),
        "p_target": float(reached.mean()),
        "days_to_target_p10": float(np.quantile(days, .1)) if len(days) else None,
        "days_to_target_p50": float(np.quantile(days, .5)) if len(days) else None,
        "days_to_target_p90": float(np.quantile(days, .9)) if len(days) else None,
        "cycles_expected": float(np.mean(cycles)) if cycles is not None else None,
        "p_inventory": float(np.mean(np.asarray(inventory_qty) > 0)) if inventory_qty is not None else None,
        "inventory_value_mean_usdt": float(np.mean(inventory_value)) if inventory_value is not None else None,
        "capital_unused_usdt": float(outcomes[0]["capital_unused_usdt"]),
        "pnl_mean_ci95": [float(np.quantile(pnl_means, .025)), float(np.quantile(pnl_means, .975))],
        "p_loss_ci95": [float(np.quantile(loss_rates, .025)), float(np.quantile(loss_rates, .975))]}


def summarize_portfolio(curves_by_piece, *, capital_initial, target_usdt,
        max_loss_pct=None, horizon_days, seed=43, replicas=1000):
    """Suma las curvas del mismo índice de escenario y agrega efectivo ocioso."""
    if not curves_by_piece or not curves_by_piece[0]:
        raise ValueError("cartera sin curvas")
    count = min(len(curves) for curves in curves_by_piece)
    combined = []
    for index in range(count):
        rows = [curves[index] for curves in curves_by_piece]
        combined.append(evaluate_portfolio(rows, capital_initial=capital_initial,
            target_usdt=target_usdt, max_loss_pct=max_loss_pct))
    pnl = np.asarray([row["pnl_usdt"] for row in combined], dtype=float)
    loss = np.asarray([row["loss_cut"] for row in combined], dtype=float)
    reached = np.asarray([row["reached"] for row in combined], dtype=float)
    tail = max(1, int(math.ceil(count * .05)))
    rng = np.random.default_rng(seed)
    sample = rng.integers(0, count, (int(replicas), count))
    return {"n": count, "pnl_mean_usdt": float(pnl.mean()),
        "pnl_per_day_mean_usdt": float(pnl.mean() / float(horizon_days)),
        "pnl_p10_usdt": float(np.quantile(pnl, .1)), "pnl_p50_usdt": float(np.quantile(pnl, .5)),
        "pnl_p90_usdt": float(np.quantile(pnl, .9)), "p_loss": float(loss.mean()),
        "cvar_5_usdt": float(np.sort(pnl)[:tail].mean()), "p_target": float(reached.mean()),
        "pnl_mean_ci95": [float(v) for v in np.quantile(pnl[sample].mean(axis=1), [.025, .975])],
        "p_loss_ci95": [float(v) for v in np.quantile(loss[sample].mean(axis=1), [.025, .975])],
        "capital_unused_usdt": float(combined[0]["capital_unused_usdt"])}
