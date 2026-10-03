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
from grid.structure import MAX_LEVELS, MIN_LEVELS, evaluate_levels, suggest_structure

router = APIRouter()
MIN_GROSS_MARGIN_PCT = 0.1
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
    target_pct: Decimal | None = Field(default=None, gt=0, le=100)
    target_usdt: Decimal | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def valid_custom_structure(self):
        supplied = (self.range_low is not None, self.range_high is not None, self.n_levels is not None)
        if any(supplied) and not all(supplied):
            raise ValueError("range_low, range_high y n_levels deben enviarse juntos")
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
        "dust_estimate_pct": float(evaluation["dust_pct"]) if evaluation.get("dust_pct") is not None and isfinite(evaluation["dust_pct"]) else None,
        "edge_after_dust_pct": float(edge_after_dust) if edge_after_dust is not None and isfinite(edge_after_dust) else None,
        "dust_estimate_label": "estimación conservadora, no medida",
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
                  min_spacing: float, min_cell: Decimal, target_amount: Decimal | None,
                  strategy_result: dict | None = None, ignore_dust: bool = False,
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
    gross_edge = evaluation["spacing_pct"] - 2.0 * fee
    if evaluation["cell_ok"]:
        reasons.append("La celda cumple el mínimo configurado y los filtros del símbolo.")
    else:
        reasons.append(f"Celda inferior al mínimo requerido de {min_cell} USDT.")
    if ignore_dust:
        gross_ok = gross_edge >= MIN_GROSS_MARGIN_PCT
        reasons.append("Selección por margen bruto; ignora explícitamente el polvo estimado.")
        if not gross_ok:
            reasons.append(f"Margen bruto menor que el mínimo de {MIN_GROSS_MARGIN_PCT:.1f}%.")
        feasible = evaluation["cell_ok"] and gross_ok
    else:
        feasible = bool(evaluation["spacing_ok"] and evaluation["cell_ok"])
        reasons.append("La viabilidad incluye el coste estimado del polvo.")
        if not evaluation["spacing_ok"]:
            reasons.append("El espaciado no cubre comisiones, polvo estimado y espaciado mínimo.")
    if strategy_result:
        reasons.extend(strategy_result.get("reasons", []))
    try:
        compute_lines(Decimal(str(low)), Decimal(str(high)), levels, filters)
    except Exception:
        feasible = False
        reasons.append("El rango o los niveles no cumplen los filtros de precio del símbolo.")
    return _metrics(name, low, high, evaluation, fee, feasible, reasons, target_amount,
                    ignores_dust=ignore_dust)


def _custom_variant(body, mid, filters, fee, min_spacing, min_cell, target_amount):
    width_pct = (float(body.range_high - body.range_low) / float(mid)) * 100.0
    return _make_variant("edited", width_pct, body.range_low, body.range_high,
                         body.n_levels, body, mid, filters, fee, min_spacing, min_cell,
                         target_amount, ignore_dust=False)


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
        min_cell = max(filters.min_notional * Decimal("1.1"),
                       Decimal(str(settings.scanner_min_cell_floor_usdt)))
        k_balanced = body.k_width or 2.0
        balanced = suggest_structure(sigma, body.capital, mid, filters, fee,
            k_width=k_balanced, min_spacing_pct=min_spacing, min_cell_usdt=min_cell)
        target_amount = _target_amount(body)
        balanced_width = _raw_width_pct(sigma, mid, k_balanced) if sigma > 0 else 0.0
        balanced_variant = _make_variant("balanced", balanced_width,
            balanced.get("range_low"), balanced.get("range_high"), balanced.get("n_levels"),
            body, mid, filters, fee, min_spacing, min_cell, target_amount,
            strategy_result=balanced, reason="No hay estructura balanceada factible.")
        if not balanced.get("feasible"):
            balanced_variant["feasible"] = False

        dense_levels = None
        dense_limit = min(MAX_LEVELS, int(body.capital // min_cell))
        for count in range(dense_limit, MIN_LEVELS - 1, -1):
            candidate = evaluate_levels(count, balanced_width, body.capital, mid, filters,
                                        fee, min_spacing, min_cell)
            if candidate["cell_ok"] and candidate["spacing_pct"] - 2 * fee >= MIN_GROSS_MARGIN_PCT:
                dense_levels = count
                break
        if balanced.get("range_low") is None or dense_levels is None:
            dense_variant = _make_variant("dense", balanced_width,
                balanced.get("range_low"), balanced.get("range_high"), None, body, mid,
                filters, fee, min_spacing, min_cell, target_amount, ignore_dust=True,
                reason="No hay nivel máximo que cumpla margen bruto y mínimo de celda.")
        else:
            dense_variant = _make_variant("dense", balanced_width,
                balanced["range_low"], balanced["range_high"], dense_levels, body, mid,
                filters, fee, min_spacing, min_cell, target_amount, ignore_dust=True)

        wide_k = max(DEFAULT_WIDE_K_WIDTH, k_balanced + 1.0)
        wide_width = _raw_width_pct(sigma, mid, wide_k) if sigma > 0 else 0.0
        wide_low = filters.round_price(Decimal(str(float(mid) * exp(-wide_k * sigma))), "down") if sigma > 0 else None
        wide_high = filters.round_price(Decimal(str(float(mid) * exp(wide_k * sigma))), "up") if sigma > 0 else None
        wide_levels = None
        if wide_low is not None and wide_high is not None and wide_low < wide_high:
            for count in range(MIN_LEVELS, MAX_LEVELS + 1):
                candidate = evaluate_levels(count, wide_width, body.capital, mid, filters,
                                            fee, min_spacing, min_cell)
                if candidate["cell_ok"] and candidate["spacing_pct"] - 2 * fee >= MIN_GROSS_MARGIN_PCT:
                    wide_levels = count
                    break
        wide_variant = _make_variant("wide", wide_width, wide_low,
            wide_high, wide_levels, body, mid, filters, fee,
            min_spacing, min_cell, target_amount,
            reason="No hay nivel mínimo que cumpla margen bruto y mínimo de celda.")
        edited = _custom_variant(body, mid, filters, fee, min_spacing, min_cell, target_amount) \
            if body.range_low is not None else None
    except Exception:
        raise HTTPException(422, "No se pudo calcular una estructura con los datos recibidos.") from None
    return {
        "symbol": symbol, "strategy": body.strategy, "capital": str(body.capital),
        "mid": str(mid), "fee_pct": fee,
        "variants": {"dense": dense_variant, "balanced": balanced_variant, "wide": wide_variant},
        "edited": edited,
        "definitions": {
            "balanced": "suggest_structure con k_width recibido o 2.0 por defecto; incluye polvo estimado.",
            "wide": f"rango con k_width={wide_k:g}; menor n que cumple margen bruto mínimo {MIN_GROSS_MARGIN_PCT:.1f}% y mínimo de celda.",
            "dense": f"mismo rango balanced; máximo n con spacing >= 2*fee + {MIN_GROSS_MARGIN_PCT:.1f}% y mínimo de celda; ignora polvo al elegir n.",
            "edge_gross_pct": "spacing_pct - 2*fee_pct",
            "dust_estimate_pct": "estimación conservadora de un step_size por celda respecto del capital por celda; no es medición real.",
            "edge_after_dust_pct": "edge_gross_pct - dust_estimate_pct",
            "min_gross_margin_pct": MIN_GROSS_MARGIN_PCT,
        },
        "data_source": DATA_SOURCE, "warning": EXECUTION_WARNING,
    }
