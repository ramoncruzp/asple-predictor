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
from config.settings import GRID_CELL_FUNCTIONAL_MULT

MAX_LEVELS = 60
MIN_LEVELS = 4
MIN_TYPICAL_CELL_USDT = Decimal("5")
DUST_TARGET_PCT = Decimal("0.1")


def _dec(value: Any) -> Decimal:
    try:
        return value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"invalid decimal value: {value!r}") from exc


def minimum_cell_threshold(filters: SymbolFilters, configured_floor: Decimal | str | float = MIN_TYPICAL_CELL_USDT) -> Decimal:
    return max(filters.min_notional, MIN_TYPICAL_CELL_USDT, _dec(configured_floor))


def functional_cell_threshold(filters: SymbolFilters, strategy: str,
                              configured_floor: Decimal | str | float = Decimal("5.5")) -> Decimal:
    base = minimum_cell_threshold(filters, configured_floor)
    if str(strategy).lower() != "smart":
        return base
    return max(base, filters.min_notional * _dec(GRID_CELL_FUNCTIONAL_MULT))


def functional_cell_warning(capital: Decimal | str | float, levels: int | None,
                            minimum_cell: Decimal | str | float, strategy: str) -> str | None:
    if str(strategy).lower() != "smart" or levels is None or levels < 1:
        return None
    if _dec(capital) / Decimal(levels) < _dec(minimum_cell):
        return "Con este capital por celda el grid no podrá prestar ni añadir niveles; solo podrá reducirlos. Está bajo el mínimo funcional."
    return None


def minimum_cell_warning(capital: Decimal | str | float, levels: int | None,
                         minimum_cell: Decimal | str | float) -> str | None:
    if levels is None or levels < 1:
        return None
    if _dec(capital) / Decimal(levels) < _dec(minimum_cell):
        return f"Mínimo de celda aplicado: {_dec(minimum_cell):g} USDT (5 USDT típico; no verificado en Testnet)."
    return None


def minimum_cell_for_dust_limit(filters: SymbolFilters, price: Decimal | str | float,
                                target_pct: Decimal | str | float = DUST_TARGET_PCT) -> Decimal:
    target_ratio = _dec(target_pct) / Decimal(100)
    if target_ratio <= 0:
        raise ValueError("target_pct must be positive")
    return filters.step_size * _dec(price) / target_ratio


def evaluate_cell_margins(cells: list[Any], filters: SymbolFilters,
                          fee_pct: Decimal | str | float = Decimal("0.1")) -> tuple[list[dict], dict]:
    """Return per-cell fee/dust estimates and a capital-weighted aggregate."""
    fee = _dec(fee_pct) / Decimal(100)
    rows = []
    total_capital = Decimal(0)
    total_gross = Decimal(0)
    total_net = Decimal(0)
    for cell in cells:
        get = cell.get if isinstance(cell, dict) else lambda key: getattr(cell, key)
        buy, sell = _dec(get("buy_price")), _dec(get("sell_price"))
        capital = _dec(get("capital"))
        gross_pct = (sell / buy - Decimal(1)) * Decimal(100) - fee * Decimal(200)
        dust_pct = filters.step_size * buy / capital * Decimal(100)
        net_pct = gross_pct - dust_pct
        gross_usdt = capital * gross_pct / Decimal(100)
        net_usdt = capital * net_pct / Decimal(100)
        row = dict(cell) if isinstance(cell, dict) else {
            "level_idx": cell.level_idx, "buy_price": str(buy), "sell_price": str(sell),
            "capital": str(capital), "quantity": str(cell.qty), "initial_state": cell.initial_state,
        }
        row.update({"gross_margin_pct": str(gross_pct), "gross_margin_usdt": str(gross_usdt),
                    "dust_estimate_pct": str(dust_pct), "net_margin_pct": str(net_pct),
                    "net_margin_usdt": str(net_usdt)})
        rows.append(row)
        total_capital += capital
        total_gross += gross_usdt
        total_net += net_usdt
    return rows, {"capital_usdt": str(total_capital), "gross_margin_usdt": str(total_gross),
                  "net_margin_usdt": str(total_net),
                  "net_margin_pct": str(total_net / total_capital * Decimal(100)) if total_capital else "0"}


def evaluate_preview_position(mid: Decimal | str | float, low: Decimal | str | float,
                              high: Decimal | str | float) -> dict:
    mid_d, low_d, high_d = _dec(mid), _dec(low), _dec(high)
    if mid_d <= 0 or low_d >= high_d:
        raise ValueError("invalid preview position")
    return {"price_in_range": low_d <= mid_d <= high_d,
            "distance_to_floor_pct": (mid_d - low_d) / mid_d * Decimal(100),
            "distance_to_ceiling_pct": (high_d - mid_d) / mid_d * Decimal(100)}


