from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from grid.loans import plan_loan, plan_repayment, select_borrower, select_lender
from grid.policy import validate_params


NOW = datetime(2026, 9, 30, 12, tzinfo=timezone.utc)
PARAMS = validate_params({"loans_enabled": True}, 6)


def cell(idx, *, state="IDLE", held="0", capital="200", base=None, loan="0", cycles=0,
         price="100", updated=None):
    return {
        "level_idx": idx, "state": state, "held_qty": Decimal(held),
        "capital": Decimal(capital), "capital_base": Decimal(base if base is not None else capital),
        "capital_loan": Decimal(loan), "cycles_completed": cycles, "price": Decimal(price),
        "updated_at": updated,
    }


def event(kind, idx, ago_h=0):
    return {"event_type": kind, "level_idx": idx, "ts": NOW - timedelta(hours=ago_h), "details": {}}


def test_loan_parameters_default_off_and_validate_types_and_bounds():
    defaults = validate_params(None, 6)
    assert defaults["loans_enabled"] is False and defaults["reserve_pct"] == 0
    invalid = [
        {"loans_enabled": 1}, {"reserve_pct": -0.1}, {"reserve_pct": 50},
        {"loan_idle_h": 0}, {"loan_borrower_min_cycles": 0},
        {"loan_recent_sell_h": 0}, {"loan_topup_pct": 101},
        {"loan_lender_max_pct": 0}, {"loan_cooldown_cycles": -1},
        {"loan_min_margin": 0.99}, {"loan_cap_pct": 101},
        {"loan_min_amount": 0},
    ]
    for supplied in invalid:
        with pytest.raises(ValueError):
            validate_params(supplied, 6)


def test_select_borrower_requires_each_state_inventory_cycles_recent_sell_and_cooldown_gate():
    params = {**PARAMS, "loan_borrower_min_cycles": 3, "loan_recent_sell_h": 1.0}
    valid = cell(0, state="IDLE", cycles=4)
    assert select_borrower([valid], [event("SELL_FILLED", 0, 0.5)], [], NOW, params) == valid
    rejected = [
        cell(0, state="SELL_OPEN", cycles=4),
        cell(0, held="0.1", cycles=4),
        cell(0, cycles=2),
    ]
    for row in rejected:
        assert select_borrower([row], [event("SELL_FILLED", 0, 0.5)], [], NOW, params) is None
    assert select_borrower([valid], [event("SELL_FILLED", 0, 2)], [], NOW, params) is None
    assert select_borrower([valid], [], [], NOW, params) is None
    prior = {"borrower_idx": 0, "created_at": NOW - timedelta(minutes=10),
             "borrower_cycles_at_open": 3, "status": "REPAID"}
    assert select_borrower([valid], [event("SELL_FILLED", 0, 0.5)], [prior], NOW, params) is None
    cooled = {**prior, "created_at": NOW - timedelta(hours=2), "borrower_cycles_at_open": 3}
    assert select_borrower([valid], [event("SELL_FILLED", 0, 0.5)], [cooled], NOW,
                           {**params, "loan_cooldown_cycles": 2}) is None


def test_select_borrower_applies_cycle_cooldown_and_deterministic_newest_sell_order():
    params = {**PARAMS, "loan_cooldown_cycles": 2}
    first, second = cell(1, cycles=6), cell(2, cycles=5)
    loans = [{"borrower_idx": 1, "created_at": NOW - timedelta(days=2),
              "borrower_cycles_at_open": 4, "status": "REPAID"}]
    events = [event("SELL_FILLED", 1, 0.2), event("SELL_FILLED", 2, 0.1)]
    assert select_borrower([first, second], events, loans, NOW, params) == second


def test_select_lender_chooses_most_inactive_then_lowest_idx_and_respects_guards():
    params = {**PARAMS, "loan_idle_h": 12, "loan_cooldown_cycles": 2}
    cells = [cell(0, state="BUY_OPEN"), cell(1, state="IDLE"), cell(2, state="IDLE"),
             cell(3, state="IDLE", held="0.2"), cell(4, state="SELL_OPEN")]
    events = [event("BUY_FILLED", 1, 20), event("CELL_REPRICED", 2, 10)]
    created = NOW - timedelta(days=3)
    assert select_lender(cells, events, [], borrower_idx=0, now=NOW, params=params,
                         grid_created_at=created, last_adjust_at=None) == cells[1]
    excluded = [{"borrower_idx": 2, "status": "OPEN"}]
    assert select_lender(cells, events, excluded, borrower_idx=0, now=NOW, params=params,
                         grid_created_at=created, last_adjust_at=None) == cells[1]


@pytest.mark.parametrize("kind", ["BUY_FILLED", "SELL_FILLED", "CELL_REPRICED", "LOAN_CREATED"])
def test_select_lender_resets_inactivity_clock_for_each_activity_type(kind):
    params = {**PARAMS, "loan_idle_h": 12}
    rows = [cell(0, state="BUY_OPEN"), cell(1, state="IDLE"), cell(2, state="IDLE")]
    selected = select_lender(rows, [event(kind, 0, 1)], [], borrower_idx=2, now=NOW,
                             params=params, grid_created_at=NOW - timedelta(days=3),
                             last_adjust_at=None)
    assert selected is rows[1]


