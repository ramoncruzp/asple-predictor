"""Run one real XRP/USDT ensemble prediction and persist it."""

import json
from pathlib import Path

from config.settings import Settings
from data.binance_client import BinanceClient
from database.db_manager import DBManager
from models.ensemble import EnsemblePredictor
from models.model_a_xgboost import ModelA
from models.model_b_gru import ModelB
from models.model_c_prophet import ModelC


def main() -> None:
    settings = Settings()
    api_key = "" if settings.binance_api_key.startswith("tu_") else settings.binance_api_key
    api_secret = "" if settings.binance_api_secret.startswith("tu_") else settings.binance_api_secret
    client = BinanceClient(api_key, api_secret)
    db = DBManager(settings.database_url)
    models = (ModelA(), ModelB(), ModelC())
    for model, filename in zip(models, ("model_a_xrp_4h.joblib", "model_b_xrp_4h.pt", "model_c_xrp_4h.joblib")):
        model.load(str(Path("models/saved") / filename))
    df = client.get_historical_klines("XRPUSDT", "4h", lookback_days=60).tail(200)
    result = EnsemblePredictor(*models, db).predict_and_save("XRPUSDT", "4h", df)
    print(json.dumps(result, indent=2, default=str))
    print("Guardado en DB: OK")


if __name__ == "__main__":
    main()
