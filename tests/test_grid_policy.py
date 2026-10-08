from __future__ import annotations

from datetime import datetime, timedelta, timezone
from math import erf, exp, log, sqrt

import pytest

from grid.policy import (
    DEFAULT_SMART_PARAMS,
    break_prob,
    evaluate_grid,
    free_cells,
    norm_cdf,
    stoploss_candidates,
    total_pnl_pct,
    trapped_capital_pct,
    validate_params,
)


NOW = datetime(2026, 9, 29, 12, tzinfo=timezone.utc)


def cell(state="IDLE", **kwargs):
    row = {
        "level_idx": 0, "state": state, "price": 99.0, "sell_price": 101.0,
        "held_qty": 0.0, "pnl": 0.0, "entry_price": None,
        "bought_at": None, "stop_loss_pct": None, "cycles_completed": 0,
    }
    row.update(kwargs)
    return row


def test_defaults_and_parameter_validation_rules():
    assert DEFAULT_SMART_PARAMS == {
        "horizon_h": 4, "sigma_scale": 1.15,
        "pause_enter_prob": 0.10, "pause_exit_prob": 0.05,
        "trapped_age_h": 24, "trapped_cap_pct": 50.0,
        "trapped_exit_factor": 0.8, "min_free_cells": 2,
        "pause_max_h": 48, "close_out_of_range_pct": 5.0,
        "max_loss_pct": 10.0, "stop_loss_pct": 5.0,
        "adjust_enabled": True, "adjust_trigger_z": 0.75,
        "adjust_cooldown_h": 6, "adjust_trapped_cap_pct": 30.0,
        "adjust_n": None,
        "compound_enabled": False, "compound_ratio": 1.0,
        "compound_max_growth_pct": 100.0,
        "loans_enabled": False, "reserve_pct": 0.0,
        "loan_idle_h": 12.0, "loan_borrower_min_cycles": 3,
        "loan_recent_sell_h": 1.0, "loan_topup_pct": 50.0,
        "loan_lender_max_pct": 50.0, "loan_cooldown_cycles": 2,
        "loan_min_margin": 1.1, "loan_cap_pct": 30.0,
        "loan_min_amount": 1.0,
    }
    assert validate_params(None, 4) == DEFAULT_SMART_PARAMS
    assert validate_params({"loans_group": "loans_v2"}, 4)["loans_group"] == "loans_v2"
    assert validate_params({"pause_enter_prob": None}, 4)["pause_enter_prob"] is None
    assert validate_params({"horizon_h": 0.5}, 4)["horizon_h"] == 0.5
    bad = [
        ({"unknown": 1}, 4),
        ({"pause_enter_prob": 0.0}, 4),
        ({"pause_exit_prob": 0.10}, 4),
        ({"trapped_cap_pct": 0}, 4),
        ({"close_out_of_range_pct": -1}, 4),
        ({"max_loss_pct": 0}, 4),
        ({"stop_loss_pct": 0}, 4),
        ({"sigma_scale": 0}, 4),
        ({"horizon_h": 0}, 4),
        ({"min_free_cells": 0}, 4),
        ({"min_free_cells": 4}, 4),
        ({"trapped_exit_factor": 1}, 4),
    ]
    for params, n_levels in bad:
        with pytest.raises(ValueError):
            validate_params(params, n_levels)


def test_norm_cdf_tracks_standard_normal_reference_values():
    assert norm_cdf(0) == pytest.approx(0.5)
    assert norm_cdf(1.96) == pytest.approx(0.9750021, abs=1e-6)
    assert norm_cdf(-1.96) == pytest.approx(0.0249979, abs=1e-6)


def test_break_prob_matches_reflection_formula_with_explicit_values():
    params = {**DEFAULT_SMART_PARAMS, "horizon_h": 6, "sigma_scale": 1.2, "close_out_of_range_pct": 5}
    price, low, high, sigma24 = 100.0, 90.0, 110.0, 0.08
    prob, z_low, z_high, sigma_h = break_prob(price, low, high, sigma24, params)
    expected_sigma = 0.08 * 1.2 * sqrt(6 / 24)
    expected_low = log(price / (low * 0.95)) / expected_sigma
    expected_high = log((high * 1.05) / price) / expected_sigma
    expected = min(1.0, 2 * (1 - norm_cdf(expected_low)) + 2 * (1 - norm_cdf(expected_high)))
    assert sigma_h == pytest.approx(expected_sigma)
    assert z_low == pytest.approx(expected_low)
    assert z_high == pytest.approx(expected_high)
    assert prob == pytest.approx(expected)


