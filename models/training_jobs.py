"""Persistent, single-flight orchestration for user-requested direction training."""
from __future__ import annotations

import json
import logging
import os
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import func, select, update

ROOT = Path(__file__).resolve().parents[1]
ACTIVE_STATUSES = ("pendiente", "descargando", "entrenando", "running")
ALL_STATUSES = (*ACTIVE_STATUSES, "listo", "error", "cancelado", "interrumpido")
TRAINING_MIN_DAYS = 365
TRAINING_MAX_DAYS = 730
VOL_RETRAIN_SCHEDULE = None
_LOG = logging.getLogger(__name__)
IS_WINDOWS = os.name == "nt"

try:
    import psutil as _psutil
except ImportError:
    _psutil = None
PID_CHECK_METHOD = ("psutil.pid_exists + create_time" if IS_WINDOWS and _psutil is not None
                    else "Windows without psutil: always false" if IS_WINDOWS
                    else "os.kill(pid, 0) (POSIX)")


class ActiveTrainingJob(Exception):
    pass


def _now_for_db(db):
    value = datetime.now(timezone.utc)
    return value.replace(tzinfo=None) if db.engine.dialect.name == "sqlite" else value


def _is_pid_alive(pid: int | None, started_at: datetime | str | None = None) -> bool:
    if not pid or int(pid) <= 0:
        return False
    pid = int(pid)
    if IS_WINDOWS:
        if _psutil is None:
            _LOG.warning("No se puede comprobar si el PID %s sigue activo en Windows sin psutil; se considera terminado.", pid)
            return False
        try:
            if not _psutil.pid_exists(pid):
                return False
            created_at = _psutil.Process(pid).create_time()
            if started_at is None:
                return True
            if isinstance(started_at, str):
                started_at = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
            if started_at.tzinfo is None:
                started_at = started_at.replace(tzinfo=timezone.utc)
            return abs(created_at - started_at.timestamp()) <= 5.0
        except (_psutil.NoSuchProcess, _psutil.ZombieProcess, _psutil.AccessDenied,
                OSError, TypeError, ValueError, OverflowError):
            return False
    try:
        os.kill(pid, 0)
        return True
    except (OSError, OverflowError) as exc:
        return getattr(exc, "winerror", None) == 5 or getattr(exc, "errno", None) == 1


def read_metrics_manifest(root: Path = ROOT, symbol: str = "XRPUSDT", interval: str = "1h") -> dict:
    base = symbol[:-4].lower() if symbol.endswith("USDT") else symbol.lower()
    path = root / "models" / "saved" / f"metrics_{base}_{interval}.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def artifact_trained_at(manifest: dict, model_name: str, *, root: Path = ROOT,
                        symbol: str = "XRPUSDT", interval: str = "1h") -> str | None:
    models = manifest.get("models") if isinstance(manifest.get("models"), dict) else {}
    entry = models.get(model_name) if isinstance(models.get(model_name), dict) else None
    if entry is not None:
        value = entry.get("trained_at") or manifest.get("trained_at")
        if value:
            return str(value)
    elif model_name in models:
        value = manifest.get("trained_at")
        if value:
            return str(value)
    suffix = ".pt" if model_name == "model_b" else ".joblib"
    base = symbol[:-4].lower() if symbol.endswith("USDT") else symbol.lower()
    artifact = Path(root) / "models" / "saved" / f"{model_name}_{base}_{interval}{suffix}"
    try:
        return datetime.fromtimestamp(artifact.stat().st_mtime, timezone.utc).isoformat()
    except OSError:
        return None


