"""Adaptador aproximado de replay activo, usado solo por scripts de estudio."""
from __future__ import annotations

import math
from collections import deque

import numpy as np

APPROXIMATION_NOTE = ("aproximación: política simplificada por salida de rango; "
    "reajuste = liquidar y reabrir; no modela rotación a otra moneda ni decisión por probabilidad")


def _slice(candles, start, stop):
    from grid.sim.data import CandleData
    return CandleData(np.asarray(candles.timestamp[start:stop]), np.asarray(candles.open[start:stop]),
        np.asarray(candles.high[start:stop]), np.asarray(candles.low[start:stop]),
        np.asarray(candles.close[start:stop]), int(getattr(candles, "gaps", 0)))


def _simulate(candles, *, cash, low, high, n, filters, trace):
    from grid.sim.runner import run_simulation
    return run_simulation(candles, strategy="simple", n=int(n), capital=float(cash),
        low=float(low), high=float(high), filters=filters, fee_pct=.1,
        close_wait_enabled=True, adjust_enabled_global=False, stoploss_enabled=False,
        meta_max_loss_pct=None, meta_portfolio_piece=True,
        sigma_values=np.full(len(candles.timestamp), np.nan), trace_callback=trace.append)


def _segment_curve(candles, result, trace, cash_in, *, terminal_liquidation=False):
    times, cash_values, equity_values = [], [], []
    for row in trace:
        index = int(row["candle"])
        close = float(candles.close[index])
        inventory = sum(float(cell.get("held_qty") or 0) for cell in row.get("cells", []))
        market_equity = float(row.get("equity") or 0)
        cash = market_equity - inventory * close
        marked = cash + inventory * close * .999
        times.append(int(row["timestamp"]))
        cash_values.append(cash)
        equity_values.append(marked)
    if not times:
        times = [int(candles.timestamp[0])]
        cash_values, equity_values = [float(cash_in)], [float(cash_in)]
    cash_out = float(result.get("metrics", {}).get("equity_final_after_repository_liquidation") or cash_in)
    if terminal_liquidation:
        cash_values[-1] = cash_out
        equity_values[-1] = cash_out
    return times, cash_values, equity_values, cash_out


def _eligible_trigger(candles, start, low, high, persistence, *, last_adjust, recent,
                      cooldown_s, max_per_24h):
    streak = 0
    for index in range(start, len(candles.timestamp)):
        ts = int(candles.timestamp[index])
        if ts % 900:
            continue
        close = float(candles.close[index])
        streak = streak + 1 if close < low or close > high else 0
        if streak < persistence:
            continue
        recent = [stamp for stamp in recent if ts - stamp < 86400]
        if last_adjust is not None and ts - last_adjust < cooldown_s:
            streak = 0
            continue
        if len(recent) >= max_per_24h:
            streak = 0
            continue
        return index
    return None


def active_replay(candles, *, low, high, n=6, capital=100, persistence=1,
                  cooldown_s=3600, max_per_24h=4, filters=None, simulator=None):
    """Simula segmentos continuos en efectivo, con liquidación y reapertura aproximadas."""
    if simulator is None:
        simulator = _simulate
    if len(candles.timestamp) == 0:
        raise ValueError("ventana sin velas")
    first_close = float(candles.close[0])
    lower_ratio, upper_ratio = float(low) / first_close, float(high) / first_close
    cash = float(capital)
    cursor = 0
    actions = []
    times_all, cash_all, equity_all = [], [], []
    commissions_total = 0.0
    recent = deque()
    last_adjust = None
    max_reconfigurations = 4 if max_per_24h is None else int(max_per_24h)
    while cursor < len(candles.timestamp):
        anchor = float(candles.close[cursor])
        segment_low, segment_high = anchor * lower_ratio, anchor * upper_ratio
        trigger = _eligible_trigger(candles, cursor, segment_low, segment_high, int(persistence),
            last_adjust=last_adjust, recent=list(recent), cooldown_s=int(cooldown_s),
            max_per_24h=max_reconfigurations)
        stop = (trigger + 1) if trigger is not None else len(candles.timestamp)
        segment = _slice(candles, cursor, stop)
        trace = []
        result = simulator(segment, cash=cash, low=segment_low, high=segment_high,
            n=n, filters=filters, trace=trace)
        segment_times, segment_cash, segment_equity, cash_out = _segment_curve(
            segment, result, trace, cash, terminal_liquidation=trigger is not None or stop == len(candles.timestamp))
        if times_all and segment_times and segment_times[0] == times_all[-1]:
            segment_times, segment_cash, segment_equity = segment_times[1:], segment_cash[1:], segment_equity[1:]
        times_all.extend(segment_times)
        cash_all.extend(segment_cash)
        equity_all.extend(segment_equity)
        metrics = result.get("metrics", {})
        commissions_total += float(metrics.get("fees_usdt", 0) or 0)
        if trigger is not None or stop == len(candles.timestamp):
            commissions_total += float(metrics.get("base_final", 0) or 0) * float(segment.close[-1]) * .001
        if trigger is None:
            cash = cash_out
            break
        stamp = int(candles.timestamp[trigger])
        actions.append({"at": stamp, "cash_before": cash, "cash_after_liquidation": cash_out,
            "close": float(candles.close[trigger]), "low": segment_low, "high": segment_high})
        cash = cash_out
        recent.append(stamp)
        last_adjust = stamp
        cursor = trigger + 1
    if not times_all:
        times_all = [int(candles.timestamp[0])]
        cash_all = equity_all = [float(capital)]
    return {"times": np.asarray(times_all, dtype=np.int64), "cash": np.asarray(cash_all, dtype=float),
        "equity": np.asarray(equity_all, dtype=float), "cash_final": float(cash),
        "commissions_usdt": commissions_total, "reconfigurations": actions,
        "status": "approximated", "approximation": APPROXIMATION_NOTE}


def evaluate_active_curve(replay, *, capital, target_usdt, max_loss_pct):
    from grid.sim.meta_portfolio import pack_curve, evaluate_portfolio
    curve = pack_curve(replay["times"], replay["cash"], replay["equity"], capital)
    metrics = evaluate_portfolio([curve], capital_initial=capital, target_usdt=target_usdt,
        max_loss_pct=max_loss_pct)
    return {**metrics, "reconfigurations": len(replay["reconfigurations"]),
        "commissions_and_liquidation_in_equity": True, "active_status": "approximated"}