def test_break_prob_handles_barriers_outside_price_and_sigma_monotonicity():
    params = {**DEFAULT_SMART_PARAMS, "close_out_of_range_pct": 5.0}
    low_barrier, high_barrier = 90 * 0.95, 110 * 1.05
    at_low = break_prob(low_barrier, 90, 110, 0.03, params)[0]
    below_low = break_prob(low_barrier - 1, 90, 110, 0.03, params)[0]
    above_high = break_prob(high_barrier + 1, 90, 110, 0.03, params)[0]
    narrow = break_prob(100, 90, 110, 0.01, params)[0]
    wide = break_prob(100, 90, 110, 0.10, params)[0]
    assert at_low == 1.0
    assert below_low == 1.0 and above_high == 1.0
    assert wide > narrow


def test_trapped_capital_counts_only_old_sell_inventory_and_reports_unknown_age():
    params = {**DEFAULT_SMART_PARAMS, "trapped_age_h": 24}
    cells = [
        cell("SELL_OPEN", entry_price=20, held_qty=2, bought_at=NOW - timedelta(hours=25)),
        cell("SELL_OPEN", entry_price=10, held_qty=4, bought_at=NOW - timedelta(hours=2)),
        cell("SELL_OPEN", level_idx=2, entry_price=10, held_qty=3, bought_at=None),
        cell("DONE", entry_price=5, held_qty=10, bought_at=NOW - timedelta(days=3)),
    ]
    assert trapped_capital_pct(cells, NOW, params, 100) == pytest.approx(40.0)
    decision = evaluate_grid("ACTIVE", params, cells, 100, 90, 110, 100, None, None, NOW)
    assert decision.metrics["unknown_age"] == [2]


def test_free_cells_counts_only_idle_and_buy_open():
    assert free_cells([cell("IDLE"), cell("BUY_OPEN"), cell("SELL_OPEN"), cell("DONE")]) == 2


def test_total_pnl_includes_realized_and_known_entry_inventory_and_reports_unknown_entry():
    cells = [
        cell("SELL_OPEN", entry_price=90, held_qty=2, pnl=3),
        cell("SELL_OPEN", level_idx=1, entry_price=None, held_qty=4, pnl=1),
        cell("DONE", pnl=-2),
    ]
    assert total_pnl_pct(cells, 100, 100) == pytest.approx(22.0)
    decision = evaluate_grid("ACTIVE", DEFAULT_SMART_PARAMS, cells, 100, 90, 110, 100, None, None, NOW)
    assert decision.metrics["unknown_entry"] == [1]


def test_stoploss_candidates_includes_exact_boundary_and_ignores_missing_inputs():
    cells = [
        cell("SELL_OPEN", level_idx=1, entry_price=100, held_qty=1, stop_loss_pct=5),
        cell("SELL_OPEN", level_idx=2, entry_price=None, held_qty=1, stop_loss_pct=5),
        cell("SELL_OPEN", level_idx=3, entry_price=100, held_qty=1, stop_loss_pct=None),
        cell("BUY_OPEN", level_idx=4, entry_price=100, held_qty=1, stop_loss_pct=5),
    ]
    assert [row["level_idx"] for row in stoploss_candidates(cells, 95)] == [1]


@pytest.mark.parametrize(
    "trigger, expected_reason",
    [("volatility", "break_prob"), ("trapped", "trapped_capital_pct"), ("free", "free_cells")],
)
def test_evaluate_grid_pauses_for_each_trigger_independently(trigger, expected_reason):
    params = {**DEFAULT_SMART_PARAMS, "pause_enter_prob": 0.1, "min_free_cells": 2, "trapped_cap_pct": 50}
    cells = [cell("IDLE"), cell("IDLE"), cell("DONE")]
    mid, sigma, now = 100, None, NOW
    if trigger == "volatility":
        sigma = 0.20
        cells = [cell("IDLE"), cell("IDLE"), cell("DONE")]
    elif trigger == "trapped":
        cells = [cell("SELL_OPEN", entry_price=60, held_qty=1, bought_at=NOW - timedelta(hours=25)), cell("IDLE"), cell("IDLE")]
    else:
        cells = [cell("SELL_OPEN"), cell("DONE"), cell("DONE")]
    decision = evaluate_grid("ACTIVE", params, cells, mid, 90, 110, 100, sigma, None, now)
    assert decision.action == "PAUSE"
    assert expected_reason in decision.reasons


