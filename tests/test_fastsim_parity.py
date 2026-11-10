from decimal import Decimal

import numpy as np
import pytest

from data.exchange_filters import SymbolFilters
from grid.sim.data import CandleData
from grid.sim.meta_bot import run_meta_bot
from grid.sim.meta_portfolio import evaluate_portfolio, pack_curve
from grid.sim.runner import FILTERS, run_simulation
from grid.levels import compute_lines, plan_cells
from research.scenarios.fastsim import simulate_bot, simulate_simple


def _scenario(close):
    close = np.asarray(close, dtype=np.float64)
    opn = np.r_[close[0], close[:-1]]
    high = np.maximum(opn, close) * 1.002
    low = np.minimum(opn, close) * .998
    return {"open": opn[None, :], "high": high[None, :],
        "low": low[None, :], "close": close[None, :]}


def _candles(scenario, start_ts=0):
    return CandleData(start_ts + np.arange(scenario["close"].shape[1], dtype=np.int64) * 300,
        *(scenario[key][0] for key in ("open", "high", "low", "close")), 0)


def test_initial_cells_equivalentes_a_plan_cells_reproducen_el_replay():
    scenario = _scenario([100, 99, 98, 101, 103, 100, 97, 102, 104, 101])
    filters = FILTERS
    plans = plan_cells(compute_lines("90", "110", 4, filters), "1000",
        {"bid_price": "99.999999", "ask_price": "100.000001", "avg_price": "100"},
        filters, {"grid_min_step_pct": .003, "capital_max_por_nivel_pct": .30})
    initial = {"cash_usdt": 1000, "cells": [{
        "buy_price": float(plan.buy_price), "sell_price": float(plan.sell_price),
        "qty": float(plan.qty), "held_qty": 0,
        "state": plan.initial_state, "active_from": 1, "reserved_cash": 0,
    } for plan in plans]}
    usual = simulate_simple(scenario, n_levels=4, capital=1000, low=90, high=110,
        filters=filters, max_loss_pct=18, target_usdt=10)
    from_state = simulate_simple(scenario, n_levels=4, capital=1000, low=90, high=110,
        filters=filters, max_loss_pct=18, target_usdt=10, initial_cells=initial)
    for key in ("final_equity", "pnl_usdt", "buys", "sells", "fees_usdt",
            "inventory_qty", "loss_cut", "loss_cut_candle", "target_candle",
            "cash_curve", "equity_curve"):
        np.testing.assert_allclose(from_state[key], usual[key], rtol=0, atol=1e-12,
            err_msg=key)


def test_initial_cells_fuera_de_rango_conserva_ventas_y_no_abre_compras():
    scenario = _scenario([.24] * 10)
    scenario["high"][:] = .27
    scenario["low"][:] = .23
    cells = {"cash_usdt": 20, "wait_outside_range": True, "cells": [
        {"buy_price": .254, "sell_price": .2646, "qty": 1, "held_qty": 1,
            "state": "SELL_OPEN", "active_from": 0},
        {"buy_price": .27, "sell_price": .28, "qty": 1, "held_qty": 0,
            "state": "IDLE"}]}
    filters = FILTERS
    result = simulate_simple(scenario, n_levels=2, capital=20, low=.254, high=.318,
        filters=filters, initial_cells=cells)
    assert result["sells"].tolist() == [1]
    assert result["buys"].tolist() == [0]
    assert result["inventory_qty"].tolist() == [0]


def test_initial_cells_buy_open_incluye_reserva_en_efectivo_sin_doble_conteo():
    scenario = _scenario([100] * 8)
    filters = FILTERS
    reserved = 21.021
    result = simulate_simple(scenario, n_levels=6, capital=reserved,
        low=90, high=110, filters=filters, initial_cells={"cash_usdt": 0,
            "cells": [{"buy_price": 90, "sell_price": 95, "qty": .1,
                "held_qty": 0, "state": "BUY_OPEN", "active_from": 99,
                "reserved_cash": reserved}]})

    np.testing.assert_allclose(result["cash_curve"], reserved, rtol=0, atol=1e-12)
    np.testing.assert_allclose(result["equity_curve"], reserved, rtol=0, atol=1e-12)


