"""Selección pura de una pieza adicional para la asignación Meta en sombra."""
from __future__ import annotations



def rank_with_tie_tolerance(rows, tolerance_pp=5.0):
    """Ordena por probabilidad y resuelve grupos empatados con métricas de replay."""
    pending = sorted((dict(row) for row in rows),
        key=lambda row: float(row.get("probability_replay") or 0), reverse=True)
    ranked = []
    while pending:
        leader = float(pending[0].get("probability_replay") or 0)
        tied = [row for row in pending if (leader - float(row.get("probability_replay") or 0)) * 100
            <= float(tolerance_pp) + 1e-9]
        pending = [row for row in pending if row not in tied]
        tied.sort(key=lambda row: (-float(row.get("median_progress_pct") or 0),
            float(row.get("days_median") if row.get("days_median") is not None else float("inf")),
            float(row.get("worst_drawdown_pct") or 0)))
        ranked.extend(tied)
    return ranked


def evaluate_extended_allocation(plan, current_pieces, *, free_cash, capital_initial,
        target_usdt, window_days, limits, tie_tolerance_pp=5.0):
    """Evalúa «piezas actuales + una pieza» usando curvas alineadas v3."""
    from grid.meta_operator import combine_aligned_replays

    capital_initial, free_cash = float(capital_initial), max(0.0, float(free_cash))
    current = [item for item in (current_pieces or {}).values()
        if item.get("estimate") and item.get("window_results")]
    current_alts = [{"pieces": [dict(item["piece"])], "window_results": item["window_results"]}
        for item in current]
    if current_alts:
        baseline, _ = combine_aligned_replays(current_alts, target_usdt, window_days,
            capital_initial=capital_initial, max_loss_pct=limits.get("max_loss_pct"))
    else:
        baseline = {"probability": 0.0, "median_progress_pct": 0.0, "days_median": None,
            "worst_drawdown_pct": 0.0}
    current_symbols = {}
    for item in current:
        piece = item["piece"]
        current_symbols[piece.get("symbol")] = current_symbols.get(piece.get("symbol"), 0.0) + float(piece.get("capital_usdt") or 0)
    used = sum(float(item["piece"].get("capital_usdt") or 0) for item in current)
    free_cash = min(free_cash, max(0.0, capital_initial - used))
    grids = sum(item["piece"].get("piece_type") != "individual_bot_grid" for item in current)
    bots = len(current) - grids
    step = capital_initial * .05
    minimum = max(5.5, step)
    candidates = []
    reasons = []
    geometry_counts = {}
    seen_geometries = set()
    source_alternatives = sorted(plan.get("alternatives") or [],
        key=lambda item: float(item.get("replay_probability") or
            (item.get("estimate") or {}).get("probability_replay") or 0), reverse=True)
    for alternative in source_alternatives:
        pieces = alternative.get("pieces") or []
        if len(pieces) != 1:
            continue
        source_piece = pieces[0]
        kind = source_piece.get("piece_type", "grid")
        symbol = source_piece.get("symbol")
        if not (alternative.get("testnet_openability") or {}).get("allowed", False):
            reasons.append("moneda no abrible en Testnet")
            continue
        if kind == "individual_bot_grid" and bots >= int(limits.get("max_bots", 0)):
            reasons.append("límite de bots alcanzado")
            continue
        if kind != "individual_bot_grid" and grids >= int(limits.get("max_grids", 0)):
            reasons.append("límite de grids alcanzado")
            continue
        coin_cap = capital_initial * float(limits.get("capital_per_coin_pct", 0)) / 100
        coin_free = max(0.0, coin_cap - current_symbols.get(symbol, 0.0))
        cap_limit = min(free_cash, coin_free)
        if cap_limit + 1e-8 < minimum:
            reasons.append("efectivo o tope por moneda insuficiente")
            continue
        # Limita a tres geometrías por moneda y tipo, ya priorizadas por replay.
        geometry_key = (symbol, kind, source_piece.get("strategy"),
            round(float(source_piece.get("floor") or 0), 12),
            round(float(source_piece.get("ceiling") or 0), 12),
            int(source_piece.get("n_levels") or 0),
            round(float(source_piece.get("margin_pct") or 0), 8),
            round(float(source_piece.get("bot_buy_distance_pct") or
                alternative.get("bot_buy_distance_pct") or 0), 8))
        if geometry_key not in seen_geometries and geometry_counts.get((symbol, kind), 0) >= 3:
            continue
        if geometry_key not in seen_geometries:
            seen_geometries.add(geometry_key)
            geometry_counts[(symbol, kind)] = geometry_counts.get((symbol, kind), 0) + 1
        variants = alternative.get("capital_variants") or []
        if not variants:
            reasons.append("curvas v3 por capital no disponibles")
            continue
        evaluation_rows = []
        for variant in variants:
            variant_piece = (variant.get("pieces") or [{}])[0]
            cap = float(variant_piece.get("capital_usdt") or 0)
            if cap_limit + 1e-8 >= cap >= minimum and (variant.get("testnet_openability") or {}).get("allowed"):
                evaluation_rows.append((variant, cap, variant_piece))
        for scaled, cap, variant_piece in evaluation_rows:
            if scaled is not None:
                try:
                    estimate, _ = combine_aligned_replays(current_alts + [scaled], target_usdt,
                        window_days, capital_initial=capital_initial,
                        max_loss_pct=limits.get("max_loss_pct"))
                    old_p = float(baseline.get("probability") or 0)
                    new_p = float(estimate.get("probability") or 0)
                    old_progress = float(baseline.get("median_progress_pct") or 0)
                    new_progress = float(estimate.get("median_progress_pct") or 0)
                    progress_delta = ((new_progress / old_progress - 1) * 100
                        if old_progress > 0 else (100 if new_progress > 0 else 0))
                    delta_pp = (new_p - old_p) * 100
                    accepted = delta_pp >= 2 or (abs(delta_pp) <= 2 and progress_delta >= 10)
                    candidates.append({"alternative_id": alternative.get("alternative_id"),
                        "symbol": symbol, "piece_type": kind, "strategy": source_piece.get("strategy"),
                        "probability_replay": new_p,
                        "capital_usdt": round(cap, 4), "piece": {**variant_piece, "capital_usdt": round(cap, 4)},
                        "probability_before": old_p, "probability_after": new_p,
                        "delta_probability_pp": delta_pp, "median_progress_before_pct": old_progress,
                        "median_progress_after_pct": new_progress,
                        "progress_improvement_pct": progress_delta,
                        "days_median": estimate.get("days_median"),
                        "worst_drawdown_pct": estimate.get("worst_drawdown_pct"),
                        "accepted": accepted, "reason": "supera el umbral de asignación" if accepted
                            else "no alcanza la mejora mínima"})
                except (ValueError, KeyError, TypeError):
                    reasons.append("curvas v3 no alineadas")
        # Las variantes de geometría se limitarán después del orden global.
    ranked = rank_with_tie_tolerance(candidates, tie_tolerance_pp)
    accepted = next((row for row in ranked if row["accepted"]), None)
    best = ranked[0] if ranked else None
    return {"decision": "agregar" if accepted else "no_agregar", "selected": accepted,
        "probability_before": float(baseline.get("probability") or 0),
        "best_evaluated": best, "evaluated_top": ranked[:5],
        "reason": (accepted["reason"] if accepted else
            (best["reason"] if best else (reasons[0] if reasons else "sin alternativas elegibles")))}
