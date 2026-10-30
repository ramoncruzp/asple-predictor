"""Replay numpy por lotes para Simple y bot individual.

La ruta Simple sigue las reglas documentadas en FASTSIM_SPEC.md. Se mantiene un
bucle temporal; la dimensión de escenarios y celdas usa arreglos numpy.
"""
from __future__ import annotations

from decimal import Decimal

import numpy as np

from data.exchange_filters import SymbolFilters
from grid.levels import compute_lines, plan_cells
from grid.policy import plan_dust_sweep
from grid.sim.runner import FILTERS


def _inputs(scenarios):
    arrays = {key: np.asarray(scenarios[key], dtype=np.float64)
        for key in ("open", "high", "low", "close")}
    shape = arrays["close"].shape
    if len(shape) != 2 or any(value.shape != shape for value in arrays.values()):
        raise ValueError("OHLC debe tener forma común (escenarios, velas)")
    if shape[0] < 1 or shape[1] < 2:
        raise ValueError("se requiere al menos un escenario y dos velas")
    return arrays


def _curves(timestamps, cash_rows, equity_rows, capital):
    from grid.sim.meta_portfolio import pack_curve
    return [pack_curve(timestamps, cash_rows[i], equity_rows[i], capital)
        for i in range(len(cash_rows))]


