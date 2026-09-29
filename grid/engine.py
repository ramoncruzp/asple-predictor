"""Recoverable pull-based spot grid engine over an injected exchange client."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from binance.exceptions import BinanceRequestException
from requests.exceptions import RequestException

from data.exchange_filters import FilterViolation, SymbolFilters
from data.testnet_client import TestnetOrderError
from grid.levels import GridConfigError, compute_lines, plan_cells

logger = logging.getLogger(__name__)


class GridCreationError(RuntimeError):
    pass


def _d(value: Any) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))


def _get(obj: Any, key: str, default: Any = None) -> Any:
    return obj.get(key, default) if isinstance(obj, dict) else getattr(obj, key, default)


class GridEngine:
    def __init__(self, db: Any, exchange: Any, settings: Any, environment: str = "testnet"):
        if environment != "testnet":
            raise ValueError("GridEngine only supports the testnet environment in this phase")
        self.db = db
        self.exchange = exchange
        self.settings = settings
        self.environment = environment

    def _market_context(self, symbol: str) -> tuple[SymbolFilters, dict[str, Decimal], Decimal]:
        filters = SymbolFilters.from_symbol_info(self.exchange.get_symbol_info(symbol))
        book = self.exchange.get_book_ticker(symbol)
        avg = _d(self.exchange.get_avg_price(symbol))
        snapshot = {
            "bid_price": _d(book["bid_price"]),
            "ask_price": _d(book["ask_price"]),
            "avg_price": avg,
        }
        return filters, snapshot, avg

    def _active_coin(self, symbol: str) -> bool:
        coin = self.db.get_coin(symbol)
        return coin is not None and int(coin["active"]) == 1

    @staticmethod
    def _client_order_id(grid_id: int, level_idx: int, side: str, cycle: int) -> str:
        suffix = "B" if side == "BUY" else "S"
        value = f"g{grid_id}L{level_idx}{suffix}{cycle}"
        if len(value) > 36:
            raise ValueError("deterministic client order id exceeds Binance's 36 character limit")
        return value

    def _send_limit(
        self,
        symbol: str,
        side: str,
        qty: Decimal,
        price: Decimal,
        client_order_id: str,
        filters: SymbolFilters,
        avg_price: Decimal,
    ) -> dict[str, Any]:
        filters.validate_order(side, price, qty, avg_price)
        return self.exchange.place_order(
            symbol, side, qty, price, "LIMIT", client_order_id=client_order_id,
        )

    def create_grid(
        self,
        symbol: str,
        range_low: Decimal | str | float,
        range_high: Decimal | str | float,
        n_levels: int,
        capital: Decimal | str | float | None = None,
    ) -> dict:
        symbol = str(symbol).replace("/", "").upper()
        if not self._active_coin(symbol):
            raise GridConfigError(f"{symbol} must be an active registered coin")
        if self.db.count_open_grids() >= int(_get(self.settings, "max_grids_simultaneos", 5)):
            raise GridConfigError("maximum simultaneous open grids reached")
        capital_total = _d(
            _get(self.settings, "usdt_por_grid", 100) if capital is None else capital
        )
        filters, snapshot, avg_price = self._market_context(symbol)
        open_orders = self.exchange.get_open_orders(symbol)
        if len(open_orders) >= filters.max_num_orders:
            raise GridConfigError("exchange MAX_NUM_ORDERS is already reached")
        lines = compute_lines(range_low, range_high, n_levels, filters)
        plans = plan_cells(lines, capital_total, snapshot, filters, self.settings)
        buy_plans = [plan for plan in plans if plan.initial_state == "BUY_OPEN"]
        if len(open_orders) + len(buy_plans) > filters.max_num_orders:
            raise GridConfigError("grid buy orders exceed exchange MAX_NUM_ORDERS")
        balance = self.exchange.get_balance("USDT").get("USDT", {"free": 0.0})
        required = sum((plan.capital for plan in buy_plans), Decimal(0))
        if _d(balance.get("free", 0)) < required:
            raise GridConfigError("free USDT balance is below the required buy-cell capital")

        grid = self.db.create_grid_with_levels(
            {
                "symbol": symbol,
                "range_low": lines[0],
                "range_high": lines[-1],
                "n_levels": n_levels,
                "capital_total": capital_total,
                "status": "OPENING",
                "environment": self.environment,
                "open_price": (snapshot["bid_price"] + snapshot["ask_price"]) / 2,
            },
            [
                {
                    "level_idx": plan.level_idx,
                    "price": plan.buy_price,
                    "sell_price": plan.sell_price,
                    "capital": plan.capital,
                    "state": plan.initial_state,
                    "held_qty": 0,
                    "cycles_completed": 0,
                    "pnl": 0,
                    "fee_paid": 0,
                }
                for plan in plans
            ],
        )
        grid_id = int(grid["id"])
        attempted_ids: list[tuple[int, str]] = []
        try:
            for plan in buy_plans:
                cid = self._client_order_id(grid_id, plan.level_idx, "BUY", 0)
                attempted_ids.append((plan.level_idx, cid))
                self.db.update_level(
                    grid_id, plan.level_idx, state="BUY_OPEN", client_order_id=cid, order_id=None,
                )
                logger.info("grid=%s level=%s action=BUY_INTENT client_order_id=%s", grid_id, plan.level_idx, cid)
                order = self._send_limit(
                    symbol, "BUY", plan.qty, plan.buy_price, cid, filters, avg_price,
                )
                self.db.update_level(grid_id, plan.level_idx, order_id=order["order_id"])
                logger.info("grid=%s level=%s action=BUY_SENT order_id=%s", grid_id, plan.level_idx, order["order_id"])
            return self.db.update_grid(grid_id, status="ACTIVE")
        except Exception as exc:
            pending = []
            for level_idx, cid in attempted_ids:
                level = next(row for row in self.db.get_grid_levels(grid_id) if row["level_idx"] == level_idx)
                order_id = level.get("order_id")
                try:
                    if order_id is None:
                        recovered = self.exchange.find_order_by_client_id(symbol, cid)
                        order_id = recovered["order_id"] if recovered else None
                    if order_id is not None:
                        canceled = self.exchange.cancel_order(symbol, order_id)
                        if canceled.get("status") not in {"CANCELED", "EXPIRED", "REJECTED"}:
                            pending.append(str(order_id))
                except Exception:
                    pending.append(str(order_id or cid))
            reason = str(exc)
            if pending:
                reason += f"; cancel pending for {','.join(pending)}"
                logger.error("grid=%s action=CREATE_CLEANUP_FAILED pending=%s", grid_id, pending)
            self.db.update_grid(grid_id, status="FAILED", fail_reason=reason)
            raise GridCreationError(reason) from exc

    def _fee_value_usdt(self, trades: list[dict[str, Any]], symbol: str) -> Decimal:
        base_asset, quote_asset = symbol[:-4], symbol[-4:]
        total = Decimal(0)
        for trade in trades:
            commission = _d(trade["commission"])
            asset = trade["commission_asset"].upper()
            if commission == 0:
                continue
            if asset == quote_asset or asset == "USDT":
                total += commission
            elif asset == base_asset:
                total += commission * _d(trade["price"])
            else:
                total += commission * _d(self.exchange.get_avg_price(f"{asset}USDT"))
        return total

    def _place_level_intent(
        self,
        grid_id: int,
        level: dict,
        side: str,
        qty: Decimal,
        price: Decimal,
        filters: SymbolFilters,
        avg_price: Decimal,
    ) -> int:
        cycle = int(level["cycles_completed"])
        cid = self._client_order_id(grid_id, int(level["level_idx"]), side, cycle)
        symbol = self.db.get_grid(grid_id)["symbol"]
        self.db.update_level(
            grid_id, int(level["level_idx"]), state=f"{side}_OPEN",
            client_order_id=cid, order_id=None,
        )
        logger.info("grid=%s level=%s action=%s_INTENT client_order_id=%s", grid_id, level["level_idx"], side, cid)
        order = self._send_limit(
            symbol, side, qty, price, cid, filters, avg_price,
        )
        self.db.update_level(grid_id, int(level["level_idx"]), order_id=order["order_id"])
        logger.info("grid=%s level=%s action=%s_SENT order_id=%s", grid_id, level["level_idx"], side, order["order_id"])
        return int(order["order_id"])

    def _handle_buy_fill(
        self, grid: dict, level: dict, order: dict, filters: SymbolFilters, avg_price: Decimal,
    ) -> tuple[bool, bool]:
        symbol, grid_id, idx = grid["symbol"], int(grid["id"]), int(level["level_idx"])
        trades = self.exchange.get_my_trades(symbol, order["order_id"])
        base_asset = symbol[:-4]
        base_fee = sum(
            (_d(t["commission"]) for t in trades if t["commission_asset"].upper() == base_asset),
            Decimal(0),
        )
        gross_qty = _d(order["executed_qty"])
        held_qty = filters.round_qty_down(gross_qty - base_fee)
        fee_usdt = self._fee_value_usdt(trades, symbol)
        if held_qty < filters.min_qty:
            self.db.update_level(grid_id, idx, state="ERROR", held_qty=float(held_qty), fee_paid=float(_d(level["fee_paid"]) + fee_usdt))
            logger.error("grid=%s level=%s action=SELL_BLOCKED reason=held_qty_below_min", grid_id, idx)
            return True, True
        try:
            filters.validate_order("SELL", _d(level["sell_price"]), held_qty, avg_price)
        except FilterViolation as exc:
            self.db.update_level(
                grid_id, idx, state="ERROR", held_qty=float(held_qty),
                fee_paid=float(_d(level["fee_paid"]) + fee_usdt),
            )
            logger.error("grid=%s level=%s action=SELL_BLOCKED reason=%s", grid_id, idx, exc)
            return True, True
        self.db.update_level(
            grid_id, idx, held_qty=float(held_qty),
            fee_paid=float(_d(level["fee_paid"]) + fee_usdt),
        )
        try:
            self._place_level_intent(
                grid_id, level, "SELL", held_qty, _d(level["sell_price"]), filters, avg_price,
            )
            return True, False
        except (FilterViolation, TestnetOrderError) as exc:
            self.db.update_level(grid_id, idx, state="ERROR")
            logger.error("grid=%s level=%s action=SELL_FAILED reason=%s", grid_id, idx, exc)
            return True, True
        except (RuntimeError, BinanceRequestException, RequestException) as exc:
            logger.warning("grid=%s level=%s action=SELL_RETRY reason=%s", grid_id, idx, exc)
            return True, False

    def _handle_sell_fill(
        self, grid: dict, level: dict, sell_order: dict, bid: Decimal,
        filters: SymbolFilters, avg_price: Decimal,
    ) -> tuple[bool, bool]:
        grid_id, idx, symbol = int(grid["id"]), int(level["level_idx"]), grid["symbol"]
        cycle = int(level["cycles_completed"])
        buy_cid = self._client_order_id(grid_id, idx, "BUY", cycle)
        try:
            buy_order = self.exchange.get_order(symbol, client_order_id=buy_cid)
            buy_trades = self.exchange.get_my_trades(symbol, buy_order["order_id"])
            sell_trades = self.exchange.get_my_trades(symbol, sell_order["order_id"])
            buy_fee = self._fee_value_usdt(buy_trades, symbol)
            sell_fee = self._fee_value_usdt(sell_trades, symbol)
            cycle_pnl = (
                _d(sell_order["cummulative_quote_qty"])
                - _d(buy_order["cummulative_quote_qty"])
                - buy_fee - sell_fee
            )
        except (TestnetOrderError, RuntimeError, KeyError) as exc:
            sell_fee = Decimal(0)
            cycle_pnl = (
                _d(level["sell_price"]) * _d(sell_order["executed_qty"])
                - _d(level["price"]) * _d(sell_order["executed_qty"])
            )
            logger.warning("grid=%s level=%s action=PNL_ESTIMATE reason=%s", grid_id, idx, exc)
        new_cycle = cycle + 1
        self.db.update_level(
            grid_id, idx, cycles_completed=new_cycle,
            pnl=float(_d(level["pnl"]) + cycle_pnl), held_qty=0.0,
            fee_paid=float(_d(level["fee_paid"]) + sell_fee), order_id=None,
            client_order_id=None,
        )
        updated = next(row for row in self.db.get_grid_levels(grid_id) if row["level_idx"] == idx)
        if _d(level["price"]) < bid:
            updated["cycles_completed"] = new_cycle
            updated["symbol"] = symbol
            try:
                qty = filters.round_qty_down(_d(level["capital"]) / _d(level["price"]))
                self._place_level_intent(
                    grid_id, updated, "BUY", qty, _d(level["price"]), filters, avg_price,
                )
                return True, False
            except Exception as exc:
                self.db.update_level(grid_id, idx, state="ERROR")
                logger.error("grid=%s level=%s action=REARM_FAILED reason=%s", grid_id, idx, exc)
                return True, True
        self.db.update_level(grid_id, idx, state="IDLE")
        return True, False

    def sync_grid(self, grid_id: int) -> dict[str, Any]:
        grid = self.db.get_grid(grid_id)
        if grid is None:
            raise ValueError(f"grid {grid_id} does not exist")
        summary = {"buys_filled": 0, "sells_filled": 0, "cycles_completed": 0,
                   "orders_placed": 0, "errors": 0, "states": {}}
        if grid["status"] != "ACTIVE":
            summary["states"] = {}
            return summary
        symbol = grid["symbol"]
        filters, snapshot, avg_price = self._market_context(symbol)
        bid = snapshot["bid_price"]

        # Recover write-ahead intents before taking the single open-order snapshot.
        for level in self.db.get_grid_levels(grid_id):
            cid = level.get("client_order_id")
            if not cid or level.get("order_id") is not None or level["state"] not in {"BUY_OPEN", "SELL_OPEN"}:
                continue
            try:
                found = self.exchange.find_order_by_client_id(symbol, cid)
                if found is not None:
                    self.db.update_level(grid_id, level["level_idx"], order_id=found["order_id"])
                    continue
                side = "BUY" if level["state"] == "BUY_OPEN" else "SELL"
                qty = (
                    filters.round_qty_down(_d(level["capital"]) / _d(level["price"]))
                    if side == "BUY" else filters.round_qty_down(_d(level["held_qty"]))
                )
                order = self._send_limit(
                    symbol, side, qty,
                    _d(level["price"] if side == "BUY" else level["sell_price"]),
                    cid, filters, avg_price,
                )
                self.db.update_level(grid_id, level["level_idx"], order_id=order["order_id"])
                summary["orders_placed"] += 1
            except (FilterViolation, TestnetOrderError) as exc:
                self.db.update_level(grid_id, level["level_idx"], state="ERROR")
                summary["errors"] += 1
                logger.error("grid=%s level=%s action=INTENT_RECOVERY_FAILED reason=%s", grid_id, level["level_idx"], exc)
            except (RuntimeError, BinanceRequestException, RequestException) as exc:
                logger.warning("grid=%s level=%s action=INTENT_RECOVERY_RETRY reason=%s", grid_id, level["level_idx"], exc)

        open_orders = self.exchange.get_open_orders(symbol)
        open_ids = {int(row["order_id"]) for row in open_orders}
        for level in self.db.get_grid_levels(grid_id):
            if level["state"] not in {"BUY_OPEN", "SELL_OPEN"} or level.get("order_id") is None:
                continue
            order_id = int(level["order_id"])
            if order_id in open_ids:
                continue
            try:
                order = self.exchange.get_order(symbol, order_id=order_id)
            except Exception as exc:
                self.db.update_level(grid_id, level["level_idx"], state="ERROR")
                summary["errors"] += 1
                logger.error("grid=%s level=%s action=ORDER_LOOKUP_FAILED reason=%s", grid_id, level["level_idx"], exc)
                continue
            status = str(order["status"]).upper()
            if status == "PARTIALLY_FILLED" or status == "NEW":
                continue
            if status in {"CANCELED", "EXPIRED", "REJECTED"}:
                self.db.update_level(grid_id, level["level_idx"], state="ERROR")
                summary["errors"] += 1
                continue
            if status != "FILLED":
                continue
            if level["state"] == "BUY_OPEN":
                changed, error = self._handle_buy_fill(grid, level, order, filters, avg_price)
                summary["buys_filled"] += int(changed)
            else:
                changed, error = self._handle_sell_fill(grid, level, order, bid, filters, avg_price)
                summary["sells_filled"] += int(changed)
                summary["cycles_completed"] += int(changed and not error)
            summary["errors"] += int(error)
            summary["orders_placed"] += int(changed and not error and self.db.get_grid_levels(grid_id)[level["level_idx"]]["state"] in {"BUY_OPEN", "SELL_OPEN"})

        current_levels = self.db.get_grid_levels(grid_id)
        if len(open_orders) + summary["orders_placed"] < filters.max_num_orders:
            for level in current_levels:
                if level["state"] != "IDLE" or _d(level["price"]) >= bid:
                    continue
                qty = filters.round_qty_down(_d(level["capital"]) / _d(level["price"]))
                try:
                    self._place_level_intent(grid_id, {**level, "symbol": symbol}, "BUY", qty, _d(level["price"]), filters, avg_price)
                    summary["orders_placed"] += 1
                except Exception as exc:
                    self.db.update_level(grid_id, level["level_idx"], state="ERROR")
                    summary["errors"] += 1
                    logger.error("grid=%s level=%s action=IDLE_ARM_FAILED reason=%s", grid_id, level["level_idx"], exc)
        summary["states"] = {}
        for level in self.db.get_grid_levels(grid_id):
            summary["states"][level["state"]] = summary["states"].get(level["state"], 0) + 1
        return summary

    def cancel_grid_orders(self, grid_id: int) -> dict[str, Any]:
        grid = self.db.get_grid(grid_id)
        if grid is None:
            raise ValueError(f"grid {grid_id} does not exist")
        symbol = grid["symbol"]
        levels = self.db.get_grid_levels(grid_id)
        errors, owned_ids = [], set()
        # Resolve write-ahead intents before cancellation; an accepted order can
        # exist at the exchange even when its order_id was not persisted locally.
        for level in levels:
            if level.get("order_id") is None and level.get("client_order_id"):
                try:
                    found = self.exchange.find_order_by_client_id(symbol, level["client_order_id"])
                    if found is not None:
                        self.db.update_level(
                            grid_id, level["level_idx"], order_id=found["order_id"],
                        )
                        level["order_id"] = found["order_id"]
                except Exception as exc:
                    errors.append({"client_order_id": level["client_order_id"], "reason": str(exc)})
            if level.get("order_id") is not None:
                owned_ids.add(int(level["order_id"]))
        open_orders = self.exchange.get_open_orders(symbol)
        open_ids = {int(order["order_id"]) for order in open_orders}
        canceled, filled_during_cancel = [], []
        for level in levels:
            order_id = level.get("order_id")
            if order_id is None:
                continue
            if int(order_id) not in open_ids:
                try:
                    status = self.exchange.get_order(symbol, order_id=int(order_id))["status"]
                    if status == "FILLED":
                        filled_during_cancel.append(int(order_id))
                except Exception as exc:
                    logger.warning("grid=%s level=%s action=CANCEL_STATUS_UNKNOWN order_id=%s reason=%s", grid_id, level["level_idx"], order_id, exc)
                continue
            try:
                result = self.exchange.cancel_order(symbol, int(order_id))
                if result.get("status") == "CANCELED":
                    canceled.append(int(order_id))
                elif result.get("status") == "FILLED":
                    filled_during_cancel.append(int(order_id))
                else:
                    errors.append({"order_id": int(order_id), "reason": f"cancel returned {result.get('status')}"})
            except Exception as exc:
                errors.append({"order_id": int(order_id), "reason": str(exc)})
                try:
                    status = self.exchange.get_order(symbol, order_id=int(order_id))["status"]
                    if status == "FILLED":
                        filled_during_cancel.append(int(order_id))
                except Exception:
                    pass
        still_open = owned_ids & {
            int(order["order_id"]) for order in self.exchange.get_open_orders(symbol)
        }
        new_status = "CANCELLED" if not errors and not still_open else grid["status"]
        fail_reason = None
        if errors or still_open:
            fail_reason = "cancel incomplete"
            if still_open:
                fail_reason += f"; live order ids: {','.join(map(str, sorted(still_open)))}"
        updates = {"status": new_status, "fail_reason": fail_reason}
        if new_status == "CANCELLED":
            updates["closed_at"] = datetime.now(timezone.utc).replace(tzinfo=None)
        self.db.update_grid(grid_id, **updates)
        inventory = [
            {"level_idx": row["level_idx"], "asset": symbol[:-4], "held_qty": row["held_qty"]}
            for row in self.db.get_grid_levels(grid_id)
            if row["state"] == "SELL_OPEN" and _d(row["held_qty"]) > 0
        ]
        return {
            "grid_id": grid_id, "status": new_status, "canceled_order_ids": canceled,
            "filled_during_cancel": filled_during_cancel, "cancel_errors": errors,
            "pending_order_ids": sorted(still_open),
            "remaining_inventory": inventory,
        }
