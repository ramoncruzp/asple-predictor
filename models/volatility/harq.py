"""HARQ OLS model fitted in realized-variance levels."""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.linear_model import LinearRegression

from data.volatility import build_volatility_frame
from .common import prediction_series


class HARQModel:
    """HARQ: RV_24 slope varies with the square root of 24-hour quarticity."""

    def __init__(self, horizon: int = 4):
        self.horizon = horizon
        self.regressor = LinearRegression(fit_intercept=True)
        self.coef_: np.ndarray | None = None
        self.intercept_: float | None = None

    def _frame(self, df: pd.DataFrame) -> pd.DataFrame:
        required = {"rv_1", "rv_24", "rv_168", "sqrt_rq_24"}
        if required.issubset(df.columns):
            return df.copy(deep=True)
        return build_volatility_frame(df, self.horizon)

    @staticmethod
    def _design(frame: pd.DataFrame) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "rv_24": frame["rv_24"],
                "sqrt_rq_24_x_rv_24": frame["sqrt_rq_24"] * frame["rv_24"],
                "rv_1": frame["rv_1"],
                "rv_168": frame["rv_168"],
            },
            index=frame.index,
        )

    def fit(self, train_df: pd.DataFrame, val_df: pd.DataFrame | None = None):
        frame = self._frame(train_df)
        target_column = f"future_var_{self.horizon}"
        if target_column not in frame:
            raise ValueError(f"Falta target HARQ {target_column}")
        design = self._design(frame).replace([np.inf, -np.inf], np.nan)
        target = pd.to_numeric(frame[target_column], errors="coerce")
        valid = design.notna().all(axis=1) & np.isfinite(target)
        if not valid.any():
            raise ValueError("No hay filas completas para ajustar HARQ")
        self.regressor.fit(design.loc[valid], target.loc[valid])
        self.coef_ = self.regressor.coef_.copy()
        self.intercept_ = float(self.regressor.intercept_)
        return self

    def predict(self, df: pd.DataFrame) -> pd.Series:
        frame = self._frame(df)
        design = self._design(frame).replace([np.inf, -np.inf], np.nan)
        valid = design.notna().all(axis=1)
        variance = np.full(len(frame), np.nan, dtype="float64")
        variance[valid.to_numpy()] = self.regressor.predict(design.loc[valid])
        logvol = 0.5 * np.log(np.maximum(variance, 1e-12))
        return pd.Series(logvol, index=df.index, name="predicted_logvol")