@pytest.mark.parametrize("strategy", ["simple", "bot"])
def test_lote_geometrias_coincide_con_candidatos_individuales(strategy):
    rng = np.random.default_rng(4308)
    closes = np.vstack([100 * np.exp(np.cumsum(np.r_[0.,
        rng.normal(0, .012, 59)])) for _ in range(5)])
    open_prices = np.column_stack([closes[:, 0], closes[:, :-1]])
    scenarios = {"open": open_prices,
        "high": np.maximum(open_prices, closes) * 1.003,
        "low": np.minimum(open_prices, closes) * .997, "close": closes}
    capitals = np.array([1000., 700., 500.])
    if strategy == "simple":
        lows, highs = np.array([80., 82., 84.]), np.array([120., 118., 116.])
        combined_input = {key: np.concatenate([value] * len(capitals), axis=0)
            for key, value in scenarios.items()}
        combined = simulate_simple(combined_input, n_levels=6,
            capital=np.repeat(capitals, len(closes)), low=np.repeat(lows, len(closes)),
            high=np.repeat(highs, len(closes)), max_loss_pct=18, target_usdt=20)
        single = [simulate_simple(scenarios, n_levels=6, capital=capital,
            low=low, high=high, max_loss_pct=18, target_usdt=20)
            for capital, low, high in zip(capitals, lows, highs)]
    else:
        buys, sells = np.array([98., 99., 97.]), np.array([104., 105., 103.])
        combined_input = {key: np.concatenate([value] * len(capitals), axis=0)
            for key, value in scenarios.items()}
        combined = simulate_bot(combined_input,
            capital=np.repeat(capitals, len(closes)),
            buy_price=np.repeat(buys, len(closes)), sell_price=np.repeat(sells, len(closes)),
            max_loss_pct=18, target_usdt=20)
        single = [simulate_bot(scenarios, capital=capital, buy_price=buy,
            sell_price=sell, max_loss_pct=18, target_usdt=20)
            for capital, buy, sell in zip(capitals, buys, sells)]
    for geometry_index, reference in enumerate(single):
        start, end = geometry_index * len(closes), (geometry_index + 1) * len(closes)
        for key in ("final_equity", "pnl_usdt", "cycles", "buys", "sells",
                "fees_usdt", "inventory_qty", "inventory_value", "loss_cut",
                "loss_cut_candle", "target_candle", "cash_curve", "equity_curve"):
            np.testing.assert_allclose(combined[key][start:end], reference[key],
                rtol=0, atol=1e-12, err_msg=f"{strategy} geometría={geometry_index} campo={key}")


@pytest.mark.parametrize("case", range(40))
@pytest.mark.parametrize("start_ts", [0, 1_727_740_800])
def test_simple_batch_matches_production_replay(case, start_ts):
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
    reference = run_simulation(_candles(scenario, start_ts), strategy="simple", n=n_levels,
        capital=1000, low=80, high=120, params={}, fee_pct=.1,
        resync_candles=3, meta_max_loss_pct=max_loss, meta_portfolio_piece=True,
        include_details=True,
        trace_callback=trace.append)
    fast = simulate_simple(scenario, n_levels=n_levels, capital=1000,
        low=80, high=120, max_loss_pct=max_loss, target_usdt=20, initial_timestamp=start_ts)
    events = reference["events"]
    assert int(fast["buys"][0]) == sum(event["type"] == "BUY_FILLED" for event in events)
    assert int(fast["sells"][0]) == sum(event["type"] == "SELL_FILLED" for event in events)
    assert int(fast["cycles"][0]) == reference["metrics"]["cycles_completed"]
    assert bool(fast["loss_cut"][0]) == reference["metrics"]["loss_cut_reached"]
    cut_event = next((event for event in reference["events"] if event["type"] == "META_LOSS_CUT"), None)
    assert int(fast["loss_cut_candle"][0]) == (-1 if cut_event is None else
        (int(cut_event["ts"]) - start_ts) // 300)
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


def test_jup_low_capital_geometry_matches_reference_filters():
    filters = SymbolFilters(Decimal("0.0001"), Decimal("0.0001"), Decimal("1000"),
        Decimal("0.1"), Decimal("0.1"), Decimal("1000000"), Decimal("5"), True, 200)
    close = .25 * np.exp(np.cumsum(np.r_[0., np.sin(np.arange(1, 60) / 5) * .004]))
    scenario = _scenario(close)
    trace = []
    reference = run_simulation(_candles(scenario, 1_727_740_800), strategy="simple", n=4,
        capital=80, low=.20, high=.30, filters=filters, params={}, fee_pct=.1,
        resync_candles=3, meta_portfolio_piece=True, trace_callback=trace.append)
    fast = simulate_simple(scenario, n_levels=4, capital=80, low=.20, high=.30,
        filters=filters, initial_timestamp=1_727_740_800)
    assert int(fast["buys"][0]) == sum(event["type"] == "BUY_FILLED" for event in reference["events"])
    assert int(fast["sells"][0]) == sum(event["type"] == "SELL_FILLED" for event in reference["events"])
    assert abs(float(fast["pnl_usdt"][0]) -
        (reference["metrics"]["equity_final_after_repository_liquidation"] - 80)) <= .02
