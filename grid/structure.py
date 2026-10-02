"""Pure function that proposes a grid range/level structure from a sigma forecast.

No direction is predicted here; the structure is always centered on ``mid``. This
module is intentionally standalone: it is not wired into ``grid.engine`` or
``grid.policy`` (see Phase 15E scope notes). It only consumes a sigma_24h value and
exchange filters to produce an explainable, Decimal-safe range/level suggestion.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from math import exp, floor, isfinite, sqrt
from typing import Any

from data.exchange_filters import SymbolFilters

MAX_LEVELS = 60
MIN_LEVELS = 4


def _dec(value: Any) -> Decimal:
    try:
        return value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"invalid decimal value: {value!r}") from exc


def suggest_structure(
    sigma_24h: float,
    capital: Decimal | str | float,
    mid: Decimal | str | float,
    filters: SymbolFilters,
    fee_pct: float,
    *,
    horizon_h: float = 24.0,
    k_width: float = 2.0,
    min_spacing_pct: float = 0.8,
    min_cell_usdt: Decimal | str | float | None = None,
) -> dict:
    """Propose a centered range/level structure from a forecast sigma_24h.

    Width = ``k_width`` log-sigma at the requested horizon (``sigma_h = sigma_24h *
    sqrt(horizon_h/24)``, matching ``grid.policy``'s scaling convention). The level
    count is the largest value that keeps per-level spacing at or above both
    ``min_spacing_pct`` and ``2*fee_pct + dust_margin_pct`` (the expected dust cost
    from rounding to ``filters.step_size``, expressed as a percentage of the cell).
    Pure function: no I/O, no randomness, no mutation of inputs.
    """
    reasons: list[str] = []
    capital_d = _dec(capital)
    mid_d = _dec(mid)
    if capital_d <= 0:
        raise ValueError("capital must be positive")
    if mid_d <= 0:
        raise ValueError("mid must be positive")
    if fee_pct < 0:
        raise ValueError("fee_pct must not be negative")
    if min_spacing_pct <= 0:
        raise ValueError("min_spacing_pct must be positive")
    if horizon_h <= 0 or k_width <= 0:
        raise ValueError("horizon_h and k_width must be positive")

    min_cell_d = _dec(min_cell_usdt) if min_cell_usdt is not None else filters.min_notional
    if min_cell_d < filters.min_notional:
        min_cell_d = filters.min_notional

    if sigma_24h is None or not isfinite(float(sigma_24h)) or float(sigma_24h) <= 0:
        return {
            "range_low": None, "range_high": None, "n_levels": None,
            "spacing_pct": None, "cell_usdt": None, "net_edge_pct_per_cycle": None,
            "reasons": ["sigma_24h no disponible o no positivo; no se puede proponer estructura"],
            "feasible": False,
        }

    sigma_h = float(sigma_24h) * sqrt(horizon_h / 24.0)
    half_width_log = k_width * sigma_h
    reasons.append(
        f"sigma_h={sigma_h:.6f} (sigma_24h={float(sigma_24h):.6f}, horizon_h={horizon_h}); "
        f"ancho = k_width({k_width}) * sigma_h, mitad en escala log = {half_width_log:.6f}"
    )
    range_low_raw = float(mid_d) * exp(-half_width_log)
    range_high_raw = float(mid_d) * exp(half_width_log)
    width_pct = (range_high_raw - range_low_raw) / float(mid_d) * 100.0

    n_raw = max(MIN_LEVELS, min(MAX_LEVELS, int(floor(width_pct / min_spacing_pct))))
    reasons.append(f"ancho_total={width_pct:.4f}%; n_raw=floor(ancho/min_spacing_pct)={n_raw}")

    step_dust_usdt = float(filters.step_size) * float(mid_d)

    def _evaluate(n: int) -> dict:
        spacing_pct = width_pct / n
        cell_usdt = capital_d / Decimal(n)
        dust_pct = (step_dust_usdt / float(cell_usdt) * 100.0) if cell_usdt > 0 else float("inf")
        required_spacing = max(min_spacing_pct, 2.0 * fee_pct + dust_pct)
        net_edge_pct = spacing_pct - 2.0 * fee_pct - dust_pct
        return {
            "n": n, "spacing_pct": spacing_pct, "cell_usdt": cell_usdt,
            "dust_pct": dust_pct, "required_spacing": required_spacing,
            "net_edge_pct": net_edge_pct,
            "spacing_ok": spacing_pct >= required_spacing,
            "cell_ok": cell_usdt >= min_cell_d,
        }

    chosen = None
    for n in range(n_raw, MIN_LEVELS - 1, -1):
        evaluation = _evaluate(n)
        if evaluation["spacing_ok"] and evaluation["cell_ok"]:
            chosen = evaluation
            break
    if chosen is None:
        floor_eval = _evaluate(MIN_LEVELS)
        reasons.append(
            f"infeasible: incluso con n_levels={MIN_LEVELS} el espaciado "
            f"({floor_eval['spacing_pct']:.4f}%) o la celda "
            f"(${floor_eval['cell_usdt']:.4f}) no cumplen el minimo requerido "
            f"(spacing>={floor_eval['required_spacing']:.4f}%, cell>=${min_cell_d})"
        )
        return {
            "range_low": None, "range_high": None, "n_levels": None,
            "spacing_pct": None, "cell_usdt": None, "net_edge_pct_per_cycle": None,
            "reasons": reasons, "feasible": False,
        }

    reasons.append(
        f"n_levels elegido={chosen['n']}: spacing={chosen['spacing_pct']:.4f}% "
        f"(minimo requerido {chosen['required_spacing']:.4f}%, incluye fee*2={2.0*fee_pct:.4f}% "
        f"y margen de polvo={chosen['dust_pct']:.4f}% por step_size={filters.step_size})"
    )
    reasons.append(
        f"net_edge_pct_per_cycle={chosen['net_edge_pct']:.4f}% "
        f"(spacing - 2*fee - polvo); cell_usdt=${chosen['cell_usdt']:.4f} >= minimo ${min_cell_d}"
    )
    range_low = filters.round_price(Decimal(str(range_low_raw)), "down")
    range_high = filters.round_price(Decimal(str(range_high_raw)), "up")
    if range_low <= 0 or range_low >= range_high:
        reasons.append("infeasible: rango resultante invalido tras redondeo a tick_size")
        return {
            "range_low": None, "range_high": None, "n_levels": None,
            "spacing_pct": None, "cell_usdt": None, "net_edge_pct_per_cycle": None,
            "reasons": reasons, "feasible": False,
        }

    return {
        "range_low": range_low, "range_high": range_high, "n_levels": int(chosen["n"]),
        "spacing_pct": float(chosen["spacing_pct"]), "cell_usdt": chosen["cell_usdt"],
        "net_edge_pct_per_cycle": float(chosen["net_edge_pct"]), "reasons": reasons,
        "feasible": True,
    }
