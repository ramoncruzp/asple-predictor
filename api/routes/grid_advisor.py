from __future__ import annotations
from decimal import Decimal
from math import ceil
from fastapi import APIRouter, Query, Request
import numpy as np
from config.models_config import ACTIVE_INTERVAL, ACTIVE_SYMBOL
from grid.structure import (MAX_LEVELS, MIN_LEVELS, evaluate_levels,
                            minimum_cell_threshold, minimum_cell_warning)
from grid.sim.runner import FILTERS as SIM_FILTERS, run_simulation
from grid.range_risk import estimate_range_risk
from datetime import datetime, timezone

RISK_LEVELS = {
    "low": {"label": "Conservador", "floor_atr": 1.0, "ceiling_atr": 0.25, "max_range_pct": 25.0,
            "meaning": "Piso cercano: menos capital inmovilizado lejos del precio; más probabilidad de salir del rango."},
    "medium": {"label": "Moderado", "floor_atr": 2.0, "ceiling_atr": 0.5, "max_range_pct": 45.0,
               "meaning": "Equilibrio entre amplitud del rango y capital inmovilizado."},
    "high": {"label": "Agresivo", "floor_atr": 3.0, "ceiling_atr": 0.75, "max_range_pct": 70.0,
             "meaning": "Piso profundo: cubre caídas mayores; inmoviliza más capital y aumenta la pérdida no realizada posible."},
}
router = APIRouter()

def _level(values, current):
    local = values.rolling(20, center=True).min() if values.name == "low" else values.rolling(20, center=True).max()
    is_support = values.name == "low"
    candidates = [(float(v), i) for i, (v, x) in enumerate(zip(values, local)) if np.isfinite(x) and v == x and ((v <= current) if is_support else (v >= current))]
    if not candidates:
        eligible = values[values <= current] if is_support else values[values >= current]
        return float(eligible.max() if is_support else eligible.min()), 0
    clusters = []
    for value, index in sorted(candidates):
        for cluster in clusters:
            if abs(value - cluster[0]) / cluster[0] <= 0.005:
                cluster[1].append((value, index)); break
        else: clusters.append([value, [(value, index)]])
    qualified = []
    for cluster in clusters:
        level = float(np.mean([v for v, _ in cluster[1]]))
        tolerance = level * 0.01
        touches = int((values.sub(level).abs() <= tolerance).sum())
        if touches >= 3:
            qualified.append((cluster, level, touches))
    if not qualified:
        eligible = values[values <= current] if is_support else values[values >= current]
        return float(eligible.max() if is_support else eligible.min()), 0
    best, level, touches = max(qualified, key=lambda item: item[2] * (1 + max(i for _, i in item[0][1]) / len(values)))
    return level, touches

