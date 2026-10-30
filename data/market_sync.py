"""Actualización horaria de velas públicas en el almacén separado."""
from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.background import BackgroundScheduler

logger = logging.getLogger(__name__)
STEP_SECONDS = 300
RUN_BUDGET_SECONDS = 60


class MarketSyncJob:
    def __init__(self, store, client, scheduler=None, *, clock=None, monotonic=None):
        self.store = store
        self.client = client
        self.scheduler = scheduler or BackgroundScheduler(timezone="UTC")
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.monotonic = monotonic or time.monotonic

    def run_once(self):
        started = self.monotonic()
        deadline = started + RUN_BUDGET_SECONDS
        now = self.clock().astimezone(timezone.utc)
        last_closed = int(now.timestamp()) // STEP_SECONDS * STEP_SECONDS - STEP_SECONDS
        result = {"symbols": 0, "candles": 0, "errors": 0}

        def work():
            try:
                os.nice(5)
            except (AttributeError, OSError):
                pass
            for item in self.store.active_symbols():
                if self.monotonic() >= deadline:
                    break
                symbol, role = item["symbol"], item["role"]
                result["symbols"] += 1
                position = self.store.sync_position(symbol)
                if position is None:
                    continue
                begin = position + STEP_SECONDS
                if begin > last_closed:
                    continue
                try:
                    frame = self.client.get_historical_klines(symbol, "5m", 1,
                        start_time=datetime.fromtimestamp(begin, timezone.utc),
                        deadline_monotonic=deadline)
                    rows = []
                    for row in frame.to_dict("records"):
                        stamp = row["timestamp"]
                        stamp = int(stamp.timestamp()) if hasattr(stamp, "timestamp") else int(stamp)
                        if stamp <= last_closed:
                            rows.append({"open_ts": stamp, "open": row["open"], "high": row["high"],
                                "low": row["low"], "close": row["close"], "volume": row["volume"],
                                "quote_volume": row["quote_volume"], "trades": row["trades"]})
                    saved = self.store.upsert(symbol, "5m", rows, "binance_api", role=role)
                    result["candles"] += saved["accepted"]
                    if rows:
                        self.store.update_sync_state(symbol, "5m", max(r["open_ts"] for r in rows), "binance_api")
                except Exception as exc:
                    result["errors"] += 1
                    self.store.set_sync_error(symbol, "5m", f"{type(exc).__name__}: {exc}")
                    logger.warning("MARKET_SYNC error symbol=%s error=%s", symbol, exc)

        thread = threading.Thread(target=work, name="market-sync-low-priority", daemon=True)
        thread.start()
        thread.join(max(0, deadline - self.monotonic()))
        duration = max(0.0, self.monotonic() - started)
        logger.info("MARKET_SYNC monedas=%d velas_nuevas=%d errores=%d duración_s=%.2f",
            result["symbols"], result["candles"], result["errors"], duration)
        return result

    def start(self):
        self.scheduler.add_job(self.run_once, "cron", minute=2, timezone="UTC",
            id="market_sync_hourly", max_instances=1, coalesce=True, replace_existing=True)
        self.scheduler.start()

    def stop(self):
        if getattr(self.scheduler, "running", False):
            self.scheduler.shutdown(wait=False)
