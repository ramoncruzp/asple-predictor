"""Pure decision metrics and provisional policy rules for smart grids."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from math import erf, exp, isfinite, log, sqrt
from typing import Any, Mapping


DEFAULT_SMART_PARAMS: dict[str, float | int | None] = {
    "horizon_h": 24,
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
    "adjust_trigger_z": 0.75,
    "adjust_cooldown_h": 6,
    "adjust_trapped_cap_pct": 30.0,
    "adjust_n": None,
}


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
    unknown = sorted(set(supplied) - set(DEFAULT_SMART_PARAMS))
    if unknown:
        raise ValueError(f"unknown smart parameter(s): {', '.join(unknown)}")
    result = dict(DEFAULT_SMART_PARAMS)
    result.update(supplied)

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
    return result


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
