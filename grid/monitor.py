"""Periodic audit and synchronization loop for active spot grids."""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Callable

from apscheduler.schedulers.background import BackgroundScheduler
from grid.policy import adjust_decision, evaluate_grid, stoploss_candidates, PolicyDecision
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

    def _emit(self, event: dict) -> None:
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
            "fee_paid": float(sum((_d(level.get("fee_paid")) for level in levels), Decimal(0))),
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
            all_grids = normal_grids + repositories + closings
            mids = self._market_mids(all_grids)
            policy_enabled = bool(getattr(self.settings, "grid_policy_enabled", True))
            vol_cache: dict[str, Any] = {}

            def unavailable_event(grid: dict, reason: str) -> None:
                previous_event = self.db.get_last_event(int(grid["id"]), "VOL_UNAVAILABLE")
                if previous_event and (_utc(now) - _utc(previous_event["ts"])).total_seconds() < 6 * 3600:
                    return
                self._emit({"event_type": "VOL_UNAVAILABLE", "grid_id": int(grid["id"]),
                            "reason": reason, "price": mids.get(grid["symbol"]), "details": {"reason": reason}})

            def process_grid(grid: dict) -> None:
                nonlocal failed
                grid_id, status_before = int(grid["id"]), grid["status"]
                mid = mids.get(grid["symbol"])
                metrics: dict[str, Any] = {}
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

                    if policy_enabled and mid is not None and current.get("strategy", "simple") == "smart" \
                            and current["status"] in {"ACTIVE", "PAUSED"}:
                        levels = self.db.get_grid_levels(grid_id)
                        sigma = None if view is None else float(view.sigma_24h)
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
                        # Adjustment has precedence over PAUSE, but CLOSE remains
                        # the highest priority. Legacy grids without the new
                        # adjust_enabled key stay on the 15B-1 policy path.
                        adjust = adjust_decision(
                            current, levels, mid, sigma, now,
                            (self.db.get_last_event(grid_id, "GRID_ADJUSTED") or {}).get("ts"),
                        )
                        if decision.action != "CLOSE_REPOSITORY" and adjust.action == "ADJUST":
                            preview = self.engine.preview_adjust(
                                grid_id, adjust.metrics["range_low"], adjust.metrics["range_high"],
                                adjust.metrics["n_levels"],
                            )
                            if not preview.get("ok"):
                                adjust = PolicyDecision("BLOCKED", (preview.get("reason") or "invalid_plan",),
                                                        {**adjust.metrics, "plan": preview.get("plan")})
                        if adjust.action == "ADJUST" and decision.action != "CLOSE_REPOSITORY":
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
                if grid["status"] == "CLOSING":
                    close_event = self.db.get_last_event(int(grid["id"]), "GRID_CLOSE_STARTED")
                    close_details = (close_event or {}).get("details") or {}
                    if close_event and close_event.get("source") == "MONITOR" and close_details.get("mode"):
                        try:
                            self.engine.close_grid(int(grid["id"]), close_details["mode"])
                        except Exception as exc:
                            failed += 1
                            self._emit({"event_type": "POLICY_ACTION_FAILED", "grid_id": int(grid["id"]),
                                        "reason": str(exc), "price": mids.get(grid["symbol"]),
                                        "details": {"action": "CLOSE_RETRY", "mode": close_details["mode"]}})
                    refreshed = self.db.get_grid(int(grid["id"])) or grid
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
