#!/usr/bin/env python3
"""Completa resultados de decisiones sombra con velas reales ya cerradas."""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from data.market_store import CandleStore, market_db_path
from grid.meta import DEFAULT_PROFILES

BAR_SECONDS = 300


def _datetime(value):
    stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return stamp.replace(tzinfo=timezone.utc) if stamp.tzinfo is None else stamp.astimezone(timezone.utc)


def _json(value):
    try:
        return json.loads(value or "{}")
    except (TypeError, ValueError):
        return {}


def _shadow_result(value):
    payload = _json(value)
    return payload.get("result") or payload


def _window_end(row, horizon_days):
    review_at = _datetime(row["review_at"])
    closed_at = _datetime(row["finished_at"]) if row["finished_at"] else None
    horizon_end = review_at + timedelta(days=int(horizon_days))
    return min(closed_at, horizon_end) if closed_at else horizon_end


def _piece_value(piece, *keys, default=None):
    for key in keys:
        if piece.get(key) is not None:
            return piece[key]
    return default


def _simulate_alternative(result, goal, store, start_ts, end_ts):
    from grid.sim.data import ewma_sigma_24h
    from grid.sim.meta_portfolio import evaluate_portfolio, simulate_piece_curve

    pieces = list(result.get("pieces") or [])
    if not pieces:
        return None
    curves = []
    for piece in pieces:
        symbol = str(piece.get("symbol") or "").upper()
        piece_type = str(piece.get("piece_type") or "").lower()
        strategy = ("bot" if piece_type == "individual_bot_grid" else
            str(piece.get("strategy") or "simple").lower())
        capital = float(_piece_value(piece, "capital_usdt", "capital", default=0) or 0)
        low = float(_piece_value(piece, "floor", "range_low", "buy_price", "low", default=0) or 0)
        high = float(_piece_value(piece, "ceiling", "range_high", "sell_price", "high", default=0) or 0)
        levels = int(_piece_value(piece, "n_levels", "n", default=6) or 6)
        if not symbol or capital <= 0 or low <= 0 or high <= low:
            raise ValueError("la pieza sombra no contiene símbolo, capital o geometría válida")
        candles = store.load(symbol, "5m", start_ts, end_ts)
        if len(candles.timestamp) < 2 or candles.gaps:
            raise ValueError(f"velas 5m incompletas para {symbol}")
        filters = store.filters(symbol)
        if filters is None:
            raise ValueError(f"filtros ausentes para {symbol}")
        sim_strategy = "individual_bot" if strategy == "bot" else strategy
        sigma = ewma_sigma_24h(candles.close) if strategy == "smart" else None
        replay = simulate_piece_curve(candles, strategy=sim_strategy, n=levels,
            capital=capital, low=low, high=high, filters=filters,
            sigma_values=sigma, fee_pct=.1)
        curves.append(replay["curve"])
    target = float(goal["target_usdt"] or 0)
    config = _json(goal["config_snapshot"])
    profile = DEFAULT_PROFILES.get(str(goal["profile"]).upper(), {})
    loss_pct = float(config.get("max_loss_pct", profile.get("max_loss_pct", 0)))
    outcome = evaluate_portfolio(curves, capital_initial=float(goal["capital_initial"]),
        target_usdt=target, max_loss_pct=loss_pct)
    return {"source": "velas_reales", "simulator": "Smart exacto" if any(
        str(piece.get("strategy", "")).lower() == "smart" for piece in pieces) else "FastSim",
        "pnl_usdt": outcome["pnl_usdt"], "reached": outcome["reached"],
        "loss_cut": outcome["loss_cut"], "days": outcome.get("days") or
            (end_ts - start_ts) / 86400,
        "max_advance_usdt": outcome.get("max_advance_usdt"), "n_pieces": len(pieces)}


def _trade_flow(event_type, details, price):
    quote = details.get("cummulative_quote_qty", details.get("quote_qty",
        details.get("executed_quote_qty")))
    qty = details.get("held_qty", details.get("executed_qty", details.get("qty")))
    if quote is None and qty is not None and price is not None:
        quote = float(qty) * float(price)
    if event_type == "CELL_LIQUIDATED" and details.get("cash_proceeds_net") is not None:
        return float(details["cash_proceeds_net"]), 0.0
    if quote is None:
        return None, 0.0
    fee = float(details.get("fee_usdt", details.get("commission_usdt", 0)) or 0)
    if event_type == "BUY_FILLED":
        return -float(quote) - fee, float(details.get("held_qty", qty or 0) or 0)
    if event_type in {"SELL_FILLED", "CELL_LIQUIDATED"}:
        return float(quote) - fee, -float(details.get("executed_qty", qty or 0) or 0)
    return 0.0, 0.0


