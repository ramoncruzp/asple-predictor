"""Nexo-HAR: train-only UTC seasonality and deseasonalized HAR regression."""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.linear_model import LinearRegression

from data.volatility import (
    build_volatility_frame,
    seasonal_bucket_ids,
    utc_timestamps,
)
from .common import finite_x, prediction_series


class NexoHARModel:
    FEATURE_COLUMNS = [
        "log_d_mean_1",
        "log_d_mean_24",
        "log_d_mean_168",
        "log_d_median_24",
        "log_semivar_neg_24",
        "log_semivar_neg_168",
        "volume_surprise_log",
    ]

    def __init__(self, horizon: int = 4):
        self.horizon = horizon
        self.regressor = LinearRegression(fit_intercept=True)
        self.seasonal_factors_: dict[int, float] = {bucket: 1.0 for bucket in range(48)}
        self.bucket_counts_: dict[int, int] = {bucket: 0 for bucket in range(48)}
        self.coef_: np.ndarray | None = None
        self.intercept_: float | None = None

    def _frame(self, df: pd.DataFrame) -> pd.DataFrame:
        needed = {
            "parkinson_bar", "variance_per_hour", "log_return", "volume", "target_logvol",
            *(f"future_r2_{step}" for step in range(1, self.horizon + 1)),
        }
        if needed.issubset(df.columns) and "volatility_horizon" in df:
            if df["volatility_horizon"].eq(self.horizon).all():
                return df.copy(deep=True)
        return build_volatility_frame(df, self.horizon)

    def _fit_seasonality(self, train: pd.DataFrame) -> None:
        buckets = seasonal_bucket_ids(utc_timestamps(train))
        variance = pd.to_numeric(train["variance_per_hour"], errors="coerce")
        valid = variance.notna() & np.isfinite(variance) & (variance >= 0.0)
        baseline = float(variance.loc[valid].mean()) if valid.any() else 0.0
        factors: dict[int, float] = {}
        counts: dict[int, int] = {}
        for bucket in range(48):
            selected = valid & buckets.eq(bucket)
            count = int(selected.sum())
            counts[bucket] = count
            if count < 30 or baseline <= 0.0:
                factors[bucket] = 1.0
            else:
                factors[bucket] = float(variance.loc[selected].mean() / baseline)
        self.seasonal_factors_ = factors
        self.bucket_counts_ = counts

    def _features(self, frame: pd.DataFrame) -> pd.DataFrame:
        buckets = seasonal_bucket_ids(utc_timestamps(frame))
        seasonal = buckets.map(self.seasonal_factors_).astype("float64")
        deseasonalized = frame["variance_per_hour"].astype("float64") / seasonal
        features = pd.DataFrame(index=frame.index)
        for window in (1, 24, 168):
            mean = deseasonalized.rolling(window, min_periods=window).mean()
            features[f"log_d_mean_{window}"] = np.log(mean.clip(lower=0.0) + 1e-12)
        median_24 = deseasonalized.rolling(24, min_periods=24).median()
        features["log_d_median_24"] = np.log(median_24.clip(lower=0.0) + 1e-12)
        features["log_semivar_neg_24"] = frame["log_semivar_neg_24"]
        features["log_semivar_neg_168"] = frame["log_semivar_neg_168"]
        volume = pd.to_numeric(frame["volume"], errors="coerce").astype("float64")
        volume_mean_168 = volume.rolling(168, min_periods=168).mean()
        features["volume_surprise_log"] = np.log(
            (volume / volume_mean_168).clip(lower=1e-12)
        )
        return features

    def _target_deseasonalized(self, frame: pd.DataFrame) -> pd.Series:
        times = utc_timestamps(frame)
        parts = []
        for step in range(1, self.horizon + 1):
            future_bucket = seasonal_bucket_ids(times + pd.Timedelta(hours=step))
            future_factor = future_bucket.map(self.seasonal_factors_).astype("float64")
            parts.append(frame[f"future_r2_{step}"].astype("float64") / future_factor)
        future_variance = pd.concat(parts, axis=1).mean(axis=1, skipna=False)
        return (0.5 * np.log(future_variance + 1e-12)).rename("target_nexo_deseason")

    def fit(self, train_df: pd.DataFrame, val_df: pd.DataFrame | None = None):
        train = self._frame(train_df)
        self._fit_seasonality(train)
        features = self._features(train)[self.FEATURE_COLUMNS].replace([np.inf, -np.inf], np.nan)
        target = self._target_deseasonalized(train)
        valid = features.notna().all(axis=1) & np.isfinite(target)
        if not valid.any():
            raise ValueError("No hay filas completas para ajustar Nexo-HAR")
        self.regressor.fit(features.loc[valid], target.loc[valid])
        self.coef_ = self.regressor.coef_.copy()
        self.intercept_ = float(self.regressor.intercept_)
        return self

    def predict(self, df: pd.DataFrame) -> pd.Series:
        frame = self._frame(df)
        features = self._features(frame)
        x, valid = finite_x(features, self.FEATURE_COLUMNS)
        deseasonalized = prediction_series(df, self.regressor.predict(x), valid)
        times = utc_timestamps(frame)
        future_factors = []
        for step in range(1, self.horizon + 1):
            future_bucket = seasonal_bucket_ids(times + pd.Timedelta(hours=step))
            future_factors.append(future_bucket.map(self.seasonal_factors_).to_numpy(dtype="float64"))
        mean_future_factor = np.mean(np.vstack(future_factors), axis=0)
        output = deseasonalized + 0.5 * np.log(mean_future_factor)
        output.name = "predicted_logvol"
        return output
