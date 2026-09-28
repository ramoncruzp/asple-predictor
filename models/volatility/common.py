"""Shared feature preparation helpers for volatility estimators."""

from __future__ import annotations

import numpy as np
import pandas as pd

from data.volatility import build_volatility_frame, feature_columns


def ensure_features(df: pd.DataFrame, horizon: int) -> pd.DataFrame:
    expected = set(feature_columns(horizon)) | {"target_logvol"}
    correct_horizon = (
        "volatility_horizon" in df
        and df["volatility_horizon"].eq(horizon).all()
    )
    if expected.issubset(df.columns) and correct_horizon:
        return df.copy(deep=True)
    return build_volatility_frame(df, horizon)


def finite_xy(df: pd.DataFrame, columns: list[str]) -> tuple[pd.DataFrame, pd.Series]:
    missing = set(columns + ["target_logvol"]).difference(df.columns)
    if missing:
        raise ValueError(f"Faltan columnas para ajustar el modelo: {sorted(missing)}")
    values = df[columns + ["target_logvol"]].replace([np.inf, -np.inf], np.nan)
    valid = values.notna().all(axis=1)
    x = values.loc[valid, columns].astype("float64")
    y = values.loc[valid, "target_logvol"].astype("float64")
    if not len(y):
        raise ValueError("No hay filas completas para ajustar el modelo de volatilidad")
    return x, y


def finite_x(df: pd.DataFrame, columns: list[str]) -> tuple[pd.DataFrame, pd.Series]:
    missing = set(columns).difference(df.columns)
    if missing:
        raise ValueError(f"Faltan features para predecir: {sorted(missing)}")
    values = df[columns].replace([np.inf, -np.inf], np.nan)
    valid = values.notna().all(axis=1)
    return values.loc[valid].astype("float64"), valid


def prediction_series(df: pd.DataFrame, values: np.ndarray, valid: pd.Series) -> pd.Series:
    output = pd.Series(np.nan, index=df.index, dtype="float64", name="predicted_logvol")
    output.loc[valid] = np.asarray(values, dtype="float64")
    return output
