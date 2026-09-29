"""Pure Decimal calculations for grid price lines and cell plans."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_DOWN
from typing import Any

from data.exchange_filters import FilterViolation, SymbolFilters


# Testnet reports zero spot commission, but live spot fees are typically 0.1%;
# use that conservative estimate so sell filters do not depend on the Testnet profile.
FEE_RATE_ESTIMATE = Decimal("0.001")


class GridConfigError(ValueError):
    pass


@dataclass(frozen=True)
class CellPlan:
    level_idx: int
    buy_price: Decimal
    sell_price: Decimal
    capital: Decimal
    qty: Decimal
    initial_state: str


def _d(value: Any) -> Decimal:
    try:
        return value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise GridConfigError(f"invalid decimal value: {value!r}") from exc


def _cfg(cfg: Any, name: str, default: Any) -> Decimal:
    value = cfg.get(name, default) if isinstance(cfg, dict) else getattr(cfg, name, default)
    return _d(value)


def compute_lines(
    low: Decimal | str | float,
    high: Decimal | str | float,
    n: int,
    filters: SymbolFilters,
) -> list[Decimal]:
    low_d, high_d = _d(low), _d(high)
    if n < 1:
        raise GridConfigError("n must be positive")
    if low_d >= high_d:
        raise GridConfigError("low must be less than high")
    step = (high_d - low_d) / Decimal(n)
    lines = [filters.round_price(low_d + step * i, "nearest") for i in range(n + 1)]
    if any(right <= left for left, right in zip(lines, lines[1:])):
        raise GridConfigError("rounded grid lines must be strictly increasing")
    return lines


def plan_cells(
    lines: list[Decimal],
    capital_total: Decimal | str | float,
    snapshot: dict[str, Any],
    filters: SymbolFilters,
    cfg: Any,
) -> list[CellPlan]:
    n = len(lines) - 1
    if n < 4:
        raise GridConfigError("at least 4 grid levels are required")
    if len(lines) != n + 1 or any(b <= a for a, b in zip(lines, lines[1:])):
        raise GridConfigError("grid lines must be strictly increasing")
    low, high, capital = lines[0], lines[-1], _d(capital_total)
    if low >= high or capital <= 0:
        raise GridConfigError("low must be below high and capital must be positive")

    bid = _d(snapshot["bid_price"])
    ask = _d(snapshot["ask_price"])
    avg_price = _d(snapshot["avg_price"])
    mid = (bid + ask) / Decimal(2)
    if not low < mid < high:
        raise GridConfigError("exchange mid price must be strictly inside the grid range")

    min_step_pct = _cfg(cfg, "grid_min_step_pct", Decimal("0.003"))
    max_level_pct = _cfg(cfg, "capital_max_por_nivel_pct", Decimal("0.30"))
    per_cell = (capital / Decimal(n)).quantize(Decimal("0.00000001"), rounding=ROUND_DOWN)
    if per_cell > capital * max_level_pct:
        raise GridConfigError("cell capital exceeds CAPITAL_MAX_POR_NIVEL_PCT")

    plans = []
    for index, (buy_price, sell_price) in enumerate(zip(lines, lines[1:])):
        if (sell_price - buy_price) / buy_price < min_step_pct:
            raise GridConfigError(f"level {index} step is below GRID_MIN_STEP_PCT")
        qty = filters.round_qty_down(per_cell / buy_price)
        if qty < filters.min_qty or qty * buy_price < filters.min_notional:
            raise GridConfigError(f"level {index} buy does not meet quantity/notional filters")
        net_est = filters.round_qty_down(qty * (Decimal(1) - FEE_RATE_ESTIMATE))
        if net_est < filters.min_qty or net_est * sell_price < filters.min_notional:
            raise GridConfigError(f"level {index} net sell does not meet quantity/notional filters")
        initial_state = "IDLE"
        if buy_price < bid:
            try:
                filters.validate_order("BUY", buy_price, qty, avg_price)
            except FilterViolation:
                initial_state = "IDLE"
            else:
                initial_state = "BUY_OPEN"
        plans.append(CellPlan(index, buy_price, sell_price, per_cell, qty, initial_state))

    if sum((plan.capital for plan in plans), Decimal(0)) > capital:
        raise GridConfigError("sum of cell capital exceeds capital_total")
    return plans