def _actual_meta_outcome(app_db, goal, store, start_ts, end_ts):
    """Reconstruye equity por eventos de fills y cierres de velas del período."""
    with sqlite3.connect(Path(app_db).resolve().as_uri() + "?mode=ro", uri=True) as conn:
        conn.row_factory = sqlite3.Row
        links = conn.execute("SELECT l.grid_id,l.created_at,l.released_at,g.symbol FROM meta_piece_links l "
            "JOIN grids g ON g.id=l.grid_id WHERE l.meta_id=? AND julianday(l.created_at)<=julianday(?) "
            "AND (l.released_at IS NULL OR julianday(l.released_at)>julianday(?))",
            (int(goal["id"]), datetime.fromtimestamp(end_ts, timezone.utc).isoformat(),
             datetime.fromtimestamp(start_ts, timezone.utc).isoformat())).fetchall()
        if not links:
            return {"source": "meta_real", "pnl_usdt": 0.0, "reached": False,
                "loss_cut": False, "days": (end_ts - start_ts) / 86400}
        grid_symbols = {int(row["grid_id"]): str(row["symbol"]).upper() for row in links}
        link_intervals = {int(row["grid_id"]): (
            int(_datetime(row["created_at"]).timestamp()),
            int(_datetime(row["released_at"]).timestamp()) if row["released_at"] else None)
            for row in links}
        grid_ids = list(grid_symbols)
        marks = ",".join("?" for _ in grid_ids)
        events = conn.execute(f"SELECT ts,grid_id,event_type,price,details FROM grid_events "
            f"WHERE grid_id IN ({marks}) AND event_type IN ('BUY_FILLED','SELL_FILLED','CELL_LIQUIDATED') "
            "ORDER BY ts,id", grid_ids).fetchall()

    symbols = sorted(set(grid_symbols.values()))
    candles = {symbol: store.load(symbol, "5m", start_ts, end_ts) for symbol in symbols}
    if any(len(data.timestamp) < 2 or data.gaps for data in candles.values()):
        raise ValueError("velas 5m incompletas para medir el resultado real de la meta")
    common = set(int(ts) for ts in next(iter(candles.values())).timestamp)
    for data in list(candles.values())[1:]:
        common.intersection_update(int(ts) for ts in data.timestamp)
    stamps = sorted(ts for ts in common if start_ts <= ts <= end_ts)
    if len(stamps) < 2:
        raise ValueError("no hay velas comunes para la ventana real de la meta")
    indexed = {symbol: {int(ts): float(close) for ts, close in zip(data.timestamp, data.close)}
        for symbol, data in candles.items()}
    cash = float(goal["capital_initial"])
    held_by_grid = {grid_id: 0.0 for grid_id in grid_ids}
    cash_flows = []
    for event in events:
        event_ts = int(_datetime(event["ts"]).timestamp())
        link_start, link_end = link_intervals[int(event["grid_id"])]
        if event_ts < link_start or (link_end is not None and event_ts > link_end) or event_ts > end_ts:
            continue
        details = _json(event["details"])
        flow, qty_delta = _trade_flow(str(event["event_type"]), details, event["price"])
        if flow is None:
            raise ValueError("evento real sin valor cotizado suficiente para reconstruir la meta")
        cash_flows.append((event_ts, flow, int(event["grid_id"]), qty_delta))
    pointer = 0
    while pointer < len(cash_flows) and cash_flows[pointer][0] < stamps[0]:
        _event_ts, flow, grid_id, qty_delta = cash_flows[pointer]
        cash += flow
        held_by_grid[grid_id] += qty_delta
        pointer += 1
    start_equity = cash + sum(max(0.0, quantity) * indexed[grid_symbols[grid_id]][stamps[0]] * .999
        for grid_id, quantity in held_by_grid.items())
    config = _json(goal["config_snapshot"])
    profile = DEFAULT_PROFILES.get(str(goal["profile"]).upper(), {})
    loss_pct = float(config.get("max_loss_pct", profile.get("max_loss_pct", 0)))
    target_line = float(goal["capital_initial"]) + float(goal["target_usdt"])
    loss_line = float(goal["capital_initial"]) * (1 - loss_pct / 100)
    reached_at, loss_at, equity = None, None, start_equity
    for stamp in stamps:
        while pointer < len(cash_flows) and cash_flows[pointer][0] <= stamp:
            _event_ts, flow, grid_id, qty_delta = cash_flows[pointer]
            if _event_ts >= stamps[0]:
                cash += flow
                held_by_grid[grid_id] += qty_delta
            pointer += 1
        inventory = sum(max(0.0, quantity) * indexed[grid_symbols[grid_id]][stamp] * .999
            for grid_id, quantity in held_by_grid.items())
        equity = cash + inventory
        if reached_at is None and equity >= target_line:
            reached_at = stamp
        if loss_at is None and equity <= loss_line:
            loss_at = stamp
    loss_cut = loss_at is not None and (reached_at is None or loss_at <= reached_at)
    return {"source": "meta_real", "pnl_usdt": equity - start_equity,
        "reached": reached_at is not None and not loss_cut, "loss_cut": loss_cut,
        "days": ((reached_at - stamps[0]) / 86400 if reached_at is not None and not loss_cut
            else (stamps[-1] - stamps[0]) / 86400), "as_of_ts": stamps[-1]}


