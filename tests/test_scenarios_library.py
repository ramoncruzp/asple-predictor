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
    assert np.isfinite(mean)
    assert dates[0] >= _candles().timestamp[0] + 30 * 86400
    assert np.all(values[:, :, 1] >= values[:, :, 0] - 1e-4)
    assert np.all(values[:, :, 2] <= values[:, :, 0] + 1e-4)


def test_load_library_before_excludes_boundary_and_future(tmp_path):
    dates = np.asarray([100, 200, 300], dtype=np.int64)
    values = np.zeros((3, 288, 3), dtype=np.float32)
    daily_sum = values[:, :, 0].sum(axis=1, dtype=np.float64)
    np.savez_compressed(tmp_path / "XRPUSDT.npz", values=values, dates=dates,
        daily_z_return_sum=daily_sum, mean_z_return=np.float32(0))
    loaded, selected = load_library("XRPUSDT", before=250, directory=tmp_path)
    assert selected.tolist() == [100, 200]
    assert loaded.shape == (2, 288, 3)


def test_before_mean_and_library_ignore_future_days(tmp_path):
    dates = np.asarray([100, 200, 300], dtype=np.int64)
    before_values = np.zeros((3, 288, 3), dtype=np.float32)
    changed_values = before_values.copy()
    changed_values[2, :, :] = 50
    for name, values in (("base", before_values), ("future", changed_values)):
        sums = values[:, :, 0].sum(axis=1, dtype=np.float64)
        np.savez_compressed(tmp_path / f"{name}_XRPUSDT.npz", values=values,
            dates=dates, daily_z_return_sum=sums, mean_z_return=np.float32(0))
        directory = tmp_path / name
        directory.mkdir()
        (directory / "XRPUSDT.npz").write_bytes((tmp_path / f"{name}_XRPUSDT.npz").read_bytes())
    before_a, dates_a = load_library("XRPUSDT", before=250, directory=tmp_path / "base")
    before_b, dates_b = load_library("XRPUSDT", before=250, directory=tmp_path / "future")
    np.testing.assert_array_equal(dates_a, dates_b)
    np.testing.assert_array_equal(before_a, before_b)
    assert float(before_a[:, :, 0].mean()) == 0


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


def test_generator_uses_float64_for_pepe_tick_reconstruction(tmp_path):
    values = np.zeros((3, 288, 3), dtype=np.float32)
    values[:, :, 0] = np.tile(np.linspace(-1, 1, 288, dtype=np.float32), (3, 1))
    values[:, :, 1] = np.maximum(values[:, :, 0], .5)
    values[:, :, 2] = np.minimum(values[:, :, 0], -.5)
    dates = np.asarray([100 * 86400, 101 * 86400, 102 * 86400], dtype=np.int64)
    np.savez_compressed(tmp_path / "PEPEUSDT.npz", values=values, dates=dates,
        daily_z_return_sum=values[:, :, 0].sum(axis=1), mean_z_return=np.float32(.7))
    batch = next(generate(["PEPEUSDT"], horizon_days=1, n_scenarios=1, seed=43,
        price_now=1e-5, sigma_today_24h=.02, sigma_long_24h=.02,
        library_directory=tmp_path, batch_size=1))
    prices = batch["symbols"]["PEPEUSDT"]["close"][0]
    source_day = int(batch["symbols"]["PEPEUSDT"]["source_dates"][0, 0])
    source_index = int(np.flatnonzero(dates == source_day)[0])
    expected = 1e-5 * np.exp(np.cumsum(values[source_index, :, 0].astype(np.float64)
        * .02 / np.sqrt(288)))
    np.testing.assert_allclose(prices, expected, rtol=1e-13, atol=1e-19)
    assert prices.dtype == np.float64
    assert round(prices[-1] / 1e-8) == round(expected[-1] / 1e-8)
