from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace

import pytest

from grid.adjust import plan_adjust, plan_adjust_with_shrink
from grid.sim.runner import FILTERS
from grid.policy import validate_params


def fixture(fixed_count=4, free_count=14, *, enabled=True, capital=Decimal("100")):
    n = fixed_count + free_count
    per = capital / n
    cells = []
    for idx in range(n):
        fixed = idx < fixed_count
        cells.append({"level_idx": idx, "state": "SELL_OPEN" if fixed else "BUY_OPEN",
            "held_qty": Decimal("0.01") if fixed else Decimal("0"),
            "capital": per, "price": Decimal("80")-idx if fixed else Decimal("90")+idx,
            "sell_price": Decimal("81")-idx if fixed else Decimal("91")+idx})
    filters = replace(FILTERS, step_size=Decimal("0.0001"), min_qty=Decimal("0.0001"))
    grid = {"n_levels": n, "range_low": Decimal("85"), "range_high": Decimal("115"),
            "capital_total": capital, "params": {"min_free_cells": 2, "adjust_shrink_n": enabled}}
    settings = SimpleNamespace(grid_min_step_pct=0.003, capital_max_por_nivel_pct=0.30)
    return grid, cells, filters, settings


def call(grid, cells, filters, settings, n=None):
    return plan_adjust_with_shrink(grid, cells, Decimal("90"), Decimal("110"),
        grid["n_levels"] if n is None else n, Decimal("100"), filters, settings,
        enabled=grid["params"]["adjust_shrink_n"])


def test_capital_shortage_with_fourteen_free_cells_shrinks_to_fourteen_and_conserves_capital():
    grid, cells, filters, settings = fixture()
    before_fixed = [dict(cell) for cell in cells[:4]]
    old = plan_adjust(grid, cells, 90, 110, 18, 100, filters, settings)
    assert not old.ok and old.reason == "capital_per_cell_below_min_notional_margin"
    plan = call(grid, cells, filters, settings)
    assert plan.ok and plan.details["n_levels"] == 14
    assert plan.capital_per_cell >= filters.min_notional * Decimal("1.1")
    assert sum(Decimal(item["capital"]) for item in plan.mapping) == pytest.approx(plan.free_capital, abs=Decimal("0.000001"))
    assert cells[:4] == before_fixed
    assert plan.details["n_from"] == 18 and plan.details["n_to"] == 14
    assert plan.details["source"] == "CAPITAL_SHRINK"


def test_one_uncovered_inventory_cell_does_not_block_valid_shrink():
    grid, cells, filters, settings = fixture(fixed_count=1, free_count=17)
    old = plan_adjust(grid, cells, 90, 110, 18, 100, filters, settings)
    assert not old.ok and old.reason == "capital_per_cell_below_min_notional_margin"
    plan = call(grid, cells, filters, settings)
    assert plan.ok and plan.details["n_levels"] == 17
    assert cells[0]["held_qty"] == Decimal("0.01") and cells[0]["state"] == "SELL_OPEN"


def test_shrink_disabled_and_noncapital_rejections_keep_existing_result():
    grid, cells, filters, settings = fixture(enabled=False)
    expected = plan_adjust(grid, cells, 90, 110, 18, 100, filters, settings)
    actual = call(grid, cells, filters, settings)
    assert actual == expected and actual.reason == "capital_per_cell_below_min_notional_margin"
    grid, cells, filters, settings = fixture(fixed_count=17, free_count=1)
    plan = call(grid, cells, filters, settings)
    assert not plan.ok and plan.reason == "capital_per_cell_below_min_notional_margin"


def test_valid_existing_adjust_plan_is_equivalent_and_has_no_shrink_source():
    grid, cells, filters, settings = fixture(fixed_count=0, free_count=18)
    expected = plan_adjust(grid, cells, 90, 110, 18, 100, filters, settings)
    actual = call(grid, cells, filters, settings)
    assert actual == expected and actual.ok
    assert "source" not in actual.details


def test_adjust_shrink_n_defaults_on_and_accepts_per_grid_override():
    assert validate_params(None, 18)["adjust_shrink_n"] is True
    assert validate_params({"adjust_shrink_n": False}, 18)["adjust_shrink_n"] is False
    with pytest.raises(ValueError):
        validate_params({"adjust_shrink_n": "false"}, 18)
