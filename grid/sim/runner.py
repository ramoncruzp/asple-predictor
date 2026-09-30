"""Historical candle loop around production grid planning and policy functions."""
from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone
from decimal import Decimal

import numpy as np

from data.exchange_filters import SymbolFilters
from grid.adjust import plan_adjust
from grid.levels import compute_lines, plan_cells
from grid.policy import adjust_decision, evaluate_grid, stoploss_candidates, validate_params
from grid.sim.data import ewma_sigma_24h
from grid.sim.exchange import SimExchange
from grid.sim.metrics import calculate_metrics


FILTERS = SymbolFilters(Decimal("0.0001"), Decimal("0.0001"), Decimal("100000"),
                       Decimal("0.1"), Decimal("0.1"), Decimal("100000000"),
                       Decimal("5"), True, 200)
ADJUST_BLOCKED_THROTTLE_SECONDS = 6 * 60 * 60


def _should_emit_adjust_blocked(timestamp, blocked_reason, recent_events):
    """Mirror GridMonitor's same-reason/six-hour/last-100 event throttle."""
    for event in list(recent_events)[-100:]:
        details = event.get("details") or {}
        previous_reason = details.get("blocked_reason", event.get("blocked_reason"))
        if previous_reason != blocked_reason:
            continue
        previous_ts = int(event.get("ts", 0))
        if int(timestamp) - previous_ts < ADJUST_BLOCKED_THROTTLE_SECONDS:
            return False
    return True


