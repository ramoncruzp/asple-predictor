from __future__ import annotations

import numpy as np
import pytest

from grid.sim.data import CandleData
from grid.sim.data import load_candles
from grid.sim.runner import run_simulation
from tests.sim_replay_adapter import CandleFakeExchange
from tests.test_grid_engine import make_engine


GRID_LOW, GRID_HIGH, GRID_N, CAPITAL = 94.0, 106.0, 6, 12000.0


def candles_from_close(close, lows=None, highs=None):
    close = np.asarray(close, dtype=float)
    lows = close.copy() if lows is None else np.asarray(lows, dtype=float)
    highs = close.copy() if highs is None else np.asarray(highs, dtype=float)
    return CandleData(np.arange(len(close), dtype=np.int64) * 300 + 1_700_000_100,
                      close.copy(), highs, lows, close.copy(), 0)


def run_pair(candles, *, fee_pct=.1, resync_candles=3, low=GRID_LOW,
             high=GRID_HIGH, n=GRID_N, capital=CAPITAL, csv_hash=None):
    engine, db, _ = make_engine(fee_rate=str(fee_pct / 100), fee_asset="XRP")
    exchange = CandleFakeExchange(fee_rate=str(fee_pct / 100), fee_asset="XRP")
    exchange.move_price(candles.close[0], candles.close[0], candles.close[0])
    engine.exchange = exchange
    filters = exchange.filters
    grid = engine.create_grid("XRPUSDT", low, high, n,
                              capital=capital, strategy="simple")
    sim = run_simulation(candles, n=n, capital=capital, low=low,
                         high=high, fee_pct=fee_pct, fee_asset="XRP", csv_hash=csv_hash,
                         filters=filters, resync_candles=resync_candles,
                         include_details=True)
    for i, (low, high, close) in enumerate(zip(candles.low, candles.high, candles.close)):
        exchange.advance(low, high, close)
        if i % resync_candles == 0:
            engine.sync_grid(grid["id"])
    # Flush only an end-of-window exchange fill into GridEngine's persisted cell
    # projection; there is no subsequent candle on which a new order can fill.
    if (len(candles.timestamp) - 1) % resync_candles != 0:
        engine.sync_grid(grid["id"])
    levels = db.get_grid_levels(grid["id"])
    real_cells = [{"level_idx": int(row["level_idx"]), "state": row["state"],
                   "held_qty": float(row.get("held_qty") or 0),
                   "cycles_completed": int(row["cycles_completed"]),
                   "pnl": float(row["pnl"])} for row in levels]
    trades = [exchange.get_my_trades("XRPUSDT", fill["order_id"])[0]
              for fill in exchange.candle_fills]
    real_balances = {
        "USDT": CAPITAL + sum((float(t["qty"]) * float(t["price"]) *
                                 (1 if exchange.orders[fill["order_id"]]["side"] == "SELL" else -1))
                                for t, fill in zip(trades, exchange.candle_fills)),
        "XRP": sum(((float(t["qty"]) - float(t["commission"]))
                    if exchange.orders[fill["order_id"]]["side"] == "BUY" else
                    -(float(t["qty"]) + float(t["commission"])))
                   if t["commission_asset"] == "XRP" else
                   (float(t["qty"]) if exchange.orders[fill["order_id"]]["side"] == "BUY" else -float(t["qty"]))
                   for t, fill in zip(trades, exchange.candle_fills)),
    }
    real_fills = [{**{key: fill[key] for key in ("candle", "level_idx", "side")},
                   "price": float(fill["price"]), "qty_net": float(fill["qty_net"])}
                  for fill in exchange.candle_fills]
    sim_fills = [{"candle": int((event["ts"] - int(candles.timestamp[0])) / 300),
                  "level_idx": event["level_idx"], "side": event["type"].removesuffix("_FILLED"),
                  "price": float(event["price"]), "qty_net": float(event["qty_net"])}
                 for event in sim["events"] if event["type"] in {"BUY_FILLED", "SELL_FILLED"}]
    real_metrics = {
        "cycles_completed": sum(cell["cycles_completed"] for cell in real_cells),
        "pnl_realized_net_usdt": sum(cell["pnl"] for cell in real_cells),
        "fees_usdt": sum(float(row.get("fee_paid") or 0) for row in levels),
        "balances": real_balances,
    }
    sim_metrics = {**sim["metrics"], "balances": {k: float(v) for k, v in sim["details"]["balances"].items()}}
    sim_cells = [{"level_idx": int(row["level_idx"]), "state": row["state"],
                  "held_qty": float(row["held_qty"]), "cycles_completed": row["cycles_completed"],
                  "pnl": float(row["pnl"])} for row in sim["details"]["cells"]]
    return {"real_fills": real_fills, "sim_fills": sim_fills,
            "real_cells": real_cells, "sim_cells": sim_cells,
            "real_metrics": real_metrics, "sim_metrics": sim_metrics,
            "real_fee_paid_cells": [float(row.get("fee_paid") or 0) for row in levels],
            "raw_sim": sim}


