"""Pure decision metrics and provisional policy rules for smart grids."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from math import erf, exp, isfinite, log, sqrt
from typing import Any, Mapping


DEFAULT_SMART_PARAMS: dict[str, float | int | None] = {
    "horizon_h": 4,
    "sigma_scale": 1.15,
    "pause_enter_prob": 0.10,
    "pause_exit_prob": 0.05,
    "trapped_age_h": 24,
    "trapped_cap_pct": 50.0,
    "trapped_exit_factor": 0.8,
    "min_free_cells": 2,
    "pause_max_h": 48,
    "close_out_of_range_pct": 5.0,
    "max_loss_pct": 10.0,
    "stop_loss_pct": 5.0,
    "adjust_enabled": True,
    "adjust_shrink_n": True,
    "adjust_trigger_z": 0.75,
    "adjust_cooldown_h": 6,
    "adjust_trapped_cap_pct": 30.0,
    "adjust_n": None,
    "compound_enabled": False,
    "compound_ratio": 1.0,
    "compound_max_growth_pct": 100.0,
    "loans_enabled": False,
    "reserve_pct": 0.0,
    "loan_idle_h": 12.0,
    "loan_borrower_min_cycles": 3,
    "loan_recent_sell_h": 1.0,
    "loan_topup_pct": 50.0,
    "loan_lender_max_pct": 50.0,
    "loan_cooldown_cycles": 2,
    "loan_min_margin": 1.1,
    "loan_cap_pct": 30.0,
    "loan_min_amount": 0.2,
}
DEFAULT_GRID_FEE_PCT = 0.1
PROFIT_CLOSE_RETRY_MAX = 8
PROFIT_CLOSE_RETRY_BASE_SECONDS = 60
PROFIT_CLOSE_RETRY_MAX_SECONDS = 900


def profit_close_retry_delay(retry_count: int) -> int:
    return min(PROFIT_CLOSE_RETRY_BASE_SECONDS * (2 ** max(0, int(retry_count))),
               PROFIT_CLOSE_RETRY_MAX_SECONDS)


def build_profit_cells(levels, load_buy_trades, fee_value_usdt,
                       fee_pct: float = DEFAULT_GRID_FEE_PCT,
                       on_fee_estimated=None):
    """Build held-cell accounting from actual BUY fills, estimating fees only without fills."""
    cells = []
    for row in levels:
        qty = float(row.get("held_qty") or 0)
        if qty <= 0:
            continue
        fallback_cost = float(row.get("entry_price") or 0) * qty
        entry_cost = fallback_cost
        entry_fee = fallback_cost * float(fee_pct) / 100.0
        estimated = True
        cid = row.get("buy_client_order_id") or row.get("client_order_id")
        if cid:
            try:
                trades = load_buy_trades(cid)
                quote = sum(float(trade.get("quoteQty") or
                    float(trade.get("qty", 0)) * float(trade.get("price", 0)))
                    for trade in (trades or []))
                if quote > 0:
                    actual_fee = float(fee_value_usdt(trades))
                    entry_cost, entry_fee = quote, actual_fee
                    estimated = False
            except Exception:
                pass
        if estimated and on_fee_estimated is not None:
            on_fee_estimated()
        cells.append({**row, "level_idx": int(row["level_idx"]), "held_qty": qty,
                      "entry_cost": entry_cost, "entry_fee_usdt": entry_fee})
    return cells


@dataclass(frozen=True)
class PolicyDecision:
    action: str
    reasons: tuple[str, ...]
    metrics: dict[str, Any]


def _number(value: Any, name: str, *, nullable: bool = False) -> float | None:
    if value is None and nullable:
        return None
    if isinstance(value, bool):
        raise ValueError(f"{name} must be numeric")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be numeric") from exc
    if not isfinite(result):
        raise ValueError(f"{name} must be finite")
    return result


def validate_params(params: Mapping[str, Any] | None, n_levels: int) -> dict[str, Any]:
    if isinstance(n_levels, bool) or int(n_levels) != n_levels or n_levels < 2:
        raise ValueError("n_levels must be an integer >= 2")
    if params is not None and not isinstance(params, Mapping):
        raise ValueError("params must be a mapping or None")
    supplied = dict(params or {})
    target_keys = {"target_pct", "target_usdt", "target_basis"}
    optional_runtime_keys = {"dust_sweep_threshold_pct", "max_days"}
    metadata_keys = {"loans_group"}
    unknown = sorted(set(supplied) - set(DEFAULT_SMART_PARAMS) - target_keys - optional_runtime_keys - metadata_keys)
    if unknown:
        raise ValueError(f"unknown smart parameter(s): {', '.join(unknown)}")
    result = dict(DEFAULT_SMART_PARAMS)
    result.update(supplied)
    if "loans_group" in supplied and supplied["loans_group"] not in {"loans", "loans_v2", "control", "manual"}:
        raise ValueError("loans_group must be loans, loans_v2, control, or manual")
    if "dust_sweep_threshold_pct" in supplied:
        result["dust_sweep_threshold_pct"] = _number(
            supplied["dust_sweep_threshold_pct"], "dust_sweep_threshold_pct", nullable=True)
        if result["dust_sweep_threshold_pct"] is not None and not 0 <= result["dust_sweep_threshold_pct"] <= 100:
            raise ValueError("dust_sweep_threshold_pct must be in [0, 100]")
    if "max_days" in supplied:
        result["max_days"] = _number(supplied["max_days"], "max_days")
        if result["max_days"] <= 0:
            raise ValueError("max_days must be greater than zero")

    if target_keys & set(supplied):
        result.setdefault("target_basis", "cash")
        for key, maximum in (("target_pct", 100.0), ("target_usdt", None)):
            if key not in result:
                continue
            result[key] = _number(result[key], key, nullable=True)
            if result[key] is not None and (result[key] <= 0 or
                    (maximum is not None and result[key] > maximum)):
                raise ValueError(f"{key} must be in (0, {maximum}]" if maximum else f"{key} must be greater than zero")
        result["target_basis"] = str(result["target_basis"]).lower()
        if result["target_basis"] not in {"cash", "equity"}:
            raise ValueError("target_basis must be cash or equity")

    for key in ("pause_enter_prob", "pause_exit_prob"):
        result[key] = _number(result[key], key, nullable=True)
        if result[key] is not None and not 0 < result[key] < 1:
            raise ValueError(f"{key} must be between 0 and 1")
    enter, exit_ = result["pause_enter_prob"], result["pause_exit_prob"]
    if enter is not None and exit_ is not None and not exit_ < enter:
        raise ValueError("pause_exit_prob must be less than pause_enter_prob")

    for key in (
        "horizon_h", "sigma_scale", "trapped_age_h", "trapped_cap_pct",
        "trapped_exit_factor", "pause_max_h", "close_out_of_range_pct",
        "max_loss_pct", "stop_loss_pct",
    ):
        result[key] = _number(result[key], key)
        if result[key] <= 0:
            raise ValueError(f"{key} must be greater than zero")
    if result["close_out_of_range_pct"] >= 100:
        raise ValueError("close_out_of_range_pct must be below 100")
    if result["trapped_exit_factor"] >= 1:
        raise ValueError("trapped_exit_factor must be below 1")

    free = result["min_free_cells"]
    if isinstance(free, bool) or int(free) != free or not 1 <= int(free) < int(n_levels):
        raise ValueError("min_free_cells must satisfy 1 <= min_free_cells < n_levels")
    result["min_free_cells"] = int(free)
    enabled = result["adjust_enabled"]
    if not isinstance(enabled, bool):
        raise ValueError("adjust_enabled must be boolean")
    shrink_n = result["adjust_shrink_n"]
    if not isinstance(shrink_n, bool):
        raise ValueError("adjust_shrink_n must be boolean")
    result["adjust_trigger_z"] = _number(result["adjust_trigger_z"], "adjust_trigger_z")
    if result["adjust_trigger_z"] <= 0:
        raise ValueError("adjust_trigger_z must be greater than zero")
    result["adjust_cooldown_h"] = _number(result["adjust_cooldown_h"], "adjust_cooldown_h")
    if result["adjust_cooldown_h"] < 0:
        raise ValueError("adjust_cooldown_h must be >= 0")
    result["adjust_trapped_cap_pct"] = _number(result["adjust_trapped_cap_pct"], "adjust_trapped_cap_pct")
    if not 0 < result["adjust_trapped_cap_pct"] <= 100:
        raise ValueError("adjust_trapped_cap_pct must be in (0, 100]")
    adjust_n = result["adjust_n"]
    if adjust_n is not None:
        if isinstance(adjust_n, bool) or int(adjust_n) != adjust_n or int(adjust_n) < 4:
            raise ValueError("adjust_n must be null or an integer >= 4")
        if result["min_free_cells"] >= int(adjust_n):
            raise ValueError("min_free_cells must be less than adjust_n")
        result["adjust_n"] = int(adjust_n)
    compound_enabled = result["compound_enabled"]
    if not isinstance(compound_enabled, bool):
        raise ValueError("compound_enabled must be boolean")
    result["compound_ratio"] = _number(result["compound_ratio"], "compound_ratio")
    if not 0 < result["compound_ratio"] <= 1:
        raise ValueError("compound_ratio must be in (0, 1]")
    result["compound_max_growth_pct"] = _number(
        result["compound_max_growth_pct"], "compound_max_growth_pct",
    )
    if result["compound_max_growth_pct"] <= 0:
        raise ValueError("compound_max_growth_pct must be greater than zero")
    loans_enabled = result["loans_enabled"]
    if not isinstance(loans_enabled, bool):
        raise ValueError("loans_enabled must be boolean")
    result["reserve_pct"] = _number(result["reserve_pct"], "reserve_pct")
    if not 0 <= result["reserve_pct"] < 50:
        raise ValueError("reserve_pct must be in [0, 50)")
    for key in ("loan_idle_h", "loan_recent_sell_h", "loan_min_amount"):
        result[key] = _number(result[key], key)
        if result[key] <= 0:
            raise ValueError(f"{key} must be greater than zero")
    for key in ("loan_topup_pct", "loan_lender_max_pct", "loan_cap_pct"):
        result[key] = _number(result[key], key)
        if not 0 < result[key] <= 100:
            raise ValueError(f"{key} must be in (0, 100]")
    result["loan_min_margin"] = _number(result["loan_min_margin"], "loan_min_margin")
    if result["loan_min_margin"] < 1:
        raise ValueError("loan_min_margin must be >= 1")
    for key, minimum in (("loan_borrower_min_cycles", 1), ("loan_cooldown_cycles", 0)):
        value = result[key]
        if isinstance(value, bool) or int(value) != value or int(value) < minimum:
            raise ValueError(f"{key} must be an integer >= {minimum}")
        result[key] = int(value)
    if ("loan_lender_max_pct" not in supplied
            and (supplied.get("loans_group") == "control"
                 or (supplied.get("loans_group") == "manual"
                     and supplied.get("loans_enabled") is False))):
        result.pop("loan_lender_max_pct", None)
    return result


def evaluate_target(
    params: Mapping[str, Any] | None, capital_total: float, cash_now: float,
    cells: list[dict], bid: float, filters: Any, fee_pct: float,
    *, equity_now: float | None = None, dust: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Purely select profitable owned cells for an optional target close."""
    effective = dict(params or {})
    target_pct = effective.get("target_pct")
    target_usdt = effective.get("target_usdt")
    basis = str(effective.get("target_basis", "cash")).lower()
    if basis not in {"cash", "equity"}:
        raise ValueError("target_basis must be cash or equity")
    capital = _number(capital_total, "capital_total")
    cash = _number(cash_now, "cash_now")
    bid = _number(bid, "bid")
    fee = _number(fee_pct, "fee_pct") / 100.0
    if capital <= 0 or bid <= 0 or not 0 <= fee < 1:
        raise ValueError("capital, bid, or fee_pct is invalid")
    goals = []
    if target_pct is not None:
        pct = _number(target_pct, "target_pct")
        if not 0 < pct <= 100:
            raise ValueError("target_pct must be in (0, 100]")
        goals.append(capital * pct / 100.0)
    if target_usdt is not None:
        amount = _number(target_usdt, "target_usdt")
        if amount <= 0:
            raise ValueError("target_usdt must be greater than zero")
        goals.append(amount)
    if not goals:
        return {"reached": False, "basis": basis, "cash_now": cash, "projected_cash": cash,
                "equity_now": cash, "sell_cells": [], "repo_cells": [], "reason": "target_disabled"}
    target_profit = min(goals)
    threshold = capital + target_profit
    fee_rate = fee
    dust_qty = Decimal(0)
    dust_proceeds = 0.0
    if dust is not None:
        dust_qty = Decimal(str(dust.get("dust_qty", 0)))
        dust_plan = plan_dust_sweep(dust_qty, Decimal(str(bid)), filters, fee_pct)
        dust_proceeds = float(dust_plan["proceeds_net"]) if dust_plan["sweepable"] else 0.0
        cash += dust_proceeds
    selected, candidates, repository = [], [], []
    equity = cash
    for cell in cells:
        qty = _number(cell.get("held_qty", cell.get("qty", 0)), "held_qty")
        if qty <= 0:
            continue
        proceeds = bid * qty * (1.0 - fee_rate)
        basis_cost = _number(cell.get("entry_cost", 0), "entry_cost") + _number(
            cell.get("entry_fee_usdt", 0), "entry_fee_usdt")
        equity += proceeds
        gain = proceeds - basis_cost
        idx = int(cell.get("level_idx", 0))
        sellable = True
        try:
            rounded = filters.round_qty_down(qty)
            if rounded < filters.min_qty:
                sellable = False
            if getattr(filters, "max_qty", 0) and rounded > filters.max_qty:
                sellable = False
            if getattr(filters, "apply_min_to_market", False) and rounded * Decimal(str(bid)) < filters.min_notional:
                sellable = False
        except (AttributeError, TypeError, ValueError):
            sellable = False
        if gain > 0 and sellable:
            candidates.append({"level_idx": idx, "qty": qty, "bid": bid,
                               "gain_usdt": gain, "proceeds_usdt": proceeds})
        else:
            reason = "not_sellable" if not sellable else "not_profitable"
            repository.append({"level_idx": idx, "reason": reason})
    candidates.sort(key=lambda row: (-row["gain_usdt"], row["level_idx"]))
    projected = cash
    for row in candidates:
        selected.append(row)
        projected += row["proceeds_usdt"]
        if projected >= threshold:
            break
    calculated_equity = cash + sum(
        bid * _number(cell.get("held_qty", cell.get("qty", 0)), "held_qty") * (1.0-fee_rate)
        for cell in cells if _number(cell.get("held_qty", cell.get("qty", 0)), "held_qty") > 0
    )
    equity_value = calculated_equity if equity_now is None else _number(equity_now, "equity_now")
    projected_equity = equity_value
    reached = (projected >= threshold) if basis == "cash" else (equity_value >= threshold)
    if reached:
        sold_idxs = {item["level_idx"] for item in selected}
        repository.extend({"level_idx": int(cell.get("level_idx", 0)), "reason": "target_not_needed"}
                          for cell in cells if _number(cell.get("held_qty", cell.get("qty", 0)), "held_qty") > 0
                          and int(cell.get("level_idx", 0)) not in sold_idxs
                          and int(cell.get("level_idx", 0)) not in {x["level_idx"] for x in repository})
    else:
        selected = []
        projected = cash
    return {"reached": reached, "basis": basis, "cash_now": cash,
            "projected_cash": projected, "equity_now": equity_value,
            "sell_cells": selected if reached else [], "repo_cells": repository,
            "reason": "target_reached" if reached else "target_not_reached",
            "target_usdt": target_profit, "target_threshold": threshold,
            "projected_equity": projected_equity}


