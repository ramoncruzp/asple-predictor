import numpy as np
import sqlite3
from datetime import datetime, timezone

from grid.sim.data import CandleData
from research.scenarios.evaluate import (causal_sigma_24h, evaluate_history,
    _remove_log_trend, _calibration_factors, _calibration_factor_timeline)


class _SyntheticStore:
    def __init__(self, _path):
        pass

    def load(self, _symbol, _interval):
        count = 65 * 288 + 1
        rng = np.random.default_rng(42)
        returns = rng.normal(0, .0008, count - 1)
        close = 100 * np.exp(np.r_[0, np.cumsum(returns)])
        open_ = np.r_[close[0], close[:-1]]
        high = np.maximum(open_, close) * 1.001
        low = np.minimum(open_, close) * .999
        ts = 1_700_000_100 + np.arange(count, dtype=np.int64) * 300
        return CandleData(ts, open_, high, low, close, 0)

    def filters(self, _symbol):
        return None


def test_history_evaluator_emits_causal_windows_and_both_variants(monkeypatch):
    monkeypatch.setattr("research.scenarios.evaluate.CandleStore", _SyntheticStore)
    result = evaluate_history([{"symbol": "XRPUSDT", "strategy": "simple",
        "capital": 1000, "n": 6, "width_sigma": 2}], perfil="RAPIDO", capital=1000,
        meta=3, db_path="unused.db", months=2, step_days=7, replicas=30)
    row = result["symbols"]["XRPUSDT"]
    assert row["ventanas_candidatas"] > 0
    metrics = row["pieces"][0]["variantes"]
    assert metrics["real"]["metrics"]["n"] == metrics["sin_tendencia"]["metrics"]["n"]
    assert metrics["real"]["metrics"]["n"] > 0
    assert len(metrics["real"]["metrics"]["pnl_mean_ci95"]) == 2


def test_detrending_keeps_first_close_and_removes_log_slope():
    close = (100 * np.exp(np.linspace(0, .4, 600) + .01 * np.sin(np.arange(600) / 5)))[None, :]
    open_ = np.concatenate((close[:, :1], close[:, :-1]), axis=1)
    bars = {"open": open_, "high": np.maximum(open_, close) * 1.001,
        "low": np.minimum(open_, close) * .999, "close": close}
    adjusted = _remove_log_trend(bars)
    assert adjusted["close"][0, 0] == close[0, 0]
    assert abs(np.polyfit(np.arange(600), np.log(adjusted["close"][0]), 1)[0]) < 1e-12


def test_causal_sigma_is_shared_with_the_historical_start():
    data = _SyntheticStore("unused").load("XRPUSDT", "5m")
    start = 40 * 288
    expected = causal_sigma_24h(data.close, data.timestamp, start, calibration=1.25)
    # La misma función compartida es el estimador usado para un prefijo before=t.
    actual = causal_sigma_24h(data.close[:start], data.timestamp[:start], None,
        calibration=1.25)
    assert abs(expected - actual) <= 1e-12


def test_history_bot_uses_offsets_from_start_price(monkeypatch):
    monkeypatch.setattr("research.scenarios.evaluate.CandleStore", _SyntheticStore)
    result = evaluate_history([{"symbol": "XRPUSDT", "strategy": "bot",
        "capital": 1000, "buy_offset_pct": -1.5, "sell_offset_pct": 1.5}],
        perfil="RAPIDO", capital=1000, meta=3, db_path="unused.db", months=2,
        step_days=7, replicas=10)
    geometry = result["symbols"]["XRPUSDT"]["pieces"][0]["geometrias"][0]
    expected_open = _SyntheticStore("unused").load("XRPUSDT", "5m").open
    expected_close = _SyntheticStore("unused").load("XRPUSDT", "5m").close
    ts = _SyntheticStore("unused").load("XRPUSDT", "5m").timestamp
    start = int(np.searchsorted(ts, geometry["timestamp"]))
    price = expected_close[start - 1]
    assert abs(geometry["buy_price"] - price * .985) < 1e-10
    assert abs(geometry["sell_price"] - price * 1.015) < 1e-10


