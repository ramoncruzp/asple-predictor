from decimal import Decimal

from grid.adjust import plan_adjust
from tests.test_grid_engine import create, make_engine


def test_plan_maps_nearest_free_slots_deterministically_and_preserves_pool():
    engine, db, exchange = make_engine(fee_rate="0", fee_asset="USDT")
    grid = create(engine)
    cells = db.get_grid_levels(grid["id"])
    plan = plan_adjust(grid, cells, 95, 105, 5, 100, exchange.filters, engine.settings)
    assert plan.ok
    assert [row["level_idx"] for row in plan.mapping] == [0, 1, 2, 3, 4]
    assert plan.free_capital == Decimal("1000.0000000000")
    assert sum(Decimal(row["capital"]) for row in plan.mapping) == plan.free_capital
    again = plan_adjust(grid, cells, 95, 105, 5, 100, exchange.filters, engine.settings)
    assert plan == again


def test_plan_rejects_midpoint_outside_and_minimum_step():
    engine, db, exchange = make_engine()
    grid = create(engine)
    cells = db.get_grid_levels(grid["id"])
    outside = plan_adjust(grid, cells, 95, 105, 5, 120, exchange.filters, engine.settings)
    narrow = plan_adjust(grid, cells, 99.9, 100.1, 5, 100, exchange.filters, engine.settings)
    assert not outside.ok and "mid_outside" in outside.reason
    assert not narrow.ok and "step_below" in narrow.reason


def test_fixed_cell_covers_target_without_duplicate_and_preserves_fixed_order():
    engine, db, exchange = make_engine(fee_rate="0", fee_asset="USDT")
    grid = create(engine)
    cells = db.get_grid_levels(grid["id"])
    cells[0] = {**cells[0], "state": "SELL_OPEN", "held_qty": 1, "order_id": 999,
                "price": 95, "entry_price": 95}
    plan = plan_adjust(grid, cells, 95, 105, 5, 100, exchange.filters, engine.settings)
    assert plan.ok and len(plan.covered) == 1
    assert plan.covered[0]["level_idx"] == 0
    assert len(plan.mapping) == 4
    assert all(row["target_idx"] != plan.covered[0]["target_idx"] for row in plan.mapping)


def test_plan_rejects_insufficient_free_targets_and_binance_margin():
    engine, db, exchange = make_engine(fee_rate="0", fee_asset="USDT")
    grid = create(engine)
    cells = db.get_grid_levels(grid["id"])
    grid["params"] = {"min_free_cells": 4}
    too_few = plan_adjust(grid, cells, 95, 105, 4, 100, exchange.filters, engine.settings)
    assert not too_few.ok and "min_free_cells_gte_new_n" in too_few.reason
    grid["params"] = {}
    small_cells = [{**row, "capital": 5} for row in cells]
    small = plan_adjust(grid, small_cells, 95, 105, 5, 100, exchange.filters, engine.settings)
    assert not small.ok and "min_notional" in small.reason
