"""OHLC replay adapter for running GridEngine against the simulator's candles."""
from __future__ import annotations

import re
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

from tests.grid_fakes import FakeExchange
from data.testnet_client import TestnetOrderError


class CandleFakeExchange(FakeExchange):
    """Fill only candle-start orders with the 15A strict penetration rules."""

    def __init__(self, fee_rate="0.001", fee_asset="XRP"):
        super().__init__(fee_rate=fee_rate, fee_asset=fee_asset)
        self.candle_fills = []
        self.candle_index = -1
        self.initial_usdt = self.free_usdt

    def get_balance(self, asset=None):
        usdt, base = self.initial_usdt, Decimal(0)
        for order in self.orders.values():
            if order["status"] != "FILLED":
                continue
            for trade in order.get("_trades", []):
                qty, price = Decimal(str(trade["qty"])), Decimal(str(trade["price"]))
                fee = Decimal(str(trade["commission"]))
                fee_asset = str(trade["commission_asset"]).upper()
                if order["side"] == "BUY":
                    usdt -= qty * price + (fee if fee_asset == "USDT" else 0)
                    base += qty - (fee if fee_asset == "XRP" else 0)
                else:
                    usdt += qty * price - (fee if fee_asset == "USDT" else 0)
                    base -= qty + (fee if fee_asset == "XRP" else 0)
        locked = sum((Decimal(str(order["price"])) * Decimal(str(order["quantity"]))
                      * (Decimal(1) + self.fee_rate if self.fee_asset == "USDT" else Decimal(1))
                      for order in self.get_open_orders("XRPUSDT") if order["side"] == "BUY"), Decimal(0))
        balances = {"USDT": {"free": float(usdt - locked), "locked": float(locked)},
                    "XRP": {"free": float(base), "locked": 0.0}}
        return balances if asset is None else {str(asset).upper(): balances.get(
            str(asset).upper(), {"free": 0.0, "locked": 0.0})}

    def place_order(self, symbol, side, quantity, price=None, order_type="LIMIT", client_order_id=None):
        if side == "BUY" and order_type != "MARKET":
            price_d = Decimal(str(self.avg if price is None else price))
            qty_d = Decimal(str(quantity))
            required = price_d * qty_d
            if self.fee_asset == "USDT":
                required += required * self.fee_rate
            free = Decimal(str(self.get_balance("USDT")["USDT"]["free"]))
            if required > free:
                raise TestnetOrderError(-2010, f"Account has insufficient balance: free={free}, required={required}")
        return super().place_order(symbol, side, quantity, price, order_type, client_order_id)

    @staticmethod
    def _cell_idx(order):
        match = re.search(r"L(\d+)", str(order.get("client_order_id") or ""))
        return int(match.group(1)) if match else 2**31

    def move_price(self, bid, ask, avg=None):
        # advance() already did OHLC matching; update the reference market without
        # letting FakeExchange's bid/ask matcher add a close-only fill.
        self.bid = Decimal(str(bid))
        self.ask = Decimal(str(ask))
        self.avg = Decimal(str(avg if avg is not None else (self.bid + self.ask) / 2))

    def advance(self, low, high, close):
        self.candle_index += 1
        open_at_start = list(self.get_open_orders("XRPUSDT"))
        eligible = []
        for order in open_at_start:
            price = Decimal(str(order["price"]))
            hit = (order["side"] == "BUY" and Decimal(str(low)) < price) or (
                order["side"] == "SELL" and Decimal(str(high)) > price
            )
            if hit:
                eligible.append(order)
        for order in sorted(eligible, key=lambda row: (self._cell_idx(row), int(row["order_id"]))):
            filled = self.fill(int(order["order_id"]))
            trade = self.get_my_trades("XRPUSDT", int(order["order_id"]))[0]
            fee = Decimal(str(trade["commission"]))
            qty = Decimal(str(filled["executed_qty"]))
            net_qty = qty - fee if self.fee_asset == "XRP" and order["side"] == "BUY" else qty
            if order["side"] == "BUY":
                net_qty = self.filters.round_qty_down(net_qty)
            self.candle_fills.append({
                "candle": self.candle_index,
                "level_idx": self._cell_idx(order),
                "side": order["side"],
                "price": Decimal(str(order["price"])),
                "qty": qty,
                "qty_net": net_qty,
                "fee": fee,
                "order_id": int(order["order_id"]),
            })
        self.move_price(close, close, close)
        return tuple(self.candle_fills[-len(eligible):]) if eligible else ()

    def fill(self, order_id, partial=False):
        filled = super().fill(order_id, partial=partial)
        order = self.orders[int(order_id)]
        if self.fee_asset == "USDT":
            qty = Decimal(str(filled["executed_qty"]))
            price = Decimal(str(filled["price"]))
            order["_trades"][0]["commission"] = qty * price * self.fee_rate
        return filled