def plan_profit_close(cells: list[dict], bid: float, filters: Any, fee_pct: float) -> dict[str, Any]:
    """Plan a close that market-sells every profitable sellable cell and repositories the rest."""
    price = _number(bid, "bid")
    fee = _number(fee_pct, "fee_pct") / 100.0
    if price <= 0 or not 0 <= fee < 1:
        raise ValueError("bid or fee_pct is invalid")
    sell, repository = [], []
    for cell in cells:
        qty = _number(cell.get("held_qty", cell.get("qty", 0)), "held_qty")
        if qty <= 0:
            continue
        basis = _number(cell.get("entry_cost", 0), "entry_cost") + _number(
            cell.get("entry_fee_usdt", 0), "entry_fee_usdt")
        proceeds = price * qty * (1.0 - fee)
        gain = proceeds - basis
        idx = int(cell.get("level_idx", 0))
        try:
            rounded = filters.round_qty_down(qty)
            sellable = rounded >= filters.min_qty
            if getattr(filters, "max_qty", 0) and rounded > filters.max_qty:
                sellable = False
            if getattr(filters, "apply_min_to_market", False) and rounded * Decimal(str(price)) < filters.min_notional:
                sellable = False
        except (AttributeError, TypeError, ValueError):
            sellable = False
        if gain > 0 and sellable:
            sell.append({"level_idx": idx, "qty": qty, "bid": price,
                         "gain_usdt": gain, "proceeds_usdt": proceeds})
        else:
            repository.append({"level_idx": idx,
                "reason": "not_sellable" if not sellable else "not_profitable",
                "loss_usdt": max(0.0, -gain)})
    projected_cash = sum((row["proceeds_usdt"] for row in sell), 0.0)
    net_gain = sum((row["gain_usdt"] for row in sell), 0.0)
    net_loss = sum((row["loss_usdt"] for row in repository), 0.0)
    fees = sum((price * row["qty"] * fee for row in sell), 0.0)
    return {"sell_cells": sell, "repo_cells": repository, "projected_cash": projected_cash,
            "net_gain_usdt": net_gain, "net_loss_usdt": net_loss,
            "estimated_commission_usdt": fees, "bid_used": price}


