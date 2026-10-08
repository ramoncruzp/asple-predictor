from datetime import datetime, timezone

import numpy as np
import pytest

from grid.sim.data import CandleDataError, load_candles, ewma_sigma_24h


def _csv(path, stamps):
    with path.open("w", encoding="utf-8") as f:
        f.write("timestamp,open,high,low,close\n")
        for i, stamp in enumerate(stamps):
            f.write(f"{stamp},100,101,99,{100+i/10}\n")


def test_sigma_is_causal():
    closes = np.array([100., 101., 99., 102., 103.])
    baseline = ewma_sigma_24h(closes)
    changed = closes.copy()
    changed[-1] = 1_000
    assert np.array_equal(baseline[:-1], ewma_sigma_24h(changed)[:-1])


def test_sigma_scales_known_volatility_for_hourly_and_five_minute_bars():
    rng = np.random.default_rng(206)
    hourly = 100 * np.exp(np.cumsum(rng.normal(0, .01 / np.sqrt(24), 12000)))
    five_minute = 100 * np.exp(np.cumsum(rng.normal(0, .01 / np.sqrt(288), 12000)))
    hourly_sigma = ewma_sigma_24h(hourly, bars_per_hour=1)
    five_minute_sigma = ewma_sigma_24h(five_minute, bars_per_hour=12)
    assert np.median(hourly_sigma[3000:]) == pytest.approx(.01, abs=.002)
    assert np.median(five_minute_sigma[3000:]) == pytest.approx(.01, abs=.002)


def test_five_minute_default_sigma_is_bit_identical():
    closes = np.array([100., 101., 99., 102., 103.])
    returns = np.diff(np.log(closes), prepend=np.log(closes[0]))
    alpha = 1.0 - np.exp(np.log(.5) / (72.0 * 12.0))
    variance = np.zeros(len(closes), dtype=float)
    for i in range(1, len(closes)):
        variance[i] = (1 - alpha) * variance[i - 1] + alpha * returns[i] ** 2
    previous = np.sqrt(variance * 288.0)
    assert np.array_equal(ewma_sigma_24h(closes), previous)


def test_load_reports_small_gap(tmp_path):
    origin = 1_700_000_100
    stamps = [datetime.fromtimestamp(origin + 300 * i, timezone.utc).isoformat() for i in (0, 1, 3, 4)]
    path = tmp_path / "candles.csv"
    _csv(path, stamps)
    assert load_candles(path, allow_gaps=True).gaps == 1


def test_rejects_window_with_more_than_half_percent_gaps(tmp_path):
    origin = 1_700_000_100
    stamps = [datetime.fromtimestamp(origin + 300 * i, timezone.utc).isoformat()
              for i in range(12) if i != 5]
    path = tmp_path / "candles.csv"
    _csv(path, stamps)
    with pytest.raises(CandleDataError, match=">0.5%"):
        load_candles(path)


@pytest.mark.parametrize("offset", [[0, 1, 1], [0, 2, 1]])
def test_rejects_duplicate_or_unsorted(tmp_path, offset):
    origin = 1_700_000_100
    path = tmp_path / "candles.csv"
    _csv(path, [datetime.fromtimestamp(origin + 300 * i, timezone.utc).isoformat() for i in offset])
    with pytest.raises(CandleDataError):
        load_candles(path, allow_gaps=True)
