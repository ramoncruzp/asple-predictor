from __future__ import annotations
from decimal import Decimal
from math import asinh, ceil, isfinite, sqrt
from statistics import NormalDist
from fastapi import APIRouter, Query, Request
import numpy as np
from config.models_config import (ACTIVE_INTERVAL, ACTIVE_SYMBOL, VOL_CHAMPIONS,
                                  VOL_SOURCE, VOL_SYMBOL, VOL_WIDEN_DISAGREEMENT_PCT,
                                  VOL_WIDEN_K_ACTIVE, WIDEN_DISAGREEMENT_MIN)
from grid.structure import (MAX_LEVELS, MIN_LEVELS, evaluate_levels,
                            minimum_cell_threshold, minimum_cell_warning)
from grid.sim.runner import FILTERS as SIM_FILTERS, run_simulation
from grid.range_risk import estimate_range_risk
from grid.policy import DEFAULT_SMART_PARAMS, break_prob
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
CENTERED_TOUCH_TARGETS = {"low": 0.50, "medium": 0.30, "high": 0.15}
CENTERED_Z = {risk: NormalDist().inv_cdf(1.0 - probability / 2.0)
              for risk, probability in CENTERED_TOUCH_TARGETS.items()}


def _centered_range(price, sigma_24h, risk):
    if not isfinite(float(sigma_24h)) or float(sigma_24h) <= 0:
        return None
    sigma_72h = float(sigma_24h) * sqrt(3.0)
    d_raw = CENTERED_Z[risk] * sigma_72h
    max_d = asinh(float(RISK_LEVELS[risk]["max_range_pct"]) / 200.0)
    d = min(d_raw, max_d)
    return {"floor": float(price) * float(np.exp(-d)),
            "ceiling": float(price) * float(np.exp(d)),
            "range_pct": 200.0 * float(np.sinh(d)), "d": d,
            "sigma_72h": sigma_72h, "z": CENTERED_Z[risk],
            "target_touch_probability_72h": CENTERED_TOUCH_TARGETS[risk],
            "touch_probability_each_side_72h": 2.0 * (1.0 - NormalDist().cdf(d / sigma_72h)),
            "limited_by_profile": d_raw > max_d}


def _simulation_window(frame, floor, ceiling, days):
    closes = np.asarray(frame["close"], dtype=float)
    inside = np.flatnonzero((closes > float(floor)) & (closes < float(ceiling)))
    if not len(inside):
        return None, {"sim_start": None, "sim_days": 0.0,
                      "window_warning": "Ventana corta (menos de 30 días): poca evidencia.",
                      "unavailable": f"El precio de los últimos {int(days)} días nunca estuvo dentro de este rango."}
    start_index = int(inside[0])
    selected = frame.iloc[start_index:]
    if "timestamp" in selected:
        stamps = selected["timestamp"]
        start = stamps.iloc[0]
        sim_start = start.isoformat() if hasattr(start, "isoformat") else str(start)
        sim_days = max(0.0, (stamps.iloc[-1] - start).total_seconds() / 86400.0) if len(stamps) > 1 else 0.0
    else:
        sim_start = f"índice {start_index} (timestamp no disponible)"
        sim_days = max(0.0, (len(selected) - 1) / 24.0)
    return selected, {"sim_start": sim_start, "sim_days": sim_days,
                      "window_warning": "Ventana corta (menos de 30 días): poca evidencia."
                      if sim_days < 30 else None}


