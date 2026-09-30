"""Pure planning primitives for in-place grid range changes."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN
from typing import Any

from data.exchange_filters import SymbolFilters
from grid.levels import GridConfigError, compute_lines


@dataclass(frozen=True)
class AdjustPlan:
    ok: bool
    reason: str | None
    lines: tuple[Decimal, ...]
    covered: tuple[dict, ...]
    mapping: tuple[dict, ...]
    append_level_idxs: tuple[int, ...]
    retire_level_idxs: tuple[int, ...]
    capital_per_cell: Decimal
    free_capital: Decimal
    details: dict[str, Any]


def _d(value: Any) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))


def _free(cell: dict) -> bool:
    return cell.get("state") in {"IDLE", "BUY_OPEN", "DONE"} and _d(cell.get("held_qty", 0)) <= 0


def plan_adjust(
    grid: dict, cells: list[dict], new_low: Any, new_high: Any, new_n: int | None,
    mid: Any, filters: SymbolFilters, settings: Any,
) -> AdjustPlan:
    """Build a deterministic target-line to free-cell assignment or explain rejection."""
    empty = dict(ok=False, reason=None, lines=(), covered=(), mapping=(),
                 append_level_idxs=(), retire_level_idxs=(), capital_per_cell=Decimal(0),
                 free_capital=Decimal(0), details={})
    try:
        low, high, price = _d(new_low), _d(new_high), _d(mid)
        raw_n = grid.get("n_levels", len(cells)) if new_n is None else new_n
        if isinstance(raw_n, bool) or int(raw_n) != raw_n:
            raise ValueError("n_levels_must_be_integer")
        n = int(raw_n)
        minimum_free = int((grid.get("params") or {}).get("min_free_cells", 2))
        if low <= 0 or high <= low or not low < price < high:
            raise ValueError("mid_outside_new_range")
        if n < 4:
            raise ValueError("n_levels_below_4")
        if minimum_free >= n:
            raise ValueError("min_free_cells_gte_new_n")
        lines = compute_lines(low, high, n, filters)
        min_step = _d(getattr(settings, "grid_min_step_pct", 0.003))
        if any((b - a) / a < min_step for a, b in zip(lines, lines[1:])):
            raise ValueError("step_below_grid_min_step_pct")
        fixed = [row for row in cells if not _free(row)]
        free_cells = sorted((row for row in cells if _free(row)), key=lambda row: int(row["level_idx"]))
        targets = list(lines[:-1])
        covered_targets: set[int] = set()
        covered = []
        for cell in fixed:
            cell_price = _d(cell["price"])
            candidates = [(abs(target - cell_price), i) for i, target in enumerate(targets)
                          if abs(target - cell_price) <= filters.tick_size]
            if candidates:
                _dist, index = min(candidates)
                covered_targets.add(index)
                covered.append({"level_idx": int(cell["level_idx"]), "target_idx": index,
                                "price": str(cell_price)})
        uncovered = [i for i in range(len(targets)) if i not in covered_targets]
        if len(uncovered) < minimum_free:
            raise ValueError("too_few_uncovered_free_cells")
        free_capital = sum((_d(row.get("capital", 0)) for row in free_cells), Decimal(0))
        if free_capital <= 0:
            raise ValueError("insufficient_free_capital")
        per_cell = (free_capital / Decimal(len(uncovered))).quantize(Decimal("0.00000001"), rounding=ROUND_DOWN)
        total_capital = _d(grid["capital_total"])
        max_pct = _d(getattr(settings, "capital_max_por_nivel_pct", 0.30))
        if per_cell > total_capital * max_pct:
            raise ValueError("capital_per_cell_exceeds_CAPITAL_MAX_POR_NIVEL_PCT")
        if per_cell < filters.min_notional * Decimal("1.1"):
            raise ValueError("capital_per_cell_below_min_notional_margin")
        slot_idxs = [int(row["level_idx"]) for row in free_cells]
        max_idx = max((int(row["level_idx"]) for row in cells), default=-1)
        append_count = max(0, len(uncovered) - len(slot_idxs))
        append_idxs = list(range(max_idx + 1, max_idx + 1 + append_count))
        remaining = set(uncovered)
        mapping = []
        used_slots = []
        rows_by_idx = {int(row["level_idx"]): row for row in free_cells}
        for level_idx in slot_idxs:
            if not remaining:
                break
            old_price = _d(rows_by_idx[level_idx]["price"])
            target_idx = min(remaining, key=lambda target: (abs(targets[target] - old_price), target))
            remaining.remove(target_idx)
            used_slots.append(level_idx)
            mapping.append({"level_idx": level_idx, "target_idx": target_idx,
                            "price": str(targets[target_idx]),
                            "sell_price": str(lines[target_idx + 1]),
                            "capital": str(per_cell)})
        for target_idx, level_idx in zip(sorted(remaining), append_idxs):
            mapping.append({"level_idx": level_idx, "target_idx": target_idx,
                            "price": str(targets[target_idx]),
                            "sell_price": str(lines[target_idx + 1]),
                            "capital": str(per_cell)})
        retire = [idx for idx in slot_idxs if idx not in used_slots]
        for item in mapping:
            target_price = _d(item["price"])
            qty = filters.round_qty_down(per_cell / target_price)
            if qty < filters.min_qty or qty * target_price < filters.min_notional * Decimal("1.1"):
                raise ValueError("planned_cell_below_binance_minimum_margin")
        return AdjustPlan(True, None, tuple(lines), tuple(covered), tuple(mapping),
                          tuple(append_idxs), tuple(retire), per_cell, free_capital,
                          {"range": {"low": str(lines[0]), "high": str(lines[-1])},
                           "n_levels": n, "covered": covered, "mapping": mapping,
                           "append_level_idxs": append_idxs, "retire_level_idxs": retire,
                           "capital_per_cell": str(per_cell), "free_capital": str(free_capital)})
    except (ValueError, ArithmeticError, GridConfigError) as exc:
        empty["reason"] = str(exc)
        empty["details"] = {"reason": str(exc)}
        return AdjustPlan(**empty)
