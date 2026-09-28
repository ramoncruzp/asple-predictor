"""Exponentially weighted realized-variance baseline."""

from __future__ import annotations

import numpy as np
import pandas as pd

from data.volatility import EPSILON
from .common import ensure_features


class EWMAModel:
    def __init__(self, horizon: int = 4, lam: float = 0.94):
        if not 0.0 < lam < 1.0:
            raise ValueError("lam debe estar entre 0 y 1")
        self.horizon = horizon
        self.lam = lam

    def fit(self, train_df: pd.DataFrame, val_df: pd.DataFrame | None = None):
        ensure_features(train_df, self.horizon)
        return self

    def predict(self, df: pd.DataFrame) -> pd.Series:
        frame = ensure_features(df, self.horizon)
        variance = frame["log_return"].pow(2).ewm(
            alpha=1.0 - self.lam, adjust=False, min_periods=1
        ).mean()
        return np.log(np.sqrt(variance.clip(lower=0.0)) + EPSILON).rename("predicted_logvol")