def _resolve_volatility_source(symbol, forecast24, requested_source):
    """Select the Advisor's 24h sigma without changing forecast or grid formulas."""
    if symbol != VOL_SYMBOL:
        return {"sigma_24h": None, "effective": "realizada", "requested": requested_source,
                "confidence": None, "consensus": None, "champion_sigma_24h": None,
                "reason": "moneda sin modelos de consenso"}
    if not forecast24:
        return {"sigma_24h": None, "effective": "realizada", "requested": requested_source,
                "confidence": None, "consensus": None, "champion_sigma_24h": None,
                "reason": "pronóstico XRP no disponible"}
    if forecast24.get("stale"):
        return {"sigma_24h": None, "effective": "realizada", "requested": requested_source,
                "confidence": None, "consensus": None, "champion_sigma_24h": None,
                "reason": "pronóstico obsoleto"}
    champion_sigma = (float(forecast24["move_1sigma_pct"]) / 100.0
                      if forecast24.get("move_1sigma_pct") is not None else None)
    consensus = forecast24.get("consensus") or None
    consensus_sigma = (float(consensus["sigma_pct"]) / 100.0
                       if consensus and consensus.get("sigma_pct") is not None else None)
    validated = bool(consensus and consensus.get("validation_status_live") == "validated")
    requested = requested_source if requested_source in {"champion", "consensus", "auto"} else "auto"
    effective = None
    sigma = None
    if requested == "champion" and champion_sigma and champion_sigma > 0:
        effective, sigma = "campeon", champion_sigma
    elif requested == "consensus" and consensus_sigma and consensus_sigma > 0:
        effective, sigma = "consenso", consensus_sigma
    elif requested == "auto" and validated and consensus_sigma and consensus_sigma > 0:
        effective, sigma = "consenso", consensus_sigma
    elif champion_sigma and champion_sigma > 0:
        effective, sigma = "campeon", champion_sigma
    return {"sigma_24h": sigma, "effective": effective or "realizada", "requested": requested,
            "confidence": consensus.get("confidence") if consensus else None,
            "consensus": consensus, "champion_sigma_24h": champion_sigma,
            "consensus_sigma_24h": consensus_sigma,
            "reason": None if sigma is not None else "pronóstico ausente o sigma inválida"}


def _model_volatility_advisories(request, selection, forecast24):
    if selection["effective"] == "realizada":
        return [], []
    model_names = ([forecast24.get("champion") or VOL_CHAMPIONS[24]]
                   if selection["effective"] == "campeon"
                   else (selection.get("consensus") or {}).get("eligible_models", []))
    try:
        from api.routes.volatility import model_stats
        horizon_stats = model_stats(request, symbol=VOL_SYMBOL, horizon=24)["horizons"][0]
        by_name = {item["model_name"]: item for item in horizon_stats.get("models", [])}
    except Exception:
        return [], []
    accumulating, bias_alerts = [], []
    from models.volatility.model_stats import N_MIN
    for name in model_names:
        item = by_name.get(name)
        if item is None:
            continue
        n = int(item.get("n_verificadas") or 0)
        if n < N_MIN:
            accumulating.append({"model_name": name, "n_verificadas": n, "n_min": N_MIN})
        if item.get("bias_alert"):
            bias = (item.get("all") or {}).get("bias_mean")
            bias_alerts.append({"model_name": name,
                                "direction": "sobreestima" if bias is not None and bias > 0 else "subestima"})
    return accumulating, bias_alerts


