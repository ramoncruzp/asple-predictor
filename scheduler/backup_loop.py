"""Daily, online SQLite backups for the active application database."""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from apscheduler.schedulers.background import BackgroundScheduler

logger = logging.getLogger(__name__)
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


class BackupLoop:
    def __init__(self, db: Any, settings: Any, scheduler: Any = None,
                 clock: Callable[[], datetime] | None = None):
        self.db = db
        self.settings = settings
        self.scheduler = scheduler or BackgroundScheduler(timezone="UTC")
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def _backup_files(self) -> list[Path]:
        directory = self._directory()
        return sorted(directory.glob("asple_*.db"), key=lambda item: item.name, reverse=True)

    def _directory(self) -> Path:
        path = Path(getattr(self.settings, "db_backup_dir", "backups")).expanduser()
        return path.resolve() if path.is_absolute() else (REPOSITORY_ROOT / path).resolve()

    def backup_if_due(self, force: bool = False) -> Path | None:
        if not bool(getattr(self.settings, "db_backup_enabled", True)):
            return None
        try:
            directory = self._directory()
            directory.mkdir(parents=True, exist_ok=True)
            previous = self._backup_files()
            now = self.clock().astimezone(timezone.utc)
            if not force and previous:
                modified = datetime.fromtimestamp(previous[0].stat().st_mtime, timezone.utc)
                if now - modified < timedelta(hours=24):
                    return None
            destination = directory / f"asple_{now:%Y%m%d_%H%M%S}.db"
            raw = self.db.engine.raw_connection()
            try:
                with sqlite3.connect(destination) as target:
                    raw.driver_connection.backup(target)
            finally:
                raw.close()
            with sqlite3.connect(destination) as check:
                integrity = check.execute("PRAGMA integrity_check").fetchone()
            if not integrity or integrity[0] != "ok":
                destination.unlink(missing_ok=True)
                logger.error("SQLite backup integrity check failed at %s: %s", destination, integrity)
                return None
            backups = self._backup_files()
            for old in backups[max(1, int(self.settings.db_backup_keep)):]:
                old.unlink()
            logger.info("SQLite backup created at %s", destination)
            return destination
        except Exception:
            logger.exception("SQLite backup failed; application remains available")
            return None

    def start(self) -> None:
        if not bool(getattr(self.settings, "db_backup_enabled", True)):
            return
        self.backup_if_due()
        logger.info("SQLite backup directory resolved to %s", self._directory())
        self.scheduler.add_job(self.backup_if_due, "cron", hour=0, minute=10,
                               timezone="UTC", id="database_backup", replace_existing=True)
        self.scheduler.start()

    def stop(self) -> None:
        if getattr(self.scheduler, "running", False):
            self.scheduler.shutdown(wait=True)
