"""Shared Smart-grid loan cohort assignment and in-process creation lock."""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
import logging
import os
from pathlib import Path
import tempfile
from threading import Lock
from time import monotonic, sleep
from typing import Callable


LOAN_COHORT_LOCK = Lock()
# debe ser mayor que el peor caso de una apertura (varias órdenes en Testnet).
_PROCESS_LOCK_TIMEOUT_SECONDS = 120.0
_PROCESS_LOCK_RETRY_SECONDS = 0.05
_LOGGER = logging.getLogger(__name__)
_GRID_STATUSES = {"OPENING", "ACTIVE", "PAUSED", "CLOSING", "HOLDING",
                  "CLOSED", "CANCELLED", "FAILED", "ERROR"}


def _lock_file_path(db) -> Path:
    engine = getattr(db, "engine", None)
    url = getattr(engine, "url", None)
    identity = str(url) if url is not None else "asple-grid-loan-cohort-default"
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]
    return Path(tempfile.gettempdir()) / f"asple-loan-cohort-{digest}.lock"


def _open_lock_file(path: Path):
    created = False
    try:
        descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
        created = True
    except FileExistsError:
        descriptor = os.open(path, os.O_RDWR)
    handle = os.fdopen(descriptor, "r+b", buffering=0)
    if created:
        handle.write(b"\0")
        handle.flush()
    else:
        deadline = monotonic() + 1.0
        while os.fstat(descriptor).st_size == 0 and monotonic() < deadline:
            sleep(0.001)
        if os.fstat(descriptor).st_size == 0:
            # Recover an orphaned empty file; only byte existence is needed for locking.
            handle.seek(0)
            handle.write(b"\0")
            handle.flush()
    return handle


def _try_os_lock(handle):
    if os.name == "nt":
        import msvcrt
        os.lseek(handle.fileno(), 0, os.SEEK_SET)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _release_os_lock(handle):
    if os.name == "nt":
        import msvcrt
        os.lseek(handle.fileno(), 0, os.SEEK_SET)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextmanager
def _process_creation_lock(db):
    try:
        handle = _open_lock_file(_lock_file_path(db))
    except OSError as exc:
        _LOGGER.warning("No se pudo abrir el candado de cohortes entre procesos; se usa el candado local: %s", exc)
        yield False
        return
    acquired = False
    deadline = monotonic() + _PROCESS_LOCK_TIMEOUT_SECONDS
    try:
        while not acquired:
            try:
                _try_os_lock(handle)
                acquired = True
            except OSError as exc:
                if monotonic() >= deadline:
                    raise TimeoutError("Tiempo agotado esperando el candado de cohortes entre procesos") from exc
                sleep(_PROCESS_LOCK_RETRY_SECONDS)
        yield True
    finally:
        if acquired:
            try:
                _release_os_lock(handle)
            finally:
                handle.close()
        else:
            handle.close()


def _all_grids(db):
    return [row for status in _GRID_STATUSES for row in db.list_grids_by_status({status})]


def assign_loan_creation_defaults(db, strategy: str, params: dict, control_every_n: int,
                                  *, explicit_params: dict | None = None) -> dict:
    """Apply loan cohort defaults to Smart grids, preserving explicit overrides."""
    effective = dict(params)
    if str(strategy).lower() != "smart":
        return effective
    explicit = params if explicit_params is None else explicit_params
    if "loans_enabled" in explicit:
        enabled = explicit["loans_enabled"]
        effective["loans_group"] = "manual"
        if "loan_lender_max_pct" not in explicit:
            if enabled is True:
                effective["loan_lender_max_pct"] = 70.0
            else:
                effective.pop("loan_lender_max_pct", None)
        return effective
    assigned = sum(1 for row in _all_grids(db)
                   if row.get("strategy") == "smart"
                   and isinstance(row.get("params"), dict)
                   and "loans_group" in row["params"])
    interval = int(control_every_n)
    group = "control" if interval > 0 and (assigned + 1) % interval == 0 else "loans_v2"
    effective["loans_group"] = group
    effective["loans_enabled"] = group == "loans_v2"
    if group == "loans_v2":
        effective["loan_topup_pct"] = 70.0
        if "loan_lender_max_pct" in explicit:
            effective["loan_lender_max_pct"] = explicit["loan_lender_max_pct"]
        else:
            effective["loan_lender_max_pct"] = 70.0
    else:
        effective.pop("loan_lender_max_pct", None)
    return effective


def create_grid_with_loan_cohort(db, strategy: str, params: dict, control_every_n: int,
                                 create: Callable[[dict], dict], *,
                                 explicit_params: dict | None = None):
    """Serialize counting and creation in this process and across processes sharing the DB URL.

    The temp-file lock does not coordinate other machines or a different DB URL.
    """
    if str(strategy).lower() != "smart":
        effective = dict(params)
        return effective, create(effective)
    with LOAN_COHORT_LOCK:
        with _process_creation_lock(db):
            effective = assign_loan_creation_defaults(
                db, strategy, params, control_every_n, explicit_params=explicit_params)
            result = create(effective)
            return effective, result


def create_loan_pair(db, create: Callable[[], dict]):
    """Run both pair-arm creations under one fail-closed process lock."""
    with LOAN_COHORT_LOCK:
        with _process_creation_lock(db) as acquired:
            if not acquired:
                raise RuntimeError("candado entre procesos no disponible; no se abre el par")
            return create()
