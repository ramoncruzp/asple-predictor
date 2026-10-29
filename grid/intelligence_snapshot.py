"""Instantánea acotada y de solo lectura para servir Inteligencia sin recalcularla."""
from __future__ import annotations

import json
import logging
import threading
import time
from collections import OrderedDict
from datetime import datetime, timezone

from sqlalchemy import select

logger = logging.getLogger("asple.slow")
INTELLIGENCE_AUX_CACHE_MAX = 50
INTELLIGENCE_AUX_TTL_S = 300
INTELLIGENCE_SNAPSHOT_MAX_BYTES = 16 * 1024 * 1024


class IntelligenceSnapshotService:
    """Mantiene una única instantánea en memoria/SQLite y un solo cálculo activo."""

    def __init__(self, db, builder):
        self.db = db
        self.builder = builder
        self._lock = threading.RLock()
        self._ready = threading.Event()
        self._calculating = False
        self._snapshot = self._load()
        self._last_error = None
        self._aux_cache = OrderedDict()
        self._aux_pending = OrderedDict()
        self._aux_active = False

    def _load(self):
        try:
            with self.db.engine.connect() as conn:
                raw = conn.execute(select(self.db.intelligence_snapshots.c.payload_json).where(
                    self.db.intelligence_snapshots.c.id == 1)).scalar_one_or_none()
            return json.loads(raw) if raw else None
        except Exception:
            logger.exception("No se pudo restaurar la instantánea de Inteligencia")
            return None

    @property
    def calculating(self):
        with self._lock:
            return self._calculating

    @property
    def snapshot(self):
        return self._snapshot

    def request_refresh(self):
        """Agenda un cálculo único; llamadas simultáneas comparten el que ya existe."""
        with self._lock:
            if self._calculating:
                return {"status": "calculando", "shared": True}
            self._calculating = True
            self._ready.clear()
            worker = threading.Thread(target=self._refresh, name="intelligence-snapshot", daemon=True)
            worker.start()
        return {"status": "calculando", "shared": False}

    def get_aux(self, key):
        with self._lock:
            entry = self._aux_cache.get(str(key))
            if entry is None or time.monotonic() - entry[0] >= INTELLIGENCE_AUX_TTL_S:
                return None
            self._aux_cache.move_to_end(str(key))
            return entry[1]

    def request_aux_refresh(self, key, builder):
        key = str(key)
        with self._lock:
            if self.get_aux(key) is not None:
                return {"status": "ready", "shared": True}
            self._aux_pending[key] = builder
            self._aux_pending.move_to_end(key)
            if self._aux_active:
                return {"status": "calculando", "shared": True}
            self._aux_active = True
            threading.Thread(target=self._drain_aux, name="intelligence-aux", daemon=True).start()
        return {"status": "calculando", "shared": False}

    def _drain_aux(self):
        while True:
            with self._lock:
                if not self._aux_pending:
                    self._aux_active = False
                    return
                key, builder = self._aux_pending.popitem(last=False)
            started = time.perf_counter()
            try:
                value = json.loads(json.dumps(builder(), ensure_ascii=False, default=str))
                with self._lock:
                    self._aux_cache[key] = (time.monotonic(), value)
                    self._aux_cache.move_to_end(key)
                    while len(self._aux_cache) > INTELLIGENCE_AUX_CACHE_MAX:
                        self._aux_cache.popitem(last=False)
                logger.warning("INTELLIGENCE_AUX_REFRESH key=%s duration_s=%.3f", key,
                    time.perf_counter() - started)
            except Exception:
                logger.exception("Falló el resumen auxiliar de Inteligencia (%s)", key)

    def _refresh(self):
        started = time.perf_counter()
        try:
            payload = self.builder()
            snapshot = json.loads(json.dumps(payload, ensure_ascii=False, default=str))
            computed_at = datetime.now(timezone.utc).isoformat()
            snapshot["status"] = "ready"
            snapshot["computed_at"] = computed_at
            timings = snapshot.setdefault("timings_ms", {})
            timings["snapshot_total"] = round((time.perf_counter() - started) * 1000, 2)
            encoded = json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"))
            encoded_bytes = len(encoded.encode("utf-8"))
            if encoded_bytes > INTELLIGENCE_SNAPSHOT_MAX_BYTES:
                raise ValueError(f"La instantánea supera el límite de {INTELLIGENCE_SNAPSHOT_MAX_BYTES} bytes")
            stamp = datetime.now(timezone.utc)
            with self.db.engine.begin() as conn:
                exists = conn.execute(select(self.db.intelligence_snapshots.c.id).where(
                    self.db.intelligence_snapshots.c.id == 1)).first()
                values = {"computed_at": stamp, "payload_json": encoded}
                if exists:
                    conn.execute(self.db.intelligence_snapshots.update().where(
                        self.db.intelligence_snapshots.c.id == 1).values(**values))
                else:
                    conn.execute(self.db.intelligence_snapshots.insert().values(id=1, **values))
            # Una sola referencia viva; al reemplazarla la anterior queda liberable.
            self._snapshot = snapshot
            self._last_error = None
            logger.warning("INTELLIGENCE_SNAPSHOT_REFRESH status=ready duration_s=%.3f bytes=%d",
                (time.perf_counter() - started), encoded_bytes)
        except Exception as exc:
            self._last_error = f"{type(exc).__name__}: {exc}"
            logger.exception("Falló el precálculo de la instantánea de Inteligencia")
        finally:
            with self._lock:
                self._calculating = False
                self._ready.set()

    def wait(self, timeout=None):
        return self._ready.wait(timeout)

    def shutdown(self):
        # El trabajador es daemon y no bloquea el cierre del servidor.
        self._ready.wait(1.0)

    def status(self):
        snapshot = self._snapshot or {}
        return {"status": "calculando" if self.calculating else snapshot.get("status", "sin_datos"),
            "computed_at": snapshot.get("computed_at"), "snapshot_bytes": len(json.dumps(
                snapshot, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) if snapshot else 0,
            "last_error": self._last_error}
