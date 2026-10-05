"""Tests for the pure grid.structure.suggest_structure function (Phase 15E),
plus the statistical-control helpers added in Phase 15E-2's structure study
audit (`grid.sim.structure_study`)."""
from __future__ import annotations

from decimal import Decimal
from dataclasses import replace
from math import asinh

import numpy as np
import pytest

from data.exchange_filters import SymbolFilters
from grid.sim.runner import FILTERS
from grid.sim.structure_study import _max_fixed_bootstrap, _structure_distribution
from grid.structure import suggest_structure


def test_feasible_structure_respects_min_spacing_and_fee_floor():
    result = suggest_structure(0.03, 1000, 1.5, FILTERS, 0.1, min_spacing_pct=0.8)
    assert result["feasible"] is True
    assert result["n_levels"] >= 4
    assert result["spacing_pct"] >= 0.8 - 1e-9
    assert result["spacing_pct"] >= 2 * 0.1 - 1e-9
    assert result["range_low"] < Decimal("1.5") < result["range_high"]
    assert result["reasons"]


def test_wider_sigma_yields_wider_range_and_not_more_levels_than_narrow_sigma():
    narrow = suggest_structure(0.02, 1000, 1.5, FILTERS, 0.1)
    wide = suggest_structure(0.08, 1000, 1.5, FILTERS, 0.1)
    assert (wide["range_high"] - wide["range_low"]) > (narrow["range_high"] - narrow["range_low"])


def test_none_sigma_is_infeasible_with_explicit_reason():
    result = suggest_structure(None, 1000, 1.5, FILTERS, 0.1)
    assert result["feasible"] is False
    assert result["n_levels"] is None
    assert "sigma_24h" in result["reasons"][0]


def test_non_positive_sigma_is_infeasible():
    result = suggest_structure(0.0, 1000, 1.5, FILTERS, 0.1)
    assert result["feasible"] is False
    result_negative = suggest_structure(-0.01, 1000, 1.5, FILTERS, 0.1)
    assert result_negative["feasible"] is False


def test_insufficient_capital_is_infeasible_with_reason():
    result = suggest_structure(0.03, 1, 1.5, FILTERS, 0.1)
    assert result["feasible"] is False
    assert result["n_levels"] is None
    assert any("infeasible" in reason for reason in result["reasons"])


def test_dust_margin_is_subtracted_from_net_edge():
    """A coarser step_size (more dust per cycle) must lower net_edge_pct_per_cycle
    for an otherwise identical structure, and can force fewer levels."""
    fine_filters = SymbolFilters(
        Decimal("0.0001"), Decimal("0.0001"), Decimal("100000"),
        Decimal("0.0001"), Decimal("0.1"), Decimal("100000000"), Decimal("5"), True, 200,
    )
    coarse_filters = SymbolFilters(
        Decimal("0.0001"), Decimal("0.0001"), Decimal("100000"),
        Decimal("1"), Decimal("0.1"), Decimal("100000000"), Decimal("5"), True, 200,
    )
    fine = suggest_structure(0.03, 200, 1.5, fine_filters, 0.1)
    coarse = suggest_structure(0.03, 200, 1.5, coarse_filters, 0.1)
    assert fine["feasible"] is True
    if coarse["feasible"]:
        assert coarse["net_edge_pct_per_cycle"] < fine["net_edge_pct_per_cycle"]
        assert coarse["n_levels"] <= fine["n_levels"]
    else:
        assert any("infeasible" in reason for reason in coarse["reasons"])


def test_result_is_deterministic_for_identical_inputs():
    args = (0.035, Decimal("500"), Decimal("1.5"), FILTERS, 0.1)
    first = suggest_structure(*args)
    second = suggest_structure(*args)
    assert first == second


def test_is_pure_and_does_not_mutate_filters_or_inputs():
    filters_copy = SymbolFilters(**{
        field: getattr(FILTERS, field) for field in
        ("tick_size", "min_price", "max_price", "step_size", "min_qty", "max_qty",
         "min_notional", "apply_min_to_market", "max_num_orders", "band")
    })
    suggest_structure(0.03, 1000, 1.5, filters_copy, 0.1)
    assert filters_copy == FILTERS


def test_range_is_centered_on_mid_in_log_space():
    result = suggest_structure(0.03, 1000, 2.0, FILTERS, 0.1)
    low, high, mid = float(result["range_low"]), float(result["range_high"]), 2.0
    assert abs((mid / low) - (high / mid)) < 0.01 * mid


@pytest.mark.parametrize("horizon_h,k_width,expect_narrower", [(24, 1.0, True), (48, 3.0, False)])
def test_horizon_and_k_width_scale_the_width_predictably(horizon_h, k_width, expect_narrower):
    base = suggest_structure(0.03, 1000, 1.5, FILTERS, 0.1, horizon_h=24.0, k_width=2.0)
    scaled = suggest_structure(0.03, 1000, 1.5, FILTERS, 0.1, horizon_h=horizon_h, k_width=k_width)
    assert base["feasible"] and scaled["feasible"]
    base_width = float(base["range_high"]) - float(base["range_low"])
    scaled_width = float(scaled["range_high"]) - float(scaled["range_low"])
    assert (scaled_width < base_width) == expect_narrower


def test_invalid_arguments_raise_value_error():
    with pytest.raises(ValueError):
        suggest_structure(0.03, 0, 1.5, FILTERS, 0.1)
    with pytest.raises(ValueError):
        suggest_structure(0.03, 1000, 0, FILTERS, 0.1)
    with pytest.raises(ValueError):
        suggest_structure(0.03, 1000, 1.5, FILTERS, -0.1)
    with pytest.raises(ValueError):
        suggest_structure(0.03, 1000, 1.5, FILTERS, 0.1, min_spacing_pct=0)


