"""Model C: hybrid Prophet trend/seasonality plus XGBoost residual classifier."""

from __future__ import annotations

import inspect
import importlib.resources as resources
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from prophet import Prophet
from sklearn.metrics import accuracy_score, f1_score, precision_score, recall_score, roc_auc_score
from xgboost import XGBClassifier

from data.feature_engineer import FeatureEngineer
from models.base_model import BaseModel


class ModelC(BaseModel):
    """Hybrid Prophet plus XGBoost classifier."""

    MODEL_NAME = "model_c"
    MODEL_VERSION = "1.0"
    RESIDUAL_FEATURES = ["residual", "residual_rolling_5", "prophet_trend"]

    def __init__(self) -> None:
        self.feature_engineer = FeatureEngineer()
        self.feature_names = self.feature_engineer.get_feature_names() + self.RESIDUAL_FEATURES
        self.prophet_model: Prophet | None = None
        self.xgb_model: XGBClassifier | None = None
        self.feature_importances_: dict[str, float] = {}
        self.prophet_rmse: float | None = None
        self.last_updated: str | None = None
        self.cumulative_accuracy: float | None = None
        self.metrics_: dict[str, Any] = {}

    def _train_prophet(self, df: pd.DataFrame) -> tuple[Prophet, pd.DataFrame]:
        """Fit Prophet and return its fitted historical forecast."""
        prophet_data = self._prophet_frame(df)
        model = self._new_prophet()
        model.fit(prophet_data)
        forecast = model.predict(prophet_data[["ds"]])
        return model, forecast

    @staticmethod
    def _new_prophet() -> Prophet:
        """Create Prophet, using its bundled compiled model when CmdStan is unbuilt."""
        kwargs = {
            "yearly_seasonality": True,
            "weekly_seasonality": True,
            "daily_seasonality": False,
            "changepoint_prior_scale": 0.05,
        }
        bundled_model = resources.files("prophet") / "stan_model" / "prophet_model.bin"
        if bundled_model.exists():
            # Prophet's Windows wheel needs the MinGW runtime DLLs when the
            # bundled Stan executable is launched by cmdstanpy. Make the
            # user-scoped WinLibs installation visible to that child process.
            if shutil.which("mingw32-make") is None:
                winget_root = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Packages"
                for make_file in winget_root.glob("**/mingw64/bin/mingw32-make.exe"):
                    os.environ["PATH"] = f"{make_file.parent};{os.environ.get('PATH', '')}"
                    break
            # The Windows Prophet wheel's bundled Stan executable depends on
            # the TBB DLLs shipped beside its bundled CmdStan tree.
            tbb_dir = Path(str(resources.files("prophet"))) / "stan_model" / "cmdstan-2.33.1" / "stan" / "lib" / "stan_math" / "lib" / "tbb"
            if tbb_dir.exists():
                os.environ["PATH"] = f"{tbb_dir};{os.environ.get('PATH', '')}"
            import cmdstanpy

            original_set_path = cmdstanpy.set_cmdstan_path
            cmdstanpy.set_cmdstan_path = lambda _path: None
            try:
                return Prophet(**kwargs)
            finally:
                cmdstanpy.set_cmdstan_path = original_set_path
        return Prophet(**kwargs)

    @staticmethod
    def _prophet_frame(df: pd.DataFrame) -> pd.DataFrame:
        if "close" not in df.columns:
            raise ValueError("El DataFrame debe contener la columna close para Prophet")
        if "timestamp" in df.columns:
            ds = pd.to_datetime(df["timestamp"], utc=True).dt.tz_localize(None).reset_index(drop=True)
        else:
            ds = pd.Series(pd.to_datetime(df.index, utc=True).tz_localize(None)).reset_index(drop=True)
        return pd.DataFrame({"ds": ds, "y": df["close"].astype(float).reset_index(drop=True)})

    def _compute_residuals(self, df: pd.DataFrame, prophet_model: Prophet) -> pd.DataFrame:
        """Add fitted residual and Prophet trend columns to a copy of df."""
        result = df.copy(deep=True)
        prophet_data = self._prophet_frame(result)
        forecast = prophet_model.predict(prophet_data[["ds"]])
        result["residual"] = result["close"].astype(float).to_numpy() - forecast["yhat"].to_numpy()
        result["residual_rolling_5"] = result["residual"].rolling(5).mean()
        result["prophet_trend"] = forecast["trend"].to_numpy()
        return result

    def _prepare_hybrid_data(self, df: pd.DataFrame, prophet_model: Prophet) -> pd.DataFrame:
        residuals = self._compute_residuals(df, prophet_model)
        technical = self.feature_engineer.compute_features(df)
        if "timestamp" in technical.columns:
            hybrid = technical.merge(
                residuals[["timestamp", *self.RESIDUAL_FEATURES]], on="timestamp", how="left"
            )
        else:
            hybrid = technical.copy()
            for name in self.RESIDUAL_FEATURES:
                hybrid[name] = residuals.loc[technical.index, name].to_numpy()
        return hybrid.dropna(subset=[*self.feature_names, "target"]).reset_index(drop=True)

    @staticmethod
    def _new_xgb(scale_pos_weight: float) -> XGBClassifier:
        params: dict[str, Any] = {
            "n_estimators": 300,
            "max_depth": 6,
            "learning_rate": 0.05,
            "subsample": 0.8,
            "colsample_bytree": 0.8,
            "scale_pos_weight": scale_pos_weight,
            "eval_metric": "logloss",
            "random_state": 42,
        }
        return XGBClassifier(**params)

    def train(self, df: pd.DataFrame) -> dict:
        """Fit Prophet on all history, then train temporal XGBoost on hybrid features."""
        self.prophet_model, _forecast = self._train_prophet(df)
        residual_df = self._compute_residuals(df, self.prophet_model)
        self.prophet_rmse = float(np.sqrt(np.mean(np.square(residual_df["residual"].dropna()))))
        prepared = self._prepare_hybrid_data(df, self.prophet_model)
        if len(prepared) < 10:
            raise ValueError("Se requieren al menos 10 filas limpias para entrenar ModelC")
        X = prepared[self.feature_names].astype(float)
        y = prepared["target"].astype("int8")
        split_at = int(len(prepared) * 0.8)
        X_train, X_test = X.iloc[:split_at], X.iloc[split_at:]
        y_train, y_test = y.iloc[:split_at], y.iloc[split_at:]
        if y_train.nunique() < 2 or y_test.nunique() < 2:
            raise ValueError("Cada tramo temporal debe contener ambas clases")
        positives = int((y_train == 1).sum())
        negatives = int((y_train == 0).sum())
        self.xgb_model = self._new_xgb(negatives / positives if positives else 1.0)
        fit_kwargs: dict[str, Any] = {"eval_set": [(X_test, y_test)], "verbose": False}
        if "early_stopping_rounds" in inspect.signature(self.xgb_model.fit).parameters:
            fit_kwargs["early_stopping_rounds"] = 50
        else:
            self.xgb_model.set_params(early_stopping_rounds=50)
        self.xgb_model.fit(X_train, y_train, **fit_kwargs)
        probabilities = self.xgb_model.predict_proba(X_test)[:, 1]
        predictions = (probabilities >= 0.5).astype("int8")
        trained_at = datetime.now(timezone.utc).isoformat()
        metrics = {
            "accuracy": float(accuracy_score(y_test, predictions)),
            "f1": float(f1_score(y_test, predictions, zero_division=0)),
            "roc_auc": float(roc_auc_score(y_test, probabilities)),
            "precision": float(precision_score(y_test, predictions, zero_division=0)),
            "recall": float(recall_score(y_test, predictions, zero_division=0)),
            "prophet_rmse": self.prophet_rmse,
            "trained_at": trained_at,
        }
        self.feature_importances_ = dict(
            zip(self.feature_names, self.xgb_model.feature_importances_.astype(float))
        )
        self.metrics_ = metrics
        self.last_updated = trained_at
        self.cumulative_accuracy = metrics["accuracy"]
        return metrics

    def _future_prophet_trend(self, df: pd.DataFrame) -> float:
        if self.prophet_model is None:
            raise RuntimeError("ModelC debe entrenarse o cargarse antes de predecir")
        prophet_data = self._prophet_frame(df)
        if len(prophet_data) < 2:
            raise ValueError("Se requieren al menos dos timestamps para proyectar Prophet")
        step = prophet_data["ds"].diff().dropna().median()
        future = self.prophet_model.make_future_dataframe(
            periods=4, freq=pd.tseries.frequencies.to_offset(step), include_history=True
        )
        forecast = self.prophet_model.predict(future)
        close_actual = float(df["close"].iloc[-1])
        return float((forecast["yhat"].iloc[-1] - close_actual) / close_actual)

    def predict(self, df: pd.DataFrame) -> dict:
        """Combine four-candle Prophet direction with XGBoost probability."""
        if self.prophet_model is None or self.xgb_model is None:
            raise RuntimeError("ModelC debe entrenarse o cargarse antes de predecir")
        prophet_trend = self._future_prophet_trend(df)
        hybrid = self._prepare_hybrid_data(df, self.prophet_model)
        if hybrid.empty:
            raise ValueError("No hay filas híbridas disponibles para predecir")
        latest = hybrid.iloc[[-1]]
        xgb_probability = float(self.xgb_model.predict_proba(latest[self.feature_names])[:, 1][0])
        up_agreement = prophet_trend > 0 and xgb_probability > 0.55
        down_agreement = prophet_trend < 0 and xgb_probability < 0.45
        disagreement = (prophet_trend > 0 and xgb_probability < 0.45) or (
            prophet_trend < 0 and xgb_probability > 0.55
        )
        if up_agreement:
            signal, confidence = "ALCISTA", "alta"
        elif down_agreement:
            signal, confidence = "BAJISTA", "alta"
        elif disagreement:
            signal, confidence = "NEUTRAL", "baja"
        elif 0.45 <= xgb_probability <= 0.55:
            signal, confidence = "NEUTRAL", "baja"
        else:
            signal = "ALCISTA" if xgb_probability > 0.55 else "BAJISTA"
            confidence = "media"
        top_features = sorted(self.feature_importances_.items(), key=lambda item: item[1], reverse=True)[:5]
        top_features = [(name, float(importance)) for name, importance in top_features]
        timestamp: Any = df["timestamp"].iloc[-1] if "timestamp" in df else datetime.now(timezone.utc)
        if isinstance(timestamp, pd.Timestamp):
            timestamp = timestamp.to_pydatetime()
        return {
            "model": self.MODEL_NAME,
            "probability_up": xgb_probability,
            "probability_down": float(1.0 - xgb_probability),
            "signal": signal,
            "confidence": confidence,
            "top_features": top_features,
            "timestamp": timestamp,
            "prophet_trend": prophet_trend,
        }

    def save(self, path: str) -> None:
        if self.prophet_model is None or self.xgb_model is None:
            raise RuntimeError("No se puede guardar ModelC antes de entrenarlo")
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump({
            "prophet_model": self.prophet_model,
            "xgb_model": self.xgb_model,
            "feature_names": self.feature_names,
            "feature_importances": self.feature_importances_,
            "prophet_rmse": self.prophet_rmse,
            "last_updated": self.last_updated,
            "cumulative_accuracy": self.cumulative_accuracy,
            "metrics": self.metrics_,
        }, target)

    def load(self, path: str) -> None:
        artifact = joblib.load(path)
        self.prophet_model = artifact["prophet_model"]
        self.xgb_model = artifact["xgb_model"]
        self.feature_names = list(artifact.get("feature_names", self.feature_names))
        self.feature_importances_ = dict(artifact.get("feature_importances", {}))
        self.prophet_rmse = artifact.get("prophet_rmse")
        self.last_updated = artifact.get("last_updated")
        self.cumulative_accuracy = artifact.get("cumulative_accuracy")
        self.metrics_ = dict(artifact.get("metrics", {}))

    def get_model_info(self) -> dict:
        return {
            "nombre": "Prophet+XGBoost",
            "version": self.MODEL_VERSION,
            "ultima_actualizacion": self.last_updated,
            "accuracy_acumulada": self.cumulative_accuracy,
        }
