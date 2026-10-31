"""Ejecuta el marcador de resultados sombra fuera del servicio API."""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy import select

logger = logging.getLogger(__name__)


class ShadowMarkerJob:
    def __init__(self, db, market_db_path, *, script_path=None, scheduler=None,
            run=None):
        self.db = db
        self.market_db_path = Path(market_db_path).resolve()
        self.script_path = Path(script_path or Path(__file__).resolve().parents[1] /
            "scripts" / "marcador_sombra.py")
        self.scheduler = scheduler or BackgroundScheduler(timezone="UTC")
        self.run_process = run

    def enabled(self):
        with self.db.engine.connect() as conn:
            raw = conn.execute(select(self.db.meta_profile_settings.c.settings).where(
                self.db.meta_profile_settings.c.profile == "GLOBAL")).scalar_one_or_none()
        return bool(json.loads(raw or "{}").get("shadow_marker_enabled", False))

    def _app_db_path(self):
        value = self.db.engine.url.database
        if value in {None, "", ":memory:"}:
            raise ValueError("el marcador de sombra requiere una BD de aplicación persistente")
        return str(Path(value).resolve())

    def run_if_enabled(self):
        if not self.enabled():
            logger.info("SHADOW_MARKER estado=apagado")
            return {"status": "disabled"}
        args = [sys.executable, str(self.script_path), "--app-db", self._app_db_path(),
            "--market-db", str(self.market_db_path)]
        env = os.environ.copy()
        env.update({"OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1",
            "MALLOC_ARENA_MAX": "2"})
        if os.name == "posix":
            args = ["nice", "-n", "10", *args]
        try:
            if self.run_process is not None:
                result = self.run_process(args, capture_output=True, text=True,
                    timeout=600, check=False, env=env)
            else:
                result = subprocess.run(args, capture_output=True, text=True,
                    timeout=600, check=False, env=env)
            if result.returncode:
                logger.error("SHADOW_MARKER estado=failed salida=%s",
                    (result.stderr or "")[-2000:])
                return {"status": "failed", "reason": (result.stderr or "falló subproceso")[-2000:]}
            logger.info("SHADOW_MARKER estado=completed salida=%s", (result.stdout or "")[-2000:])
            return {"status": "completed", "stdout": result.stdout or ""}
        except subprocess.TimeoutExpired:
            logger.error("SHADOW_MARKER estado=timeout")
            return {"status": "timeout"}
        except Exception as exc:
            logger.exception("SHADOW_MARKER estado=failed")
            return {"status": "failed", "reason": str(exc)}

    def start(self):
        self.scheduler.add_job(self.run_if_enabled, "cron", hour=4, minute=20,
            timezone="UTC", id="shadow_marker_daily", replace_existing=True,
            max_instances=1, coalesce=True)
        self.scheduler.start()

    def stop(self):
        if getattr(self.scheduler, "running", False):
            self.scheduler.shutdown(wait=True)