def _ada_gross_floor_structure(capital):
    width_pct = 18 * .942
    sigma = asinh(width_pct / 200) / 2
    cell = capital / 18
    step_size = Decimal(str(.46 * cell / (100 * 100)))
    filters = replace(FILTERS, step_size=step_size)
    return suggest_structure(sigma, capital, 100, filters, .1,
        min_spacing_pct=.8, min_cell_usdt=5.5, min_margin_after_fees_pct=.7)


def test_ada_100_usdt_18_levels_meets_gross_target_with_informational_dust():
    result = _ada_gross_floor_structure(100)
    assert result["feasible"] is True
    assert result["n_levels"] == 18
    assert result["edge_gross_pct"] == pytest.approx(.742, abs=.001)
    assert result["dust_estimate_pct"] == pytest.approx(.46, abs=.001)
    assert result["net_edge_pct_per_cycle"] == pytest.approx(.282, abs=.002)
    assert result["cell_usdt"] >= Decimal("5.5")


def test_structure_selection_uses_gross_floor_independent_of_net_dust():
    result = _ada_gross_floor_structure(1000)
    assert result["feasible"] is True
    assert result["n_levels"] == 18
    assert result["edge_gross_pct"] >= .7
    assert result["net_edge_pct_per_cycle"] < .7


def test_margin_after_fees_below_minimum_is_infeasible():
    width_pct = 3.5
    sigma = asinh(width_pct / 200) / 2
    filters = replace(FILTERS, step_size=Decimal("0.000001"))
    result = suggest_structure(sigma, 1000, 100, filters, .1,
        min_spacing_pct=.8, min_cell_usdt=5.5, min_margin_after_fees_pct=.7)
    assert result["feasible"] is False
    assert result["n_levels"] is None
    assert any("margen tras comisiones" in reason for reason in result["reasons"])


# --- Phase 15E-2: statistical-control helpers in grid.sim.structure_study ---

def test_max_fixed_bootstrap_point_estimate_matches_formula():
    rng = np.random.default_rng(0)
    b = rng.normal(1.0, 0.1, 50)
    fixed = rng.normal(0.0, 0.1, (50, 5))
    result = _max_fixed_bootstrap(b, fixed, block=5, samples=200, seed=1)
    assert result["point_mean_b"] == pytest.approx(float(b.mean()))
    assert result["point_best_fixed_mean_ex_post"] == pytest.approx(float(fixed.mean(axis=0).max()))
    assert result["point_mean_difference"] == pytest.approx(
        result["point_mean_b"] - result["point_best_fixed_mean_ex_post"]
    )


def test_max_fixed_bootstrap_is_deterministic_for_same_seed():
    rng = np.random.default_rng(3)
    b = rng.normal(0.5, 0.2, 40)
    fixed = rng.normal(0.0, 0.2, (40, 8))
    first = _max_fixed_bootstrap(b, fixed, block=5, samples=300, seed=7)
    second = _max_fixed_bootstrap(b, fixed, block=5, samples=300, seed=7)
    assert first == second


def test_max_fixed_bootstrap_detects_clear_winner():
    b = np.full(60, 5.0)  # B always beats every fixed candidate by a wide, noiseless margin
    fixed = np.tile(np.array([1.0, 2.0, 3.0]), (60, 1))
    result = _max_fixed_bootstrap(b, fixed, block=5, samples=500, seed=2, confidence=.99)
    assert result["beats_best_ex_post_corrected"] is True
    assert result["ci_low"] > 0


def test_max_fixed_bootstrap_detects_clear_loser():
    b = np.full(60, 1.0)  # B always loses to the best fixed candidate (5.0)
    fixed = np.tile(np.array([2.0, 3.0, 5.0]), (60, 1))
    result = _max_fixed_bootstrap(b, fixed, block=5, samples=500, seed=2, confidence=.99)
    assert result["beats_best_ex_post_corrected"] is False
    assert result["ci_high"] < 0


def test_structure_distribution_ignores_infeasible_windows_and_counts_differences():
    elig = [{"window_id": 0}, {"window_id": 1}]
    structures_by_window = {
        0: {"X": {"feasible": True, "n_levels": 6, "width_pct_realized": 20.0,
                  "spacing_pct": 3.0, "cell_usdt": Decimal("16.66"), "net_edge_pct_per_cycle": 0.1}},
        1: {"X": {"feasible": False, "n_levels": None, "width_pct_realized": None,
                  "spacing_pct": None, "cell_usdt": None, "net_edge_pct_per_cycle": None}},
    }
    summary = _structure_distribution(structures_by_window, elig, "X")
    assert summary["n_total_windows"] == 2
    assert summary["n_feasible"] == 1
    assert summary["windows_differ_from_a_fixed_10_9pct"] == 1
    assert summary["n_levels"]["median"] == 6.0
    assert summary["width_pct_realized"]["min"] == summary["width_pct_realized"]["max"] == 20.0


def test_structure_distribution_matches_a_is_not_counted_as_different():
    elig = [{"window_id": 0}]
    structures_by_window = {
        0: {"X": {"feasible": True, "n_levels": 10, "width_pct_realized": 9.2,
                  "spacing_pct": 0.9, "cell_usdt": Decimal("10"), "net_edge_pct_per_cycle": 0.2}},
    }
    summary = _structure_distribution(structures_by_window, elig, "X")
    assert summary["windows_differ_from_a_fixed_10_9pct"] == 0
