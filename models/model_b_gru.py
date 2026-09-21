"""Model B: a temporal GRU classifier implemented with PyTorch."""

from __future__ import annotations

import copy
import ctypes
import os
import pathlib
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Windows: pre-cargar DLLs de torch para evitar WinError 1114 en c10.dll.
_torch_dll_handle = None
if sys.platform == "win32":
    try:
        _torch_lib = (
            pathlib.Path(sys.executable).parent.parent
            / "Lib" / "site-packages" / "torch" / "lib"
        )
        if _torch_lib.exists():
            if hasattr(os, "add_dll_directory"):
                _torch_dll_handle = os.add_dll_directory(str(_torch_lib))
            ctypes.CDLL(str(_torch_lib / "c10.dll"))
    except Exception as _dll_err:
        pass

import numpy as np
import pandas as pd
from sklearn.metrics import accuracy_score, f1_score, precision_score, recall_score, roc_auc_score
from sklearn.preprocessing import MinMaxScaler

try:
    import torch
    import torch.nn as nn
    from torch.utils.data import DataLoader, TensorDataset
    TORCH_AVAILABLE = True
except (ImportError, OSError) as e:
    print(f"[ModelB-GRU] PyTorch no disponible: {e}")
    TORCH_AVAILABLE = False
    torch = None
    nn = None
    DataLoader = None
    TensorDataset = None

from data.feature_engineer import FeatureEngineer
from models.base_model import BaseModel


if TORCH_AVAILABLE:
    class GRUNet(nn.Module):
        """GRU network for sequences shaped ``(batch, sequence_len, features)``."""

        def __init__(self, n_features: int, hidden_size: int = 128, num_layers: int = 2,
                     dropout: float = 0.3) -> None:
            super().__init__()
            self.gru = nn.GRU(n_features, hidden_size, num_layers=num_layers,
                              batch_first=True, dropout=dropout, bidirectional=False)
            self.classifier = nn.Sequential(
                nn.Linear(hidden_size, 64), nn.ReLU(), nn.Dropout(0.2),
                nn.Linear(64, 1), nn.Sigmoid()
            )

        def forward(self, x: torch.Tensor) -> torch.Tensor:
            output, _ = self.gru(x)
            return self.classifier(output[:, -1, :])
else:
    class GRUNet:
        def __init__(self, *a, **kw):
            pass


