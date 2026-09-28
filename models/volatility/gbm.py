"""Histogram gradient boosting with chronological validation early stopping."""

from __future__ import annotations

from copy import deepcopy

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.metrics import mean_squared_error

from data.volatility import RANGE_ESTIMATORS, WINDOWS
from .common import ensure_features, finite_x, finite_xy, prediction_series


class GBMModel:
    def __init__(self, horizon: int = 4, patience: int = 50):
        self.horizon = horizon
        self.patience = patience
        self.feature_columns = [
            *[f"log_rv_{window}" for window in WINDOWS],
            *[
                f"log_{estimator}_{window}"
                for estimator in RANGE_ESTIMATORS
                for window in WINDOWS
            ],
            *[f"log_semivar_neg_{window}" for window in WINDOWS],
            "abs_return_24",
            "vol_ratio",
            "hour_utc",
            "day_of_week_utc",
        ]
        self.regressor: HistGradientBoostingRegressor | None = None
        self.best_iteration_: int | None = None

    def fit(self, train_df: pd.DataFrame, val_df: pd.DataFrame | None = None):
        train = ensure_features(train_df, self.horizon)
        x_train, y_train = finite_xy(train, self.feature_columns)
        if val_df is None:
            self.regressor = HistGradientBoostingRegressor(
                max_depth=3, learning_rate=0.05, max_iter=500, early_stopping=False,
                random_state=42,
            ).fit(x_train, y_train)
            self.best_iteration_ = 500
            return self

        val = ensure_features(val_df, self.horizon)
        x_val, y_val = finite_xy(val, self.feature_columns)
        model = HistGradientBoostingRegressor(
            max_depth=3,
            learning_rate=0.05,
            max_iter=1,
            early_stopping=False,
            warm_start=True,
            random_state=42,
        )
        best_mse = np.inf
        best_model = None
        best_iteration = 0
        stale_rounds = 0
        for iteration in range(1, 501):
            model.set_params(max_iter=iteration)
            model.fit(x_train, y_train)
            mse = mean_squared_error(y_val, model.predict(x_val))
            if mse < best_mse:
                best_mse = mse
                best_model = deepcopy(model)
                best_iteration = iteration
                stale_rounds = 0
            else:
                stale_rounds += 1
                if stale_rounds >= self.patience:
                    break
        self.regressor = best_model
        self.best_iteration_ = best_iteration
        return self

    def predict(self, df: pd.DataFrame) -> pd.Series:
        if self.regressor is None:
            raise RuntimeError("GBM debe ajustarse antes de predecir")
        frame = ensure_features(df, self.horizon)
        x, valid = finite_x(frame, self.feature_columns)
        return prediction_series(df, self.regressor.predict(x), valid)
