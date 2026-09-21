"""Training and live-candle data pipelines."""

from __future__ import annotations

import logging
import math

import pandas as pd

from data.binance_client import BinanceClient


logger = logging.getLogger(__name__)


class DataPipeline:
    """Data access layer for training and live inference."""

    def __init__(self, client: BinanceClient):
        self.client = client

    def get_training_data(self, symbol: str, interval: str, lookback_days: int) -> pd.DataFrame:
        frame = self.client.get_historical_klines(symbol, interval, lookback_days)
        ohlcv = ["open", "high", "low", "close", "volume"]
        missing = [column for column in ohlcv if column not in frame.columns]
        if missing:
            raise ValueError(f"Faltan columnas OHLCV en los datos de entrenamiento: {missing}")
        if frame[ohlcv].isna().any().any():
            raise ValueError(
                f"Los datos de entrenamiento contienen NaN en OHLCV para {symbol} {interval}"
            )
        return frame

    def get_latest_candles(
        self, symbol: str, interval: str, n_candles: int = 100
    ) -> pd.DataFrame:
        if n_candles <= 0:
            raise ValueError("n_candles debe ser mayor que cero")
        if interval not in BinanceClient.INTERVAL_MINUTES:
            raise ValueError(f"Intervalo no válido: {interval}")
        minutes = BinanceClient.INTERVAL_MINUTES[interval]
        lookback_days = max(1, math.ceil(n_candles * minutes / 1440) + 1)
        frame = self.client.get_historical_klines(symbol, interval, lookback_days)
        return frame.tail(n_candles).reset_index(drop=True)
