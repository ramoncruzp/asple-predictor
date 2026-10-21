"""Historical candle loop around production grid planning and policy functions."""
from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone
from decimal import Decimal

import numpy as np

from data.exchange_filters import SymbolFilters
from grid.adjust import plan_adjust_with_shrink
from grid.levels import compute_lines, plan_cells
from grid.policy import (PolicyDecision, adjust_decision, evaluate_grid, evaluate_target,
                         evaluate_max_days, idle_shrink_decision, respace_decision,
                         stoploss_candidates, validate_params, direction_gate)
from grid.sim.data import ewma_sigma_24h
from grid.sim.exchange import SimExchange, SimInsufficientFunds
from grid.sim.metrics import calculate_metrics
from grid.compound import compound_amount
from grid.loans import plan_loan, plan_repayment, select_borrower, select_lender


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


def _max_cash_reachable(cash_now, cells, bid, filters, fee_rate, dust_qty=0):
    """Upper-bound cash if every profitable, market-sellable cell were sold now."""
    cash = Decimal(str(cash_now))
    price = Decimal(str(bid))
    fee = Decimal(str(fee_rate))
    from grid.policy import plan_dust_sweep
    dust_plan = plan_dust_sweep(dust_qty, price, filters, fee * 100)
    if dust_plan["sweepable"]:
        cash += dust_plan["proceeds_net"]
    for cell in cells:
        qty = cell["held_qty"]
        if qty <= 0:
            continue
        try:
            rounded = filters.round_qty_down(qty)
            sellable = rounded >= filters.min_qty
            if getattr(filters, "max_qty", 0) and rounded > filters.max_qty:
                sellable = False
            if getattr(filters, "apply_min_to_market", False) and rounded * price < filters.min_notional:
                sellable = False
        except (AttributeError, TypeError, ValueError):
            sellable = False
        proceeds = qty * price * (1 - fee)
        basis = cell["entry_cost"] + cell.get("entry_fee_usdt", Decimal(0))
        if sellable and proceeds > basis:
            cash += proceeds
    return float(cash)