def test_ou_grid_outperforms_equal_sigma_gbm_after_trend_removal():
    from research.scenarios.fastsim import simulate_simple
    rng = np.random.default_rng(20261030)
    n, bars = 24, 20 * 288
    sigma_bar = .04 / np.sqrt(288)
    gbm_returns = rng.normal(0, sigma_bar, (n, bars))
    ou_returns = np.empty((n, bars), dtype=np.float64)
    state = np.zeros(n, dtype=np.float64)
    for index in range(bars):
        previous = state.copy()
        state = state - .005 * state + rng.normal(0, sigma_bar, n)
        ou_returns[:, index] = state - previous
    outcomes = {}
    for name, returns in (("gbm", gbm_returns), ("ou", ou_returns)):
        close = 100 * np.exp(np.cumsum(returns, axis=1))
        open_ = np.concatenate((np.full((n, 1), 100.0), close[:, :-1]), axis=1)
        high = np.maximum(open_, close) * 1.001
        low = np.minimum(open_, close) * .999
        result = simulate_simple({"open": open_, "high": high, "low": low, "close": close},
            n_levels=10, capital=1000, low=100 * np.exp(-.04 * np.sqrt(20)),
            high=100 * np.exp(.04 * np.sqrt(20)), max_loss_pct=None)
        outcomes[name] = result
    assert outcomes["ou"]["pnl_usdt"].mean() > 0
    assert outcomes["ou"]["pnl_usdt"].mean() > outcomes["gbm"]["pnl_usdt"].mean()
    assert outcomes["gbm"]["pnl_usdt"].mean() < outcomes["gbm"]["fees_usdt"].mean() * 3


def test_history_uses_only_calibration_factors_finished_before_start(tmp_path):
    db = tmp_path / "scorecard.sqlite"
    with sqlite3.connect(db) as conn:
        conn.executescript("""
            CREATE TABLE scorecard_runs(id INTEGER PRIMARY KEY, finished_at TEXT, status TEXT);
            CREATE TABLE calibration_factors(run_id INTEGER, symbol TEXT, kind TEXT,
                horizon_h INTEGER, factor REAL, computed_at TEXT);
        """)
        conn.executemany("INSERT INTO scorecard_runs VALUES(?,?,?)", [
            (1, "2024-01-01T00:00:00+00:00", "completed"),
            (2, "2024-01-03T00:00:00+00:00", "completed")])
        conn.executemany("INSERT INTO calibration_factors VALUES(?,?,?,?,?,?)", [
            (1, "XRPUSDT", "sigma", 24, 1.25, "2024-01-01T00:01:00+00:00"),
            (2, "XRPUSDT", "sigma", 24, 2.0, "2024-01-03T00:01:00+00:00")])
    cutoff = int(datetime(2024, 1, 2, tzinfo=timezone.utc).timestamp())
    assert _calibration_factors(db, ["XRPUSDT"], before=cutoff) == {"XRPUSDT": 1.25}
    dates, factors = _calibration_factor_timeline(db, "XRPUSDT")
    assert factors.tolist() == [1.25, 2.0]
    assert factors[np.searchsorted(dates, cutoff, side="left") - 1] == 1.25


def test_smart_history_uses_exact_runner_for_both_variants(monkeypatch):
    calls = []
    from grid.sim.meta_portfolio import pack_curve
    def fake_smart(bars, *, n_smart, low, high, **_kwargs):
        calls.append((bars["close"].shape, n_smart, np.asarray(low).shape, np.asarray(high).shape))
        curve = pack_curve([0, 20 * 86400], [1000, 1000], [1000, 1000], 1000)
        return [{"curve": curve, "metrics": {"cycles_completed": 0},
            "inventory_qty": 0.0, "inventory_value": 0.0}]
    monkeypatch.setattr("research.scenarios.evaluate.CandleStore", _SyntheticStore)
    monkeypatch.setattr("research.scenarios.evaluate.evaluate_smart_scenarios", fake_smart)
    result = evaluate_history([{"symbol": "XRPUSDT", "strategy": "smart",
        "capital": 1000, "n": 6, "width_sigma": 2}], perfil="RAPIDO", capital=1000,
        meta=3, db_path="unused.db", months=2, step_days=7, replicas=5, n_smart=1)
    assert len(calls) == 2
    assert all(shape == (1, 20 * 288) and n_smart == 1 for shape, n_smart, _, _ in calls)
    assert result["symbols"]["XRPUSDT"]["pieces"][0]["n"] == 1