def real_csv_pair():
    from pathlib import Path
    import hashlib

    path = Path("data/cache/xrp_5m.csv")
    if not path.exists():
        pytest.skip("falta data/cache/xrp_5m.csv, escenario real d no disponible")
    raw = load_candles(path)
    if len(raw.timestamp) < 576:
        pytest.skip("xrp_5m.csv tiene menos de 576 velas para dos días completos")
    start = len(raw.timestamp) - 576
    timestamps = raw.timestamp[start:]
    if not np.all(np.diff(timestamps) == 300):
        pytest.skip("las últimas 576 velas del CSV contienen huecos; no hay segmento continuo de dos días")
    factor = 100.0
    scaled = CandleData(timestamps.copy(), raw.open[start:] * factor, raw.high[start:] * factor,
                        raw.low[start:] * factor, raw.close[start:] * factor, 0)
    low, high = float(np.min(scaled.low)), float(np.max(scaled.high))
    n = 6
    assert (high - low) / n / low >= .003
    assert all(abs(float(value) * 100 - round(float(value) * 100)) < 1e-8
               for values in (scaled.open, scaled.high, scaled.low, scaled.close) for value in values)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return scaled, low, high, n, digest


def _first_difference(real, simulated):
    for i, (left, right) in enumerate(zip(real, simulated)):
        if left != right:
            return i, left, right
    if len(real) != len(simulated):
        return min(len(real), len(simulated)), real[min(len(real), len(simulated)):], simulated[min(len(real), len(simulated)):]
    return None


