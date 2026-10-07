"""Background volatility onboarding for registered USDT pairs."""
from __future__ import annotations

import logging
import os
import queue
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from config.models_config import ACTIVE_SYMBOL, VOL_SYMBOL, load_vol_consensus, vol_artifact_dir, vol_base, vol_champions, vol_consensus_path, vol_manifest_path
from models.training_jobs import _is_pid_alive

_LOG = logging.getLogger(__name__)
ACTIVE_READINESS = ("descargando", "entrenando", "consensuando")
READINESS_STATES = ("pendiente", *ACTIVE_READINESS, "lista", "datos_insuficientes", "error")


class OnboardingArtifactsSaving(Exception):
    pass


class OnboardingCancelled(Exception):
    pass


def coin_is_ready(db, registry, symbol: str) -> bool:
    """Use legacy XRP or require ready artifacts. registry is the coin-row fallback for DB adapters without get_coin."""
    if symbol == VOL_SYMBOL:
        return True
    if db is None or not hasattr(db, "get_readiness"):
        return True
    coin = db.get_coin(symbol) if hasattr(db, "get_coin") else registry
    if coin is not None and int(coin.get("active", 0)) != 1:
        return False
    row = db.get_readiness(symbol)
    if not row or row.get("state") != "lista":
        return False
    return vol_manifest_path(symbol).is_file() and load_vol_consensus(symbol) is not None


def readiness_public(row: dict | None) -> dict:
    fields = ("state", "stage_detail", "progress_pct", "history_days", "error", "ready_at")
    return {key: (row or {}).get(key) for key in fields} if row else {
        "state": "pendiente", "stage_detail": None, "progress_pct": 0.0,
        "history_days": None, "error": None, "ready_at": None,
    }


