"""Read-only grid structure variants using the scanner's public market source."""
from __future__ import annotations

from decimal import Decimal
from math import ceil, exp, isfinite, sqrt
from typing import Literal

import numpy as np
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator

from api.routes.grids import _authorize
from grid.levels import compute_lines
from grid.scan_service import DATA_SOURCE, EXECUTION_WARNING
from grid.structure import (DUST_TARGET_PCT, MAX_LEVELS, MIN_LEVELS, evaluate_levels,
                            minimum_cell_for_dust_limit, minimum_cell_threshold,
                            minimum_cell_warning)

router = APIRouter()
DEFAULT_WIDE_K_WIDTH = 3.0


class StructurePreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbol: str = Field(min_length=5, max_length=20)
    capital: Decimal = Field(gt=0)
    strategy: Literal["simple", "smart"] = "simple"
    k_width: float | None = Field(default=None, gt=0)
    range_low: Decimal | None = Field(default=None, gt=0)
    range_high: Decimal | None = Field(default=None, gt=0)
    n_levels: int | None = Field(default=None, ge=MIN_LEVELS, le=MAX_LEVELS)
    spacing_pct: float | None = Field(default=None, gt=0, le=100)
    target_pct: Decimal | None = Field(default=None, gt=0, le=100)
    target_usdt: Decimal | None = Field(default=None, gt=0)
    margin_target_pct: float | None = Field(default=None, gt=0, le=10)

    @model_validator(mode="after")
    def valid_custom_structure(self):
        supplied = (self.range_low is not None, self.range_high is not None)
        if any(supplied) and not all(supplied):
            raise ValueError("range_low y range_high deben enviarse juntos")
        if all(supplied) and self.n_levels is None and self.spacing_pct is None:
            raise ValueError("proporciona n_levels o spacing_pct junto con el rango")
        if self.spacing_pct is not None and self.n_levels is not None:
            raise ValueError("envía n_levels o spacing_pct para mantener una sola fuente")
        if self.range_low is not None and self.range_low >= self.range_high:
            raise ValueError("range_low debe ser menor que range_high")
        if self.target_pct is not None and self.target_usdt is not None:
            raise ValueError("envía target_pct o target_usdt, no ambos")
        return self


def _target_amount(body: StructurePreviewRequest) -> Decimal | None:
    if body.target_usdt is not None:
        return body.target_usdt
    if body.target_pct is not None:
        return body.capital * body.target_pct / Decimal(100)
    return None


def _metrics(name: str, low, high, evaluation: dict, fee: float, feasible: bool,
             reasons: list[str], target_amount: Decimal | None, **extra) -> dict:
    spacing = evaluation.get("spacing_pct")
    edge_gross = None if spacing is None else spacing - 2.0 * fee
    edge_after_dust = evaluation["net_edge_pct"]
    dust_pct = evaluation.get("dust_pct")
    dust_warning = (edge_gross is not None and dust_pct is not None
                    and dust_pct > edge_gross * .5)
    cell = evaluation["cell_usdt"]
    per_cycle = None if cell is None or edge_after_dust is None else cell * Decimal(str(edge_after_dust)) / Decimal(100)
    cycles = None
    if target_amount is not None and per_cycle is not None and per_cycle > 0:
        cycles = int(ceil(target_amount / per_cycle))
    return {
        "name": name,
        "range_low": str(low) if low is not None else None,
        "range_high": str(high) if high is not None else None,
        "n_levels": int(evaluation["n"]) if evaluation.get("n") is not None else None,
        "spacing_pct": float(spacing) if spacing is not None else None,
        "cell_usdt": str(cell) if cell is not None else None,
        "edge_gross_pct": float(edge_gross) if edge_gross is not None and isfinite(edge_gross) else None,
        "dust_estimate_pct": float(dust_pct) if dust_pct is not None and isfinite(dust_pct) else None,
        "edge_after_dust_pct": float(edge_after_dust) if edge_after_dust is not None and isfinite(edge_after_dust) else None,
        "dust_estimate_label": "estimación conservadora, no medida",
        "dust_warning": dust_warning,
        "dust_warning_message": ("El polvo estimado es alto para esta celda; sube el capital por celda o reduce niveles. "
            "Es un tope pesimista, a\u00fan no medido en Testnet." if dust_warning else None),
        "feasible": bool(feasible),
        "reasons": list(reasons),
        "cycles_to_target": None if target_amount is None else {
            "target_usdt": str(target_amount),
            "estimated_net_per_cell_cycle_usdt": str(per_cycle) if per_cycle is not None else None,
            "cycles": cycles,
            "note": "cálculo teórico; no es una predicción",
        },
        **extra,
    }


