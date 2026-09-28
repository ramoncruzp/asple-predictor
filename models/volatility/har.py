"""HAR realized-volatility, range, and asymmetric OLS models."""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.linear_model import LinearRegression
from sklearn.metrics import mean_squared_error

from data.volatility import RANGE_ESTIMATORS, WINDOWS
from .common import ensure_features, finite_x, finite_xy, prediction_series


class HARModel:
    """OLS HAR: target log volatility on log RV at 1, 24, and 168 hours."""

    FEATURE_COLUMNS = [f"log_rv_{window}" for window in WINDOWS]

    def __init__(self, horizon: int = 4):
        self.horizon = horizon
        self.regressor = LinearRegression(fit_intercept=True)
        self.coef_: np.ndarray | None = None
        self.intercept_: float | None = None

    def fit(self, train_df: pd.DataFrame, val_df: pd.DataFrame | None = None):
        needed = set(self.FEATURE_COLUMNS + ["target_logvol"])
        frame = train_df if needed.issubset(train_df.columns) else ensure_features(train_df, self.horizon)
        x, y = finite_xy(frame, self.FEATURE_COLUMNS)
        self.regressor.fit(x, y)
        self.coef_ = self.regressor.coef_.copy()
        self.intercept_ = float(self.regressor.intercept_)
        return self

    def predict(self, df: pd.DataFrame) -> pd.Series:
        frame = df if set(self.FEATURE_COLUMNS).issubset(df.columns) else ensure_features(df, self.horizon)
        x, valid = finite_x(frame, self.FEATURE_COLUMNS)
        return prediction_series(df, self.regressor.predict(x), valid)


class HARRangeModel:
    """HAR-range model, selecting one range estimator by validation MSE only."""

    def __init__(self, horizon: int = 4):
        self.horizon = horizon
        self.selected_estimator: str | None = None
        self.selection_mse_val: dict[str, float] = {}
        self.regressor: LinearRegression | None = None
        self.feature_columns: list[str] = []

    def fit(self, train_df: pd.DataFrame, val_df: pd.DataFrame | None = None):
        train = ensure_features(train_df, self.horizon)
        val = ensure_features(val_df, self.horizon) if val_df is not None else None
        candidates: dict[str, tuple[LinearRegression, list[str]]] = {}
        for estimator in RANGE_ESTIMATORS:
            columns = [f"log_{estimator}_{window}" for window in WINDOWS]
            x_train, y_train = finite_xy(train, columns)
            regressor = LinearRegression(fit_intercept=True).fit(x_train, y_train)
            candidates[estimator] = (regressor, columns)
            if val is not None:
                x_val, y_val = finite_xy(val, columns)
                self.selection_mse_val[estimator] = float(
                    mean_squared_error(y_val, regressor.predict(x_val))
                )
        if self.selection_mse_val:
            selected = min(self.selection_mse_val, key=self.selection_mse_val.get)
        else:
            selected = RANGE_ESTIMATORS[0]
        self.selected_estimator = selected
        self.regressor, self.feature_columns = candidates[selected]
        return self

    def predict(self, df: pd.DataFrame) -> pd.Series:
        if self.regressor is None:
            raise RuntimeError("HAR_range debe ajustarse antes de predecir")
        frame = ensure_features(df, self.horizon)
        x, valid = finite_x(frame, self.feature_columns)
        return prediction_series(df, self.regressor.predict(x), valid)


class HARAsymModel:
    """HAR with 24-hour and 168-hour negative semivariance terms."""

    FEATURE_COLUMNS = [
        *[f"log_rv_{window}" for window in WINDOWS],
        "log_semivar_neg_24",
        "log_semivar_neg_168",
    ]

    def __init__(self, horizon: int = 4):
        self.horizon = horizon
        self.regressor = LinearRegression(fit_intercept=True)

    def fit(self, train_df: pd.DataFrame, val_df: pd.DataFrame | None = None):
        needed = set(self.FEATURE_COLUMNS + ["target_logvol"])
        frame = train_df if needed.issubset(train_df.columns) else ensure_features(train_df, self.horizon)
        x, y = finite_xy(frame, self.FEATURE_COLUMNS)
        self.regressor.fit(x, y)
        self.coef_ = self.regressor.coef_.copy()
        self.intercept_ = float(self.regressor.intercept_)
        return self

    def predict(self, df: pd.DataFrame) -> pd.Series:
        frame = df if set(self.FEATURE_COLUMNS).issubset(df.columns) else ensure_features(df, self.horizon)
        x, valid = finite_x(frame, self.FEATURE_COLUMNS)
        return prediction_series(df, self.regressor.predict(x), valid)
