from fastapi import APIRouter, Query, Request
from data.feature_engineer import FeatureEngineer
from config.models_config import ACTIVE_INTERVAL, ACTIVE_SYMBOL
router = APIRouter()

@router.get("/latest")
def latest(request: Request, symbol: str = ACTIVE_SYMBOL, interval: str = ACTIVE_INTERVAL, limit: int = Query(20, ge=1, le=100)):
    return request.app.state.db.get_predictions_with_outcomes(symbol=symbol, interval=interval, limit=limit)

@router.get("/consensus")
def consensus(request: Request, symbol: str = ACTIVE_SYMBOL, interval: str = ACTIVE_INTERVAL):
    symbol = symbol.strip().upper().replace("/", "")
    interval = interval.strip().lower()
    lookback_days = {"1h": 30, "4h": 90, "12h": 180, "1d": 365, "1w": 2200}.get(interval, 90)
    df = request.app.state.client.get_historical_klines(symbol, interval, lookback_days=lookback_days).tail(200)
    active = symbol == ACTIVE_SYMBOL and interval == ACTIVE_INTERVAL
    if not active:
        result = {"symbol": symbol, "interval": interval, "consensus_signal": None,
                  "consensus_probability_up": None, "consensus_confidence": None,
                  "model_available": False, "persisted": False}
    else:
        cached = request.app.state.prediction_loop.latest.get((symbol, interval))
        if cached is not None:
            result = dict(cached)
            result["persisted"] = True
        else:
            result = dict(request.app.state.ensemble.predict(symbol, interval, df))
            result["persisted"] = False
        result["model_available"] = True
    features = FeatureEngineer().compute_features(df)
    latest = features.iloc[-1]
    result["candles"] = [{"time": int(row.timestamp.timestamp()), "open": float(row.open), "high": float(row.high), "low": float(row.low), "close": float(row.close)} for row in df.itertuples()]
    result["indicators"] = {"rsi_14": float(latest["rsi_14"]), "macd": float(latest["macd"]), "atr_14": float(latest["atr_14"])}
    return result

@router.get("/history")
def history(request: Request, symbol: str | None = None, model: str | None = None, days: int = Query(30, ge=1, le=365)):
    return request.app.state.db.get_predictions_with_outcomes(symbol=symbol, model_name=model, days=days, limit=1000)