class CoinOnboardingService:
    def __init__(self, db, registry, training_job_service, *, root: Path | str,
                 popen_factory=None, kill_tree=None, pid_alive=None, step_runner=None,
                 poll_seconds: float = 0.1):
        self.db, self.registry, self.training_job_service = db, registry, training_job_service
        self.root = Path(root)
        self.log_dir = self.root / "logs" / "training"
        self.popen_factory = popen_factory or subprocess.Popen
        self.kill_tree = kill_tree or TrainingTreeKiller()
        self.pid_alive = pid_alive or _is_pid_alive
        self.step_runner = step_runner
        self.poll_seconds = poll_seconds
        self._queue: queue.Queue[str | None] = queue.Queue()
        self._lock = threading.RLock()
        self._queued: set[str] = set()
        self._processes: dict[str, object] = {}
        self._cancelled: set[str] = set()
        self._worker: threading.Thread | None = None
        self._stopping = threading.Event()
        self._done: dict[str, threading.Event] = {}

    def start(self):
        self.recover_interrupted()
        for coin in self.db.get_active_coins():
            symbol = coin["symbol"]
            if symbol != VOL_SYMBOL and self.db.get_readiness(symbol) is None:
                self.db.set_readiness(symbol, "pendiente", stage_detail="pendiente", progress_pct=0.0)
        with self._lock:
            if self._worker and self._worker.is_alive():
                return
            self._stopping.clear()
            self._worker = threading.Thread(target=self._work, name="coin-onboarding", daemon=True)
            self._worker.start()

    def stop(self):
        self._stopping.set()
        with self._lock:
            active = list(self._processes.items())
        for symbol, process in active:
            try:
                self.kill_tree(int(process.pid))
            except Exception:
                _LOG.exception("No se pudo detener onboarding de %s", symbol)
        self._queue.put(None)
        if self._worker:
            self._worker.join(timeout=5)

    def recover_interrupted(self):
        for row in self.db.list_readiness():
            if row.get("state") not in ACTIVE_READINESS:
                continue
            pid = row.get("pid")
            if pid and self.pid_alive(pid):
                _LOG.warning("Onboarding de %s conserva subproceso vivo tras reinicio; no se reanuda", row["symbol"])
                threading.Thread(target=self._watch_orphan, args=(row["symbol"], int(pid)), daemon=True).start()
                continue
            self._cleanup_symbol(row["symbol"])
            self.db.set_readiness(row["symbol"], "error", stage_detail="error",
                                  error="Interrumpido por reinicio; reintentar", pid=None)

    def _watch_orphan(self, symbol: str, pid: int):
        while self.pid_alive(pid):
            time.sleep(self.poll_seconds)
        self._cleanup_symbol(symbol)
        self.db.set_readiness(symbol, "error", stage_detail="error",
                              error="Interrumpido por reinicio; reintentar", pid=None)

    def is_training_active(self) -> bool:
        return bool(self.db.has_active_training_job())

    def enqueue(self, symbol: str) -> bool:
        with self._lock:
            if symbol in self._queued or symbol in self._processes:
                return False
            self._queued.add(symbol)
            self._done[symbol] = threading.Event()
            if self._worker is None or not self._worker.is_alive():
                self.start()
            self._queue.put(symbol)
            return True

    def prepare(self, symbol: str) -> bool:
        row = self.db.get_readiness(symbol)
        if row and row.get("state") in ACTIVE_READINESS:
            return False
        if coin_is_ready(self.db, self.registry, symbol):
            return False
        self.db.set_readiness(symbol, "pendiente", stage_detail="pendiente", progress_pct=0.0,
                              history_days=(row or {}).get("history_days"), error=None, ready_at=None, pid=None)
        return self.enqueue(symbol)

    def in_progress(self, symbol: str) -> bool:
        row = self.db.get_readiness(symbol)
        return bool(row and (row.get("state") in ACTIVE_READINESS or
                            (row.get("state") == "pendiente" and symbol in self._queued)))

    def cancel(self, symbol: str) -> bool:
        row = self.db.get_readiness(symbol)
        if not row or not self.in_progress(symbol):
            return False
        if row.get("stage_detail") == "guardando":
            raise OnboardingArtifactsSaving("Guardando artefactos; espera unos segundos")
        with self._lock:
            self._cancelled.add(symbol)
            process = self._processes.get(symbol)
        if process is not None:
            self.kill_tree(int(process.pid))
        else:
            self.db.set_readiness(symbol, "error", stage_detail="error",
                                  error="Preparación cancelada por el usuario", pid=None)
            self._done.setdefault(symbol, threading.Event()).set()
        return True

    def wait_for(self, symbol: str, timeout: float = 10.0) -> dict | None:
        event = self._done.get(symbol)
        if event:
            event.wait(timeout)
        return self.db.get_readiness(symbol)

    def _work(self):
        while not self._stopping.is_set():
            try:
                symbol = self._queue.get(timeout=self.poll_seconds)
            except queue.Empty:
                continue
            if symbol is None:
                break
            try:
                self._process_coin(symbol)
            finally:
                with self._lock:
                    self._queued.discard(symbol)
                self._done.setdefault(symbol, threading.Event()).set()
                self._queue.task_done()

    def _set_progress(self, symbol: str, line: str):
        if not line.startswith("PROGRESS:"):
            return
        detail = line.partition(":")[2].strip()[:160]
        state = "descargando"
        progress = 5.0
        if detail.startswith("entrenando") or detail == "guardando":
            state, progress = "entrenando", 55.0 if detail != "guardando" else 90.0
        elif detail.startswith("consensuando"):
            state, progress = "consensuando", 94.0
        elif detail == "consenso_guardado":
            state, progress = "consensuando", 99.0
        self.db.set_readiness(symbol, state, stage_detail=detail, progress_pct=progress)

    def _wait_training_slot(self, symbol: str):
        if symbol in self._cancelled:
            raise OnboardingCancelled()
        while not self._stopping.is_set() and self.is_training_active():
            if symbol in self._cancelled:
                raise OnboardingCancelled()
            time.sleep(self.poll_seconds)
        if self._stopping.is_set():
            raise OnboardingCancelled()

    def _process_coin(self, symbol: str):
        log_path = self.log_dir / f"onboarding_{symbol.lower()}.log"
        try:
            self._wait_training_slot(symbol)
            while not self.db.try_claim_coin_onboarding(symbol):
                self._wait_training_slot(symbol)
                time.sleep(self.poll_seconds)
            self.log_dir.mkdir(parents=True, exist_ok=True)
            self._archive_consensus(symbol, log_path)
            train = [sys.executable, "-u", "scripts/train_vol_models.py", "--symbol", symbol,
                     "--refresh-candles", "--candles-dir", "data/cache/vol_train",
                     "--days", "730", "--min-days", "540", "--progress"]
            train_result = self._run_step(symbol, train, log_path)
            if symbol in self._cancelled:
                raise OnboardingCancelled()
            if train_result.returncode:
                output = (train_result.stdout or "") + "\n" + (train_result.stderr or "")
                if "Datos insuficientes" in output or "se requieren" in output:
                    days = self._history_days(symbol)
                    self.db.set_readiness(symbol, "datos_insuficientes", stage_detail="datos_insuficientes",
                        progress_pct=0.0, history_days=days, error=output.strip()[-1000:], pid=None)
                    self._cleanup_temps(symbol)
                    return
                raise RuntimeError(output.strip()[-1000:] or f"Entrenamiento terminó con código {train_result.returncode}")
            self.db.set_readiness(symbol, "consensuando", stage_detail="consensuando", progress_pct=92.0, pid=None)
            base = vol_base(symbol)
            candles = Path("data/cache/vol_train") / f"{base}_1h.csv"
            candles_5m = Path("data/cache/vol_train") / f"{base}_5m.csv"
            consensus = [sys.executable, "-u", "scripts/vol_consensus_eval.py", "--symbol", symbol,
                         "--candles", str(candles), "--candles-5m", str(candles_5m), "--progress"]
            result = self._run_step(symbol, consensus, log_path)
            if symbol in self._cancelled:
                raise OnboardingCancelled()
            if result.returncode:
                raise RuntimeError(((result.stdout or "") + "\n" + (result.stderr or "")).strip()[-1000:]
                                   or f"Consenso terminó con código {result.returncode}")
            if load_vol_consensus(symbol) is None:
                raise RuntimeError("El estudio de consenso no produjo un reporte válido para los cuatro horizontes")
            predictor = self.registry.reload(symbol) if hasattr(self.registry, "reload") else None
            if predictor is None:
                predictor = self.registry.load(symbol)
            if predictor is None:
                raise RuntimeError("No se pudo cargar el manifest y los modelos entrenados")
            champions, provisional = vol_champions(symbol)
            if provisional or any(not predictor.is_champion(h, champions[h]) for h in (1, 2, 4, 24)):
                raise RuntimeError("La recarga no activó los campeones del consenso")
            now = datetime.now(timezone.utc)
            self.db.set_readiness(symbol, "lista", stage_detail="lista", progress_pct=100.0,
                                  error=None, ready_at=now, pid=None)
        except OnboardingCancelled:
            self._cleanup_symbol(symbol)
            self.db.set_readiness(symbol, "error", stage_detail="error",
                                  error="Preparación cancelada por el usuario", pid=None)
        except Exception as exc:
            self._cleanup_symbol(symbol)
            self.db.set_readiness(symbol, "error", stage_detail="error",
                                  error=str(exc)[:1000] or type(exc).__name__, pid=None)
        finally:
            with self._lock:
                self._processes.pop(symbol, None)
                self._cancelled.discard(symbol)

    def _archive_consensus(self, symbol: str, log_path: Path) -> Path | None:
        consensus_path = vol_consensus_path(symbol)
        if not consensus_path.exists():
            return None
        base = vol_base(symbol)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        archived_path = consensus_path.with_name(
            f"consensus_{base}.archivado_{timestamp}.json"
        )
        suffix = 1
        while archived_path.exists():
            archived_path = consensus_path.with_name(
                f"consensus_{base}.archivado_{timestamp}_{suffix:02d}.json"
            )
            suffix += 1
        try:
            os.replace(consensus_path, archived_path)
        except OSError as exc:
            raise RuntimeError(f"No se pudo archivar el consenso anterior {consensus_path}: {exc}") from exc
        with log_path.open("a", encoding="utf-8", newline="\n") as log_file:
            log_file.write(f"CONSENSUS_ARCHIVED: {archived_path}\n")
        return archived_path

    def _run_step(self, symbol: str, command: list[str], log_path: Path):
        if self.step_runner is not None:
            result = self.step_runner(
                command,
                symbol,
                lambda line: self._set_progress(symbol, line),
            )
            if isinstance(result, int):
                result = subprocess.CompletedProcess(command, result, "", "")
            return result

        creationflags = (
            getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            if os.name == "nt"
            else 0
        )
        process = self.popen_factory(
            command,
            cwd=str(self.root),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            creationflags=creationflags,
            start_new_session=os.name != "nt",
        )
        with self._lock:
            self._processes[symbol] = process
        row = self.db.get_readiness(symbol) or {}
        self.db.set_readiness(
            symbol,
            row["state"],
            stage_detail=row.get("stage_detail"),
            progress_pct=row.get("progress_pct"),
            pid=int(process.pid),
        )
        lines = []
        try:
            with log_path.open("a", encoding="utf-8", newline="\n") as log_file:
                for raw_line in process.stdout:
                    line = raw_line.rstrip("\r\n")
                    lines.append(line)
                    log_file.write(line + "\n")
                    log_file.flush()
                    self._set_progress(symbol, line)
                    if symbol in self._cancelled:
                        self.kill_tree(int(process.pid))
                return_code = int(process.wait())
            return subprocess.CompletedProcess(
                command,
                return_code,
                "\n".join(lines),
                "",
            )
        finally:
            row = self.db.get_readiness(symbol) or {}
            self.db.set_readiness(
                symbol,
                row.get("state") or "error",
                stage_detail=row.get("stage_detail"),
                progress_pct=row.get("progress_pct"),
                pid=None,
            )
            with self._lock:
                self._processes.pop(symbol, None)

    def _history_days(self, symbol: str) -> int | None:
        import pandas as pd

        base = vol_base(symbol)
        path = self.root / "data/cache/vol_train" / f"{base}_1h.csv"
        try:
            frame = pd.read_csv(path)
            stamps = pd.to_datetime(frame["timestamp"], utc=True, errors="coerce").dropna()
            if len(stamps) <= 1:
                return 0
            duration = (stamps.max() - stamps.min()).total_seconds()
            return max(0, int(duration // 86400))
        except Exception:
            return None

    def _cleanup_symbol(self, symbol: str):
        self._cleanup_temps(symbol)

    def _cleanup_temps(self, symbol: str):
        base = vol_base(symbol)
        directories = (
            self.root / vol_artifact_dir(symbol),
            self.root / "data/cache/vol_train",
        )
        for directory in directories:
            if not directory.exists():
                continue
            for temp_path in directory.glob("*.tmp"):
                if directory.name == "vol_train" and not temp_path.name.startswith(base + "_"):
                    continue
                try:
                    temp_path.unlink(missing_ok=True)
                except OSError:
                    _LOG.warning("No se pudo eliminar temporal %s", temp_path)

        lock_path = vol_consensus_path(symbol).with_name(
            vol_consensus_path(symbol).name + ".lock"
        )
        if not lock_path.exists():
            return
        try:
            pid = int(lock_path.read_text(encoding="ascii").strip())
        except (OSError, ValueError):
            _LOG.warning("No se eliminó lock huérfano sin PID verificable: %s", lock_path)
            return
        if not self.pid_alive(pid):
            try:
                lock_path.unlink(missing_ok=True)
            except OSError:
                _LOG.warning("No se pudo eliminar lock huérfano: %s", lock_path)


class TrainingTreeKiller:
    def __call__(self, pid: int):
        if os.name == "nt":
            result = subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                capture_output=True,
                check=False,
            )
            return result.returncode == 0
        try:
            os.killpg(pid, 15)
            return True
        except ProcessLookupError:
            return False
