"""Lanza la evaluación pesada de escenarios en un proceso aislado."""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy import select


logger = logging.getLogger(__name__)
TIMEOUT_SECONDS = 600
MEMORY_LIMIT_BYTES = 600 * 1024 * 1024
POLL_SECONDS = 0.2
MEMORY_ENV = {"OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1",
    "MALLOC_ARENA_MAX": "2"}


class ScenarioCacheJob:
    def __init__(self, db, market_db_path, *, script_path=None, scheduler=None,
            run=None, clock=time.perf_counter, rss_reader=None):
        self.db = db
        self.market_db_path = Path(market_db_path).resolve()
        self.script_path = Path(script_path or Path(__file__).resolve().parents[1] /
            "scripts" / "escenarios_cache.py")
        self.scheduler = scheduler or BackgroundScheduler(timezone="UTC")
        self.run_process = run
        self.clock = clock
        self.rss_reader = rss_reader or self._process_rss

    @staticmethod
    def _process_rss(pid):
        try:
            import psutil
            return int(psutil.Process(pid).memory_info().rss)
        except (ImportError, OSError, AttributeError):
            if os.name == "posix":
                try:
                    with open(f"/proc/{int(pid)}/statm", encoding="ascii") as stream:
                        resident_pages = int(stream.read().split()[1])
                    return resident_pages * os.sysconf("SC_PAGE_SIZE")
                except (OSError, ValueError, IndexError):
                    return None
            if os.name == "nt":
                try:
                    import ctypes
                    from ctypes import wintypes

                    class _Counters(ctypes.Structure):
                        _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                            ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                            ("QuotaPagedPoolUsage", ctypes.c_size_t),
                            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                            ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]

                    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
                    psapi = ctypes.WinDLL("psapi", use_last_error=True)
                    handle = kernel.OpenProcess(0x1000, False, int(pid))
                    if not handle:
                        return None
                    try:
                        counters = _Counters()
                        counters.cb = ctypes.sizeof(counters)
                        if psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
                            return int(counters.WorkingSetSize)
                    finally:
                        kernel.CloseHandle(handle)
                except (AttributeError, OSError, TypeError, ValueError):
                    return None
            return None

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

    def _mark_timeout(self):
        try:
            with sqlite3.connect(self.market_db_path, timeout=5) as conn:
                conn.execute("UPDATE scenario_eval_runs SET status='timeout',error=? "
                    "WHERE run_id=(SELECT run_id FROM scenario_eval_runs "
                    "WHERE status='running' ORDER BY as_of DESC LIMIT 1)",
                    (f"subproceso excedió {TIMEOUT_SECONDS} s",))
        except sqlite3.Error:
            logger.exception("No se pudo registrar timeout de scenario cache")

    def _mark_memory(self):
        try:
            with sqlite3.connect(self.market_db_path, timeout=5) as conn:
                conn.execute("UPDATE scenario_eval_runs SET status='memoria',error=? "
                    "WHERE run_id=(SELECT run_id FROM scenario_eval_runs "
                    "WHERE status='running' ORDER BY as_of DESC LIMIT 1)",
                    ("RSS del subproceso superó 600 MiB",))
        except sqlite3.Error:
            logger.exception("No se pudo registrar exceso de memoria de scenario cache")

    def _run_monitored(self, args, env):
        if self.run_process is not None:
            return self.run_process(args, capture_output=True, text=True,
                timeout=TIMEOUT_SECONDS, check=False, env=env)
        with tempfile.TemporaryFile() as stdout_file, tempfile.TemporaryFile() as stderr_file:
            process = subprocess.Popen(args, stdout=stdout_file, stderr=stderr_file, env=env)
            started = self.clock()
            exceeded = False
            while process.poll() is None:
                rss = self.rss_reader(process.pid)
                if rss is not None and rss > MEMORY_LIMIT_BYTES:
                    exceeded = True
                    process.kill()
                    break
                if self.clock() - started > TIMEOUT_SECONDS:
                    process.kill()
                    process.wait()
                    raise subprocess.TimeoutExpired(args, TIMEOUT_SECONDS)
                time.sleep(POLL_SECONDS)
            process.wait()
            stdout_file.seek(0)
            stderr_file.seek(0)
            result = subprocess.CompletedProcess(args, process.returncode,
                stdout_file.read().decode("utf-8", errors="replace"),
                stderr_file.read().decode("utf-8", errors="replace"))
            if exceeded:
                self._mark_memory()
                result.returncode = 75
                result.stderr = (result.stderr or "") + "\nRSS superó 600 MiB; corrida marcada memoria"
            return result

    def run_if_enabled(self):
        started = self.clock()
        if not self.enabled():
            elapsed = self.clock() - started
            logger.info("SCENARIO_CACHE estado=apagado duración_s=%.3f", elapsed)
            return {"status": "disabled", "duration_s": elapsed}
        args = [sys.executable, str(self.script_path), "--app-db", self._app_db_path(),
            "--market-db", str(self.market_db_path)]
        env = os.environ.copy()
        env.update(MEMORY_ENV)
        if os.name == "posix":
            args = ["nice", "-n", "10", *args]
        try:
            result = self._run_monitored(args, env)
            elapsed = self.clock() - started
            if result.returncode:
                status = "memoria" if result.returncode == 75 else "failed"
                logger.error("SCENARIO_CACHE estado=failed duración_s=%.3f stderr=%s",
                    elapsed, (result.stderr or "")[-2000:])
                return {"status": status, "duration_s": elapsed,
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