def evaluate_levels(
    n: int,
    width_pct: float,
    capital: Decimal | str | float,
    mid: Decimal | str | float,
    filters: SymbolFilters,
    fee_pct: float,
    min_spacing_pct: float,
    min_cell_usdt: Decimal | str | float,
) -> dict:
    """Evaluate one level count using the same fee, spacing, cell and dust math."""
    capital_d, mid_d = _dec(capital), _dec(mid)
    min_cell_d = _dec(min_cell_usdt)
    if min_cell_d < filters.min_notional:
        min_cell_d = filters.min_notional
    step_dust_usdt = float(filters.step_size) * float(mid_d)
    spacing_pct = width_pct / n
    cell_usdt = capital_d / Decimal(n)
    dust_pct = (step_dust_usdt / float(cell_usdt) * 100.0) if cell_usdt > 0 else float("inf")
    required_spacing = max(min_spacing_pct, 2.0 * fee_pct + dust_pct)
    net_edge_pct = spacing_pct - 2.0 * fee_pct - dust_pct
    return {
        "n": n, "spacing_pct": spacing_pct, "cell_usdt": cell_usdt,
        "dust_pct": dust_pct, "required_spacing": required_spacing,
        "edge_gross_pct": spacing_pct - 2.0 * fee_pct,
        "net_edge_pct": net_edge_pct,
        "spacing_ok": spacing_pct >= required_spacing,
        "cell_ok": cell_usdt >= min_cell_d,
    }


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
    min_margin_after_fees_pct: float = 0.7,
) -> dict:
    """Propose a centered range/level structure from a forecast sigma_24h.

    Width = ``k_width`` log-sigma at the requested horizon (``sigma_h = sigma_24h *
    sqrt(horizon_h/24)``, matching ``grid.policy``'s scaling convention). The level
    count is the largest value that keeps per-level spacing at or above
    ``min_spacing_pct``, gross edge after fees at or above
    ``min_margin_after_fees_pct``, and cell size above its exchange minimum.
    Estimated dust is reported as information only.
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
    if min_margin_after_fees_pct < 0:
        raise ValueError("min_margin_after_fees_pct must not be negative")
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

    chosen = None
    for n in range(n_raw, MIN_LEVELS - 1, -1):
        evaluation = evaluate_levels(n, width_pct, capital_d, mid_d, filters,
                                     fee_pct, min_spacing_pct, min_cell_d)
        if (evaluation["cell_ok"]
                and evaluation["spacing_pct"] >= min_spacing_pct
                and evaluation["edge_gross_pct"] >= min_margin_after_fees_pct):
            chosen = evaluation
            break
    if chosen is None:
        floor_eval = evaluate_levels(MIN_LEVELS, width_pct, capital_d, mid_d, filters,
                                     fee_pct, min_spacing_pct, min_cell_d)
        reasons.append(
            f"infeasible: incluso con n_levels={MIN_LEVELS} el espaciado "
            f"({floor_eval['spacing_pct']:.4f}%), margen tras comisiones "
            f"({floor_eval['edge_gross_pct']:.4f}%) o celda "
            f"(${floor_eval['cell_usdt']:.4f}) no cumplen los minimos "
            f"(spacing>={min_spacing_pct:.4f}%, margen>={min_margin_after_fees_pct:.4f}%, "
            f"cell>=${min_cell_d}); el polvo estimado es informativo"
        )
        return {
            "range_low": None, "range_high": None, "n_levels": None,
            "spacing_pct": None, "cell_usdt": None, "net_edge_pct_per_cycle": None,
            "reasons": reasons, "feasible": False,
        }

    reasons.append(
        f"n_levels elegido={chosen['n']}: spacing={chosen['spacing_pct']:.4f}% "
        f"(minimo de espaciado={min_spacing_pct:.4f}%, margen tras comisiones "
        f"{chosen['edge_gross_pct']:.4f}% >= {min_margin_after_fees_pct:.4f}%)"
    )
    reasons.append(
        f"net_edge_pct_per_cycle={chosen['net_edge_pct']:.4f}% "
        f"(informativo; polvo estimado={chosen['dust_pct']:.4f}% por step_size={filters.step_size}); "
        f"cell_usdt=${chosen['cell_usdt']:.4f} >= minimo ${min_cell_d}"
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
        "edge_gross_pct": float(chosen["edge_gross_pct"]),
        "dust_estimate_pct": float(chosen["dust_pct"]),
        "net_edge_pct_per_cycle": float(chosen["net_edge_pct"]), "reasons": reasons,
        "feasible": True,
    }
