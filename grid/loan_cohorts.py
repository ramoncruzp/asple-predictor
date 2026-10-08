"""Shared Smart-grid loan cohort assignment and in-process creation lock."""

from __future__ import annotations

from threading import Lock
from typing import Callable


LOAN_COHORT_LOCK = Lock()
_GRID_STATUSES = {"OPENING", "ACTIVE", "PAUSED", "CLOSING", "HOLDING",
                  "CLOSED", "CANCELLED", "FAILED", "ERROR"}


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
    group = "control" if interval > 0 and (assigned + 1) % interval == 0 else "loans"
    effective["loans_group"] = group
    effective["loans_enabled"] = group == "loans"
    if group == "loans":
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
    """Serialize cohort counting and Smart-grid creation within this process.

    The lock does not coordinate separate worker or CLI processes.
    """
    if str(strategy).lower() != "smart":
        effective = dict(params)
        return effective, create(effective)
    with LOAN_COHORT_LOCK:
        effective = assign_loan_creation_defaults(
            db, strategy, params, control_every_n, explicit_params=explicit_params)
        result = create(effective)
        return effective, result