@pytest.mark.parametrize("scenario", ["synthetic", "multiwick", "range_excursions", "rearm"])
def test_simple_grid_replay_matches_real_engine(scenario):
    if scenario == "synthetic":
        rng = np.random.default_rng(150415)
        i = np.arange(500)
        close = 100 + 3.6 * np.sin(2 * np.pi * i / 38) + .4 * np.sin(2 * np.pi * i / 177)
        close += rng.normal(0, .11, len(i))
        close = np.round(close, 2)
        wick = np.round(rng.uniform(.05, .5, len(i)), 2)
        lows, highs = np.round(close - wick, 2), np.round(close + wick, 2)
    elif scenario == "multiwick":
        close = np.array([100, 100, 100, 100, 101, 100, 100, 100, 100, 100], dtype=float)
        lows = np.array([99.9, 93.9, 99.9, 99.9, 99.9, 99.9, 99.9, 99.9, 99.9, 99.9])
        highs = np.array([100.1, 100.1, 100.1, 100.1, 102.1, 100.1, 100.1, 100.1, 100.1, 100.1])
    elif scenario == "range_excursions":
        close = np.array([100, 100, 100, 100, 109, 108, 104, 100, 96, 91, 92, 96, 100, 104, 108, 100], dtype=float)
        lows = np.round(close - .2, 2)
        highs = np.round(close + .2, 2)
        lows[8], highs[4] = 93.9, 108.1
        lows[9], lows[10], highs[11] = 90.9, 91.9, 100.1
    else:
        close = np.array([100, 100, 100, 100, 98, 100, 101, 101, 99, 97, 96, 101,
                          100, 98, 99, 100], dtype=float)
        lows = np.round(close - .1, 2)
        highs = np.round(close + .1, 2)
        lows[4], highs[6], highs[7], lows[9], lows[13] = 97.9, 100.1, 101.1, 93.9, 97.9
    pair = run_pair(candles_from_close(close, lows, highs), fee_pct=.1)
    first_fill_difference = _first_difference(pair["real_fills"], pair["sim_fills"])
    assert first_fill_difference is None, f"{scenario}: first fill difference={first_fill_difference}"
    assert pair["real_cells"] == pair["sim_cells"], f"{scenario}: real/sim per-cell state differs"
    assert pair["real_metrics"]["cycles_completed"] == pair["sim_metrics"]["cycles_completed"]
    assert pair["real_metrics"]["pnl_realized_net_usdt"] == pytest.approx(
        pair["sim_metrics"]["pnl_realized_net_usdt"], abs=1e-8)
    assert pair["real_metrics"]["fees_usdt"] == pytest.approx(pair["sim_metrics"]["fees_usdt"], abs=1e-8)
    for asset in ("USDT", "XRP"):
        assert pair["real_metrics"]["balances"][asset] == pytest.approx(
            pair["sim_metrics"]["balances"][asset], abs=1e-8)
    if scenario == "synthetic":
        assert pair["real_metrics"]["cycles_completed"] >= 8
    elif scenario == "multiwick":
        buys = [fill for fill in pair["real_fills"] if fill["candle"] == 1 and fill["side"] == "BUY"]
        sells = [fill for fill in pair["real_fills"] if fill["candle"] == 4 and fill["side"] == "SELL"]
        assert [fill["level_idx"] for fill in buys] == [0, 1, 2]
        assert [fill["level_idx"] for fill in sells] == [0, 1, 2]
    elif scenario == "range_excursions":
        assert max(candles_from_close(close, lows, highs).close) > GRID_HIGH
        assert min(candles_from_close(close, lows, highs).close) < GRID_LOW
        assert any(fill["side"] == "SELL" and fill["candle"] > 9 for fill in pair["real_fills"])
    elif scenario == "rearm":
        level_two = [fill for fill in pair["real_fills"] if fill["level_idx"] == 2]
        assert [(fill["candle"], fill["side"]) for fill in level_two] == [
            (4, "BUY"), (7, "SELL"), (13, "BUY")]


def test_two_day_real_csv_replay_matches_engine_and_resync_sensitivity():
    candles, low, high, n, digest = real_csv_pair()
    baseline = run_pair(candles, low=low, high=high, n=n, csv_hash=digest)
    assert _first_difference(baseline["real_fills"], baseline["sim_fills"]) is None
    assert baseline["real_cells"] == baseline["sim_cells"]
    assert baseline["real_metrics"]["pnl_realized_net_usdt"] == pytest.approx(
        baseline["sim_metrics"]["pnl_realized_net_usdt"], abs=1e-8)
    assert baseline["real_metrics"]["fees_usdt"] == pytest.approx(
        baseline["sim_metrics"]["fees_usdt"], abs=1e-8)
    for asset in ("USDT", "XRP"):
        assert baseline["real_metrics"]["balances"][asset] == pytest.approx(
            baseline["sim_metrics"]["balances"][asset], abs=1e-8)
    sensitivity = {}
    for cadence in (1, 3, 6):
        result = run_simulation(candles, n=n, capital=CAPITAL, low=low, high=high,
                                filters=CandleFakeExchange().filters, fee_pct=.1,
                                fee_asset="XRP", resync_candles=cadence, csv_hash=digest)
        sensitivity[cadence] = (result["metrics"]["cycles_completed"],
                                result["metrics"]["pnl_realized_net_usdt"])
    assert set(sensitivity) == {1, 3, 6}


