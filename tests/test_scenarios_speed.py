import numpy as np
from decimal import Decimal

from data.exchange_filters import SymbolFilters
from data.market_store import CandleStore
from scripts.escenarios_velocidad import benchmark, _volatility_estimates


def test_speed_benchmark_runs_both_horizons_with_synthetic_library(tmp_path):
    library = tmp_path / "library"
    library.mkdir()
    rng = np.random.default_rng(43)
    returns = rng.standard_t(5, (50, 288)).astype(np.float32)
    values = np.empty((50, 288, 3), np.float32)
    values[:, :, 0] = returns
    values[:, :, 1] = np.maximum(returns, 0) + .2
    values[:, :, 2] = np.minimum(returns, 0) - .2
    dates = np.arange(1_700_000_000 // 86400, 1_700_000_000 // 86400 + 50,
        dtype=np.int64) * 86400
    np.savez_compressed(library / "SYNTHUSDT.npz", values=values, dates=dates,
        daily_z_return_sum=returns.sum(axis=1), mean_z_return=np.float32(0))
    result = benchmark(library_directory=library, symbol="SYNTHUSDT", price=100,
        sigma_today=.005, sigma_long=.005, n_scenarios=1, batch_size=1,
        filters=SymbolFilters(Decimal("0.0001"), Decimal("0.0001"), Decimal("100000"),
            Decimal("0.0001"), Decimal("0.0001"), Decimal("100000000"),
            Decimal("0.01"), True, 200))
    assert [row["days"] for row in result] == [20, 45]
    assert all(row["scenarios"] == 1 and row["seconds"] >= 0 for row in result)


def test_daily_volatility_estimates_align_returns_to_synthetic_store_days(tmp_path):
    store = CandleStore(tmp_path / "market_data_synthetic.sqlite")
    count = 32 * 288 + 1
    close = 100 * np.exp(np.cumsum(np.random.default_rng(44).normal(0, .0002, count)))
    timestamps = 1_704_067_200 + np.arange(count, dtype=np.int64) * 300
    previous = np.r_[close[0], close[:-1]]
    rows = [{"open_ts": int(ts), "open": float(open_),
        "high": float(max(open_, end) * 1.0001), "low": float(min(open_, end) * .9999),
        "close": float(end), "volume": 1, "quote_volume": 100, "trades": 1}
        for ts, open_, end in zip(timestamps, previous, close)]
    store.upsert("SYNTHUSDT", "5m", rows, "test")

    sigma_today, sigma_long = _volatility_estimates(store.load("SYNTHUSDT", "5m"))

    assert np.isfinite(sigma_today) and sigma_today > 0
    assert np.isfinite(sigma_long) and sigma_long > 0