def _json_safe(value):
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_safe(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def run_simulation(candles, *, strategy="simple", n=10, capital=100, low=None, high=None,
                   width_pct=None, fee_pct=.1, resync_candles=3, params=None,
                   halflife_h=72, sigma_scale=1.0, csv_hash=None, filters=None,
                   fee_asset=None, include_details=False):
    if strategy not in {"simple", "smart"}:
        raise ValueError("strategy must be simple or smart")
    if n < 4 or capital <= 0 or resync_candles < 1 or fee_pct < 0:
        raise ValueError("n >= 4, capital > 0, resync-candles >= 1 and fee-pct >= 0 required")
    if params and any(params.get(key) is True for key in ("compound_enabled", "loans_enabled")):
        raise ValueError("compound/loans are available in 15B-4b")
    effective = validate_params(params, n) if strategy == "smart" else {}
    closes = candles.close
    initial = float(closes[0])
    if width_pct is not None:
        half = float(width_pct) / 200.0
        low = initial * np.exp(-half)
        high = initial * np.exp(half)
    if low is None or high is None or float(low) <= 0 or float(high) <= float(low):
        raise ValueError("provide valid low/high or width_pct")
    filters = FILTERS if filters is None else filters
    fee_asset = None if fee_asset is None else str(fee_asset).upper()
    if fee_asset not in {None, "XRP", "USDT"}:
        raise ValueError("fee_asset must be XRP, USDT or None")
    lines = compute_lines(str(low), str(high), n, filters)
    cells_plan = plan_cells(lines, str(capital),
                            {"bid_price": str(initial * (1 - 1e-8)), "ask_price": str(initial * (1 + 1e-8)),
                             "avg_price": str(initial)}, filters,
                            {"grid_min_step_pct": .003, "capital_max_por_nivel_pct": .30})
    cells = [{"level_idx": p.level_idx, "price": p.buy_price, "sell_price": p.sell_price,
              "capital": p.capital, "qty": p.qty, "state": "IDLE", "entry_price": None,
              "entry_cost": Decimal(0), "held_qty": Decimal(0), "bought_at": None, "cycles_completed": 0,
              "pnl": Decimal(0), "stop_loss_pct": Decimal(str(effective.get("stop_loss_pct", 5))) if strategy == "smart" else None}
             for p in cells_plan]
    for row, plan in zip(cells, cells_plan):
        row["state"] = plan.initial_state
    exchange = SimExchange(capital, fee_pct, filters=filters, fee_asset=fee_asset)
    sigma = ewma_sigma_24h(closes, halflife_h) * float(sigma_scale)
    equity, events, trapped_values = [], [], []
    recent_adjust_blocked = []
    adjust_attempts_blocked = 0
    adjust_rejected_events = 0
    status, paused_since, pause_reasons = "ACTIVE", None, None
    last_adjust_at = None
    active_low, active_high = float(lines[0]), float(lines[-1])
    paused = 0
    for i, (ts, lo, hi, close) in enumerate(zip(candles.timestamp, candles.low, candles.high, closes)):
        fills = exchange.process(i, lo, hi, close, cells)
        for idx, side, price, qty, fee, qty_net in fills:
            events.append({"ts": int(ts), "type": f"{side}_FILLED", "level_idx": idx,
                           "price": str(price), "qty": str(qty), "qty_net": str(qty_net),
                           "fee": str(fee)})
        if i % resync_candles == 0:
            if strategy == "smart" and status in {"ACTIVE", "PAUSED"}:
                policy_cells = [dict(c, held_qty=float(c["held_qty"]),
                                     entry_price=None if c["entry_price"] is None else float(c["entry_price"]),
                                     pnl=float(c["pnl"]),
                                     bought_at=None if c["bought_at"] is None else datetime.fromtimestamp(
                                         int(candles.timestamp[int(c["bought_at"])]) + (int(c["bought_at"]) % 1), timezone.utc),
                                     stop_loss_pct=float(c["stop_loss_pct"]) if c["stop_loss_pct"] is not None else None)
                                for c in cells]
                decision = evaluate_grid(status, effective, policy_cells, float(close), active_low,
                                         active_high, float(capital), float(sigma[i]), paused_since,
                                         datetime.fromtimestamp(int(ts), timezone.utc), pause_reasons)
                action = decision.action
                now = datetime.fromtimestamp(int(ts), timezone.utc)
                if action != "CLOSE_REPOSITORY":
                    adjust_decision_result = adjust_decision(
                        {"strategy": "smart", "status": status, "params": effective, "n_levels": n,
                         "range_low": active_low, "range_high": active_high, "capital_total": capital},
                        policy_cells, float(close), float(sigma[i]), now, last_adjust_at)
                    if adjust_decision_result.action == "ADJUST":
                        action = "ADJUST"
                        decision = adjust_decision_result
                    elif adjust_decision_result.action == "BLOCKED":
                        adjust_attempts_blocked += 1
                        blocked_reason = ",".join(adjust_decision_result.reasons) or "plan_invalid"
                        blocked_event = {"ts": int(ts), "type": "ADJUST_REJECTED",
                                         "reason": list(adjust_decision_result.reasons),
                                         "metrics": adjust_decision_result.metrics,
                                         "details": {"blocked_reason": blocked_reason}}
                        if _should_emit_adjust_blocked(int(ts), blocked_reason, recent_adjust_blocked):
                            events.append(blocked_event)
                            recent_adjust_blocked.append(blocked_event)
                            recent_adjust_blocked = recent_adjust_blocked[-100:]
                            adjust_rejected_events += 1
                if action not in {"NONE", "ADJUST"}:
                    events.append({"ts": int(ts), "type": action, "reason": list(decision.reasons),
                                   "metrics": decision.metrics})
                if action == "PAUSE":
                    status, paused_since, pause_reasons = "PAUSED", datetime.fromtimestamp(int(ts), timezone.utc), decision.reasons
                    for c in cells:
                        exchange.cancel(c["level_idx"], "BUY")
                elif action == "RESUME":
                    status, paused_since, pause_reasons = "ACTIVE", None, None
                elif action == "CLOSE_REPOSITORY":
                    status = "HOLDING"
                    for c in cells:
                        exchange.cancel(c["level_idx"])
                # Production stop-loss decision helper is shared; execution occurs at monitor close.
                for candidate in stoploss_candidates(policy_cells, float(close)):
                    c = cells[int(candidate["level_idx"])]
                    exchange.cancel(c["level_idx"])
                    pnl = exchange.market_sell(c, close)
                    events.append({"ts": int(ts), "type": "STOP_LOSS", "level_idx": c["level_idx"],
                                   "reason": "stop_loss_pct", "pnl": str(pnl)})
                if action == "ADJUST":
                    grid = {"n_levels": n, "range_low": active_low, "range_high": active_high,
                            "capital_total": capital, "params": effective}
                    adjust = plan_adjust(grid, cells, decision.metrics["range_low"],
                                         decision.metrics["range_high"], decision.metrics["n_levels"],
                                         close, filters, type("Settings", (), {"grid_min_step_pct": .003,
                                         "capital_max_por_nivel_pct": .30})())
                    events.append({"ts": int(ts), "type": "ADJUST" if adjust.ok else "ADJUST_REJECTED",
                                   "reason": adjust.reason,
                                   "details": {**adjust.details, "decision": decision.metrics}})
                    if adjust.ok:
                        last_adjust_at = now
                        for c in cells:
                            exchange.cancel(c["level_idx"])
                        for idx in adjust.retire_level_idxs:
                            if idx < len(cells):
                                cells[idx]["state"] = "DONE"
                                cells[idx]["capital"] = Decimal(0)
                        for item in adjust.mapping:
                            idx = int(item["level_idx"])
                            while len(cells) <= idx:
                                new_idx = len(cells)
                                cells.append({"level_idx": new_idx, "price": Decimal(0),
                                              "sell_price": Decimal(0), "capital": Decimal(0),
                                              "qty": Decimal(0), "state": "IDLE", "entry_price": None,
                                              "entry_cost": Decimal(0),
                                              "held_qty": Decimal(0), "bought_at": None,
                                              "cycles_completed": 0, "pnl": Decimal(0),
                                              "stop_loss_pct": Decimal(str(effective.get("stop_loss_pct", 5)))})
                            c = cells[idx]
                            c.update(price=Decimal(item["price"]), sell_price=Decimal(item["sell_price"]),
                                     capital=Decimal(item["capital"]), qty=filters.round_qty_down(
                               Decimal(item["capital"]) / Decimal(item["price"])), state="IDLE")
                        active_low, active_high, n = float(adjust.lines[0]), float(adjust.lines[-1]), len(adjust.lines)-1
            if status in {"ACTIVE", "PAUSED"}:
                for c in cells:
                    idx = c["level_idx"]
                    if c["state"] == "SELL_OPEN" and idx not in exchange.orders:
                        exchange.place(idx, "SELL", c["sell_price"], c["held_qty"], i + 1, i)
                    elif (c["state"] in {"IDLE", "BUY_OPEN"} and idx not in exchange.orders
                          and status == "ACTIVE" and c["price"] < Decimal(str(close))):
                        c["state"] = "BUY_OPEN"
                        exchange.place(idx, "BUY", c["price"], c["qty"], i + 1, i)
        if status == "PAUSED":
            paused += 1
        market_equity = exchange.usdt + exchange.base * Decimal(str(close))
        equity.append(float(market_equity))
        trapped_values.append(float(sum((c["held_qty"] * c["entry_price"]
                                         for c in cells if c["entry_price"] is not None), Decimal(0))))
    # Open inventory after CLOSE is retained and valued at the last close.
    metric = calculate_metrics(equity, float(capital), cells, exchange, closes,
                               paused=paused, trapped_values=trapped_values)
    metric["adjust_attempts_blocked"] = adjust_attempts_blocked
    metric["adjust_rejected_events"] = adjust_rejected_events
    intervention_types = {"PAUSE", "RESUME", "ADJUST", "ADJUST_REJECTED", "CLOSE_REPOSITORY", "STOP_LOSS"}
    metric["interventions_by_type"] = {
        kind: sum(event.get("type") == kind for event in events)
        for kind in sorted({event.get("type") for event in events} & intervention_types)
    }
    hashed_params = {"strategy": strategy, "n": n, "capital": capital,
                     "range_low": str(lines[0]), "range_high": str(lines[-1]),
                     "width_pct": width_pct, "fee_pct": fee_pct,
                     "fee_asset": fee_asset,
                     "filters": {
                         "tick_size": str(filters.tick_size), "min_price": str(filters.min_price),
                         "max_price": str(filters.max_price), "step_size": str(filters.step_size),
                         "min_qty": str(filters.min_qty), "max_qty": str(filters.max_qty),
                         "min_notional": str(filters.min_notional),
                         "apply_min_to_market": bool(filters.apply_min_to_market),
                         "max_num_orders": filters.max_num_orders,
                         "band": None if filters.band is None else {
                             key: str(getattr(filters.band, key))
                             for key in ("bid_up", "bid_down", "ask_up", "ask_down")
                         },
                     },
                     "resync_candles": resync_candles, "halflife_h": halflife_h,
                     "sigma_scale": sigma_scale, "params": effective}
    params_digest = hashlib.sha256(json.dumps(hashed_params,
                                               sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    result = {"strategy": strategy, "start": int(candles.timestamp[0]), "end": int(candles.timestamp[-1]),
            "candles": len(candles.timestamp), "gaps": candles.gaps,
            "low": str(lines[0]), "high": str(lines[-1]), "n": n, "capital": float(capital),
            "fee_pct": float(fee_pct), "fee_asset": fee_asset,
            "filters": hashed_params["filters"], "resync_candles": resync_candles,
            "halflife_h": float(halflife_h), "sigma_scale": float(sigma_scale),
            "csv_sha256": csv_hash, "params_sha256": params_digest, "metrics": metric,
            "events": events, "equity": equity}
    if include_details:
        result["details"] = {
            "balances": {"USDT": str(exchange.usdt), "XRP": str(exchange.base)},
            "fees_usdt": str(exchange.fees),
            "cells": [{key: (str(cell[key]) if isinstance(cell.get(key), Decimal) else cell.get(key))
                       for key in ("level_idx", "state", "held_qty", "cycles_completed", "pnl",
                                   "entry_price", "entry_cost", "capital", "qty", "price", "sell_price")}
                      for cell in cells],
        }
    return _json_safe(result)