def test_adjust_blocked_throttle_uses_same_reason_six_hours_and_latest_100(monkeypatch):
    from types import SimpleNamespace
    import grid.sim.runner as runner

    recent = [{"ts": 10_000, "details": {"blocked_reason": "too_close"}}]
    assert not runner._should_emit_adjust_blocked(10_000 + 21_599, "too_close", recent)
    assert runner._should_emit_adjust_blocked(10_000 + 21_600, "too_close", recent)
    assert runner._should_emit_adjust_blocked(10_001, "other", recent)
    last_hundred = [{"ts": i, "details": {"blocked_reason": "other"}} for i in range(100)]
    assert runner._should_emit_adjust_blocked(200, "too_close", last_hundred)

    monkeypatch.setattr(runner, "evaluate_grid", lambda *args, **kwargs:
                        SimpleNamespace(action="NONE", reasons=(), metrics={}))
    monkeypatch.setattr(runner, "adjust_decision", lambda *args, **kwargs:
                        SimpleNamespace(action="BLOCKED", reasons=("too_close",), metrics={}))
    close = np.full(25, 100.0)
    result = runner.run_simulation(candles_from_close(close), strategy="smart", n=6,
                                   capital=12000, low=94, high=106, fee_pct=0,
                                   filters=CandleFakeExchange().filters)
    assert result["metrics"]["adjust_attempts_blocked"] == 9
    assert result["metrics"]["adjust_rejected_events"] == 1
    assert sum(event["type"] == "ADJUST_REJECTED" for event in result["events"]) == 1


def test_new_sell_order_cannot_fill_on_the_buy_candle():
    close = np.array([100, 100, 101, 101], dtype=float)
    lows = np.array([99.9, 93.9, 99.9, 99.9])
    highs = np.array([100.1, 102.1, 102.1, 100.1])
    pair = run_pair(candles_from_close(close, lows, highs), resync_candles=1)
    assert _first_difference(pair["real_fills"], pair["sim_fills"]) is None
    buys = [fill for fill in pair["real_fills"] if fill["side"] == "BUY" and fill["candle"] == 1]
    sells = [fill for fill in pair["real_fills"] if fill["side"] == "SELL"]
    assert [(fill["candle"], fill["level_idx"]) for fill in buys] == [(1, 0), (1, 1), (1, 2)]
    assert [(fill["candle"], fill["level_idx"]) for fill in sells] == [(2, 0), (2, 1), (2, 2)]


def test_zero_commission_multiwick_matches_real_engine():
    close = np.array([100, 100, 100, 100, 101, 100, 100, 100, 100, 100], dtype=float)
    lows = np.array([99.9, 93.9, 99.9, 99.9, 99.9, 99.9, 99.9, 99.9, 99.9, 99.9])
    highs = np.array([100.1, 100.1, 100.1, 100.1, 102.1, 100.1, 100.1, 100.1, 100.1, 100.1])
    pair = run_pair(candles_from_close(close, lows, highs), fee_pct=0)
    assert _first_difference(pair["real_fills"], pair["sim_fills"]) is None
    assert pair["real_cells"] == pair["sim_cells"]
    assert pair["real_metrics"]["fees_usdt"] == pair["sim_metrics"]["fees_usdt"] == 0
    assert pair["real_metrics"]["pnl_realized_net_usdt"] == pair["sim_metrics"]["pnl_realized_net_usdt"]
    assert pair["real_metrics"]["balances"] == pytest.approx(pair["sim_metrics"]["balances"], abs=1e-8)
