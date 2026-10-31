"""Selección explicable de alternativas para el motor v2, sin conexión al operador."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from grid.meta import DEFAULT_PROFILES


DEFAULT_P_LIMIT_CAP = {"PRUDENTE": 5.0, "RAPIDO": 15.0}


def objective_limits(perfil, *, settings_db=None):
    """Lee topes de perfil en SQLite de solo lectura y completa con defaults D2."""
    profile_name = str(perfil).upper()
    if profile_name not in DEFAULT_PROFILES:
        raise ValueError(f"perfil desconocido: {perfil}")
    defaults = {"max_loss_pct": float(DEFAULT_PROFILES[profile_name]["max_loss_pct"]),
        "p_limit_cap": DEFAULT_P_LIMIT_CAP[profile_name], "ev_floor_pct": 1.0,
        "cut_slippage_max_pct": 2.0}
    if settings_db is None:
        return defaults
    path = Path(settings_db).expanduser().resolve()
    if not path.is_file():
        return defaults
    try:
        with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as conn:
            present = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                "AND name='meta_profile_settings'").fetchone()
            if not present:
                return defaults
            row = conn.execute("SELECT settings FROM meta_profile_settings WHERE profile=?",
                (profile_name,)).fetchone()
        if not row or not row[0]:
            return defaults
        saved = json.loads(row[0])
        for key in defaults:
            if key in saved:
                defaults[key] = float(saved[key])
    except (OSError, sqlite3.Error, ValueError, TypeError, json.JSONDecodeError):
        return defaults
    return defaults


def objective_v2(metrics, *, perfil, capital_meta, max_loss_pct=None,
        p_limit_cap=None, ev_floor_pct=1.0, cut_slippage_max_pct=2.0):
    """Evalúa factibilidad; la cota superior del IC de pérdida se usa por seguridad."""
    profile_name = str(perfil).upper()
    if profile_name not in DEFAULT_PROFILES:
        raise ValueError(f"perfil desconocido: {perfil}")
    capital = float(capital_meta)
    if capital <= 0:
        raise ValueError("capital_meta debe ser positivo")
    profile = DEFAULT_PROFILES[profile_name]
    probability_cap = float(DEFAULT_P_LIMIT_CAP[profile_name] if p_limit_cap is None else p_limit_cap)
    mean_pnl = float(metrics["pnl_mean_usdt"])
    cut_slippage_mean = float(metrics.get("cut_slippage_mean_usdt", float("inf")))
    target_probability = float(metrics["p_target"])
    median_days = metrics.get("days_to_target_p50")
    loss_ci = metrics.get("p_loss_ci95")
    loss_upper = float(loss_ci[1]) if isinstance(loss_ci, (list, tuple)) and len(loss_ci) == 2 else None

    reasons = []
    if mean_pnl < -capital * float(ev_floor_pct) / 100:
        reasons.append(f"ganancia esperada menor que -{float(ev_floor_pct):g}% del capital")
    if loss_upper is None:
        reasons.append("falta el límite superior del IC 95 % de pérdida")
    elif loss_upper > probability_cap / 100:
        reasons.append(f"límite superior de P(pérdida) supera {probability_cap:g}%")
    if cut_slippage_mean > capital * float(cut_slippage_max_pct) / 100:
        reasons.append(f"desliz medio del corte supera {float(cut_slippage_max_pct):g}% del capital")

    return {"factible": not reasons, "motivos": reasons,
        "clave_orden": (-target_probability,
            float("inf") if median_days is None else float(median_days), -mean_pnl)}


def rank_alternatives_v2(results, *, perfil, capital_meta, max_loss_pct=None,
        p_limit_cap=None, ev_floor_pct=1.0, cut_slippage_max_pct=2.0):
    """Ordena primero factibles y, si no hay ninguna, devuelve USDT quieto."""
    ranked = []
    for result in results:
        metrics = result.get("metrics") or {}
        objective = objective_v2(metrics, perfil=perfil, capital_meta=capital_meta,
            max_loss_pct=max_loss_pct, p_limit_cap=p_limit_cap, ev_floor_pct=ev_floor_pct,
            cut_slippage_max_pct=cut_slippage_max_pct)
        ranked.append({**result, "objetivo": objective})
    feasible = sorted((row for row in ranked if row["objetivo"]["factible"]),
        key=lambda row: row["objetivo"]["clave_orden"])
    infeasible = sorted((row for row in ranked if not row["objetivo"]["factible"]),
        key=lambda row: row["objetivo"]["clave_orden"])
    if feasible:
        return feasible + infeasible
    quiet = {"piece": {"strategy": "usdt_quieto", "label": "USDT quieto"},
        "metrics": {"pnl_mean_usdt": 0.0, "p_target": 0.0, "p_loss": 0.0,
            "p_loss_ci95": [0.0, 0.0], "cvar_5_usdt": 0.0,
            "cut_slippage_mean_usdt": 0.0, "cut_slippage_p90_usdt": 0.0,
            "days_to_target_p50": None},
        "objetivo": {"factible": True, "motivos": [],
            "clave_orden": (0.0, float("inf"), 0.0)}}
    return [quiet] + infeasible
