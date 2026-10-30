"""Job semanal de baja prioridad para el marcador de pronósticos."""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy import select

from research.scorecard.runner import run_scorecard
from scripts.marcador_pronosticos import _write_report


logger = logging.getLogger(__name__)


class ScorecardJob:
    def __init__(self, db, market_db_path, *, registry_db_path=None, output_dir="reports/marcador",
            scheduler=None, calculate=None, write_report=None, clock=None):
        self.db = db
        self.market_db_path = Path(market_db_path)
        self.registry_db_path = registry_db_path
        self.output_dir = output_dir
        self.scheduler = scheduler or BackgroundScheduler(timezone="UTC")
        self.calculate = calculate or run_scorecard
        self.write_report = write_report or _write_report
        self.clock = clock or time.perf_counter

    def enabled(self):
        with self.db.engine.connect() as conn:
            raw = conn.execute(select(self.db.meta_profile_settings.c.settings).where(
                self.db.meta_profile_settings.c.profile == "GLOBAL")).scalar_one_or_none()
        return bool(json.loads(raw or "{}").get("scorecard_weekly_enabled", False))

    def run_if_enabled(self):
        started = self.clock()
        if not self.enabled():
            elapsed = self.clock() - started
            logger.info("SCORECARD estado=apagado duración_s=%.3f", elapsed)
            return {"status": "apagado", "duration_s": elapsed}
        try:
            if os.name == "posix":
                try:
                    os.nice(10)
                except OSError:
                    pass
            result = self.calculate(self.market_db_path, dry_run=False,
                output_dir=self.output_dir, registry_db_path=self.registry_db_path)
            self.write_report(result, self.output_dir)
            elapsed = self.clock() - started
            logger.info("SCORECARD estado=completed duración_s=%.3f", elapsed)
            return {"status": "completed", "duration_s": elapsed, "result": result}
        except Exception as exc:
            elapsed = self.clock() - started
            logger.exception("SCORECARD estado=failed duración_s=%.3f", elapsed)
            return {"status": "failed", "duration_s": elapsed, "reason": str(exc)}

    def start(self):
        self.scheduler.add_job(self.run_if_enabled, "cron", day_of_week="sun",
            hour=6, minute=20, timezone="UTC", id="scorecard_weekly",
            replace_existing=True, max_instances=1, coalesce=True)
        self.scheduler.start()

    def stop(self):
        if getattr(self.scheduler, "running", False):
            self.scheduler.shutdown(wait=True)
