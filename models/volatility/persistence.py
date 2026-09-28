"""Realized-volatility persistence baseline."""

from __future__ import annotations

import numpy as np
import pandas as pd

from data.volatility import EPSILON, build_volatility_frame
from .common import ensure_features


class PersistenceModel:
    def __init__(self, horizon: int = 4):
        self.horizon = horizon

    def fit(self, train_df: pd.DataFrame, val_df: pd.DataFrame | None = None):
        ensure_features(train_df, self.horizon)
        return self

    def predict(self, df: pd.DataFrame) -> pd.Series:
        frame = ensure_features(df, self.horizon)
        variance = frame[f"rv_{self.horizon}"].clip(lower=0.0)
        return np.log(np.sqrt(variance) + EPSILON).rename("predicted_logvol")
