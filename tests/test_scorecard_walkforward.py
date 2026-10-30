from types import SimpleNamespace

import numpy as np

from research.scorecard.metrics import (calibration, calibration_factor_ci,
    causal_interval_coverage, qlike)
from research.scorecard.diagnostics import efficiency_ratio, rupture_probabilities
from research.scorecard.runner import init_scorecard_tables, run_scorecard, _load_live_forecasts
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
    low, high = calibration_factor_ci(actual / 2, actual, replicas=200)
    assert low <= 2 <= high


def test_coverage_uses_only_prior_points_and_minimum_30():
    pred = np.ones(32)
    real = np.ones(32)
    real[-1] = 10
    coverage, n = causal_interval_coverage(pred, real, minimum_history=30)
    assert n == 2
    assert coverage == 0.5


def test_scorecard_verdict_schema_migrates_symbol_nullable(tmp_path):
    import sqlite3

    path = tmp_path / "market_data.db"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE scorecard_verdicts(run_id INTEGER,target TEXT,horizon_h INTEGER,"
            "model TEXT,verdict TEXT,reason TEXT)")
    init_scorecard_tables(path)
    with sqlite3.connect(path) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(scorecard_verdicts)")}
    assert "symbol" in columns


def test_runner_persists_symbol_and_aggregate_verdicts_with_factor_ci(tmp_path):
    import sqlite3
    from data.market_store import CandleStore

    path = tmp_path / "market_data.db"
    store = CandleStore(path)
    n = 180 * 288
    rng = np.random.default_rng(8)
    close = 100 * np.exp(np.cumsum(rng.normal(0, .001, n)))
    rows = [{"open_ts": 1_700_000_100 + i * 300, "open": float(close[i]),
        "high": float(close[i] * 1.001), "low": float(close[i] * .999),
        "close": float(close[i])} for i in range(n)]
    store.upsert("SYNUSDT", "5m", rows, "synthetic", role="datos")
    run_scorecard(path, symbols=["SYNUSDT"], horizons=(24,), dry_run=False)
    with sqlite3.connect(path) as conn:
        verdicts = conn.execute("SELECT symbol,model,verdict FROM scorecard_verdicts").fetchall()
        factors = conn.execute("SELECT ci_low,ci_high FROM calibration_factors").fetchall()
        coverage = conn.execute("SELECT n,value FROM forecast_scorecard WHERE metric='cobertura_80'").fetchall()
    assert any(row[0] == "SYNUSDT" for row in verdicts)
    assert any(row[0] is None for row in verdicts)
    assert factors and all(row[0] is not None and row[1] is not None for row in factors)
    assert coverage and all(row[0] > 0 for row in coverage)


def test_live_forecast_is_readonly_and_scaled_from_hourly_sigma(tmp_path):
    import sqlite3
    from datetime import datetime, timezone

    path = tmp_path / "registry.sqlite"
    forecast_at = datetime.fromtimestamp(1_700_000_000, timezone.utc).isoformat()
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE vol_forecasts(symbol TEXT,horizon_h INTEGER,model_name TEXT,"
            "forecast_at TEXT,pred_logvol_cal REAL,realized_logvol REAL,verified_at TEXT)")
        conn.execute("INSERT INTO vol_forecasts VALUES(?,?,?,?,?,?,?)",
            ("SYNUSDT", 24, "model_a", forecast_at, .02, .03, forecast_at))
    rows = _load_live_forecasts(path, "SYNUSDT", 24)
    assert rows[0][1] == "vivo_model_a"
    assert abs(rows[0][2] - .02 * np.sqrt(24)) < 1e-12


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


