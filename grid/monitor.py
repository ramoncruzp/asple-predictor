"""Periodic audit and synchronization loop for active spot grids."""

from __future__ import annotations

import logging
import math
import threading
import time
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Callable

from apscheduler.schedulers.background import BackgroundScheduler
from grid.policy import adjust_decision, evaluate_grid, evaluate_target, evaluate_max_days, plan_dust_sweep, stoploss_candidates, PolicyDecision
from grid.volatility_provider import VolatilityProvider

logger = logging.getLogger(__name__)


def _d(value: Any) -> Decimal:
    return Decimal(str(value or 0))


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class GridMonitor:
    def __init__(
        self, db: Any, exchange: Any, engine: Any, settings: Any,
        scheduler: Any = None, clock: Callable[[], datetime] | None = None,
        vol_provider: Any = None,
    ):
        self.db = db
        self.exchange = exchange
        self.engine = engine
        self.settings = settings
        self.scheduler = scheduler or BackgroundScheduler()
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.vol_provider = vol_provider or VolatilityProvider(db, clock=self.clock)
        self.logger = logging.getLogger(__name__)
        self._run_id: int | None = None
        self._event_count = 0
        self.ops_lock = threading.RLock()

    def _emit(self, event: dict) -> None:
        if event.get("persisted"):
            self._event_count += 1
            return
        saved = self.db.add_grid_event(
            run_id=self._run_id, source="MONITOR", grid_id=event.get("grid_id"),
            level_idx=event.get("level_idx"), client_order_id=event.get("client_order_id"),
            order_id=event.get("order_id"), event_type=event["event_type"],
            reason=event.get("reason"), price=event.get("price"), details=event.get("details"),
        )
        if saved:
            self._event_count += 1

    def start(self) -> None:
        interval = max(1, int(self.settings.grid_monitor_interval))
        self.scheduler.add_job(
            self.run_once, "interval", seconds=interval, args=["SCHEDULED"],
            id="grid_monitor", replace_existing=True, max_instances=1,
            coalesce=True, misfire_grace_time=max(60, interval),
        )
        self.scheduler.add_job(
            self.run_once, "date", run_date=self.clock(), args=["STARTUP"],
            id="grid_monitor_startup", replace_existing=True, misfire_grace_time=300,
        )
        self.scheduler.start()
        self.logger.info("Grid monitor scheduled every %s seconds with immediate startup pass", interval)

    def stop(self) -> None:
        if getattr(self.scheduler, "running", False):
            self.scheduler.shutdown(wait=True)

    def _market_mids(self, grids: list[dict]) -> dict[str, float | None]:
        mids: dict[str, float | None] = {}
        for symbol in sorted({str(grid["symbol"]) for grid in grids}):
            try:
                book = self.exchange.get_book_ticker(symbol)
                mids[symbol] = float((_d(book["bid_price"]) + _d(book["ask_price"])) / 2)
            except Exception:
                self.logger.warning("grid snapshot mid unavailable for %s", symbol, exc_info=True)
                mids[symbol] = None
        return mids

    def _repository_origins(self, grid_id: int) -> dict[int, tuple[dict, datetime]]:
        events = self.db.list_grid_events(grid_id=grid_id, event_type="CELL_MOVED_TO_REPOSITORY", limit=5000)
        origins = {}
        for event in reversed(events):
            details = event.get("details") or {}
            if event.get("level_idx") is not None:
                origins[int(event["level_idx"])] = (details, _utc(event["ts"]))
        return origins

    def _snapshot_grid(self, run_id: int, grid: dict, mid: float | None,
                       policy_metrics: dict | None = None) -> int:
        grid_id = int(grid["id"])
        levels = self.db.get_grid_levels(grid_id)
        repository = grid["status"] == "HOLDING"
        origins = self._repository_origins(grid_id) if repository else {}
        ts = self.clock()
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        rows = []
        inventory = Decimal(0)
        open_count = 0
        for level in levels:
            qty = _d(level.get("held_qty"))
            buy_price = _d(level.get("price"))
            if qty > 0 and mid is not None:
                inventory += qty * _d(mid)
            if level["state"] in {"BUY_OPEN", "SELL_OPEN"}:
                open_count += 1
            origin, moved_at = origins.get(int(level["level_idx"]), ({}, None))
            unrealized = (Decimal(str(mid)) - buy_price) * qty if qty > 0 and mid is not None else None
            rows.append({
                "run_id": run_id, "ts": ts, "grid_id": grid_id,
                "level_idx": int(level["level_idx"]), "symbol": grid["symbol"],
                "grid_status": grid["status"], "level_state": level["state"],
                "buy_price": float(buy_price), "sell_price": float(_d(level.get("sell_price"))),
                "held_qty": float(qty), "cycles_completed": int(level.get("cycles_completed", 0)),
                "pnl_realized": float(_d(level.get("pnl"))), "fee_paid": float(_d(level.get("fee_paid"))),
                "market_mid": mid, "unrealized_pnl": None if unrealized is None else float(unrealized),
                "open_orders_db": None, "inventory_value_usdt": None,
                "in_repository": int(repository), "origin_grid_id": origin.get("origin_grid_id"),
                "origin_level_idx": origin.get("origin_level_idx"),
                "age_hours": None if moved_at is None else max(0.0, (_utc(ts) - moved_at).total_seconds() / 3600),
            })
        rows.append({
            "run_id": run_id, "ts": ts, "grid_id": grid_id, "level_idx": None,
            "symbol": grid["symbol"], "grid_status": grid["status"], "level_state": None,
            "buy_price": None, "sell_price": None, "held_qty": None, "cycles_completed": None,
            "pnl_realized": float(sum((_d(level.get("pnl")) for level in levels), Decimal(0))),
            "fee_paid": float(sum((_d(level.get("fee_paid")) for level in levels), Decimal(0))
                              + _d((grid.get("params") or {}).get("dust_sweep_fee_usdt", 0))),
            "market_mid": mid, "unrealized_pnl": None, "open_orders_db": open_count,
            "inventory_value_usdt": float(inventory) if mid is not None else None,
            "in_repository": int(repository), "origin_grid_id": None,
            "origin_level_idx": None, "age_hours": None,
            "break_prob": (policy_metrics or {}).get("break_prob"),
            "sigma_24h": (policy_metrics or {}).get("sigma_24h"),
            "trapped_capital_pct": (policy_metrics or {}).get("trapped_capital_pct"),
            "free_cells": (policy_metrics or {}).get("free_cells"),
        })
        return self.db.add_snapshots(rows)

    def _close_pending(self, run_id: int, grid: dict, now: datetime) -> None:
        started = grid.get("created_at") or now
        close_events = self.db.list_grid_events(grid_id=int(grid["id"]), event_type="GRID_CLOSE_STARTED", limit=1)
        if close_events:
            started = close_events[0]["ts"]
        age = (_utc(now) - _utc(started)).total_seconds() / 60
        if age <= 30:
            return
        previous = self.db.list_grid_events(grid_id=int(grid["id"]), event_type="CLOSE_PENDING", limit=1)
        if previous and (_utc(now) - _utc(previous[0]["ts"])).total_seconds() < 1800:
            return
        self._emit({
            "event_type": "CLOSE_PENDING", "grid_id": int(grid["id"]),
            "reason": "grid remains CLOSING for more than 30 minutes",
            "price": None, "details": {"age_minutes": round(age, 2)},
        })

    def run_once(self, trigger: str = "SCHEDULED") -> dict:
        with self.ops_lock:
            return self._run_once_locked(trigger)

    def _run_once_locked(self, trigger: str = "SCHEDULED") -> dict:
        trigger = str(trigger).upper()
        started_clock = time.monotonic()
        run = self.db.start_monitor_run(trigger)
        run_id = int(run["id"])
        self._run_id, self._event_count = run_id, 0
        old_sink = getattr(self.engine, "event_sink", None)
        self.engine.event_sink = self._emit
        checked = failed = 0
        unexpected = None
        def snapshot_grid(grid: dict, price: float | None) -> None:
            nonlocal failed
            try:
                self._snapshot_grid(run_id, grid, price)
            except Exception as exc:
                failed += 1
                self.logger.warning("grid=%s monitor snapshot failed", grid["id"], exc_info=True)
                self._emit({
                    "event_type": "SNAPSHOT_FAILED", "grid_id": int(grid["id"]),
                    "reason": str(exc), "price": price, "details": {},
                })
        try:
            if trigger == "STARTUP":
                self.db.mark_stale_runs_interrupted(before_id=run_id)
            previous = self.db.get_last_monitor_run(exclude_id=run_id)
            now = self.clock()
            if previous is not None:
                gap_minutes = (_utc(run["started_at"]) - _utc(previous["started_at"])).total_seconds() / 60
                if gap_minutes > int(self.settings.grid_monitor_gap_minutes):
                    self._emit({
                        "event_type": "RUN_GAP", "grid_id": None,
                        "reason": "monitor interval exceeded configured gap",
                        "price": None, "details": {
                            "minutes": round(gap_minutes, 2), "previous_run_id": previous["id"],
                        },
                    })

            normal_grids = self.db.list_grids_by_status({"ACTIVE", "PAUSED"})
            repositories = self.db.list_grids_by_status({"HOLDING"})
            closings = self.db.list_grids_by_status({"CLOSING"})
            closed_targets = [grid for grid in self.db.list_grids_by_status({"CLOSED"})
                              if (grid.get("params") or {}).get("target_close_plan") is not None
                              and (grid.get("params") or {}).get("target_close_plan", {}).get("phase") != "COMPLETE"]
            closed_max_days = [grid for grid in self.db.list_grids_by_status({"CLOSED"})
                if (grid.get("params") or {}).get("max_days_close_plan", {}).get("phase") == "STARTED"
                and self.db.get_last_event(int(grid["id"]), "MAX_DAYS_REACHED") is None]
            all_grids = normal_grids + repositories + closings + closed_targets + closed_max_days
            mids = self._market_mids(all_grids)
            policy_enabled = bool(getattr(self.settings, "grid_policy_enabled", True))
            vol_cache: dict[str, Any] = {}

            def unavailable_event(grid: dict, reason: str) -> None:
                previous_event = self.db.get_last_event(int(grid["id"]), "VOL_UNAVAILABLE")
                if previous_event and (_utc(now) - _utc(previous_event["ts"])).total_seconds() < 6 * 3600:
                    return
                self._emit({"event_type": "VOL_UNAVAILABLE", "grid_id": int(grid["id"]),
                            "reason": reason, "price": mids.get(grid["symbol"]), "details": {"reason": reason}})

            def start_max_days_plan(grid: dict, levels: list[dict], expiry: dict, price: float) -> dict:
                held = [row for row in levels if _d(row.get("held_qty")) > 0]
                plan = {**expiry,
                    "repository_cells": [int(row["level_idx"]) for row in held],
                    "unrealized_pnl": sum(float(row.get("held_qty") or 0)
                        * (float(price) * .999 - float(row.get("entry_price") or 0) * 1.001)
                        for row in held), "phase": "STARTED"}
                params = dict(grid.get("params") or {})
                params["max_days_close_plan"] = plan
                self.db.update_grid(int(grid["id"]), params=self.db._json(params))
                return plan

            def emit_max_days_reached(grid_id: int, price: float | None,
                                      close_status: str) -> None:
                if self.db.get_last_event(int(grid_id), "MAX_DAYS_REACHED") is not None:
                    return
                grid = self.db.get_grid(int(grid_id)) or {}
                params = dict(grid.get("params") or {})
                plan = dict(params.get("max_days_close_plan") or {})
                swept = self.db.get_last_event(int(grid_id), "DUST_SWEPT")
                swept_details = (swept or {}).get("details") or {}
                details = {key: value for key, value in plan.items() if key != "phase"}
                details.update({"dust_swept": swept_details.get("qty", "0")
                                    if swept_details.get("reason") == "max_days" else "0",
                                "dust_pending": str(grid.get("dust_qty", 0)),
                                "close_status": close_status})
                self._emit({"event_type": "MAX_DAYS_REACHED", "grid_id": int(grid_id),
                    "reason": "max_days", "price": price, "details": details})
                plan["phase"] = "EVENT_EMITTED"
                params["max_days_close_plan"] = plan
                self.db.update_grid(int(grid_id), params=self.db._json(params))

            def process_grid(grid: dict) -> None:
                nonlocal failed
                grid_id, status_before = int(grid["id"]), grid["status"]
                mid = mids.get(grid["symbol"])
                metrics: dict[str, Any] = {}
                adjusted_this_pass = False
                try:
                    current = self.db.get_grid(grid_id) or grid
                    view = None
                    if policy_enabled:
                        if grid["symbol"] not in vol_cache:
                            vol_cache[grid["symbol"]] = self.vol_provider.get(grid["symbol"])
                        view = vol_cache[grid["symbol"]]
                    if policy_enabled and mid is not None:
                        levels = self.db.get_grid_levels(grid_id)
                        if current["status"] in {"ACTIVE", "PAUSED", "HOLDING"}:
                            for cell in stoploss_candidates(levels, mid):
                                try:
                                    outcome = self.engine.stoploss_cell(
                                        grid_id, int(cell["level_idx"]), "stop_loss_pct",
                                        {"params": current.get("params"), "mid": mid,
                                         "sigma_24h": None if view is None else view.sigma_24h,
                                         "sigma_h": None, "z_low": None, "z_high": None,
                                         "break_prob": None, "trapped_capital_pct": None,
                                         "free_cells": sum(row["state"] in {"BUY_OPEN", "IDLE"} for row in levels),
                                         "total_pnl_pct": None, "pause_hours": None},
                                    )
                                    if not outcome.get("ok"):
                                        raise RuntimeError(outcome.get("reason") or outcome.get("status"))
                                except Exception as exc:
                                    failed += 1
                                    self._emit({"event_type": "POLICY_ACTION_FAILED", "grid_id": grid_id,
                                                "reason": str(exc), "price": mid,
                                                "details": {"action": "STOPLOSS", "level_idx": cell["level_idx"]}})
                                    self.logger.warning("grid=%s stop-loss action failed", grid_id, exc_info=True)
                            levels = self.db.get_grid_levels(grid_id)

                    # Retry triggered stop-losses before sync can classify their
                    # canceled limit sell as an external cancellation.
                    if status_before == "ACTIVE":
                        self.engine.sync_grid(grid_id)
                    elif status_before == "PAUSED":
                        self.engine.sync_paused(grid_id)
                    elif status_before == "HOLDING":
                        self.engine.sync_repository(grid_id)
                    current = self.db.get_grid(grid_id) or current
                    max_days_result = evaluate_max_days(current.get("params") or {}, current.get("created_at"), now)
                    max_days_plan_started = ((current.get("params") or {}).get(
                        "max_days_close_plan", {}).get("phase") == "STARTED")
                    if max_days_plan_started:
                        max_days_result = {**(current.get("params") or {}).get("max_days_close_plan", {}),
                                           "expired": True}

                    if policy_enabled and mid is not None and current.get("strategy", "simple") == "smart" \
                            and current["status"] in {"ACTIVE", "PAUSED"}:
                        levels = self.db.get_grid_levels(grid_id)
                        try:
                            sigma = None if view is None else float(view.sigma_24h)
                        except (TypeError, ValueError, OverflowError):
                            sigma = None
                        if sigma is not None and (not math.isfinite(sigma) or sigma <= 0):
                            sigma = None
                        if sigma is None:
                            unavailable_event(grid, getattr(self.vol_provider, "last_reason", None) or "unavailable")
                        last_pause = self.db.get_last_event(grid_id, "GRID_PAUSED")
                        paused_since = last_pause.get("ts") if last_pause else now
                        pause_reasons = None
                        if last_pause:
                            pause_details = last_pause.get("details") or {}
                            values = pause_details.get("reasons")
                            pause_reasons = tuple(values) if values is not None else None
                        decision = evaluate_grid(
                            current["status"], current.get("params") or {}, levels, mid,
                            float(current["range_low"]), float(current["range_high"]),
                            float(current["capital_total"]), sigma, paused_since, now,
                            pause_reasons=pause_reasons,
                        )
                        target_plan = None
                        target_params = current.get("params") or {}
                        if (decision.action != "CLOSE_REPOSITORY" and current.get("strategy") == "smart"
                                and current["status"] in {"ACTIVE", "PAUSED"}
                                and (target_params.get("target_pct") is not None
                                     or target_params.get("target_usdt") is not None)):
                            loans = self.db.list_grid_loans(grid_id) if hasattr(self.db, "list_grid_loans") else []
                            saga_cells = [row for row in levels if float(row.get("capital_loan") or 0) > 0
                                          or str(row.get("state", "")).upper().startswith("LOAN_")]
                            blocking_loans = [loan for loan in loans if loan.get("status") in {"PENDING", "OPEN"}]
                            skip_reason = ("open_or_pending_grid_loan" if blocking_loans else
                                           "cell_in_loan_saga" if saga_cells else None)
                            if skip_reason:
                                recent = self.db.get_last_event(grid_id, "TARGET_SKIPPED")
                                if not recent or (_utc(now) - _utc(recent["ts"])).total_seconds() >= 6 * 3600 \
                                        or (recent.get("reason") != skip_reason):
                                    cash_at_skip = float(current["capital_total"]) + sum(
                                        float(row.get("pnl") or 0) - float(row.get("entry_price") or 0)
                                        * float(row.get("held_qty") or 0) * 1.001 for row in levels)
                                    equity_at_skip = cash_at_skip + sum(
                                        float(row.get("held_qty") or 0) * float(mid or 0) * .999 for row in levels)
                                    self._emit({"event_type": "TARGET_SKIPPED", "grid_id": grid_id,
                                                "reason": skip_reason, "price": mid,
                                                "details": {"target_basis": target_params.get("target_basis", "cash"),
                                                            "pending_or_open_loans": len(blocking_loans),
                                                            "saga_cells": [int(row["level_idx"]) for row in saga_cells],
                                                            "cash_total": cash_at_skip,
                                                            "equity_total_at_close": equity_at_skip}})
                            else:
                                book = self.exchange.get_book_ticker(current["symbol"])
                                bid = float(book["bid_price"])
                                filters, _, _ = self.engine._market_context(current["symbol"])
                                eval_cells = []
                                held_basis = 0.0
                                for row in levels:
                                    item = dict(row)
                                    qty = float(row.get("held_qty") or 0)
                                    basis_cost = float(row.get("entry_price") or 0) * qty
                                    entry_fee = basis_cost * .001
                                    try:
                                        buy_cid = (row.get("buy_client_order_id") or row.get("client_order_id"))
                                        if buy_cid:
                                            buy_order = self.exchange.get_order(current["symbol"],
                                                                                client_order_id=buy_cid)
                                            buy_trades = self.exchange.get_my_trades(
                                                current["symbol"], buy_order["order_id"])
                                            quote = sum(float(trade.get("quoteQty") or
                                                float(trade.get("qty", 0)) * float(trade.get("price", 0)))
                                                for trade in buy_trades)
                                            if quote > 0:
                                                basis_cost = quote
                                                entry_fee = float(self.engine._fee_value_usdt(buy_trades, current["symbol"]))
                                    except Exception:
                                        self.logger.debug("grid=%s level=%s target buy basis fallback",
                                                          grid_id, row.get("level_idx"), exc_info=True)
                                    item["entry_cost"] = basis_cost
                                    item["entry_fee_usdt"] = entry_fee
                                    held_basis += basis_cost + entry_fee
                                    eval_cells.append(item)
                                realized = sum(float(row.get("pnl") or 0) for row in levels)
                                dust_in_cells = sum((_d(row.get("held_qty")) for row in levels
                                                     if row.get("state") == "DONE"), Decimal(0))
                                sweepable_dust_qty = max(Decimal(0),
                                    _d(current.get("dust_qty")) - dust_in_cells)
                                cash_now = (float(current["capital_total"]) + realized - held_basis
                                            + float(target_params.get("dust_cash_proceeds", 0)))
                                equity_now = cash_now + sum(float(row.get("held_qty") or 0) * bid * .999
                                                            for row in levels)
                                target_plan = evaluate_target(
                                    target_params, current["capital_total"], cash_now, eval_cells,
                                    bid, filters, .1, equity_now=equity_now,
                                    dust={"dust_qty": sweepable_dust_qty})
                                dust_threshold = target_params.get("dust_sweep_threshold_pct")
                                if (dust_threshold is not None and not target_plan["reached"]
                                        and decision.action != "CLOSE_REPOSITORY"):
                                    dust_plan = plan_dust_sweep(sweepable_dust_qty, bid,
                                                                filters, .1)
                                    capital = float(current.get("capital_total") or 0)
                                    if (capital > 0 and dust_plan["sweepable"]
                                            and float(dust_plan["proceeds_net"]) / capital * 100
                                            >= float(dust_threshold)):
                                        self.engine.sweep_grid_dust(grid_id, bid, reason="threshold")
                                target_plan["bid_used"] = bid
                                target_plan["estimated_fees_usdt"] = sum(
                                    float(row.get("held_qty") or 0) * bid * .001
                                    for row in levels if float(row.get("held_qty") or 0) > 0)
                                sell_indices = {int(row["level_idx"]) for row in target_plan["sell_cells"]}
                                target_plan["repo_cells"] = [
                                    {"level_idx": int(row["level_idx"]), "reason": "not_sold_by_target_plan"}
                                    for row in levels if float(row.get("held_qty") or 0) > 0
                                    and int(row["level_idx"]) not in sell_indices]
                                target_plan["cash_total"] = target_plan["projected_cash"]
                                target_plan["equity_total_at_close"] = target_plan["projected_cash"] + sum(
                                    float(row.get("held_qty") or 0) * bid * .999
                                    for row in levels if float(row.get("held_qty") or 0) > 0
                                    and int(row["level_idx"]) not in sell_indices)
                                metrics["target"] = {key: target_plan.get(key) for key in
                                    ("reached", "basis", "cash_now", "projected_cash", "equity_now",
                                     "target_usdt", "target_threshold", "reason", "cash_total",
                                     "equity_total_at_close")}
                        # Adjustment has precedence over PAUSE, but CLOSE remains
                        # the highest priority. Legacy grids without the new
                        # adjust_enabled key stay on the 15B-1 policy path.
                        adjust = adjust_decision(
                            current, levels, mid, sigma, now,
                            (self.db.get_last_event(grid_id, "GRID_ADJUSTED") or {}).get("ts"),
                        )
                        if target_plan and target_plan["reached"]:
                            decision = PolicyDecision("TARGET", (), {**decision.metrics, "target": target_plan})
                        if max_days_result["expired"] and (max_days_plan_started
                                or decision.action not in {"CLOSE_REPOSITORY", "TARGET"}):
                            decision = PolicyDecision("MAX_DAYS", (), {**decision.metrics,
                                "max_days": max_days_result})
                        if decision.action not in {"CLOSE_REPOSITORY", "TARGET", "MAX_DAYS"} and adjust.action == "ADJUST":
                            preview = self.engine.preview_adjust(
                                grid_id, adjust.metrics["range_low"], adjust.metrics["range_high"],
                                adjust.metrics["n_levels"],
                            )
                            if not preview.get("ok"):
                                adjust = PolicyDecision("BLOCKED", (preview.get("reason") or "invalid_plan",),
                                                        {**adjust.metrics, "plan": preview.get("plan")})
                        if adjust.action == "ADJUST" and decision.action not in {"CLOSE_REPOSITORY", "TARGET", "MAX_DAYS"}:
                            decision = adjust
                        elif adjust.action == "BLOCKED":
                            decision = decision if decision.action == "CLOSE_REPOSITORY" else decision
                            blocked_reason = ",".join(adjust.reasons) or "plan_invalid"
                            recent = self.db.list_grid_events(grid_id=grid_id, event_type="ADJUST_BLOCKED", limit=100)
                            already_logged = any(
                                (event.get("details") or {}).get("blocked_reason") == blocked_reason
                                and (_utc(now) - _utc(event["ts"])).total_seconds() < 6 * 3600
                                for event in recent
                            )
                            if not already_logged:
                                self._emit({"event_type": "ADJUST_BLOCKED", "grid_id": grid_id,
                                    "reason": blocked_reason, "price": mid,
                                    "details": {**adjust.metrics, "blocked_reason": blocked_reason}})
                        metrics = decision.metrics
                        details = {**decision.metrics, "reasons": list(decision.reasons)}
                        if decision.action == "PAUSE":
                            try:
                                result = self.engine.pause_grid(grid_id, ",".join(decision.reasons), details)
                                if not result.get("ok"):
                                    raise RuntimeError("grid could not transition to PAUSED")
                            except Exception as exc:
                                failed += 1
                                self._emit({"event_type": "POLICY_ACTION_FAILED", "grid_id": grid_id,
                                            "reason": str(exc), "price": mid,
                                            "details": {"action": "PAUSE", **details}})
                                self.logger.warning("grid=%s pause action failed", grid_id, exc_info=True)
                        elif decision.action == "ADJUST":
                            try:
                                result = self.engine.adjust_grid(
                                    grid_id, decision.metrics["range_low"], decision.metrics["range_high"],
                                    decision.metrics["n_levels"], reason="policy_adjust",
                                    details={**decision.metrics, "source": "MONITOR"},
                                )
                                if not result.get("ok"):
                                    raise RuntimeError(result.get("reason") or "adjust rejected")
                                adjusted_this_pass = bool(result.get("changed"))
                            except Exception as exc:
                                failed += 1
                                self._emit({"event_type": "POLICY_ACTION_FAILED", "grid_id": grid_id,
                                            "reason": str(exc), "price": mid,
                                            "details": {"action": "ADJUST", **decision.metrics}})
                                self.logger.warning("grid=%s adjust action failed", grid_id, exc_info=True)
                        elif decision.action == "RESUME":
                            try:
                                result = self.engine.resume_grid(grid_id, "hysteresis_cleared", details)
                                if not result.get("ok"):
                                    raise RuntimeError("grid could not transition to ACTIVE")
                            except Exception as exc:
                                failed += 1
                                self._emit({"event_type": "POLICY_ACTION_FAILED", "grid_id": grid_id,
                                            "reason": str(exc), "price": mid,
                                            "details": {"action": "RESUME", **details}})
                                self.logger.warning("grid=%s resume action failed", grid_id, exc_info=True)
                        elif decision.action == "CLOSE_REPOSITORY":
                            self._emit({"event_type": "GRID_AUTO_CLOSE", "grid_id": grid_id,
                                        "reason": ",".join(decision.reasons), "price": mid,
                                        "details": details})
                            try:
                                self.engine.close_grid(grid_id, "repository")
                            except Exception as exc:
                                failed += 1
                                self._emit({"event_type": "POLICY_ACTION_FAILED", "grid_id": grid_id,
                                            "reason": str(exc), "price": mid,
                                            "details": {"action": "CLOSE_REPOSITORY", **details}})
                                self.logger.warning("grid=%s automatic close failed", grid_id, exc_info=True)
                        elif decision.action == "MAX_DAYS":
                            try:
                                levels = self.db.get_grid_levels(grid_id)
                                start_max_days_plan(current, levels, max_days_result, mid)
                                result = self.engine.close_grid(grid_id, "repository", close_reason="max_days")
                                emit_max_days_reached(grid_id, mid, result.get("status", "UNKNOWN"))
                            except Exception as exc:
                                failed += 1
                                self._emit({"event_type": "POLICY_ACTION_FAILED", "grid_id": grid_id,
                                            "reason": str(exc), "price": mid,
                                            "details": {"action": "MAX_DAYS", **max_days_result}})
                                self.logger.warning("grid=%s max-days close failed", grid_id, exc_info=True)
                        elif decision.action == "TARGET":
                            try:
                                self.engine.close_grid_target(grid_id, decision.metrics["target"], mid)
                            except Exception as exc:
                                failed += 1
                                self._emit({"event_type": "POLICY_ACTION_FAILED", "grid_id": grid_id,
                                            "reason": str(exc), "price": mid,
                                            "details": {"action": "TARGET", **decision.metrics["target"]}})
                                self.logger.warning("grid=%s target close failed", grid_id, exc_info=True)
                    current = self.db.get_grid(grid_id) or current
                    if (current.get("status") in {"ACTIVE", "PAUSED"} and mid is not None):
                        max_days_result = evaluate_max_days(current.get("params") or {},
                            current.get("created_at"), now)
                        max_days_plan_started = ((current.get("params") or {}).get(
                            "max_days_close_plan", {}).get("phase") == "STARTED")
                        if max_days_plan_started:
                            max_days_result = {**(current.get("params") or {}).get("max_days_close_plan", {}),
                                               "expired": True}
                        if max_days_result["expired"]:
                            levels = self.db.get_grid_levels(grid_id)
                            start_max_days_plan(current, levels, max_days_result, mid)
                            result = self.engine.close_grid(grid_id, "repository", close_reason="max_days")
                            emit_max_days_reached(grid_id, mid, result.get("status", "UNKNOWN"))
                    final_grid = self.db.get_grid(grid_id) or current
                    params = final_grid.get("params") or {}
                    if (policy_enabled and status_before == "ACTIVE" and not adjusted_this_pass
                            and final_grid.get("status") == "ACTIVE"
                            and final_grid.get("strategy", "simple") == "smart"
                            and params.get("loans_enabled") is True):
                        try:
                            self.engine.process_grid_loans(grid_id, now=now)
                        except Exception as exc:
                            failed += 1
                            self._emit({"event_type": "POLICY_ACTION_FAILED", "grid_id": grid_id,
                                        "reason": str(exc), "price": mid,
                                        "details": {"action": "LOANS"}})
                            self.logger.warning("grid=%s loan processing failed", grid_id, exc_info=True)
                    final_grid = self.db.get_grid(grid_id) or current
                    try:
                        self._snapshot_grid(run_id, final_grid, mid, metrics)
                    except Exception as exc:
                        failed += 1
                        self.logger.warning("grid=%s monitor snapshot failed", grid_id, exc_info=True)
                        self._emit({"event_type": "SNAPSHOT_FAILED", "grid_id": grid_id,
                                    "reason": str(exc), "price": mid, "details": {}})
                except Exception as exc:
                    failed += 1
                    self.logger.exception("grid=%s monitor processing failed", grid_id)
                    self._emit({"event_type": "SYNC_FAILED", "grid_id": grid_id,
                                "reason": str(exc), "price": mid, "details": {}})
                    snapshot_grid(grid, mid)

            for grid in all_grids:
                checked += 1
                if grid["status"] == "CLOSED":
                    if ((grid.get("params") or {}).get("max_days_close_plan", {}).get("phase") == "STARTED"
                            and self.db.get_last_event(int(grid["id"]), "MAX_DAYS_REACHED") is None):
                        emit_max_days_reached(int(grid["id"]), mids.get(grid["symbol"]), "CLOSED")
                    try:
                        self.engine.close_grid_target(int(grid["id"]),
                            (grid.get("params") or {}).get("target_close_plan") or {}, mids.get(grid["symbol"]))
                    except Exception as exc:
                        failed += 1
                        self._emit({"event_type": "POLICY_ACTION_FAILED", "grid_id": int(grid["id"]),
                                    "reason": str(exc), "price": mids.get(grid["symbol"]),
                                    "details": {"action": "TARGET_CLOSE_FINALIZE",
                                        "cash_total": ((grid.get("params") or {}).get("target_close_plan") or {}).get("cash_total"),
                                        "equity_total_at_close": ((grid.get("params") or {}).get("target_close_plan") or {}).get("equity_total_at_close")}})
                    snapshot_grid(self.db.get_grid(int(grid["id"])) or grid, mids.get(grid["symbol"]))
                    continue
                if grid["status"] == "CLOSING":
                    closing_params = (grid.get("params") or {})
                    target_plan = closing_params.get("target_close_plan")
                    if target_plan:
                        try:
                            self.engine.close_grid_target(int(grid["id"]), target_plan, mids.get(grid["symbol"]))
                        except Exception as exc:
                            failed += 1
                            self._emit({"event_type": "POLICY_ACTION_FAILED", "grid_id": int(grid["id"]),
                                        "reason": str(exc), "price": mids.get(grid["symbol"]),
                                        "details": {"action": "TARGET_CLOSE_RETRY",
                                            "cash_total": target_plan.get("cash_total"),
                                            "equity_total_at_close": target_plan.get("equity_total_at_close")}})
                        refreshed = self.db.get_grid(int(grid["id"])) or grid
                        snapshot_grid(refreshed, mids.get(grid["symbol"]))
                        continue
                    close_event = self.db.get_last_event(int(grid["id"]), "GRID_CLOSE_STARTED")
                    close_details = (close_event or {}).get("details") or {}
                    if close_event and close_event.get("source") == "MONITOR" and close_details.get("mode"):
                        try:
                            self.engine.close_grid(int(grid["id"]), close_details["mode"],
                                close_reason=close_details.get("close_reason", "grid_close"))
                        except Exception as exc:
                            failed += 1
                            self._emit({"event_type": "POLICY_ACTION_FAILED", "grid_id": int(grid["id"]),
                                        "reason": str(exc), "price": mids.get(grid["symbol"]),
                                        "details": {"action": "CLOSE_RETRY", "mode": close_details["mode"]}})
                    refreshed = self.db.get_grid(int(grid["id"])) or grid
                    if (refreshed.get("status") == "CLOSED"
                            and (refreshed.get("params") or {}).get("max_days_close_plan", {}).get("phase") == "STARTED"):
                        emit_max_days_reached(int(grid["id"]), mids.get(grid["symbol"]), "CLOSED")
                    if refreshed["status"] == "CLOSING":
                        self._close_pending(run_id, refreshed, now)
                        snapshot_grid(refreshed, mids.get(grid["symbol"]))
                    else:
                        snapshot_grid(refreshed, mids.get(grid["symbol"]))
                    continue
                process_grid(grid)

            status = "OK" if failed == 0 else "PARTIAL"
        except Exception as exc:
            unexpected = exc
            status = "FAILED"
            self.logger.exception("grid monitor pass failed")
        finally:
            duration_ms = int((time.monotonic() - started_clock) * 1000)
            self.db.finish_monitor_run(
                run_id, status=status, grids_checked=checked, grids_failed=failed,
                events_written=self._event_count, duration_ms=duration_ms,
                note=None if unexpected is None else str(unexpected),
            )
            self.engine.event_sink = old_sink
            self._run_id = None
        return {
            "run_id": run_id, "status": status, "grids_checked": checked,
            "grids_failed": failed, "events_written": self._event_count,
            "duration_ms": duration_ms,
        }
