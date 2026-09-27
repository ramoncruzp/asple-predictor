"""Model A: temporal binary classifier based on XGBoost."""

from __future__ import annotations

import inspect
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import joblib
import pandas as pd
from sklearn.metrics import (
    accuracy_score,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
)
from sklearn.preprocessing import StandardScaler
from xgboost import XGBClassifier

from data.feature_engineer import FeatureEngineer
from data.splits import chronological_split
from models.base_model import BaseModel


class ModelA(BaseModel):
    """XGBoost model for the four-candle, 0.5% upward target."""

    MODEL_NAME = "model_a"
    MODEL_VERSION = "1.0"

    def __init__(self) -> None:
        self.feature_engineer = FeatureEngineer()
        self.feature_names = self.feature_engineer.get_feature_names()
        self.model: XGBClassifier | None = None
        self.scaler: StandardScaler | None = None
        self.feature_importances_: dict[str, float] = {}
        self.last_updated: str | None = None
        self.cumulative_accuracy: float | None = None
        self.metrics_: dict[str, Any] = {}

    def _new_classifier(self, scale_pos_weight: float) -> XGBClassifier:
        """Create the requested classifier parameters for installed XGBoost."""
        parameters: dict[str, Any] = {
            "n_estimators": 300,
            "max_depth": 6,
            "learning_rate": 0.05,
            "subsample": 0.8,
            "colsample_bytree": 0.8,
            "scale_pos_weight": scale_pos_weight,
            "eval_metric": "logloss",
            "random_state": 42,
        }
        return XGBClassifier(**parameters)

    def train(self, df: pd.DataFrame) -> dict:
        """Train with chronological, embargoed train/validation/test partitions."""
        prepared = self.feature_engineer.prepare_for_model(df)
        train_slice, val_slice, test_slice = chronological_split(len(prepared))

        X = prepared[self.feature_names].astype("float64")
        y = prepared["target"].astype("int8")
        X_train, X_val, X_test = X.iloc[train_slice], X.iloc[val_slice], X.iloc[test_slice]
        y_train, y_val, y_test = y.iloc[train_slice], y.iloc[val_slice], y.iloc[test_slice]
        if y_train.nunique() < 2:
            raise ValueError("El tramo de entrenamiento contiene una sola clase")
        if y_test.nunique() < 2:
            raise ValueError("El tramo test contiene una sola clase")

        negatives = int((y_train == 0).sum())
        positives = int((y_train == 1).sum())
        scale_pos_weight = negatives / positives if positives else 1.0
        self.scaler = StandardScaler()
        X_train_scaled = self.scaler.fit_transform(X_train)
        X_val_scaled = self.scaler.transform(X_val)
        X_test_scaled = self.scaler.transform(X_test)
        self.model = self._new_classifier(scale_pos_weight)

        fit_kwargs: dict[str, Any] = {
            "eval_set": [(X_val_scaled, y_val)],
            "verbose": False,
        }
        if "early_stopping_rounds" in inspect.signature(self.model.fit).parameters:
            fit_kwargs["early_stopping_rounds"] = 50
        else:
            self.model.set_params(early_stopping_rounds=50)
        self.model.fit(X_train_scaled, y_train, **fit_kwargs)

        probabilities = self.model.predict_proba(X_test_scaled)[:, 1]
        predictions = (probabilities >= 0.5).astype("int8")
        signal_mask = (probabilities > 0.55) | (probabilities < 0.45)
        signal_predictions = (probabilities[signal_mask] > 0.55).astype("int8")
        positive_rate_test = float(y_test.mean())
        trained_at = datetime.now(timezone.utc).isoformat()
        metrics = {
            "accuracy": float(accuracy_score(y_test, predictions)),
            "precision": float(precision_score(y_test, predictions, zero_division=0)),
            "recall": float(recall_score(y_test, predictions, zero_division=0)),
            "f1": float(f1_score(y_test, predictions, zero_division=0)),
            "roc_auc": float(roc_auc_score(y_test, probabilities)),
            "positive_rate_test": positive_rate_test,
            "baseline_accuracy": max(positive_rate_test, 1.0 - positive_rate_test),
            "signal_coverage": float(signal_mask.mean()),
            "signal_accuracy": (
                float(accuracy_score(y_test.to_numpy()[signal_mask], signal_predictions))
                if signal_mask.any() else None
            ),
            "n_train": len(y_train),
            "n_val": len(y_val),
            "n_test": len(y_test),
            "trained_at": trained_at,
        }
        self.feature_importances_ = dict(
            zip(self.feature_names, self.model.feature_importances_.astype(float))
        )
        self.last_updated = trained_at
        self.cumulative_accuracy = metrics["signal_accuracy"]
        metrics["best_iteration"] = int(getattr(self.model, "best_iteration", self.model.n_estimators - 1))
        metrics["final_fit_rows"] = len(prepared)
        metrics["cumulative_accuracy_source"] = (
            "signal_accuracy" if metrics["signal_accuracy"] is not None else "accuracy"
        )
        self.cumulative_accuracy = metrics[metrics["cumulative_accuracy_source"]]
        final_estimators = metrics["best_iteration"] + 1
        self.scaler = StandardScaler().fit(X)
        self.model = self._new_classifier(
            int((y == 0).sum()) / int((y == 1).sum()) if int((y == 1).sum()) else 1.0
        )
        self.model.set_params(n_estimators=final_estimators)
        self.model.fit(self.scaler.transform(X), y, verbose=False)
        self.feature_importances_ = dict(
            zip(self.feature_names, self.model.feature_importances_.astype(float))
        )
        self.metrics_ = metrics
        return metrics

    def predict(self, df: pd.DataFrame) -> dict:
        """Predict the most recent engineered candle."""
        if self.model is None or self.scaler is None:
            raise RuntimeError("ModelA debe entrenarse o cargarse antes de predecir")
        engineered = self.feature_engineer.compute_features(df)
        if engineered.empty:
            raise ValueError("No hay filas disponibles después del calentamiento de features")
        latest = engineered.iloc[[-1]]
        X_latest = self.scaler.transform(latest[self.feature_names].astype("float64"))
        probability_up = float(self.model.predict_proba(X_latest)[0, 1])
        probability_down = float(1.0 - probability_up)
        if 0.45 <= probability_up <= 0.55:
            signal = "NEUTRAL"
        elif probability_up > 0.55:
            signal = "ALCISTA"
        else:
            signal = "BAJISTA"
        confidence_distance = abs(probability_up - 0.5)
        confidence = "alta" if confidence_distance >= 0.25 else "media" if confidence_distance >= 0.10 else "baja"
        top_features = sorted(
            self.feature_importances_.items(), key=lambda item: item[1], reverse=True
        )[:5]
        top_features = [(name, float(importance)) for name, importance in top_features]
        timestamp = latest["timestamp"].iloc[0] if "timestamp" in latest else datetime.now(timezone.utc)
        if isinstance(timestamp, pd.Timestamp):
            timestamp = timestamp.to_pydatetime()
        return {
            "model": self.MODEL_NAME,
            "probability_up": probability_up,
            "probability_down": probability_down,
            "signal": signal,
            "confidence": confidence,
            "top_features": top_features,
            "timestamp": timestamp,
        }

    def save(self, path: str) -> None:
        """Save model, scaler, feature names, and metadata with joblib."""
        if self.model is None or self.scaler is None:
            raise RuntimeError("No se puede guardar ModelA antes de entrenarlo")
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump(
            {
                "model": self.model,
                "scaler": self.scaler,
                "feature_set_version": self.feature_engineer.FEATURE_SET_VERSION,
                "feature_names": self.feature_names,
                "feature_importances": self.feature_importances_,
                "last_updated": self.last_updated,
                "cumulative_accuracy": self.cumulative_accuracy,
                "metrics": self.metrics_,
            },
            target,
        )

    def load(self, path: str) -> None:
        """Load a previously saved ModelA artifact."""
        artifact = joblib.load(path)
        version = artifact.get("feature_set_version")
        if version != self.feature_engineer.FEATURE_SET_VERSION:
            raise ValueError(
                f"Feature set incompatible en artefacto ModelA: {version!r}; "
                f"se requiere {self.feature_engineer.FEATURE_SET_VERSION!r}"
            )
        required = {"model", "scaler", "feature_names"}
        missing = required.difference(artifact)
        if missing:
            raise ValueError(f"Artefacto ModelA incompleto; faltan: {sorted(missing)}")
        artifact_features = list(artifact["feature_names"])
        if artifact_features != self.feature_names:
            raise KeyError("Las features del artefacto ModelA no coinciden con las actuales")
        self.model = artifact["model"]
        self.scaler = artifact["scaler"]
        self.feature_importances_ = dict(artifact.get("feature_importances", {}))
        self.last_updated = artifact.get("last_updated")
        self.cumulative_accuracy = artifact.get("cumulative_accuracy")
        self.metrics_ = dict(artifact.get("metrics", {}))

    def get_model_info(self) -> dict:
        """Return model identity and latest cumulative accuracy."""
        return {
            "nombre": "XGBoost",
            "version": self.MODEL_VERSION,
            "ultima_actualizacion": self.last_updated,
            "accuracy_acumulada": self.cumulative_accuracy,
        }
