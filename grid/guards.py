"""Pure safety guards shared by grid creation paths."""
from __future__ import annotations

from decimal import Decimal
from typing import Any, Iterable


def sell_level_conflicts(new_sell_prices: Iterable[Any], existing_grids_levels: Iterable[Any],
                         tick_size: Any, tolerance_pct: Any) -> list[dict[str, str]]:
    tick = Decimal(str(tick_size))
    tolerance = Decimal(str(tolerance_pct)) / Decimal(100)
    existing = [row for row in existing_grids_levels
                if str(row.get("state", "")).upper() != "DONE" and row.get("sell_price") is not None]
    conflicts = []
    for candidate in new_sell_prices:
        price = Decimal(str(candidate))
        for row in existing:
            old = Decimal(str(row["sell_price"]))
            if abs(price - old) <= max(tick, price * tolerance):
                conflicts.append({"new_sell_price": str(price), "existing_sell_price": str(old)})
    return conflicts


def sell_level_conflict_message(conflicts: list[dict[str, str]]) -> str:
    pairs = ", ".join(f"{item['new_sell_price']}~{item['existing_sell_price']}" for item in conflicts)
    return f"sell level conflict: overlapping SELL prices ({pairs})"