def evaluate_max_days(params: Mapping[str, Any] | None, created_at: Any,
                      now: Any) -> dict[str, Any]:
    """Evaluate a wall-clock grid lifetime; paused time remains part of its age."""
    raw = (params or {}).get("max_days")
    if raw is None:
        return {"expired": False, "age_days": None, "max_days": None}
    maximum = _number(raw, "max_days")
    if maximum <= 0:
        raise ValueError("max_days must be greater than zero")
    age = max(0.0, (_as_utc(now) - _as_utc(created_at)).total_seconds() / 86400.0)
    return {"expired": age >= maximum, "age_days": age, "max_days": maximum}


def plan_dust_sweep(dust_qty: Any, bid: Any, filters: Any, fee_pct: Any) -> dict[str, Any]:
    """Pure plan for selling only the grid's recorded residual base asset."""
    qty_owned = Decimal(str(dust_qty))
    price = Decimal(str(bid))
    fee = Decimal(str(fee_pct)) / Decimal(100)
    if qty_owned < 0 or price <= 0 or not Decimal(0) <= fee < Decimal(1):
        raise ValueError("dust_qty, bid, or fee_pct is invalid")
    qty = filters.round_qty_down(qty_owned)
    residual = qty_owned - qty
    sweepable = qty >= filters.min_qty
    if sweepable and getattr(filters, "apply_min_to_market", False):
        sweepable = qty * price >= filters.min_notional
    if getattr(filters, "max_qty", 0) and qty > filters.max_qty:
        sweepable = False
    proceeds = qty * price * (Decimal(1) - fee) if sweepable else Decimal(0)
    return {"qty": qty, "sweepable": sweepable, "proceeds_net": proceeds,
            "residual": residual}


