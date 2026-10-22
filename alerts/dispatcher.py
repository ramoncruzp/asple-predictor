"""Polls grid_events (and a few other read-only tables) and sends Telegram
alerts. Runs on its own APScheduler job, independent of the grid monitor --
if the monitor hangs, this keeps running and can still report that fact.

Never changes who emits grid_events (grid/engine.py and grid/monitor.py are
untouched); this module only reads.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Callable
from zoneinfo import ZoneInfo

import requests
from apscheduler.schedulers.background import BackgroundScheduler

from alerts.telegram_sender import TelegramSender
from alerts import formatters as fmt

logger = logging.getLogger(__name__)

LOCAL_TZ = ZoneInfo("America/Santo_Domingo")
BINANCE_PING_URL = "https://testnet.binance.vision/api/v3/ping"

# D1: operational events that warrant a Telegram alert (see REPORTE_FASE10.md
# section "event_type emitidos" for why each one is / is not on this list).
OPERATIONAL_EVENT_TYPES = {
    "RUN_GAP", "SYNC_FAILED", "GRID_AUTO_CLOSE", "TESTNET_RESET_DETECTED",
    "MAX_DAYS_REACHED", "BOOK_ONE_SIDED", "CLOSE_PENDING", "POLICY_ACTION_FAILED",
    "GRID_PAUSED", "GRID_RESUMED", "TARGET_REACHED", "CELL_STOPLOSS",
    "DUST_RECONCILIATION",
}
FILL_EVENT_TYPES = {"BUY_FILLED", "SELL_FILLED"}
LIQUIDATION_EVENT_TYPES = {"CELL_LIQUIDATED"}


def _is_manual_api(event: dict) -> bool:
    details = event.get("details") or {}
    return details.get("who") == "api" or details.get("source") == "API"


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _local_timestamp(value: datetime | str) -> str:
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return _as_utc(value).astimezone(LOCAL_TZ).strftime("%Y-%m-%d %H:%M (hora local)")


def _day_start_utc(now: datetime) -> datetime:
    return _as_utc(now).replace(hour=0, minute=0, second=0, microsecond=0)


def _local_day_start_utc(now: datetime) -> datetime:
    """Midnight in America/Santo_Domingo, converted back to UTC -- for
    anything the person reads as "today" (e.g. the digest's pnl_today),
    UTC midnight is 4 hours early for this timezone and would attribute the
    evening's fills to the wrong day."""
    local_midnight = _as_utc(now).astimezone(LOCAL_TZ).replace(
        hour=0, minute=0, second=0, microsecond=0)
    return local_midnight.astimezone(timezone.utc)


def default_probe(timeout: float = 8.0) -> bool:
    # Deliberately the opposite of TelegramSender: this probe DOES go through
    # the reverse tunnel (requests.get() honors HTTPS_PROXY by default), since
    # the point is to detect the tunnel being down.
    try:
        response = requests.get(BINANCE_PING_URL, timeout=timeout)
        return response.status_code == 200
    except Exception:
        return False


class AlertDispatcher:
    def __init__(
        self, db: Any, settings_factory: Callable[[], Any], *,
        scheduler: Any = None, clock: Callable[[], datetime] | None = None,
        sender_factory: Callable[[str, str], Any] | None = None,
        probe: Callable[[], bool] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ):
        self.db = db
        self.settings_factory = settings_factory
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.sender_factory = sender_factory or (lambda token, chat_id: TelegramSender(token, chat_id))
        self.probe = probe or default_probe
        self.sleep = sleep
        self.monotonic = monotonic
        self._last_sent_monotonic: float | None = None
        self._startup_announced_this_process = False
        self.scheduler = scheduler or BackgroundScheduler(timezone="UTC")

    # -- lifecycle -------------------------------------------------------

    def start(self, poll_seconds: int) -> None:
        self._startup_announced_this_process = False
        self.scheduler.add_job(
            self.run_once, "interval", seconds=max(1, int(poll_seconds)),
            id="alert_dispatcher", replace_existing=True, max_instances=1, coalesce=True,
        )
        if not self.scheduler.running:
            self.scheduler.start()

    def stop(self) -> None:
        if self.scheduler.running:
            self.scheduler.shutdown(wait=True)

    # -- delivery ----------------------------------------------------------

    def _rate_limit(self) -> None:
        if self._last_sent_monotonic is None:
            return
        elapsed = self.monotonic() - self._last_sent_monotonic
        if elapsed < 1.0:
            self.sleep(1.0 - elapsed)

    def _send(self, sender, *, alert_key: str, kind: str, text: str,
               event_id: int | None = None, now: datetime | None = None) -> bool:
        self._rate_limit()
        result = sender.send(text)
        self._last_sent_monotonic = self.monotonic()
        self.db.add_alert_log(
            alert_key=alert_key, kind=kind, sent=bool(result.get("ok")), event_id=event_id,
            error=None if result.get("ok") else result.get("error"), ts=now or self.clock(),
        )
        return bool(result.get("ok"))

    def _cooldown_ok(self, alert_key: str, cooldown_min: int, now: datetime) -> bool:
        if cooldown_min <= 0:
            return True
        last = self.db.get_last_alert_sent_at(alert_key)
        if last is None:
            return True
        return (_as_utc(now) - _as_utc(last)).total_seconds() >= cooldown_min * 60

    def _daily_cap_remaining(self, kind: str, cap: int, now: datetime) -> int:
        sent_today = self.db.count_alert_log_since(kind, _day_start_utc(now))
        return cap - sent_today

    # -- main entry point ----------------------------------------------------

    def run_once(self) -> dict:
        cfg = self.settings_factory()
        if not bool(getattr(cfg, "alerts_enabled", False)):
            return {"status": "disabled"}
        token = str(getattr(cfg, "telegram_bot_token", "") or "").strip()
        chat_id = str(getattr(cfg, "telegram_chat_id", "") or "").strip()
        if not token or not chat_id:
            return {"status": "unconfigured"}
        sender = self.sender_factory(token, chat_id)
        now = self.clock()
        counters = {"operational": 0, "fills": 0}
        self._ensure_watermark()
        self._process_startup(sender, cfg, now)
        self._process_events(sender, cfg, now, counters)
        counters["metas"] = self._process_meta_events(sender, cfg, now)
        self._process_probe(sender, now)
        self._process_stale(sender, cfg, now)
        self._process_digest(sender, cfg, now)
        return {"status": "ok", **counters}

    def _process_meta_events(self, sender, cfg, now) -> int:
        """Entrega los eventos de Meta usando el mismo límite y registro Telegram."""
        list_events = getattr(self.db, "list_meta_ledger_since", None)
        if not callable(list_events):
            return 0
        last_id = int(self.db.get_alert_state("meta_last_event_id") or 0)
        events = list_events(last_id, limit=5000)
        max_seen = last_id
        sent = 0
        titles = {
            "META_CREATED": "Meta creada", "META_STARTED": "Meta iniciada",
            "META_PIECE_LINKED": "Pieza ligada", "META_LOSS_WARNING": "Aviso: pérdida cerca del límite",
            "META_UNKNOWN": "Valor DESCONOCIDO: libro sin compradores o datos no conciliables",
            "META_CORTADA_POR_PERDIDA": "Meta cortada por pérdida máxima",
            "META_VENCIDA": "Meta vencida", "META_CUMPLIDA": "Meta cumplida",
            "META_CANCELLED": "Meta cancelada", "META_CLOSE_PENDING": "Cierre Meta pendiente",
            "META_OPERATOR_OPEN": "Pieza abierta por el operador",
            "META_OPERATOR_WITHDRAW": "Pieza retirada por el operador",
            "META_OPERATOR_CLOSE": "Pieza cerrada por el operador",
        }
        for event in events:
            event_id = int(event["id"])
            max_seen = max(max_seen, event_id)
            event_type = str(event.get("event_type") or "")
            if event_type not in titles:
                continue
            meta_id = int(event["meta_id"])
            alert_key = f"{event_type}:{meta_id}"
            cooldown = 30 if event_type == "META_UNKNOWN" else int(getattr(cfg, "alerts_cooldown_min", 60))
            if not self._cooldown_ok(alert_key, cooldown, now):
                continue
            if self._daily_cap_remaining("operativa", int(getattr(cfg, "alerts_daily_cap", 30)), now) <= 0:
                continue
            details = event.get("details") or {}
            text = f"{titles[event_type]} · Meta #{meta_id}"
            reason = event.get("reason") or details.get("error")
            if reason:
                text += f"\nMotivo: {reason}"
            for key in ("result_pct", "threshold_pct", "target_usdt", "mode", "surplus_wait"):
                if key in details:
                    text += f"\n{key}: {details[key]}"
            sent += int(self._send(sender, alert_key=alert_key, kind="operativa", text=text,
                                   event_id=event_id, now=now))
        self.db.set_alert_state("meta_last_event_id", max_seen)
        return sent

    def _ensure_watermark(self) -> None:
        if self.db.get_alert_state("last_event_id") is None:
            self.db.set_alert_state("last_event_id", self.db.get_max_grid_event_id())

    # -- D1 + D2: grid_events ------------------------------------------------

    def _grid_label(self, grid_id: int | None, fallback_symbol: str | None = None) -> str | None:
        if grid_id is None:
            return fallback_symbol
        grid = self.db.get_grid(int(grid_id))
        if grid is None:
            return fallback_symbol
        return fmt.grid_label(grid["symbol"], grid_id)

    def _process_events(self, sender, cfg, now: datetime, counters: dict) -> None:
        last_id = int(self.db.get_alert_state("last_event_id") or 0)
        events = self.db.list_grid_events_since(last_id, limit=5000)
        if not events:
            return
        fills_by_grid: dict[int, list[tuple[dict, str]]] = {}
        max_seen_id = last_id
        for event in events:
            max_seen_id = max(max_seen_id, int(event["id"]))
            event_type = str(event.get("event_type") or "")
            if _is_manual_api(event):
                continue
            if event_type in FILL_EVENT_TYPES or event_type in LIQUIDATION_EVENT_TYPES:
                if not bool(getattr(cfg, "alerts_fills_enabled", True)):
                    continue
                line = self._format_fill_line(event, events)
                if line is None:
                    continue
                grid_id = event.get("grid_id")
                if grid_id is not None:
                    fills_by_grid.setdefault(int(grid_id), []).append((event, line))
            elif event_type in OPERATIONAL_EVENT_TYPES:
                self._maybe_send_operational(sender, cfg, event, now)
        if fills_by_grid:
            self._send_fill_groups(sender, cfg, fills_by_grid, now, counters)
        self.db.set_alert_state("last_event_id", max_seen_id)

    def _maybe_send_operational(self, sender, cfg, event: dict, now: datetime) -> None:
        event_type = str(event.get("event_type") or "")
        if event_type == "DUST_RECONCILIATION" and event.get("reason") in {"baseline", "no_material_drift"}:
            return
        grid_id = event.get("grid_id")
        if event_type == "DUST_RECONCILIATION":
            asset = (event.get("details") or {}).get("asset")
            alert_key = f"{event_type}:{asset or 'none'}"
        else:
            alert_key = f"{event_type}:{grid_id if grid_id is not None else 'none'}"
        cooldown = int(getattr(cfg, "alerts_cooldown_min", 60))
        if not self._cooldown_ok(alert_key, cooldown, now):
            return
        cap = int(getattr(cfg, "alerts_daily_cap", 30))
        remaining = self._daily_cap_remaining("operativa", cap, now)
        if remaining <= 0:
            cap_key = "daily_cap_operativas"
            if self._cooldown_ok(cap_key, 24 * 60, now):
                self._send(sender, alert_key=cap_key, kind="operativa",
                            text=fmt.format_daily_cap_reached(), now=now)
            return
        label = self._grid_label(grid_id, (event.get("details") or {}).get("symbol"))
        text = fmt.format_operational(event, label)
        self._send(sender, alert_key=alert_key, kind="operativa", text=text,
                    event_id=int(event["id"]), now=now)

    def _format_fill_line(self, event: dict, batch: list[dict]) -> str | None:
        event_type = str(event.get("event_type") or "")
        details = event.get("details") or {}
        grid_id = event.get("grid_id")
        if event_type == "CELL_LIQUIDATED":
            return fmt.format_sell_fill_line(
                details.get("qty"), details.get("price"), details.get("cycle_pnl"),
                None, details.get("cycles_completed"), is_close=True)
        if event_type == "SELL_FILLED":
            placed = self._find_sell_placed(grid_id, event.get("level_idx"), event.get("client_order_id"))
            sell_price = None if placed is None else (placed.get("details") or {}).get("price")
            is_close = self._is_repository_grid(grid_id)
            return fmt.format_sell_fill_line(
                details.get("executed_qty"), sell_price, details.get("cycle_pnl"),
                None, details.get("cycles_completed"), is_close=is_close)
        if event_type == "BUY_FILLED":
            buy_price = self._resolve_buy_price(event)
            if buy_price is None:
                return None
            next_sell = self._find_next_sell_placed(grid_id, event.get("level_idx"), int(event["id"]), batch)
            sell_price = None if next_sell is None else (next_sell.get("details") or {}).get("price")
            return fmt.format_buy_fill_line(details.get("executed_qty"), buy_price, sell_price)
        return None

    def _resolve_buy_price(self, event: dict) -> Any | None:
        """The exact limit price the filled buy was placed at.

        BUY_PLACED is only persisted while the engine's event_sink is wired
        (i.e. during a monitor pass -- see grid/engine.py:540-543 and
        REPORTE_FASE10.md discrepancy "genesis BUY_PLACED not persisted").
        A grid's very first buy per level is placed at creation time, outside
        any monitor pass, so that BUY_PLACED is silently dropped. Fall back to
        grid_levels.price, the immutable grid-line price that same order was
        placed at -- not an invented number, just a different honest source.
        """
        grid_id, level_idx = event.get("grid_id"), event.get("level_idx")
        placed = self._find_buy_placed(grid_id, level_idx, event.get("client_order_id"))
        if placed is not None:
            return (placed.get("details") or {}).get("price")
        if grid_id is None or level_idx is None:
            return None
        level = next((row for row in self.db.get_grid_levels(int(grid_id))
                      if int(row["level_idx"]) == int(level_idx)), None)
        return None if level is None else level.get("price")

    def _is_repository_grid(self, grid_id: int | None) -> bool:
        if grid_id is None:
            return False
        grid = self.db.get_grid(int(grid_id))
        return bool(grid) and grid.get("strategy") == "repository"

    def _find_buy_placed(self, grid_id, level_idx, client_order_id) -> dict | None:
        if grid_id is None or client_order_id is None:
            return None
        for event in self.db.list_grid_events(grid_id=int(grid_id), event_type="BUY_PLACED", limit=5000):
            if event.get("client_order_id") == client_order_id:
                return event
        return None

    def _find_sell_placed(self, grid_id, level_idx, client_order_id) -> dict | None:
        if grid_id is None or client_order_id is None:
            return None
        for event in self.db.list_grid_events(grid_id=int(grid_id), event_type="SELL_PLACED", limit=5000):
            if event.get("client_order_id") == client_order_id:
                return event
        return None

    def _find_next_sell_placed(self, grid_id, level_idx, after_id: int, batch: list[dict]) -> dict | None:
        if grid_id is None:
            return None
        candidates = [event for event in batch
                      if event.get("event_type") == "SELL_PLACED"
                      and event.get("grid_id") == grid_id
                      and event.get("level_idx") == level_idx
                      and int(event["id"]) > after_id]
        if candidates:
            return min(candidates, key=lambda event: int(event["id"]))
        # Not in this poll's batch (e.g. a previous poll already advanced past
        # it); fall back to the DB directly.
        for event in self.db.list_grid_events(grid_id=int(grid_id), event_type="SELL_PLACED", limit=5000):
            if event.get("level_idx") == level_idx and int(event["id"]) > after_id:
                return event
        return None

    def _send_fill_groups(self, sender, cfg, fills_by_grid: dict, now: datetime, counters: dict) -> None:
        cap = int(getattr(cfg, "alerts_fills_daily_cap", 200))
        for grid_id, items in fills_by_grid.items():
            remaining = self._daily_cap_remaining("fill", cap, now)
            if remaining <= 0:
                cap_key = "daily_cap_fills"
                if self._cooldown_ok(cap_key, 24 * 60, now):
                    self._send(sender, alert_key=cap_key, kind="fill",
                                text=fmt.format_fills_cap_reached(), now=now)
                return
            grid = self.db.get_grid(grid_id)
            symbol = grid["symbol"] if grid else "?"
            last_event_id = max(int(event["id"]) for event, _ in items)
            if len(items) == 1:
                text = self._format_single_fill_full(items[0][0], symbol, grid_id)
            else:
                text = fmt.format_fills_group(symbol, grid_id, [line for _, line in items])
            self._send(sender, alert_key=f"fill:{grid_id}:{last_event_id}", kind="fill",
                        text=text, event_id=last_event_id, now=now)
            counters["fills"] += len(items)

    def _format_single_fill_full(self, event: dict, symbol: str, grid_id: int) -> str:
        event_type = str(event.get("event_type") or "")
        details = event.get("details") or {}
        if event_type == "CELL_LIQUIDATED":
            return fmt.format_sell_fill(symbol, grid_id, details.get("qty"), details.get("price"),
                                        details.get("cycle_pnl"), None, details.get("cycles_completed"),
                                        is_close=True)
        if event_type == "SELL_FILLED":
            placed = self._find_sell_placed(grid_id, event.get("level_idx"), event.get("client_order_id"))
            sell_price = None if placed is None else (placed.get("details") or {}).get("price")
            is_close = self._is_repository_grid(grid_id)
            return fmt.format_sell_fill(symbol, grid_id, details.get("executed_qty"), sell_price,
                                        details.get("cycle_pnl"), None, details.get("cycles_completed"),
                                        is_close=is_close)
        buy_price = self._resolve_buy_price(event)
        next_sell = self._find_next_sell_placed(grid_id, event.get("level_idx"), int(event["id"]), [])
        sell_price = None if next_sell is None else (next_sell.get("details") or {}).get("price")
        return fmt.format_buy_fill(symbol, grid_id, details.get("executed_qty"), buy_price, sell_price)

    # -- D3: health ------------------------------------------------------

    def _process_probe(self, sender, now: datetime) -> None:
        ok = self.probe()
        fails = int(self.db.get_alert_state("probe_fail_count") or 0)
        was_down = bool(self.db.get_alert_state("probe_down") or False)
        cfg = self.settings_factory()
        threshold = int(getattr(cfg, "alerts_probe_fails", 3))
        if ok:
            self.db.set_alert_state("probe_fail_count", 0)
            self.db.set_alert_state("probe_down", False)
            if was_down:
                self._send(sender, alert_key="probe_recovered", kind="operativa",
                            text=fmt.format_binance_recovered(), now=now)
            return
        fails += 1
        self.db.set_alert_state("probe_fail_count", fails)
        if fails >= threshold and not was_down:
            self.db.set_alert_state("probe_down", True)
            self._send(sender, alert_key="probe_down", kind="operativa",
                        text=fmt.format_binance_unreachable(), now=now)

    def _process_stale(self, sender, cfg, now: datetime) -> None:
        if bool(getattr(cfg, "grid_monitor_enabled", True)):
            last_run = self.db.get_last_monitor_run() if hasattr(self.db, "get_last_monitor_run") else None
            if last_run and last_run.get("started_at"):
                age_min = (_as_utc(now) - _as_utc(last_run["started_at"])).total_seconds() / 60
                threshold = int(getattr(cfg, "alerts_stale_monitor_min", 30))
                if age_min > threshold:
                    self._send_cooldown(sender, "monitor_stale", int(getattr(cfg, "alerts_cooldown_min", 60)),
                                        now, kind="operativa", text=fmt.format_monitor_stale(age_min))
        loop_threshold = int(getattr(cfg, "alerts_stale_loop_min", 90))
        last_prediction = self.db.get_last_prediction_time() if hasattr(self.db, "get_last_prediction_time") else None
        if last_prediction:
            age_min = (_as_utc(now) - _as_utc(last_prediction)).total_seconds() / 60
            if age_min > loop_threshold:
                self._send_cooldown(sender, "prediction_stale", int(getattr(cfg, "alerts_cooldown_min", 60)),
                                    now, kind="operativa", text=fmt.format_loop_stale("Predicción XRP", age_min))
        last_vol = self.db.get_last_vol_forecast_time() if hasattr(self.db, "get_last_vol_forecast_time") else None
        if last_vol:
            age_min = (_as_utc(now) - _as_utc(last_vol)).total_seconds() / 60
            if age_min > loop_threshold:
                self._send_cooldown(sender, "vol_loop_stale", int(getattr(cfg, "alerts_cooldown_min", 60)),
                                    now, kind="operativa", text=fmt.format_loop_stale("Ciclo de volatilidad", age_min))

    def _send_cooldown(self, sender, alert_key: str, cooldown_min: int, now: datetime, *, kind: str, text: str) -> None:
        if self._cooldown_ok(alert_key, cooldown_min, now):
            self._send(sender, alert_key=alert_key, kind=kind, text=text, now=now)

    def _process_startup(self, sender, cfg, now: datetime) -> None:
        # In-memory, per-process-start gate: never re-fires within the same
        # running instance. The DB-backed 10-minute cooldown below is a
        # separate, independent guard against a tight restart loop (e.g.
        # systemd retrying every 30s while the tunnel is down) producing a
        # flood of messages across several process starts.
        if self._startup_announced_this_process:
            return
        self._startup_announced_this_process = True
        self._send_cooldown(sender, "startup", 10, now, kind="operativa", text=fmt.format_startup())

    # -- D4: daily digest --------------------------------------------------

    def _process_digest(self, sender, cfg, now: datetime) -> None:
        if not bool(getattr(cfg, "alerts_digest_enabled", True)):
            return
        local_now = _as_utc(now).astimezone(LOCAL_TZ)
        hour = int(getattr(cfg, "alerts_digest_hour_local", 8))
        if local_now.hour < hour:
            return
        last_date = self.db.get_alert_state("digest_last_date")
        today = local_now.date().isoformat()
        if last_date == today:
            return
        text = self._build_digest(now)
        sent = self._send(sender, alert_key="digest", kind="operativa", text=text, now=now)
        if sent:
            self.db.set_alert_state("digest_last_date", today)

    def _build_digest(self, now: datetime) -> str:
        grids = []
        for status in ("ACTIVE", "PAUSED", "CLOSING", "HOLDING"):
            grids.extend(self.db.list_grids_by_status({status}))
        day_start = _local_day_start_utc(now)
        rows = []
        for grid in grids:
            levels = self.db.get_grid_levels(int(grid["id"]))
            cycles = sum(int(row.get("cycles_completed") or 0) for row in levels)
            pnl_total = sum((Decimal(str(row.get("pnl") or 0)) for row in levels), Decimal("0"))
            sell_events = self.db.list_grid_events(grid_id=int(grid["id"]), event_type="SELL_FILLED", limit=5000)
            pnl_today = sum((Decimal(str((event.get("details") or {}).get("cycle_pnl") or 0))
                             for event in sell_events if _as_utc(event["ts"]) >= day_start), Decimal("0"))
            open_orders = sum(1 for row in levels if row.get("state") in {"BUY_OPEN", "SELL_OPEN"})
            rows.append({"label": fmt.grid_label(grid["symbol"], grid["id"]), "status": grid["status"],
                         "cycles": cycles, "pnl_today": pnl_today, "pnl_total": pnl_total,
                         "open_orders": open_orders})
        last_run = self.db.get_last_monitor_run() if hasattr(self.db, "get_last_monitor_run") else None
        last_prediction = self.db.get_last_prediction_time() if hasattr(self.db, "get_last_prediction_time") else None
        last_vol = self.db.get_last_vol_forecast_time() if hasattr(self.db, "get_last_vol_forecast_time") else None
        probe_down = self.db.get_alert_state("probe_down")
        return fmt.format_digest(
            rows,
            monitor_last_run=None if not last_run or last_run.get("started_at") is None
                else _local_timestamp(last_run["started_at"]),
            last_prediction=None if last_prediction is None else _local_timestamp(last_prediction),
            last_vol=None if last_vol is None else _local_timestamp(last_vol),
            probe_ok=None if probe_down is None else not probe_down,
        )
