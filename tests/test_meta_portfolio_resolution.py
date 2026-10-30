import json

import numpy as np
import pytest

from grid.meta_learning import REPLAY_METHOD_VERSION
from grid.meta_operator import MetaOperator
from grid.sim.data import CandleData
from grid.sim.meta_bot import run_meta_bot
from grid.sim.meta_portfolio import (evaluate_portfolio, pack_curve,
    simulate_piece_curve, unpack_curve)
from grid.sim.runner import run_simulation


def _candles(days=20):
    count = days * 288 + 1
    timestamp = 1_727_740_800 + np.arange(count, dtype=np.int64) * 300
    close = 100 + 1.5 * np.sin(np.arange(count, dtype=np.float64) / 37)
    open_ = np.r_[close[0], close[:-1]]
    high = np.maximum(open_, close) + .2
    low = np.minimum(open_, close) - .2
    return CandleData(timestamp, open_, high, low, close, 0)


def _curve_as_of_6d565d9(candles, *, strategy, n, capital, low, high, min_margin_pct=.3):
    """Copia de referencia de simulate_piece_curve en 6d565d9."""
    timestamps, cash, equity = [], [], []
    count = 0

    def trace(row):
        nonlocal count
        include = (strategy != "individual_bot" or int(row["timestamp"]) % 900 == 0
            or count == 0 or row["timestamp"] == int(candles.timestamp[-1]))
        count += 1
        if include:
            timestamps.append(int(row["timestamp"]))
            cash.append(float(row.get("cash", (row.get("balances") or {}).get("USDT", 0))))
            equity.append(float(row.get("equity_incl_dust", row["equity"])))

    if strategy == "individual_bot":
        replay = run_meta_bot(candles, capital=capital, buy_price=low, sell_price=high,
            target_usdt=None, fee_pct=.1, max_loss_pct=None, filters=None,
            min_margin_pct=min_margin_pct, trace_callback=trace)
        extra = {"bought": replay["bought"], "sold": replay["sold"]}
    else:
        run_simulation(candles, strategy=strategy, n=n, capital=capital, low=low, high=high,
            width_pct=None, params={}, fee_pct=.1, filters=None,
            close_wait_enabled=True, adjust_enabled_global=False, stoploss_enabled=False,
            meta_max_loss_pct=None, meta_portfolio_piece=True, trace_callback=trace,
            resync_candles=3)
        extra = {}
    return {"curve": pack_curve(timestamps, cash, equity, capital), **extra}


@pytest.mark.parametrize("strategy,low,high,n", [
    ("simple", 80, 120, 6), ("smart", 80, 120, 6),
    ("individual_bot", 99, 101, 1),
])
def test_curve_bytes_match_6d565d9_for_all_piece_strategies(strategy, low, high, n):
    candles = _candles()
    expected = _curve_as_of_6d565d9(candles, strategy=strategy, n=n,
        capital=1000 if strategy != "individual_bot" else 20, low=low, high=high)
    actual = simulate_piece_curve(candles, strategy=strategy, n=n,
        capital=1000 if strategy != "individual_bot" else 20, low=low, high=high)
    assert json.dumps(actual, sort_keys=True, separators=(",", ":")).encode() == \
        json.dumps(expected, sort_keys=True, separators=(",", ":")).encode()
    stamps, _, _ = unpack_curve(actual["curve"])
    assert len(stamps) == expected["curve"]["count"]


def test_meta_operator_replay_window_matches_6d565d9_outcome():
    candles = _candles()
    capital, target, maximum_loss = 1000, 3, 18
    reference_piece = _curve_as_of_6d565d9(candles, strategy="simple", n=6,
        capital=capital, low=80, high=120)
    reference_outcome = evaluate_portfolio([reference_piece["curve"],],
        capital_initial=capital, target_usdt=target, max_loss_pct=maximum_loss)
    expected = {"window_start": int(candles.timestamp[0]), **reference_piece, **reference_outcome}
    operator = MetaOperator.__new__(MetaOperator)
    actual = operator._replay_window(candles, int(candles.timestamp[0]), "simple", 6,
        capital, target, 80, 120, {"min_margin_pct": .3, "max_loss_pct": maximum_loss,
            "_meta_capital_initial": capital})
    assert actual == expected
    assert REPLAY_METHOD_VERSION == "rolling_7d_v4_equity"


def test_explicit_research_sampling_is_fifteen_minutes_only_when_false():
    candles = _candles()
    result = simulate_piece_curve(candles, strategy="simple", n=6, capital=1000,
        low=80, high=120, curve_every_candle=False)
    stamps, _, _ = unpack_curve(result["curve"])
    assert np.all(np.diff(stamps) == 900)