def adjust_decision(
    grid: Mapping[str, Any], cells: list[dict], mid: float,
    sigma_24h: float | None, now: datetime, last_adjust_at: datetime | None,
) -> PolicyDecision:
    """Pure provisional decision for an in-place smart-grid range adjustment."""
    raw = grid.get("params") or {}
    if grid.get("strategy", "simple") != "smart" or grid.get("status") != "ACTIVE" \
            or raw.get("adjust_enabled", False) is not True:
        return PolicyDecision("NONE", (), {"adjust_enabled": False})
    n_levels = int(grid.get("n_levels", len(cells)))
    params = validate_params(raw, max(2, n_levels))
    mid = _number(mid, "mid")
    low, high = _number(grid["range_low"], "range_low"), _number(grid["range_high"], "range_high")
    if mid <= 0 or low <= 0 or low >= high:
        raise ValueError("mid or range is invalid")
    if sigma_24h is None or not isfinite(float(sigma_24h)) or float(sigma_24h) <= 0:
        return PolicyDecision("BLOCKED", ("vol_unavailable",), {"sigma_24h": sigma_24h})
    sigma_h = float(sigma_24h) * params["sigma_scale"] * sqrt(params["horizon_h"] / 24.0)
    edge_z = min(log(mid / low), log(high / mid)) / sigma_h if low < mid < high else float("inf")
    close_buffer = params["close_out_of_range_pct"] / 100.0
    within_close = low * (1 - close_buffer) <= mid <= high * (1 + close_buffer)
    triggered = edge_z < params["adjust_trigger_z"] or (not low < mid < high and within_close)
    details = {"mid": mid, "edge_z": edge_z, "sigma_h": sigma_h,
               "trigger_z": params["adjust_trigger_z"], "triggered": triggered}
    if not triggered:
        return PolicyDecision("NONE", (), details)
    reasons = []
    if last_adjust_at is not None:
        elapsed = (_as_utc(now) - _as_utc(last_adjust_at)).total_seconds() / 3600
        details["cooldown_elapsed_h"] = elapsed
        if elapsed < params["adjust_cooldown_h"]:
            reasons.append("cooldown")
    trapped, _unknown = _trapped_details(cells, now, params, float(grid["capital_total"]))
    details["trapped_capital_pct"] = trapped
    if trapped >= params["adjust_trapped_cap_pct"]:
        reasons.append("trapped_capital_pct")
    free = sum(row.get("state") in {"IDLE", "BUY_OPEN", "DONE"} and
               float(row.get("held_qty") or 0) <= 0 for row in cells)
    details["free_cells"] = free
    if free < params["min_free_cells"]:
        reasons.append("free_cells")
    if reasons:
        return PolicyDecision("BLOCKED", tuple(reasons), details)
    log_width = log(high / low)
    new_low = mid / exp(log_width / 2)
    new_high = mid * exp(log_width / 2)
    details.update({"range_low": new_low, "range_high": new_high,
                    "n_levels": params["adjust_n"] or n_levels,
                    "source": "MONITOR"})
    return PolicyDecision("ADJUST", (), details)