def test_select_lender_uses_grid_open_or_latest_adjust_as_initial_activity():
    rows = [cell(0, state="BUY_OPEN"), cell(1, state="IDLE")]
    params = {**PARAMS, "loan_idle_h": 12}
    assert select_lender(rows, [], [], borrower_idx=1, now=NOW, params=params,
                         grid_created_at=NOW - timedelta(hours=2), last_adjust_at=None) is None
    assert select_lender(rows, [], [], borrower_idx=1, now=NOW, params=params,
                         grid_created_at=NOW - timedelta(days=2),
                         last_adjust_at=NOW - timedelta(hours=2)) is None


def test_plan_loan_calculates_need_cap_and_uses_reserve_before_lender():
    borrower = cell(2, capital="200", base="100", loan="0")
    lender = cell(0, state="BUY_OPEN", capital="200", price="100")
    params = {**PARAMS, "loan_topup_pct": 50, "loan_cap_pct": 30,
              "loan_lender_max_pct": 50, "loan_min_margin": 1.1, "loan_min_amount": 1}
    plan = plan_loan(borrower, lender, reserve=Decimal("20"), capital_total=1000,
                     params=params, min_notional=5, min_qty="0.1", step_size="0.1")
    assert plan["amount"] == Decimal("50.00000000")
    assert plan["reserve_part"] == Decimal("20.00000000")
    assert plan["lender_part"] == Decimal("30.00000000")
    assert plan["borrower_need"] == Decimal("50.00000000")


def test_plan_loan_uses_reserve_only_or_partial_lender_capacity_and_rejects_dust():
    borrower = cell(2, capital="200", base="100")
    lender = cell(0, state="BUY_OPEN", capital="20", price="100")
    params = {**PARAMS, "loan_topup_pct": 50, "loan_cap_pct": 30,
              "loan_lender_max_pct": 50, "loan_min_margin": 1.1, "loan_min_amount": 1}
    reserve_only = plan_loan(borrower, None, reserve=100, capital_total=1000,
                             params=params, min_notional=5, min_qty="0.1", step_size="0.1")
    assert reserve_only["amount"] == Decimal("50.00000000")
    assert reserve_only["lender_part"] == 0
    partial = plan_loan(borrower, lender, reserve=0, capital_total=1000,
                        params=params, min_notional=5, min_qty="0.1", step_size="0.1")
    assert partial["amount"] == Decimal("10.00000000")
    assert partial["lender_part"] == partial["amount"]
    dust = plan_loan(borrower, None, reserve="0.5", capital_total=1000,
                     params={**params, "loan_topup_pct": 0.5}, min_notional=5,
                     min_qty="0.1", step_size="0.1")
    assert dust is None


def test_plan_loan_caps_lender_by_minimum_margin_and_exchange_quantity_filters():
    borrower = cell(2, capital="200", base="100")
    lender = cell(0, state="BUY_OPEN", capital="20", price="100")
    params = {**PARAMS, "loan_topup_pct": 50, "loan_cap_pct": 30,
              "loan_lender_max_pct": 100, "loan_min_margin": 1.1, "loan_min_amount": 1}
    plan = plan_loan(borrower, lender, reserve=0, capital_total=1000, params=params,
                     min_notional=5, min_qty="0.1", step_size="0.1")
    assert plan["lender_part"] <= Decimal("14.50000000")
    remaining = Decimal(str(lender["capital"])) - plan["lender_part"]
    quantity = (remaining / Decimal(str(lender["price"])) / Decimal("0.1")).to_integral_value() * Decimal("0.1")
    assert quantity >= Decimal("0.1") and quantity * Decimal(str(lender["price"])) >= Decimal("5")


def test_plan_repayment_waits_for_free_borrower_and_requires_lender_cycle():
    loan = {"lender_idx": 0, "borrower_idx": 2, "amount": 30,
            "reserve_part": 0, "status": "OPEN", "lender_cycles_at_open": 3,
            "borrower_cycles_at_open": 4}
    lender, borrower = cell(0, cycles=3), cell(2, cycles=4)
    assert plan_repayment(loan, lender, borrower) is None
    plan = plan_repayment(loan, cell(0, cycles=5), borrower)
    assert plan["amount"] == Decimal("30.00000000")
    assert plan["reserve_return"] == 0
    assert plan_repayment(loan, cell(0, cycles=5), cell(2, held="1")) is None


def test_plan_repayment_to_reserve_waits_for_borrower_cycle_and_then_returns_full_amount():
    loan = {"lender_idx": None, "borrower_idx": 2, "amount": 12,
            "reserve_part": 12, "status": "OPEN", "borrower_cycles_at_open": 3}
    borrower = cell(2, cycles=3)
    assert plan_repayment(loan, None, borrower) is None
    plan = plan_repayment(loan, None, cell(2, cycles=4))
    assert plan["amount"] == Decimal("12.00000000")
    assert plan["reserve_return"] == Decimal("12.00000000")
