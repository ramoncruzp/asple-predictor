"""Regla pura para comparar actividad de piezas y carteras destino."""
from __future__ import annotations

from math import erf, exp, isfinite, log, sqrt


GRID_LEVEL_COUNTS = (10, 8, 6, 4)
BOT_SIGMA_OFFSETS = (.5, 1.0, 1.5, 2.0)


def _normal_survival(value):
    return .5 * (1.0 - erf(float(value) / sqrt(2.0)))


def piece_activity(piece, market, *, now_s=None):
    """Estima operaciones/día según distancia a órdenes y geometría actual."""
    sigma = float(market.get("sigma_24h") or 0)
    mid = float(market.get("mid") or 0)
    if sigma <= 0 or mid <= 0:
        return 0.0
    strategy = str(piece.get("strategy") or "simple").lower()
    if piece.get("piece_type") == "individual_bot_grid" or strategy == "bot":
        orders = piece.get("pending_prices") or []
        if not orders:
            orders = [piece.get("pending_price") or piece.get("sell_price") or
                piece.get("buy_price")]
        probabilities = []
        for order in orders:
            try:
                distance = abs(log(float(order) / mid))
            except (TypeError, ValueError, ZeroDivisionError):
                continue
            probabilities.append(min(1.0, 2.0 * _normal_survival(distance / sigma)))
        return max(probabilities, default=0.0)
    low, high = float(piece.get("low") or 0), float(piece.get("high") or 0)
    levels = max(1, int(piece.get("n_levels") or piece.get("n") or 1))
    if low <= 0 or high <= low:
        return 0.0
    step_pct = ((high / low) ** (1.0 / levels)) - 1.0
    if step_pct <= 0:
        return 0.0
    if mid < low or mid > high:
        distance = low / mid - 1.0 if mid < low else mid / high - 1.0
        if distance > step_pct:
            return 0.0
    return min(10.0, sigma / step_pct)


def _valid_market(market):
    try:
        sigma = float(market.get("sigma_24h"))
        mid = float(market.get("mid"))
        low = float(market.get("low_24h"))
        high = float(market.get("high_24h"))
    except (TypeError, ValueError):
        return False
    return all(isfinite(value) and value > 0 for value in (sigma, mid, low, high)) and high >= low


def _grid_destination(symbol, capital, market, profile, levels, *, allow_smart):
    filters = market.get("filters")
    min_notional = float(getattr(filters, "min_notional", 5.0) or 5.0)
    minimum_per_level = max(min_notional * 1.1, 6.0)
    sigma = float(market["sigma_24h"])
    low = float(market["low_24h"]) * (1.0 - .25 * sigma)
    high = float(market["high_24h"]) * (1.0 + .25 * sigma)
    mid = float(market["mid"])
    if low <= 0 or high <= low:
        return None
    n_levels = next((n for n in GRID_LEVEL_COUNTS if n <= levels and
        capital / n >= minimum_per_level), None)
    if n_levels is None:
        return None
    ratio = (high / low) ** (1.0 / n_levels)
    if ratio - 1.0 < .005:
        ratio = 1.005
        half = ratio ** (n_levels / 2)
        low, high = mid / half, mid * half
    step = (high / low) ** (1.0 / n_levels) - 1.0
    if mid - low < low * step:
        low = mid / (1.0 + step)
    if high - mid < mid * step:
        high = mid * (1.0 + step)
    if filters is not None:
        try:
            from grid.levels import compute_lines
            lines = compute_lines(str(low), str(high), n_levels, filters)
            step_pct = min(float((right - left) / left) for left, right in zip(lines, lines[1:]))
        except Exception:
            return None
    else:
        step_pct = (high / low) ** (1.0 / n_levels) - 1.0
    if step_pct < .005:
        return None
    kind = "smart" if allow_smart and bool(profile.get("smart_grid_enabled", True)) and (
        float(market["high_24h"]) / float(market["low_24h"]) - 1.0 >= .08) else "grid"
    return {"symbol": symbol, "type": kind, "capital": float(capital),
        "geometry": {"low": low, "high": high}, "levels": n_levels,
        "score": min(10.0, float(market["sigma_24h"]) / step_pct)}