def norm_cdf(x: float) -> float:
    """Standard normal cumulative distribution, without a scipy dependency."""
    value = _number(x, "x")
    return 0.5 * (1.0 + erf(value / sqrt(2.0)))


def break_prob(
    price: float,
    low: float,
    high: float,
    sigma_24h: float,
    params: Mapping[str, Any],
) -> tuple[float, float, float, float]:
    """Return two-barrier touch probability, z scores, and horizon sigma."""
    effective = validate_params(params, max(2, int(params.get("min_free_cells", 2)) + 1))
    price, low, high = (_number(price, "price"), _number(low, "low"), _number(high, "high"))
    sigma_24h = _number(sigma_24h, "sigma_24h")
    if min(price, low, high) <= 0 or low >= high or sigma_24h < 0:
        raise ValueError("price, range, or sigma_24h is invalid")
    buffer = effective["close_out_of_range_pct"] / 100.0
    lower_barrier, upper_barrier = low * (1.0 - buffer), high * (1.0 + buffer)
    sigma_h = sigma_24h * effective["sigma_scale"] * sqrt(effective["horizon_h"] / 24.0)
    if sigma_h == 0:
        z_low = float("inf") if price > lower_barrier else float("-inf")
        z_high = float("inf") if price < upper_barrier else float("-inf")
        lower_term = 1.0 if price <= lower_barrier else 0.0
        upper_term = 1.0 if price >= upper_barrier else 0.0
    else:
        z_low = log(price / lower_barrier) / sigma_h
        z_high = log(upper_barrier / price) / sigma_h
        lower_term = 1.0 if price <= lower_barrier else 2.0 * (1.0 - norm_cdf(z_low))
        upper_term = 1.0 if price >= upper_barrier else 2.0 * (1.0 - norm_cdf(z_high))
    probability = min(1.0, lower_term + upper_term)
    return probability, z_low, z_high, sigma_h


