from types import SimpleNamespace

import numpy as np

from research.scorecard.metrics import calibration, qlike
from research.scorecard.diagnostics import efficiency_ratio, rupture_probabilities
from research.scorecard.walkforward import walk_forward


def candles(days=33, seed=12):
    rng = np.random.default_rng(seed)
    n = days * 288
    returns = rng.normal(0, 0.001, n)
    close = 100 * np.exp(np.cumsum(returns))
    return SimpleNamespace(timestamp=1_700_000_100 + np.arange(n) * 300,
        close=close, open=close, high=close * 1.001, low=close * 0.999)


def test_walkforward_forecast_is_causal_to_future_changes():
    original = candles()
    points, gaps = walk_forward(original, horizon_h=24, step_h=24)
    assert points and gaps == 0
    first = points[0]
    altered_close = original.close.copy()
    altered_close[np.asarray(first.target_indices)] *= 1.8
    altered = SimpleNamespace(**{**vars(original), "close": altered_close})
    changed_points, _ = walk_forward(altered, horizon_h=24, step_h=24)
    assert changed_points[0].timestamp == first.timestamp
    assert changed_points[0].forecast_simple == first.forecast_simple
    assert changed_points[0].forecast_ewma == first.forecast_ewma
    assert changed_points[0].realized_sigma != first.realized_sigma


def test_sigma_accumulated_units_and_calibration_factor():
    actual = np.array([0.01, 0.02, 0.03])
    assert qlike(actual, actual).mean() == 0
    result = calibration(actual / 2, actual)
    assert abs(result["factor_calibracion"] - 2) < 1e-9


def test_break_probability_declines_when_band_widens_and_efficiency_bounds():
    probabilities = rupture_probabilities(100, 90, 110, 0.03, 24)
    assert probabilities[1.0] > probabilities[3.0]
    assert 0 <= efficiency_ratio([100, 99, 101, 100, 102], 4) <= 1


