"""Recoverable pull-based spot grid engine over an injected exchange client."""

from __future__ import annotations

import json
import hashlib
import logging
import re
import threading
import time
from collections import OrderedDict
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN
from typing import Any

from binance.exceptions import BinanceRequestException
from requests.exceptions import RequestException
from sqlalchemy import select

from data.exchange_filters import FilterViolation, SymbolFilters
from data.testnet_client import TestnetOrderError
from grid.compound import compound_amount
from grid.policy import (DEFAULT_GRID_FEE_PCT, DEFAULT_SMART_PARAMS, PROFIT_CLOSE_RETRY_MAX,
                         build_profit_cells, plan_dust_sweep, plan_profit_close, validate_params)
from grid.levels import GridConfigError, compute_lines, plan_cells
from grid.adjust import plan_adjust
from grid import loans as loan_policy
from grid.guards import sell_level_conflicts, sell_level_conflict_message

logger = logging.getLogger(__name__)
PROFIT_PREVIEW_TRADE_CACHE_TTL_SECONDS = 120
PROFIT_PREVIEW_TRADE_CACHE_MAX_SIZE = 512


class GridCreationError(RuntimeError):
    pass


def _d(value: Any) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))


def _get(obj: Any, key: str, default: Any = None) -> Any:
    return obj.get(key, default) if isinstance(obj, dict) else getattr(obj, key, default)


