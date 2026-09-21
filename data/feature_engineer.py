"""Technical-indicator feature engineering for Binance OHLCV data."""

from __future__ import annotations

import numpy as np
import pandas as pd
import ta as ta_lib
from sklearn.preprocessing import StandardScaler


class FeatureEngineer:
    """Compute the model features and the four-candle prediction target."""

    FEATURE_NAMES = [
        "ema_9", "ema_21", "ema_50", "ema_cross",
        "rsi_14", "macd", "macd_signal", "macd_hist", "stoch_k", "stoch_d",
        "atr_14", "bb_upper", "bb_lower", "bb_mid", "bb_pct",
        "obv", "vwap", "vol_ratio",
    ]
    REQUIRED_COLUMNS = {"high", "low", "close", "volume"}
    WARMUP_ROWS = 50

    def compute_features(self, df: pd.DataFrame) -> pd.DataFrame:
        """Return a feature-enriched copy of an OHLCV DataFrame."""

        missing = self.REQUIRED_COLUMNS.difference(df.columns)
        if missing:
            raise ValueError(f"Faltan columnas OHLCV requeridas: {sorted(missing)}")
        result = df.copy(deep=True)
        high = result["high"]
        low = result["low"]
        close = result["close"]
        volume = result["volume"]

        result["ema_9"] = ta_lib.trend.EMAIndicator(close, window=9).ema_indicator()
        result["ema_21"] = ta_lib.trend.EMAIndicator(close, window=21).ema_indicator()
        result["ema_50"] = ta_lib.trend.EMAIndicator(close, window=50).ema_indicator()
        result["ema_cross"] = np.select(
            [result["ema_9"] > result["ema_21"], result["ema_9"] < result["ema_21"]],
            [1, -1],
            default=0,
        ).astype("int64")

        result["rsi_14"] = ta_lib.momentum.RSIIndicator(close, window=14).rsi()
        macd = ta_lib.trend.MACD(close)
        result["macd"] = macd.macd()
        result["macd_signal"] = macd.macd_signal()
        result["macd_hist"] = macd.macd_diff()
        stoch = ta_lib.momentum.StochasticOscillator(high, low, close)
        result["stoch_k"] = stoch.stoch()
        result["stoch_d"] = stoch.stoch_signal()

        result["atr_14"] = ta_lib.volatility.AverageTrueRange(
            high, low, close, window=14
        ).average_true_range()
        bbands = ta_lib.volatility.BollingerBands(close, window=20, window_dev=2)
        result["bb_upper"] = bbands.bollinger_hband()
        result["bb_lower"] = bbands.bollinger_lband()
        result["bb_mid"] = bbands.bollinger_mavg()
        result["bb_pct"] = bbands.bollinger_pband()

        result["obv"] = ta_lib.volume.OnBalanceVolumeIndicator(
            close, volume
        ).on_balance_volume()
        cumulative_volume = volume.cumsum()
        result["vwap"] = (close * volume).cumsum() / cumulative_volume
        result["vol_ratio"] = volume / volume.rolling(20).mean()

        future_return = close.shift(-4) / close - 1
        result["target"] = np.where(
            future_return.notna(), (future_return > 0.005).astype(float), np.nan
        )

        for name in self.FEATURE_NAMES:
            if result[name].isna().all():
                raise ValueError(f"El indicador {name} produce NaN en todas las filas")

        return result.iloc[self.WARMUP_ROWS:].reset_index(drop=True)

    @staticmethod
    def _indicator_column(
        indicator: pd.DataFrame, expected: str | tuple[str, ...], name: str
    ) -> pd.Series:
        candidates = (expected,) if isinstance(expected, str) else expected
        if indicator is None:
            raise ValueError(f"El indicador {name} produce NaN en todas las filas")
        for column in candidates:
            if column in indicator.columns:
                return indicator[column]
        raise ValueError(f"El indicador {name} produce NaN en todas las filas")

    def get_feature_names(self) -> list[str]:
        """Return feature columns in their stable model-input order."""
        return list(self.FEATURE_NAMES)

    def normalize_features(
        self, df: pd.DataFrame
    ) -> tuple[pd.DataFrame, StandardScaler]:
        """Standardize features and return the transformed copy plus fitted scaler."""
        result = df.copy(deep=True)
        if not set(self.FEATURE_NAMES).issubset(result.columns):
            result = self.compute_features(result)
        if result[self.FEATURE_NAMES].isna().any().any():
            raise ValueError("No se pueden normalizar features que contienen NaN")
        scaler = StandardScaler()
        values = scaler.fit_transform(result[self.FEATURE_NAMES].astype("float64"))
        normalized = pd.DataFrame(values, columns=self.FEATURE_NAMES, index=result.index)
        result = pd.concat([result.drop(columns=self.FEATURE_NAMES), normalized], axis=1)
        return result, scaler

    def prepare_for_model(self, df: pd.DataFrame) -> pd.DataFrame:
        """Compute features and remove warmup/unknown-target rows."""
        result = self.compute_features(df)
        return result.dropna(subset=[*self.FEATURE_NAMES, "target"]).reset_index(drop=True)