def simulate_simple(scenarios, *, n_levels, capital, low, high, filters=None,
        fee_pct=.1, resync_candles=3, max_loss_pct=None, target_usdt=None,
        initial_timestamp=0, candle_seconds=300, pack_curves=False):
    """Simula Simple en un lote; OHLC son matrices float64 (escenario, vela)."""
    bars = _inputs(scenarios)
    filters = FILTERS if filters is None else filters
    if not isinstance(filters, SymbolFilters):
        raise TypeError("filters debe ser SymbolFilters")
    if resync_candles < 1 or fee_pct < 0 or capital <= 0:
        raise ValueError("parámetros de capital, comisión o resincronización inválidos")
    if max_loss_pct is not None and not 0 < max_loss_pct < 100:
        raise ValueError("max_loss_pct debe estar entre 0 y 100")
    close = bars["close"]
    n_scenarios, n_bars = close.shape
    initial_price = float(close[0, 0])
    lines = compute_lines(str(low), str(high), int(n_levels), filters)
    plans = plan_cells(lines, str(capital),
        {"bid_price": str(initial_price * (1 - 1e-8)),
         "ask_price": str(initial_price * (1 + 1e-8)), "avg_price": str(initial_price)},
        filters, {"grid_min_step_pct": .003, "capital_max_por_nivel_pct": .30})
    cells = len(plans)
    prices = np.asarray([float(plan.buy_price) for plan in plans])
    sells = np.asarray([float(plan.sell_price) for plan in plans])
    qty = np.asarray([float(plan.qty) for plan in plans])
    initial_buy_open = np.asarray([plan.initial_state == "BUY_OPEN" for plan in plans])
    fee = float(Decimal(str(fee_pct)) / 100)
    cash = np.full(n_scenarios, float(capital), dtype=np.float64)
    held = np.zeros((n_scenarios, cells), dtype=np.float64)
    dust = np.zeros(n_scenarios, dtype=np.float64)
    order_side = np.zeros((n_scenarios, cells), dtype=np.int8)
    active_from = np.zeros((n_scenarios, cells), dtype=np.int32)
    reserved = np.zeros((n_scenarios, cells), dtype=np.float64)
    state = np.broadcast_to(initial_buy_open, (n_scenarios, cells)).copy().astype(np.int8)
    buys = np.zeros(n_scenarios, dtype=np.int32)
    sales = np.zeros(n_scenarios, dtype=np.int32)
    fees = np.zeros(n_scenarios, dtype=np.float64)
    cut = np.zeros(n_scenarios, dtype=bool)
    cut_candle = np.full(n_scenarios, -1, dtype=np.int32)
    target_candle = np.full(n_scenarios, -1, dtype=np.int32)
    target_enabled = target_usdt is not None
    sample_indices = [i for i in range(n_bars) if i == 0 or
        (initial_timestamp + i * candle_seconds) % 900 == 0 or i == n_bars - 1]
    curve_cash = np.zeros((n_scenarios, len(sample_indices)), dtype=np.float64)
    curve_equity = np.zeros_like(curve_cash)
    curve_timestamps = np.asarray([initial_timestamp + i * candle_seconds for i in sample_indices], np.int64)
    sample_lookup = {bar: j for j, bar in enumerate(sample_indices)}
    for i in range(n_bars):
        lo, hi, cls = bars["low"][:, i], bars["high"][:, i], close[:, i]
        # SimExchange.process recorre índices de celda ascendentes.
        for cell in range(cells):
            buy_hit = ((order_side[:, cell] == 1) & (i >= active_from[:, cell]) & (lo < prices[cell]))
            if np.any(buy_hit):
                cost = prices[cell] * qty[cell]
                gross_fee = qty[cell] * fee
                raw_net = qty[cell] - gross_fee
                net_values = np.asarray([float(filters.round_qty_down(Decimal(str(raw_net))))] * n_scenarios)
                mask = buy_hit & (cash + 1e-12 >= cost)
                cash[mask] -= cost
                fees[mask] += gross_fee * prices[cell]
                held[mask, cell] = net_values[mask]
                dust[mask] += raw_net - net_values[mask]
                state[mask, cell] = 2
                buys[mask] += 1
                order_side[mask, cell] = 0
                reserved[mask, cell] = 0
            sell_hit = ((order_side[:, cell] == 2) & (i >= active_from[:, cell]) & (hi > sells[cell]))
            if np.any(sell_hit):
                qty_sell = held[:, cell].copy()
                proceeds = qty_sell * sells[cell] * (1 - fee)
                sell_fee = qty_sell * sells[cell] * fee
                cash[sell_hit] += proceeds[sell_hit]
                fees[sell_hit] += sell_fee[sell_hit]
                held[sell_hit, cell] = 0
                state[sell_hit, cell] = 0
                sales[sell_hit] += 1
                order_side[sell_hit, cell] = 0
                reserved[sell_hit, cell] = 0
        if i % resync_candles == 0:
            locked = np.zeros(n_scenarios, dtype=np.float64)
            locked += np.where(order_side == 1, reserved, 0).sum(axis=1)
            for cell in range(cells):
                missing = order_side[:, cell] == 0
                sell_mask = missing & (state[:, cell] == 2) & (held[:, cell] > 0)
                order_side[sell_mask, cell] = 2
                active_from[sell_mask, cell] = i + 1
                buy_mask = (missing & (state[:, cell] <= 1) & (cls > prices[cell])
                    & (cash - locked >= prices[cell] * qty[cell] * (1 + fee)))
                if np.any(buy_mask):
                    order_side[buy_mask, cell] = 1
                    state[buy_mask, cell] = 1
                    active_from[buy_mask, cell] = i + 1
                    reserve_value = prices[cell] * qty[cell] * (1 + fee)
                    reserved[buy_mask, cell] = reserve_value
                    locked[buy_mask] += reserve_value
        if max_loss_pct is not None:
            base = held.sum(axis=1) + dust
            mark_low = cash + base * lo * (1 - fee)
            newly_cut = (~cut) & (mark_low <= float(capital) * (1 - max_loss_pct / 100))
            if np.any(newly_cut):
                for cell in range(cells):
                    qty_sell = held[newly_cut, cell].copy()
                    proceeds = qty_sell * lo[newly_cut] * (1 - fee)
                    fees[newly_cut] += qty_sell * lo[newly_cut] * fee
                    cash[newly_cut] += proceeds
                    held[newly_cut, cell] = 0
                    state[newly_cut, cell] = 3
                    order_side[newly_cut, cell] = 0
                    reserved[newly_cut, cell] = 0
                for scenario in np.flatnonzero(newly_cut):
                    sweep = plan_dust_sweep(Decimal(str(dust[scenario])),
                        Decimal(str(lo[scenario])), filters, Decimal(str(fee_pct)))
                    if sweep["sweepable"]:
                        swept = float(sweep["qty"])
                        gross_proceeds = swept * lo[scenario]
                        sweep_fee = gross_proceeds * fee
                        cash[scenario] += gross_proceeds - sweep_fee
                        fees[scenario] += sweep_fee
                        dust[scenario] -= swept
                cut[newly_cut] = True
                cut_candle[newly_cut] = i
        if i in sample_lookup:
            j = sample_lookup[i]
            curve_cash[:, j] = cash
            curve_equity[:, j] = cash + (held.sum(axis=1) + dust) * cls * (1 - fee)
            if target_enabled:
                hit = (target_candle < 0) & (cash >= float(capital) + float(target_usdt))
                target_candle[hit] = i
    final_equity = cash + (held.sum(axis=1) + dust) * close[:, -1] * (1 - fee)
    return {"final_equity": final_equity, "pnl_usdt": final_equity - float(capital),
        "cycles": sales.copy(), "buys": buys, "sells": sales, "fees_usdt": fees,
        "inventory_qty": held.sum(axis=1) + dust,
        "inventory_value": (held.sum(axis=1) + dust) * close[:, -1],
        "loss_cut": cut, "loss_cut_candle": cut_candle, "target_candle": target_candle,
        "timestamps": curve_timestamps, "cash_curve": curve_cash.astype(np.float32),
        "equity_curve": curve_equity.astype(np.float32),
        "curves": (_curves(curve_timestamps, curve_cash, curve_equity, capital)
            if pack_curves else None),
        "low": lines[0], "high": lines[-1], "n_levels": n_levels}