@router.get("/recommend")
def recommend(request: Request, symbol: str = ACTIVE_SYMBOL, capital: float = Query(1000, gt=0), risk: str = Query("medium", pattern="^(low|medium|high)$"), days: int = Query(90, ge=1, le=365), margin_target_pct: float = Query(.7, gt=0, le=10)):
    symbol = symbol.strip().upper().replace("/", "")
    df = request.app.state.client.get_historical_klines(symbol, ACTIVE_INTERVAL, lookback_days=days)
    close, high, low = df["close"], df["high"], df["low"]
    current = float(close.iloc[-1])
    support, support_touches = _level(low.rename("low"), current); resistance, resistance_touches = _level(high.rename("high"), current)
    tr = np.maximum(high - low, np.maximum((high - close.shift()).abs(), (low - close.shift()).abs()))
    atr = float(tr.rolling(14).mean().iloc[-1]); profile = RISK_LEVELS[risk]
    floor = max(float(np.finfo(float).tiny), support - profile["floor_atr"] * atr)
    ceiling = resistance + profile["ceiling_atr"] * atr
    range_pct = max(0.0, (ceiling - floor) / current * 100)
    fee_pct = float(getattr(request.app.state.settings, "scanner_fee_pct", .1))
    filters = SIM_FILTERS
    service = getattr(request.app.state, "grid_scan_service", None)
    if service is not None:
        try:
            _market, filters = service._market(symbol, capital, service.clock() + float(request.app.state.settings.scanner_timeout_seconds))
        except Exception:
            filters = SIM_FILTERS
    min_cell = minimum_cell_threshold(filters)
    evaluation = None
    grids = MIN_LEVELS
    for count in range(MAX_LEVELS, MIN_LEVELS - 1, -1):
        candidate = evaluate_levels(count, range_pct, Decimal(str(capital)), Decimal(str(current)),
                                    filters, fee_pct, 0.0, min_cell)
        if candidate["cell_ok"] and candidate["edge_gross_pct"] >= margin_target_pct:
            evaluation, grids = candidate, count
            break
    if evaluation is None:
        evaluation = evaluate_levels(grids, range_pct, Decimal(str(capital)), Decimal(str(current)),
                                     filters, fee_pct, 0.0, min_cell)
    prices = np.linspace(floor, ceiling, grids + 1)
    buy_prices = prices[:-1]
    below = buy_prices < current
    capital_below = capital * float(below.sum()) / grids
    loss_at_floor = sum((capital / grids / price) * max(0.0, price - floor)
                        for price in buy_prices[below])
    target_met = bool(evaluation["cell_ok"] and evaluation["edge_gross_pct"] >= margin_target_pct)
    net_per_cycle_usdt = capital / grids * evaluation["edge_gross_pct"] / 100
    net_per_cycle_after_dust_usdt = capital / grids * evaluation["net_edge_pct"] / 100
    target_cycles = ceil(capital * margin_target_pct / 100 / net_per_cycle_usdt) if net_per_cycle_usdt > 0 else None
    prediction = request.app.state.prediction_loop.latest.get((symbol, ACTIVE_INTERVAL))
    simulations = {}
    for strategy in ("simple", "smart"):
        try:
            result = run_simulation(df, strategy=strategy, n=grids, capital=capital,
                low=floor, high=ceiling, fee_pct=fee_pct, filters=filters)
            simulations[strategy] = result["metrics"]
        except Exception as exc:
            simulations[strategy] = {"unavailable": f"{type(exc).__name__}: {exc}"}
    if symbol == "XRPUSDT":
        try:
            from api.routes.volatility import forecast as volatility_forecast
            model_forecast = volatility_forecast(request, symbol)
            forecast24 = next(row for row in model_forecast["forecasts"] if int(row["horizon_h"]) == 24)
            sigma_24h = float(forecast24["move_1sigma_pct"]) / 100.0
            sigma_source = f"pronóstico campeón XRP 24 h ({forecast24['champion']})"
        except Exception:
            sigma_24h = float(np.std(np.diff(np.log(np.asarray(close, dtype=float))), ddof=1) * np.sqrt(24))
            sigma_source = "volatilidad realizada (respaldo; pronóstico XRP no disponible)"
    else:
        sigma_24h = float(np.std(np.diff(np.log(np.asarray(close, dtype=float))), ddof=1) * np.sqrt(24))
        sigma_source = "volatilidad realizada de velas 1h (log-retornos)"
    range_risk = estimate_range_risk(current, floor, ceiling, sigma_24h)
    distance_ceiling = (ceiling - current) / current * 100
    distance_floor = (current - floor) / current * 100
    range_position_warning = None
    if distance_ceiling < 2:
        range_position_warning = "El precio está cerca del techo: casi todo el capital quedaría en compras"
    elif distance_floor < 2:
        range_position_warning = "El precio está cerca del piso: casi todo el capital quedaría en ventas"
    return {"symbol": symbol, "current_price": current, "recommended_floor": floor,
        "recommended_ceiling": ceiling, "range_pct": range_pct, "suggested_grids": grids,
        "capital_per_grid": capital / grids, "spacing_pct": evaluation["spacing_pct"],
        "margin_target_pct": margin_target_pct, "fee_pct": fee_pct,
        "edge_gross_pct": evaluation["edge_gross_pct"],
        "dust_estimate_pct": evaluation["dust_pct"], "net_margin_pct": evaluation["net_edge_pct"],
        "net_per_cycle_usdt": net_per_cycle_usdt,
        "net_per_cycle_after_dust_usdt": net_per_cycle_after_dust_usdt,
        "target_met": target_met,
        "estimated_cycles_to_target": target_cycles, "min_cell_usdt": str(min_cell),
        "min_cell_warning": minimum_cell_warning(capital, grids, min_cell),
        "range_warning": range_pct > profile["max_range_pct"],
        "risk": {**profile, "capital_below_price_pct": capital_below / capital * 100,
                 "unrealized_loss_at_floor_usdt": loss_at_floor},
        "simulations": {"label": "histórico, no promesa de resultado", "strategies": simulations},
        "range_risk": {"sigma_24h": sigma_24h, "source": sigma_source,
            "horizons": range_risk,
            "disclaimer": "Estimación; las colas gruesas hacen que la probabilidad real pueda ser mayor; no validado más allá de 24 h; no es predicción de dirección."},
        "range_position_warning": range_position_warning,
        "confidence": prediction.get("consensus_confidence") if prediction else None,
        "analysis": {"main_support": support, "main_resistance": resistance, "atr": atr,
            "support_touches": support_touches, "resistance_touches": resistance_touches},
        "prediction_signal": prediction if symbol == ACTIVE_SYMBOL else None,
        "disclaimer": "Estimación teórica basada en datos históricos. No es una promesa de resultado."}


@router.get("/volatility")
def realized_volatility(request: Request, symbol: str = ACTIVE_SYMBOL, days: int = Query(90, ge=2, le=365)):
    symbol = symbol.strip().upper().replace("/", "")
    frame = request.app.state.client.get_historical_klines(symbol, ACTIVE_INTERVAL, lookback_days=days)
    closes = np.asarray(frame["close"], dtype=float)
    if len(closes) < 3 or not np.all(np.isfinite(closes)) or np.any(closes <= 0):
        return {"symbol": symbol, "source": "volatilidad realizada", "sigma_24h_pct": None,
                "price": None, "range_2sigma": None, "samples": 0}
    returns = np.diff(np.log(closes))
    sigma_24h = float(np.std(returns, ddof=1) * np.sqrt(24))
    price = float(closes[-1])
    return {"symbol": symbol, "source": "volatilidad realizada de velas 1h (log-retornos)",
            "sigma_24h_pct": sigma_24h * 100, "price": price,
            "range_2sigma": [price * np.exp(-2 * sigma_24h), price * np.exp(2 * sigma_24h)],
            "samples": int(len(returns)), "calculated_at": datetime.now(timezone.utc).isoformat()}
