"""Construcción voraz de carteras v2 sobre trayectorias horarias pareadas."""
from __future__ import annotations

import json
from pathlib import Path
import time

import numpy as np

from research.scenarios.metrics import summarize_outcomes
from research.scenarios.objective import objective_v2


def evaluate_trajectory_portfolio(rows, *, timestamps, capital, target, max_loss_pct,
        horizon_days, seed=43):
    """Suma trayectorias float32 y aplica el primer toque de meta o límite."""
    times = np.asarray(timestamps, dtype=np.int64)
    count = (len(rows[0]["cash"]) if rows else 1)
    used = sum(float(row["capital"]) for row in rows)
    idle = float(capital) - used
    cash = np.full((count, len(times)), idle, dtype=np.float64)
    equity = cash.copy()
    inventory_qty = np.zeros(count, dtype=np.float64)
    inventory_value = np.zeros(count, dtype=np.float64)
    for row in rows:
        cash += np.asarray(row["cash"], dtype=np.float64)
        equity += np.asarray(row["equity"], dtype=np.float64)
        inventory_qty += np.asarray(row.get("inventory_qty", np.zeros(count)), dtype=np.float64)
        inventory_value += np.asarray(row.get("inventory_value", np.zeros(count)), dtype=np.float64)
    hit_mask = cash >= float(capital) + float(target)
    hit_exists = hit_mask.any(axis=1)
    hit = np.where(hit_exists, hit_mask.argmax(axis=1), len(times))
    if max_loss_pct is not None and float(max_loss_pct) > 0:
        loss_mask = equity <= float(capital) * (1 - float(max_loss_pct) / 100)
        loss_exists = loss_mask.any(axis=1)
        loss = np.where(loss_exists, loss_mask.argmax(axis=1), len(times))
    else:
        loss_exists = np.zeros(count, dtype=bool)
        loss = np.full(count, len(times), dtype=int)
    cut = loss_exists & ((~hit_exists) | (loss <= hit))
    end = np.where(cut, loss, len(times) - 1)
    pnl = equity[np.arange(count), end] - float(capital)
    reached = hit_exists & ~cut
    days = np.where(reached,
        (times[np.minimum(hit, len(times) - 1)] - times[0]) / 86400, np.nan)
    outcomes = {"pnl_usdt": pnl, "loss_cut": cut, "reached": reached,
        "days": days, "capital_unused_usdt": max(0.0, float(capital) - used)}
    return summarize_outcomes(outcomes, capital_initial=capital,
        max_loss_pct=max_loss_pct, horizon_days=horizon_days,
        inventory_qty=inventory_qty, inventory_value=inventory_value, seed=seed)


def greedy_portfolio(archive_path, *, profile, capital, free_cash, target,
        max_loss_pct, horizon_days, profile_config, seed=43):
    """Agrega piezas si suben P(meta) al menos 2 pp y pasan objetivo 1 + 3."""
    started = time.perf_counter()
    path = Path(archive_path)
    if not path.is_file():
        return None
    try:
        with np.load(path, allow_pickle=False) as archive:
            timestamps = np.asarray(archive["timestamps"], dtype=np.int64)
            metadata = json.loads(str(archive["metadata_json"].item()))
            rows = []
            for index, item in enumerate(metadata):
                rows.append({**item,
                    "cash": np.asarray(archive[f"cash_{index}"], dtype=np.float32),
                    "equity": np.asarray(archive[f"equity_{index}"], dtype=np.float32),
                    "inventory_qty": np.asarray(archive[f"inventory_qty_{index}"], dtype=np.float32),
                    "inventory_value": np.asarray(archive[f"inventory_value_{index}"], dtype=np.float32),
                    "capital": float((item.get("piece") or {}).get("capital", 0))})
    except (OSError, ValueError, KeyError, json.JSONDecodeError):
        return None
    live = [row for row in rows if row.get("is_current")]
    candidates = [row for row in rows if not row.get("is_current")]
    config = dict(profile_config or {})
    max_grids = int(config.get("max_grids", 0))
    max_bots = int(config.get("max_bots", 0))
    coin_cap = float(capital) * float(config.get("capital_per_coin_pct", 0)) / 100
    selected = []
    current_rows = list(live)
    current_metrics = evaluate_trajectory_portfolio(current_rows, timestamps=timestamps,
        capital=capital, target=target, max_loss_pct=max_loss_pct,
        horizon_days=horizon_days, seed=seed)
    steps = []
    while candidates:
        used_cash = sum(row["capital"] for row in selected)
        used_by_symbol = {}
        grid_count = bot_count = 0
        for row in [*live, *selected]:
            piece = row.get("piece") or {}
            symbol = str(piece.get("symbol") or "").upper()
            used_by_symbol[symbol] = used_by_symbol.get(symbol, 0.0) + float(row["capital"])
            if piece.get("piece_type") == "individual_bot_grid" or piece.get("strategy") == "bot":
                bot_count += 1
            else:
                grid_count += 1
        best = None
        for candidate in candidates:
            piece = candidate.get("piece") or {}
            amount = float(candidate["capital"])
            symbol = str(piece.get("symbol") or "").upper()
            is_bot = piece.get("piece_type") == "individual_bot_grid" or piece.get("strategy") == "bot"
            if amount < 10 or amount > float(free_cash) - used_cash + 1e-8:
                continue
            if used_by_symbol.get(symbol, 0.0) + amount > coin_cap + 1e-8:
                continue
            if is_bot and bot_count >= max_bots or not is_bot and grid_count >= max_grids:
                continue
            metrics = evaluate_trajectory_portfolio([*current_rows, candidate],
                timestamps=timestamps, capital=capital, target=target,
                max_loss_pct=max_loss_pct, horizon_days=horizon_days, seed=seed)
            improvement = float(metrics["p_target"]) - float(current_metrics["p_target"])
            objective = objective_v2(metrics, perfil=profile, capital_meta=capital)
            if improvement < .02 - 1e-12 or not objective["factible"]:
                continue
            score = (improvement, float(metrics["p_target"]),
                float(metrics["pnl_mean_usdt"]))
            if best is None or score > best[0]:
                best = (score, candidate, metrics, objective)
        if best is None:
            break
        _score, candidate, metrics, objective = best
        candidates.remove(candidate)
        selected.append(candidate)
        steps.append({"candidate_key": candidate["candidate_key"],
            "symbol": (candidate.get("piece") or {}).get("symbol"),
            "strategy": (candidate.get("piece") or {}).get("strategy"),
            "capital_usdt": candidate["capital"],
            "p_target_delta_pp": round((metrics["p_target"] - current_metrics["p_target"]) * 100, 6),
            "metrics": metrics, "objective": objective})
        current_rows.append(candidate)
        current_metrics = metrics
    return {"selected": [{"candidate_key": row["candidate_key"],
            "piece": row["piece"], "metrics": row.get("metrics") or {}}
            for row in [*live, *selected]],
        "added": [{"candidate_key": row["candidate_key"], "piece": row["piece"]}
            for row in selected], "steps": steps, "metrics": current_metrics,
        "candidate_count": len(candidates) + len(selected),
        "elapsed_seconds": time.perf_counter() - started}