def _as_utc(value: Any) -> datetime:
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if not isinstance(value, datetime):
        raise ValueError("datetime value must be a datetime or ISO string")
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _trapped_details(
    cells: list[dict], now: datetime, params: Mapping[str, Any], capital_total: float,
) -> tuple[float, list[Any]]:
    capital_total = _number(capital_total, "capital_total")
    if capital_total <= 0:
        raise ValueError("capital_total must be greater than zero")
    now_utc = _as_utc(now)
    threshold = float(params["trapped_age_h"])
    trapped = 0.0
    unknown_age: list[Any] = []
    for row in cells:
        qty = _number(row.get("held_qty", 0), "held_qty")
        if row.get("state") != "SELL_OPEN" or qty <= 0:
            continue
        bought_at = row.get("bought_at")
        if bought_at is None:
            unknown_age.append(row.get("level_idx"))
            continue
        age_h = (now_utc - _as_utc(bought_at)).total_seconds() / 3600.0
        if age_h < threshold:
            continue
        entry = row.get("entry_price")
        if entry is None:
            continue
        trapped += _number(entry, "entry_price") * qty
    return trapped / capital_total * 100.0, unknown_age


def trapped_capital_pct(
    cells: list[dict], now: datetime, params: Mapping[str, Any], capital_total: float,
) -> float:
    effective = validate_params(params, max(2, int(params.get("min_free_cells", 2)) + 1))
    return _trapped_details(cells, now, effective, capital_total)[0]