class CandleVolatilityProvider:
    """Deterministic per-pass sigma source aligned to the candle timestamp."""

    def __init__(self, values):
        self.values = list(values)
        self.index = -1
        self.last_reason = None

    def advance(self, index):
        self.index = int(index)

    def get(self, _symbol):
        value = self.values[self.index]
        if value is None:
            self.last_reason = "unavailable"
            return SimpleNamespace(sigma_24h=None, stale=False)
        value = float(value)
        if not __import__("math").isfinite(value):
            self.last_reason = "unavailable"
            return SimpleNamespace(sigma_24h=None, stale=False)
        self.last_reason = None
        return SimpleNamespace(sigma_24h=value, stale=False)


def run_smart_engine(candles, sigma, *, n=6, capital=12000, low=94, high=106,
                     params=None, fee_pct=.1, resync_candles=3, trace_callback=None):
    """Replay candles through the production monitor/engine with injected time and sigma."""
    from grid.monitor import GridMonitor
    from tests.test_grid_engine import make_engine

    engine, db, _unused = make_engine(fee_rate=str(fee_pct / 100), fee_asset="XRP")
    exchange = CandleFakeExchange(fee_rate=str(fee_pct / 100), fee_asset="XRP")
    exchange.initial_usdt = Decimal(str(capital))
    exchange.free_usdt = Decimal(str(capital))
    exchange.move_price(candles.close[0], candles.close[0], candles.close[0])
    engine.exchange = exchange
    execution_order = []
    stoploss_call = engine.stoploss_cell
    sync_call = engine.sync_grid
    sync_paused_call = engine.sync_paused

    def tracked_stoploss(*args, **kwargs):
        execution_order.append(("stoploss", int(args[1])))
        return stoploss_call(*args, **kwargs)

    def tracked_sync(*args, **kwargs):
        execution_order.append(("sync", None))
        return sync_call(*args, **kwargs)

    def tracked_sync_paused(*args, **kwargs):
        execution_order.append(("sync_paused", None))
        return sync_paused_call(*args, **kwargs)

    engine.stoploss_cell = tracked_stoploss
    engine.sync_grid = tracked_sync
    engine.sync_paused = tracked_sync_paused
    grid = engine.create_grid("XRPUSDT", low, high, n, capital=capital,
                              strategy="smart", params=params)
    settings = SimpleNamespace(grid_policy_enabled=True, grid_monitor_gap_minutes=10_000,
                               grid_monitor_interval=60, max_grids_simultaneos=5,
                               capital_max_por_nivel_pct=.30, grid_min_step_pct=.003)
    provider = CandleVolatilityProvider(sigma)
    now_value = [datetime.fromtimestamp(int(candles.timestamp[0]), timezone.utc)]
    handle_buy_fill = engine._handle_buy_fill

    def handle_buy_fill_at_candle(grid_row, level, order, filters, avg_price):
        result = handle_buy_fill(grid_row, level, order, filters, avg_price)
        fill = next((row for row in exchange.candle_fills
                     if int(row["order_id"]) == int(order["order_id"])), None)
        fill_candle = exchange.candle_index if fill is None else int(fill["candle"])
        filled_at = datetime.fromtimestamp(int(candles.timestamp[fill_candle]), timezone.utc)
        db.update_level(int(grid_row["id"]), int(level["level_idx"]),
                        bought_at=filled_at.replace(tzinfo=None))
        return result

    engine._handle_buy_fill = handle_buy_fill_at_candle
    # Production's clock is injectable, but DBManager event timestamps default to wall time.
    # Bind only this in-memory replay DB so hysteresis age uses the candle clock too.
    add_event = db.add_grid_event

    def add_candle_event(**kwargs):
        kwargs.setdefault("ts", now_value[0])
        return add_event(**kwargs)

    db.add_grid_event = add_candle_event
    update_adjust = db.update_levels_and_grid_with_event

    def update_adjust_at_candle(*args, **kwargs):
        result = update_adjust(*args, **kwargs)
        event_type = (kwargs.get("event") or {}).get("event_type")
        if event_type:
            saved = db.get_last_event(int(args[0] if args else kwargs["grid_id"]), event_type)
            if saved:
                with db.engine.begin() as connection:
                    connection.execute(db.grid_events.update().where(
                        db.grid_events.c.id == int(saved["id"]),
                    ).values(ts=now_value[0].replace(tzinfo=None)))
        return result

    db.update_levels_and_grid_with_event = update_adjust_at_candle
    monitor = GridMonitor(db, exchange, engine, settings,
                          clock=lambda: now_value[0], vol_provider=provider)
    event_cursor = 0
    snapshots = []
    for i, (ts, lo, hi, close) in enumerate(zip(candles.timestamp, candles.low,
                                                 candles.high, candles.close)):
        exchange.advance(lo, hi, close)
        now_value[0] = datetime.fromtimestamp(int(ts), timezone.utc)
        provider.advance(i)
        if i % resync_candles != 0:
            continue
        operation_cursor = len(execution_order)
        result = monitor.run_once("SCHEDULED")
        assert result["status"] == "OK", f"production monitor pass failed: {result}"
        current = db.get_grid(grid["id"])
        repository_grids = db.list_grids_by_status({"HOLDING"})
        if current is not None and current.get("status") == "CLOSED" and repository_grids:
            current = next((row for row in repository_grids if row["symbol"] == "XRPUSDT"), current)
        cells = db.get_grid_levels(current["id"])
        all_events = db.list_grid_events(grid_id=grid["id"], limit=5000)
        decisions = []
        new_events = sorted((event for event in all_events if int(event["id"]) > event_cursor),
                            key=lambda event: int(event["id"]))
        for event in new_events:
            kind = event.get("event_type")
            if kind in {"GRID_PAUSED", "GRID_RESUMED", "GRID_ADJUSTED", "ADJUST_BLOCKED",
                        "GRID_AUTO_CLOSE", "CELL_STOPLOSS"}:
                decisions.append({"type": kind, "reason": event.get("reason"),
                                  "details": event.get("details") or {}})
        event_cursor = max((int(event["id"]) for event in all_events), default=event_cursor)
        balances = exchange.get_balance("USDT")
        base_asset = "XRP"
        balance_base = exchange.get_balance(base_asset)
        midpoint = float(close)
        base_free = Decimal(str(balance_base[base_asset]["free"]))
        isolated_usdt = (Decimal(str(balances["USDT"]["free"]))
                         + Decimal(str(balances["USDT"].get("locked", 0)))
                         - exchange.initial_usdt + Decimal(str(capital)))
        equity = isolated_usdt + base_free * Decimal(str(midpoint))
        order_cells = {int(row["order_id"]): int(row["level_idx"]) for row in cells
                       if row.get("order_id") is not None}
        orders = [{"cell": order_cells.get(int(row["order_id"]),
                                            CandleFakeExchange._cell_idx(row)),
                   "order_id": int(row["order_id"]),
                   "side": row["side"],
                   "price": Decimal(str(row["price"])),
                   "qty": Decimal(str(row["quantity"]))}
                  for row in exchange.get_open_orders("XRPUSDT")]
        sigma_value = provider.values[i]
        if sigma_value is not None:
            sigma_value = float(sigma_value)
            if not __import__("math").isfinite(sigma_value):
                sigma_value = None
        view = {"candle": i, "timestamp": int(ts), "status": current["status"],
                "sigma_24h": sigma_value,
                "execution_order": execution_order[operation_cursor:],
                "range_low": Decimal(str(current["range_low"])),
                "range_high": Decimal(str(current["range_high"])),
                "n_levels": int(current["n_levels"]),
                "cells": [{key: row.get(key) for key in ("level_idx", "state", "price", "sell_price",
                           "held_qty", "capital", "cycles_completed")} for row in cells],
                "orders": orders, "equity": equity, "fees": sum(
                    Decimal(str(row.get("fee_paid") or 0)) for row in cells),
                "balances": {"USDT": isolated_usdt,
                             "XRP": base_free}, "decisions": decisions,
                "exchange": exchange, "grid": current, "db": db}
        view["candle_index"] = exchange.candle_index
        snapshots.append(view)
        if trace_callback is not None:
            trace_callback(view)
    return {"snapshots": snapshots, "exchange": exchange, "db": db,
            "grid": db.get_grid(grid["id"]), "cells": db.get_grid_levels(grid["id"]),
            "events": db.list_grid_events(grid_id=grid["id"], limit=5000),
            "grid_id": grid["id"]}