def _raw_width_pct(sigma: float, mid: Decimal, k_width: float) -> float:
    half_width = k_width * sigma
    low, high = float(mid) * exp(-half_width), float(mid) * exp(half_width)
    return (high - low) / float(mid) * 100.0


def _make_variant(name: str, width_pct: float, low, high, levels: int | None,
                  body: StructurePreviewRequest, mid: Decimal, filters, fee: float,
                  min_spacing: float, min_cell: Decimal, min_margin: float,
                  target_amount: Decimal | None, strategy_result: dict | None = None,
                  ignore_dust: bool = False,
                  reason: str | None = None) -> dict:
    if levels is None:
        evaluation = {"n": None, "spacing_pct": None, "cell_usdt": None,
                      "dust_pct": None, "net_edge_pct": None}
        return _metrics(name, low, high, evaluation, fee, False,
                        [reason or "No hay una cantidad de niveles que cumpla las restricciones."],
                        target_amount, ignores_dust=ignore_dust)
    evaluation = evaluate_levels(levels, width_pct, body.capital, mid, filters,
                                 fee, min_spacing, min_cell)
    reasons = []
    gross_edge = evaluation["edge_gross_pct"]
    if evaluation["cell_ok"]:
        reasons.append("La celda cumple el mínimo configurado y los filtros del símbolo.")
    else:
        reasons.append(f"Celda inferior al mínimo requerido de {min_cell} USDT.")
    gross_ok = gross_edge >= min_margin
    feasible = bool(evaluation["cell_ok"] and gross_ok)
    if not gross_ok:
        reasons.append(f"Margen tras comisiones {gross_edge:.3f} % (mínimo {min_margin:.3f} %).")
    if strategy_result:
        reasons.extend(strategy_result.get("reasons", []))
    try:
        compute_lines(Decimal(str(low)), Decimal(str(high)), levels, filters)
    except Exception:
        feasible = False
        reasons.append("El rango o los niveles no cumplen los filtros de precio del símbolo.")
    return _metrics(name, low, high, evaluation, fee, feasible, reasons, target_amount,
                    ignores_dust=ignore_dust)


def _custom_variant(body, mid, filters, fee, min_spacing, min_cell, min_margin,
                    margin_target_pct, target_amount):
    width_pct = (float(body.range_high - body.range_low) / float(mid)) * 100.0
    levels = body.n_levels
    if levels is None and body.spacing_pct is not None:
        levels = max(MIN_LEVELS, min(MAX_LEVELS, int(round(width_pct / body.spacing_pct))))
    result = _make_variant("edited", width_pct, body.range_low, body.range_high,
                           levels, body, mid, filters, fee, min_spacing, min_cell, min_margin,
                           target_amount, ignore_dust=False)
    result["margin_target_pct"] = margin_target_pct
    result["margin_target_met"] = (result.get("edge_gross_pct") is not None
                                   and result["edge_gross_pct"] >= margin_target_pct)
    if not result["margin_target_met"] and result.get("edge_gross_pct") is not None:
        result["feasible"] = False
        result["reasons"].append(f"El margen tras comisiones {result.get('edge_gross_pct'):.3f} % no alcanza el m\u00ednimo de {margin_target_pct:.3f} %.")
    return result