def simulate_bot(scenarios, *, capital, buy_price, sell_price, filters=None,
        fee_pct=.1, max_loss_pct=None, target_usdt=None, initial_timestamp=0,
        candle_seconds=300, min_margin_pct=.30, pack_curves=False):
    """Replay vectorizado del bot de compra única y venta única."""
    bars = _inputs(scenarios)
    filters = FILTERS if filters is None else filters
    fee = float(fee_pct) / 100
    if capital <= 0 or buy_price <= 0 or sell_price <= buy_price or fee < 0:
        raise ValueError("capital, rango o comisión inválidos para el bot")
    if (sell_price / buy_price - 1) * 100 - fee_pct * 2 < min_margin_pct:
        raise ValueError("margen neto inferior al mínimo del bot")
    qty = float(filters.round_qty_down(Decimal(str(capital)) /
        (Decimal(str(buy_price)) * (1 + Decimal(str(fee))))))
    if qty < float(filters.min_qty) or qty * buy_price < float(filters.min_notional):
        raise ValueError("notional de compra inferior al mínimo")
    net_inventory = qty * (1 - fee)
    sell_qty = float(filters.round_qty_down(Decimal(str(qty)) * (1 - Decimal(str(fee)))))
    if sell_qty < float(filters.min_qty) or sell_qty * sell_price < float(filters.min_notional):
        raise ValueError("notional neto de venta inferior al mínimo")
    close = bars["close"]
    count, n_bars = close.shape
    cash = np.full(count, float(capital), dtype=np.float64)
    bought = np.zeros(count, bool); sold = np.zeros(count, bool); cut = np.zeros(count, bool)
    cut_candle = np.full(count, -1, np.int32); target_candle = np.full(count, -1, np.int32)
    inventory = np.zeros(count, dtype=np.float64); fees = np.zeros(count, dtype=np.float64)
    sample_indices = [i for i in range(n_bars) if i == 0 or
        (initial_timestamp + i * candle_seconds) % 900 == 0 or i == n_bars - 1]
    cash_curve = np.zeros((count, len(sample_indices)), np.float64)
    equity_curve = np.zeros_like(cash_curve)
    timestamps = np.asarray([initial_timestamp + i * candle_seconds for i in sample_indices], np.int64)
    lookup = {bar: j for j, bar in enumerate(sample_indices)}
    for i in range(n_bars):
        was_bought = bought.copy()
        buy_mask = ~bought & (bars["low"][:, i] <= buy_price) & (qty * buy_price * (1 + fee) <= cash)
        if np.any(buy_mask):
            cash[buy_mask] -= qty * buy_price * (1 + fee)
            fees[buy_mask] += qty * buy_price * fee + qty * fee * buy_price
            inventory[buy_mask] = net_inventory
            bought[buy_mask] = True
        sell_mask = was_bought & ~sold & (bars["high"][:, i] >= sell_price)
        if np.any(sell_mask):
            proceeds = inventory[sell_mask] * sell_price * (1 - fee)
            cash[sell_mask] += proceeds
            fees[sell_mask] += inventory[sell_mask] * sell_price * fee
            inventory[sell_mask] = 0
            sold[sell_mask] = True
        if max_loss_pct is not None:
            mark = cash + inventory * bars["low"][:, i] * (1 - fee)
            mask = ~cut & (mark <= capital * (1 - max_loss_pct / 100))
            if np.any(mask):
                proceeds = inventory[mask] * bars["low"][mask, i] * (1 - fee)
                fees[mask] += inventory[mask] * bars["low"][mask, i] * fee
                cash[mask] += proceeds
                inventory[mask] = 0
                cut[mask] = True
                cut_candle[mask] = i
        if i in lookup:
            j = lookup[i]
            cash_curve[:, j] = cash
            equity_curve[:, j] = cash + inventory * close[:, i] * (1 - fee)
            if target_usdt is not None:
                mask = (target_candle < 0) & (cash >= capital + target_usdt)
                target_candle[mask] = i
    final_equity = cash + inventory * close[:, -1] * (1 - fee)
    return {"final_equity": final_equity, "pnl_usdt": final_equity - capital,
        "cycles": sold.astype(np.int32), "buys": bought.astype(np.int32), "sells": sold.astype(np.int32),
        "fees_usdt": fees, "inventory_qty": inventory, "inventory_value": inventory * close[:, -1],
        "loss_cut": cut, "loss_cut_candle": cut_candle, "target_candle": target_candle,
        "timestamps": timestamps, "cash_curve": cash_curve.astype(np.float32),
        "equity_curve": equity_curve.astype(np.float32),
        "curves": (_curves(timestamps, cash_curve, equity_curve, capital) if pack_curves else None)}