def _widen_runtime_settings(request, horizon=24):
    db = getattr(request.app.state, "db", None)
    active_query = getattr(db, "get_widen_active_values", None)
    latest_query = getattr(db, "get_latest_widen_factor", None)
    active = (active_query(VOL_SYMBOL, horizon, VOL_WIDEN_K_ACTIVE, VOL_WIDEN_DISAGREEMENT_PCT)
              if active_query else {"k_active": VOL_WIDEN_K_ACTIVE,
                                    "disagreement_pct_active": VOL_WIDEN_DISAGREEMENT_PCT})
    latest = latest_query(VOL_SYMBOL, horizon) if latest_query else None
    return active, latest

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
def recommend(request: Request, symbol: str = ACTIVE_SYMBOL, capital: float = Query(1000, gt=0), risk: str = Query("medium", pattern="^(low|medium|high)$"), days: int = Query(90, ge=1, le=365), margin_target_pct: float = Query(.7, gt=0, le=10), range_mode: str = Query("centrado", pattern="^(centrado|estructural)$")):
    symbol = symbol.strip().upper().replace("/", "")
    df = request.app.state.client.get_historical_klines(symbol, ACTIVE_INTERVAL, lookback_days=days)
    close, high, low = df["close"], df["high"], df["low"]
    current = float(close.iloc[-1])
    support, support_touches = _level(low.rename("low"), current); resistance, resistance_touches = _level(high.rename("high"), current)
    tr = np.maximum(high - low, np.maximum((high - close.shift()).abs(), (low - close.shift()).abs()))
    atr = float(tr.rolling(14).mean().iloc[-1]); profile = RISK_LEVELS[risk]
    floor = max(float(np.finfo(float).tiny), support - profile["floor_atr"] * atr)
    ceiling = resistance + profile["ceiling_atr"] * atr
    requested_vol_source = VOL_SOURCE
    forecast24 = None
    if symbol == VOL_SYMBOL:
        try:
            from api.routes.volatility import forecast as volatility_forecast
            model_forecast = volatility_forecast(request, symbol)
            forecast24 = next(row for row in model_forecast["forecasts"]
                              if int(row["horizon_h"]) == 24)
        except Exception:
            forecast24 = None
    volatility = _resolve_volatility_source(symbol, forecast24, requested_vol_source)
    sigma_realized_24h = float(np.std(np.diff(np.log(np.asarray(close, dtype=float))), ddof=1) * np.sqrt(24))
    sigma_24h = volatility["sigma_24h"] or sigma_realized_24h
    vol_source_effective = volatility["effective"]
    sigma_source = (f"pronostico {vol_source_effective} XRP 24 h"
                    if vol_source_effective in {"campeon", "consenso"}
                    else "volatilidad realizada de velas 1h (log-retornos)")
    if vol_source_effective == "realizada" and volatility.get("reason"):
        sigma_source += f" (respaldo; {volatility['reason']})"
    accumulating_models, bias_alerts = _model_volatility_advisories(request, volatility, forecast24)
    widen_active, widen_latest = _widen_runtime_settings(request)
    k_active = float(widen_active["k_active"])
    disagreement_active = float(widen_active["disagreement_pct_active"])
    current_iqr = ((forecast24.get("consensus") or {}).get("dispersion_iqr")
                   if forecast24 else None)
    adaptive_ready = bool(widen_latest and int(widen_latest.get("dispersion_n") or 0) >= WIDEN_DISAGREEMENT_MIN
                          and widen_latest.get("stress_threshold") is not None)
    is_stress = (current_iqr is not None and current_iqr >= float(widen_latest["stress_threshold"])
                 if adaptive_ready else volatility["confidence"] == "baja")
    range_widened = vol_source_effective in {"campeon", "consenso"} and is_stress
    if range_widened:
        floor = max(float(np.finfo(float).tiny), current - (current - floor) * k_active)
        ceiling = current + (ceiling - current) * k_active
        sigma_24h *= k_active
    range_structural = {"floor": floor, "ceiling": ceiling,
                        "range_pct": max(0.0, (ceiling - floor) / current * 100)}
    centered = _centered_range(current, sigma_24h, risk)
    if centered is not None:
        centered["sigma_widened"] = bool(range_widened)
        centered["sigma_widen_factor"] = k_active if range_widened else None
    actual_range_mode = range_mode if range_mode == "estructural" or centered is None else "centrado"
    range_mode_reason = ("Sigma 72 h no disponible; se conserva el rango estructural."
                         if centered is None and range_mode == "centrado" else None)
    if actual_range_mode == "centrado":
        floor, ceiling, range_pct = centered["floor"], centered["ceiling"], centered["range_pct"]
    else:
        floor, ceiling, range_pct = (range_structural["floor"], range_structural["ceiling"],
                                     range_structural["range_pct"])
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
    simulation_window, simulation_meta = _simulation_window(df, floor, ceiling, days)
    simulations = {}
    for strategy in ("simple", "smart"):
        if simulation_window is None:
            simulations[strategy] = {**simulation_meta}
            continue
        try:
            result = run_simulation(simulation_window, strategy=strategy, n=grids, capital=capital,
                low=floor, high=ceiling, fee_pct=fee_pct, filters=filters)
            simulations[strategy] = {**simulation_meta, **result["metrics"]}
        except Exception as exc:
            simulations[strategy] = {**simulation_meta,
                                     "unavailable": f"{type(exc).__name__}: {exc}"}
    range_risk = estimate_range_risk(current, floor, ceiling, sigma_24h,
        vol_source_effective=vol_source_effective,
        vol_source_requested=volatility["requested"])
    pause_break_prob, _, _, _ = break_prob(current, floor, ceiling, sigma_24h, DEFAULT_SMART_PARAMS)
    pause_enter_prob = float(DEFAULT_SMART_PARAMS["pause_enter_prob"])
    disagreement_pct = None
    if volatility.get("champion_sigma_24h") and volatility.get("consensus_sigma_24h"):
        disagreement_pct = abs(volatility["consensus_sigma_24h"] - volatility["champion_sigma_24h"])
        disagreement_pct = disagreement_pct / volatility["champion_sigma_24h"] * 100.0
    volatility_advisory = {
        "vol_source_effective": vol_source_effective,
        "vol_source_requested": volatility["requested"],
        "sigma_24h": sigma_24h,
        "confidence": volatility.get("confidence"),
        "range_widened": range_widened,
        "champion_sigma_24h": volatility.get("champion_sigma_24h"),
        "consensus_sigma_24h": volatility.get("consensus_sigma_24h"),
        "disagreement_pct": disagreement_pct,
        "show_comparison": disagreement_pct is not None and disagreement_pct > disagreement_active,
        "k_active": k_active,
        "k_suggested": (widen_latest or {}).get("k_stress_smoothed"),
        "widen_status": (widen_latest or {}).get("status", "acumulando"),
        "widen_progress_pct": (widen_latest or {}).get("progress_pct", 0.0),
        "widen_ci_low": (widen_latest or {}).get("ci_low"),
        "widen_ci_high": (widen_latest or {}).get("ci_high"),
        "widen_ci_width": (widen_latest or {}).get("ci_width"),
        "widen_n": (widen_latest or {}).get("n", 0),
        "widen_n_effective": (widen_latest or {}).get("n_effective", 0.0),
        "widen_days_estimated": (widen_latest or {}).get("days_estimated"),
        "widen_adaptive_trigger": adaptive_ready,
        "widen_stress_threshold": (widen_latest or {}).get("stress_threshold"),
        "disagreement_pct_active": disagreement_active,
        "disagreement_threshold_suggested": (widen_latest or {}).get("disagreement_threshold_suggested"),
        "disagreement_status": (widen_latest or {}).get("disagreement_status", "acumulando"),
        "disagreement_progress": (widen_latest or {}).get("disagreement_progress", 0.0),
        "reason": volatility.get("reason"),
        "accumulating_models": accumulating_models,
        "bias_alerts": bias_alerts,
    }
    distance_ceiling = (ceiling - current) / current * 100
    distance_floor = (current - floor) / current * 100
    range_position_warning = None
    if distance_ceiling < 2:
        range_position_warning = "El precio está cerca del techo: casi todo el capital quedaría en compras"
    elif distance_floor < 2:
        range_position_warning = "El precio está cerca del piso: casi todo el capital quedaría en ventas"
    range_preference_note = None
    if actual_range_mode == "centrado" and min(
            current - range_structural["floor"], range_structural["ceiling"] - current) < atr:
        range_preference_note = "El precio está a menos de 1 ATR de un borde estructural; se prefiere el rango centrado."
    if actual_range_mode == "centrado" and centered["limited_by_profile"]:
        range_mode_reason = "Rango centrado limitado por el perfil."
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
        "range_structural": range_structural,
        "range_centered": centered,
        "recommended_range": actual_range_mode,
        "range_mode": actual_range_mode,
        "range_mode_reason": range_mode_reason,
        "range_limit_warning": bool(actual_range_mode == "centrado" and centered["limited_by_profile"]),
        "vol_source_effective": vol_source_effective,
        "vol_source_requested": volatility["requested"],
        "volatility_advisory": volatility_advisory,
        "risk": {**profile, "capital_below_price_pct": capital_below / capital * 100,
                 "unrealized_loss_at_floor_usdt": loss_at_floor},
        "simulations": {"label": "histórico, no promesa de resultado",
            "sim_start": simulation_meta["sim_start"], "sim_days": simulation_meta["sim_days"],
            "window_warning": simulation_meta["window_warning"], "strategies": simulations},
        "range_risk": {"sigma_24h": sigma_24h, "source": sigma_source,
            "horizons": range_risk,
            "disclaimer": "Estimación; las colas gruesas hacen que la probabilidad real pueda ser mayor; no validado más allá de 24 h; no es predicción de dirección."},
        "pause_risk": {"horizon_h": 24, "break_prob": pause_break_prob,
                       "pause_enter_prob": pause_enter_prob,
                       "would_be_pausable": pause_break_prob > pause_enter_prob},
        "range_position_warning": range_position_warning,
        "range_preference_note": range_preference_note,
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