def predictions_before_training(db, symbol: str, interval: str, model_name: str, trained_at: str | None):
    if not trained_at:
        return None
    if getattr(db, "engine", None) is None or getattr(db, "predictions", None) is None:
        return None
    try:
        cutoff = datetime.fromisoformat(trained_at.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if db.engine.dialect.name == "sqlite" and cutoff.tzinfo is not None:
        cutoff = cutoff.astimezone(timezone.utc).replace(tzinfo=None)
    query = select(func.count()).select_from(db.predictions).where(
        db.predictions.c.symbol == symbol,
        db.predictions.c.interval == interval,
        db.predictions.c.model_name == model_name,
        db.predictions.c.predicted_at < cutoff,
    )
    try:
        with db.engine.connect() as conn:
            return int(conn.execute(query).scalar_one())
    except Exception:
        return None


class TrainingJobService:
    def __init__(self, db, root: Path = ROOT, *, popen_factory=None, command_factory=None, pid_alive=None, kill_tree=None):
        self.db = db
        self.root = Path(root)
        self.saved_dir = self.root / "models" / "saved"
        self.log_dir = self.root / "logs" / "training"
        self.popen_factory = popen_factory or subprocess.Popen
        self.command_factory = command_factory or self._command
        self.pid_alive = pid_alive or _is_pid_alive
        self._pid_alive_uses_started_at = pid_alive is None
        self.kill_tree = kill_tree or self._kill_tree
        self._processes = {}
        self._threads = {}
        self._lock = threading.RLock()

    def _is_job_pid_alive(self, pid, started_at=None):
        if self._pid_alive_uses_started_at:
            return self.pid_alive(pid, started_at)
        return self.pid_alive(pid)

    @staticmethod
    def _command(job):
        return [
            sys.executable, "-u", "scripts/train_models.py",
            "--symbol", job["symbol"], "--interval", job["interval"],
            "--days", str(job["days"]), "--models", ",".join(job["models"]), "--progress",
        ]

    @staticmethod
    def _safe_env():
        secret_fragments = ("KEY", "SECRET", "TOKEN", "PASSWORD", "CREDENTIAL")
        return {
            key: value for key, value in os.environ.items()
            if not any(fragment in key.upper() for fragment in secret_fragments)
        }

    @staticmethod
    def _kill_tree(pid: int):
        if os.name == "nt":
            result = subprocess.run(
                ["taskkill", "/PID", str(int(pid)), "/T", "/F"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
            )
            return result.returncode == 0
        try:
            os.killpg(int(pid), signal.SIGTERM)
            return True
        except ProcessLookupError:
            return False

    def _row(self, row):
        if row is None:
            return None
        item = dict(row)
        item["models"] = [name for name in str(item.get("models") or "").split(",") if name]
        for key in ("created_at", "started_at", "finished_at"):
            if item.get(key) is not None:
                value = item[key]
                if value.tzinfo is None:
                    value = value.replace(tzinfo=timezone.utc)
                item[key] = value.isoformat()
        item["confirm_reset_evaluation"] = bool(item.get("confirm_reset_evaluation"))
        return item

    def get(self, job_id: int):
        table = self.db.training_jobs
        with self.db.engine.connect() as conn:
            row = conn.execute(select(table).where(table.c.id == int(job_id))).mappings().first()
        return self._row(row)

    def latest(self):
        table = self.db.training_jobs
        with self.db.engine.connect() as conn:
            row = conn.execute(select(table).order_by(table.c.id.desc()).limit(1)).mappings().first()
        return self._row(row)

    def list_jobs(self, limit: int = 10):
        table = self.db.training_jobs
        with self.db.engine.connect() as conn:
            rows = conn.execute(select(table).order_by(table.c.id.desc()).limit(limit)).mappings().all()
        return [self._row(row) for row in rows]

    def _update(self, job_id: int, **values):
        values["id"] = int(job_id)
        table = self.db.training_jobs
        job_id = values.pop("id")
        with self.db.engine.begin() as conn:
            conn.execute(update(table).where(table.c.id == job_id).values(**values))

    def _update_active(self, job_id: int, **values) -> bool:
        table = self.db.training_jobs
        with self.db.engine.begin() as conn:
            result = conn.execute(update(table).where(
                table.c.id == int(job_id), table.c.status.in_(ACTIVE_STATUSES),
            ).values(**values))
        return result.rowcount == 1

    def create_job(self, *, symbol: str, interval: str, models: list[str], days: int, confirm_reset_evaluation: bool):
        table = self.db.training_jobs
        created = _now_for_db(self.db)
        with self.db.engine.connect() as conn:
            try:
                if self.db.engine.dialect.name == "sqlite":
                    conn.exec_driver_sql("BEGIN IMMEDIATE")
                active = conn.execute(
                    select(table.c.id).where(table.c.status.in_(ACTIVE_STATUSES)).limit(1)
                ).first()
                if active:
                    conn.rollback()
                    raise ActiveTrainingJob("Ya existe un trabajo de entrenamiento activo")
                result = conn.execute(table.insert().values(
                    symbol=symbol, interval=interval, models=",".join(models), days=days,
                    status="pendiente", phase="pendiente", created_at=created,
                    confirm_reset_evaluation=int(confirm_reset_evaluation),
                ))
                job_id = int(result.inserted_primary_key[0])
                conn.commit()
            except ActiveTrainingJob:
                raise
            except Exception:
                conn.rollback()
                raise
        self.run_training_job(job_id)
        return self.get(job_id)

    def run_training_job(self, job_id: int):
        """Start the child process; this method is also available to a future scheduler."""
        job = self.get(job_id)
        if job is None or job["status"] not in ACTIVE_STATUSES:
            return job
        self.log_dir.mkdir(parents=True, exist_ok=True)
        log_path = self.log_dir / f"job_{int(job_id)}.log"
        log_handle = log_path.open("wb")
        try:
            creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) if os.name == "nt" else 0
            process = self.popen_factory(
                self.command_factory(job), cwd=str(self.root), env=self._safe_env(),
                stdout=log_handle, stderr=subprocess.STDOUT, creationflags=creationflags,
                start_new_session=os.name != "nt",
            )
        except Exception as exc:
            log_handle.close()
            self._update(job_id, status="error", phase="error", finished_at=_now_for_db(self.db),
                         error=str(exc)[:500], exit_code=-1, log_path=str(log_path))
            return self.get(job_id)
        self._update(job_id, status="descargando", phase="descargando", started_at=_now_for_db(self.db),
                     pid=int(process.pid), log_path=str(log_path))
        with self._lock:
            self._processes[int(job_id)] = process
        thread = threading.Thread(
            target=self._monitor, args=(int(job_id), process, log_handle, log_path),
            name=f"training-job-{job_id}", daemon=True,
        )
        with self._lock:
            self._threads[int(job_id)] = thread
        thread.start()
        return self.get(job_id)

    def _set_progress(self, job_id: int, line: str):
        if not line.startswith("PROGRESS:"):
            return
        phase = line.partition(":")[2].strip()[:120]
        status = "descargando" if phase == "descargando" else "entrenando"
        if phase == "guardando":
            status = "entrenando"
        self._update_active(job_id, status=status, phase=phase)

    def _monitor(self, job_id: int, process, log_handle, log_path: Path):
        offset = 0
        try:
            while process.poll() is None:
                try:
                    with log_path.open("r", encoding="utf-8", errors="replace") as reader:
                        reader.seek(offset)
                        lines = reader.readlines()
                        offset = reader.tell()
                    for line in lines:
                        self._set_progress(job_id, line.rstrip("\r\n"))
                except OSError:
                    pass
                time.sleep(0.1)
            exit_code = int(process.wait())
            try:
                with log_path.open("r", encoding="utf-8", errors="replace") as reader:
                    reader.seek(offset)
                    lines = reader.readlines()
                for line in lines:
                    self._set_progress(job_id, line.rstrip("\r\n"))
            except OSError:
                pass
            if exit_code == 0:
                self._update_active(job_id, status="listo", phase="listo", finished_at=_now_for_db(self.db), exit_code=0, error=None)
            else:
                try:
                    tail = log_path.read_text(encoding="utf-8", errors="replace")[-1000:].strip()
                except OSError:
                    tail = ""
                self._update_active(job_id, status="error", phase="error", finished_at=_now_for_db(self.db),
                                    exit_code=exit_code, error=(tail or f"Proceso terminó con código {exit_code}")[:1000])
        except Exception:
            _LOG.exception("Error monitorizando trabajo de entrenamiento %s", job_id)
            self._update_active(job_id, status="error", phase="error", finished_at=_now_for_db(self.db),
                                error="Fallo interno al monitorizar el trabajo")
        finally:
            log_handle.close()
            with self._lock:
                self._processes.pop(job_id, None)
                self._threads.pop(job_id, None)

    def active_or_latest(self):
        table = self.db.training_jobs
        with self.db.engine.connect() as conn:
            row = conn.execute(
                select(table).where(table.c.status.in_(ACTIVE_STATUSES)).order_by(table.c.id.desc()).limit(1)
            ).mappings().first()
        return self._row(row) or self.latest()

    def cancel(self, job_id: int):
        job = self.get(job_id)
        if job is None:
            return None
        now = _now_for_db(self.db)
        if not self._update_active(job_id, status="cancelado", phase="cancelado", finished_at=now, error=None):
            return False
        with self._lock:
            process = self._processes.get(int(job_id))
        pid = int(process.pid) if process is not None else job.get("pid")
        terminated = True
        if pid:
            self.kill_tree(int(pid))
            deadline = time.monotonic() + 5.0
            if process is not None:
                try:
                    process.wait(timeout=max(0.0, deadline - time.monotonic()))
                except subprocess.TimeoutExpired:
                    terminated = False
            else:
                while self.pid_alive(int(pid)) and time.monotonic() < deadline:
                    time.sleep(0.05)
                terminated = not self._is_job_pid_alive(int(pid), job.get("started_at"))
        if terminated:
            self._cleanup_temps(job)
        else:
            message = "Cancelación: el proceso no terminó dentro del límite de 5 s; se conservaron temporales."
            log_path = Path(job["log_path"]) if job.get("log_path") else None
            if log_path:
                try:
                    with log_path.open("a", encoding="utf-8") as log:
                        log.write(message + "\n")
                except OSError:
                    pass
            self._update(job_id, error=message)
        return self.get(job_id)

    def _cleanup_temps(self, job):
        base = job["symbol"][:-4].lower() if job["symbol"].endswith("USDT") else job["symbol"].lower()
        paths = [self.saved_dir / f"metrics_{base}_{job['interval']}.json.tmp"]
        for name in job["models"]:
            suffix = ".pt" if name == "b" else ".joblib"
            paths.append(self.saved_dir / f"model_{name}_{base}_{job['interval']}{suffix}.tmp")
        for path in paths:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                _LOG.warning("No se pudo retirar archivo temporal de entrenamiento: %s", path)

    def recover_interrupted(self):
        table = self.db.training_jobs
        with self.db.engine.connect() as conn:
            rows = conn.execute(select(table).where(table.c.status.in_(ACTIVE_STATUSES))).mappings().all()
        interrupted = []
        for row in rows:
            if not self._is_job_pid_alive(row.get("pid"), row.get("started_at")):
                if self._update_active(row["id"], status="interrumpido", phase="interrumpido",
                                       finished_at=_now_for_db(self.db), error="El proceso ya no está activo"):
                    interrupted.append(int(row["id"]))
            else:
                self._reattach_monitor(row)
        return interrupted

    def _reattach_monitor(self, row):
        job_id, pid = int(row["id"]), int(row["pid"])
        with self._lock:
            if job_id in self._threads:
                return
            raw_path = row.get("log_path")
            log_path = Path(raw_path) if raw_path else self.log_dir / f"job_{job_id}.log"
            thread = threading.Thread(target=self._monitor_recovered,
                                      args=(job_id, pid, row.get("started_at"), log_path),
                                      name=f"training-job-recovered-{job_id}", daemon=True)
            self._threads[job_id] = thread
            thread.start()

    def _monitor_recovered(self, job_id: int, pid: int, started_at, log_path: Path):
        offset = 0
        try:
            while self._is_job_pid_alive(pid, started_at):
                try:
                    with log_path.open("r", encoding="utf-8", errors="replace") as log:
                        log.seek(offset)
                        lines = log.readlines()
                        offset = log.tell()
                    for line in lines:
                        self._set_progress(job_id, line.rstrip("\r\n"))
                except OSError:
                    pass
                time.sleep(0.1)
            try:
                tail = log_path.read_text(encoding="utf-8", errors="replace")[-2000:]
            except OSError:
                tail = ""
            if "Métricas guardadas:" in tail:
                self._update_active(job_id, status="listo", phase="listo", finished_at=_now_for_db(self.db),
                                    exit_code=0, error=None)
            else:
                self._update_active(job_id, status="error", phase="error", finished_at=_now_for_db(self.db),
                                    exit_code=1, error=(tail.strip()[-1000:] or "Proceso terminado sin guardar métricas"))
        except Exception:
            _LOG.exception("Error reenganchando trabajo de entrenamiento %s", job_id)
            self._update_active(job_id, status="error", phase="error", finished_at=_now_for_db(self.db),
                                error="Fallo al reenganchar el monitor")
        finally:
            with self._lock:
                self._threads.pop(job_id, None)

    def wait_for_job(self, job_id: int, timeout: float = 5.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            job = self.get(job_id)
            if not job or job["status"] not in ACTIVE_STATUSES:
                return job
            time.sleep(0.02)
        return self.get(job_id)
