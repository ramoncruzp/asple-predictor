"""Construcción conservadora de candidatos para evaluación del motor v2."""
from __future__ import annotations

import hashlib
import json
from decimal import Decimal

import numpy as np

from grid.levels import GridConfigError, compute_lines, plan_cells
from grid.meta import DEFAULT_PROFILES


def _amount(value, symbol):
    return float(value[symbol]) if isinstance(value, dict) else float(value)


def _key(piece):
    raw = json.dumps(piece, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def _market(price):
    return {"bid_price": price * (1 - 1e-8), "ask_price": price * (1 + 1e-8),
        "avg_price": price}


def _simple_candidate(symbol, capital, levels, width_sigma, price, sigma, horizon_days,
        filters):
    width = float(width_sigma) * float(sigma) * np.sqrt(float(horizon_days))
    low, high = price * np.exp(-width), price * np.exp(width)
    lines = compute_lines(str(low), str(high), int(levels), filters)
    plans = plan_cells(lines, str(capital), _market(price), filters,
        {"grid_min_step_pct": .003, "capital_max_por_nivel_pct": .30})
    # FastSim aplica 0,1 % de comisión y vuelve a redondear la venta neta.
    # Repetimos la comprobación con esa misma cantidad para descartar antes.
    fee = Decimal("0.001")
    for plan in plans:
        net_qty = filters.round_qty_down(plan.qty * (Decimal(1) - fee))
        if net_qty < filters.min_qty or net_qty * plan.sell_price < filters.min_notional:
            raise ValueError(f"level {plan.level_idx} net sell does not meet quantity/notional filters")
    return {"symbol": symbol, "strategy": "simple", "capital": float(capital),
        "n": int(levels), "width_sigma": float(width_sigma), "low": float(low),
        "high": float(high), "candidate_key": None}


def _bot_candidate(symbol, capital, buy_offset, sell_offset, price, filters, profile):
    buy = float(price) * (1 + float(buy_offset) / 100)
    sell = float(price) * (1 + float(sell_offset) / 100)
    fee = Decimal("0.001")
    qty = filters.round_qty_down(Decimal(str(capital)) / (Decimal(str(buy)) * (1 + fee)))
    net_sell_qty = filters.round_qty_down(qty * (1 - fee))
    if qty < filters.min_qty or qty * Decimal(str(buy)) < filters.min_notional:
        raise ValueError("notional mínimo de compra")
    if net_sell_qty < filters.min_qty or net_sell_qty * Decimal(str(sell)) < filters.min_notional:
        raise ValueError("notional mínimo de venta")
    margin = (sell / buy - 1) * 100 - .2
    minimum = float(profile.get("min_margin_pct", .3))
    if margin < minimum:
        raise ValueError("margen inferior al mínimo del perfil")
    return {"symbol": symbol, "strategy": "bot", "capital": float(capital),
        "n": 1, "buy_offset_pct": float(buy_offset), "sell_offset_pct": float(sell_offset),
        "buy_price": buy, "sell_price": sell, "candidate_key": None}


def build_smart_candidates(top_simple, *, filters_by_symbol=None):
    """Crea la segunda pasada Smart usando solo las dos mejores geometrías Simple."""
    smart, seen = [], set()
    for item in list(top_simple or [])[:2]:
        piece = item.get("piece", item)
        if piece.get("strategy") != "simple":
            continue
        symbol = str(piece["symbol"]).upper()
        geometry = (symbol, int(piece.get("n", 6)), float(piece["width_sigma"]),
            float(piece["capital"]))
        if geometry in seen:
            continue
        seen.add(geometry)
        candidate = {key: value for key, value in piece.items()
            if key not in {"candidate_key", "label"}}
        candidate["strategy"] = "smart"
        candidate["n"] = 6
        candidate["candidate_key"] = _key(candidate)
        smart.append(candidate)
    return smart


def build_candidates(*, symbols, capital_libre, perfil, filters_by_symbol,
        price_by_symbol, sigma_by_symbol, horizon_days, top_simple=None,
        capital_max_by_symbol=None, max_grids=None, max_bots=None,
        current_grids=0, current_bots=0):
    """Genera grids, bots, hasta dos Smart de segunda pasada y USDT quieto.

    Devuelve ``candidates``, ``smart_candidates`` y descartes agrupados por
    motivo. La primera pasada no decide: el llamador evalúa Simple/bot y entrega
    a ``top_simple`` sus dos Simple mejor clasificadas para la pasada Smart.
    """
    profile = DEFAULT_PROFILES[str(perfil).upper()]
    candidates, discarded, discarded_by_symbol = [], {}, {}
    def discard(reason, symbol, count=1):
        discarded[reason] = discarded.get(reason, 0) + count
        reasons = discarded_by_symbol.setdefault(symbol, {})
        reasons[reason] = reasons.get(reason, 0) + count
    grid_slots = (int(profile.get("max_grids", 0)) - int(current_grids)
        if max_grids is None else int(max_grids) - int(current_grids))
    bot_slots = (int(profile.get("max_bots", 0)) - int(current_bots)
        if max_bots is None else int(max_bots) - int(current_bots))
    for raw_symbol in symbols:
        symbol = str(raw_symbol).upper()
        filters = filters_by_symbol.get(symbol)
        if filters is None:
            discard("filtros ausentes", symbol)
            continue
        price, sigma = float(price_by_symbol[symbol]), float(sigma_by_symbol[symbol])
        free = _amount(capital_libre, symbol)
        if capital_max_by_symbol is not None:
            free = min(free, _amount(capital_max_by_symbol, symbol))
        if price <= 0 or sigma <= 0 or free <= 0:
            reason = ("límite de capital por moneda alcanzado" if
                capital_max_by_symbol is not None and _amount(capital_max_by_symbol, symbol) <= 0
                else "precio, sigma o capital no positivo")
            discard(reason, symbol, 30)
            continue
        for fraction in (1.0, .7, .5):
            capital = free * fraction
            if capital < 10:
                discard("capital inferior al mínimo de 10 USDT por pieza", symbol, 10)
                continue
            if grid_slots > 0:
                for levels in (6, 10):
                    for width in (.5, 1.0, 2.0):
                        try:
                            candidates.append(_simple_candidate(symbol, capital, levels, width,
                                price, sigma, horizon_days, filters))
                        except (GridConfigError, ValueError, ArithmeticError) as exc:
                            reason = str(exc).split(":", 1)[0]
                            discard(reason, symbol)
            else:
                discard("máximo de grids del perfil alcanzado", symbol, 18)
            if bot_slots > 0:
                for buy_offset, sell_offset in ((-1.5, 1.5), (-1.5, 3.0),
                        (-3.0, 1.5), (-3.0, 3.0)):
                    try:
                        candidates.append(_bot_candidate(symbol, capital, buy_offset,
                            sell_offset, price, filters, profile))
                    except (GridConfigError, ValueError, ArithmeticError) as exc:
                        reason = str(exc).split(":", 1)[0]
                        discard(reason, symbol)
            else:
                discard("máximo de bots del perfil alcanzado", symbol, 12)
    for candidate in candidates:
        candidate["candidate_key"] = _key({key: value for key, value in candidate.items()
            if key != "candidate_key"})
    quiet = {"symbol": None, "strategy": "usdt_quieto", "capital": 0.0,
        "n": 0, "candidate_key": _key({"strategy": "usdt_quieto"})}
    smart = build_smart_candidates(top_simple)
    return {"candidates": candidates + [quiet], "smart_candidates": smart,
        "discarded": discarded, "discarded_by_symbol": discarded_by_symbol,
        "invalid_count": sum(discarded.values())}
