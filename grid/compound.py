"""Pure per-cell compound-profit decisions."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN
from typing import Any, Mapping


MONEY_QUANTUM = Decimal("0.00000001")


def _d(value: Any) -> Decimal:
    if isinstance(value, bool):
        raise ValueError("compound values must be numeric")
    return value if isinstance(value, Decimal) else Decimal(str(value))


@dataclass(frozen=True)
class CompoundDecision:
    amount: Decimal
    reason: str
    details: dict[str, Any]


def compound_amount(
    cycle_pnl: Decimal | float | str,
    capital_base: Decimal | float | str,
    capital_compound: Decimal | float | str,
    params: Mapping[str, Any],
    capital_total: Decimal | float | str,
) -> CompoundDecision:
    """Return the positive cycle profit to compound, rounded down to 8 decimals."""
    pnl = _d(cycle_pnl)
    base = _d(capital_base)
    compounded = _d(capital_compound)
    total = _d(capital_total)
    ratio = _d(params.get("compound_ratio", 1.0))
    growth_pct = _d(params.get("compound_max_growth_pct", 100.0))
    enabled = params.get("compound_enabled", False) is True
    growth_limit = base * growth_pct / Decimal(100)
    room = growth_limit - compounded
    candidate = max(Decimal(0), pnl) * ratio
    amount = min(candidate, max(Decimal(0), room)).quantize(MONEY_QUANTUM, rounding=ROUND_DOWN)

    if not enabled:
        reason = "disabled"
        amount = Decimal(0)
    elif pnl <= 0:
        reason = "no_profit"
        amount = Decimal(0)
    elif room <= 0:
        reason = "growth_cap"
        amount = Decimal(0)
    elif amount <= 0:
        reason = "rounded_to_zero"
    else:
        reason = "applied"

    return CompoundDecision(amount, reason, {
        "cycle_pnl": pnl,
        "capital_base": base,
        "capital_compound": compounded,
        "capital_total": total,
        "compound_enabled": enabled,
        "compound_ratio": ratio,
        "growth_limit": growth_limit,
        "room": room,
        "candidate": candidate,
        "amount": amount,
        "reason": reason,
        "rounding": "Decimal ROUND_DOWN to 8 decimal places",
    })
