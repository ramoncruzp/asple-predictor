"""Ejecuci\u00f3n peri\u00f3dica de medidores para alertas sin abrir las pantallas."""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone

from apscheduler.schedulers.background import BackgroundScheduler

logger = logging.getLogger(__name__)


class IntelligenceReadinessJob:
    """Eval\u00faa evidencia una vez por hora y la entrega al AlertDispatcher."""

    def __init__(self, evaluator, dispatcher, *, scheduler=None, clock=None):
        self.evaluator = evaluator
        self.dispatcher = dispatcher
        self.scheduler = scheduler or BackgroundScheduler(timezone="UTC")
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self._grid_intel_guard = threading.Lock()
        self._hour_cache = None

    def start(self):
        self.scheduler.add_job(self.run_once, "cron", minute=37, second=0, timezone="UTC",
            id="grid_intelligence_readiness", replace_existing=True,
            max_instances=1, coalesce=True, misfire_grace_time=1800)
        if not self.scheduler.running:
            self.scheduler.start()

    def stop(self):
        if getattr(self.scheduler, "running", False):
            self.scheduler.shutdown(wait=True)

    def run_once(self):
        if not self._grid_intel_guard.acquire(blocking=False):
            return {"status": "skipped", "reason": "evaluaci\u00f3n anterior sigue activa"}
        started = time.perf_counter()
        now = self.clock()
        hour_key = now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H")
        try:
            cached = self._hour_cache
            if cached and cached[0] == hour_key:
                snapshot = cached[1]
                cache_status = "hit"
            else:
                snapshot = self.evaluator()
                self._hour_cache = (hour_key, snapshot)
                cache_status = "miss"
            self.dispatcher.note_intelligence_readiness(snapshot or {})
            return {"status": "completed", "cache": cache_status,
                "duration_s": round(time.perf_counter() - started, 3),
                "profiles": len(snapshot or {})}
        except Exception:
            logger.exception("Fall\u00f3 la evaluaci\u00f3n horaria de evidencia de Inteligencia")
            return {"status": "failed", "duration_s": round(time.perf_counter() - started, 3)}
        finally:
            self._grid_intel_guard.release()
