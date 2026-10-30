from types import SimpleNamespace

import numpy as np

from research.scorecard.metrics import calibration, qlike
from research.scorecard.diagnostics import efficiency_ratio, rupture_probabilities
from research.scorecard.runner import run_scorecard
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


def legacy_walk_forward(candles, horizon_h, step_h, warmup_days=30, ewma_lambda_hourly=.97):
    stamps, close = candles.timestamp, candles.close
    horizon, warmup = horizon_h * 12, warmup_days * 288
    nominal = int(np.median(np.diff(stamps)))
    by_ts = {int(ts): i for i, ts in enumerate(stamps)}
    t, end = int(stamps[0] + warmup * nominal), int(stamps[-1] + nominal)
    out, coefficient, last_fit = [], None, None
    while t + horizon_h * 3600 <= end:
        history_idx = np.flatnonzero(stamps < t)
        target_ts = np.arange(t, t + horizon_h * 3600, 300, dtype=np.int64)
        if target_ts[-1] > stamps[-1]:
            break
        target_idx = tuple(by_ts.get(int(ts), -1) for ts in target_ts)
        if len(history_idx) < warmup or any(i < 0 for i in target_idx):
            t += step_h * 3600
            continue
        ret = np.diff(np.log(close[history_idx]))
        recent = ret[-horizon:]
        lam = ewma_lambda_hourly ** (1 / 12)
        weights = lam ** np.arange(len(ret) - 1, -1, -1)
        weights /= weights.sum()
        ewma = np.sqrt(np.sum(weights * ret ** 2) * horizon)
        usable = len(ret) // 12 * 12
        rv = np.sum(ret[:usable].reshape(-1, 12) ** 2, axis=1)
        hour = int(t // 3600)
        if last_fit is None or hour - last_fit >= 168:
            design, response = [], []
            for j in range(720, len(rv) - 1):
                design.append([1., rv[j-23:j+1].sum(), rv[j-167:j+1].sum(), rv[j-719:j+1].sum()])
                response.append(rv[j+1])
            if len(response) >= 24:
                coefficient = np.linalg.lstsq(np.asarray(design), np.asarray(response), rcond=None)[0]
            last_fit = hour
        har = ewma
        if coefficient is not None and len(rv) >= 720:
            components = np.asarray([1., rv[-24:].sum(), rv[-168:].sum(), rv[-720:].sum()])
            har = np.sqrt(max(0., components @ coefficient) * horizon_h)
        target_close = close[np.asarray(target_idx)]
        future = np.diff(np.log(np.concatenate(([close[history_idx[-1]]], target_close))))
        out.append((t, target_idx, np.sqrt(np.sum(future**2)), np.sqrt(np.sum(recent**2)), ewma, har))
        t += step_h * 3600
    return out


def test_optimized_walkforward_matches_legacy_on_120_days():
    data = candles(days=120, seed=99)
    for horizon in (24, 72):
        actual, gaps = walk_forward(data, horizon_h=horizon, step_h=horizon)
        expected = legacy_walk_forward(data, horizon, horizon)
        assert gaps == 0 and len(actual) == len(expected)
        for point, old in zip(actual, expected):
            assert point.timestamp == old[0] and point.target_indices == old[1]
            np.testing.assert_allclose([point.realized_sigma, point.forecast_simple,
                point.forecast_ewma, point.forecast_har], old[2:], rtol=1e-9, atol=1e-12)


def test_sigma_accumulated_units_and_calibration_factor():
    actual = np.array([0.01, 0.02, 0.03])
    assert qlike(actual, actual).mean() == 0
    result = calibration(actual / 2, actual)
    assert abs(result["factor_calibracion"] - 2) < 1e-9


def test_break_probability_declines_when_band_widens_and_efficiency_bounds():
    probabilities = rupture_probabilities(100, 90, 110, 0.03, 24)
    assert probabilities[1.0] > probabilities[3.0]
    assert 0 <= efficiency_ratio([100, 99, 101, 100, 102], 4) <= 1


def test_dry_run_reads_synthetic_market_database_without_writing(tmp_path):
    import sqlite3
    from data.market_store import CandleStore

    path = tmp_path / "market_data.db"
    store = CandleStore(path)
    store.register_symbol("SYNUSDT", role="datos")
    store.upsert("SYNUSDT", "5m", [
        {"open_ts": 1_700_000_100 + i * 300, "open": 100, "high": 101,
         "low": 99, "close": 100 + i * .001} for i in range(100)], "synthetic")
    before = path.read_bytes()
    result = run_scorecard(path, symbols=["SYNUSDT"], horizons=(24,), dry_run=True)
    assert before == path.read_bytes()
    assert result["summary"]["insufficient"] == ["SYNUSDT"]
    with sqlite3.connect(path) as conn:
        names = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "scorecard_runs" not in names