def run_simulation(candles, *, strategy="simple", n=10, capital=100, low=None, high=None,
                   width_pct=None, fee_pct=.1, resync_candles=3, params=None,
                   halflife_h=72, sigma_scale=1.0, csv_hash=None, filters=None,
                   fee_asset=None, include_details=False, trace_callback=None,
                   sigma_values=None, idle_shrink_enabled=False, adjust_enabled_global=True,
                   stoploss_enabled=True, close_wait_enabled=False, respacing_enabled=False,
                   direction_signal=None, direction_threshold=0.60):
    if strategy not in {"simple", "smart"}:
        raise ValueError("strategy must be simple or smart")
    dust_sweep_enabled = bool(params is None or params.get("dust_sweep_enabled", True))
    policy_params = ({key: value for key, value in params.items() if key != "dust_sweep_enabled"}
                     if params else None)
    if n < 4 or capital <= 0 or resync_candles < 1 or fee_pct < 0:
        raise ValueError("n >= 4, capital > 0, resync-candles >= 1 and fee-pct >= 0 required")
    if strategy == "smart":
        effective = validate_params(policy_params, n)
        effective["out_of_range_mode"] = "wait" if close_wait_enabled else "close"
    elif policy_params and any(key in policy_params for key in
        ("target_pct", "target_usdt", "target_basis", "max_days", "dust_sweep_threshold_pct",
         "compound_enabled", "compound_ratio", "compound_max_growth_pct")):
        effective = validate_params(policy_params, n)
    else:
        effective = {}
    if strategy == "simple" and policy_params and policy_params.get("loans_enabled") is True:
        raise ValueError("loans require the smart strategy")
    target_enabled = effective.get("target_pct") is not None or effective.get("target_usdt") is not None
    closes = candles.close
    direction_values = direction_signal
    if direction_values is not None and len(direction_values) != len(candles.timestamp):
        raise ValueError("direction_signal debe alinearse con candles.timestamp")
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
    reserve = (Decimal(str(capital)) * Decimal(str(effective.get("reserve_pct", 0))) / 100
               if strategy == "smart" and effective.get("loans_enabled") else Decimal(0))
    cells_plan = plan_cells(lines, str(Decimal(str(capital)) - reserve),
                            {"bid_price": str(initial * (1 - 1e-8)), "ask_price": str(initial * (1 + 1e-8)),
                             "avg_price": str(initial)}, filters,
                            {"grid_min_step_pct": .003, "capital_max_por_nivel_pct": .30})
    cells = [{"level_idx": p.level_idx, "price": p.buy_price, "sell_price": p.sell_price,
              "capital": p.capital, "qty": p.qty, "state": "IDLE", "entry_price": None,
              "entry_cost": Decimal(0), "held_qty": Decimal(0), "bought_at": None, "cycles_completed": 0,
              "pnl": Decimal(0), "capital_base": p.capital, "capital_compound": Decimal(0),
              "capital_loan": Decimal(0), "stop_loss_pct": Decimal(str(effective.get("stop_loss_pct", 5))) if strategy == "smart" else None}
             for p in cells_plan]
    for row, plan in zip(cells, cells_plan):
        row["state"] = plan.initial_state
    exchange = SimExchange(capital, fee_pct, filters=filters, fee_asset=fee_asset)
    if sigma_values is None:
        bar_deltas = np.diff(np.asarray(candles.timestamp, dtype=np.int64))
        median_bar_seconds = float(np.median(bar_deltas)) if len(bar_deltas) else 0.0
        if median_bar_seconds == 300:
            bars_per_hour = 12
        elif median_bar_seconds == 3600:
            bars_per_hour = 1
        else:
            raise ValueError("simulation candles must have 300-second or 3600-second spacing")
        sigma = ewma_sigma_24h(closes, halflife_h, bars_per_hour=bars_per_hour) * float(sigma_scale)
    else:
        if len(sigma_values) != len(closes):
            raise ValueError("sigma_values must have one item per candle")
        sigma = np.asarray([np.nan if value is None else float(value) for value in sigma_values], dtype=float)
    if strategy == "smart" and effective.get("sigma_24h_open") is None:
        effective["sigma_24h_open"] = next(
            (float(value) for value in sigma if np.isfinite(value) and value > 0), None)
    equity, events, trapped_values = [], [], []
    recent_adjust_blocked = []
    adjust_attempts_blocked = 0
    adjust_rejected_events = 0
    status, paused_since, pause_reasons = "ACTIVE", None, None
    last_adjust_at = None
    open_loans = []
    next_loan_id = 1
    grid_created_at = datetime.fromtimestamp(int(candles.timestamp[0]), timezone.utc)

    def transfer_open_loans(ts_value, reason):
        nonlocal reserve
        for loan in open_loans:
            if loan["status"] != "OPEN":
                continue
            amount = loan["amount"]
            borrower = cells[int(loan["borrower_idx"])]
            lender_idx = loan.get("lender_idx")
            lender = None if lender_idx is None else cells[int(lender_idx)]
            if lender is None:
                reserve += amount
                borrower["capital"] -= amount
                borrower["capital_loan"] -= amount
                borrower["qty"] = filters.round_qty_down(borrower["capital"] / borrower["price"])
            else:
                borrower["capital_base"] += amount
                lender["capital_base"] -= amount
                borrower["capital_loan"] -= amount
                lender["capital_loan"] += amount
            loan["status"] = "TRANSFERRED"
            events.append({"ts": int(ts_value), "type": "LOAN_TRANSFERRED",
                "level_idx": borrower["level_idx"], "reason": reason,
                "details": {"loan_id": loan["loan_id"], "amount": str(amount),
                            "borrower_idx": borrower["level_idx"], "lender_idx": lender_idx}})
    active_low, active_high = float(lines[0]), float(lines[-1])
    paused = 0
    in_range_candles = 0
    max_cash = float(exchange.usdt)
    max_usdt_balance = float(exchange.usdt)
    max_equity_cells = float(exchange.usdt)
    max_equity_incl_dust = float(exchange.usdt)
    for i, (ts, lo, hi, close) in enumerate(zip(candles.timestamp, candles.low, candles.high, closes)):
        resumed_this_pass = False
        sigma_used = float(sigma[i])
        previous_p_down = (direction_values[i - 1] if direction_values is not None and i > 0 else None)
        direction_blocked = bool(strategy == "smart" and i % resync_candles == 0 and
                                 direction_gate(previous_p_down, direction_threshold))
        if direction_blocked:
            for c in cells:
                order = exchange.orders.get(c["level_idx"])
                if order is not None and order.side == "BUY":
                    exchange.cancel(c["level_idx"], "BUY")
                    c["state"] = "IDLE"
                    events.append({"ts": int(ts), "type": "BUY_CANCELED",
                                   "level_idx": c["level_idx"], "reason": "DIRECTION_GATE"})
        fills = exchange.process(i, lo, hi, close, cells)
        for idx, side, price, qty, fee, qty_net in fills:
            cell = cells[idx]
            fill_event = {"ts": int(ts), "type": f"{side}_FILLED", "level_idx": idx,
                          "price": str(price), "qty": str(qty), "qty_net": str(qty_net),
                          "fee": str(fee)}
            if side == "SELL":
                fill_event["cycle_pnl_net_usdt"] = str(cell.get("last_cycle_pnl", 0))
            events.append(fill_event)
            if side == "SELL" and effective.get("compound_enabled") is True:
                decision = compound_amount(cell.get("last_cycle_pnl", 0),
                    cell.get("capital_base", cell["capital"]), cell.get("capital_compound", 0),
                    effective, capital)
                if decision.amount > 0:
                    cell["capital_compound"] += decision.amount
                    cell["capital"] += decision.amount
                    cell["qty"] = filters.round_qty_down(cell["capital"] / cell["price"])
                    events.append({"ts": int(ts), "type": "COMPOUND_APPLIED", "level_idx": idx,
                                   "amount": str(decision.amount), "reason": decision.reason})
        if i % resync_candles == 0 or (strategy == "simple" and effective.get("max_days") is not None):
            if strategy == "smart" and status in {"ACTIVE", "PAUSED"}:
                policy_cells = [dict(c, held_qty=float(c["held_qty"]),
                                     entry_price=None if c["entry_price"] is None else float(c["entry_price"]),
                                     pnl=float(c["pnl"]),
                                     bought_at=None if c["bought_at"] is None else datetime.fromtimestamp(
                                         int(candles.timestamp[int(c["bought_at"])]) + (int(c["bought_at"]) % 1), timezone.utc),
                                     stop_loss_pct=float(c["stop_loss_pct"]) if c["stop_loss_pct"] is not None else None)
                                for c in cells]
                # Match GridMonitor ordering: stop-loss executes before sync/policy.
                for candidate in (stoploss_candidates(policy_cells, float(close)) if stoploss_enabled else []):
                    c = cells[int(candidate["level_idx"])]
                    exchange.cancel(c["level_idx"])
                    pnl = exchange.market_sell(c, close)
                    events.append({"ts": int(ts), "type": "STOP_LOSS", "level_idx": c["level_idx"],
                                   "reason": "stop_loss_pct", "pnl": str(pnl)})
                policy_cells = [dict(c, held_qty=float(c["held_qty"]),
                                     entry_price=None if c["entry_price"] is None else float(c["entry_price"]),
                                     pnl=float(c["pnl"]),
                                     bought_at=None if c["bought_at"] is None else datetime.fromtimestamp(
                                         int(candles.timestamp[int(c["bought_at"])]) + (int(c["bought_at"]) % 1), timezone.utc),
                                     stop_loss_pct=float(c["stop_loss_pct"]) if c["stop_loss_pct"] is not None else None)
                                for c in cells]
                sigma_now = float(sigma[i])
                if not math.isfinite(sigma_now):
                    sigma_now = None
                sigma_used = float("nan") if sigma_now is None else sigma_now
                decision = evaluate_grid(status, effective, policy_cells, float(close), active_low,
                                         active_high, float(capital), sigma_now, paused_since,
                                         datetime.fromtimestamp(int(ts), timezone.utc), pause_reasons)
                action = decision.action
                now = datetime.fromtimestamp(int(ts), timezone.utc)
                target_decision = None
                if target_enabled and action != "CLOSE_REPOSITORY":
                    target_decision = evaluate_target(effective, float(capital), float(exchange.usdt),
                        policy_cells, float(close), filters, float(fee_pct),
                        dust=({"dust_qty": exchange.dust_qty} if dust_sweep_enabled else None),
                        equity_now=float(exchange.usdt + sum(
                            (cell["held_qty"] * Decimal(str(close)) * (1-exchange.fee_rate)
                             for cell in cells if cell["held_qty"] > 0), Decimal(0))))
                    if target_decision["reached"]:
                        target_decision["bid_used"] = float(close)
                        action = "TARGET"
                        decision = PolicyDecision("TARGET", (), {**decision.metrics, "target": target_decision})
                max_days_result = evaluate_max_days(effective,
                    datetime.fromtimestamp(int(candles.timestamp[0]), timezone.utc), now)
                if max_days_result["expired"] and action not in {"CLOSE_REPOSITORY", "TARGET"}:
                    action = "MAX_DAYS"
                    decision = PolicyDecision("MAX_DAYS", (), {**decision.metrics, "max_days": max_days_result})
                if adjust_enabled_global and action not in {"CLOSE_REPOSITORY", "TARGET"}:
                    adjust_decision_result = adjust_decision(
                        {"strategy": "smart", "status": status, "params": effective, "n_levels": n,
                         "range_low": active_low, "range_high": active_high, "capital_total": capital},
                        policy_cells, float(close), sigma_now, now, last_adjust_at)
                    if (adjust_decision_result.action == "NONE" and idle_shrink_enabled
                            and effective.get("adjust_idle_shrink") is True):
                        prior_event = next((event for event in reversed(events)
                            if event.get("type") == "IDLE_SHRINK_EVAL"), None)
                        prior_details = (prior_event or {}).get("details") or {}
                        idle_decision = idle_shrink_decision(
                            {"strategy": "smart", "status": status, "params": effective, "n_levels": n,
                             "range_low": active_low, "range_high": active_high, "capital_total": capital},
                            policy_cells, float(close), now, last_adjust_at,
                            prior_details.get("cycles_snapshot") if prior_event else None,
                            (datetime.fromtimestamp(int(prior_event["ts"]), timezone.utc)
                             if prior_event else None), global_enabled=True)
                        if idle_decision.metrics.get("should_record"):
                            realized = sum(float(cell.get("pnl") or 0) for cell in cells)
                            unrealized = sum((float(close) - float(cell.get("entry_price") or 0))
                                * float(cell.get("held_qty") or 0) for cell in cells
                                if float(cell.get("held_qty") or 0) > 0)
                            events.append({"ts": int(ts), "type": "IDLE_SHRINK_EVAL",
                                "reason": idle_decision.metrics.get("reason"),
                                "details": {**idle_decision.metrics, "decision": idle_decision.action,
                                    "omission_reason": idle_decision.metrics.get("reason"),
                                    "pnl_realized": realized, "equity": float(capital) + realized + unrealized}})
                        if idle_decision.action == "ADJUST":
                            adjust_decision_result = idle_decision
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
                if (strategy == "smart" and respacing_enabled and action not in
                        {"CLOSE_REPOSITORY", "TARGET", "MAX_DAYS", "ADJUST"}):
                    respace = respace_decision(
                        {"strategy": strategy, "status": status, "params": effective,
                         "n_levels": n, "range_low": active_low, "range_high": active_high,
                         "capital_total": capital}, policy_cells, sigma_now, now, last_adjust_at)
                    reason = respace.metrics.get("reason") or "triggered"
                    previous = [event for event in events if event.get("type") == "RESPACE_EVAL"
                                and event.get("reason") == reason]
                    if not previous or int(ts) - int(previous[-1]["ts"]) >= 6 * 3600:
                        events.append({"ts": int(ts), "type": "RESPACE_EVAL", "reason": reason,
                                       "details": dict(respace.metrics)})
                    if respace.action == "ADJUST":
                        action, decision = "ADJUST", respace
                if action not in {"NONE", "ADJUST"}:
                    events.append({"ts": int(ts), "type": action, "reason": list(decision.reasons),
                                   "metrics": decision.metrics})
                if action in {"ADJUST", "CLOSE_REPOSITORY", "MAX_DAYS", "TARGET"}:
                    transfer_open_loans(ts, "before_adjust" if action == "ADJUST" else "before_close")
                if action == "PAUSE":
                    status, paused_since, pause_reasons = "PAUSED", datetime.fromtimestamp(int(ts), timezone.utc), decision.reasons
                    for c in cells:
                        exchange.cancel(c["level_idx"], "BUY")
                        if c["state"] == "BUY_OPEN":
                            c["state"] = "IDLE"
                elif action == "RESUME":
                    status, paused_since, pause_reasons = "ACTIVE", None, None
                    resumed_this_pass = True
                elif action in {"CLOSE_REPOSITORY", "MAX_DAYS"}:
                    for c in cells:
                        if c["state"] == "SELL_OPEN" and c["held_qty"] > 0:
                            if c["level_idx"] not in exchange.orders:
                                exchange.place(c["level_idx"], "SELL", c["sell_price"], c["held_qty"], i + 1, i)
                            exchange.cancel(c["level_idx"], "BUY")
                        else:
                            order = exchange.orders.get(c["level_idx"])
                            if order is not None and order.side == "BUY":
                                order_id = order.order_id
                                exchange.cancel(c["level_idx"], "BUY")
                                events.append({"ts": int(ts), "type": "BUY_CANCELED",
                                               "level_idx": c["level_idx"], "order_id": order_id,
                                               "reason": "CLOSE_REPOSITORY"})
                            exchange.cancel(c["level_idx"])
                            c.update(state="DONE", capital=Decimal(0))
                    held = [c for c in cells if c["state"] == "SELL_OPEN" and c["held_qty"] > 0]
                    repository_cells = [int(c["level_idx"]) for c in held]
                    status = "HOLDING" if held else "CLOSED"
                    swept = exchange.sweep_dust(close, filters) if dust_sweep_enabled else {"sweepable": False}
                    if swept["sweepable"]:
                        events.append({"ts": int(ts), "type": "DUST_SWEPT", "qty": str(swept["qty"]),
                                       "proceeds_net": str(swept["proceeds_net"]), "residual": str(exchange.dust_qty)})
                    if held:
                        active_low = float(min(c["price"] for c in held))
                        active_high = float(max(c["sell_price"] for c in held))
                        n = len(held)
                    if action == "MAX_DAYS":
                        events.append({"ts": int(ts), "type": "MAX_DAYS_REACHED",
                            "details": {**max_days_result, "repository_cells": repository_cells,
                                "dust_swept": str(swept.get("qty", 0)),
                                "dust_pending": str(exchange.dust_qty),
                                "unrealized_pnl": str(sum((c["held_qty"] * Decimal(str(close))
                                    - c["entry_cost"] for c in held), Decimal(0)))}})
                elif action == "TARGET":
                    target = decision.metrics["target"]
                    sell_ids = {int(row["level_idx"]) for row in target["sell_cells"]}
                    for c in cells:
                        exchange.cancel(c["level_idx"], "BUY")
                        if c["level_idx"] in sell_ids and c["held_qty"] > 0:
                            exchange.cancel(c["level_idx"], "SELL")
                            sold_qty = c["held_qty"]
                            realized = exchange.market_sell(c, close)
                            target.setdefault("fills", []).append({"level_idx": c["level_idx"],
                                "qty": str(sold_qty), "bid": float(close), "pnl": str(realized)})
                        elif c["held_qty"] <= 0:
                            exchange.cancel(c["level_idx"])
                            c.update(state="DONE", capital=Decimal(0))
                    held = [c for c in cells if c["held_qty"] > 0]
                    swept = exchange.sweep_dust(close, filters) if dust_sweep_enabled else {"sweepable": False}
                    if swept["sweepable"]:
                        events.append({"ts": int(ts), "type": "DUST_SWEPT", "qty": str(swept["qty"]),
                                       "proceeds_net": str(swept["proceeds_net"]), "residual": str(exchange.dust_qty)})
                    cash_total = float(exchange.usdt)
                    equity_total = cash_total + sum(float(c["held_qty"] * Decimal(str(close))
                                                     * (1 - exchange.fee_rate)) for c in held)
                    target.update({"cash_total": cash_total, "equity_total_at_close": equity_total,
                                   "repo_cells": [{"level_idx": c["level_idx"], "reason": "not_sold_by_target_plan"}
                                                  for c in held]})
                    event = next((item for item in reversed(events)
                                  if item.get("ts") == int(ts) and item.get("type") == "TARGET"), None)
                    if event is not None:
                        event["type"] = "TARGET_REACHED"
                        event["details"] = {"effective_params": effective, "basis": target["basis"],
                            "capital_initial": float(capital), "cash_now": target["cash_now"],
                            "projected_cash": target["projected_cash"], "equity_now": target["equity_now"],
                            "cash_total": cash_total, "equity_total_at_close": equity_total,
                            "sell_cells": target.get("fills", []), "repo_cells": target["repo_cells"],
                            "estimated_fees_usdt": float(exchange.fees), "bid_used": float(close)}
                    status = "HOLDING" if held else "CLOSED"
                if action == "ADJUST":
                    grid = {"n_levels": n, "range_low": active_low, "range_high": active_high,
                            "capital_total": capital, "params": effective}
                    adjust = plan_adjust_with_shrink(grid, cells, decision.metrics["range_low"],
                                         decision.metrics["range_high"], decision.metrics["n_levels"],
                                         close, filters, type("Settings", (), {"grid_min_step_pct": .003,
                                         "capital_max_por_nivel_pct": .30})(),
                                         enabled=effective.get("adjust_shrink_n", True))
                    if adjust.ok and adjust.mapping:
                        free_capital = sum((c["capital"] for c in cells
                                            if c["state"] in {"IDLE", "BUY_OPEN", "DONE"}
                                            and c["held_qty"] <= 0), Decimal(0))
                        assigned = sum((Decimal(item["capital"])
                                        for item in adjust.mapping[:-1]), Decimal(0))
                        adjust.mapping[-1]["capital"] = str(free_capital - assigned)
                    events.append({"ts": int(ts), "type": "ADJUST" if adjust.ok else "ADJUST_REJECTED",
                                   "reason": adjust.reason,
                                   "details": {**adjust.details, "decision": decision.metrics}})
                    if adjust.ok:
                        last_adjust_at = now
                        if decision.metrics.get("source") == "RESPACE":
                            effective["sigma_24h_open"] = decision.metrics["sigma_now"]
                        for c in cells:
                            exchange.cancel(c["level_idx"], "BUY")
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
                                              "capital_base": Decimal(0),
                                              "capital_compound": Decimal(0),
                                              "capital_loan": Decimal(0),
                                              "stop_loss_pct": Decimal(str(effective.get("stop_loss_pct", 5)))})
                            c = cells[idx]
                            c.update(price=Decimal(item["price"]), sell_price=Decimal(item["sell_price"]),
                                     capital=Decimal(item["capital"]), qty=filters.round_qty_down(
                               Decimal(item["capital"]) / Decimal(item["price"])), state="IDLE")
                        active_low, active_high, n = float(adjust.lines[0]), float(adjust.lines[-1]), len(adjust.lines)-1
            elif strategy == "simple" and target_enabled and status in {"ACTIVE", "PAUSED"}:
                target = evaluate_target(effective, float(capital), float(exchange.usdt), cells,
                    float(close), filters, float(fee_pct),
                    dust=({"dust_qty": exchange.dust_qty} if dust_sweep_enabled else None),
                    equity_now=float(exchange.usdt + sum(
                        (cell["held_qty"] * Decimal(str(close)) * (1-exchange.fee_rate)
                         for cell in cells if cell["held_qty"] > 0), Decimal(0))))
                if target["reached"]:
                    target["bid_used"] = float(close)
                    sell_ids = {int(row["level_idx"]) for row in target["sell_cells"]}
                    fills_at_target = []
                    for c in cells:
                        exchange.cancel(c["level_idx"], "BUY")
                        if c["level_idx"] in sell_ids and c["held_qty"] > 0:
                            exchange.cancel(c["level_idx"], "SELL")
                            sold_qty = c["held_qty"]
                            pnl = exchange.market_sell(c, close)
                            fills_at_target.append({"level_idx": c["level_idx"], "qty": str(sold_qty),
                                                    "bid": float(close), "pnl": str(pnl)})
                        elif c["held_qty"] <= 0:
                            exchange.cancel(c["level_idx"])
                            c.update(state="DONE", capital=Decimal(0))
                    held = [c for c in cells if c["held_qty"] > 0]
                    swept = exchange.sweep_dust(close, filters) if dust_sweep_enabled else {"sweepable": False}
                    if swept["sweepable"]:
                        events.append({"ts": int(ts), "type": "DUST_SWEPT", "qty": str(swept["qty"]),
                                       "proceeds_net": str(swept["proceeds_net"]), "residual": str(exchange.dust_qty)})
                    cash_total = float(exchange.usdt)
                    equity_total = cash_total + sum(float(c["held_qty"] * Decimal(str(close))
                                                     * (1 - exchange.fee_rate)) for c in held)
                    events.append({"ts": int(ts), "type": "TARGET_REACHED", "reason": [],
                        "details": {"effective_params": effective, "basis": target["basis"],
                            "capital_initial": float(capital), "cash_now": target["cash_now"],
                            "projected_cash": target["projected_cash"], "equity_now": target["equity_now"],
                            "cash_total": cash_total, "equity_total_at_close": equity_total,
                            "sell_cells": fills_at_target,
                            "repo_cells": [{"level_idx": c["level_idx"], "reason": "not_sold_by_target_plan"}
                                           for c in held], "estimated_fees_usdt": float(exchange.fees),
                            "bid_used": float(close)}})
                    status = "HOLDING" if held else "CLOSED"
            elif strategy == "simple" and status in {"ACTIVE", "PAUSED"}:
                now = datetime.fromtimestamp(int(ts), timezone.utc)
                max_days_result = evaluate_max_days(effective,
                    datetime.fromtimestamp(int(candles.timestamp[0]), timezone.utc), now)
                if max_days_result["expired"]:
                    for c in cells:
                        exchange.cancel(c["level_idx"], "BUY")
                        if c["state"] == "BUY_OPEN":
                            c["state"] = "IDLE"
                    held = [c for c in cells if c["held_qty"] > 0]
                    repository_cells = [int(c["level_idx"]) for c in held]
                    for c in cells:
                        if c["held_qty"] <= 0:
                            exchange.cancel(c["level_idx"])
                            c.update(state="DONE", capital=Decimal(0))
                    swept = exchange.sweep_dust(close, filters) if dust_sweep_enabled else {"sweepable": False}
                    status = "HOLDING" if held else "CLOSED"
                    events.append({"ts": int(ts), "type": "MAX_DAYS_REACHED",
                        "details": {**max_days_result, "repository_cells": repository_cells,
                            "dust_swept": str(swept.get("qty", 0)),
                            "dust_pending": str(exchange.dust_qty),
                            "unrealized_pnl": str(sum((c["held_qty"] * Decimal(str(close))
                                - c["entry_cost"] for c in held), Decimal(0)))}})
            if strategy == "simple" and status in {"ACTIVE", "PAUSED"} and target_enabled:
                now = datetime.fromtimestamp(int(ts), timezone.utc)
                max_days_result = evaluate_max_days(effective,
                    datetime.fromtimestamp(int(candles.timestamp[0]), timezone.utc), now)
                if max_days_result["expired"]:
                    for c in cells:
                        exchange.cancel(c["level_idx"], "BUY")
                        if c["state"] == "BUY_OPEN":
                            c["state"] = "IDLE"
                    held = [c for c in cells if c["held_qty"] > 0]
                    repository_cells = [int(c["level_idx"]) for c in held]
                    for c in cells:
                        if c["held_qty"] <= 0:
                            exchange.cancel(c["level_idx"])
                            c.update(state="DONE", capital=Decimal(0))
                    swept = exchange.sweep_dust(close, filters) if dust_sweep_enabled else {"sweepable": False}
                    status = "HOLDING" if held else "CLOSED"
                    events.append({"ts": int(ts), "type": "MAX_DAYS_REACHED",
                        "details": {**max_days_result, "repository_cells": repository_cells,
                            "dust_swept": str(swept.get("qty", 0)),
                            "dust_pending": str(exchange.dust_qty),
                            "unrealized_pnl": str(sum((c["held_qty"] * Decimal(str(close))
                                - c["entry_cost"] for c in held), Decimal(0)))}})
            if (strategy == "smart" and effective.get("loans_enabled") is True
                    and status == "ACTIVE"):
                now = datetime.fromtimestamp(int(ts), timezone.utc)
                by_idx = {int(cell["level_idx"]): cell for cell in cells}
                # Settle triggered repayments before considering a new loan, matching the monitor order.
                for loan in open_loans:
                    if loan["status"] != "OPEN":
                        continue
                    borrower = by_idx.get(int(loan["borrower_idx"]))
                    lender = None if loan.get("lender_idx") is None else by_idx.get(int(loan["lender_idx"]))
                    repayment = plan_repayment(loan, lender, borrower) if borrower is not None else None
                    if repayment is None:
                        continue
                    amount = repayment["amount"]
                    remaining = borrower["capital"] - amount
                    minimum = max(filters.min_notional * Decimal(str(effective["loan_min_margin"])),
                                   filters.min_qty * borrower["price"])
                    if remaining < minimum:
                        continue
                    for changed in (borrower, lender):
                        if changed is None or changed["state"] not in {"BUY_OPEN", "IDLE"}:
                            continue
                        exchange.cancel(changed["level_idx"], "BUY")
                        changed["state"] = "IDLE"
                    borrower.update(capital=remaining,
                        capital_loan=borrower["capital_loan"] - amount,
                        qty=filters.round_qty_down(remaining / borrower["price"]))
                    if lender is None:
                        reserve += amount
                    else:
                        lender.update(capital=lender["capital"] + amount,
                            capital_loan=lender["capital_loan"] + amount,
                            qty=filters.round_qty_down((lender["capital"] + amount) / lender["price"]))
                    loan["status"] = "REPAID"
                    events.append({"ts": int(ts), "type": "LOAN_REPAID", "level_idx": borrower["level_idx"],
                                   "details": {"amount": str(amount), "borrower_idx": borrower["level_idx"],
                                               "lender_idx": loan.get("lender_idx")}})
                loan_policy_events = [{**event, "event_type": event.get("type"),
                    "ts": datetime.fromtimestamp(int(event["ts"]), timezone.utc)}
                    for event in events if isinstance(event.get("ts"), (int, float))]
                borrower = select_borrower(cells, loan_policy_events, open_loans, now, effective)
                if borrower is not None:
                    lender = select_lender(cells, loan_policy_events, open_loans,
                        borrower_idx=int(borrower["level_idx"]), now=now, params=effective,
                        grid_created_at=grid_created_at, last_adjust_at=last_adjust_at)
                    loan_plan = plan_loan(borrower, lender, reserve=reserve,
                        capital_total=capital, params=effective,
                        min_notional=filters.min_notional,
                        min_qty=filters.min_qty, step_size=filters.step_size)
                    if loan_plan is not None:
                        amount = loan_plan["amount"]
                        for changed in (borrower, lender):
                            if changed is None or changed["state"] not in {"BUY_OPEN", "IDLE"}:
                                continue
                            exchange.cancel(changed["level_idx"], "BUY")
                            changed["state"] = "IDLE"
                        reserve -= loan_plan["reserve_part"]
                        borrower.update(capital=borrower["capital"] + amount,
                            capital_loan=borrower["capital_loan"] + amount,
                            qty=filters.round_qty_down((borrower["capital"] + amount) / borrower["price"]))
                        if lender is not None and loan_plan["lender_part"] > 0:
                            lender.update(capital=lender["capital"] - loan_plan["lender_part"],
                                capital_loan=lender["capital_loan"] - loan_plan["lender_part"],
                                qty=filters.round_qty_down((lender["capital"] - loan_plan["lender_part"])
                                                           / lender["price"]))
                        loan = {**loan_plan, "status": "OPEN", "created_at": now,
                            "borrower_cycles_at_open": int(borrower["cycles_completed"]),
                            "lender_cycles_at_open": 0 if lender is None else int(lender["cycles_completed"]),
                            "loan_id": next_loan_id}
                        next_loan_id += 1
                        open_loans.append(loan)
                        events.append({"ts": int(ts), "type": "LOAN_CREATED", "level_idx": borrower["level_idx"],
                            "details": {"loan_id": loan["loan_id"], "amount": str(amount),
                                "reserve_part": str(loan_plan["reserve_part"]),
                                "lender_part": str(loan_plan["lender_part"]),
                                "borrower_idx": loan_plan["borrower_idx"],
                                "lender_idx": loan_plan["lender_idx"]}})
            if status in {"ACTIVE", "PAUSED"} and not resumed_this_pass:
                for c in cells:
                    idx = c["level_idx"]
                    if c["state"] == "SELL_OPEN" and idx not in exchange.orders:
                        exchange.place(idx, "SELL", c["sell_price"], c["held_qty"], i + 1, i)
                    elif (c["state"] in {"IDLE", "BUY_OPEN"} and idx not in exchange.orders
                          and status == "ACTIVE" and not direction_blocked
                          and c["price"] < Decimal(str(close))):
                        c["state"] = "BUY_OPEN"
                        try:
                            exchange.place(idx, "BUY", c["price"], c["qty"], i + 1, i)
                        except SimInsufficientFunds as exc:
                            events.append({"ts": int(ts), "type": "BUY_REJECTED", "level_idx": idx,
                                           "reason": str(exc)})
                            c["state"] = "IDLE"
        if status == "PAUSED":
            paused += 1
        market_equity = exchange.usdt + exchange.base * Decimal(str(close))
        equity.append(float(market_equity))
        if active_low <= float(close) <= active_high:
            in_range_candles += 1
        cell_equity = exchange.usdt + sum((c["held_qty"] * Decimal(str(close))
            * (1 - exchange.fee_rate) for c in cells if c["held_qty"] > 0), Decimal(0))
        total_equity = exchange.usdt + exchange.base * Decimal(str(close)) * (1 - exchange.fee_rate)
        max_cash = max(max_cash, _max_cash_reachable(exchange.usdt, cells, close,
                                                     filters, exchange.fee_rate,
                                                     exchange.dust_qty if dust_sweep_enabled else 0))
        max_usdt_balance = max(max_usdt_balance, float(exchange.usdt))
        max_equity_cells = max(max_equity_cells, float(cell_equity))
        max_equity_incl_dust = max(max_equity_incl_dust, float(total_equity))
        trapped_values.append(float(sum((c["held_qty"] * c["entry_price"]
                                         for c in cells if c["entry_price"] is not None), Decimal(0))))
        if status == "HOLDING" and not any(c["state"] == "SELL_OPEN" and c["held_qty"] > 0
                                             for c in cells):
            status = "CLOSED"
        if trace_callback is not None and i % resync_candles == 0:
            projected_cells = cells
            projected_orders = list(exchange.orders.values())
            if status == "HOLDING":
                held = [c for c in cells if c["state"] == "SELL_OPEN" and c["held_qty"] > 0]
                level_map = {int(c["level_idx"]): new_idx for new_idx, c in enumerate(held)}
                projected_cells = [{**c, "level_idx": level_map[int(c["level_idx"])]} for c in held]
                projected_orders = [type(o)(level_map[o.cell], o.side, o.price, o.qty,
                                             o.active_from, o.created_at, o.reserved_usdt, o.order_id)
                                    for o in exchange.orders.values() if o.cell in level_map]
                projected_orders.extend(o for o in exchange.orders.values() if o.side == "BUY")
            trace_callback({"candle": i, "timestamp": int(ts), "status": status,
                            "sigma_24h": (sigma_used if math.isfinite(sigma_used) else None),
                            "range_low": active_low, "range_high": active_high, "n_levels": n,
                            "cells": [{key: c.get(key) for key in ("level_idx", "state", "price", "sell_price",
                                      "held_qty", "capital", "cycles_completed")} for c in projected_cells],
                            "orders": [{"cell": o.cell, "side": o.side, "price": o.price,
                                        "qty": o.qty, "order_id": o.order_id,
                                        "created_at": o.created_at} for o in projected_orders],
                            "equity": market_equity,
                            "equity_cells": cell_equity, "equity_incl_dust": total_equity,
                            "fees_incl_dust_sweep": exchange.fees,
                            "fees": exchange.fees - exchange.dust_sweep_fee_usdt,
                            "balances": {"USDT": exchange.usdt, "XRP": exchange.base},
                            "events": [dict(event) for event in events
                                       if int(event.get("ts", -1)) == int(ts)
                                       and event.get("type") in {"PAUSE", "RESUME", "ADJUST", "TARGET_REACHED",
                                           "ADJUST_REJECTED", "CLOSE_REPOSITORY", "STOP_LOSS"}]})
    # Open inventory after CLOSE is retained and valued at the last close.
    metric = calculate_metrics(equity, float(capital), cells, exchange, closes,
                               paused=paused, trapped_values=trapped_values)
    metric["adjust_attempts_blocked"] = adjust_attempts_blocked
    metric["adjust_rejected_events"] = adjust_rejected_events
    target_events = [event for event in events if event.get("type") == "TARGET_REACHED"]
    metric["target_reached"] = bool(target_events)
    metric["target_cash_total"] = target_events[-1]["details"].get("cash_total") if target_events else None
    metric["target_equity_total_at_close"] = (target_events[-1]["details"].get("equity_total_at_close")
                                                if target_events else None)
    metric["equity_final_after_repository_liquidation"] = float(
        exchange.usdt + exchange.base * Decimal(str(closes[-1])) * (1 - exchange.fee_rate))
    held_qty_final = sum((c["held_qty"] for c in cells), Decimal(0))
    metric.update({"max_cash": max_cash, "max_usdt_balance": max_usdt_balance,
                   "max_equity_cells": max_equity_cells,
                   "max_equity_incl_dust": max_equity_incl_dust,
                   "dust_qty_final": float(exchange.base - held_qty_final),
                   "dust_swept_usdt": float(exchange.dust_swept_usdt),
                   "dust_sweep_fee_usdt": float(exchange.dust_sweep_fee_usdt),
                   "dust_residual_qty": float(exchange.dust_qty),
                   "cash_final": float(exchange.usdt), "base_final": float(exchange.base),
                   "held_qty_final": float(held_qty_final),
                   "pct_time_in_range": in_range_candles / max(1, len(closes)) * 100.0})
    max_days_events = [event for event in events if event.get("type") == "MAX_DAYS_REACHED"]
    if max_days_events:
        details = max_days_events[-1].get("details", {})
        metric.update({"max_days_reached": True,
                       "repository_cells_at_max_days": len(details.get("repository_cells", [])),
                       "unrealized_pnl_at_max_days": float(details.get("unrealized_pnl", 0)),
                       "max_days_age": details.get("age_days")})
    else:
        metric.update({"max_days_reached": False, "repository_cells_at_max_days": None,
                       "unrealized_pnl_at_max_days": None, "max_days_age": None})
    buy_lots, dust_costs = {}, []
    for event in events:
        if event.get("type") == "BUY_FILLED":
            buy_lots.setdefault(int(event["level_idx"]), []).append(
                (Decimal(event["qty"]), Decimal(event["price"])))
        elif event.get("type") == "SELL_FILLED":
            lots = buy_lots.get(int(event["level_idx"]), [])
            if lots:
                bought, price = lots.pop(0)
                dust_costs.append(max(Decimal(0), bought - Decimal(event["qty"])) * price)
        elif event.get("type") == "TARGET_REACHED":
            for fill in event.get("details", {}).get("sell_cells", []):
                lots = buy_lots.get(int(fill["level_idx"]), [])
                if lots:
                    bought, price = lots.pop(0)
                    dust_costs.append(max(Decimal(0), bought - Decimal(fill["qty"])) * price)
    dust_total = sum(dust_costs, Decimal(0))
    spacing = Decimal(str(capital)) / Decimal(str(n)) * Decimal("0.009")
    metric.update({"dust_cycle_count": len(dust_costs),
                   "dust_cycle_cost_usdt_total": float(dust_total),
                   "dust_cycle_cost_usdt_mean": float(dust_total / len(dust_costs)) if dust_costs else 0.0,
                   "dust_spacing_fraction_mean": float(dust_total / len(dust_costs) / spacing)
                       if dust_costs and spacing else 0.0,
                   "realized_net": float(metric["pnl_realized_net_usdt"]),
                   "cycle_realized_net_usdt": sum(
                       float(event["cycle_pnl_net_usdt"]) for event in events
                       if event.get("type") == "SELL_FILLED"),
                   "cycles": int(metric["cycles_completed"])})
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
                                   "capital_base", "capital_compound", "capital_loan",
                                   "entry_price", "entry_cost", "capital", "qty", "price", "sell_price")}
                      for cell in cells],
        }
    return _json_safe(result)
