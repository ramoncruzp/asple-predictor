"""Pure deterministic planning for per-grid reserve loans."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, ROUND_CEILING, ROUND_DOWN
from typing import Any, Mapping


QUANTUM = Decimal("0.00000001")
FREE_STATES = {"IDLE", "BUY_OPEN"}
ACTIVITY_EVENTS = {
    "BUY_FILLED", "SELL_FILLED", "CELL_REPRICED", "LOAN_CREATED",
    "LOAN_REPAID", "LOAN_TRANSFERRED", "LOAN_CANCELLED",
}


def _d(value: Any) -> Decimal:
    if isinstance(value, bool):
        raise ValueError("loan values must be numeric")
    result = value if isinstance(value, Decimal) else Decimal(str(value))
    if not result.is_finite():
        raise ValueError("loan values must be finite")
    return result


def _dt(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if not isinstance(value, datetime):
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _cell_base(cell: Mapping[str, Any]) -> Decimal:
    base = cell.get("capital_base")
    if base is None:
        base = _d(cell.get("capital", 0)) - _d(cell.get("capital_compound", 0)) - _d(cell.get("capital_loan", 0))
    return _d(base)


def _event_time(event: Mapping[str, Any]) -> datetime | None:
    return _dt(event.get("ts") or event.get("created_at"))


def select_borrower(
    cells: list[dict], events: list[dict], loans: list[dict], now: datetime,
    params: Mapping[str, Any],
) -> dict | None:
    """Choose the most recently sold eligible cell, tie-breaking by level index."""
    now_utc = _dt(now)
    if now_utc is None or params.get("loans_enabled") is not True:
        return None
    cutoff = now_utc.timestamp() - float(params["loan_recent_sell_h"]) * 3600
    sells: dict[int, datetime] = {}
    for event in events:
        if str(event.get("event_type", "")).upper() != "SELL_FILLED":
            continue
        ts = _event_time(event)
        idx = event.get("level_idx")
        if ts is None or idx is None or not cutoff <= ts.timestamp() <= now_utc.timestamp():
            continue
        idx = int(idx)
        if idx not in sells or ts > sells[idx]:
            sells[idx] = ts

    candidates = []
    for cell in cells:
        idx = int(cell["level_idx"])
        state = str(cell.get("state", "")).upper()
        if state not in FREE_STATES or _d(cell.get("held_qty", 0)) > 0:
            continue
        cycles = int(cell.get("cycles_completed", 0))
        if cycles < int(params["loan_borrower_min_cycles"]) or idx not in sells:
            continue
        sold_at = sells[idx]
        history = [loan for loan in loans if int(loan.get("borrower_idx", -1)) == idx]
        if any((_dt(loan.get("created_at")) or now_utc) >= sold_at for loan in history):
            continue
        if history:
            latest = max(history, key=lambda loan: _dt(loan.get("created_at")) or datetime.min.replace(tzinfo=timezone.utc))
            since_loan = cycles - int(latest.get("borrower_cycles_at_open", 0))
            if since_loan < int(params["loan_cooldown_cycles"]):
                continue
        elif any(str(loan.get("status", "")).upper() in {"OPEN", "PENDING"}
                 and int(loan.get("borrower_idx", -1)) == idx for loan in loans):
            continue
        candidates.append((sold_at, idx, cell))
    if not candidates:
        return None
    return max(candidates, key=lambda item: (item[0], -item[1]))[2]


def select_lender(
    cells: list[dict], events: list[dict], loans: list[dict], *, borrower_idx: int,
    now: datetime, params: Mapping[str, Any], grid_created_at: datetime,
    last_adjust_at: datetime | None,
) -> dict | None:
    """Choose the longest-inactive free lender with deterministic index tie-break."""
    now_utc = _dt(now)
    if now_utc is None or params.get("loans_enabled") is not True:
        return None
    baseline = max(filter(None, (_dt(grid_created_at), _dt(last_adjust_at))), default=now_utc)
    activity: dict[int, datetime] = {}
    for event in events:
        kind = str(event.get("event_type", "")).upper()
        ts = _event_time(event)
        if kind not in ACTIVITY_EVENTS or ts is None or ts > now_utc:
            continue
        details = event.get("details") or {}
        indexes = {event.get("level_idx"), details.get("lender_idx"), details.get("borrower_idx")}
        for raw_idx in indexes - {None}:
            idx = int(raw_idx)
            if idx not in activity or ts > activity[idx]:
                activity[idx] = ts
    for loan in loans:
        ts = _dt(loan.get("updated_at") or loan.get("created_at"))
        if ts is None:
            continue
        for field in ("lender_idx", "borrower_idx"):
            idx = loan.get(field)
            if idx is not None and (int(idx) not in activity or ts > activity[int(idx)]):
                activity[int(idx)] = ts

    active_borrowers = {int(loan["borrower_idx"]) for loan in loans
                        if str(loan.get("status", "")).upper() in {"OPEN", "PENDING"}}
    candidates = []
    for cell in cells:
        idx = int(cell["level_idx"])
        if idx == int(borrower_idx) or idx in active_borrowers:
            continue
        if str(cell.get("state", "")).upper() not in FREE_STATES or _d(cell.get("held_qty", 0)) > 0:
            continue
        lender_history = [loan for loan in loans if loan.get("lender_idx") is not None
                          and int(loan["lender_idx"]) == idx]
        if any(str(loan.get("status", "")).upper() in {"OPEN", "PENDING"} for loan in lender_history):
            continue
        if lender_history and int(cell.get("cycles_completed", 0)) - int(
            max(lender_history, key=lambda loan: _dt(loan.get("created_at")) or baseline)
            .get("lender_cycles_at_open", 0)
        ) < int(params["loan_cooldown_cycles"]):
            continue
        last_activity = max(baseline, activity.get(idx, baseline))
        if (now_utc - last_activity).total_seconds() < float(params["loan_idle_h"]) * 3600:
            continue
        candidates.append((last_activity, idx, cell))
    if not candidates:
        return None
    return min(candidates, key=lambda item: (item[0], item[1]))[2]


def plan_loan(
    borrower: dict, lender: dict | None, *, reserve: Decimal | float | str,
    capital_total: Decimal | float | str, params: Mapping[str, Any],
    min_notional: Decimal | float | str, min_qty: Decimal | float | str,
    step_size: Decimal | float | str,
) -> dict | None:
    """Plan a capped top-up, consuming reserve before a lender's free capital."""
    if params.get("loans_enabled") is not True:
        return None
    reserve_d, total = max(Decimal(0), _d(reserve)), _d(capital_total)
    base, received = _cell_base(borrower), _d(borrower.get("capital_loan", 0))
    pct_topup, pct_cap = _d(params["loan_topup_pct"]), _d(params["loan_cap_pct"])
    requested = max(Decimal(0), min(
        base * pct_topup / 100,
        total * pct_cap / 100 - base - received,
    ))
    requested = requested.quantize(QUANTUM, rounding=ROUND_DOWN)
    if requested <= 0:
        return None
    reserve_part = min(requested, reserve_d).quantize(QUANTUM, rounding=ROUND_DOWN)
    remaining_need = requested - reserve_part
    lender_part = Decimal(0)
    lender_available = Decimal(0)
    if remaining_need > 0 and lender is not None:
        lender_capital = _d(lender.get("capital", 0))
        price = _d(lender.get("price", 0))
        if price <= 0:
            return None
        minimum_qty = _d(min_qty)
        step = _d(step_size)
        if step <= 0:
            return None
        valid_min_qty = (minimum_qty / step).to_integral_value(rounding=ROUND_CEILING) * step
        margin = _d(params["loan_min_margin"])
        required_remaining = max(_d(min_notional) * margin, valid_min_qty * price)
        lender_available = min(
            lender_capital * _d(params["loan_lender_max_pct"]) / 100,
            max(Decimal(0), lender_capital - required_remaining),
        ).quantize(QUANTUM, rounding=ROUND_DOWN)
        lender_part = min(remaining_need, lender_available).quantize(QUANTUM, rounding=ROUND_DOWN)
    amount = (reserve_part + lender_part).quantize(QUANTUM, rounding=ROUND_DOWN)
    if amount < _d(params["loan_min_amount"]):
        return None
    return {
        "borrower_idx": int(borrower["level_idx"]),
        "lender_idx": None if lender_part <= 0 else int(lender["level_idx"]),
        "amount": amount, "reserve_part": reserve_part, "lender_part": lender_part,
        "borrower_need": requested,
        "lender_available": lender_available,
        "lender_capital_before": Decimal(0) if lender is None else _d(lender.get("capital", 0)),
        "lender_capital_after": Decimal(0) if lender is None else _d(lender.get("capital", 0)) - lender_part,
        "borrower_capital_before": _d(borrower.get("capital", base + _d(borrower.get("capital_compound", 0)) + received)),
        "borrower_capital_after": _d(borrower.get("capital", base + _d(borrower.get("capital_compound", 0)) + received)) + amount,
        "lender_price": None if lender is None else _d(lender.get("price", 0)),
        "reason": "reserve_and_lender" if lender_part else "reserve",
    }


def plan_repayment(loan: dict, lender: dict | None, borrower: dict) -> dict | None:
    """Return a full repayment plan only after the borrower is free and its trigger fired."""
    if str(loan.get("status", "")).upper() != "OPEN":
        return None
    if str(borrower.get("state", "")).upper() not in FREE_STATES or _d(borrower.get("held_qty", 0)) > 0:
        return None
    amount = _d(loan.get("amount", 0)).quantize(QUANTUM, rounding=ROUND_DOWN)
    if amount <= 0:
        return None
    if loan.get("lender_idx") is None:
        if int(borrower.get("cycles_completed", 0)) <= int(loan.get("borrower_cycles_at_open", 0)):
            return None
        return {"amount": amount, "reserve_return": amount, "lender_idx": None,
                "borrower_idx": int(loan["borrower_idx"]), "reason": "reserve_repaid"}
    if lender is None or int(lender.get("cycles_completed", 0)) <= int(loan.get("lender_cycles_at_open", 0)):
        return None
    return {"amount": amount, "reserve_return": Decimal(0),
            "lender_idx": int(loan["lender_idx"]), "borrower_idx": int(loan["borrower_idx"]),
            "reason": "lender_cycle_repaid"}