class GridEngine:
    def __init__(
        self, db: Any, exchange: Any, settings: Any, environment: str = "testnet",
        event_sink: Any = None, vol_provider: Any = None,
    ):
        if environment != "testnet":
            raise ValueError("GridEngine only supports the testnet environment in this phase")
        self.db = db
        self.exchange = exchange
        self.settings = settings
        self.environment = environment
        self.event_sink = event_sink
        self.vol_provider = vol_provider
        self._event_price: float | None = None
        self._adjust_lock_guard = threading.Lock()
        self._adjust_locks: dict[int, threading.Lock] = {}
        self._deferred_funds_causes: dict[tuple[int, int], str] = {}
        self._profit_preview_trade_cache: OrderedDict[str, tuple[float, list[dict[str, Any]]]] = OrderedDict()
        self._profit_preview_trade_cache_lock = threading.Lock()
        self._monotonic = time.monotonic
        self._utcnow = lambda: datetime.now(timezone.utc)

    @staticmethod
    def _is_insufficient_balance(error: Exception) -> bool:
        code = getattr(error, "code", None)
        if code is not None:
            try:
                return int(code) == -2010
            except (TypeError, ValueError):
                return str(code).strip() == "-2010"
        message = getattr(error, "message", str(error))
        return "insufficient balance" in str(message).casefold()

    def _deferred_funds_event_is_duplicate(self, grid_id: int, level_idx: int, cause_key: str) -> bool:
        key = (int(grid_id), int(level_idx))
        if self._deferred_funds_causes.get(key) == cause_key:
            return True
        list_events = getattr(self.db, "list_grid_events", None)
        if callable(list_events):
            try:
                events = list_events(grid_id=int(grid_id), limit=5000)
            except Exception:
                events = []
            for event in events:
                if event.get("level_idx") is None or int(event["level_idx"]) != int(level_idx):
                    continue
                kind = str(event.get("event_type", "")).upper()
                if kind == "BUY_DEFERRED_FUNDS":
                    details = event.get("details") or {}
                    self._deferred_funds_causes[key] = str(details.get("cause_key", ""))
                    return str(details.get("cause_key", "")) == cause_key
                if kind in {"BUY_PLACED", "INTENT_RECOVERED"}:
                    break
                if kind == "CELL_REPRICED" and event.get("order_id") is not None:
                    break
        return False

    def _clear_deferred_funds(self, grid_id: int, level_idx: int, cid: str,
                              order_id: int | None) -> None:
        key = (int(grid_id), int(level_idx))
        self._deferred_funds_causes.pop(key, None)
        if self.event_sink is not None:
            return
        list_events = getattr(self.db, "list_grid_events", None)
        add_event = getattr(self.db, "add_grid_event", None)
        if not callable(list_events) or not callable(add_event):
            return
        try:
            reset_types = {"BUY_PLACED", "INTENT_RECOVERED"}
            latest = next((event for event in list_events(grid_id=int(grid_id), limit=5000)
                           if event.get("level_idx") is not None
                           and int(event["level_idx"]) == int(level_idx)
                           and (str(event.get("event_type", "")).upper() == "BUY_DEFERRED_FUNDS"
                                or str(event.get("event_type", "")).upper() in reset_types
                                or (str(event.get("event_type", "")).upper() == "CELL_REPRICED"
                                    and event.get("order_id") is not None))), None)
            if latest and str(latest.get("event_type", "")).upper() == "BUY_DEFERRED_FUNDS":
                add_event(
                    run_id=None, source="CLI", grid_id=int(grid_id), level_idx=int(level_idx),
                    client_order_id=cid, order_id=order_id, event_type="BUY_PLACED",
                    details={"clears_deferred_funds": True},
                )
        except Exception:
            logger.exception("grid=%s level=%s could not persist deferred-funds reset", grid_id, level_idx)

    def _defer_buy_for_insufficient_funds(
        self, grid_id: int, level: dict, cid: str, error: TestnetOrderError, *,
        capital: Any, qty: Any, price: Any,
    ) -> bool:
        level_idx = int(level["level_idx"])
        self.db.update_level(
            int(grid_id), level_idx, state="IDLE", order_id=None, client_order_id=None,
            buy_client_order_id=cid,
        )
        code = getattr(error, "code", None)
        raw_message = str(getattr(error, "message", str(error)))
        normalized_message = " ".join(raw_message.casefold().split())
        cause_key = f"code:{code}" if code is not None else f"message:{normalized_message}"
        duplicate = self._deferred_funds_event_is_duplicate(int(grid_id), level_idx, cause_key)
        self._deferred_funds_causes[(int(grid_id), level_idx)] = cause_key
        if not duplicate:
            details = {
                "level_idx": level_idx, "capital_requested": str(capital),
                "quantity": str(qty), "price": str(price), "error_code": code,
                "cause_key": cause_key,
            }
            if self.event_sink is None and callable(getattr(self.db, "add_grid_event", None)):
                try:
                    self.db.add_grid_event(
                        run_id=None, source="CLI", grid_id=int(grid_id), level_idx=level_idx,
                        client_order_id=cid, event_type="BUY_DEFERRED_FUNDS",
                        reason=raw_message, price=self._event_price, details=details,
                    )
                except Exception:
                    logger.exception("grid=%s level=%s could not persist BUY_DEFERRED_FUNDS",
                                     grid_id, level_idx)
            else:
                self._emit(
                    "BUY_DEFERRED_FUNDS", int(grid_id), level_idx,
                    client_order_id=cid, reason=raw_message, details=details,
                )
        return True

    def _emit(
        self, event_type: str, grid_id: int, level_idx: int | None = None, *,
        client_order_id: str | None = None, order_id: int | None = None,
        reason: str | None = None, details: dict | None = None, persisted: bool = False,
    ) -> None:
        if self.event_sink is None:
            return
        event = {
            "event_type": str(event_type), "grid_id": grid_id, "level_idx": level_idx,
            "client_order_id": client_order_id, "order_id": order_id, "reason": reason,
            "price": self._event_price, "details": details or {}, "persisted": persisted,
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

    @staticmethod
    def _actual_buy_client_order_id(grid_id: int, level: dict) -> str:
        explicit = level.get("buy_client_order_id")
        match = re.fullmatch(r"g\d+L\d+B(\d+)", str(explicit or ""))
        if explicit and (match is None or int(match.group(1)) == int(level.get("cycles_completed", 0))):
            return str(explicit)
        return GridEngine._buy_client_order_id(
            int(grid_id), int(level["level_idx"]), int(level.get("cycles_completed", 0)),
            level.get("client_order_id"),
        )

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

    @staticmethod
    def _level_sell_price(grid: dict, level: dict, filters: SymbolFilters) -> Decimal:
        stored = level.get("sell_price")
        if stored is not None:
            return _d(stored)
        step = (_d(grid["range_high"]) - _d(grid["range_low"])) / Decimal(int(grid["n_levels"]))
        derived = filters.round_price(_d(grid["range_low"]) + step * (int(level["level_idx"]) + 1), "nearest")
        return derived

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

    @staticmethod
    def _recovery_sell_client_order_id(grid_id: int, level_idx: int, canceled_order_id: int) -> str:
        key = f"{grid_id}:{level_idx}:{canceled_order_id}".encode("ascii")
        return "gR" + hashlib.sha256(key).hexdigest()[:30]

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
        strategy: str = "simple",
        params: dict | None = None,
        stop_loss_pct: Decimal | str | float | None = None,
        calibration_id: int | None = None,
    ) -> dict:
        symbol = str(symbol).replace("/", "").upper()
        if not self._active_coin(symbol):
            raise GridConfigError(f"{symbol} must be an active registered coin")
        strategy = str(strategy).lower()
        if strategy not in {"simple", "smart"}:
            raise GridConfigError("strategy must be simple or smart")
        effective_params = None
        if strategy == "smart":
            try:
                effective_params = validate_params(params, n_levels)
            except ValueError as exc:
                raise GridConfigError(str(exc)) from exc
            effective_stop = stop_loss_pct if stop_loss_pct is not None else effective_params["stop_loss_pct"]
            if float(effective_stop) <= 0:
                raise GridConfigError("stop_loss_pct must be greater than zero")
            effective_params["stop_loss_pct"] = float(effective_stop)
            if self.vol_provider is not None and self.vol_provider.get(symbol) is None:
                raise GridConfigError(f"smart requiere sigma disponible para {symbol}")
        else:
            if params is not None:
                simple_keys = {"max_days", "compound_enabled", "compound_ratio",
                               "compound_max_growth_pct", *DEFAULT_SMART_PARAMS}
                if any(
                        key not in simple_keys
                        or (key in DEFAULT_SMART_PARAMS and key not in {
                            "compound_enabled", "compound_ratio", "compound_max_growth_pct"
                        } and value != DEFAULT_SMART_PARAMS[key])
                        for key, value in params.items()):
                    raise GridConfigError(
                        "simple grids only support max_days and compound parameters"
                    )
                try:
                    validated_params = validate_params(params, n_levels)
                except ValueError as exc:
                    raise GridConfigError(str(exc)) from exc
                effective_params = {
                    key: validated_params[key]
                    for key in params
                    if key in {"max_days", "compound_enabled", "compound_ratio", "compound_max_growth_pct"}
                }
            effective_stop = stop_loss_pct
            if effective_stop is not None and float(effective_stop) <= 0:
                raise GridConfigError("stop_loss_pct must be greater than zero")
        if self.db.count_open_grids() >= int(_get(self.settings, "max_grids_simultaneos", 5)):
            raise GridConfigError("maximum simultaneous open grids reached")
        capital_total = _d(
            _get(self.settings, "usdt_por_grid", 100) if capital is None else capital
        )
        reserve = Decimal(0)
        if strategy == "smart":
            reserve = (capital_total * _d(effective_params.get("reserve_pct", 0))
                       / Decimal(100)).quantize(Decimal("0.00000001"), rounding=ROUND_DOWN)
        distributable_capital = capital_total - reserve
        filters, snapshot, avg_price = self._market_context(symbol)
        self._event_price = float((snapshot["bid_price"] + snapshot["ask_price"]) / Decimal(2))
        open_orders = self.exchange.get_open_orders(symbol)
        if filters.max_num_orders is not None and len(open_orders) >= filters.max_num_orders:
            raise GridConfigError("exchange MAX_NUM_ORDERS is already reached")
        lines = compute_lines(range_low, range_high, n_levels, filters)
        plans = plan_cells(lines, distributable_capital, snapshot, filters, self.settings)
        existing_levels = []
        for status in ("OPENING", "ACTIVE", "PAUSED", "CLOSING", "HOLDING"):
            for existing_grid in self.db.list_grids_by_status({status}):
                if existing_grid.get("symbol") == symbol:
                    existing_levels.extend(self.db.get_grid_levels(int(existing_grid["id"])))
        conflicts = sell_level_conflicts(
            [plan.sell_price for plan in plans], existing_levels, filters.tick_size,
            _get(self.settings, "same_coin_sell_tolerance_pct", 0.05),
        )
        if conflicts:
            raise GridConfigError(sell_level_conflict_message(conflicts))
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
                "reserve": reserve,
                "status": "OPENING",
                "environment": self.environment,
                "open_price": (snapshot["bid_price"] + snapshot["ask_price"]) / 2,
                "strategy": strategy,
                "params": effective_params,
                "calibration_id": calibration_id,
            },
            [
                {
                    "level_idx": plan.level_idx,
                    "price": plan.buy_price,
                    "sell_price": plan.sell_price,
                    "stop_loss_pct": None if effective_stop is None else float(effective_stop),
                    "capital": plan.capital,
                    "capital_base": plan.capital,
                    "capital_compound": Decimal(0),
                    "capital_loan": Decimal(0),
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
                    grid_id, plan.level_idx, state="BUY_OPEN", client_order_id=cid,
                    buy_client_order_id=cid, order_id=None,
                )
                logger.info("grid=%s level=%s action=BUY_INTENT client_order_id=%s", grid_id, plan.level_idx, cid)
                try:
                    order = self._send_limit(
                        symbol, "BUY", plan.qty, plan.buy_price, cid, filters, avg_price,
                    )
                except TestnetOrderError as exc:
                    if not self._is_insufficient_balance(exc):
                        raise
                    self._defer_buy_for_insufficient_funds(
                        grid_id, {"level_idx": plan.level_idx}, cid, exc,
                        capital=plan.capital, qty=plan.qty, price=plan.buy_price,
                    )
                    continue
                self._clear_deferred_funds(grid_id, int(plan.level_idx), cid, int(order["order_id"]))
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

    def _build_profit_cells(
        self, grid_id: int, symbol: str, levels: list[dict] | None = None, *,
        use_cache: bool = False, deadline: float | None = None,
        clock: Any = None, on_fee_estimated: Any = None,
    ) -> list[dict]:
        levels = self.db.get_grid_levels(int(grid_id)) if levels is None else levels
        clock = clock or self._monotonic

        def load_buy_trades(cid: str) -> list[dict] | None:
            now = clock()
            if use_cache:
                with self._profit_preview_trade_cache_lock:
                    cached = self._profit_preview_trade_cache.get(str(cid))
                    if cached is not None:
                        expiry, trades = cached
                        if expiry > now:
                            self._profit_preview_trade_cache.move_to_end(str(cid))
                            return [dict(trade) for trade in trades]
                        self._profit_preview_trade_cache.pop(str(cid), None)
            if deadline is not None and now >= deadline:
                return None
            order = self.exchange.get_order(symbol, client_order_id=cid)
            if deadline is not None and clock() >= deadline:
                return None
            trades = [dict(trade) for trade in self.exchange.get_my_trades(symbol, int(order["order_id"]))]
            if use_cache:
                with self._profit_preview_trade_cache_lock:
                    self._profit_preview_trade_cache[str(cid)] = (
                        clock() + PROFIT_PREVIEW_TRADE_CACHE_TTL_SECONDS, trades)
                    self._profit_preview_trade_cache.move_to_end(str(cid))
                    while len(self._profit_preview_trade_cache) > PROFIT_PREVIEW_TRADE_CACHE_MAX_SIZE:
                        self._profit_preview_trade_cache.popitem(last=False)
            return trades

        return build_profit_cells(levels, load_buy_trades,
                                  lambda trades: self._fee_value_usdt(trades, symbol),
                                  on_fee_estimated=on_fee_estimated)

    def _place_level_intent(
        self,
        grid_id: int,
        level: dict,
        side: str,
        qty: Decimal,
        price: Decimal,
        filters: SymbolFilters,
        avg_price: Decimal,
        *,
        client_order_id: str | None = None,
    ) -> int:
        cycle = int(level["cycles_completed"])
        cid = client_order_id or self._client_order_id(grid_id, int(level["level_idx"]), side, cycle)
        if side == "BUY":
            prior_cid = level.get("buy_client_order_id") or level.get("client_order_id")
            prior_cycle = re.fullmatch(rf"g{int(grid_id)}L{int(level['level_idx'])}B(\d+)", str(prior_cid or ""))
            prior_is_adjusted = bool(re.fullmatch(r"gA[0-9a-f]{30}", str(prior_cid or "")))
            if prior_is_adjusted or (prior_cycle and int(prior_cycle.group(1)) == cycle):
                seed = f"{grid_id}:{level['level_idx']}:cycle:{cycle}:previous:{prior_cid}:rearm"
                cid = "gA" + hashlib.sha256(seed.encode("ascii")).hexdigest()[:30]
        symbol = self.db.get_grid(grid_id)["symbol"]
        self.db.update_level(
            grid_id, int(level["level_idx"]), state=f"{side}_OPEN",
            client_order_id=cid, order_id=None,
            **({"buy_client_order_id": cid} if side == "BUY" else {}),
        )
        logger.info("grid=%s level=%s action=%s_INTENT client_order_id=%s", grid_id, level["level_idx"], side, cid)
        order = self._send_limit(
            symbol, side, qty, price, cid, filters, avg_price,
        )
        if side == "BUY":
            self._clear_deferred_funds(
                int(grid_id), int(level["level_idx"]), cid, int(order["order_id"]),
            )
        self.db.update_level(grid_id, int(level["level_idx"]), order_id=order["order_id"])
        self._emit(
            f"{side}_PLACED", grid_id, int(level["level_idx"]), client_order_id=cid,
            order_id=order["order_id"], details={"qty": str(qty), "price": str(price)},
        )
        logger.info("grid=%s level=%s action=%s_SENT order_id=%s", grid_id, level["level_idx"], side, order["order_id"])
        return int(order["order_id"])

    @staticmethod
    def _loan_cid(grid_id: int, level_idx: int, cycle: int, previous: str | None,
                  loan_id: int, amount: Any, role: str) -> str:
        seed = f"{grid_id}:{level_idx}:{cycle}:{previous or '<none>'}:{loan_id}:{amount}:{role}"
        return "gL" + hashlib.sha256(seed.encode("ascii")).hexdigest()[:30]

    def _pending_loan_level_indices(self, grid_id: int) -> set[int]:
        """Return cells exclusively owned by a durable, unfinished loan saga."""
        return {
            int(idx)
            for loan in self.db.list_grid_loans(int(grid_id), statuses={"PENDING"})
            for idx in (loan.get("lender_idx"), loan.get("borrower_idx"))
            if idx is not None
        }

    def _defer_pending_loan_for_funds(
        self, grid: dict, loan: dict, plan: dict, role: str,
        cid: str | None, error: TestnetOrderError,
    ) -> None:
        stage = str(plan.get("stage", "PREPARED"))
        raw_message = str(getattr(error, "message", str(error)))
        normalized_message = " ".join(raw_message.casefold().split())
        code = getattr(error, "code", None)
        cause_key = (f"code:{code}|message:{normalized_message}" if code is not None
                     else f"message:{normalized_message}")
        events = self.db.list_grid_events(
            grid_id=int(grid["id"]), event_type="LOAN_DEFERRED_FUNDS", limit=5000,
        )
        duplicate = False
        for event in events:
            details = event.get("details") or {}
            try:
                event_loan_id = int(details.get("loan_id", -1))
            except (TypeError, ValueError):
                continue
            if event_loan_id != int(loan["id"]):
                continue
            if details.get("role") != role:
                continue
            duplicate = details.get("stage") == stage and details.get("cause_key") == cause_key
            break
        if duplicate:
            return
        owner = getattr(self.event_sink, "__self__", None)
        self.db.add_grid_event(
            run_id=getattr(owner, "_run_id", None),
            source="MONITOR" if self.event_sink else "CLI",
            grid_id=int(grid["id"]),
            level_idx=int(plan[f"{role}_idx"]),
            client_order_id=cid,
            event_type="LOAN_DEFERRED_FUNDS",
            reason=raw_message,
            price=self._event_price,
            details={
                "loan_id": int(loan["id"]), "role": role, "stage": stage,
                "cause_key": cause_key, "error_code": code,
            },
        )

    def _resize_loan_buy(self, grid: dict, loan: dict, plan: dict, role: str,
                         target_capital: Decimal, filters: SymbolFilters,
                         avg_price: Decimal) -> str:
        """Idempotently cancel and replace one BUY as a durable loan-plan step."""
        idx_key = f"{role}_idx"
        idx = int(plan[idx_key])
        cell = next(row for row in self.db.get_grid_levels(int(grid["id"]))
                    if int(row["level_idx"]) == idx)
        if cell["state"] != "BUY_OPEN":
            if cell["state"] not in {"IDLE", "DONE"} or _d(cell.get("held_qty", 0)) > 0:
                return "CHANGED"
            return "IDLE"
        old_cid = cell.get("client_order_id") or cell.get("buy_client_order_id")
        cid_key = f"{role}_replacement_cid"
        cid = plan.get(cid_key)
        if not cid:
            cid = self._loan_cid(int(grid["id"]), idx, int(cell.get("cycles_completed", 0)),
                                 old_cid, int(loan["id"]), plan["amount"], role)
            plan[cid_key] = cid
            self.db.update_grid_loan(int(loan["id"]), plan=plan)
        existing = self.exchange.find_order_by_client_id(grid["symbol"], cid)
        if existing is not None:
            status = str(existing.get("status", "")).upper()
            if status == "FILLED" or _d(existing.get("executed_qty", 0)) > 0:
                return "FILLED"
            if status in {"NEW", "PARTIALLY_FILLED", "PENDING_CANCEL"}:
                self.db.update_level(int(grid["id"]), idx, order_id=int(existing["order_id"]),
                                     client_order_id=cid, buy_client_order_id=cid)
                return "PLACED"
            if status not in {"CANCELED", "EXPIRED", "REJECTED"}:
                raise RuntimeError(f"loan replacement status unresolved: {status or 'unknown'}")
            # Terminal IDs are immutable at Binance. A retry gets another deterministic ID.
            cid = self._loan_cid(int(grid["id"]), idx, int(cell.get("cycles_completed", 0)),
                                 cid, int(loan["id"]), plan["amount"], role + "retry")
            cid_key = f"{role}_replacement_cid"
            plan[cid_key] = cid
            self.db.update_grid_loan(int(loan["id"]), plan=plan)
        old_order_id = cell.get("order_id")
        if old_order_id is not None:
            old_order = self.exchange.get_order(grid["symbol"], order_id=int(old_order_id))
            if str(old_order.get("client_order_id")) != cid:
                try:
                    canceled = self.exchange.cancel_order(grid["symbol"], int(old_order_id))
                except Exception:
                    canceled = self.exchange.get_order(grid["symbol"], order_id=int(old_order_id))
                if _d(canceled.get("executed_qty", 0)) > 0 or str(canceled.get("status", "")).upper() == "FILLED":
                    actual_cid = canceled.get("client_order_id") or old_cid
                    self.db.update_level(int(grid["id"]), idx, state="BUY_OPEN",
                        client_order_id=actual_cid, buy_client_order_id=actual_cid,
                        order_id=int(old_order_id))
                    current = next(row for row in self.db.get_grid_levels(int(grid["id"]))
                                   if int(row["level_idx"]) == idx)
                    self._handle_buy_fill(grid, current, canceled, filters, avg_price)
                    return "FILLED"
                if str(canceled.get("status", "")).upper() not in {"CANCELED", "EXPIRED", "REJECTED"}:
                    raise RuntimeError("loan lender/borrower buy cancellation unresolved")
        # Write-ahead replacement intent is recoverable by client order ID.
        self.db.update_level(int(grid["id"]), idx, state="BUY_OPEN", client_order_id=cid,
                             buy_client_order_id=cid, order_id=None)
        qty = filters.round_qty_down(target_capital / _d(cell["price"]))
        order = self._send_limit(grid["symbol"], "BUY", qty, _d(cell["price"]), cid,
                                 filters, avg_price)
        self.db.update_level(int(grid["id"]), idx, order_id=int(order["order_id"]))
        return "PLACED"

    def lend_from_plan(self, grid_id: int, plan: dict[str, Any], market: dict | None = None) -> dict:
        """Execute one reserve-first loan from a write-ahead plan."""
        grid = self.db.get_grid(int(grid_id))
        if (grid is None or grid.get("status") != "ACTIVE"
                or grid.get("strategy", "simple") != "smart"
                or not (grid.get("params") or {}).get("loans_enabled", False)):
            return {"ok": False, "reason": "loans_disabled_or_grid_inactive"}
        reserve_part = _d(plan.get("reserve_part", 0))
        lender_part = _d(plan.get("lender_part", 0))
        amount = _d(plan["amount"])
        if amount <= 0 or reserve_part + lender_part != amount:
            raise ValueError("invalid loan plan amounts")
        loans = self.db.list_grid_loans(int(grid_id))
        pending = next((row for row in loans if row["status"] == "PENDING"
                        and int(row["borrower_idx"]) == int(plan["borrower_idx"])), None)
        if pending is None:
            detail = {**(market or {}), "source": "monitor_or_cli"}
            loan = self.db.create_grid_loan({
                "grid_id": int(grid_id), "lender_idx": plan.get("lender_idx"),
                "borrower_idx": int(plan["borrower_idx"]), "amount": float(amount),
                "reserve_part": float(reserve_part),
                "lender_cycles_at_open": int(plan.get("lender_cycles_at_open", 0)),
                "borrower_cycles_at_open": int(plan.get("borrower_cycles_at_open", 0)),
                "plan": {**plan, "stage": "PREPARED"}, "details": detail,
            })
        else:
            loan = pending
        self._continue_pending_loan(grid, loan)
        final = self.db.get_grid_loan(int(loan["id"]))
        return {"ok": final is not None and final["status"] == "OPEN", "loan": final}

    def _continue_pending_loan(self, grid: dict, loan: dict) -> None:
        if loan["status"] != "PENDING":
            return
        plan = dict(loan["plan"] or {})
        filters, _snapshot, avg_price = self._market_context(grid["symbol"])
        lender_idx = loan.get("lender_idx")
        lender_part = _d(plan.get("lender_part", 0))
        borrower_idx = int(loan["borrower_idx"])
        amount = _d(loan["amount"])
        if lender_idx is not None and lender_part > 0 and plan.get("stage") not in {
            "LENDER_RESIZED", "BORROWER_RESIZED", "OPEN"
        }:
            try:
                outcome = self._resize_loan_buy(grid, loan, plan, "lender",
                                                _d(plan["lender_capital_before"]) - lender_part,
                                                filters, avg_price)
            except TestnetOrderError as exc:
                if not self._is_insufficient_balance(exc):
                    raise
                self._defer_pending_loan_for_funds(
                    grid, loan, plan, "lender", plan.get("lender_replacement_cid"), exc,
                )
                return
            if outcome == "FILLED":
                self._cancel_pending_loan(grid, loan, "lender_buy_filled_during_resize")
                return
            if outcome == "CHANGED":
                self._cancel_pending_loan(grid, loan, "lender_cell_changed")
                return
            plan["stage"] = "LENDER_RESIZED"
            self.db.update_grid_loan(int(loan["id"]), plan=plan)
        if plan.get("stage") not in {"BORROWER_RESIZED", "OPEN"}:
            borrower = next(row for row in self.db.get_grid_levels(int(grid["id"]))
                            if int(row["level_idx"]) == borrower_idx)
            if borrower.get("state") not in {"IDLE", "BUY_OPEN"} or _d(borrower.get("held_qty", 0)) > 0:
                self._cancel_pending_loan(grid, loan, "borrower_cell_changed")
                return
            try:
                outcome = self._resize_loan_buy(grid, loan, plan, "borrower",
                                                _d(plan["borrower_capital_before"]) + amount,
                                                filters, avg_price)
            except TestnetOrderError as exc:
                if not self._is_insufficient_balance(exc):
                    raise
                self._defer_pending_loan_for_funds(
                    grid, loan, plan, "borrower", plan.get("borrower_replacement_cid"), exc,
                )
                return
            if outcome in {"FILLED", "CHANGED"}:
                self._cancel_pending_loan(grid, loan, "borrower_cell_changed_during_resize")
                return
            plan["stage"] = "BORROWER_RESIZED"
            self.db.update_grid_loan(int(loan["id"]), plan=plan)
        cells = {int(row["level_idx"]): row for row in self.db.get_grid_levels(int(grid["id"]))}
        lender_updates = {}
        if lender_idx is not None and lender_part > 0:
            lender = cells[int(lender_idx)]
            lender_updates[int(lender_idx)] = {
                "capital": float(_d(plan["lender_capital_before"]) - lender_part),
                "capital_base": float(_d(lender.get("capital_base", lender["capital_base"]))),
                "capital_loan": float(_d(lender.get("capital_loan", 0)) - lender_part),
            }
        borrower = cells[borrower_idx]
        borrower_updates = {
            "capital": float(_d(plan["borrower_capital_before"]) + amount),
            "capital_base": float(_d(borrower.get("capital_base", borrower["capital_base"]))),
            "capital_loan": float(_d(borrower.get("capital_loan", 0)) + amount),
        }
        lender_updates[borrower_idx] = borrower_updates
        grid_fields = {"reserve": float(_d(grid.get("reserve", 0)) - _d(loan["reserve_part"]))}
        if grid_fields["reserve"] < -1e-8:
            raise RuntimeError("loan reserve became negative")
        self.db.apply_grid_loan_ledger(int(loan["id"]), level_updates=lender_updates,
            grid_fields=grid_fields, status="OPEN", event={
                "grid_id": int(grid["id"]), "source": "MONITOR" if self.event_sink else "CLI",
                "run_id": getattr(getattr(self.event_sink, "__self__", None), "_run_id", None),
                "event_type": "LOAN_CREATED", "reason": None, "price": self._event_price,
                "details": {**(loan.get("details") or {}), "amount": str(amount),
                            "reserve_part": str(loan["reserve_part"]), "lender_part": str(lender_part),
                            "lender_idx": lender_idx, "borrower_idx": borrower_idx},
            })

    def _cancel_pending_loan(self, grid: dict, loan: dict, reason: str) -> None:
        loan = self.db.get_grid_loan(int(loan["id"])) or loan
        plan = dict(loan.get("plan") or {})
        if plan.get("stage") in {"LENDER_RESIZED", "BORROWER_RESIZED"} \
                and loan.get("lender_idx") is not None and _d(plan.get("lender_part", 0)) > 0 \
                and "borrower" in reason:
            plan["rollback_idx"] = int(loan["lender_idx"])
            lender_before = _d(plan["lender_capital_before"])
            grid_now = self.db.get_grid(int(grid["id"]))
            filters, _snapshot, avg_price = self._market_context(grid_now["symbol"])
            outcome = self._resize_loan_buy(grid_now, loan, plan, "rollback",
                                            lender_before, filters, avg_price)
            if outcome in {"FILLED", "CHANGED"}:
                # A fill while restoring is real owned inventory; do not leave a synthetic loan.
                reason += ":rollback_" + outcome.lower()
            if plan.get("stage") == "BORROWER_RESIZED":
                plan["borrower_rollback_idx"] = int(loan["borrower_idx"])
                borrower_before = _d(plan["borrower_capital_before"])
                outcome = self._resize_loan_buy(grid_now, loan, plan, "borrower_rollback",
                                                borrower_before, filters, avg_price)
                if outcome in {"FILLED", "CHANGED"}:
                    reason += ":borrower_rollback_" + outcome.lower()
        self.db.update_grid_loan(int(loan["id"]), status="CANCELLED", close_reason=reason,
                                 closed_at=datetime.now(timezone.utc).replace(tzinfo=None))
        self.db.add_grid_event(run_id=None, source="MONITOR" if self.event_sink else "CLI",
            grid_id=int(grid["id"]), event_type="LOAN_CANCELLED", reason=reason,
            details={"loan_id": int(loan["id"]), "amount": loan["amount"]})

    def resume_pending_loans(self, grid_id: int | None = None) -> int:
        grids = ([self.db.get_grid(int(grid_id))] if grid_id is not None else
                 self.db.list_grids_by_status({"ACTIVE"}))
        resumed = 0
        for grid in grids:
            if not grid or grid.get("strategy", "simple") != "smart":
                continue
            for loan in self.db.list_grid_loans(int(grid["id"]), statuses={"PENDING"}):
                self._continue_pending_loan(grid, loan)
                resumed += 1
        return resumed

    def repay_loan(self, loan_id: int) -> dict:
        loan = self.db.get_grid_loan(int(loan_id))
        if loan is None or loan["status"] != "OPEN":
            return {"ok": False, "reason": "loan_not_open"}
        grid = self.db.get_grid(int(loan["grid_id"]))
        borrower_idx = int(loan["borrower_idx"])
        amount = _d(loan["amount"])
        cells = {int(row["level_idx"]): row for row in self.db.get_grid_levels(int(grid["id"]))}
        borrower = cells[borrower_idx]
        if borrower["state"] not in {"IDLE", "BUY_OPEN"} or _d(borrower.get("held_qty", 0)) > 0:
            self._loan_throttled_event(int(grid["id"]), "LOAN_REPAY_WAITING", "borrower_not_free",
                {"loan_id": int(loan_id), "amount": str(amount)}, key="loan_id", value=int(loan_id),
                level_idx=borrower_idx)
            return {"ok": False, "reason": "borrower_not_free"}
        lender_idx = loan.get("lender_idx")
        lender = None if lender_idx is None else cells[int(lender_idx)]
        lender_target = None if lender is None else _d(lender["capital"]) + amount
        borrower_target = _d(borrower["capital"]) - amount
        if borrower_target < 0:
            return {"ok": False, "reason": "insufficient_borrower_capital"}
        filters, _snapshot, avg_price = self._market_context(grid["symbol"])
        if lender is None:
            repayment_triggered = int(borrower.get("cycles_completed", 0)) > int(
                loan.get("borrower_cycles_at_open", 0)
            )
        else:
            repayment_triggered = int(lender.get("cycles_completed", 0)) > int(
                loan.get("lender_cycles_at_open", 0)
            )
        if not repayment_triggered:
            return {"ok": False, "reason": "repayment_trigger_not_reached"}
        minimum_remaining = max(
            filters.min_notional * _d((grid.get("params") or {}).get("loan_min_margin", 1.1)),
            filters.min_qty * _d(borrower["price"]),
        )
        if borrower_target < minimum_remaining:
            self._loan_throttled_event(int(grid["id"]), "LOAN_REPAY_WAITING",
                "borrower_minimum_margin", {"loan_id": int(loan_id),
                    "amount": str(amount), "capital_after": str(borrower_target),
                    "minimum_remaining": str(minimum_remaining)}, key="loan_id",
                value=int(loan_id), level_idx=borrower_idx)
            return {"ok": False, "reason": "borrower_minimum_margin"}
        repayment_plan = {"amount": str(amount), "borrower_idx": borrower_idx,
                          "lender_idx": lender_idx, "stage": "REPAYING"}
        self.db.update_grid_loan(int(loan_id), plan={**(loan.get("plan") or {}), **repayment_plan})
        for role, cell, target in (("borrower", borrower, borrower_target),
                                   ("lender", lender, lender_target)):
            if cell is None or cell.get("state") != "BUY_OPEN":
                continue
            outcome = self._resize_loan_buy(grid, loan,
                {**repayment_plan, "lender_idx": lender_idx, "borrower_idx": borrower_idx,
                 "amount": str(amount)}, role, target, filters, avg_price)
            if outcome != "PLACED":
                return {"ok": False, "reason": f"{role}_resize_{outcome.lower()}"}
        updates = {}
        updates[borrower_idx] = {
            "capital": float(borrower_target), "capital_base": float(borrower["capital_base"]),
            "capital_loan": float(_d(borrower.get("capital_loan", 0)) - amount),
        }
        grid_fields = {}
        if lender is not None:
            updates[int(lender_idx)] = {
                "capital": float(lender_target), "capital_base": float(lender["capital_base"]),
                "capital_loan": float(_d(lender.get("capital_loan", 0)) + amount),
            }
        else:
            grid_fields["reserve"] = float(_d(grid.get("reserve", 0)) + amount)
        self.db.apply_grid_loan_ledger(int(loan_id), level_updates=updates, grid_fields=grid_fields,
            status="REPAID", event={"grid_id": int(grid["id"]),
            "source": "MONITOR" if self.event_sink else "CLI", "event_type": "LOAN_REPAID",
            "reason": "repayment_triggered", "price": self._event_price,
            "details": {"amount": str(amount), "borrower_idx": borrower_idx,
                        "lender_idx": lender_idx}})
        return {"ok": True, "loan_id": int(loan_id), "amount": float(amount)}

    def transfer_loans(self, grid_id: int, reason: str, level_idx: int | None = None) -> int:
        """Permanently settle all OPEN loans in accounting before ADJUST/close."""
        grid = self.db.get_grid(int(grid_id))
        if grid is None:
            return 0
        count = 0
        for loan in self.db.list_grid_loans(int(grid_id), statuses={"OPEN"}):
            if level_idx is not None and int(level_idx) not in {
                int(loan["borrower_idx"]),
                -1 if loan.get("lender_idx") is None else int(loan["lender_idx"]),
            }:
                continue
            grid = self.db.get_grid(int(grid_id))
            amount = _d(loan["amount"])
            borrower_idx = int(loan["borrower_idx"])
            cells = {int(row["level_idx"]): row for row in self.db.get_grid_levels(int(grid_id))}
            borrower = cells[borrower_idx]
            updates = {}
            grid_fields = {}
            if loan.get("lender_idx") is None:
                grid_fields["reserve"] = float(_d(grid.get("reserve", 0)) + amount)
                borrower_base = _d(borrower["capital_base"])
                borrower_loan = _d(borrower.get("capital_loan", 0)) - amount
                borrower_capital = _d(borrower["capital"]) - amount
                updates[borrower_idx] = {"capital": float(borrower_capital),
                    "capital_base": float(borrower_base), "capital_loan": float(borrower_loan)}
            else:
                lender_idx = int(loan["lender_idx"])
                lender = cells[lender_idx]
                lender_base = _d(lender["capital_base"]) - amount
                borrower_base = _d(borrower["capital_base"]) + amount
                lender_loan = _d(lender.get("capital_loan", 0)) + amount
                borrower_loan = _d(borrower.get("capital_loan", 0)) - amount
                updates[lender_idx] = {"capital": float(_d(lender["capital"])),
                    "capital_base": float(lender_base), "capital_loan": float(lender_loan)}
                updates[borrower_idx] = {"capital": float(_d(borrower["capital"])),
                    "capital_base": float(borrower_base), "capital_loan": float(borrower_loan)}
            event_type = "LOAN_TRANSFERRED"
            self.db.apply_grid_loan_ledger(int(loan["id"]), level_updates=updates,
                grid_fields=grid_fields, status="TRANSFERRED", event={
                    "grid_id": int(grid_id), "source": "MONITOR" if self.event_sink else "CLI",
                    "event_type": event_type, "reason": reason, "price": self._event_price,
                    "details": {"loan_id": int(loan["id"]), "amount": str(amount),
                                "lender_idx": loan.get("lender_idx"), "borrower_idx": borrower_idx},
                })
            count += 1
        return count

    def process_grid_loans(self, grid_id: int, now: datetime | None = None) -> dict:
        grid = self.db.get_grid(int(grid_id))
        if (grid is None or grid.get("status") != "ACTIVE" or grid.get("strategy") != "smart"
                or not (grid.get("params") or {}).get("loans_enabled", False)):
            return {"resumed": 0, "repaid": 0, "created": 0, "skipped": "disabled_or_inactive"}
        resumed = self.resume_pending_loans(int(grid_id))
        now = now or datetime.now(timezone.utc)
        cells = self.db.get_grid_levels(int(grid_id))
        events = self.db.list_grid_events(grid_id=int(grid_id), limit=5000)
        loans = self.db.list_grid_loans(int(grid_id))
        by_idx = {int(cell["level_idx"]): cell for cell in cells}
        repaid = 0
        for loan in loans:
            if loan.get("status") != "OPEN":
                continue
            lender = None if loan.get("lender_idx") is None else by_idx.get(int(loan["lender_idx"]))
            borrower = by_idx.get(int(loan["borrower_idx"]))
            repay = loan_policy.plan_repayment(loan, lender, borrower) if borrower else None
            if repay:
                result = self.repay_loan(int(loan["id"]))
                repaid += int(result.get("ok", False))
        grid = self.db.get_grid(int(grid_id))
        cells = self.db.get_grid_levels(int(grid_id))
        loans = self.db.list_grid_loans(int(grid_id))
        borrower = loan_policy.select_borrower(cells, events, loans, now, grid.get("params") or {})
        if borrower is None:
            return {"resumed": resumed, "repaid": repaid, "created": 0}
        last_adjust = self.db.get_last_event(int(grid_id), "GRID_ADJUSTED")
        lender = loan_policy.select_lender(cells, events, loans,
            borrower_idx=int(borrower["level_idx"]), now=now, params=grid["params"],
            grid_created_at=grid["created_at"],
            last_adjust_at=None if last_adjust is None else last_adjust["ts"])
        filters, _snapshot, avg_price = self._market_context(grid["symbol"])
        plan = loan_policy.plan_loan(borrower, lender, reserve=grid.get("reserve", 0),
            capital_total=grid["capital_total"], params=grid["params"],
            min_notional=filters.min_notional, min_qty=filters.min_qty,
            step_size=filters.step_size)
        if plan is None:
            self._loan_throttled_event(int(grid_id), "LOAN_SKIPPED", "no_valid_plan",
                {"reason": "no_valid_plan"}, key="reason", value="no_valid_plan")
            return {"resumed": resumed, "repaid": repaid, "created": 0, "skipped": "no_valid_plan"}
        plan = {**plan, "lender_cycles_at_open": 0 if lender is None else int(lender.get("cycles_completed", 0)),
                "borrower_cycles_at_open": int(borrower.get("cycles_completed", 0))}
        result = self.lend_from_plan(int(grid_id), plan,
            market={"mid": str((_d(_snapshot["bid_price"]) + _d(_snapshot["ask_price"])) / 2),
                    "avg_price": str(avg_price)})
        return {"resumed": resumed, "repaid": repaid,
                "created": int(bool(result.get("ok"))), "loan": result.get("loan")}

    def _loan_throttled_event(self, grid_id: int, event_type: str, reason: str,
                              details: dict, *, key: str, value: Any,
                              level_idx: int | None = None) -> None:
        now = datetime.now(timezone.utc)
        recent = self.db.list_grid_events(grid_id=int(grid_id), event_type=event_type, limit=1000)
        for event in recent:
            old_details = event.get("details") or {}
            if old_details.get(key) != value:
                continue
            stamp = event.get("ts")
            if isinstance(stamp, str):
                stamp = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
            if isinstance(stamp, datetime):
                if stamp.tzinfo is None:
                    stamp = stamp.replace(tzinfo=timezone.utc)
                if (now - stamp.astimezone(timezone.utc)).total_seconds() < 6 * 3600:
                    return
        self._policy_event(event_type, int(grid_id), reason=reason, details=details, level_idx=level_idx)

    def _handle_buy_fill(
        self, grid: dict, level: dict, order: dict, filters: SymbolFilters, avg_price: Decimal,
        *, place_sell: bool = True,
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
        dust_delta = (gross_qty - base_fee) - held_qty
        fills = [trade for trade in trades if _d(trade.get("qty", 0)) > 0]
        gross_quote = sum((_d(trade.get("quoteQty", _d(trade.get("qty", 0)) * _d(trade.get("price", 0)))) for trade in fills), Decimal(0))
        entry_price = gross_quote / gross_qty if gross_qty > 0 else _d(order.get("price", level["price"]))
        bought_at = datetime.now(timezone.utc).replace(tzinfo=None)
        sell_price = self._level_sell_price(grid, level, filters)
        if level.get("sell_price") is None:
            self.db.set_level_fields(grid_id, idx, sell_price=float(sell_price))
        fee_usdt = self._fee_value_usdt(trades, symbol)
        self.db.record_grid_buy_fill_dust(
            grid_id, idx, f"buy:{int(order['order_id'])}", dust_delta,
            {"held_qty": float(held_qty), "entry_price": float(entry_price),
             "bought_at": bought_at,
             "buy_client_order_id": level.get("buy_client_order_id") or level.get("client_order_id"),
             "fee_paid": float(_d(level["fee_paid"]) + fee_usdt)},
        )
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
            filters.validate_order("SELL", sell_price, held_qty, avg_price)
        except FilterViolation as exc:
            self.db.update_level(
                grid_id, idx, state="ERROR", held_qty=float(held_qty),
                fee_paid=float(_d(level["fee_paid"]) + fee_usdt),
            )
            logger.error("grid=%s level=%s action=SELL_BLOCKED reason=%s", grid_id, idx, exc)
            return True, True
        self.db.update_level(
            grid_id, idx, held_qty=float(held_qty), entry_price=float(entry_price), bought_at=bought_at,
            buy_client_order_id=level.get("buy_client_order_id") or level.get("client_order_id"),
            fee_paid=float(_d(level["fee_paid"]) + fee_usdt),
        )
        if not place_sell:
            self.db.update_level(grid_id, idx, state="ERROR")
            return True, False
        try:
            self._place_level_intent(
                grid_id, {**level, "sell_price": sell_price}, "SELL", held_qty, sell_price, filters, avg_price,
            )
            return True, False
        except (FilterViolation, TestnetOrderError) as exc:
            self.db.update_level(grid_id, idx, state="ERROR")
            logger.error("grid=%s level=%s action=SELL_FAILED reason=%s", grid_id, idx, exc)
            return True, True
        except (RuntimeError, BinanceRequestException, RequestException) as exc:
            logger.warning("grid=%s level=%s action=SELL_RETRY reason=%s", grid_id, idx, exc)
            return True, False

    def _settle_canceled_buy_partial(
        self, grid: dict, level: dict, order: dict, path: str, *, place_sell: bool = True,
    ) -> bool:
        """Account a BUY that partially filled before cancellation."""
        executed = _d(order.get("executed_qty", 0))
        if executed <= 0:
            return False
        grid_id, idx = int(grid["id"]), int(level["level_idx"])
        current = next((row for row in self.db.get_grid_levels(grid_id)
                        if int(row["level_idx"]) == idx), None)
        order_id = order.get("order_id")
        if (current is None or current.get("state") != "BUY_OPEN"
                or current.get("order_id") is None or order_id is None
                or int(current["order_id"]) != int(order_id)):
            logger.info("grid=%s level=%s action=BUY_PARTIAL_IGNORED reason=level_order_changed",
                        grid_id, idx)
            return False
        registered_cids = {str(value) for value in
                           (current.get("client_order_id"), current.get("buy_client_order_id")) if value}
        reported_cid = order.get("client_order_id")
        if reported_cid and registered_cids and str(reported_cid) not in registered_cids:
            logger.info("grid=%s level=%s action=BUY_PARTIAL_IGNORED reason=client_order_changed",
                        grid_id, idx)
            return False
        cid = order.get("client_order_id") or level.get("client_order_id")
        self.db.update_level(grid_id, idx, state="BUY_OPEN", order_id=order_id,
                             client_order_id=cid, buy_client_order_id=cid)
        filters, _, avg_price = self._market_context(grid["symbol"])
        settled = next(row for row in self.db.get_grid_levels(grid_id) if int(row["level_idx"]) == idx)
        self._handle_buy_fill(grid, settled, {**order, "order_id": order_id}, filters, avg_price,
                              place_sell=place_sell)
        self._close_event("BUY_PARTIAL_SETTLED", grid_id,
                          {"executed_qty": str(executed), "path": path},
                          client_order_id=cid, order_id=order_id, level_idx=idx)
        return True

    def _emit_compound_skipped(
        self, grid_id: int, level_idx: int, level: dict, sell_order: dict,
        reason: str, details: dict[str, Any],
    ) -> None:
        if self.event_sink is not None:
            self._emit(
                "COMPOUND_SKIPPED", grid_id, level_idx,
                client_order_id=level.get("client_order_id"),
                order_id=sell_order.get("order_id"), reason=reason, details=details,
            )
            return
        self.db.add_grid_event(
            run_id=None, source="CLI", grid_id=grid_id, level_idx=level_idx,
            client_order_id=level.get("client_order_id"), order_id=sell_order.get("order_id"),
            event_type="COMPOUND_SKIPPED", reason=reason,
            price=self._event_price, details=details,
        )

    def _handle_sell_fill(
        self, grid: dict, level: dict, sell_order: dict, bid: Decimal,
        filters: SymbolFilters, avg_price: Decimal, rearm: bool = True,
        post_sell_state: str = "DONE",
    ) -> tuple[bool, bool, bool]:
        grid_id, idx, symbol = int(grid["id"]), int(level["level_idx"]), grid["symbol"]
        cycle = int(level["cycles_completed"])
        buy_cid = self._actual_buy_client_order_id(grid_id, level)
        pnl_estimated = False
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
            pnl_estimated = True
            sell_fee = Decimal(0)
            cycle_pnl = (
                _d(level["sell_price"]) * _d(sell_order["executed_qty"])
                - _d(level["price"]) * _d(sell_order["executed_qty"])
            )
            logger.warning("grid=%s level=%s action=PNL_ESTIMATE reason=%s", grid_id, idx, exc)
        new_cycle = cycle + 1
        params = grid.get("params") or {}
        grid_status = str(grid.get("status", "")).upper()
        compound_allowed = (
            params.get("compound_enabled", False) is True
            and grid_status in {"ACTIVE", "PAUSED"}
            and post_sell_state != "DONE"
            and (rearm or grid_status == "PAUSED")
        )
        capital_before = _d(level.get("capital", 0))
        compound_before = _d(level.get("capital_compound", 0))
        base_value = level.get("capital_base")
        capital_base = (
            capital_before - compound_before if base_value is None else _d(base_value)
        )
        decision = None
        compound_amount_value = Decimal(0)
        skip_reason: str | None = None
        skip_details: dict[str, Any] = {}
        if compound_allowed:
            if pnl_estimated:
                skip_reason = "pnl_estimated"
                skip_details = {"cycle_pnl": str(cycle_pnl), "estimated": True}
            else:
                decision = compound_amount(
                    cycle_pnl, capital_base, compound_before, params, grid["capital_total"],
                )
                skip_reason = decision.reason if decision.amount <= 0 else None
                skip_details = dict(decision.details)
                compound_amount_value = decision.amount
            if compound_amount_value > 0:
                try:
                    balance = self.exchange.get_balance("USDT")
                    free_usdt = _d(balance["USDT"]["free"])
                except Exception as exc:
                    compound_amount_value = Decimal(0)
                    skip_reason = "balance_unavailable"
                    skip_details.update({"balance_error_type": type(exc).__name__})
                else:
                    required = capital_before + compound_amount_value
                    skip_details["free_usdt"] = free_usdt
                    skip_details["required_usdt"] = required
                    if free_usdt < required:
                        compound_amount_value = Decimal(0)
                        skip_reason = "insufficient_usdt"

        cycle_state = "IDLE" if rearm or grid_status == "PAUSED" else post_sell_state
        cycle_fields = {
            "state": cycle_state,
            "cycles_completed": new_cycle,
            "pnl": float(_d(level["pnl"]) + cycle_pnl),
            "held_qty": 0.0,
            "entry_price": None,
            "bought_at": None,
            "fee_paid": float(_d(level["fee_paid"]) + sell_fee),
            "order_id": None,
            "buy_client_order_id": None,
            "client_order_id": None,
        }
        compound_event_details: dict[str, Any] | None = None
        if compound_amount_value > 0 and decision is not None:
            capital_after = capital_before + compound_amount_value
            compound_after = compound_before + compound_amount_value
            ratio = _d(params.get("compound_ratio", 1.0))
            compound_event_details = {
                "cycle_pnl": str(cycle_pnl), "ratio": str(ratio),
                "amount": str(compound_amount_value),
                "capital_before": str(capital_before), "capital_after": str(capital_after),
                "capital_base": str(capital_base),
                "capital_compound_before": str(compound_before),
                "capital_compound_after": str(compound_after),
                "room": str(decision.details["room"]),
            }
            source = "MONITOR" if self.event_sink is not None else "CLI"
            sink_owner = getattr(self.event_sink, "__self__", None)
            event = {
                "source": source,
                "run_id": getattr(sink_owner, "_run_id", None),
                "event_type": "COMPOUND_APPLIED", "reason": "cycle_profit",
                "client_order_id": level.get("client_order_id"),
                "order_id": sell_order.get("order_id"),
                "price": self._event_price, "details": compound_event_details,
            }
            self.db.update_level_with_event(
                grid_id, idx, event=event,
                **cycle_fields,
                capital=float(capital_after), capital_base=float(capital_base),
                capital_compound=float(compound_after),
            )
            self._emit(
                "COMPOUND_APPLIED", grid_id, idx,
                client_order_id=level.get("client_order_id"),
                order_id=sell_order.get("order_id"), reason="cycle_profit",
                details=compound_event_details, persisted=True,
            )
        else:
            self.db.update_level(grid_id, idx, **cycle_fields)
            if compound_allowed and skip_reason is not None:
                self._emit_compound_skipped(
                    grid_id, idx, level, sell_order, skip_reason, skip_details,
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
            self.db.update_level(grid_id, idx, state=post_sell_state)
            return True, False, False
        if _d(level["price"]) < bid:
            updated["cycles_completed"] = new_cycle
            updated["symbol"] = symbol
            try:
                qty = filters.round_qty_down(_d(updated["capital"]) / _d(level["price"]))
                self._place_level_intent(
                    grid_id, updated, "BUY", qty, _d(level["price"]), filters, avg_price,
                )
                return True, False, False
            except (RuntimeError, BinanceRequestException, RequestException) as exc:
                logger.warning("grid=%s level=%s action=REARM_RETRY reason=%s", grid_id, idx, exc)
                return True, False, False
            except TestnetOrderError as exc:
                if self._is_insufficient_balance(exc):
                    cid = str(self.db.get_grid_levels(grid_id)[idx].get("client_order_id") or
                              self.db.get_grid_levels(grid_id)[idx].get("buy_client_order_id") or "")
                    emitted = self._defer_buy_for_insufficient_funds(
                        grid_id, {"level_idx": idx}, cid, exc,
                        capital=updated["capital"], qty=qty, price=level["price"],
                    )
                    return True, False, emitted
                self.db.update_level(grid_id, idx, state="ERROR")
                logger.error("grid=%s level=%s action=REARM_FAILED reason=%s", grid_id, idx, exc)
                return True, True, False
            except FilterViolation as exc:
                self.db.update_level(grid_id, idx, state="ERROR")
                logger.error("grid=%s level=%s action=REARM_FAILED reason=%s", grid_id, idx, exc)
                return True, True, False
            except Exception as exc:
                self.db.update_level(grid_id, idx, state="ERROR")
                logger.exception("grid=%s level=%s action=REARM_FAILED reason=%s", grid_id, idx, exc)
                return True, True, False
        self.db.update_level(grid_id, idx, state="IDLE")
        return True, False, False

    def _recover_legacy_entry(self, grid: dict, level: dict) -> None:
        """Recover cost basis for pre-15B inventory; preserve unknown fill time as unknown."""
        if _d(level.get("held_qty")) <= 0 or (level.get("entry_price") is not None and level.get("bought_at") is not None):
            return
        idx, cycle = int(level["level_idx"]), int(level.get("cycles_completed", 0))
        buy_cid = self._actual_buy_client_order_id(grid["id"], level)
        entry = level.get("entry_price")
        bought_at = level.get("bought_at")
        try:
            order = self.exchange.get_order(grid["symbol"], client_order_id=buy_cid)
            qty, quote = _d(order.get("executed_qty")), _d(order.get("cummulative_quote_qty"))
            if entry is None and qty > 0 and quote > 0:
                entry = float(quote / qty)
            stamp = order.get("time")
            if bought_at is None and stamp is not None:
                if isinstance(stamp, datetime):
                    bought_at = stamp.astimezone(timezone.utc).replace(tzinfo=None) if stamp.tzinfo else stamp
                else:
                    bought_at = datetime.fromtimestamp(float(stamp) / 1000.0, timezone.utc).replace(tzinfo=None)
        except Exception as exc:
            logger.warning("grid=%s level=%s legacy buy lookup failed; using estimated entry and unknown age (%s)",
                           grid["id"], idx, type(exc).__name__)
        if entry is None:
            entry = float(level["price"])
            logger.warning("grid=%s level=%s legacy inventory entry estimated from grid price", grid["id"], idx)
        if bought_at is None:
            updated_at = level.get("updated_at")
            if isinstance(updated_at, datetime):
                bought_at = updated_at
                logger.warning("grid=%s level=%s legacy inventory fill time estimated from updated_at", grid["id"], idx)
            else:
                logger.warning("grid=%s level=%s legacy inventory fill time unavailable; age remains unknown", grid["id"], idx)
        updates = {"entry_price": entry}
        if bought_at is not None:
            updates["bought_at"] = bought_at
        self.db.set_level_fields(int(grid["id"]), idx, **updates)

    def sync_grid(self, grid_id: int) -> dict[str, Any]:
        return self._sync_grid(
            grid_id, rearm=True, allowed_statuses={"ACTIVE"}, post_sell_state="IDLE",
        )

    def sync_closing(self, grid_id: int) -> dict[str, Any]:
        return self._sync_grid(grid_id, rearm=False, allowed_statuses={"CLOSING"})

    def sync_paused(self, grid_id: int) -> dict[str, Any]:
        grid = self.db.get_grid(grid_id)
        if grid is None or grid["status"] != "PAUSED":
            return {"buys_filled": 0, "sells_filled": 0, "cycles_completed": 0,
                    "orders_placed": 0, "errors": 0, "states": {}}
        errors = 0
        for level in self.db.get_grid_levels(grid_id):
            if level["state"] != "BUY_OPEN":
                continue
            order_id = level.get("order_id")
            try:
                if order_id is None and level.get("client_order_id"):
                    found = self.exchange.find_order_by_client_id(grid["symbol"], level["client_order_id"])
                    if found:
                        order_id = int(found["order_id"])
                        self.db.update_level(grid_id, level["level_idx"], order_id=order_id)
                if order_id is None:
                    self.db.update_level(grid_id, level["level_idx"], state="IDLE", order_id=None, client_order_id=None)
                    continue
                result = self.exchange.cancel_order(grid["symbol"], int(order_id))
                status = str(result.get("status", "")).upper()
                if status in {"CANCELED", "EXPIRED", "REJECTED"}:
                    if self._settle_canceled_buy_partial(grid, level, result, "sync_paused"):
                        continue
                    self.db.update_level(grid_id, level["level_idx"], state="IDLE", order_id=None, client_order_id=None)
                elif status == "FILLED":
                    # The shared synchronization below settles this real fill and places its protective sell.
                    continue
                else:
                    errors += 1
                    self._emit("PAUSE_BUY_CANCEL_RETRY", grid_id, int(level["level_idx"]),
                               client_order_id=level.get("client_order_id"), order_id=int(order_id),
                               reason=status or "unknown cancel status")
            except Exception as exc:
                errors += 1
                logger.warning("grid=%s level=%s pause buy cancel will retry", grid_id, level["level_idx"], exc_info=True)
                self._emit("PAUSE_BUY_CANCEL_RETRY", grid_id, int(level["level_idx"]),
                           reason=str(exc), client_order_id=level.get("client_order_id"), order_id=order_id)
        summary = self._sync_grid(grid_id, rearm=False, allowed_statuses={"PAUSED"}, post_sell_state="IDLE")
        summary["errors"] += errors
        return summary

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

    def _policy_event(self, event_type: str, grid_id: int, *, reason: str | None, details: dict,
                      level_idx: int | None = None, order_id: int | None = None) -> None:
        event = {"event_type": event_type, "grid_id": grid_id, "level_idx": level_idx,
                 "order_id": order_id, "reason": reason, "price": self._event_price, "details": details}
        if self.event_sink is not None:
            self._emit(event_type, grid_id, level_idx, order_id=order_id, reason=reason, details=details)
        else:
            self._close_event(event_type, grid_id, details, reason=reason, price=self._event_price,
                              level_idx=level_idx, order_id=order_id)

    def adjust_grid(
        self, grid_id: int, new_low: Any, new_high: Any, new_n: int | None = None,
        reason: str | None = None, details: dict | None = None,
    ) -> dict[str, Any]:
        key = int(grid_id)
        with self._adjust_lock_guard:
            lock = self._adjust_locks.setdefault(key, threading.Lock())
        if not lock.acquire(blocking=False):
            return {"ok": False, "reason": "adjust_in_progress"}
        try:
            return self._adjust_grid_locked(key, new_low, new_high, new_n, reason, details)
        finally:
            lock.release()

    def _adjust_grid_locked(
        self, grid_id: int, new_low: Any, new_high: Any, new_n: int | None = None,
        reason: str | None = None, details: dict | None = None,
    ) -> dict[str, Any]:
        """Move free cells through recoverable write-ahead BUY intents."""
        grid = self.db.get_grid(int(grid_id))
        if grid is None or grid.get("status") != "ACTIVE":
            return {"ok": False, "reason": "grid_not_active" if grid else "grid_not_found"}
        loan_enabled = (grid.get("strategy") == "smart"
                        and (grid.get("params") or {}).get("loans_enabled") is True)
        if loan_enabled:
            self.transfer_loans(int(grid_id), "before_adjust")
            grid = self.db.get_grid(int(grid_id))
        filters, snapshot, avg_price = self._market_context(grid["symbol"])
        mid = (snapshot["bid_price"] + snapshot["ask_price"]) / Decimal(2)
        target_n = int(grid["n_levels"]) if new_n is None else int(new_n)
        if (_d(grid["range_low"]) == _d(new_low) and _d(grid["range_high"]) == _d(new_high)
                and int(grid["n_levels"]) == target_n):
            return {"ok": True, "changed": False, "reason": "already_at_target"}
        cells = self.db.get_grid_levels(grid_id)
        reserve_target = (_d(grid["capital_total"])
                          * _d((grid.get("params") or {}).get("reserve_pct", 0)) / Decimal(100))
        reserve_excess = max(Decimal(0), _d(grid.get("reserve", 0)) - reserve_target) if loan_enabled else Decimal(0)
        planning_cells = [dict(row) for row in cells]
        if reserve_excess:
            free = next((row for row in planning_cells if row.get("state") in {"IDLE", "BUY_OPEN", "DONE"}
                         and _d(row.get("held_qty", 0)) <= 0), None)
            if free is not None:
                free["capital"] = str(_d(free.get("capital", 0)) + reserve_excess)
                free["capital_base"] = str(_d(free.get("capital_base", 0)) + reserve_excess)
        plan = plan_adjust(grid, planning_cells, new_low, new_high, target_n, mid, filters, self.settings)
        if not plan.ok:
            return {"ok": False, "reason": plan.reason, "plan": plan.details}
        old_range = {"low": grid["range_low"], "high": grid["range_high"], "n": grid["n_levels"]}
        for cell in cells:
            if cell.get("sell_price") is None:
                self.db.set_level_fields(grid_id, int(cell["level_idx"]),
                    sell_price=float(self._level_sell_price(grid, cell, filters)))
        for idx in plan.append_level_idxs:
            self.db.add_grid_level(grid_id, {
                "level_idx": idx, "price": float(plan.lines[0]), "capital": 0,
                "capital_base": 0, "capital_compound": 0,
                "sell_price": float(plan.lines[1]), "state": "IDLE",
            })
        mapping = list(plan.mapping)
        capital_updates: dict[int, dict[str, Any]] = {}
        if mapping:
            assigned = sum((Decimal(item["capital"]) for item in mapping[:-1]), Decimal(0))
            mapping[-1]["capital"] = str(plan.free_capital - assigned)
        by_idx = {int(row["level_idx"]): row for row in self.db.get_grid_levels(grid_id)}
        for item in mapping:
            idx, target = int(item["level_idx"]), _d(item["price"])
            cell = by_idx[idx]
            if cell.get("state") not in {"IDLE", "BUY_OPEN", "DONE"} or _d(cell.get("held_qty")) > 0:
                self._emit("ADJUST_SKIPPED_CELL", grid_id, idx, reason="cell_became_pinned",
                           details={"target_price": item["price"]})
                continue
            old_order_id, old_cid = cell.get("order_id"), cell.get("client_order_id")
            previous_cid = old_cid or cell.get("buy_client_order_id")

            # Recover an accepted BUY whose response was lost before order_id
            # was persisted. Link it first so the following write-ahead CID
            # replacement cannot orphan the old exchange order on a crash.
            if old_order_id is None and old_cid:
                unlinked = self.exchange.find_order_by_client_id(grid["symbol"], old_cid)
                if unlinked is not None:
                    unlinked_status = str(unlinked.get("status", "")).upper()
                    if unlinked_status in {"NEW", "PARTIALLY_FILLED", "PENDING_CANCEL", "FILLED"}:
                        old_order_id = int(unlinked["order_id"])
                        self.db.update_level(grid_id, idx, order_id=old_order_id)
                        cell = {**cell, "order_id": old_order_id}
                    elif unlinked_status not in {"CANCELED", "EXPIRED", "REJECTED"}:
                        raise RuntimeError(
                            f"unlinked BUY lookup unresolved: {unlinked_status or 'unknown'}"
                        )

            # A matching persisted gA intent is the write-ahead record for a
            # retry. Otherwise chain from the last CID so returning to an old
            # price (or reusing a line after changing N) always gets a new ID.
            is_adjust_intent = (
                isinstance(old_cid, str) and old_cid.startswith("gA")
                and _d(cell.get("price")) == target
                and str(cell.get("state", "")).upper() == "BUY_OPEN"
            )

            intent_cycle = int(cell.get("cycles_completed", 0))

            def adjusted_buy_cid(prior_cid: str | None, cycle_number: int | None = None) -> str:
                cycle_seed = intent_cycle if cycle_number is None else int(cycle_number)
                prior_seed = "<none>" if prior_cid is None else str(prior_cid)
                digest = hashlib.sha256(
                    f"{grid_id}:{idx}:cycle:{cycle_seed}:previous:{prior_seed}:{target}".encode("ascii")
                ).hexdigest()[:30]
                return "gA" + digest

            cid = old_cid if is_adjust_intent else adjusted_buy_cid(previous_cid)
            self.db.update_level(grid_id, idx, price=float(target),
                sell_price=float(Decimal(item["sell_price"])), state="BUY_OPEN",
                client_order_id=cid, buy_client_order_id=cid)
            existing_old = None
            if old_order_id is not None:
                # The DB CID may already be the replacement after a crash; the
                # exchange order itself determines whether this is old or new.
                existing_old = self.exchange.get_order(grid["symbol"], order_id=int(old_order_id))
            actual_old_cid = None if existing_old is None else existing_old.get("client_order_id")
            if old_order_id is not None and actual_old_cid != cid:
                try:
                    canceled = self.exchange.cancel_order(grid["symbol"], int(old_order_id))
                except Exception:
                    canceled = self.exchange.get_order(grid["symbol"], order_id=int(old_order_id))
                status = str(canceled.get("status", "")).upper()
                if _d(canceled.get("executed_qty", 0)) > 0 or status == "FILLED":
                    actual_cid = canceled.get("client_order_id") or old_cid
                    actual_price = _d(canceled.get("price", cell["price"]))
                    old_sell = self._level_sell_price(grid, {**cell, "price": actual_price}, filters)
                    self.db.update_level(grid_id, idx, price=float(actual_price), sell_price=float(old_sell),
                        client_order_id=actual_cid, buy_client_order_id=actual_cid, order_id=int(old_order_id))
                    actual_cell = next(row for row in self.db.get_grid_levels(grid_id)
                                       if int(row["level_idx"]) == idx)
                    self._handle_buy_fill(grid, actual_cell, canceled, filters, avg_price)
                    capital_updates[idx] = {
                        "capital": float(Decimal(item["capital"])),
                        "capital_base": float(Decimal(item["capital"])), "capital_compound": 0.0,
                    }
                    self._emit("ADJUST_SKIPPED_CELL", grid_id, idx, client_order_id=actual_cid,
                               order_id=int(old_order_id), reason="buy_filled_during_cancel")
                    continue
                if status not in {"CANCELED", "EXPIRED", "REJECTED"}:
                    raise RuntimeError(f"old BUY cancellation unresolved: {status or 'unknown'}")
            elif old_order_id is not None and existing_old is not None \
                    and str(existing_old.get("status", "")).upper() in {"NEW", "PARTIALLY_FILLED"}:
                # Already placed before a crash; retain the live order and
                # complete the DB linkage without submitting a duplicate.
                self.db.update_level(grid_id, idx, order_id=int(old_order_id))
                capital_updates[idx] = {
                    "capital": float(Decimal(item["capital"])),
                    "capital_base": float(Decimal(item["capital"])), "capital_compound": 0.0,
                }
                self._emit("CELL_REPRICED", grid_id, idx, client_order_id=cid,
                           order_id=int(old_order_id), details={"recovered_existing": True,
                           "new_price": str(target)})
                continue
            self.db.update_level(grid_id, idx, order_id=None)
            if target >= snapshot["bid_price"]:
                # Retain the last buy CID as the per-cell chain head; a later
                # reposition must not recreate an ID already used by Binance.
                self.db.update_level(grid_id, idx, state="IDLE", client_order_id=None,
                                     buy_client_order_id=cid)
                capital_updates[idx] = {
                    "capital": float(Decimal(item["capital"])),
                    "capital_base": float(Decimal(item["capital"])), "capital_compound": 0.0,
                }
                self._emit("CELL_REPRICED", grid_id, idx, details={
                    "old_price": cell["price"], "new_price": str(target),
                    "old_client_order_id": old_cid, "new_client_order_id": None,
                    "state": "IDLE"})
                continue

            existing = None
            for _attempt in range(100):
                existing = self.exchange.find_order_by_client_id(grid["symbol"], cid)
                if existing is None:
                    break
                existing_status = str(existing.get("status", "")).upper()
                if existing_status in {"NEW", "PARTIALLY_FILLED", "PENDING_CANCEL"}:
                    break
                if existing_status == "FILLED":
                    persisted = next(row for row in self.db.get_grid_levels(grid_id)
                                     if int(row["level_idx"]) == idx)
                    persisted_cycle = int(persisted.get("cycles_completed", 0))
                    if persisted.get("client_order_id") != cid or persisted_cycle != intent_cycle:
                        # A terminal fill from an earlier cycle is historical,
                        # even if the exchange still resolves its client ID.
                        intent_cycle = persisted_cycle
                        cid = adjusted_buy_cid(cid, intent_cycle)
                        self.db.update_level(grid_id, idx, client_order_id=cid,
                                             buy_client_order_id=cid)
                        existing = None
                        continue
                    self.db.update_level(grid_id, idx, order_id=int(existing["order_id"]))
                    filled_cell = next(row for row in self.db.get_grid_levels(grid_id)
                                       if int(row["level_idx"]) == idx)
                    self._handle_buy_fill(grid, filled_cell, existing, filters, avg_price)
                    capital_updates[idx] = {
                        "capital": float(Decimal(item["capital"])),
                        "capital_base": float(Decimal(item["capital"])), "capital_compound": 0.0,
                    }
                    self._emit("ADJUST_SKIPPED_CELL", grid_id, idx, client_order_id=cid,
                               order_id=int(existing["order_id"]), reason="buy_filled_before_link")
                    break
                if existing_status in {"CANCELED", "EXPIRED", "REJECTED"}:
                    cid = adjusted_buy_cid(cid)
                    self.db.update_level(grid_id, idx, client_order_id=cid, buy_client_order_id=cid)
                    existing = None
                    continue
                raise RuntimeError(f"replacement BUY lookup unresolved: {existing_status or 'unknown'}")
            else:
                raise RuntimeError("could not allocate an unused replacement BUY client id")

            if existing is not None and str(existing.get("status", "")).upper() == "FILLED":
                continue
            if existing is None:
                qty = filters.round_qty_down(Decimal(item["capital"]) / target)
                try:
                    order = self._send_limit(grid["symbol"], "BUY", qty, target, cid, filters, avg_price)
                except TestnetOrderError as exc:
                    if not self._is_insufficient_balance(exc):
                        raise
                    self._defer_buy_for_insufficient_funds(
                        grid_id, {"level_idx": idx}, cid, exc,
                        capital=item["capital"], qty=qty, price=target,
                    )
                    capital_updates[idx] = {
                        "capital": float(Decimal(item["capital"])),
                        "capital_base": float(Decimal(item["capital"])), "capital_compound": 0.0,
                    }
                    self._emit("CELL_REPRICED", grid_id, idx, client_order_id=cid,
                               reason="buy_deferred_insufficient_funds",
                               details={"old_price": cell["price"], "new_price": str(target),
                                        "state": "IDLE", "side": "BUY"})
                    continue
            else:
                order = existing
            self._clear_deferred_funds(int(grid_id), idx, cid, int(order["order_id"]))
            self.db.update_level(grid_id, idx, order_id=int(order["order_id"]))
            capital_updates[idx] = {
                "capital": float(Decimal(item["capital"])),
                "capital_base": float(Decimal(item["capital"])), "capital_compound": 0.0,
            }
            self._emit("CELL_REPRICED", grid_id, idx, client_order_id=cid, order_id=int(order["order_id"]),
                details={"old_price": cell["price"], "new_price": str(target),
                         "old_client_order_id": old_cid, "new_client_order_id": cid})
        for idx in plan.retire_level_idxs:
            idx = int(idx)
            cell = by_idx[idx]
            if cell.get("order_id") is not None:
                try:
                    result = self.exchange.cancel_order(grid["symbol"], int(cell["order_id"]))
                except Exception:
                    result = self.exchange.get_order(grid["symbol"], order_id=int(cell["order_id"]))
                status = str(result.get("status", "")).upper()
                if _d(result.get("executed_qty", 0)) > 0 or status == "FILLED":
                    actual_cid = result.get("client_order_id") or cell.get("client_order_id")
                    self.db.update_level(grid_id, idx, state="BUY_OPEN",
                        client_order_id=actual_cid, buy_client_order_id=actual_cid,
                        order_id=int(cell["order_id"]))
                    settled = next(row for row in self.db.get_grid_levels(grid_id)
                                   if int(row["level_idx"]) == idx)
                    self._handle_buy_fill(grid, settled, result, filters, avg_price)
                    self._emit("ADJUST_SKIPPED_CELL", grid_id, idx, client_order_id=actual_cid,
                               order_id=int(cell["order_id"]), reason="retiring_buy_filled_during_cancel")
                    continue
                if status not in {"CANCELED", "EXPIRED", "REJECTED"}:
                    raise RuntimeError(f"retired BUY cancellation unresolved: {status or 'unknown'}")
            self.db.update_level(grid_id, idx, state="DONE", order_id=None,
                client_order_id=None)
            capital_updates[idx] = {"capital": 0.0, "capital_base": 0.0, "capital_compound": 0.0}
        assigned_capital = sum((_d(fields["capital"]) for fields in capital_updates.values()), Decimal(0))
        if abs(assigned_capital - plan.free_capital) > Decimal("0.00000001"):
            raise RuntimeError("adjust_capital_assignment_incomplete")
        compound_normalized = sum((
            _d(by_idx[idx].get("capital_compound", 0)) for idx in capital_updates
        ), Decimal(0)).quantize(Decimal("0.00000001"), rounding=ROUND_DOWN)
        normalized_capital_total = _d(grid["capital_total"]) + compound_normalized
        event_details = {**(details or {}), "old": old_range,
            "new": {"low": str(plan.lines[0]), "high": str(plan.lines[-1]), "n": target_n},
            "free_capital": str(plan.free_capital), "capital_per_cell": str(plan.capital_per_cell),
            "compound_normalized": str(compound_normalized),
            "fixed_cells": list(plan.covered), "mapping": list(plan.mapping),
            "source": (details or {}).get("source", "ENGINE")}
        source = "MONITOR" if self.event_sink is not None else "CLI"
        sink_owner = getattr(self.event_sink, "__self__", None)
        self.db.update_levels_and_grid_with_event(
            grid_id, level_updates=capital_updates,
            grid_fields={"range_low": float(plan.lines[0]), "range_high": float(plan.lines[-1]),
                         "n_levels": target_n, "capital_total": float(normalized_capital_total),
                         **({"reserve": float(_d(grid.get("reserve", 0)) - reserve_excess)}
                            if loan_enabled else {})},
            event={"source": source, "run_id": getattr(sink_owner, "_run_id", None),
                   "event_type": "GRID_ADJUSTED", "reason": reason,
                   "price": self._event_price, "details": event_details},
        )
        self._emit("GRID_ADJUSTED", grid_id, reason=reason, details=event_details, persisted=True)
        return {"ok": True, "changed": True, "plan": plan.details, "details": event_details}

    def preview_adjust(self, grid_id: int, new_low: Any, new_high: Any,
                       new_n: int | None = None) -> dict[str, Any]:
        grid = self.db.get_grid(int(grid_id))
        if grid is None:
            return {"ok": False, "reason": "grid_not_found"}
        if grid.get("status") != "ACTIVE":
            return {"ok": False, "reason": "grid_not_active"}
        filters, snapshot, _avg = self._market_context(grid["symbol"])
        mid = (snapshot["bid_price"] + snapshot["ask_price"]) / Decimal(2)
        cells = self.db.get_grid_levels(grid_id)
        loan_enabled = (grid.get("strategy") == "smart"
                        and (grid.get("params") or {}).get("loans_enabled") is True)
        target_reserve = (_d(grid["capital_total"])
                          * _d((grid.get("params") or {}).get("reserve_pct", 0)) / Decimal(100))
        excess = max(Decimal(0), _d(grid.get("reserve", 0)) - target_reserve) if loan_enabled else Decimal(0)
        cells = [dict(row) for row in cells]
        if excess:
            free = next((row for row in cells if row.get("state") in {"IDLE", "BUY_OPEN", "DONE"}
                         and _d(row.get("held_qty", 0)) <= 0), None)
            if free:
                free["capital"] = str(_d(free.get("capital", 0)) + excess)
                free["capital_base"] = str(_d(free.get("capital_base", 0)) + excess)
        plan = plan_adjust(grid, cells, new_low, new_high,
                           new_n, mid, filters, self.settings)
        return {"ok": plan.ok, "reason": plan.reason, "plan": plan.details}

    def pause_grid(self, grid_id: int, reason: str, details: dict) -> dict:
        if not self.db.transition_grid_status(grid_id, {"ACTIVE"}, "PAUSED"):
            return {"ok": False, "grid_id": int(grid_id), "status": (self.db.get_grid(grid_id) or {}).get("status")}
        payload = dict(details or {})
        reasons = payload.get("reasons")
        if reasons is None:
            reasons = [str(reason)]
        payload["reasons"] = list(reasons)
        self._policy_event("GRID_PAUSED", grid_id, reason=reason, details=payload)
        summary = self.sync_paused(grid_id)
        return {"ok": True, "grid_id": int(grid_id), "status": "PAUSED", "sync": summary}

    def resume_grid(self, grid_id: int, reason: str, details: dict) -> dict:
        if not self.db.transition_grid_status(grid_id, {"PAUSED"}, "ACTIVE"):
            return {"ok": False, "grid_id": int(grid_id), "status": (self.db.get_grid(grid_id) or {}).get("status")}
        self._policy_event("GRID_RESUMED", grid_id, reason=reason, details=details or {})
        return {"ok": True, "grid_id": int(grid_id), "status": "ACTIVE"}

    def stoploss_cell(self, grid_id: int, level_idx: int, reason: str, details: dict) -> dict:
        grid = self.db.get_grid(grid_id)
        level = next((row for row in self.db.get_grid_levels(grid_id)
                      if int(row["level_idx"]) == int(level_idx)), None) if grid else None
        if grid is None or grid["status"] not in {"ACTIVE", "PAUSED", "HOLDING"} or level is None \
                or level["state"] != "SELL_OPEN" or _d(level.get("held_qty")) <= 0:
            return {"ok": False, "reason": "cell_not_eligible"}
        try:
            filters, _snapshot, avg_price = self._market_context(grid["symbol"])
            self._event_price = float((_snapshot["bid_price"] + _snapshot["ask_price"]) / 2)
            order_id = level.get("order_id")
            if order_id is None and level.get("client_order_id"):
                found = self.exchange.find_order_by_client_id(grid["symbol"], level["client_order_id"])
                order_id = found["order_id"] if found else None
            if order_id is not None:
                try:
                    canceled = self.exchange.cancel_order(grid["symbol"], int(order_id))
                except Exception:
                    canceled = self.exchange.get_order(grid["symbol"], order_id=int(order_id))
                status = str(canceled.get("status", "")).upper()
                if status == "FILLED":
                    if grid["status"] == "ACTIVE":
                        settled = self.sync_grid(grid_id)
                    elif grid["status"] == "PAUSED":
                        settled = self.sync_paused(grid_id)
                    else:
                        settled = self.sync_repository(grid_id)
                    return {"ok": True, "status": "SELL_FILLED", "settled": settled}
                if status not in {"CANCELED", "EXPIRED", "REJECTED"}:
                    return {"ok": False, "status": status or "CANCEL_PENDING"}
            result = self._market_sell_owned_cell(grid, level, filters, avg_price, emit_event=False)
            if result.get("status") == "DUST":
                self.return_cell_to_reserve(grid_id, int(level_idx), "stoploss_dust")
                return {"ok": True, **result}
            self.return_cell_to_reserve(grid_id, int(level_idx), "stoploss")
            updated = next(row for row in self.db.get_grid_levels(grid_id) if int(row["level_idx"]) == int(level_idx))
            payload = dict(details or {})
            payload.update({
                "entry_price": level.get("entry_price"), "stop_loss_pct": level.get("stop_loss_pct"),
                "market_price": self._event_price, "execution_price": result.get("price"),
                "held_qty": level.get("held_qty"), "pnl_realized": updated.get("pnl"),
                "realized_pnl": result.get("realized_pnl"),
                "cycles_completed": updated.get("cycles_completed"),
            })
            self._policy_event("CELL_STOPLOSS", grid_id, reason=reason, details=payload,
                               level_idx=int(level_idx), order_id=result.get("order_id"))
            return {"ok": True, **result}
        except Exception as exc:
            logger.warning("grid=%s level=%s stop-loss will retry", grid_id, level_idx, exc_info=True)
            return {"ok": False, "reason": str(exc)}

    def _sync_grid(
        self, grid_id: int, *, rearm: bool, allowed_statuses: set[str],
        repository_only: bool = False, post_sell_state: str = "DONE",
    ) -> dict[str, Any]:
        grid = self.db.get_grid(grid_id)
        if grid is None:
            raise ValueError(f"grid {grid_id} does not exist")
        summary = {"buys_filled": 0, "sells_filled": 0, "cycles_completed": 0,
                   "orders_placed": 0, "errors": 0, "buys_deferred": 0, "states": {}}
        if grid["status"] not in allowed_statuses:
            summary["states"] = {}
            return summary
        symbol = grid["symbol"]
        filters, snapshot, avg_price = self._market_context(symbol)
        self._event_price = float((snapshot["bid_price"] + snapshot["ask_price"]) / Decimal(2))
        bid = snapshot["bid_price"]
        deferred_this_pass: set[int] = set()
        pending_loan_levels = self._pending_loan_level_indices(int(grid_id))

        for existing_level in self.db.get_grid_levels(grid_id):
            if int(existing_level["level_idx"]) in pending_loan_levels:
                continue
            if existing_level.get("sell_price") is None:
                derived_sell = self._level_sell_price(grid, existing_level, filters)
                self.db.set_level_fields(grid_id, int(existing_level["level_idx"]), sell_price=float(derived_sell))
                existing_level["sell_price"] = float(derived_sell)
            if existing_level["state"] == "SELL_OPEN" and _d(existing_level.get("held_qty")) > 0:
                self._recover_legacy_entry(grid, existing_level)

        # Recover write-ahead intents before taking the single open-order snapshot.
        for level in self.db.get_grid_levels(grid_id):
            if int(level["level_idx"]) in pending_loan_levels:
                continue
            if repository_only and level["state"] != "SELL_OPEN":
                continue
            cid = level.get("client_order_id")
            if not cid or level.get("order_id") is not None or level["state"] not in {"BUY_OPEN", "SELL_OPEN"}:
                continue
            side = "BUY" if level["state"] == "BUY_OPEN" else "SELL"
            try:
                found = self.exchange.find_order_by_client_id(symbol, cid)
                if found is not None:
                    fields = {"order_id": found["order_id"]}
                    if level["state"] == "BUY_OPEN":
                        fields["buy_client_order_id"] = cid
                        self._clear_deferred_funds(
                            int(grid_id), int(level["level_idx"]), cid, int(found["order_id"]),
                        )
                    self.db.update_level(grid_id, level["level_idx"], **fields)
                    self._emit("INTENT_RECOVERED", grid_id, level["level_idx"], client_order_id=cid,
                               order_id=found["order_id"], details={"found_existing": True})
                    continue
                if not rearm and level["state"] == "BUY_OPEN":
                    self.db.update_level(
                        grid_id, level["level_idx"], state="IDLE",
                        order_id=None, client_order_id=None,
                    )
                    continue
                qty = (
                    filters.round_qty_down(_d(level["capital"]) / _d(level["price"]))
                    if side == "BUY" else filters.round_qty_down(_d(level["held_qty"]))
                )
                order = self._send_limit(
                    symbol, side, qty,
                    _d(level["price"] if side == "BUY" else self._level_sell_price(grid, level, filters)),
                    cid, filters, avg_price,
                )
                fields = {"order_id": order["order_id"]}
                if side == "BUY":
                    fields["buy_client_order_id"] = cid
                    self._clear_deferred_funds(
                        int(grid_id), int(level["level_idx"]), cid, int(order["order_id"]),
                    )
                self.db.update_level(grid_id, level["level_idx"], **fields)
                summary["orders_placed"] += 1
                self._emit("INTENT_RECOVERED", grid_id, level["level_idx"], client_order_id=cid,
                           order_id=order["order_id"], details={"found_existing": False})
            except TestnetOrderError as exc:
                if side == "BUY" and self._is_insufficient_balance(exc):
                    deferred_cid = str(level.get("client_order_id") or level.get("buy_client_order_id") or "")
                    self._defer_buy_for_insufficient_funds(
                        grid_id, level, deferred_cid, exc,
                        capital=level.get("capital", 0), qty=qty,
                        price=level.get("price", 0),
                    )
                    summary["buys_deferred"] += 1
                    deferred_this_pass.add(int(level["level_idx"]))
                    continue
                summary["errors"] += 1
                if level["state"] == "SELL_OPEN" and _d(level.get("held_qty")) > 0:
                    self._emit("SELL_REPROTECT_RETRY", grid_id, int(level["level_idx"]),
                               client_order_id=cid, reason=str(exc))
                    logger.warning("grid=%s level=%s action=SELL_REPROTECT_RETRY reason=%s",
                                   grid_id, level["level_idx"], exc)
                else:
                    self.db.update_level(grid_id, level["level_idx"], state="ERROR")
                    self._emit("LEVEL_ERROR", grid_id, level["level_idx"], client_order_id=cid,
                               order_id=level.get("order_id"), reason=str(exc))
                    logger.error("grid=%s level=%s action=INTENT_RECOVERY_FAILED reason=%s",
                                 grid_id, level["level_idx"], exc)
            except FilterViolation as exc:
                summary["errors"] += 1
                if level["state"] == "SELL_OPEN" and _d(level.get("held_qty")) > 0:
                    self._emit("SELL_REPROTECT_RETRY", grid_id, int(level["level_idx"]),
                               client_order_id=cid, reason=str(exc))
                    logger.warning("grid=%s level=%s action=SELL_REPROTECT_RETRY reason=%s",
                                   grid_id, level["level_idx"], exc)
                else:
                    self.db.update_level(grid_id, level["level_idx"], state="ERROR")
                    self._emit("LEVEL_ERROR", grid_id, level["level_idx"], client_order_id=cid,
                               order_id=level.get("order_id"), reason=str(exc))
                    logger.error("grid=%s level=%s action=INTENT_RECOVERY_FAILED reason=%s",
                                 grid_id, level["level_idx"], exc)
            except (RuntimeError, BinanceRequestException, RequestException) as exc:
                logger.warning("grid=%s level=%s action=INTENT_RECOVERY_RETRY reason=%s", grid_id, level["level_idx"], exc)

        open_orders = self.exchange.get_open_orders(symbol)
        open_ids = {int(row["order_id"]) for row in open_orders}
        for level in self.db.get_grid_levels(grid_id):
            if int(level["level_idx"]) in pending_loan_levels:
                continue
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
                summary["errors"] += 1
                if level["state"] == "SELL_OPEN" and _d(level.get("held_qty")) > 0:
                    logger.warning("grid=%s level=%s action=SELL_STATUS_RETRY reason=%s",
                                   grid_id, level["level_idx"], type(exc).__name__)
                else:
                    self.db.update_level(grid_id, level["level_idx"], state="ERROR")
                    self._emit("LEVEL_ERROR", grid_id, level["level_idx"],
                               client_order_id=level.get("client_order_id"),
                               order_id=order_id, reason=str(exc))
                    logger.error("grid=%s level=%s action=ORDER_LOOKUP_FAILED reason=%s",
                                 grid_id, level["level_idx"], exc)
                continue
            status = str(order["status"]).upper()
            if level["state"] == "BUY_OPEN" and order.get("client_order_id") \
                    and order.get("client_order_id") != level.get("client_order_id") \
                    and (status == "FILLED" or _d(order.get("executed_qty", 0)) > 0):
                actual_cid = order["client_order_id"]
                actual_price = _d(order.get("price", level["price"]))
                old_geometry_sell = self._level_sell_price(
                    grid, {**level, "price": actual_price, "sell_price": None}, filters,
                )
                self.db.update_level(
                    grid_id, level["level_idx"], price=float(actual_price),
                    sell_price=float(old_geometry_sell), client_order_id=actual_cid,
                    buy_client_order_id=actual_cid,
                )
                actual_level = next(row for row in self.db.get_grid_levels(grid_id)
                                    if int(row["level_idx"]) == int(level["level_idx"]))
                changed, error = self._handle_buy_fill(grid, actual_level, order, filters, avg_price)
                summary["buys_filled"] += int(changed)
                summary["errors"] += int(error)
                continue
            if status == "PARTIALLY_FILLED" or status == "NEW":
                continue
            if status in {"CANCELED", "EXPIRED", "REJECTED"}:
                self._emit(
                    "ORDER_CANCELLED_EXTERNALLY", grid_id, level["level_idx"],
                    client_order_id=level.get("client_order_id"), order_id=order_id,
                    reason=status,
                )
                if level["state"] == "SELL_OPEN" and _d(level.get("held_qty")) > 0:
                    # Keep owned inventory protected when its sell was canceled
                    # during an interrupted stop-loss. The canceled client ID
                    # must not be reused by write-ahead recovery.
                    idx = int(level["level_idx"])
                    recovery_cid = self._recovery_sell_client_order_id(grid_id, idx, order_id)
                    self.db.update_level(
                        grid_id, idx, state="SELL_OPEN", order_id=None,
                        client_order_id=recovery_cid,
                    )
                    try:
                        qty = filters.round_qty_down(_d(level["held_qty"]))
                        sell_price = self._level_sell_price(grid, level, filters)
                        order = self._send_limit(
                            symbol, "SELL", qty, sell_price, recovery_cid, filters, avg_price,
                        )
                        self.db.update_level(grid_id, idx, order_id=order["order_id"])
                        summary["orders_placed"] += 1
                        self._emit(
                            "SELL_REPROTECTED", grid_id, idx, client_order_id=recovery_cid,
                            order_id=order["order_id"], details={"qty": str(qty), "price": str(sell_price)},
                        )
                    except Exception as exc:
                        # Leave the write-ahead SELL_OPEN intent and inventory
                        # intact so the next pass retries with this new ID.
                        summary["errors"] += 1
                        self._emit(
                            "SELL_REPROTECT_RETRY", grid_id, idx,
                            client_order_id=recovery_cid, reason=str(exc),
                        )
                        logger.warning(
                            "grid=%s level=%s canceled inventory sell will retry", grid_id, idx,
                            exc_info=True,
                        )
                    continue
                # An in-place adjustment writes its replacement CID before it
                # cancels the old BUY. A crash in that window leaves the row
                # pointing at the canceled exchange order. Do not classify the
                # replacement intent as an external cancellation/error.
                if level["state"] == "BUY_OPEN" and order.get("client_order_id") \
                        and order.get("client_order_id") != level.get("client_order_id"):
                    executed = _d(order.get("executed_qty", 0))
                    if executed > 0 or status == "FILLED":
                        old_cid = order["client_order_id"]
                        old_price = _d(order.get("price", level["price"]))
                        old_sell = self._level_sell_price(
                            grid, {**level, "price": old_price, "sell_price": None}, filters,
                        )
                        self.db.update_level(
                            grid_id, level["level_idx"], price=float(old_price), sell_price=float(old_sell),
                            client_order_id=old_cid, buy_client_order_id=old_cid,
                            order_id=order_id,
                        )
                        settled_level = next(
                            row for row in self.db.get_grid_levels(grid_id)
                            if int(row["level_idx"]) == int(level["level_idx"])
                        )
                        changed, error = self._handle_buy_fill(
                            grid, settled_level, order, filters, avg_price,
                        )
                        summary["buys_filled"] += int(changed)
                        summary["errors"] += int(error)
                    else:
                        self.db.update_level(grid_id, level["level_idx"], order_id=None)
                    continue
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
                changed, error, deferred = self._handle_sell_fill(
                    grid, level, order, bid, filters, avg_price,
                    rearm=rearm, post_sell_state=post_sell_state,
                )
                summary["sells_filled"] += int(changed)
                summary["cycles_completed"] += int(changed and not error)
                summary["buys_deferred"] += int(deferred)
                if deferred:
                    deferred_this_pass.add(int(level["level_idx"]))
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
                if (level["state"] != "IDLE" or int(level["level_idx"]) in pending_loan_levels
                        or int(level["level_idx"]) in deferred_this_pass
                        or _d(level["price"]) >= bid):
                    continue
                qty = filters.round_qty_down(_d(level["capital"]) / _d(level["price"]))
                try:
                    self._place_level_intent(grid_id, {**level, "symbol": symbol}, "BUY", qty, _d(level["price"]), filters, avg_price)
                    summary["orders_placed"] += 1
                except (RuntimeError, BinanceRequestException, RequestException) as exc:
                    summary["errors"] += 1
                    logger.warning("grid=%s level=%s action=IDLE_ARM_RETRY reason=%s", grid_id, level["level_idx"], exc)
                except TestnetOrderError as exc:
                    if self._is_insufficient_balance(exc):
                        cid = str(self.db.get_grid_levels(grid_id)[int(level["level_idx"])].get("client_order_id") or
                                  self.db.get_grid_levels(grid_id)[int(level["level_idx"])].get("buy_client_order_id") or "")
                        self._defer_buy_for_insufficient_funds(
                            grid_id, level, cid, exc,
                            capital=level["capital"], qty=qty, price=level["price"],
                        )
                        summary["buys_deferred"] += 1
                        deferred_this_pass.add(int(level["level_idx"]))
                        continue
                    self.db.update_level(grid_id, level["level_idx"], state="ERROR")
                    self._emit("LEVEL_ERROR", grid_id, level["level_idx"], reason=str(exc))
                    summary["errors"] += 1
                    logger.error("grid=%s level=%s action=IDLE_ARM_FAILED reason=%s", grid_id, level["level_idx"], exc)
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
            })

        def current_buy_order(level: dict, order_id: int, order: dict | None = None) -> bool:
            current = next((row for row in self.db.get_grid_levels(grid_id)
                            if int(row["level_idx"]) == int(level["level_idx"])), None)
            if (current is None or current.get("state") != "BUY_OPEN"
                    or current.get("order_id") is None
                    or int(current["order_id"]) != int(order_id)):
                return False
            cid = (order or {}).get("client_order_id")
            registered = {str(value) for value in
                          (current.get("client_order_id"), current.get("buy_client_order_id")) if value}
            return not cid or not registered or str(cid) in registered

        for level in levels:
            order_id = level.get("order_id")
            if order_id is None:
                continue
            if int(order_id) not in open_ids:
                try:
                    order = self.exchange.get_order(symbol, order_id=int(order_id))
                    if str(order["status"]).upper() == "FILLED":
                        if current_buy_order(level, int(order_id), order):
                            capture_filled_buy(level, int(order_id), order)
                            self._settle_canceled_buy_partial(grid, level, order, "cancel_grid_orders",
                                                              place_sell=False)
                        else:
                            logger.info("grid=%s level=%s action=BUY_PARTIAL_IGNORED reason=level_order_changed",
                                        grid_id, level["level_idx"])
                except Exception as exc:
                    logger.warning("grid=%s level=%s action=CANCEL_STATUS_UNKNOWN order_id=%s reason=%s", grid_id, level["level_idx"], order_id, exc)
                continue
            try:
                result = self.exchange.cancel_order(symbol, int(order_id))
                if level.get("state") == "BUY_OPEN" and _d(result.get("executed_qty", 0)) > 0:
                    if self._settle_canceled_buy_partial(grid, level, result, "cancel_grid_orders",
                                                         place_sell=False):
                        capture_filled_buy(level, int(order_id), result)
                elif result.get("status") == "CANCELED":
                    canceled.append(int(order_id))
                elif result.get("status") == "FILLED":
                    if current_buy_order(level, int(order_id), result):
                        capture_filled_buy(level, int(order_id), result)
                        self._settle_canceled_buy_partial(grid, level, result, "cancel_grid_orders",
                                                          place_sell=False)
                    else:
                        logger.info("grid=%s level=%s action=BUY_PARTIAL_IGNORED reason=level_order_changed",
                                    grid_id, level["level_idx"])
                else:
                    errors.append({"order_id": int(order_id), "reason": f"cancel returned {result.get('status')}"})
            except Exception as exc:
                errors.append({"order_id": int(order_id), "reason": str(exc)})
                try:
                    order = self.exchange.get_order(symbol, order_id=int(order_id))
                    if str(order["status"]).upper() == "FILLED":
                        if (level.get("state") == "BUY_OPEN"
                                and self._settle_canceled_buy_partial(
                                    grid, level, order, "cancel_grid_orders", place_sell=False)):
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
            if _d(row["held_qty"]) > 0
        ]
        return {
            "grid_id": grid_id, "status": new_status, "canceled_order_ids": canceled,
            "filled_during_cancel": filled_during_cancel, "cancel_errors": errors,
            "pending_order_ids": sorted(still_open),
            "remaining_inventory": inventory,
            "unmanaged_inventory": inventory,
        }

    def _close_event(
        self, event_type: str, grid_id: int, details: dict,
        reason: str | None = None, price: Decimal | float | None = None,
        level_idx: int | None = None, client_order_id: str | None = None,
        order_id: int | None = None,
    ) -> None:
        if self.event_sink is not None:
            self._emit(event_type, grid_id, level_idx, client_order_id=client_order_id,
                       order_id=order_id, reason=reason, details=details)
            return
        self.db.add_grid_event(
            run_id=None, source="CLI", grid_id=grid_id, level_idx=level_idx,
            client_order_id=client_order_id, order_id=order_id,
            event_type=event_type, reason=reason,
            price=None if price is None else float(price), details=details,
        )

    def sweep_grid_dust(self, grid_id: int, bid: Any, *, reason: str = "manual") -> dict:
        """Sell only this grid's recorded dust with a persisted recoverable intent."""
        grid = self.db.get_grid(int(grid_id))
        if grid is None:
            raise ValueError(f"grid {grid_id} does not exist")
        filters, _, _ = self._market_context(grid["symbol"])
        dust_cells = sum((_d(row.get("held_qty")) for row in self.db.get_grid_levels(int(grid_id))
                          if row.get("state") == "DONE" and _d(row.get("held_qty")) > 0), Decimal(0))
        sweepable_qty = max(Decimal(0), _d(grid.get("dust_qty") or 0) - dust_cells)
        plan = plan_dust_sweep(sweepable_qty, bid, filters,
                               getattr(self.settings, "fee_pct", None) or .1)
        if not plan["sweepable"]:
            return {**plan, "status": "DEFERRED", "reason": "market_filters"}
        params = dict(grid.get("params") or {})
        intent = params.get("dust_sweep_intent")
        if not intent:
            seq = int(params.get("dust_sweep_seq", 0)) + 1
            cid = f"gS{int(grid_id)}{seq}"
            intent = {"client_order_id": cid, "qty": str(plan["qty"]), "reason": reason}
            params.update(dust_sweep_seq=seq, dust_sweep_intent=intent)
            self.db.update_grid(int(grid_id), params=self.db._json(params))
        cid, qty = str(intent["client_order_id"]), _d(intent["qty"])
        existing = self.exchange.find_order_by_client_id(grid["symbol"], cid)
        try:
            if existing:
                order = self.exchange.get_order(grid["symbol"], order_id=existing["order_id"])
            else:
                accepted = self.exchange.place_order(grid["symbol"], "SELL", qty,
                    order_type="MARKET", client_order_id=cid)
                order = self.exchange.get_order(grid["symbol"], order_id=accepted["order_id"])
            if str(order.get("status", "")).upper() != "FILLED":
                if str(order.get("status", "")).upper() in {"REJECTED", "CANCELED", "EXPIRED"}:
                    params = dict((self.db.get_grid(int(grid_id)) or {}).get("params") or {})
                    params.pop("dust_sweep_intent", None)
                    self.db.update_grid(int(grid_id), params=self.db._json(params))
                    self._close_event("DUST_SWEEP_DEFERRED", int(grid_id),
                                      {"client_order_id": cid, "reason": order.get("status")})
                    return {"status": "DEFERRED", "client_order_id": cid,
                            "reason": order.get("status")}
                return {"status": "PENDING", "client_order_id": cid, "order_id": order.get("order_id")}
            trades = self.exchange.get_my_trades(grid["symbol"], order["order_id"])
            fee = self._fee_value_usdt(trades, grid["symbol"])
            gross = _d(order.get("cummulative_quote_qty", 0))
            avg = gross / qty if qty else _d(bid)
            net = gross - fee
            self.db.settle_grid_dust_sweep(int(grid_id), cid, qty, net, fee)
            current = self.db.get_grid(int(grid_id))
            params = dict(current.get("params") or {})
            params.pop("dust_sweep_intent", None)
            self.db.update_grid(int(grid_id), params=self.db._json(params))
            details = {"qty": str(qty), "price_avg": str(avg), "fee_usdt": str(fee),
                       "residual": str(self.db.get_grid(int(grid_id)).get("dust_qty", 0)),
                       "proceeds_net": str(net), "reason": reason}
            self._close_event("DUST_SWEPT", int(grid_id), details, client_order_id=cid,
                              order_id=int(order["order_id"]), price=avg)
            return {"status": "FILLED", **details, "client_order_id": cid}
        except Exception as exc:
            code = getattr(exc, "code", None)
            if code == -2010 or "filter" in str(exc).casefold() or "notional" in str(exc).casefold():
                current = self.db.get_grid(int(grid_id))
                params = dict((current or {}).get("params") or {})
                params.pop("dust_sweep_intent", None)
                self.db.update_grid(int(grid_id), params=self.db._json(params))
                self._close_event("DUST_SWEEP_DEFERRED", int(grid_id),
                                  {"client_order_id": cid, "reason": str(exc)})
                return {"status": "DEFERRED", "client_order_id": cid, "reason": str(exc)}
            raise

    def _market_sell_owned_cell(
        self, grid: dict, level: dict, filters: SymbolFilters, avg_price: Decimal,
        *, emit_event: bool = True,
    ) -> dict:
        grid_id, idx, symbol = int(grid["id"]), int(level["level_idx"]), grid["symbol"]
        held_qty = _d(level.get("held_qty"))
        qty = filters.round_qty_down(held_qty)
        if qty < filters.min_qty:
            result = {"level_idx": idx, "qty": str(held_qty), "status": "DUST"}
            self.db.add_grid_dust_once(grid_id, f"cell:{idx}:{int(level.get('cycles_completed', 0))}", held_qty)
            current_grid = self.db.get_grid(grid_id)
            if (current_grid.get("strategy") == "smart"
                    and (current_grid.get("params") or {}).get("loans_enabled") is True):
                self._persist_done_to_reserve(grid_id, idx, "dust", {"state": "DONE", "held_qty": 0.0})
            else:
                self.db.update_level(grid_id, idx, state="DONE")
            self._close_event("CELL_DUST", grid_id, result, reason="below market minimum notional", level_idx=idx)
            return result
        if filters.max_qty > 0 and qty > filters.max_qty:
            raise FilterViolation("LOT_SIZE", f"market liquidation quantity {qty} exceeds maxQty")
        if filters.apply_min_to_market and qty * avg_price < filters.min_notional:
            result = {"level_idx": idx, "qty": str(held_qty), "status": "DUST"}
            self.db.add_grid_dust_once(grid_id, f"cell:{idx}:{int(level.get('cycles_completed', 0))}", held_qty)
            current_grid = self.db.get_grid(grid_id)
            if (current_grid.get("strategy") == "smart"
                    and (current_grid.get("params") or {}).get("loans_enabled") is True):
                self._persist_done_to_reserve(grid_id, idx, "dust", {"state": "DONE", "held_qty": 0.0})
            else:
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

        buy_cid = self._actual_buy_client_order_id(grid_id, level)
        buy_order = self.exchange.get_order(symbol, client_order_id=buy_cid)
        buy_trades = self.exchange.get_my_trades(symbol, buy_order["order_id"])
        sell_trades = self.exchange.get_my_trades(symbol, market_order["order_id"])
        buy_fee = self._fee_value_usdt(buy_trades, symbol)
        sell_fee = self._fee_value_usdt(sell_trades, symbol)
        cycle_pnl = (
            _d(market_order["cummulative_quote_qty"])
            - _d(buy_order["cummulative_quote_qty"]) - buy_fee - sell_fee
        )
        completion_fields = {
            "state": "DONE", "cycles_completed": cycle,
            "pnl": float(_d(level.get("pnl")) + cycle_pnl),
            "fee_paid": float(_d(level.get("fee_paid")) + sell_fee),
            "held_qty": 0.0, "order_id": market_order["order_id"],
            "client_order_id": cid, "entry_price": None, "bought_at": None,
            "buy_client_order_id": None,
        }
        current_grid = self.db.get_grid(grid_id)
        if (current_grid.get("strategy") == "smart"
                and (current_grid.get("params") or {}).get("loans_enabled") is True):
            self._persist_done_to_reserve(grid_id, idx, "liquidated", completion_fields)
        else:
            self.db.update_level(grid_id, idx, **completion_fields)
        result = {
            "level_idx": idx, "client_order_id": cid, "order_id": int(market_order["order_id"]),
            "qty": str(_d(market_order["executed_qty"])), "cycle_pnl": str(cycle_pnl),
            "pnl_realized": str(cycle_pnl), "cycles_completed": cycle, "status": "FILLED",
            "price": str(_d(market_order["cummulative_quote_qty"]) / _d(market_order["executed_qty"])),
            "cash_proceeds_net": str(_d(market_order["cummulative_quote_qty"]) - sell_fee),
            "realized_pnl": str(cycle_pnl),
        }
        if emit_event:
            self._close_event(
                "CELL_LIQUIDATED", grid_id, result, level_idx=idx,
                client_order_id=cid, order_id=int(market_order["order_id"]),
            )
        return result

    def _persist_done_to_reserve(self, grid_id: int, level_idx: int, reason: str,
                                 level_updates: dict[str, Any] | None = None) -> dict:
        grid = self.db.get_grid(int(grid_id))
        if (grid is None or grid.get("strategy") != "smart"
                or not (grid.get("params") or {}).get("loans_enabled", False)):
            return {"ok": True, "returned": 0.0, "skipped": "disabled"}
        self.transfer_loans(int(grid_id), f"done_cell:{reason}", level_idx=int(level_idx))
        grid = self.db.get_grid(int(grid_id))
        cell = next((row for row in self.db.get_grid_levels(int(grid_id))
                     if int(row["level_idx"]) == int(level_idx)), None)
        if cell is None:
            return {"ok": False, "reason": "cell_not_found"}
        capital = _d(cell.get("capital", 0))
        if capital == 0 and not level_updates:
            return {"ok": True, "returned": 0.0, "skipped": "already_returned"}
        updated_reserve = _d(grid.get("reserve", 0)) + capital
        self.db.update_levels_and_grid_with_event(int(grid_id),
            level_updates={int(level_idx): {**(level_updates or {}), "capital": 0.0,
                "capital_base": 0.0, "capital_compound": 0.0, "capital_loan": 0.0}},
            grid_fields={"reserve": float(updated_reserve)},
            event={"source": "MONITOR" if self.event_sink else "CLI",
                "run_id": getattr(getattr(self.event_sink, "__self__", None), "_run_id", None),
                "event_type": "RESERVE_RETURNED", "reason": reason,
                "price": self._event_price, "level_idx": int(level_idx),
                "details": {"amount": str(capital), "reserve_before": str(grid.get("reserve", 0)),
                            "reserve_after": str(updated_reserve)}})
        return {"ok": True, "returned": float(capital), "reserve": float(updated_reserve)}

    def return_cell_to_reserve(self, grid_id: int, level_idx: int, reason: str = "cell_done") -> dict:
        grid = self.db.get_grid(int(grid_id))
        cell = next((row for row in self.db.get_grid_levels(int(grid_id))
                     if int(row["level_idx"]) == int(level_idx)), None) if grid else None
        if cell is None or cell.get("state") != "DONE" or _d(cell.get("held_qty", 0)) > 0:
            return {"ok": False, "reason": "cell_not_done_or_has_inventory"}
        return self._persist_done_to_reserve(int(grid_id), int(level_idx), reason)

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
                if (self._settle_canceled_buy_partial(grid, level, result, "close_repository")
                        if level.get("state") == "BUY_OPEN" else False):
                    continue
                elif status == "FILLED":
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
                    strategy="repository", params=None,
                )).inserted_primary_key[0])
                conn.execute(self.db.grid_events.insert().values(
                    run_id=None, source="MONITOR" if self.event_sink is not None else "CLI", ts=db_now, grid_id=repository_id,
                    event_type="REPOSITORY_CREATED", details=self.db._json({
                        "symbol": symbol, "origin_grid_id": grid_id,
                        **({"cash_total": (grid.get("params") or {}).get("target_close_plan", {}).get("cash_total"),
                            "equity_total_at_close": (grid.get("params") or {}).get("target_close_plan", {}).get("equity_total_at_close")}
                           if (grid.get("params") or {}).get("target_close_plan") else {})}),
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
                        "buy_client_order_id": self._actual_buy_client_order_id(grid_id, level),
                        "moved_at": now.isoformat(),
                        "market_mid": None if market_mid is None else str(market_mid),
                    }
                    target_close = (grid.get("params") or {}).get("target_close_plan") or {}
                    if target_close:
                        detail.update({"cash_total": target_close.get("cash_total"),
                                       "equity_total_at_close": target_close.get("equity_total_at_close")})
                    conn.execute(self.db.grid_events.insert().values(
                        run_id=None, source="MONITOR" if self.event_sink is not None else "CLI", ts=db_now, grid_id=repository_id,
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

    def close_grid(self, grid_id: int, mode: str, *, close_reason: str = "grid_close") -> dict:
        mode = str(mode).lower()
        if mode not in {"cancel", "liquidate", "repository", "profit_repository"}:
            raise ValueError("mode must be cancel, liquidate, repository, or profit_repository")
        grid = self.db.get_grid(grid_id)
        if grid is None:
            raise ValueError(f"grid {grid_id} does not exist")
        if not self.db.transition_grid_status(grid_id, {"ACTIVE", "PAUSED", "CLOSING"}, "CLOSING"):
            raise ValueError(f"grid {grid_id} cannot close from status {grid['status']}")
        if grid.get("strategy") == "smart" and (grid.get("params") or {}).get("loans_enabled") is True:
            self.transfer_loans(int(grid_id), "before_close")
        start_details = {"mode": mode, "close_reason": close_reason}
        target_plan = (grid.get("params") or {}).get("target_close_plan")
        if target_plan:
            start_details.update({"cash_total": target_plan.get("cash_total"),
                                  "equity_total_at_close": target_plan.get("equity_total_at_close")})
        self._close_event("GRID_CLOSE_STARTED", grid_id, start_details)
        try:
            self.sync_closing(grid_id)
        except Exception as exc:
            logger.exception("grid=%s close synchronization failed", grid_id)
            self.db.update_grid(grid_id, status="CLOSING", fail_reason=f"sync_closing failed: {exc}")

        if mode == "profit_repository":
            try:
                filters, snapshot, avg_price = self._market_context(grid["symbol"])
                bid = _d(snapshot["bid_price"])
                levels = self.db.get_grid_levels(int(grid_id))
                cells = self._build_profit_cells(int(grid_id), grid["symbol"], levels)
                held_basis = sum((_d(row["entry_cost"]) + _d(row["entry_fee_usdt"])
                                  for row in cells), Decimal(0))
                selected = plan_profit_close(cells, float(bid), filters, DEFAULT_GRID_FEE_PCT)
                cash_now = float(_d(grid.get("capital_total")) + sum(
                    (_d(row.get("pnl") or 0) for row in levels), Decimal(0)) - held_basis)
                plan = {**selected, "reason": "PROFIT_CLOSE", "phase": "MARKED",
                    "basis": "profit_repository", "cash_now": cash_now,
                    "projected_cash": cash_now + selected["projected_cash"],
                    "cash_total": cash_now, "target_threshold": 0,
                    "target_missed_after_fills": False, "sold_done": [],
                    "sale_failures": [], "actual_market_proceeds": [],
                    "retry_count": 0, "last_retry_at": None,
                    "retry_exhausted_event_emitted": False}
                params = dict(grid.get("params") or {})
                params["target_close_plan"] = plan
                self.db.update_grid(int(grid_id), status="CLOSING", fail_reason="PROFIT_CLOSE",
                                    params=self.db._json(params))
                self._close_event("PROFIT_CLOSE", int(grid_id), {
                    "sell_cells": plan["sell_cells"], "repo_cells": plan["repo_cells"],
                    "bid_used": float(bid), "projected_cash": plan["projected_cash"],
                    "net_gain_usdt": plan["net_gain_usdt"], "net_loss_usdt": plan["net_loss_usdt"],
                    "estimated_commission_usdt": plan["estimated_commission_usdt"]}, price=bid)
                result = self.close_grid_target(int(grid_id), plan, float(bid))
            except Exception as exc:
                self.db.update_grid(int(grid_id), status="CLOSING", fail_reason=f"PROFIT_CLOSE: {exc}")
                result = {"mode": mode, "status": "CLOSING", "errors": [{"reason": str(exc)}]}
        elif mode == "cancel":
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
                    if sold["status"] == "DUST":
                        self.return_cell_to_reserve(grid_id, int(level["level_idx"]), "close_dust")
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
        if result.get("status") == "CLOSED" and mode != "profit_repository":
            try:
                _, _, close_bid = self._market_context(grid["symbol"])
                result["dust_sweep"] = self.sweep_grid_dust(grid_id, close_bid, reason=close_reason)
            except Exception as exc:
                result.setdefault("errors", []).append({"reason": f"dust sweep failed: {exc}"})
                self.db.update_grid(grid_id, status="CLOSING", fail_reason=str(exc))
                result["status"] = "CLOSING"
        if target_plan:
            result.update({"cash_total": target_plan.get("cash_total"),
                           "equity_total_at_close": target_plan.get("equity_total_at_close"),
                           "target_missed_after_fills": target_plan.get("target_missed_after_fills", False)})
        self._close_event("GRID_CLOSED", grid_id, result)
        return result

    def _mark_profit_close_retry_exhausted(self, grid_id: int, plan: dict) -> None:
        previous = self.db.get_last_event(int(grid_id), "PROFIT_CLOSE_MARKET_SELL_FAILED")
        if not (previous and previous.get("reason") == "retry_exhausted"):
            self._close_event("PROFIT_CLOSE_MARKET_SELL_FAILED", int(grid_id),
                              {"retry_count": int(plan.get("retry_count", 0))},
                              reason="retry_exhausted", price=plan.get("bid_used"))
        plan["retry_exhausted_event_emitted"] = True
        self._persist_target_plan(int(grid_id), plan)
        self.db.update_grid(int(grid_id), status="CLOSING",
                            fail_reason="PROFIT_CLOSE_RETRY_EXHAUSTED")

    def close_grid_target(self, grid_id: int, target: dict, price: float | None = None) -> dict:
        """Write-ahead, resumable target close; market-sell winners then use the repository path."""
        grid = self.db.get_grid(int(grid_id))
        if grid is None:
            raise ValueError(f"grid {grid_id} does not exist")
        params = dict(grid.get("params") or {})
        plan = dict(params.get("target_close_plan") or target)
        requested_reason = str(plan.get("reason") or "TARGET_REACHED")
        close_event_type = requested_reason if requested_reason in {"PROFIT_CLOSE", "TARGET_REACHED"} else "TARGET_REACHED"
        if not params.get("target_close_plan"):
            if not plan.get("sell_cells") and plan.get("projected_cash") is None:
                raise ValueError("target close requires a target_close_plan or a non-empty target")
            levels = self.db.get_grid_levels(int(grid_id))
            sold = {int(row["level_idx"]) for row in plan.get("sell_cells", [])}
            repo = [row for row in levels if _d(row.get("held_qty")) > 0 and
                    int(row["level_idx"]) not in sold]
            bid = _d(plan.get("bid_used", price or 0))
            equity_at_close = _d(plan.get("projected_cash", 0)) + sum(
                (_d(row.get("held_qty")) * bid * Decimal("0.999") for row in repo), Decimal(0))
            plan.update({"reason": close_event_type, "phase": "MARKED", "sold_done": [],
                         "sale_failures": [], "actual_market_proceeds": [],
                         "cash_total": float(plan.get("projected_cash", 0)),
                         "equity_total_at_close": float(equity_at_close),
                         "target_missed_after_fills": False})
            params["target_close_plan"] = plan
            self.db.update_grid(int(grid_id), status="CLOSING", fail_reason=close_event_type,
                                params=self.db._json(params))
            self._close_event(close_event_type, int(grid_id), {
                "effective_params": {key: params.get(key) for key in
                    ("target_pct", "target_usdt", "target_basis")},
                "basis": plan.get("basis"), "capital_initial": grid.get("capital_total"),
                "cash_now": plan.get("cash_now"), "projected_cash": plan.get("projected_cash"),
                "cash_total": plan.get("cash_total"),
                "equity_now": plan.get("equity_now"),
                "equity_total_at_close": plan.get("equity_total_at_close"),
                "sell_cells": plan.get("sell_cells", []), "repo_cells": [
                    {"level_idx": int(row["level_idx"]), "reason": "not_sold_by_target_plan"}
                    for row in repo], "estimated_fees_usdt": plan.get("estimated_fees_usdt", 0),
                "bid_used": plan.get("bid_used", price),
            }, reason=close_event_type, price=plan.get("bid_used", price))
        else:
            self.db.update_grid(int(grid_id), status="CLOSING", fail_reason=close_event_type)

        grid = self.db.get_grid(int(grid_id))
        plan = dict((grid.get("params") or {}).get("target_close_plan") or plan)
        retrying_profit_failure = (close_event_type == "PROFIT_CLOSE"
                                   and plan.get("phase") == "BUYS_CANCELED"
                                   and bool(plan.get("sale_failures")))
        if retrying_profit_failure:
            if int(plan.get("retry_count", 0)) >= PROFIT_CLOSE_RETRY_MAX:
                self._mark_profit_close_retry_exhausted(int(grid_id), plan)
                return {"status": "CLOSING", "phase": "RETRY_EXHAUSTED"}
            plan["retry_count"] = int(plan.get("retry_count", 0)) + 1
            plan["last_retry_at"] = self._utcnow().astimezone(timezone.utc).isoformat()
            plan["sale_failures"] = []
            self._persist_target_plan(int(grid_id), plan)
        symbol = grid["symbol"]
        filters, _, avg_price = self._market_context(symbol)
        if plan.get("phase") == "MARKED":
            for level in self.db.get_grid_levels(int(grid_id)):
                if level.get("state") != "BUY_OPEN":
                    continue
                order_id = level.get("order_id")
                if order_id is None and level.get("client_order_id"):
                    found = self.exchange.find_order_by_client_id(symbol, level["client_order_id"])
                    order_id = found["order_id"] if found else None
                if order_id is not None:
                    canceled = self.exchange.cancel_order(symbol, int(order_id))
                    result_status = str(canceled.get("status", "")).upper()
                    filled_during_cancel = result_status == "FILLED"
                    if filled_during_cancel:
                        self.sync_closing(int(grid_id))
                    elif _d(canceled.get("executed_qty", 0)) > 0:
                        self._settle_canceled_buy_partial(grid, level, canceled,
                                                          "close_grid_target", place_sell=True)
                    elif result_status not in {"CANCELED", "EXPIRED", "REJECTED"}:
                        return {"status": "CLOSING", "phase": "CANCEL_BUYS", "errors": [
                            {"level_idx": int(level["level_idx"]), "status": result_status}]}
                    if not filled_during_cancel:
                        self.db.update_level(int(grid_id), int(level["level_idx"]), state="IDLE", order_id=None)
                else:
                    self.db.update_level(int(grid_id), int(level["level_idx"]), state="IDLE", order_id=None)
            plan["phase"] = "BUYS_CANCELED"
            self._persist_target_plan(int(grid_id), plan)

        if plan.get("phase") == "BUYS_CANCELED":
            for selection in plan.get("sell_cells", []):
                idx = int(selection["level_idx"])
                if any(int(row.get("level_idx", -1)) == idx for row in plan.get("sale_failures", [])):
                    continue
                level = next((row for row in self.db.get_grid_levels(int(grid_id))
                              if int(row["level_idx"]) == idx), None)
                if level is None or _d(level.get("held_qty")) <= 0:
                    continue
                try:
                    sold = self._market_sell_owned_cell(grid, level, filters, avg_price, emit_event=False)
                    if sold.get("status") == "FILLED":
                        plan.setdefault("sold_done", []).append(idx)
                        plan.setdefault("actual_market_proceeds", []).append({
                            "level_idx": idx, "proceeds_usdt": str(sold.get("cash_proceeds_net") or
                                _d(sold.get("qty")) * _d(sold.get("price")))})
                    else:
                        plan.setdefault("sale_failures", []).append({"level_idx": idx, "reason": sold.get("status")})
                except Exception as exc:
                    cid = f"g{int(grid_id)}L{idx}X{int(level.get('cycles_completed', 0))}"
                    found = self.exchange.find_order_by_client_id(symbol, cid)
                    recovered_status = (str(self.exchange.get_order(symbol, order_id=found["order_id"])
                                             .get("status", "")).upper() if found else "")
                    if recovered_status == "FILLED":
                        recovered = self._market_sell_owned_cell(grid, level, filters, avg_price, emit_event=False)
                        plan.setdefault("sold_done", []).append(idx)
                        plan.setdefault("actual_market_proceeds", []).append({
                            "level_idx": idx, "proceeds_usdt": str(recovered.get("cash_proceeds_net") or
                                _d(recovered.get("qty")) * _d(recovered.get("price")))})
                        self._persist_target_plan(int(grid_id), plan)
                        continue
                    if recovered_status in {"NEW", "PARTIALLY_FILLED", "PENDING_CANCEL"}:
                        plan["phase"] = "BUYS_CANCELED"
                        self._persist_target_plan(int(grid_id), plan)
                        return {"status": "CLOSING", "phase": "MARKET_SELL_PENDING",
                                "level_idx": idx, "client_order_id": cid}
                    if found and recovered_status not in {"REJECTED", "CANCELED", "EXPIRED"}:
                        return {"status": "CLOSING", "phase": "MARKET_SELL_STATUS_UNKNOWN",
                                "level_idx": idx, "client_order_id": cid,
                                "order_status": recovered_status}
                    plan.setdefault("sale_failures", []).append({"level_idx": idx, "reason": str(exc)})
                    if close_event_type == "PROFIT_CLOSE":
                        plan.setdefault("retry_count", 0)
                        plan["last_retry_at"] = self._utcnow().astimezone(timezone.utc).isoformat()
                    failure_event = ("PROFIT_CLOSE_MARKET_SELL_FAILED" if close_event_type == "PROFIT_CLOSE"
                                     else "TARGET_MARKET_SELL_FAILED")
                    self._close_event(failure_event, int(grid_id),
                                      {"level_idx": idx, "reason": str(exc),
                                       "cash_total": plan.get("cash_total"),
                                       "equity_total_at_close": plan.get("equity_total_at_close")}, reason="market_sell_failed",
                                      level_idx=idx, price=plan.get("bid_used", price))
                    self._persist_target_plan(int(grid_id), plan)
                    if close_event_type != "PROFIT_CLOSE":
                        current = next((row for row in self.db.get_grid_levels(int(grid_id))
                                        if int(row["level_idx"]) == idx), None)
                        if current and _d(current.get("held_qty")) > 0:
                            self._place_level_intent(int(grid_id), current, "SELL", _d(current["held_qty"]),
                                                     _d(current["sell_price"]), filters, avg_price)
                self._persist_target_plan(int(grid_id), plan)
            if close_event_type == "PROFIT_CLOSE" and plan.get("sale_failures"):
                plan["phase"] = "BUYS_CANCELED"
                if int(plan.get("retry_count", 0)) >= PROFIT_CLOSE_RETRY_MAX:
                    self._mark_profit_close_retry_exhausted(int(grid_id), plan)
                else:
                    self._persist_target_plan(int(grid_id), plan)
                    self.db.update_grid(int(grid_id), status="CLOSING", fail_reason="PROFIT_CLOSE")
                return {"status": "CLOSING", "phase": "MARKET_SELL_FAILED",
                        "sale_failures": plan["sale_failures"]}
            if close_event_type == "PROFIT_CLOSE":
                plan["retry_count"] = 0
                plan["last_retry_at"] = None
                plan["retry_exhausted_event_emitted"] = False
                self._persist_target_plan(int(grid_id), plan)
            # The target close is already write-ahead persisted; sweep can be
            # recovered by the same deterministic CID if the process restarts.
            sweep = self.sweep_grid_dust(int(grid_id), plan.get("bid_used", price or 0), reason="target")
            if sweep.get("status") == "PENDING":
                return {"status": "CLOSING", "phase": "DUST_SWEEP_PENDING",
                        "client_order_id": sweep.get("client_order_id")}
            plan["phase"] = "MARKET_SELLS_DONE"
            plan["cash_total"] = float(_d(plan.get("cash_now")) + sum(
                (_d(row["proceeds_usdt"]) for row in plan.get("actual_market_proceeds", [])), Decimal(0)))
            remaining_levels = self.db.get_grid_levels(int(grid_id))
            remaining_value = sum((_d(row.get("held_qty")) * _d(plan.get("bid_used")) * Decimal("0.999")
                                   for row in remaining_levels), Decimal(0))
            plan["equity_total_at_close"] = float(_d(plan["cash_total"]) + remaining_value)
            threshold = _d(plan.get("target_threshold", 0))
            achieved_value = _d(plan["cash_total"] if plan.get("basis") == "cash"
                                else plan["equity_total_at_close"])
            plan["target_missed_after_fills"] = bool(plan.get("sale_failures")) and achieved_value < threshold
            self._persist_target_plan(int(grid_id), plan)

        if plan.get("phase") == "MARKET_SELLS_DONE":
            for level in self.db.get_grid_levels(int(grid_id)):
                if _d(level.get("held_qty")) <= 0:
                    continue
                rearm_cid = None
                live = False
                if level.get("state") == "SELL_OPEN" and level.get("order_id") is None \
                        and level.get("client_order_id"):
                    found = self.exchange.find_order_by_client_id(symbol, level["client_order_id"])
                    if found:
                        level = self.db.update_level(int(grid_id), int(level["level_idx"]),
                                                     order_id=int(found["order_id"])) or level
                        order_status = str(found.get("status", "")).upper()
                        live = order_status in {"NEW", "PARTIALLY_FILLED"}
                        if order_status == "FILLED":
                            self.sync_closing(int(grid_id))
                            refreshed = next((row for row in self.db.get_grid_levels(int(grid_id))
                                              if int(row["level_idx"]) == int(level["level_idx"])), None)
                            if not refreshed or _d(refreshed.get("held_qty")) <= 0:
                                continue
                            level = refreshed
                    else:
                        rearm_cid = str(level["client_order_id"])
                if level.get("state") == "SELL_OPEN" and level.get("order_id") is not None:
                    try:
                        order = self.exchange.get_order(symbol, order_id=int(level["order_id"]))
                        order_status = str(order.get("status", "")).upper()
                        if order_status == "FILLED":
                            self.sync_closing(int(grid_id))
                            refreshed = next((row for row in self.db.get_grid_levels(int(grid_id))
                                              if int(row["level_idx"]) == int(level["level_idx"])), None)
                            if not refreshed or _d(refreshed.get("held_qty")) <= 0:
                                continue
                            level = refreshed
                        else:
                            live = order_status in {"NEW", "PARTIALLY_FILLED"}
                    except Exception as exc:
                        self._persist_target_plan(int(grid_id), plan)
                        return {"status": "CLOSING", "phase": "REPOSITORY_SELL_STATUS_UNKNOWN",
                                "level_idx": int(level["level_idx"]), "reason": str(exc)}
                if not live:
                    if rearm_cid is None:
                        previous_cid = str(level.get("client_order_id") or "")
                        rearm_cid = "gT" + hashlib.sha256(
                            f"{grid_id}:{level['level_idx']}:{level['cycles_completed']}:{previous_cid}:target-repository".encode()
                        ).hexdigest()[:30]
                    self._place_level_intent(int(grid_id), level, "SELL", _d(level["held_qty"]),
                                             _d(level["sell_price"]), filters, avg_price,
                                             client_order_id=rearm_cid)
                rearm_cid = None
            plan["phase"] = "REPOSITORY"
            self._persist_target_plan(int(grid_id), plan)

        if plan.get("phase") == "REPOSITORY":
            result = ({"status": "CLOSED", "mode": "repository", "recovered": True}
                      if grid.get("status") == "CLOSED" else self.close_grid(int(grid_id), "repository"))
            if result.get("status") == "CLOSED":
                params = dict((self.db.get_grid(int(grid_id)) or {}).get("params") or {})
                final_plan = dict(params.get("target_close_plan") or plan)
                final_plan["phase"] = "COMPLETE"
                final_plan["target_missed_after_fills"] = bool(final_plan.get("sale_failures"))
                params["target_close_plan"] = final_plan
                self.db.update_grid(int(grid_id), params=self.db._json(params),
                                    fail_reason=(close_event_type if final_plan["sale_failures"] else None))
                result.update({"cash_total": final_plan.get("cash_total"),
                               "equity_total_at_close": final_plan.get("equity_total_at_close"),
                               "target_missed_after_fills": final_plan["target_missed_after_fills"]})
            return result
        return {"status": "CLOSING", "phase": plan.get("phase")}

    def _persist_target_plan(self, grid_id: int, plan: dict) -> None:
        grid = self.db.get_grid(int(grid_id))
        params = dict((grid or {}).get("params") or {})
        params["target_close_plan"] = plan
        self.db.update_grid(int(grid_id), status="CLOSING",
                            fail_reason=str(plan.get("reason") or "TARGET_REACHED"),
                            params=self.db._json(params))
