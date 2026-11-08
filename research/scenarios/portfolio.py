"""Construcción voraz de carteras v2 sobre trayectorias horarias pareadas."""
from __future__ import annotations

import json
from pathlib import Path
import time

import numpy as np

from research.scenarios.metrics import summarize_outcomes
from research.scenarios.objective import objective_v2


def evaluate_trajectory_portfolio(rows, *, timestamps, capital, target, max_loss_pct,
        horizon_days, seed=43, loss_confirm_minutes=2):
    """Suma trayectorias float32 y aplica el primer toque de meta o límite."""
    times = np.asarray(timestamps, dtype=np.int64)
    # La caché puede cubrir más días que los que le quedan a la meta. Recorta
    # por tiempo antes de combinar series; nunca se deben reutilizar índices
    # de una resolución para indexar otra.
    if len(times) and horizon_days is not None:
        end_at = int(times[0] + max(0.0, float(horizon_days)) * 86400)
        hourly_mask = times <= end_at
        times = times[hourly_mask]
    else:
        hourly_mask = slice(None)
    count = (len(rows[0]["cash"]) if rows else 1)
    used = sum(float(row["capital"]) for row in rows)
    idle = float(capital) - used
    cash = np.full((count, len(times)), idle, dtype=np.float64)
    equity = cash.copy()
    inventory_qty = np.zeros(count, dtype=np.float64)
    inventory_value = np.zeros(count, dtype=np.float64)
    for row in rows:
        cash += np.asarray(row["cash"], dtype=np.float64)[:, hourly_mask]
        equity += np.asarray(row["equity"], dtype=np.float64)[:, hourly_mask]
        inventory_qty += np.asarray(row.get("inventory_qty", np.zeros(count)), dtype=np.float64)
        inventory_value += np.asarray(row.get("inventory_value", np.zeros(count)), dtype=np.float64)
    low_rows = [row.get("low_equity_5m") for row in rows]
    has_low_path = bool(rows) and all(value is not None for value in low_rows)
    if has_low_path:
        timestamps_5m = np.asarray(rows[0].get("timestamps_5m"), dtype=np.int64)
        if len(times) and horizon_days is not None:
            low_mask = (timestamps_5m >= times[0]) & (timestamps_5m <= end_at)
            timestamps_5m = timestamps_5m[low_mask]
        else:
            low_mask = slice(None)
        has_low_path = bool(len(timestamps_5m)) and all(np.array_equal(timestamps_5m,
            np.asarray(row.get("timestamps_5m"), dtype=np.int64)[low_mask]) for row in rows[1:])
    if has_low_path:
        low_equity = np.full((count, len(timestamps_5m)), idle, dtype=np.float64)
        for values in low_rows:
            low_equity += np.asarray(values, dtype=np.float64)[:, low_mask]
    hit_mask = cash >= float(capital) + float(target)
    hit_exists = hit_mask.any(axis=1)
    hit = np.where(hit_exists, hit_mask.argmax(axis=1), len(times))
    if max_loss_pct is not None and float(max_loss_pct) > 0:
        if has_low_path:
            breach = low_equity <= float(capital) * (1 - float(max_loss_pct) / 100)
            confirmations = max(1, int(np.ceil(max(0, int(loss_confirm_minutes)) / 5)))
            if confirmations > 1:
                kernel = np.ones(confirmations, dtype=np.int8)
                breach_count = np.apply_along_axis(lambda row: np.convolve(
                    row.astype(np.int8), kernel, mode="valid"), 1, breach)
                found = breach_count >= confirmations
                loss_exists = found.any(axis=1)
                loss = np.where(loss_exists, found.argmax(axis=1) + confirmations - 1,
                    len(timestamps_5m))
            else:
                loss_exists = breach.any(axis=1)
                loss = np.where(loss_exists, breach.argmax(axis=1), len(timestamps_5m))
            cut_time = np.where(loss_exists, timestamps_5m[np.minimum(loss,
                len(timestamps_5m) - 1)], np.iinfo(np.int64).max)
            hit_time = times[np.minimum(hit, len(times) - 1)]
            cut = loss_exists & ((~hit_exists) | (cut_time <= hit_time))
        else:
            loss_mask = equity <= float(capital) * (1 - float(max_loss_pct) / 100)
            loss_exists = loss_mask.any(axis=1)
            loss = np.where(loss_exists, loss_mask.argmax(axis=1), len(times))
            cut = loss_exists & ((~hit_exists) | (loss <= hit))
    else:
        loss_exists = np.zeros(count, dtype=bool)
        loss = np.full(count, len(times), dtype=int)
        cut = np.zeros(count, dtype=bool)
    if has_low_path and max_loss_pct is not None and float(max_loss_pct) > 0:
        cut_at = timestamps_5m[np.minimum(loss, len(timestamps_5m) - 1)]
        # Punto horario correspondiente al corte, para inventario y métricas
        # que usan la trayectoria horaria. El PnL de corte usa el low exacto.
        hourly_cut = np.maximum(0, np.searchsorted(times, cut_at, side="right") - 1)
        end = np.where(cut, hourly_cut, len(times) - 1)
    else:
        end = np.where(cut, loss, len(times) - 1)
    if has_low_path and max_loss_pct is not None and float(max_loss_pct) > 0:
        cut_equity = low_equity[np.arange(count), np.minimum(loss, len(timestamps_5m) - 1)]
        pnl = np.where(cut, cut_equity, equity[np.arange(count), end]) - float(capital)
    else:
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
        max_loss_pct, horizon_days, profile_config, seed=43, exploration=False):
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
                    "low_equity_5m": (np.asarray(archive[f"low_equity_5m_{index}"], dtype=np.float32)
                        if f"low_equity_5m_{index}" in archive.files else None),
                    "timestamps_5m": (np.asarray(archive["timestamps_5m"], dtype=np.int64)
                        if "timestamps_5m" in archive.files else None),
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
        horizon_days=horizon_days, seed=seed,
        loss_confirm_minutes=config.get("loss_confirm_minutes", 2))
    baseline_metrics = current_metrics
    exploration_mode = bool(exploration and max((float(row.get("metrics", {}).get(
        "p_target") or 0) for row in rows), default=0.0) < .05)
    steps = []
    last_attempts = []
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
        attempts = []
        for candidate in candidates:
            piece = candidate.get("piece") or {}
            amount = float(candidate["capital"])
            symbol = str(piece.get("symbol") or "").upper()
            is_bot = piece.get("piece_type") == "individual_bot_grid" or piece.get("strategy") == "bot"
            if amount < 10 or amount > float(free_cash) - used_cash + 1e-8:
                attempts.append({"candidate_key": candidate["candidate_key"],
                    "symbol": symbol, "strategy": piece.get("strategy"),
                    "reason": "no cabe por el límite de efectivo disponible"})
                continue
            if used_by_symbol.get(symbol, 0.0) + amount > coin_cap + 1e-8:
                attempts.append({"candidate_key": candidate["candidate_key"],
                    "symbol": symbol, "strategy": piece.get("strategy"),
                    "reason": "no cabe por el límite de capital por moneda"})
                continue
            if is_bot and bot_count >= max_bots or not is_bot and grid_count >= max_grids:
                attempts.append({"candidate_key": candidate["candidate_key"],
                    "symbol": symbol, "strategy": piece.get("strategy"),
                    "reason": "no cabe por el máximo de piezas del perfil"})
                continue
            metrics = evaluate_trajectory_portfolio([*current_rows, candidate],
                timestamps=timestamps, capital=capital, target=target,
                max_loss_pct=max_loss_pct, horizon_days=horizon_days, seed=seed,
                loss_confirm_minutes=config.get("loss_confirm_minutes", 2))
            improvement = float(metrics["p_target"]) - float(current_metrics["p_target"])
            objective = objective_v2(metrics, perfil=profile, capital_meta=capital,
                ev_floor_pct=None if exploration_mode else 1.0,
                cut_slippage_expected_max_pct=float(config.get(
                    "cut_slippage_expected_max_pct", .5)))
            pnl_improvement = float(metrics["pnl_mean_usdt"]) - float(current_metrics["pnl_mean_usdt"])
            below_threshold = (pnl_improvement < .2 - 1e-12 if exploration_mode else
                improvement < .02 - 1e-12)
            if below_threshold or not objective["factible"]:
                reason = ("rompe los lÃ­mites de riesgo: " + "; ".join(objective["motivos"])
                    if not objective["factible"] else
                    "no sube la ganancia esperada al menos 0,2 USDT" if exploration_mode else
                    "no sube P(meta) al menos 2 pp")
                attempts.append({"candidate_key": candidate["candidate_key"],
                    "symbol": symbol, "strategy": piece.get("strategy"),
                    "p_target": metrics.get("p_target"), "reason": reason})
                continue
            score = ((pnl_improvement, float(metrics.get("cycles_expected") or 0))
                if exploration_mode else (improvement, float(metrics["p_target"]),
                    float(metrics["pnl_mean_usdt"])))
            if best is None or score > best[0]:
                best = (score, candidate, metrics, objective)
        if best is None:
            last_attempts = sorted(attempts, key=lambda row: (
                -float(row.get("p_target", 0)), str(row.get("symbol") or "")))[:5]
            break
        _score, candidate, metrics, objective = best
        candidates.remove(candidate)
        last_attempts = attempts
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
        "not_added_reasons": last_attempts, "metrics_before": baseline_metrics,
        "mode": "exploracion" if exploration_mode else "meta",
        "candidate_count": len(candidates) + len(selected),
        "elapsed_seconds": time.perf_counter() - started}
