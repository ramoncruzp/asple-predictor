from decimal import Decimal

import numpy as np
import pytest

from data.exchange_filters import SymbolFilters
from grid.sim.data import CandleData
from grid.sim.meta_bot import run_meta_bot
from grid.sim.meta_portfolio import evaluate_portfolio, pack_curve
from grid.sim.runner import run_simulation
from research.scenarios.fastsim import simulate_bot, simulate_simple


def _scenario(close):
    close = np.asarray(close, dtype=np.float64)
    opn = np.r_[close[0], close[:-1]]
    high = np.maximum(opn, close) * 1.002
    low = np.minimum(opn, close) * .998
    return {"open": opn[None, :], "high": high[None, :],
        "low": low[None, :], "close": close[None, :]}


def _candles(scenario):
    return CandleData(np.arange(scenario["close"].shape[1], dtype=np.int64) * 300,
        *(scenario[key][0] for key in ("open", "high", "low", "close")), 0)


@pytest.mark.parametrize("case", range(40))
def test_simple_batch_matches_production_replay(case):
    rng = np.random.default_rng(case + 43)
    noise = rng.normal(0, .018, 60)
    if case % 5 == 0:
        noise[15] += .15
    elif case % 5 == 1:
        noise[20:30] -= .035
    elif case % 5 == 2:
        noise[25:] += .02
    elif case % 5 == 3:
        noise[10:40] *= -1
    else:
        noise[::2] *= -1
    close = 100 * np.exp(np.cumsum(np.r_[0., noise]))
    scenario = _scenario(close)
    n_levels = (4, 6, 10)[case % 3]
    max_loss = 20. if case % 2 else None
    trace = []
    reference = run_simulation(_candles(scenario), strategy="simple", n=n_levels,
        capital=1000, low=80, high=120, params={}, fee_pct=.1,
        resync_candles=3, meta_max_loss_pct=max_loss, meta_portfolio_piece=True,
        include_details=True,
        trace_callback=trace.append)
    fast = simulate_simple(scenario, n_levels=n_levels, capital=1000,
        low=80, high=120, max_loss_pct=max_loss, target_usdt=20, initial_timestamp=0)
    events = reference["events"]
    assert int(fast["buys"][0]) == sum(event["type"] == "BUY_FILLED" for event in events)
    assert int(fast["sells"][0]) == sum(event["type"] == "SELL_FILLED" for event in events)
    assert int(fast["cycles"][0]) == reference["metrics"]["cycles_completed"]
    assert bool(fast["loss_cut"][0]) == reference["metrics"]["loss_cut_reached"]
    cut_event = next((event for event in reference["events"] if event["type"] == "META_LOSS_CUT"), None)
    assert int(fast["loss_cut_candle"][0]) == (-1 if cut_event is None else int(cut_event["ts"]) // 300)
    assert abs(float(fast["pnl_usdt"][0]) - reference["metrics"]["equity_final_after_repository_liquidation"] + 1000) <= .02
    assert abs(float(fast["fees_usdt"][0]) - float(reference["details"]["fees_usdt"])) <= .02
    curve = pack_curve([row["timestamp"] for row in trace],
        [float(row["balances"]["USDT"]) for row in trace],
        [float(row["equity_incl_dust"]) for row in trace], 1000)
    portfolio = evaluate_portfolio([curve], capital_initial=1000, target_usdt=20)
    hit = int(fast["target_candle"][0])
    cut_index = int(fast["loss_cut_candle"][0])
    expected_hit = next((int(row["candle"]) for row in trace
        if float(row["balances"]["USDT"]) >= 1020), -1)
    assert hit == expected_hit
    assert portfolio["reached"] == (hit >= 0 and (cut_index < 0 or hit < cut_index))
    np.testing.assert_allclose(fast["cash_curve"][0], [float(row["balances"]["USDT"]) for row in trace],
        rtol=0, atol=.02)
    np.testing.assert_allclose(fast["equity_curve"][0], [float(row["equity_incl_dust"]) for row in trace],
        rtol=0, atol=.02)


@pytest.mark.parametrize("margin", [.3, .5, .7])
def test_individual_bot_matches_reference_semantics(margin):
    close = np.r_[np.full(8, 100.), np.linspace(100, 96, 8),
        np.linspace(96, 105, 20), np.full(8, 105.)]
    scenario = _scenario(close)
    trace = []
    reference = run_meta_bot(_candles(scenario), capital=1000, buy_price=98,
        sell_price=102, target_usdt=None, fee_pct=.1, trace_callback=trace.append,
        min_margin_pct=margin)
    fast = simulate_bot(scenario, capital=1000, buy_price=98, sell_price=102,
        min_margin_pct=margin, initial_timestamp=0)
    assert bool(fast["buys"][0]) == reference["bought"]
    assert bool(fast["sells"][0]) == reference["sold"]
    assert bool(fast["loss_cut"][0]) == reference["loss_cut"]
    assert abs(float(fast["pnl_usdt"][0]) - reference["pnl_usdt"]) <= .02
    sample = [row for i, row in enumerate(trace)
        if i == 0 or int(row["timestamp"]) % 900 == 0 or i == len(trace) - 1]
    np.testing.assert_allclose(fast["cash_curve"][0], [row["cash"] for row in sample],
        rtol=0, atol=.02)


def test_pepe_geometry_uses_tick_filters():
    filters = SymbolFilters(Decimal("0.00000001"), Decimal("0.00000001"), Decimal("1"),
        Decimal("1"), Decimal("1"), Decimal("100000000000"), Decimal("0.0001"),
        True, 200)
    close = np.r_[np.full(5, .0001), np.linspace(.0001, .000087, 5),
        np.linspace(.000087, .000112, 10)]
    scenario = _scenario(close)
    trace = []
    reference = run_simulation(_candles(scenario), strategy="simple", n=4,
        capital=1, low=.00008, high=.00012, filters=filters, params={}, fee_pct=.1,
        resync_candles=3, meta_portfolio_piece=True, trace_callback=trace.append)
    result = simulate_simple(scenario, n_levels=4, capital=1,
        low=.00008, high=.00012, filters=filters, initial_timestamp=0)
    assert result["final_equity"].dtype == np.float64
    assert result["buys"][0] == sum(event["type"] == "BUY_FILLED" for event in reference["events"])
    assert result["sells"][0] == sum(event["type"] == "SELL_FILLED" for event in reference["events"])
    assert abs(float(result["pnl_usdt"][0]) - reference["metrics"]["pnl_total_net_usdt"]) <= .02