def _bot_ladder(symbol, capital, market, profile, available_slots):
    filters = market.get("filters")
    minimum = max(10.0, float(getattr(filters, "min_notional", 5.0) or 5.0) * 1.1)
    count = min(4, max(0, int(available_slots)), int(float(capital) // minimum))
    if count < 2:
        return []
    unit = float(capital) / count
    mid, sigma = float(market["mid"]), float(market["sigma_24h"])
    min_margin = float(profile.get("min_margin_pct", .3)) / 100.0
    sell_move = max(sigma, min_margin + .002)
    bots = []
    for multiple in BOT_SIGMA_OFFSETS[:count]:
        buy = mid * exp(-multiple * sigma)
        sell = buy * (1.0 + sell_move)
        if filters is not None:
            from decimal import Decimal
            qty = filters.round_qty_down(Decimal(str(unit)) / Decimal(str(buy * 1.001)))
            net_qty = filters.round_qty_down(qty * Decimal("0.999"))
            if qty < filters.min_qty or qty * Decimal(str(buy)) < filters.min_notional:
                continue
            if net_qty < filters.min_qty or net_qty * Decimal(str(sell)) < filters.min_notional:
                continue
        bots.append({"symbol": symbol, "type": "bot", "capital": unit,
            "geometry": {"buy_price": buy, "sell_price": sell,
                "buy_sigma_offset": -multiple}, "levels": 1,
            "score": min(1.0, 2.0 * _normal_survival(multiple))})
    return bots


def _destination_portfolio(markets, budget, source_sigma, profile, limits, *,
        source_symbol=None, source_piece_type=None, require_sigma_ratio=1.2):
    candidates = [(symbol, market) for symbol, market in markets.items()
        if _valid_market(market) and (symbol == str(source_symbol or "").upper() or
            float(market["sigma_24h"]) >= require_sigma_ratio * float(source_sigma))]
    candidates.sort(key=lambda item: (-float(item[1]["sigma_24h"]), item[0]))
    candidates = candidates[:3]
    if not candidates or budget <= 0:
        return []
    while candidates:
        total_sigma = sum(float(market["sigma_24h"]) for _, market in candidates)
        grids_left = max(0, int(limits.get("max_grids", 0)))
        bots_left = max(0, int(limits.get("max_bots", 0)))
        by_symbol = {str(key).upper(): float(value) for key, value in
            (limits.get("capital_by_symbol") or {}).items()}
        coin_cap = float(limits.get("capital_per_coin_usdt", float("inf")))
        output, failed_symbols = [], []
        for index, (symbol, market) in enumerate(candidates):
            share = budget * float(market["sigma_24h"]) / total_sigma
            amount = min(share, max(0.0, coin_cap - by_symbol.get(symbol, 0.0)))
            if amount < 10.0:
                failed_symbols.append(symbol)
                continue
            # El primer destino es un grid si alcanza; los posteriores usan bots.
            grid = (_grid_destination(symbol, amount, market, profile, grids_left,
                allow_smart=True) if index == 0 and grids_left > 0 else None)
            if symbol == str(source_symbol or "").upper() and source_piece_type == "grid":
                grid = None
            if grid is not None:
                output.append(grid)
                grids_left -= 1
                by_symbol[symbol] = by_symbol.get(symbol, 0.0) + amount
                continue
            same_symbol_same_type = (symbol == str(source_symbol or "").upper()
                and source_piece_type == "individual_bot_grid")
            ladder = ([] if same_symbol_same_type else
                _bot_ladder(symbol, amount, market, profile, bots_left))
            if ladder:
                output.extend(ladder)
                bots_left -= len(ladder)
                by_symbol[symbol] = by_symbol.get(symbol, 0.0) + sum(row["capital"] for row in ladder)
            else:
                failed_symbols.append(symbol)
        if not failed_symbols:
            return output
        # Quita una sola moneda fallida por iteraci??n para redistribuir su parte
        # antes de descartar tambi??n una segunda/tercera moneda que s?? cabe.
        failed = next((row[0] for row in reversed(candidates)
            if row[0] in set(failed_symbols)), None)
        if failed is None:
            return output
        candidates = [row for row in candidates if row[0] != failed]
    return []


def decide_cazador(context):
    """Evalúa rotación/agregado sin escribir en BD ni llamar al exchange."""
    if context.get("cash_coherent") is False:
        return {"accion": "ninguna", "pieza": None, "destino": [],
            "motivo": "efectivo de la meta incoherente", "puntajes_piezas": []}
    markets = {str(symbol).upper(): dict(value) for symbol, value in
        (context.get("markets") or {}).items() if _valid_market(value)}
    pieces = [dict(row) for row in context.get("pieces", [])]
    now = float(context.get("now_s") or 0)
    profile = dict(context.get("profile") or {})
    limits = dict(context.get("limits") or {})
    scores = []
    sigma_all = sorted(float(row["sigma_24h"]) for row in markets.values())
    median_sigma = sigma_all[len(sigma_all) // 2] if sigma_all else 0.0
    current_loss = max(0.0, -float(context.get("result_pct") or 0) *
        float(context.get("capital_initial") or 0) / 100.0)
    loss_limit = float(context.get("capital_initial") or 0) * float(
        profile.get("max_loss_pct", 18.0)) / 100.0
    for piece in pieces:
        symbol = str(piece.get("symbol") or "").upper()
        market = markets.get(symbol)
        score = piece_activity(piece, market or {})
        reasons = []
        warnings = []
        if piece.get("last_cycle_at_s") is not None and now - float(piece["last_cycle_at_s"]) < 12 * 3600:
            reasons.append("ciclo completo en las últimas 12 h")
        if piece.get("opened_by_cazador_at_s") is not None and now - float(
                piece["opened_by_cazador_at_s"]) < 6 * 3600:
            reasons.append("pieza abierta por el Cazador hace menos de 6 h")
        if market and piece.get("sell_price"):
            distance = abs(log(float(piece["sell_price"]) / float(market["mid"])))
            if distance <= .5 * float(market["sigma_24h"]):
                reasons.append("venta pendiente a punto de ejecutarse")
        if market is None:
            reasons.append("sin libro o volatilidad válida para la pieza")
        capital = float(piece.get("liquidation_value") if piece.get("liquidation_value") is not None
            else piece.get("capital") or 0)
        budget = capital + (float(context.get("free_cash") or 0)
            if float(context.get("free_cash") or 0) >= 10 else 0.0)
        piece_limits = dict(limits)
        piece_limits["capital_by_symbol"] = dict(limits.get("capital_by_symbol") or {})
        source_allocated = float(piece.get("capital_nominal")
            if piece.get("capital_nominal") is not None else capital)
        piece_limits["capital_by_symbol"][symbol] = max(0.0,
            float(piece_limits["capital_by_symbol"].get(symbol, 0.0)) - source_allocated)
        if piece.get("piece_type") == "individual_bot_grid":
            piece_limits["max_bots"] = int(piece_limits.get("max_bots", 0)) + 1
        else:
            piece_limits["max_grids"] = int(piece_limits.get("max_grids", 0)) + 1
        destination = (_destination_portfolio(markets, budget,
            float(market["sigma_24h"]), profile, piece_limits,
            source_symbol=symbol, source_piece_type=piece.get("piece_type")) if market is not None else [])
        destination_score = sum(float(item["score"]) for item in destination)
        gain = destination_score - score
        if not destination:
            reasons.append("ninguna moneda cumple el umbral de volatilidad y capital")
        if not (market and any(float(row["sigma_24h"]) >= 1.2 *
                float(market["sigma_24h"]) for row in markets.values() if row is not market)):
            if destination and any(row["symbol"] != symbol for row in destination):
                reasons.append("σ de destino no supera 1,2× la pieza")
        if gain < .5 * score:
            reasons.append("la actividad destino no supera 1,5× la actividad actual")
        def loss_penalty(rows):
            return sum(.5 * float(item["capital"]) * max(0.0,
                (float(markets[item["symbol"]]["mid"]) - float(item["geometry"].get("low",
                    item["geometry"].get("buy_price", markets[item["symbol"]]["mid"])))) /
                    float(markets[item["symbol"]]["mid"])) for item in rows)
        penalty = loss_penalty(destination)
        initial_budget = budget
        for _attempt in range(12):
            if current_loss + penalty <= loss_limit or not destination:
                break
            allowed = max(0.0, loss_limit - current_loss)
            ratio = allowed / penalty if penalty > 0 else 0.0
            budget *= min(.9, max(.5, ratio * .98))
            destination = (_destination_portfolio(markets, budget,
                float(market["sigma_24h"]), profile, piece_limits,
                source_symbol=symbol, source_piece_type=piece.get("piece_type")) if market is not None else [])
            destination_score = sum(float(item["score"]) for item in destination)
            gain = destination_score - score
            penalty = loss_penalty(destination)
        if initial_budget > budget + 1e-8 and current_loss + penalty <= loss_limit:
            warnings.append("capital de destino reducido para respetar el presupuesto de pérdida")
        if current_loss + penalty > loss_limit:
            reasons.append("presupuesto de pérdida insuficiente")
        if not destination:
            reasons.append("sin cartera destino factible tras aplicar limites")
            if current_loss >= loss_limit - 1e-8 and not any("presupuesto" in item for item in reasons):
                reasons.append("presupuesto de perdida insuficiente")
        scores.append({"grid_id": piece.get("grid_id"), "symbol": symbol,
            "capital": capital, "puntaje_pieza": score, "puntaje_destino": destination_score,
            "ganancia_actividad": gain, "presupuesto": budget, "destino": destination,
            "motivos_freno": reasons, "avisos": warnings,
            "perdida_estimada_usdt": penalty})
    eligible = [row for row in scores if not row["motivos_freno"]]
    if eligible:
        chosen = max(eligible, key=lambda row: (row["ganancia_actividad"], row["capital"]))
        return {"accion": "rotar", "pieza": chosen["grid_id"],
            "puntaje_pieza": chosen["puntaje_pieza"],
            "puntaje_destino": chosen["puntaje_destino"], "motivo": "mayor ganancia de actividad",
            "por_que_elegida": [{"grid_id": row.get("grid_id"),
                "ganancia_actividad": row.get("ganancia_actividad"),
                "seleccionada": row is chosen} for row in scores],
            "destino": chosen["destino"], "puntajes_piezas": scores,
            "sigma_por_moneda": {key: value["sigma_24h"] for key, value in markets.items()},
            "rango_24h": {key: {"low": value["low_24h"], "high": value["high_24h"]}
                for key, value in markets.items()}, "capital_total": sum(
                    row["capital"] for row in chosen["destino"]),
            "perdida_realizada_estimada": chosen["perdida_estimada_usdt"],
            "presupuesto_perdida": {"limite_usdt": loss_limit,
                "perdida_actual_usdt": current_loss,
                "perdida_estimada_usdt": chosen["perdida_estimada_usdt"]}}
    cash = float(context.get("free_cash") or 0)
    if cash >= 10 and median_sigma > 0:
        destination = _destination_portfolio(markets, cash, median_sigma, profile, limits,
            require_sigma_ratio=1.2)
        if destination:
            return {"accion": "agregar", "pieza": None, "puntaje_pieza": 0.0,
                "puntaje_destino": sum(row["score"] for row in destination),
                "motivo": "efectivo libre y monedas con movimiento suficiente",
                "destino": destination, "puntajes_piezas": scores,
                "sigma_por_moneda": {key: value["sigma_24h"] for key, value in markets.items()},
                "rango_24h": {key: {"low": value["low_24h"], "high": value["high_24h"]}
                    for key, value in markets.items()}, "capital_total": sum(
                        row["capital"] for row in destination),
                "perdida_realizada_estimada": 0.0,
                "presupuesto_perdida": {"limite_usdt": loss_limit,
                    "perdida_actual_usdt": current_loss, "perdida_estimada_usdt": 0.0}}
    return {"accion": "ninguna", "pieza": None, "puntaje_pieza": 0.0,
        "puntaje_destino": 0.0, "motivo": (scores[0]["motivos_freno"][0] if scores and
            scores[0]["motivos_freno"] else "no hay cartera destino factible"),
        "destino": [], "puntajes_piezas": scores,
        "sigma_por_moneda": {key: value["sigma_24h"] for key, value in markets.items()},
        "rango_24h": {key: {"low": value["low_24h"], "high": value["high_24h"]}
            for key, value in markets.items()}, "capital_total": 0.0,
        "perdida_realizada_estimada": 0.0,
        "presupuesto_perdida": {"limite_usdt": loss_limit,
            "perdida_actual_usdt": current_loss, "perdida_estimada_usdt": 0.0}}


def build_runtime_context(operator, goal, valuation):
    """Carga el contexto del Cazador desde velas locales y lecturas públicas actuales."""
    import numpy as np
    import json
    import sqlite3
    from data.market_store import market_db_path
    from data.exchange_filters import SymbolFilters
    from grid.meta_live_pieces import live_meta_grid_links
    from grid.meta import DEFAULT_PROFILES

    path = market_db_path()
    markets = {}
    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as conn:
        symbols = conn.execute("SELECT id,symbol FROM symbols WHERE role='operar' AND active=1 ORDER BY symbol").fetchall()
        for symbol_id, symbol in symbols:
            rows = conn.execute("SELECT high,low,close FROM candles WHERE symbol_id=? AND interval='5m' ORDER BY open_ts DESC LIMIT 289",
                (symbol_id,)).fetchall()
            if len(rows) < 289:
                continue
            rows.reverse()
            close = np.asarray([row[2] for row in rows], dtype=float)
            returns = np.diff(np.log(close))
            sigma = float(np.std(returns, ddof=1) * np.sqrt(288)) if len(returns) > 1 else 0.0
            filter_row = conn.execute("SELECT raw_json FROM symbol_filters WHERE symbol=?",
                (str(symbol).upper(),)).fetchone()
            filters = SymbolFilters.from_symbol_info(json.loads(filter_row[0])) if filter_row else None
            try:
                book = operator._book_ticker(symbol)
                bid = float(book.get("bid_price") or book.get("bidPrice") or 0)
                ask = float(book.get("ask_price") or book.get("askPrice") or 0)
            except Exception:
                continue
            if bid <= 0 or ask < bid:
                continue
            markets[str(symbol).upper()] = {"mid": (bid + ask) / 2, "bid": bid,
                "sigma_24h": sigma, "low_24h": float(min(row[1] for row in rows[-288:])),
                "high_24h": float(max(row[0] for row in rows[-288:])), "filters": filters}
    pieces = []
    for link in live_meta_grid_links(operator.core, int(goal["id"])):
        grid_id = int(link["grid_id"])
        grid = operator.db.get_grid(grid_id)
        if not grid:
            continue
        levels = operator.db.get_grid_levels(grid_id)
        symbol = str(grid.get("symbol") or "").upper()
        market = markets.get(symbol, {})
        pending = []
        held = 0.0
        buy_open_capital = 0.0
        for level in levels:
            state = str(level.get("state") or "").upper()
            held += float(level.get("held_qty") or 0)
            if state == "BUY_OPEN":
                pending.append(float(level.get("price") or 0))
                # El capital reservado ya representa el importe comprometido por la orden.
                buy_open_capital += float(level.get("capital") or 0)
            elif state == "SELL_OPEN": pending.append(float(level.get("sell_price") or 0))
        capital = float(grid.get("capital_total") or 0)
        pieces.append({"grid_id": grid_id, "symbol": symbol,
            "piece_type": link.get("piece_type"), "strategy": grid.get("strategy"),
            "low": grid.get("range_low"), "high": grid.get("range_high"),
            "n_levels": grid.get("n_levels"), "pending_prices": pending,
            "sell_price": next((float(row.get("sell_price") or 0) for row in levels
                if str(row.get("state") or "").upper() == "SELL_OPEN"), None),
            "liquidation_value": (held * float(market.get("bid") or 0) * .999 + buy_open_capital),
            "buy_open_capital": buy_open_capital, "capital_nominal": capital,
            "capital": capital, "status": grid.get("status")})
    free_cash = float(valuation.get("free_capital_usdt") or 0.0)
    try:
        account_attributable, _reserve = operator.core._available_usdt(int(goal["id"]))
        free_cash = min(free_cash, float(account_attributable))
    except Exception:
        free_cash = 0.0
    cfg = {**DEFAULT_PROFILES.get(str(goal.get("profile") or "").upper(), {}),
        **(goal.get("config_snapshot") or {})}
    by_symbol = {}
    grids = bots = 0
    for link in live_meta_grid_links(operator.core, int(goal["id"])):
        row = operator.db.get_grid(int(link["grid_id"]))
        if not row: continue
        symbol = str(row.get("symbol") or "").upper()
        by_symbol[symbol] = by_symbol.get(symbol, 0.0) + float(row.get("capital_total") or 0)
        if link.get("piece_type") == "individual_bot_grid": bots += 1
        else: grids += 1
    initial = float(goal.get("capital_initial") or 0)
    return {"markets": markets, "pieces": pieces, "free_cash": free_cash,
        "cash_coherent": valuation.get("cash_coherent") is not False,
        "result_pct": valuation.get("result_pct"), "capital_initial": initial,
        "profile": cfg, "limits": {"max_grids": max(0, int(cfg.get("max_grids", 0))-grids),
            "max_bots": max(0, int(cfg.get("max_bots", 0))-bots),
            "capital_per_coin_usdt": initial * float(cfg.get("capital_per_coin_pct", 100))/100,
            "capital_by_symbol": by_symbol}, "now_s": __import__("time").time()}
