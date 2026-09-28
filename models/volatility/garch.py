"""Fixed-parameter GARCH(1,1)-t volatility forecasts."""

from __future__ import annotations

import numpy as np
import pandas as pd

from data.volatility import EPSILON
from .common import ensure_features


class GARCHModel:
    def __init__(self, horizon: int = 4):
        self.horizon = horizon
        self.params: pd.Series | None = None

    def fit(self, train_df: pd.DataFrame, val_df: pd.DataFrame | None = None):
        from arch import arch_model

        train = ensure_features(train_df, self.horizon)
        returns = train["log_return"].replace([np.inf, -np.inf], np.nan).dropna() * 100.0
        if len(returns) < 50:
            raise ValueError("GARCH requiere al menos 50 rendimientos de entrenamiento")
        fitted = arch_model(
            returns, mean="Zero", vol="GARCH", p=1, q=1, dist="t", rescale=False
        ).fit(disp="off")
        self.params = fitted.params.copy()
        return self

    def predict(self, df: pd.DataFrame) -> pd.Series:
        from arch import arch_model

        if self.params is None:
            raise RuntimeError("GARCH debe ajustarse antes de predecir")
        frame = ensure_features(df, self.horizon)
        returns = frame["log_return"].replace([np.inf, -np.inf], np.nan).dropna() * 100.0
        model = arch_model(
            returns, mean="Zero", vol="GARCH", p=1, q=1, dist="t", rescale=False
        )
        fixed = model.fix(self.params)
        forecast = fixed.forecast(horizon=self.horizon, start=0, reindex=True)
        hourly_variance = forecast.variance.mean(axis=1) / 10000.0
        predicted = np.log(np.sqrt(hourly_variance.clip(lower=0.0)) + EPSILON)
        output = pd.Series(np.nan, index=df.index, dtype="float64", name="predicted_logvol")
        output.loc[predicted.index] = predicted.to_numpy(dtype="float64")
        return output
