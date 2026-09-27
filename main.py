"""Run one real XRP/USDT Model A shadow prediction and persist it."""

import json
from datetime import datetime, timezone

from config.settings import Settings
from config.models_config import ACTIVE_INTERVAL, ACTIVE_SYMBOL, SHADOW_ARTIFACT
from data.binance_client import BinanceClient
from database.db_manager import DBManager
from models.model_a_xgboost import ModelA
from models.shadow_predictor import ShadowPredictor


def main() -> None:
    settings = Settings()
    api_key = "" if settings.binance_api_key.startswith("tu_") else settings.binance_api_key
    api_secret = "" if settings.binance_api_secret.startswith("tu_") else settings.binance_api_secret
    client = BinanceClient(api_key, api_secret)
    db = DBManager(settings.database_url)
    model_a = ModelA()
    model_a.load(SHADOW_ARTIFACT)
    df = client.get_historical_klines(ACTIVE_SYMBOL, ACTIVE_INTERVAL, lookback_days=30)
    if not df.empty and df.iloc[-1]["close_time"] > datetime.now(timezone.utc):
        df = df.iloc[:-1]
    df = df.tail(200)
    result = ShadowPredictor(model_a, db).predict_and_save(ACTIVE_SYMBOL, ACTIVE_INTERVAL, df)
    print(json.dumps(result, indent=2, default=str))
    print("Guardado en DB: OK")


if __name__ == "__main__":
    main()