class ModelB(BaseModel):
    """GRU model for the four-candle, 0.5% upward target."""

    MODEL_NAME = "model_b"
    MODEL_VERSION = "1.0"
    SEQUENCE_LEN = 24
    HIDDEN_SIZE = 128
    NUM_LAYERS = 2
    GRU_DROPOUT = 0.3
    HEAD_DROPOUT = 0.2
    MAX_EPOCHS = 100
    EARLY_STOPPING_PATIENCE = 15
    BATCH_SIZE = 32
    LEARNING_RATE = 0.001
    WEIGHT_DECAY = 1e-4

    def __init__(self) -> None:
        self.feature_engineer = FeatureEngineer()
        self.feature_names = self.feature_engineer.get_feature_names()
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu") if TORCH_AVAILABLE else None
        self.scaler: MinMaxScaler | None = None
        self.model: GRUNet | None = None
        self.training_history: dict[str, list[float]] = {"train_loss": [], "val_loss": []}
        self.last_updated: str | None = None
        self.cumulative_accuracy: float | None = None
        self.metrics_: dict[str, Any] = {}

    def _prepare_sequences(self, df: pd.DataFrame, scaler: MinMaxScaler | None = None) -> tuple[torch.Tensor, torch.Tensor]:
        """Build 24-candle sequences and binary labels as CPU tensors."""
        if not set(self.feature_names).issubset(df.columns) or "target" not in df.columns:
            prepared = self.feature_engineer.prepare_for_model(df)
        else:
            prepared = df.copy(deep=True)
        prepared = prepared.dropna(subset=[*self.feature_names, "target"]).reset_index(drop=True)
        if len(prepared) < self.SEQUENCE_LEN:
            raise ValueError(f"Se requieren al menos {self.SEQUENCE_LEN} filas para crear secuencias GRU")
        feature_frame = prepared[self.feature_names].astype("float32")
        if scaler is None:
            scaler = MinMaxScaler()
            values = scaler.fit_transform(feature_frame)
        else:
            values = scaler.transform(feature_frame)
        targets = prepared["target"].astype("float32").to_numpy()
        X = np.stack([values[i - self.SEQUENCE_LEN + 1:i + 1] for i in range(self.SEQUENCE_LEN - 1, len(values))])
        y = targets[self.SEQUENCE_LEN - 1:].reshape(-1, 1)
        return torch.tensor(X, dtype=torch.float32), torch.tensor(y, dtype=torch.float32)

    def _make_model(self) -> GRUNet:
        return GRUNet(len(self.feature_names), self.HIDDEN_SIZE, self.NUM_LAYERS, self.GRU_DROPOUT).to(self.device)

    def train(self, df: pd.DataFrame) -> dict:
        """Train with chronological sequences and validation early stopping."""
        torch.manual_seed(42)
        if self.device.type == "cuda":
            torch.cuda.manual_seed_all(42)
        prepared = self.feature_engineer.prepare_for_model(df)
        if len(prepared) < self.SEQUENCE_LEN + 10:
            raise ValueError("No hay suficientes datos limpios para entrenar ModelB")
        row_split = int(len(prepared) * 0.8)
        self.scaler = MinMaxScaler()
        self.scaler.fit(prepared.iloc[:row_split][self.feature_names].astype("float32"))
        X, y = self._prepare_sequences(prepared, self.scaler)
        sequence_split = row_split - self.SEQUENCE_LEN + 1
        if sequence_split <= 0 or sequence_split >= len(X):
            raise ValueError("No se pudo crear el split temporal 80/20 para las secuencias")
        X_train, X_test = X[:sequence_split], X[sequence_split:]
        y_train, y_test = y[:sequence_split], y[sequence_split:]
        if y_train.unique().numel() < 2 or y_test.unique().numel() < 2:
            raise ValueError("Cada tramo temporal debe contener ambas clases")
        loader = DataLoader(TensorDataset(X_train, y_train), batch_size=self.BATCH_SIZE, shuffle=False)
        self.model = self._make_model()
        optimizer = torch.optim.Adam(self.model.parameters(), lr=self.LEARNING_RATE, weight_decay=self.WEIGHT_DECAY)
        loss_function = nn.BCELoss()
        best_state: dict[str, torch.Tensor] | None = None
        best_val_loss = float("inf")
        stale_epochs = 0
        self.training_history = {"train_loss": [], "val_loss": []}
        X_test_device, y_test_device = X_test.to(self.device), y_test.to(self.device)
        for _epoch in range(self.MAX_EPOCHS):
            self.model.train()
            total_loss, count = 0.0, 0
            for batch_X, batch_y in loader:
                batch_X, batch_y = batch_X.to(self.device), batch_y.to(self.device)
                optimizer.zero_grad()
                loss = loss_function(self.model(batch_X), batch_y)
                loss.backward()
                optimizer.step()
                total_loss += float(loss.item()) * len(batch_X)
                count += len(batch_X)
            self.model.eval()
            with torch.no_grad():
                val_loss = float(loss_function(self.model(X_test_device), y_test_device).item())
            train_loss = total_loss / count
            self.training_history["train_loss"].append(float(train_loss))
            self.training_history["val_loss"].append(val_loss)
            if val_loss < best_val_loss - 1e-8:
                best_val_loss = val_loss
                best_state = copy.deepcopy(self.model.state_dict())
                stale_epochs = 0
            else:
                stale_epochs += 1
                if stale_epochs >= self.EARLY_STOPPING_PATIENCE:
                    break
        if best_state is not None:
            self.model.load_state_dict(best_state)
        self.model.eval()
        with torch.no_grad():
            probabilities = self.model(X_test_device).squeeze(1).cpu().numpy()
        actual = y_test.squeeze(1).numpy().astype("int8")
        predictions = (probabilities >= 0.5).astype("int8")
        trained_at = datetime.now(timezone.utc).isoformat()
        metrics = {
            "accuracy": float(accuracy_score(actual, predictions)),
            "precision": float(precision_score(actual, predictions, zero_division=0)),
            "recall": float(recall_score(actual, predictions, zero_division=0)),
            "f1": float(f1_score(actual, predictions, zero_division=0)),
            "roc_auc": float(roc_auc_score(actual, probabilities)),
            "trained_at": trained_at,
            "epochs_trained": len(self.training_history["train_loss"]),
            "best_val_loss": float(best_val_loss),
            "device": str(self.device),
        }
        self.metrics_, self.last_updated, self.cumulative_accuracy = metrics, trained_at, metrics["accuracy"]
        return metrics

    def predict(self, df: pd.DataFrame) -> dict:
        """Predict from the most recent 24 engineered candles."""
        if not TORCH_AVAILABLE:
            return {"signal": "hold", "confidence": 0.0, "probability_up": 0.5}
        if self.model is None or self.scaler is None:
            raise RuntimeError("ModelB debe entrenarse o cargarse antes de predecir")
        engineered = self.feature_engineer.compute_features(df).dropna(subset=self.feature_names).reset_index(drop=True)
        if len(engineered) < self.SEQUENCE_LEN:
            raise ValueError("Se requieren al menos 24 filas limpias para predecir con ModelB")
        values = self.scaler.transform(engineered[self.feature_names].astype("float32"))
        sequence = torch.tensor(values[-self.SEQUENCE_LEN:][None, ...], dtype=torch.float32).to(self.device)
        self.model.eval()
        with torch.no_grad():
            probability_up = float(self.model(sequence).item())
        probability_down = float(1.0 - probability_up)
        if 0.45 <= probability_up <= 0.55:
            signal = "NEUTRAL"
        elif probability_up > 0.55:
            signal = "ALCISTA"
        else:
            signal = "BAJISTA"
        distance = abs(probability_up - 0.5)
        confidence = "alta" if distance >= 0.25 else "media" if distance >= 0.10 else "baja"
        timestamp: Any = engineered["timestamp"].iloc[-1] if "timestamp" in engineered else datetime.now(timezone.utc)
        if isinstance(timestamp, pd.Timestamp):
            timestamp = timestamp.to_pydatetime()
        return {"model": self.MODEL_NAME, "probability_up": probability_up, "probability_down": probability_down,
                "signal": signal, "confidence": confidence, "top_features": [], "timestamp": timestamp}

    def save(self, path: str) -> bool:
        """Save PyTorch state, scaler, and model configuration."""
        if not TORCH_AVAILABLE:
            print(f"[ModelB-GRU] torch no disponible, skip save de {path}")
            return False
        if self.model is None or self.scaler is None:
            raise RuntimeError("No se puede guardar ModelB antes de entrenarlo")
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        torch.save({"model_state": self.model.state_dict(), "scaler": self.scaler, "config": {
            "feature_names": self.feature_names, "sequence_len": self.SEQUENCE_LEN,
            "hidden_size": self.HIDDEN_SIZE, "num_layers": self.NUM_LAYERS,
            "gru_dropout": self.GRU_DROPOUT, "head_dropout": self.HEAD_DROPOUT,
            "last_updated": self.last_updated, "cumulative_accuracy": self.cumulative_accuracy,
            "metrics": self.metrics_,
        }}, target)
        return True

    def load(self, path: str) -> bool:
        """Load a PyTorch artifact on CPU or the available device."""
        if not TORCH_AVAILABLE:
            print(f"[ModelB-GRU] torch no disponible, skip load de {path}")
            return False
        artifact = torch.load(path, map_location="cpu", weights_only=False)
        config = artifact.get("config", {})
        self.feature_names = list(config.get("feature_names", self.feature_names))
        self.scaler = artifact["scaler"]
        self.model = self._make_model()
        self.model.load_state_dict(artifact["model_state"])
        self.model.eval()
        self.last_updated = config.get("last_updated")
        self.cumulative_accuracy = config.get("cumulative_accuracy")
        self.metrics_ = dict(config.get("metrics", {}))
        return True

    def get_model_info(self) -> dict:
        return {"nombre": "GRU", "version": self.MODEL_VERSION, "ultima_actualizacion": self.last_updated,
                "accuracy_acumulada": self.cumulative_accuracy, "device": str(self.device)}
