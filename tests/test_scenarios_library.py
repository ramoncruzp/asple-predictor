import numpy as np

from grid.sim.data import CandleData
from research.scenarios.library import load_library, standardized_library
from research.scenarios.generator import generate


def _candles(days=34):
    count = days * 288
    rng = np.random.default_rng(43)
    returns = rng.standard_t(4, count) * .0005
    close = 100 * np.exp(np.cumsum(returns))
    previous = np.r_[close[0], close[:-1]]
    timestamps = 1_700_006_400 + np.arange(count) * 300
    return CandleData(timestamps, previous, np.maximum(previous, close) * 1.0001,
        np.minimum(previous, close) * .9999, close, 0)


def test_standardized_library_centers_and_preserves_ohlc_order():
    values, dates, mean = standardized_library(_candles())
    assert len(dates) >= 2
    assert values.dtype == np.float32
    assert abs(float(values[:, :, 0].mean())) < 1e-6
    assert np.isfinite(mean)
    assert np.all(values[:, :, 1] >= values[:, :, 0] - 1e-4)
    assert np.all(values[:, :, 2] <= values[:, :, 0] + 1e-4)


def test_load_library_before_excludes_boundary_and_future(tmp_path):
    dates = np.asarray([100, 200, 300], dtype=np.int64)
    values = np.zeros((3, 288, 3), dtype=np.float32)
    np.savez_compressed(tmp_path / "XRPUSDT.npz", values=values, dates=dates,
        mean_z_return=np.float32(0))
    loaded, selected = load_library("XRPUSDT", before=250, directory=tmp_path)
    assert selected.tolist() == [100, 200]
    assert loaded.shape == (2, 288, 3)


def test_generator_is_reproducible_batched_and_ohlc_ordered(tmp_path):
    values, dates, mean = standardized_library(_candles())
    for symbol in ("AUSDT", "BUSDT"):
        np.savez_compressed(tmp_path / f"{symbol}.npz", values=values,
            dates=dates, mean_z_return=np.float32(mean))
    args = dict(symbols=["AUSDT", "BUSDT"], horizon_days=2, n_scenarios=3,
        seed=43, price_now={"AUSDT": 1., "BUSDT": 2.}, sigma_today_24h=.02,
        sigma_long_24h=.02, library_directory=tmp_path, batch_size=2)
    first = list(generate(**args))
    second = list(generate(**args))
    assert [b["scenario_start"] for b in first] == [0, 2]
    for batch_a, batch_b in zip(first, second):
        a = batch_a["symbols"]["AUSDT"]
        b = batch_b["symbols"]["AUSDT"]
        np.testing.assert_array_equal(a["close"], b["close"])
        assert np.all(a["low"] <= np.minimum(a["open"], a["close"]))
        assert np.all(a["high"] >= np.maximum(a["open"], a["close"]))
        assert a["close"].shape[1] == 576
