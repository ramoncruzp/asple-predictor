from __future__ import annotations
from fastapi import APIRouter, Query, Request
import numpy as np
from config.models_config import ACTIVE_INTERVAL, ACTIVE_SYMBOL
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
def recommend(request: Request, symbol: str = ACTIVE_SYMBOL, capital: float = Query(1000, gt=0), risk: str = Query("medium", pattern="^(low|medium|high)$"), days: int = Query(90, ge=1, le=365)):
    symbol = symbol.strip().upper().replace("/", "")
    df = request.app.state.client.get_historical_klines(symbol, ACTIVE_INTERVAL, lookback_days=days)
    close, high, low = df["close"], df["high"], df["low"]
    current = float(close.iloc[-1])
    support, support_touches = _level(low.rename("low"), current); resistance, resistance_touches = _level(high.rename("high"), current)
    tr = np.maximum(high - low, np.maximum((high - close.shift()).abs(), (low - close.shift()).abs()))
    atr = float(tr.rolling(14).mean().iloc[-1]); floor = support - {"low": 1.5, "medium": 2.0, "high": 3.0}[risk] * atr; ceiling = resistance + 0.5 * atr
    range_pct = max(0.0, (ceiling - floor) / floor * 100)
    grids = max(5, min(20, round(range_pct / 2.5)))
    prediction = request.app.state.prediction_loop.latest.get((symbol, ACTIVE_INTERVAL))
    return {"symbol": symbol, "current_price": current, "recommended_floor": floor, "recommended_ceiling": ceiling, "range_pct": range_pct, "suggested_grids": grids, "capital_per_grid": capital / grids, "spacing_pct": range_pct / grids, "confidence": prediction.get("consensus_confidence") if prediction else None, "analysis": {"main_support": support, "main_resistance": resistance, "atr": atr, "support_touches": support_touches, "resistance_touches": resistance_touches}, "prediction_signal": prediction, "disclaimer": "Análisis estadístico. Validar antes de operar."}