def free_cells(cells: list[dict]) -> int:
    return sum(row.get("state") in {"BUY_OPEN", "IDLE"} for row in cells)


def _pnl_details(cells: list[dict], mid: float, capital_total: float) -> tuple[float, list[Any]]:
    mid = _number(mid, "mid")
    capital_total = _number(capital_total, "capital_total")
    if capital_total <= 0:
        raise ValueError("capital_total must be greater than zero")
    pnl = sum(_number(row.get("pnl", 0), "pnl") for row in cells)
    unknown_entry: list[Any] = []
    for row in cells:
        qty = _number(row.get("held_qty", 0), "held_qty")
        if qty <= 0:
            continue
        entry = row.get("entry_price")
        if entry is None:
            unknown_entry.append(row.get("level_idx"))
            continue
        pnl += (mid - _number(entry, "entry_price")) * qty
    return pnl / capital_total * 100.0, unknown_entry


def total_pnl_pct(cells: list[dict], mid: float, capital_total: float) -> float:
    return _pnl_details(cells, mid, capital_total)[0]


def stoploss_candidates(cells: list[dict], mid: float | None) -> list[dict]:
    if mid is None:
        return []
    mid_value = _number(mid, "mid")
    candidates = []
    for row in cells:
        if row.get("state") != "SELL_OPEN" or _number(row.get("held_qty", 0), "held_qty") <= 0:
            continue
        entry, stop = row.get("entry_price"), row.get("stop_loss_pct")
        if entry is None or stop is None:
            continue
        entry_value, stop_value = _number(entry, "entry_price"), _number(stop, "stop_loss_pct")
        if stop_value > 0 and mid_value <= entry_value * (1.0 - stop_value / 100.0):
            candidates.append(row)
    return candidates