@router.post("/structure-preview")
def structure_preview(request: Request, body: StructurePreviewRequest):
    _authorize(request)
    symbol = body.symbol.strip().upper().replace("/", "")
    if not symbol.endswith("USDT") or len(symbol) <= 4:
        raise HTTPException(422, "symbol debe ser un par USDT válido.")
    coin = request.app.state.db.get_coin(symbol)
    if not coin or int(coin.get("active", 0)) != 1:
        raise HTTPException(422, "El símbolo debe estar activo en Coin Registry.")
    service = request.app.state.grid_scan_service
    settings = request.app.state.settings
    try:
        market, filters = service._market(
            symbol, float(body.capital), service.clock() + float(settings.scanner_timeout_seconds))
    except Exception:
        raise HTTPException(503, "No se pudieron obtener datos públicos de mercado para este símbolo.") from None
    try:
        bid, ask = Decimal(str(market["bid"])), Decimal(str(market["ask"]))
        mid = (bid + ask) / Decimal(2)
        closes = market["klines_1h"]["close"].astype(float).tolist()
        sigma = float(np.std(np.diff(np.log(closes)), ddof=1) * sqrt(24)) \
            if len(closes) > 2 and all(value > 0 for value in closes) else 0.0
        fee = float(settings.scanner_fee_pct)
        min_spacing = float(settings.scanner_min_spacing_pct)
        min_margin = float(getattr(settings, "grid_min_margin_after_fees_pct",
                                   getattr(settings, "grid_min_net_margin_pct", .7)))
        min_cell = minimum_cell_threshold(filters, max(
            filters.min_notional * Decimal("1.1"),
            Decimal(str(settings.scanner_min_cell_floor_usdt))))
        k_balanced = body.k_width or 2.0
        target_amount = _target_amount(body)
        balanced_width = _raw_width_pct(sigma, mid, k_balanced) if sigma > 0 else 0.0
        balanced_low = filters.round_price(Decimal(str(float(mid) * exp(-k_balanced * sigma))), "down") if sigma > 0 else None
        balanced_high = filters.round_price(Decimal(str(float(mid) * exp(k_balanced * sigma))), "up") if sigma > 0 else None
        balanced_levels = None
        if balanced_low is not None and balanced_high is not None and balanced_low < balanced_high:
            max_levels = min(MAX_LEVELS, int(body.capital // min_cell))
            for count in range(max_levels, MIN_LEVELS - 1, -1):
                candidate = evaluate_levels(count, balanced_width, body.capital, mid, filters,
                                            fee, min_spacing, min_cell)
                if candidate["cell_ok"] and candidate["edge_gross_pct"] >= min_margin:
                    balanced_levels = count
                    break
        balanced_variant = _make_variant("balanced", balanced_width,
            balanced_low, balanced_high, balanced_levels, body, mid, filters, fee,
            min_spacing, min_cell, min_margin, target_amount,
            reason="No hay estructura balanceada que cumpla margen tras comisiones y mínimo de celda.")

        dense_levels = None
        dense_limit = min(MAX_LEVELS, int(body.capital // min_cell))
        for count in range(dense_limit, MIN_LEVELS - 1, -1):
            candidate = evaluate_levels(count, balanced_width, body.capital, mid, filters,
                                        fee, min_spacing, min_cell)
            if candidate["cell_ok"] and candidate["edge_gross_pct"] >= min_margin:
                dense_levels = count
                break
        if balanced_low is None or dense_levels is None:
            dense_variant = _make_variant("dense", balanced_width,
                balanced_low, balanced_high, None, body, mid,
                filters, fee, min_spacing, min_cell, min_margin, target_amount, ignore_dust=True,
                reason="No hay nivel máximo que cumpla margen tras comisiones y mínimo de celda.")
        else:
            dense_variant = _make_variant("dense", balanced_width,
                balanced_low, balanced_high, dense_levels, body, mid,
                filters, fee, min_spacing, min_cell, min_margin, target_amount, ignore_dust=True)

        wide_k = max(DEFAULT_WIDE_K_WIDTH, k_balanced + 1.0)
        wide_width = _raw_width_pct(sigma, mid, wide_k) if sigma > 0 else 0.0
        wide_low = filters.round_price(Decimal(str(float(mid) * exp(-wide_k * sigma))), "down") if sigma > 0 else None
        wide_high = filters.round_price(Decimal(str(float(mid) * exp(wide_k * sigma))), "up") if sigma > 0 else None
        wide_levels = None
        if wide_low is not None and wide_high is not None and wide_low < wide_high:
            for count in range(MIN_LEVELS, MAX_LEVELS + 1):
                candidate = evaluate_levels(count, wide_width, body.capital, mid, filters,
                                            fee, min_spacing, min_cell)
                if candidate["cell_ok"] and candidate["edge_gross_pct"] >= min_margin:
                    wide_levels = count
                    break
        wide_variant = _make_variant("wide", wide_width, wide_low,
            wide_high, wide_levels, body, mid, filters, fee,
            min_spacing, min_cell, min_margin, target_amount,
            reason="No hay nivel mínimo que cumpla margen tras comisiones y mínimo de celda.")
        edited = _custom_variant(body, mid, filters, fee, min_spacing, min_cell, min_margin,
            body.margin_target_pct if body.margin_target_pct is not None else min_margin, target_amount) \
            if body.range_low is not None else None
        variants = (balanced_variant, dense_variant, wide_variant)
        for variant in variants:
            variant["minimum_cell_usdt"] = str(min_cell)
            variant["min_cell_warning"] = minimum_cell_warning(body.capital, variant.get("n_levels"), min_cell)
            variant["dust_target_pct"] = float(DUST_TARGET_PCT)
            variant["dust_min_cell_usdt"] = str(minimum_cell_for_dust_limit(filters, mid))
        if edited is not None:
            edited["minimum_cell_usdt"] = str(min_cell)
            edited["min_cell_warning"] = minimum_cell_warning(body.capital, edited.get("n_levels"), min_cell)
            edited["dust_target_pct"] = float(DUST_TARGET_PCT)
            edited["dust_min_cell_usdt"] = str(minimum_cell_for_dust_limit(filters, mid))
    except Exception:
        raise HTTPException(422, "No se pudo calcular una estructura con los datos recibidos.") from None
    return {
        "symbol": symbol, "strategy": body.strategy, "capital": str(body.capital),
        "minimum_margin_after_fees_pct": min_margin,
        "mid": str(mid), "fee_pct": fee,
        "variants": {"balanced": balanced_variant, "wide": wide_variant},
        "edited": edited,
        "minimum_cell_usdt": str(min_cell),
        "min_cell_warning": minimum_cell_warning(body.capital,
            (edited or balanced_variant).get("n_levels"), min_cell),
        "dust_target_pct": float(DUST_TARGET_PCT),
        "dust_min_cell_usdt": str(minimum_cell_for_dust_limit(filters, mid)),
        "definitions": {
            "balanced": "rango k_width recibido o 2.0 por defecto; mayor n que cumple margen tras comisiones y mínimo de celda.",
            "wide": f"rango con k_width={wide_k:g}; menor n que cumple margen tras comisiones mínimo {min_margin:.1f}% y mínimo de celda.",
            "merged_variants": "dense se fusionó con balanced: en los datos actuales seleccionaban los mismos niveles y el mismo rango, por lo que se evita presentar dos nombres como opciones distintas.",
            "edge_gross_pct": "spacing_pct - 2*fee_pct",
            "dust_estimate_pct": "estimación conservadora de un step_size por celda respecto del capital por celda; no es medición real.",
            "edge_after_dust_pct": "edge_gross_pct - dust_estimate_pct",
            "min_gross_margin_pct": min_margin,
        },
        "data_source": DATA_SOURCE, "warning": EXECUTION_WARNING,
    }