def _calculate_outcome(row, store, start_ts, end_ts):
    shadow = _shadow_result(row["shadow_decision_json"])
    decision = str(shadow.get("decision") or "").lower()
    if decision in {"mantener", "no_hacer_nada"}:
        return _actual_meta_outcome(row["app_db"], dict(row), store, start_ts, end_ts)
    if decision in {"mantener_usdt", "usdt_quieto"} or (
            decision == "seleccionar_alternativa" and not shadow.get("pieces")):
        return {"source": "usdt_quieto", "simulator": None, "pnl_usdt": 0.0,
            "reached": False, "loss_cut": False, "days": (end_ts - start_ts) / 86400}
    return _simulate_alternative(shadow, dict(row), store, start_ts, end_ts)


def mark_mature_decisions(app_db, market_db, *, now=None):
    now = now or datetime.now(timezone.utc)
    market_path = market_db_path(market_db)
    store = CandleStore(market_path)
    app_path = Path(app_db).resolve()
    with sqlite3.connect(app_path.as_uri() + "?mode=ro", uri=True) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT d.id,d.goal_id,d.review_at,d.shadow_decision_json,g.profile,"
            "g.capital_initial,g.target_usdt,g.config_snapshot,g.finished_at,g.status "
            "FROM engine_shadow_decisions d JOIN meta_goals g ON g.id=d.goal_id "
            "WHERE d.shadow_outcome_json IS NULL ORDER BY d.id").fetchall()
    marked, pending, errors = [], [], []
    for source in rows:
        row = dict(source)
        config = _json(row.get("config_snapshot"))
        profile = DEFAULT_PROFILES.get(str(row.get("profile") or "").upper(), {})
        shadow = _shadow_result(row["shadow_decision_json"])
        horizon = int(config.get("max_days", shadow.get("deadline_days") or
            profile.get("max_days", 0)))
        if horizon <= 0:
            errors.append({"id": row["id"], "reason": "horizonte de meta ausente"})
            continue
        due = _window_end(row, horizon)
        if due > now:
            pending.append(int(row["id"]))
            continue
        start_ts = int(_datetime(row["review_at"]).timestamp())
        end_ts = int(due.timestamp()) // BAR_SECONDS * BAR_SECONDS
        if end_ts <= start_ts:
            errors.append({"id": row["id"], "reason": "ventana sin duración"})
            continue
        try:
            outcome = _calculate_outcome({**row, "app_db": str(app_path)}, store,
                start_ts, end_ts)
            if outcome is None:
                errors.append({"id": row["id"], "reason": "la decisión sombra no seleccionó alternativa"})
                continue
            outcome.update({"window_start": row["review_at"],
                "window_end": due.isoformat(), "horizon_days": horizon})
            payload = json.dumps(outcome, ensure_ascii=False, sort_keys=True, default=str)
            with sqlite3.connect(app_path, timeout=10) as conn:
                cursor = conn.execute("UPDATE engine_shadow_decisions SET shadow_outcome_json=? "
                    "WHERE id=? AND shadow_outcome_json IS NULL", (payload, int(row["id"])))
            if cursor.rowcount:
                marked.append(int(row["id"]))
        except (OSError, ValueError, ArithmeticError, sqlite3.Error) as exc:
            errors.append({"id": row["id"], "reason": f"{type(exc).__name__}: {exc}"})
    return {"marked": marked, "pending": pending, "errors": errors}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app-db", required=True)
    parser.add_argument("--market-db", required=True)
    args = parser.parse_args(argv)
    result = mark_mature_decisions(args.app_db, args.market_db)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
