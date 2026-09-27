"""Technical-indicator feature engineering for Binance OHLCV data."""

from __future__ import annotations

import numpy as np
import pandas as pd
import ta as ta_lib
from sklearn.preprocessing import StandardScaler
from math import ceil

from config.models_config import TARGET_HORIZON_CANDLES, TARGET_UP_THRESHOLD


def _ewm_window(span: int, alpha: float | None = None) -> int:
    effective_alpha = alpha if alpha is not None else 2.0 / (span + 1.0)
    return ceil(4.0 / effective_alpha)


def _calculate_warmup_rows() -> int:
    """Calculate the last feature's first-valid index (120)."""
    ema_26_first = _ewm_window(26) - 1
    macd_signal_first = ema_26_first + _ewm_window(9) - 1
    first_valid_indices = [
        _ewm_window(50) - 1,
        macd_signal_first,
        ceil(6.0 / (1.0 / 14.0)),  # RSI's extended finite history, plus first diff NaN
        15,  # 14-period stochastic plus its 3-row signal
        19,  # Bollinger/VWAP/volume windows
        20,  # OBV slope shift and rolling denominator
    ]
    return max(first_valid_indices)


class FeatureEngineer:
    """Compute the model features and the four-candle prediction target."""

    FEATURE_NAMES = [
        "ema_9_rel", "ema_21_rel", "ema_50_rel", "ema_cross",
        "rsi_14", "macd_rel", "macd_signal_rel", "macd_hist_rel", "stoch_k", "stoch_d",
        "atr_pct", "bb_width", "bb_pct", "obv_slope_20", "vwap_20_rel", "vol_ratio",
    ]
    REQUIRED_COLUMNS = {"high", "low", "close", "volume"}
    FEATURE_SET_VERSION = "rel_v1"

    # Calculated across the feature dependency windows; MACD signal is the
    # limiting feature (first valid index 120).
    WARMUP_ROWS = _calculate_warmup_rows()

    @staticmethod
    def _finite_ewm(
        values: pd.Series, span: int, alpha: float | None = None, window: int | None = None
    ) -> pd.Series:
        """Calculate a finite approximation of adjust=False EWM.

        A finite window keeps overlapping features identical when callers
        provide different amounts of older history.
        """
        alpha = alpha if alpha is not None else 2.0 / (span + 1.0)
        decay = 1.0 - alpha
        window = window if window is not None else ceil(4.0 / alpha)
        if window <= 0:
            raise ValueError("window debe ser positivo")
        # Chronological order, oldest to newest. The oldest point absorbs the
        # omitted infinite tail, preserving the steady-state response.
        weights = np.asarray(
            [decay ** (window - 1)]
            + [alpha * decay**offset for offset in reversed(range(window - 1))],
            dtype="float64",
        )
        array = values.to_numpy(dtype="float64")
        output = np.full(len(array), np.nan, dtype="float64")
        if len(array) >= window:
            output[window - 1:] = np.convolve(array, weights[::-1], mode="valid")
        return pd.Series(output, index=values.index)

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

        result["ema_9"] = self._finite_ewm(close, 9)
        result["ema_21"] = self._finite_ewm(close, 21)
        result["ema_50"] = self._finite_ewm(close, 50)
        result["ema_cross"] = np.select(
            [result["ema_9"] > result["ema_21"], result["ema_9"] < result["ema_21"]],
            [1, -1],
            default=0,
        ).astype("int64")
        result["ema_9_rel"] = result["ema_9"] / close - 1.0
        result["ema_21_rel"] = result["ema_21"] / close - 1.0
        result["ema_50_rel"] = result["ema_50"] / close - 1.0

        delta = close.diff()
        gains = delta.clip(lower=0.0)
        losses = -delta.clip(upper=0.0)
        rsi_window = ceil(6.0 / (1.0 / 14.0))
        avg_gains = self._finite_ewm(gains, 14, alpha=1.0 / 14.0, window=rsi_window)
        avg_losses = self._finite_ewm(losses, 14, alpha=1.0 / 14.0, window=rsi_window)
        relative_strength = avg_gains / avg_losses
        result["rsi_14"] = 100.0 - 100.0 / (1.0 + relative_strength)
        ema_12 = self._finite_ewm(close, 12)
        ema_26 = self._finite_ewm(close, 26)
        result["macd"] = ema_12 - ema_26
        result["macd_signal"] = self._finite_ewm(result["macd"], 9)
        result["macd_hist"] = result["macd"] - result["macd_signal"]
        result["macd_rel"] = result["macd"] / close
        result["macd_signal_rel"] = result["macd_signal"] / close
        result["macd_hist_rel"] = result["macd_hist"] / close
        stoch = ta_lib.momentum.StochasticOscillator(high, low, close)
        result["stoch_k"] = stoch.stoch()
        result["stoch_d"] = stoch.stoch_signal()

        previous_close = close.shift(1)
        true_range = pd.concat(
            [high - low, (high - previous_close).abs(), (low - previous_close).abs()], axis=1
        ).max(axis=1)
        result["atr_14"] = self._finite_ewm(
            true_range, 14, alpha=1.0 / 14.0, window=ceil(6.0 / (1.0 / 14.0))
        )
        result["atr_pct"] = result["atr_14"] / close
        bbands = ta_lib.volatility.BollingerBands(close, window=20, window_dev=2)
        result["bb_upper"] = bbands.bollinger_hband()
        result["bb_lower"] = bbands.bollinger_lband()
        result["bb_mid"] = bbands.bollinger_mavg()
        result["bb_pct"] = bbands.bollinger_pband()
        result["bb_width"] = (result["bb_upper"] - result["bb_lower"]) / result["bb_mid"]

        result["obv"] = ta_lib.volume.OnBalanceVolumeIndicator(
            close, volume
        ).on_balance_volume()
        rolling_volume_20 = volume.rolling(20).sum()
        result["obv_slope_20"] = (result["obv"] - result["obv"].shift(20)) / rolling_volume_20
        cumulative_volume = volume.cumsum()
        result["vwap"] = (close * volume).cumsum() / cumulative_volume
        vwap_20 = (close * volume).rolling(20).sum() / rolling_volume_20
        result["vwap_20_rel"] = close / vwap_20 - 1.0
        result["vol_ratio"] = volume / volume.rolling(20).mean()

        relative_columns = [
            "ema_9_rel", "ema_21_rel", "ema_50_rel", "macd_rel",
            "macd_signal_rel", "macd_hist_rel", "atr_pct", "bb_width",
            "obv_slope_20", "vwap_20_rel",
        ]
        result[relative_columns] = result[relative_columns].replace([np.inf, -np.inf], np.nan)

        future_return = close.shift(-TARGET_HORIZON_CANDLES) / close - 1
        result["future_return"] = future_return
        result["target"] = np.where(
            future_return.notna(), (future_return > TARGET_UP_THRESHOLD).astype(float), np.nan
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
