"""Explainable, descriptive grid cost and feasibility scoring (no predictions)."""
from __future__ import annotations

from decimal import Decimal
from math import log10, sqrt
from typing import Any

import numpy as np

from config.settings import SCANNER_DEFAULTS
from grid.structure import suggest_structure


def _rows(value: Any) -> list[dict]:
    if hasattr(value, "to_dict"):
        return value.to_dict("records")
    return list(value or [])


def _num(row: dict, name: str) -> float:
    return float(row[name])


def _er(closes: list[float]) -> float:
    if len(closes) < 2 or closes[0] <= 0:
        return 0.0
    path = sum(abs(b-a) for a, b in zip(closes, closes[1:]))
    return min(1.0, abs(closes[-1] - closes[0]) / path) if path else 0.0


def score_symbol(market: dict, filters: Any, params: dict | None = None) -> dict:
    """Score one market using explicit hard gates and descriptive history only."""
    cfg = {**SCANNER_DEFAULTS, **(params or {})}
    symbol = str(market.get("symbol", "")).upper().replace("/", "")
    quote = str(market.get("quote_asset", "USDT")).upper()
    status = str(market.get("status", "")).upper()
    bid, ask = float(market.get("bid", 0)), float(market.get("ask", 0))
    mid = (bid + ask) / 2 if bid > 0 and ask > 0 else 0
    spread_bps = ((ask - bid) / mid * 10000) if mid else float("inf")
    k5, k1 = _rows(market.get("klines_5m")), _rows(market.get("klines_1h"))
    closes5 = [_num(row, "close") for row in k5]
    closes1 = [_num(row, "close") for row in k1]
    min_5m, min_1h = int(cfg["history_days"] * 24 * 12 * .95), int(cfg["history_days"] * 24 * .95)
    history_ok = len(closes5) >= min_5m and len(closes1) >= min_1h
    fee = float(cfg["fee_pct"])
    cell_floor = max(Decimal(str(filters.min_notional)) * Decimal("1.1"),
                     Decimal(str(cfg.get("min_cell_floor_usdt", SCANNER_DEFAULTS["min_cell_floor_usdt"]))))
    sigma = float(np.std(np.diff(np.log(closes1)), ddof=1) * sqrt(24)) if len(closes1) > 2 and all(x > 0 for x in closes1) else 0.0
    structure = suggest_structure(
        sigma, Decimal(str(market.get("capital", 100))), Decimal(str(mid or 1)), filters, fee,
        min_spacing_pct=float(cfg["min_spacing_pct"]), min_cell_usdt=cell_floor,
    )
    edge = structure.get("net_edge_pct_per_cycle")
    structure_ok = bool(structure["feasible"] and edge is not None and float(edge) > 0)
    spread_ok = bid > 0 and ask >= bid and spread_bps <= float(cfg["max_spread_bps"])
    hard = [
        ("active_registry", bool(market.get("active", False)), market.get("active"), True,
         "El símbolo debe estar activo en Coin Registry."),
        ("quote_asset", quote == "USDT", quote, "USDT", "El par debe cotizar en USDT."),
        ("trading_status", status == "TRADING", status, "TRADING", "El estado del mercado debe ser TRADING."),
        ("volume_24h", float(market.get("volume_24h_quote", 0)) >= float(cfg["min_volume_24h"]),
         float(market.get("volume_24h_quote", 0)), float(cfg["min_volume_24h"]),
         "El volumen quote de 24 h debe alcanzar el umbral configurado."),
        ("spread", spread_ok, spread_bps,
         float(cfg["max_spread_bps"]), "El spread debe estar bajo el máximo configurado."),
        ("structure", structure_ok, structure, "spacing/celda factibles y net edge > 0",
         "La estructura debe respetar spacing, filtros, polvo y margen neto por ciclo."),
        ("history", history_ok, {"5m": len(closes5), "1h": len(closes1)},
         {"5m": min_5m, "1h": min_1h}, "Se requieren 30 días de historia utilizable en 5m y 1h."),
    ]
    hard_filters = [{"name": name, "passed": bool(ok), "measured": measured,
                     "threshold": threshold, "reason": reason if ok else f"No elegible: {reason}"}
                    for name, ok, measured, threshold, reason in hard]
    eligible = all(item["passed"] for item in hard_filters)
    components: list[dict] = []
    warnings: list[str] = []
    reasons = [item["reason"] for item in hard_filters if not item["passed"]]
    score = 0.0
    oscillation = {"crossings": 0, "time_in_range_pct": 0.0}
    er = _er(closes1[-720:])
    if eligible:
        low, high = float(structure["range_low"]), float(structure["range_high"])
        hist = closes5[-min(len(closes5), 30 * 24 * 12):]
        inside = [low <= value <= high for value in hist]
        levels = [low + (high - low) * idx / int(structure["n_levels"])
                  for idx in range(1, int(structure["n_levels"]))]
        crossings = sum(1 for previous, current in zip(hist, hist[1:])
                        for level in levels
                        if (previous < level <= current) or (current < level <= previous))
        oscillation = {"crossings": crossings,
                       "time_in_range_pct": 100 * sum(inside) / len(inside)}
        net = float(structure["net_edge_pct_per_cycle"])
        cost_value = min(1.0, max(0.0, net / max(2 * fee, 0.1)))
        spread_quality = max(0.0, min(1.0, 1 - spread_bps / float(cfg["max_spread_bps"])))
        volume_factor = min(1.0, max(0.0, log10(max(float(market["volume_24h_quote"]),
            float(cfg["min_volume_24h"])) / float(cfg["min_volume_24h"]))))
        liquidity_value = .75 * spread_quality + .25 * volume_factor
        oscillation_value = min(1.0, oscillation["crossings"] / 12) * min(1.0, oscillation["time_in_range_pct"] / 50)
        trend_value = max(0.0, 1 - er)
        weight_sum = sum(float(value) for value in cfg["weights"].values())
        if weight_sum <= 0:
            raise ValueError("at least one scanner score weight must be positive")
        vals = [("cost_headroom", cost_value, "Holgura descriptiva sobre comisiones y polvo.",
                 {"net_edge_pct_per_cycle": net, "fee_pct": fee}),
                ("liquidity", liquidity_value, "Escala de spread y volumen observados; Testnet puede diferir.",
                 {"spread_bps": spread_bps, "volume_24h_quote": float(market["volume_24h_quote"])}),
                ("historical_oscillation", oscillation_value, "Cruces históricos y tiempo dentro del rango sugerido.",
                 oscillation),
                ("trend_penalty", trend_value, "Penalización descriptiva basada en ER histórico 30 d.",
                 {"efficiency_ratio_30d": er})]
        for name, value, explanation, measured in vals:
            weight = float(cfg["weights"][name]) / weight_sum
            contribution = weight * value
            score += contribution
            components.append({"name": name, "value": round(value, 6), "weight": weight,
                               "contribution": round(contribution, 6), "measured": measured,
                               "explanation": explanation})
        if float(structure["net_edge_pct_per_cycle"]) < .1:
            warnings.append("Margen neto descriptivo bajo; revisar comisiones y polvo por ciclo.")
        reasons.append("Elegible por condiciones observadas de costo y factibilidad; score descriptivo, no predictivo.")
    return {"symbol": symbol, "eligible": eligible, "score": round(score, 6) if eligible else None,
            "hard_filters": hard_filters, "components": components, "reasons": reasons,
            "suggested_structure": structure, "historical_oscillation": oscillation,
            "efficiency_ratio_30d": er if eligible else None, "warnings": warnings}