@pytest.mark.parametrize(
    "trigger, status, mid, paused_since, cells, expected_reason",
    [
        ("range", "ACTIVE", 85, None, [cell("IDLE") for _ in range(4)], "out_of_range"),
        ("pause_age", "PAUSED", 100, NOW - timedelta(hours=49), [cell("IDLE") for _ in range(4)], "pause_max_h"),
        ("loss", "ACTIVE", 80, None, [cell("DONE", pnl=-11)], "max_loss_pct"),
    ],
)
def test_evaluate_grid_closes_for_each_trigger(trigger, status, mid, paused_since, cells, expected_reason):
    decision = evaluate_grid(status, DEFAULT_SMART_PARAMS, cells, mid, 90, 110, 100, None, paused_since, NOW)
    assert decision.action == "CLOSE_REPOSITORY"
    assert expected_reason in decision.reasons


def test_close_precedes_pause_and_resume_uses_hysteresis():
    params = {**DEFAULT_SMART_PARAMS, "horizon_h": 24, "min_free_cells": 2,
              "pause_enter_prob": 0.1, "pause_exit_prob": 0.05}
    close_decision = evaluate_grid("ACTIVE", params, [cell("SELL_OPEN")], 85, 90, 110, 100, 0.2, None, NOW)
    assert close_decision.action == "CLOSE_REPOSITORY"
    between = break_prob(100, 90, 110, 0.065, params)[0]
    assert 0.05 < between < 0.1
    active = evaluate_grid("ACTIVE", params, [cell("IDLE") for _ in range(4)], 100, 90, 110, 100, 0.065, None, NOW)
    paused = evaluate_grid("PAUSED", params, [cell("IDLE") for _ in range(4)], 100, 90, 110, 100, 0.065, NOW, NOW)
    assert active.action == "NONE"
    assert paused.action == "NONE"


def test_missing_or_disabled_volatility_does_not_block_other_triggers_or_resume():
    params = {**DEFAULT_SMART_PARAMS, "pause_enter_prob": None, "pause_exit_prob": 0.05}
    active = evaluate_grid("ACTIVE", params, [cell("SELL_OPEN"), cell("DONE"), cell("DONE")], 100, 90, 110, 100, None, None, NOW)
    assert active.action == "PAUSE" and "free_cells" in active.reasons
    paused = evaluate_grid("PAUSED", params, [cell("IDLE") for _ in range(4)], 100, 90, 110, 100, None, NOW, NOW)
    assert paused.action == "RESUME"
    assert paused.metrics["break_prob"] is None


def test_policy_metrics_contain_effective_audit_values():
    decision = evaluate_grid("ACTIVE", DEFAULT_SMART_PARAMS, [cell("IDLE") for _ in range(4)], 100, 90, 110, 100, 0.03, None, NOW)
    for key in ("params", "mid", "sigma_24h", "sigma_h", "z_low", "z_high", "break_prob", "trapped_capital_pct", "free_cells", "total_pnl_pct", "pause_hours", "unknown_age", "unknown_entry"):
        assert key in decision.metrics


def test_break_pause_with_unavailable_volatility_holds_but_other_pause_can_resume():
    cells = [cell("IDLE") for _ in range(4)]
    params = {**DEFAULT_SMART_PARAMS, "min_free_cells": 2}
    break_paused = evaluate_grid("PAUSED", params, cells, 100, 90, 110, 100, None,
                                 NOW, NOW, pause_reasons=("break_prob",))
    capital_paused = evaluate_grid("PAUSED", params, cells, 100, 90, 110, 100, None,
                                   NOW, NOW, pause_reasons=("trapped_capital_pct",))
    available_again = evaluate_grid("PAUSED", params, cells, 100, 90, 110, 100, 0.001,
                                    NOW, NOW, pause_reasons=("break_prob",))
    assert break_paused.action == "NONE" and break_paused.reasons == ("vol_unavailable_hold",)
    assert capital_paused.action == "RESUME"
    assert available_again.action == "RESUME"
