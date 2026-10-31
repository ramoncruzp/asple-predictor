"""Recomendación experimental de geometrías sin conexión al operador."""
from __future__ import annotations

import math


def recommend_grid(symbol, strategy, capital, horizon_days, *, price_now,
        sigma_24h, db_path="market_data.db", library_dir="data/derived/scenario_lib",
        n_scenarios=1000, n_smart=200, seed=43, max_loss_pct=18.0,
        n_levels=6, width_multipliers=(0.5, 1.0, 2.0),
        bot_offsets=(-1.5, -3.0), evaluator=None):
    """Devuelve geometrías ordenadas por P(ganancia > 0) y ganancia media.

    `sigma_24h` es sigma logarítmica acumulada de 24 horas. La amplitud de un
    grid es `multiplicador * sigma_24h * sqrt(horizon_days)` en unidades log.
    El límite de pérdida se aplica en FastSim/Smart exacto por escenario. Esta
    función es experimental: no lee ni escribe la base de la aplicación y no
    cambia ni propone órdenes reales.
    """
    import numpy as np

    from research.scenarios.evaluate import evaluate_pieces

    symbol = str(symbol).upper()
    strategy = str(strategy).lower()
    capital = float(capital)
    horizon_days = int(horizon_days)
    price_now = float(price_now)
    sigma_24h = float(sigma_24h)
    if strategy not in {"simple", "smart", "bot"}:
        raise ValueError("strategy debe ser simple, smart o bot")
    if capital <= 0 or horizon_days <= 0 or price_now <= 0 or sigma_24h <= 0:
        raise ValueError("capital, horizonte, precio y sigma deben ser positivos")

    pieces = []
    if strategy == "bot":
        for offset in bot_offsets:
            offset = abs(float(offset))
            pieces.append({"symbol": symbol, "strategy": "bot", "capital": capital,
                "buy_offset_pct": -offset, "sell_offset_pct": offset,
                "candidate_key": f"bot:{offset:g}"})
    else:
        for multiplier in width_multipliers:
            multiplier = float(multiplier)
            width = multiplier * sigma_24h * math.sqrt(horizon_days)
            pieces.append({"symbol": symbol, "strategy": strategy, "capital": capital,
                "low": price_now * math.exp(-width), "high": price_now * math.exp(width),
                "n": int(n_levels),
                "candidate_key": f"{strategy}:{n_levels}:{multiplier:g}sigma"})
    runner = evaluator or evaluate_pieces
    result = runner(pieces, perfil="RAPIDO", capital=capital, meta=capital * 1_000_000,
        n_scenarios=int(n_scenarios), n_smart=min(int(n_smart), int(n_scenarios)),
        seed=int(seed), db_path=db_path, library_dir=library_dir,
        mode="alternativas", max_loss_pct=float(max_loss_pct),
        horizon_days=horizon_days)
    recommendations = []
    for row in result.get("pieces", []):
        metrics = row.get("metrics") or {}
        outcomes = row.get("scenario_results") or []
        pnl = np.asarray([float(item["pnl_usdt"]) for item in outcomes], dtype=float)
        p_profit = float(np.mean(pnl > 0)) if len(pnl) else 0.0
        recommendations.append({"piece": row.get("piece", {}),
            "p_profit": p_profit,
            "pnl_mean_usdt": float(np.mean(pnl)) if len(pnl) else float(metrics.get("pnl_mean_usdt", 0)),
            "p_loss": float(np.mean([bool(item["loss_cut"]) for item in outcomes])) if outcomes else 0.0,
            "n": int(len(pnl)), "aviso": row.get("aviso")})
    recommendations.sort(key=lambda row: (-row["p_profit"], -row["pnl_mean_usdt"]))
    return {"symbol": symbol, "strategy": strategy, "capital": capital,
        "horizon_days": horizon_days, "max_loss_pct": float(max_loss_pct),
        "recommendations": recommendations,
        "skipped_symbols": result.get("skipped_symbols", {})}
