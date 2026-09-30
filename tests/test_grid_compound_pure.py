from decimal import Decimal

import pytest

from grid.compound import compound_amount
from grid.policy import DEFAULT_SMART_PARAMS, validate_params


def params(**updates):
    return {**DEFAULT_SMART_PARAMS, "compound_enabled": True,
            "compound_ratio": 1.0, "compound_max_growth_pct": 100.0, **updates}


def test_compound_amount_applies_ratio_and_growth_room_exactly():
    decision = compound_amount(Decimal("20.123456789"), Decimal("100"), Decimal("5"),
                               params(compound_ratio=0.5), Decimal("500"))
    assert decision.amount == Decimal("10.06172839")
    assert decision.reason == "applied"
    assert decision.details["room"] == Decimal("95")


def test_compound_amount_caps_at_growth_room_and_reports_cap():
    decision = compound_amount(Decimal("50"), Decimal("100"), Decimal("95"),
                               params(compound_max_growth_pct=100), Decimal("500"))
    assert decision.amount == Decimal("5")
    assert decision.reason == "applied"


@pytest.mark.parametrize("pnl", [Decimal("0"), Decimal("-1.25")])
def test_compound_amount_does_not_compound_zero_or_loss(pnl):
    decision = compound_amount(pnl, Decimal("100"), Decimal("0"), params(), Decimal("500"))
    assert decision.amount == 0
    assert decision.reason == "no_profit"


def test_compound_amount_disabled_and_growth_cap_reasons():
    disabled = compound_amount(Decimal("5"), 100, 0, params(compound_enabled=False), 500)
    capped = compound_amount(Decimal("5"), 100, 100, params(compound_max_growth_pct=100), 500)
    assert (disabled.amount, disabled.reason) == (0, "disabled")
    assert (capped.amount, capped.reason) == (0, "growth_cap")


def test_compound_amount_rounds_down_to_eight_decimal_places():
    decision = compound_amount(Decimal("0.000000019"), 100, 0,
                               params(compound_ratio=0.5), 500)
    assert decision.amount == Decimal("0.00000000")
    assert decision.reason == "rounded_to_zero"


def test_compound_params_default_off_and_validate_types_and_ranges():
    defaults = validate_params({}, 5)
    assert defaults["compound_enabled"] is False
    assert defaults["compound_ratio"] == 1.0
    assert defaults["compound_max_growth_pct"] == 100.0
    for invalid in (
        {"compound_enabled": 1}, {"compound_ratio": 0},
        {"compound_ratio": 1.01}, {"compound_max_growth_pct": 0},
    ):
        with pytest.raises(ValueError):
            validate_params(invalid, 5)