def evaluate_grid(
    grid_status: str,
    params: Mapping[str, Any],
    cells: list[dict],
    mid: float,
    low: float,
    high: float,
    capital_total: float,
    sigma_24h: float | None,
    paused_since: datetime | None,
    now: datetime,
    pause_reasons: tuple[str, ...] | None = None,
) -> PolicyDecision:
    effective = validate_params(params, max(len(cells), int(params.get("min_free_cells", 2)) + 1, 2))
    status = str(grid_status).upper()
    if status not in {"ACTIVE", "PAUSED"}:
        raise ValueError("grid_status must be ACTIVE or PAUSED")
    mid, low, high = (_number(mid, "mid"), _number(low, "low"), _number(high, "high"))
    if min(mid, low, high) <= 0 or low >= high:
        raise ValueError("mid or range is invalid")
    now_utc = _as_utc(now)
    free = free_cells(cells)
    trapped, unknown_age = _trapped_details(cells, now_utc, effective, capital_total)
    pnl, unknown_entry = _pnl_details(cells, mid, capital_total)
    probability = z_low = z_high = sigma_h = None
    if sigma_24h is not None:
        probability, z_low, z_high, sigma_h = break_prob(mid, low, high, sigma_24h, effective)

    pause_hours = None
    if paused_since is not None:
        pause_hours = max(0.0, (now_utc - _as_utc(paused_since)).total_seconds() / 3600.0)
    metrics = {
        "params": dict(effective), "mid": mid, "sigma_24h": sigma_24h,
        "sigma_h": sigma_h, "z_low": z_low, "z_high": z_high,
        "break_prob": probability, "trapped_capital_pct": trapped,
        "free_cells": free, "total_pnl_pct": pnl, "pause_hours": pause_hours,
        "unknown_age": unknown_age, "unknown_entry": unknown_entry,
    }

    close_reasons = []
    buffer = effective["close_out_of_range_pct"] / 100.0
    if mid < low * (1.0 - buffer) or mid > high * (1.0 + buffer):
        close_reasons.append("out_of_range")
    if status == "PAUSED" and pause_hours is not None and pause_hours >= effective["pause_max_h"]:
        close_reasons.append("pause_max_h")
    if pnl <= -effective["max_loss_pct"]:
        close_reasons.append("max_loss_pct")
    if close_reasons:
        return PolicyDecision("CLOSE_REPOSITORY", tuple(close_reasons), metrics)

    if status == "ACTIVE":
        pause_reasons = []
        enter = effective["pause_enter_prob"]
        if probability is not None and enter is not None and probability >= enter:
            pause_reasons.append("break_prob")
        if trapped >= effective["trapped_cap_pct"]:
            pause_reasons.append("trapped_capital_pct")
        if free < effective["min_free_cells"]:
            pause_reasons.append("free_cells")
        if pause_reasons:
            return PolicyDecision("PAUSE", tuple(pause_reasons), metrics)
        return PolicyDecision("NONE", (), metrics)

    resume_reasons = []
    if sigma_24h is None and pause_reasons is not None and "break_prob" in pause_reasons:
        return PolicyDecision("NONE", ("vol_unavailable_hold",), metrics)
    exit_prob = effective["pause_exit_prob"]
    if probability is not None and effective["pause_enter_prob"] is not None and exit_prob is not None:
        if probability > exit_prob:
            resume_reasons.append("break_prob")
    if trapped > effective["trapped_cap_pct"] * effective["trapped_exit_factor"]:
        resume_reasons.append("trapped_capital_pct")
    if free < effective["min_free_cells"] + 1:
        resume_reasons.append("free_cells")
    if resume_reasons:
        return PolicyDecision("NONE", tuple(resume_reasons), metrics)
    return PolicyDecision("RESUME", (), metrics)
