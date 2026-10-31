"""Lanza la evaluación pesada de escenarios en un proceso aislado."""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy import select


logger = logging.getLogger(__name__)
TIMEOUT_SECONDS = 600
MEMORY_LIMIT_BYTES = 600 * 1024 * 1024


class ScenarioCacheJob:
    def __init__(self, db, market_db_path, *, script_path=None, scheduler=None,
            run=subprocess.run, clock=time.perf_counter):
        self.db = db
        self.market_db_path = Path(market_db_path).resolve()
        self.script_path = Path(script_path or Path(__file__).resolve().parents[1] /
            "scripts" / "escenarios_cache.py")
        self.scheduler = scheduler or BackgroundScheduler(timezone="UTC")
        self.run_process = run
        self.clock = clock

    def enabled(self):
        with self.db.engine.connect() as conn:
            raw = conn.execute(select(self.db.meta_profile_settings.c.settings).where(
                self.db.meta_profile_settings.c.profile == "GLOBAL")).scalar_one_or_none()
        return bool(json.loads(raw or "{}").get("scenario_cache_enabled", False))

    def _app_db_path(self):
        value = self.db.engine.url.database
        if value in {None, "", ":memory:"}:
            raise ValueError("la caché de escenarios requiere una BD app persistente")
        return str(Path(value).resolve())

    @staticmethod
    def _child_setup():
        try:
            os.nice(10)
        except (AttributeError, OSError):
            pass
        try:
            import resource
            resource.setrlimit(resource.RLIMIT_AS,
                (MEMORY_LIMIT_BYTES, MEMORY_LIMIT_BYTES))
        except (ImportError, AttributeError, OSError, ValueError):
            pass

    def _mark_timeout(self):
        try:
            with sqlite3.connect(self.market_db_path, timeout=5) as conn:
                conn.execute("UPDATE scenario_eval_runs SET status='timeout',error=? "
                    "WHERE run_id=(SELECT run_id FROM scenario_eval_runs "
                    "WHERE status='running' ORDER BY as_of DESC LIMIT 1)",
                    (f"subproceso excedió {TIMEOUT_SECONDS} s",))
        except sqlite3.Error:
            logger.exception("No se pudo registrar timeout de scenario cache")

    def run_if_enabled(self):
        started = self.clock()
        if not self.enabled():
            elapsed = self.clock() - started
            logger.info("SCENARIO_CACHE estado=apagado duración_s=%.3f", elapsed)
            return {"status": "disabled", "duration_s": elapsed}
        args = [sys.executable, str(self.script_path), "--app-db", self._app_db_path(),
            "--market-db", str(self.market_db_path)]
        kwargs = {"capture_output": True, "text": True,
            "timeout": TIMEOUT_SECONDS, "check": False}
        if os.name == "posix":
            kwargs["preexec_fn"] = self._child_setup
        try:
            result = self.run_process(args, **kwargs)
            elapsed = self.clock() - started
            if result.returncode:
                logger.error("SCENARIO_CACHE estado=failed duración_s=%.3f stderr=%s",
                    elapsed, (result.stderr or "")[-2000:])
                return {"status": "failed", "duration_s": elapsed,
                    "reason": (result.stderr or "subproceso falló")[-2000:]}
            logger.info("SCENARIO_CACHE estado=completed duración_s=%.3f salida=%s",
                elapsed, (result.stdout or "")[-2000:])
            return {"status": "completed", "duration_s": elapsed,
                "stdout": result.stdout or ""}
        except subprocess.TimeoutExpired:
            self._mark_timeout()
            elapsed = self.clock() - started
            logger.error("SCENARIO_CACHE estado=timeout duración_s=%.3f", elapsed)
            return {"status": "timeout", "duration_s": elapsed}
        except Exception as exc:
            elapsed = self.clock() - started
            logger.exception("SCENARIO_CACHE estado=failed duración_s=%.3f", elapsed)
            return {"status": "failed", "duration_s": elapsed, "reason": str(exc)}

    def start(self):
        self.scheduler.add_job(self.run_if_enabled, "cron", minute=10,
            timezone="UTC", id="scenario_cache_hourly", replace_existing=True,
            max_instances=1, coalesce=True)
        self.scheduler.start()

    def stop(self):
        if getattr(self.scheduler, "running", False):
            self.scheduler.shutdown(wait=True)
