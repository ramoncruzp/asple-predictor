"""Upper bounds for touching grid barriers under zero-drift Gaussian log returns.

Assumes normally distributed log returns, constant 24-hour sigma, zero drift, and
Brownian scaling with sqrt(time). The sum of single-barrier touch probabilities
is only an upper bound on leaving either side; fat tails can make real risk higher.
"""
from math import log, sqrt, erfc

HORIZONS_HOURS = (24, 72, 168)


def _phi(x: float) -> float:
    return 0.5 * erfc(-x / sqrt(2.0))


def estimate_range_risk(price: float, floor: float, ceiling: float, sigma_24h: float,
                        horizons_hours=HORIZONS_HOURS) -> dict:
    """Estimate floor/ceiling touch and range exit bounds for each horizon."""
    s, f, c, sigma = map(float, (price, floor, ceiling, sigma_24h))
    if not (0 < f < s < c) or sigma < 0:
        raise ValueError("require 0 < floor < price < ceiling and sigma >= 0")
    result = {}
    for hours in horizons_hours:
        t_days = float(hours) / 24.0
        scale = sigma * sqrt(t_days)
        p_floor = 0.0 if scale == 0 else 2.0 * _phi(-abs(log(f / s)) / scale)
        p_ceiling = 0.0 if scale == 0 else 2.0 * _phi(-abs(log(c / s)) / scale)
        p_exit = min(1.0, p_floor + p_ceiling)
        result[int(hours)] = {"touch_floor": p_floor, "touch_ceiling": p_ceiling,
                              "exit_upper_bound": p_exit,
                              "stay_lower_bound": max(0.0, 1.0 - p_exit)}
    return result
