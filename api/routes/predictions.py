from fastapi import APIRouter, Query, Request
from data.feature_engineer import FeatureEngineer
router = APIRouter()

@router.get("/latest")
def latest(request: Request, symbol: str = "XRPUSDT", interval: str = "4h", limit: int = Query(20, ge=1, le=100)):
    return request.app.state.db.get_predictions_with_outcomes(symbol=symbol, interval=interval, limit=limit)

@router.get("/consensus")
def consensus(request: Request, symbol: str = "XRPUSDT", interval: str = "4h"):
    lookback_days = {"1h": 20, "4h": 90, "12h": 180, "1d": 365, "1w": 2200}.get(interval.lower(), 90)
    df = request.app.state.client.get_historical_klines(symbol, interval.lower(), lookback_days=lookback_days).tail(200)
    result = request.app.state.ensemble.predict_and_save(symbol, interval, df)
    features = FeatureEngineer().compute_features(df)
    latest = features.iloc[-1]
    result["candles"] = [{"time": int(row.timestamp.timestamp()), "open": float(row.open), "high": float(row.high), "low": float(row.low), "close": float(row.close)} for row in df.itertuples()]
    result["indicators"] = {"rsi_14": float(latest["rsi_14"]), "macd": float(latest["macd"]), "atr_14": float(latest["atr_14"])}
    return result

@router.get("/history")
def history(request: Request, symbol: str | None = None, model: str | None = None, days: int = Query(30, ge=1, le=365)):
    return request.app.state.db.get_predictions_with_outcomes(symbol=symbol, model_name=model, days=days, limit=1000)
