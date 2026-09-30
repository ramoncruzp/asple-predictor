"""Recoverable pull-based spot grid engine over an injected exchange client."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from binance.exceptions import BinanceRequestException
from requests.exceptions import RequestException
from sqlalchemy import select

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
    def __init__(
        self, db: Any, exchange: Any, settings: Any, environment: str = "testnet",
        event_sink: Any = None,
    ):
        if environment != "testnet":
            raise ValueError("GridEngine only supports the testnet environment in this phase")
        self.db = db
        self.exchange = exchange
        self.settings = settings
        self.environment = environment
        self.event_sink = event_sink
        self._event_price: float | None = None

    def _emit(
        self, event_type: str, grid_id: int, level_idx: int | None = None, *,
        client_order_id: str | None = None, order_id: int | None = None,
        reason: str | None = None, details: dict | None = None,
    ) -> None:
        if self.event_sink is None:
            return
        event = {
            "event_type": str(event_type), "grid_id": grid_id, "level_idx": level_idx,
            "client_order_id": client_order_id, "order_id": order_id, "reason": reason,
            "price": self._event_price, "details": details or {},
        }
        try:
            self.event_sink(event)
        except Exception:
            logger.exception("grid=%s event sink failed for %s", grid_id, event_type)

    @staticmethod
    def _buy_client_order_id(grid_id: int, level_idx: int, cycle: int, sell_client_order_id: str | None = None) -> str:
        if sell_client_order_id:
            match = re.fullmatch(r"g(\d+)L(\d+)S(\d+)", sell_client_order_id)
            if match and (int(match.group(1)) != grid_id or int(match.group(2)) != level_idx):
                return f"g{match.group(1)}L{match.group(2)}B{match.group(3)}"
        return GridEngine._client_order_id(grid_id, level_idx, "BUY", cycle)

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
        self._event_price = float((snapshot["bid_price"] + snapshot["ask_price"]) / Decimal(2))
        open_orders = self.exchange.get_open_orders(symbol)
        if filters.max_num_orders is not None and len(open_orders) >= filters.max_num_orders:
            raise GridConfigError("exchange MAX_NUM_ORDERS is already reached")
        lines = compute_lines(range_low, range_high, n_levels, filters)
        plans = plan_cells(lines, capital_total, snapshot, filters, self.settings)
        buy_plans = [plan for plan in plans if plan.initial_state == "BUY_OPEN"]
        if filters.max_num_orders is not None and len(open_orders) + len(buy_plans) > filters.max_num_orders:
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
                self._emit(
                    "BUY_PLACED", grid_id, plan.level_idx, client_order_id=cid,
                    order_id=order["order_id"], details={"qty": str(plan.qty), "price": str(plan.buy_price)},
                )
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
                        cancel_status = str(canceled.get("status", "")).upper()
                        if cancel_status not in {"CANCELED", "EXPIRED", "REJECTED"}:
                            pending.append(str(order_id))
                        elif cancel_status in {"CANCELED", "EXPIRED", "REJECTED"}:
                            self.db.update_level(grid_id, level_idx, state="IDLE", order_id=None)
                        else:
                            self.db.update_level(grid_id, level_idx, state="ERROR")
                    else:
                        self.db.update_level(grid_id, level_idx, state="IDLE", order_id=None)
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
        self._emit(
            f"{side}_PLACED", grid_id, int(level["level_idx"]), client_order_id=cid,
            order_id=order["order_id"], details={"qty": str(qty), "price": str(price)},
        )
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
        self._emit(
            "BUY_FILLED", grid_id, idx, client_order_id=level.get("client_order_id"),
            order_id=order["order_id"], details={
                "executed_qty": str(gross_qty), "held_qty": str(held_qty),
                "fee_usdt": str(fee_usdt), "base_fee": str(base_fee),
            },
        )
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
        filters: SymbolFilters, avg_price: Decimal, rearm: bool = True,
    ) -> tuple[bool, bool]:
        grid_id, idx, symbol = int(grid["id"]), int(level["level_idx"]), grid["symbol"]
        cycle = int(level["cycles_completed"])
        buy_cid = self._buy_client_order_id(
            grid_id, idx, cycle, level.get("client_order_id"),
        )
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
        self._emit(
            "SELL_FILLED", grid_id, idx, client_order_id=level.get("client_order_id"),
            order_id=sell_order["order_id"], details={
                "cycle_pnl": str(cycle_pnl), "cycles_completed": new_cycle,
                "executed_qty": str(sell_order.get("executed_qty", 0)),
            },
        )
        updated = next(row for row in self.db.get_grid_levels(grid_id) if row["level_idx"] == idx)
        if not rearm:
            self.db.update_level(grid_id, idx, state="DONE")
            return True, False
        if _d(level["price"]) < bid:
            updated["cycles_completed"] = new_cycle
            updated["symbol"] = symbol
            try:
                qty = filters.round_qty_down(_d(level["capital"]) / _d(level["price"]))
                self._place_level_intent(
                    grid_id, updated, "BUY", qty, _d(level["price"]), filters, avg_price,
                )
                return True, False
            except (RuntimeError, BinanceRequestException, RequestException) as exc:
                logger.warning("grid=%s level=%s action=REARM_RETRY reason=%s", grid_id, idx, exc)
                return True, False
            except (FilterViolation, TestnetOrderError) as exc:
                self.db.update_level(grid_id, idx, state="ERROR")
                logger.error("grid=%s level=%s action=REARM_FAILED reason=%s", grid_id, idx, exc)
                return True, True
            except Exception as exc:
                self.db.update_level(grid_id, idx, state="ERROR")
                logger.exception("grid=%s level=%s action=REARM_FAILED reason=%s", grid_id, idx, exc)
                return True, True
        self.db.update_level(grid_id, idx, state="IDLE")
        return True, False

    def sync_grid(self, grid_id: int) -> dict[str, Any]:
        return self._sync_grid(grid_id, rearm=True, allowed_statuses={"ACTIVE"})

    def sync_closing(self, grid_id: int) -> dict[str, Any]:
        return self._sync_grid(grid_id, rearm=False, allowed_statuses={"CLOSING"})

    def sync_repository(self, grid_id: int) -> dict[str, Any]:
        summary = self._sync_grid(
            grid_id, rearm=False, allowed_statuses={"HOLDING"}, repository_only=True,
        )
        grid = self.db.get_grid(grid_id)
        if grid is None or grid["status"] != "HOLDING":
            return summary
        active_states = {"SELL_OPEN", "BUY_OPEN", "IDLE"}
        if not any(row["state"] in active_states for row in self.db.get_grid_levels(grid_id)):
            self.db.update_grid(grid_id, status="CLOSED", closed_at=datetime.now(timezone.utc).replace(tzinfo=None))
            self._emit("REPOSITORY_EMPTY_CLOSED", grid_id, details={"summary": summary})
        summary["states"] = {}
        for row in self.db.get_grid_levels(grid_id):
            summary["states"][row["state"]] = summary["states"].get(row["state"], 0) + 1
        return summary

    def _sync_grid(
        self, grid_id: int, *, rearm: bool, allowed_statuses: set[str],
        repository_only: bool = False,
    ) -> dict[str, Any]:
        grid = self.db.get_grid(grid_id)
        if grid is None:
            raise ValueError(f"grid {grid_id} does not exist")
        summary = {"buys_filled": 0, "sells_filled": 0, "cycles_completed": 0,
                   "orders_placed": 0, "errors": 0, "states": {}}
        if grid["status"] not in allowed_statuses:
            summary["states"] = {}
            return summary
        symbol = grid["symbol"]
        filters, snapshot, avg_price = self._market_context(symbol)
        self._event_price = float((snapshot["bid_price"] + snapshot["ask_price"]) / Decimal(2))
        bid = snapshot["bid_price"]

        # Recover write-ahead intents before taking the single open-order snapshot.
        for level in self.db.get_grid_levels(grid_id):
            if repository_only and level["state"] != "SELL_OPEN":
                continue
            cid = level.get("client_order_id")
            if not cid or level.get("order_id") is not None or level["state"] not in {"BUY_OPEN", "SELL_OPEN"}:
                continue
            try:
                found = self.exchange.find_order_by_client_id(symbol, cid)
                if found is not None:
                    self.db.update_level(grid_id, level["level_idx"], order_id=found["order_id"])
                    self._emit("INTENT_RECOVERED", grid_id, level["level_idx"], client_order_id=cid,
                               order_id=found["order_id"], details={"found_existing": True})
                    continue
                if not rearm and level["state"] == "BUY_OPEN":
                    self.db.update_level(
                        grid_id, level["level_idx"], state="IDLE",
                        order_id=None, client_order_id=None,
                    )
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
                self._emit("INTENT_RECOVERED", grid_id, level["level_idx"], client_order_id=cid,
                           order_id=order["order_id"], details={"found_existing": False})
            except (FilterViolation, TestnetOrderError) as exc:
                self.db.update_level(grid_id, level["level_idx"], state="ERROR")
                self._emit("LEVEL_ERROR", grid_id, level["level_idx"], client_order_id=cid,
                           order_id=level.get("order_id"), reason=str(exc))
                summary["errors"] += 1
                logger.error("grid=%s level=%s action=INTENT_RECOVERY_FAILED reason=%s", grid_id, level["level_idx"], exc)
            except (RuntimeError, BinanceRequestException, RequestException) as exc:
                logger.warning("grid=%s level=%s action=INTENT_RECOVERY_RETRY reason=%s", grid_id, level["level_idx"], exc)

        open_orders = self.exchange.get_open_orders(symbol)
        open_ids = {int(row["order_id"]) for row in open_orders}
        for level in self.db.get_grid_levels(grid_id):
            if repository_only and level["state"] != "SELL_OPEN":
                continue
            if level["state"] not in {"BUY_OPEN", "SELL_OPEN"} or level.get("order_id") is None:
                continue
            order_id = int(level["order_id"])
            if order_id in open_ids:
                continue
            try:
                order = self.exchange.get_order(symbol, order_id=order_id)
            except (RuntimeError, BinanceRequestException, RequestException) as exc:
                summary["errors"] += 1
                logger.warning("grid=%s level=%s action=ORDER_LOOKUP_RETRY reason=%s", grid_id, level["level_idx"], exc)
                continue
            except Exception as exc:
                self.db.update_level(grid_id, level["level_idx"], state="ERROR")
                self._emit("LEVEL_ERROR", grid_id, level["level_idx"], client_order_id=level.get("client_order_id"),
                           order_id=order_id, reason=str(exc))
                summary["errors"] += 1
                logger.error("grid=%s level=%s action=ORDER_LOOKUP_FAILED reason=%s", grid_id, level["level_idx"], exc)
                continue
            status = str(order["status"]).upper()
            if status == "PARTIALLY_FILLED" or status == "NEW":
                continue
            if status in {"CANCELED", "EXPIRED", "REJECTED"}:
                self._emit(
                    "ORDER_CANCELLED_EXTERNALLY", grid_id, level["level_idx"],
                    client_order_id=level.get("client_order_id"), order_id=order_id,
                    reason=status,
                )
                if not rearm and status == "CANCELED":
                    self.db.update_level(grid_id, level["level_idx"], state="IDLE", order_id=None, client_order_id=None)
                else:
                    self.db.update_level(grid_id, level["level_idx"], state="ERROR")
                    self._emit("LEVEL_ERROR", grid_id, level["level_idx"], order_id=order_id, reason=status)
                    summary["errors"] += 1
                continue
            if status != "FILLED":
                continue
            if level["state"] == "BUY_OPEN":
                changed, error = self._handle_buy_fill(grid, level, order, filters, avg_price)
                summary["buys_filled"] += int(changed)
            else:
                changed, error = self._handle_sell_fill(grid, level, order, bid, filters, avg_price, rearm=rearm)
                summary["sells_filled"] += int(changed)
                summary["cycles_completed"] += int(changed and not error)
            summary["errors"] += int(error)
            current = self.db.get_grid_levels(grid_id)[level["level_idx"]]
            summary["orders_placed"] += int(
                changed and not error and current["state"] in {"BUY_OPEN", "SELL_OPEN"}
                and current.get("order_id") is not None
            )

        current_levels = self.db.get_grid_levels(grid_id)
        if rearm and not repository_only and (
            filters.max_num_orders is None or len(open_orders) + summary["orders_placed"] < filters.max_num_orders
        ):
            for level in current_levels:
                if level["state"] != "IDLE" or _d(level["price"]) >= bid:
                    continue
                qty = filters.round_qty_down(_d(level["capital"]) / _d(level["price"]))
                try:
                    self._place_level_intent(grid_id, {**level, "symbol": symbol}, "BUY", qty, _d(level["price"]), filters, avg_price)
                    summary["orders_placed"] += 1
                except (RuntimeError, BinanceRequestException, RequestException) as exc:
                    summary["errors"] += 1
                    logger.warning("grid=%s level=%s action=IDLE_ARM_RETRY reason=%s", grid_id, level["level_idx"], exc)
                except Exception as exc:
                    self.db.update_level(grid_id, level["level_idx"], state="ERROR")
                    self._emit("LEVEL_ERROR", grid_id, level["level_idx"], reason=str(exc))
                    summary["errors"] += 1
                    logger.error("grid=%s level=%s action=IDLE_ARM_FAILED reason=%s", grid_id, level["level_idx"], exc)
        summary["states"] = {}
        for level in self.db.get_grid_levels(grid_id):
            summary["states"][level["state"]] = summary["states"].get(level["state"], 0) + 1
        return summary

    def cancel_grid_orders(self, grid_id: int, *, finalize: bool = True) -> dict[str, Any]:
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

        def capture_filled_buy(level: dict, order_id: int, order: dict | None = None) -> None:
            if level.get("state") != "BUY_OPEN":
                return
            if order is None:
                order = self.exchange.get_order(symbol, order_id=int(order_id))
            filled_during_cancel.append({
                "grid_id": grid_id, "level_idx": int(level["level_idx"]),
                "order_id": int(order_id), "client_order_id": level.get("client_order_id"),
                "executed_qty": str(_d(order.get("executed_qty", 0))),
                "has_sell": bool(
                    level.get("state") == "SELL_OPEN"
                    and _d(level.get("held_qty", 0)) > 0
                    and level.get("order_id") is not None
                ),
            })
        for level in levels:
            order_id = level.get("order_id")
            if order_id is None:
                continue
            if int(order_id) not in open_ids:
                try:
                    order = self.exchange.get_order(symbol, order_id=int(order_id))
                    if str(order["status"]).upper() == "FILLED":
                        capture_filled_buy(level, int(order_id), order)
                except Exception as exc:
                    logger.warning("grid=%s level=%s action=CANCEL_STATUS_UNKNOWN order_id=%s reason=%s", grid_id, level["level_idx"], order_id, exc)
                continue
            try:
                result = self.exchange.cancel_order(symbol, int(order_id))
                if result.get("status") == "CANCELED":
                    canceled.append(int(order_id))
                elif result.get("status") == "FILLED":
                    capture_filled_buy(level, int(order_id))
                else:
                    errors.append({"order_id": int(order_id), "reason": f"cancel returned {result.get('status')}"})
            except Exception as exc:
                errors.append({"order_id": int(order_id), "reason": str(exc)})
                try:
                    order = self.exchange.get_order(symbol, order_id=int(order_id))
                    if str(order["status"]).upper() == "FILLED":
                        capture_filled_buy(level, int(order_id), order)
                except Exception:
                    pass
        still_open = owned_ids & {
            int(order["order_id"]) for order in self.exchange.get_open_orders(symbol)
        }
        new_status = "CANCELLED" if finalize and not errors and not still_open else grid["status"]
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

    def _close_event(
        self, event_type: str, grid_id: int, details: dict,
        reason: str | None = None, price: Decimal | float | None = None,
        level_idx: int | None = None, client_order_id: str | None = None,
        order_id: int | None = None,
    ) -> None:
        self.db.add_grid_event(
            run_id=None, source="CLI", grid_id=grid_id, level_idx=level_idx,
            client_order_id=client_order_id, order_id=order_id,
            event_type=event_type, reason=reason,
            price=None if price is None else float(price), details=details,
        )

    def _market_sell_owned_cell(
        self, grid: dict, level: dict, filters: SymbolFilters, avg_price: Decimal,
    ) -> dict:
        grid_id, idx, symbol = int(grid["id"]), int(level["level_idx"]), grid["symbol"]
        held_qty = _d(level.get("held_qty"))
        qty = filters.round_qty_down(held_qty)
        if qty < filters.min_qty:
            result = {"level_idx": idx, "qty": str(held_qty), "status": "DUST"}
            self.db.update_level(grid_id, idx, state="DONE")
            self._close_event("CELL_DUST", grid_id, result, reason="below market minimum notional", level_idx=idx)
            return result
        if filters.max_qty > 0 and qty > filters.max_qty:
            raise FilterViolation("LOT_SIZE", f"market liquidation quantity {qty} exceeds maxQty")
        if filters.apply_min_to_market and qty * avg_price < filters.min_notional:
            result = {"level_idx": idx, "qty": str(held_qty), "status": "DUST"}
            self.db.update_level(grid_id, idx, state="DONE")
            self._close_event("CELL_DUST", grid_id, result, reason="below market minimum notional", level_idx=idx)
            return result

        cycle = int(level["cycles_completed"])
        cid = f"g{grid_id}L{idx}X{cycle}"
        existing = self.exchange.find_order_by_client_id(symbol, cid)
        if existing is None:
            accepted_order = self.exchange.place_order(
                symbol, "SELL", qty, order_type="MARKET", client_order_id=cid,
            )
            market_order = self.exchange.get_order(symbol, order_id=accepted_order["order_id"])
        else:
            market_order = self.exchange.get_order(symbol, order_id=existing["order_id"])
        if str(market_order.get("status", "")).upper() != "FILLED":
            market_order = self.exchange.get_order(symbol, order_id=market_order["order_id"])
        if str(market_order.get("status", "")).upper() != "FILLED":
            raise RuntimeError(f"market liquidation {cid} is not FILLED")

        buy_cid = self._buy_client_order_id(
            grid_id, idx, cycle, level.get("client_order_id"),
        )
        buy_order = self.exchange.get_order(symbol, client_order_id=buy_cid)
        buy_trades = self.exchange.get_my_trades(symbol, buy_order["order_id"])
        sell_trades = self.exchange.get_my_trades(symbol, market_order["order_id"])
        buy_fee = self._fee_value_usdt(buy_trades, symbol)
        sell_fee = self._fee_value_usdt(sell_trades, symbol)
        cycle_pnl = (
            _d(market_order["cummulative_quote_qty"])
            - _d(buy_order["cummulative_quote_qty"]) - buy_fee - sell_fee
        )
        self.db.update_level(
            grid_id, idx, state="DONE", cycles_completed=cycle,
            pnl=float(_d(level.get("pnl")) + cycle_pnl), fee_paid=float(_d(level.get("fee_paid")) + sell_fee),
            held_qty=0.0, order_id=market_order["order_id"], client_order_id=cid,
        )
        result = {
            "level_idx": idx, "client_order_id": cid, "order_id": int(market_order["order_id"]),
            "qty": str(_d(market_order["executed_qty"])), "cycle_pnl": str(cycle_pnl),
            "pnl_realized": str(cycle_pnl), "cycles_completed": cycle, "status": "FILLED",
        }
        self._close_event(
            "CELL_LIQUIDATED", grid_id, result, level_idx=idx,
            client_order_id=cid, order_id=int(market_order["order_id"]),
        )
        return result

    def _close_repository(self, grid_id: int) -> dict:
        grid = self.db.get_grid(grid_id)
        symbol = grid["symbol"]
        errors: list[dict] = []
        # Cancel only pending buys. A fill racing cancellation is synchronized
        # without rearming, which can create the corresponding live sell.
        for level in self.db.get_grid_levels(grid_id):
            if level["state"] != "BUY_OPEN":
                continue
            order_id = level.get("order_id")
            if order_id is None and level.get("client_order_id"):
                found = self.exchange.find_order_by_client_id(symbol, level["client_order_id"])
                order_id = found["order_id"] if found else None
            if order_id is None:
                self.db.update_level(grid_id, level["level_idx"], state="IDLE", order_id=None)
                continue
            try:
                result = self.exchange.cancel_order(symbol, int(order_id))
                status = str(result.get("status", "")).upper()
                if status == "FILLED":
                    self.sync_closing(grid_id)
                elif status in {"CANCELED", "EXPIRED", "REJECTED"}:
                    self.db.update_level(grid_id, level["level_idx"], state="IDLE", order_id=None)
                else:
                    errors.append({"level_idx": level["level_idx"], "reason": f"buy cancel returned {status}"})
            except Exception as exc:
                errors.append({"level_idx": level["level_idx"], "reason": str(exc)})
        if errors:
            reason = "repository close aborted; buy cancellation failed"
            self.db.update_grid(grid_id, status="CLOSING", fail_reason=reason)
            return {"mode": "repository", "status": "CLOSING", "moved_cells": [],
                    "unmanaged_inventory": [], "errors": errors, "pnl_realized": 0.0}

        movable, unmanaged = [], []
        lookup_errors: list[dict] = []
        market_mid = None
        try:
            book = self.exchange.get_book_ticker(symbol)
            market_mid = (_d(book["bid_price"]) + _d(book["ask_price"])) / 2
        except Exception:
            logger.warning("grid=%s repository movement price unavailable", grid_id, exc_info=True)
        for level in self.db.get_grid_levels(grid_id):
            if _d(level.get("held_qty")) <= 0:
                continue
            live_sell = False
            if level["state"] == "SELL_OPEN" and level.get("order_id") is not None:
                try:
                    order = self.exchange.get_order(symbol, order_id=level["order_id"])
                    status = str(order["status"]).upper()
                    if status in {"NEW", "PARTIALLY_FILLED"}:
                        live_sell = True
                    elif status not in {"FILLED", "CANCELED", "EXPIRED", "REJECTED"}:
                        lookup_errors.append({"level_idx": level["level_idx"], "reason": f"unknown sell status {status}"})
                except TestnetOrderError as exc:
                    if exc.code != -2013:
                        lookup_errors.append({"level_idx": level["level_idx"], "reason": str(exc)})
                except (RuntimeError, BinanceRequestException, RequestException) as exc:
                    lookup_errors.append({"level_idx": level["level_idx"], "reason": str(exc)})
                except Exception as exc:
                    lookup_errors.append({"level_idx": level["level_idx"], "reason": str(exc)})
            if live_sell:
                movable.append(level)
            else:
                unmanaged.append({"level_idx": level["level_idx"], "held_qty": level["held_qty"]})

        if lookup_errors:
            reason = "repository close aborted; live sell status unavailable"
            self.db.update_grid(grid_id, status="CLOSING", fail_reason=reason)
            return {"mode": "repository", "status": "CLOSING", "moved_cells": [],
                    "unmanaged_inventory": [], "errors": lookup_errors, "pnl_realized": 0.0}

        repositories = [row for row in self.db.list_grids_by_status({"HOLDING"}) if row["symbol"] == symbol]
        now = datetime.now(timezone.utc)
        db_now = now.replace(tzinfo=None) if self.db.engine.dialect.name == "sqlite" else now
        moved_cells = []
        repository_id = int(repositories[0]["id"]) if repositories else None
        with self.db.engine.begin() as conn:
            if repository_id is None and movable:
                repository_id = int(conn.execute(self.db.grids.insert().values(
                    symbol=symbol, range_low=min(float(row["price"]) for row in movable),
                    range_high=max(float(row["sell_price"]) for row in movable),
                    n_levels=0, capital_total=0.0, status="HOLDING", created_at=db_now,
                    environment=grid["environment"], open_price=grid.get("open_price"),
                )).inserted_primary_key[0])
                conn.execute(self.db.grid_events.insert().values(
                    run_id=None, source="CLI", ts=db_now, grid_id=repository_id,
                    event_type="REPOSITORY_CREATED", details=self.db._json({"symbol": symbol, "origin_grid_id": grid_id}),
                ))
            if repository_id is not None:
                existing_rows = conn.execute(select(self.db.grid_levels).where(
                    self.db.grid_levels.c.grid_id == repository_id,
                )).mappings().all()
                next_idx = max((int(row["level_idx"]) for row in existing_rows), default=-1) + 1
                for level in movable:
                    new_idx = next_idx
                    next_idx += 1
                    self.db.move_level_to_grid(grid_id, int(level["level_idx"]), repository_id, new_idx, conn=conn)
                    detail = {
                        "origin_grid_id": grid_id, "origin_level_idx": int(level["level_idx"]),
                        "pnl": level.get("pnl", 0), "cycles_completed": level.get("cycles_completed", 0),
                        "held_qty": level["held_qty"], "sell_price": level["sell_price"],
                        "buy_client_order_id": self._buy_client_order_id(
                            grid_id, int(level["level_idx"]), int(level["cycles_completed"]), level.get("client_order_id"),
                        ),
                        "moved_at": now.isoformat(),
                        "market_mid": None if market_mid is None else str(market_mid),
                    }
                    conn.execute(self.db.grid_events.insert().values(
                        run_id=None, source="CLI", ts=db_now, grid_id=repository_id,
                        level_idx=new_idx, client_order_id=level.get("client_order_id"),
                        order_id=level.get("order_id"), event_type="CELL_MOVED_TO_REPOSITORY",
                        price=None if market_mid is None else float(market_mid), details=self.db._json(detail),
                    ))
                    moved_cells.append({"origin_level_idx": level["level_idx"], "level_idx": new_idx,
                                        "client_order_id": level.get("client_order_id"),
                                        "order_id": level.get("order_id"), "held_qty": level["held_qty"]})
                repo_levels = conn.execute(select(self.db.grid_levels).where(
                    self.db.grid_levels.c.grid_id == repository_id,
                )).mappings().all()
                if repo_levels:
                    conn.execute(self.db.grids.update().where(self.db.grids.c.id == repository_id).values(
                        range_low=min(float(row["price"]) for row in repo_levels),
                        range_high=max(float(row["sell_price"]) for row in repo_levels),
                        n_levels=len(repo_levels), capital_total=sum(float(row["capital"]) for row in repo_levels),
                    ))
            reason = None if not unmanaged else "unmanaged inventory: " + json.dumps(unmanaged, default=str)
            conn.execute(self.db.grids.update().where(self.db.grids.c.id == grid_id).values(
                status="CLOSED", closed_at=db_now, fail_reason=reason,
            ))
        return {"mode": "repository", "status": "CLOSED", "repository_grid_id": repository_id,
                "moved_cells": moved_cells, "unmanaged_inventory": unmanaged, "errors": [],
                "pnl_realized": sum(float(row.get("pnl", 0)) for row in movable)}

    def close_grid(self, grid_id: int, mode: str) -> dict:
        mode = str(mode).lower()
        if mode not in {"cancel", "liquidate", "repository"}:
            raise ValueError("mode must be cancel, liquidate, or repository")
        grid = self.db.get_grid(grid_id)
        if grid is None:
            raise ValueError(f"grid {grid_id} does not exist")
        if not self.db.transition_grid_status(grid_id, {"ACTIVE", "PAUSED", "CLOSING"}, "CLOSING"):
            raise ValueError(f"grid {grid_id} cannot close from status {grid['status']}")
        self._close_event("GRID_CLOSE_STARTED", grid_id, {"mode": mode})
        try:
            self.sync_closing(grid_id)
        except Exception as exc:
            logger.exception("grid=%s close synchronization failed", grid_id)
            self.db.update_grid(grid_id, status="CLOSING", fail_reason=f"sync_closing failed: {exc}")

        if mode == "cancel":
            canceled = self.cancel_grid_orders(grid_id)
            result = {
                "mode": mode, "status": canceled["status"],
                "canceled_order_ids": canceled["canceled_order_ids"],
                "unmanaged_inventory": canceled["remaining_inventory"],
                "errors": canceled["cancel_errors"], "pnl_realized": 0.0,
            }
        elif mode == "repository":
            result = self._close_repository(grid_id)
        else:
            cancellation = self.cancel_grid_orders(grid_id, finalize=False)
            errors = list(cancellation["cancel_errors"])
            liquidated, dust = [], []
            filters, _, avg_price = self._market_context(grid["symbol"])
            for level in self.db.get_grid_levels(grid_id):
                if _d(level.get("held_qty")) <= 0:
                    if level["state"] != "ERROR":
                        self.db.update_level(grid_id, level["level_idx"], state="DONE")
                    continue
                if level.get("order_id") is not None:
                    try:
                        exchange_order = self.exchange.get_order(grid["symbol"], order_id=level["order_id"])
                        status = str(exchange_order["status"]).upper()
                        if status == "FILLED":
                            self.sync_closing(grid_id)
                            refreshed = next(row for row in self.db.get_grid_levels(grid_id) if row["level_idx"] == level["level_idx"])
                            if _d(refreshed.get("held_qty")) <= 0:
                                continue
                            level = refreshed
                        elif status in {"NEW", "PARTIALLY_FILLED"}:
                            errors.append({"level_idx": level["level_idx"], "reason": "sell order remains live"})
                            continue
                    except Exception as exc:
                        errors.append({"level_idx": level["level_idx"], "reason": f"sell status unknown: {exc}"})
                        continue
                try:
                    sold = self._market_sell_owned_cell(grid, level, filters, avg_price)
                    (dust if sold["status"] == "DUST" else liquidated).append(sold)
                except Exception as exc:
                    errors.append({"level_idx": level["level_idx"], "reason": str(exc)})
                    self.db.update_grid(grid_id, status="CLOSING", fail_reason=str(exc))
                    logger.exception("grid=%s level=%s liquidation failed", grid_id, level["level_idx"])
            if not errors:
                self.db.update_grid(grid_id, status="CLOSED", closed_at=datetime.now(timezone.utc).replace(tzinfo=None), fail_reason=None)
            result = {
                "mode": mode, "status": self.db.get_grid(grid_id)["status"],
                "canceled_order_ids": cancellation["canceled_order_ids"],
                "liquidated_cells": liquidated, "dust_cells": dust,
                "unmanaged_inventory": [row for row in self.db.get_grid_levels(grid_id) if _d(row.get("held_qty")) > 0 and row["state"] == "ERROR"],
                "errors": errors,
                "pnl_realized": sum(float(row.get("cycle_pnl", 0)) for row in liquidated),
            }
        self._close_event("GRID_CLOSED", grid_id, result)
        return result
