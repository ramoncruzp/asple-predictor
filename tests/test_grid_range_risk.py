import pytest
from math import erfc, sqrt, log
from grid.range_risk import estimate_range_risk

def test_touch_probability_known_value_and_far_barrier():
    sigma = .1
    row = estimate_range_risk(100, 90, 10000, sigma, (24,))[24]
    assert row["touch_floor"] == pytest.approx(.292, abs=.002)
    assert row["touch_ceiling"] < 1e-10
    assert row["exit_upper_bound"] == pytest.approx(row["touch_floor"])

def test_barrier_touch_is_symmetric_and_increases_with_volatility_and_time():
    symmetric = estimate_range_risk(100, 90, 1000/9, .03, (24,))[24]
    assert symmetric["touch_floor"] == pytest.approx(symmetric["touch_ceiling"])
    low = estimate_range_risk(100, 90, 110, .02, (24, 72))
    high = estimate_range_risk(100, 90, 110, .04, (24, 72))
    assert high[24]["touch_floor"] > low[24]["touch_floor"]
    assert low[72]["touch_floor"] > low[24]["touch_floor"]
    expected_72 = erfc(abs(log(90/100)) / (.02*sqrt(3)*sqrt(2)))
    assert low[72]["touch_floor"] == pytest.approx(expected_72)
    assert all(0 <= row["stay_lower_bound"] <= 1 for row in low.values())


def test_range_risk_rows_carry_volatility_source_metadata():
    row = estimate_range_risk(100, 90, 110, .03, (24,),
        vol_source_effective="consenso", vol_source_requested="auto")[24]
    assert row["vol_source_effective"] == "consenso"
    assert row["vol_source_requested"] == "auto"
