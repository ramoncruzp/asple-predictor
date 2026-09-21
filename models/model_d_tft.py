"""Optional Temporal Fusion Transformer model."""
from __future__ import annotations

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
import ta
import torch
from sklearn.metrics import mean_absolute_error

from models.base_model import BaseModel
from data.feature_engineer import FeatureEngineer

try:
    from pytorch_forecasting import TemporalFusionTransformer, TimeSeriesDataSet
    from pytorch_forecasting.metrics import MAE
    try:
        import lightning.pytorch as pl
    except ImportError:
        import pytorch_lightning as pl
except ImportError as exc:
    raise ImportError(
        "ModelD requiere pytorch-forecasting y pytorch-lightning. "
        "Instala con: pip install pytorch-forecasting pytorch-lightning"
    ) from exc


class ModelD(BaseModel):
    FEATURE_NAMES = [
        "ema_9", "ema_21", "rsi_14", "macd", "macd_signal", "stoch_k", "stoch_d",
        "atr_14", "bb_upper", "bb_lower", "bb_pct", "obv", "vwap", "vol_ratio",
        "adx", "price_change", "high_low_range", "close_to_vwap",
    ]
    SEQUENCE_LENGTH = 60

    def __init__(self) -> None:
        self.tft_model = None
        self.training_dataset = None
        self.feature_names = list(self.FEATURE_NAMES)
        self.metrics: dict[str, Any] = {}
        self.last_trained = None

    @staticmethod
    def _features(df: pd.DataFrame, include_target: bool = True) -> pd.DataFrame:
        result = FeatureEngineer().compute_features(df)
        result["price_change"] = result["close"].pct_change()
        result["high_low_range"] = (result["high"] - result["low"]) / result["close"]
        result["close_to_vwap"] = result["close"] / result["vwap"] - 1.0
        result["adx"] = ta.trend.ADXIndicator(
            result["high"], result["low"], result["close"], window=14
        ).adx()
        columns = ["timestamp", "close", *ModelD.FEATURE_NAMES]
        if include_target and "target" in result:
            columns.append("target")
        result = result.replace([np.inf, -np.inf], np.nan).dropna(subset=ModelD.FEATURE_NAMES)
        return result[columns].reset_index(drop=True)

    def _dataset(self, prepared: pd.DataFrame) -> TimeSeriesDataSet:
        data = prepared.copy()
        data["time_idx"] = np.arange(len(data), dtype=np.int64)
        data["group_id"] = "XRPUSDT"
        data["target"] = data.get("target", pd.Series(0.0, index=data.index)).fillna(0.0).astype(float)
        return TimeSeriesDataSet(
            data, time_idx="time_idx", target="target", group_ids=["group_id"],
            min_encoder_length=self.SEQUENCE_LENGTH, max_encoder_length=self.SEQUENCE_LENGTH,
            min_prediction_length=1, max_prediction_length=1, static_categoricals=["group_id"],
            time_varying_known_reals=["time_idx"], time_varying_unknown_reals=self.FEATURE_NAMES,
            target_normalizer=None,
        )

    def train(self, df: pd.DataFrame) -> dict:
        prepared = self._features(df).dropna(subset=["target"]).reset_index(drop=True)
        if len(prepared) < self.SEQUENCE_LENGTH + 20:
            raise ValueError("ModelD necesita al menos 80 velas válidas para entrenar")
        cutoff = max(self.SEQUENCE_LENGTH + 1, int(len(prepared) * 0.8))
        train_df = prepared.iloc[:cutoff].copy()
        self.training_dataset = self._dataset(prepared)
        train_dataset = self._dataset(train_df)
        validation_data = prepared.copy()
        validation_data["time_idx"] = np.arange(len(validation_data), dtype=np.int64)
        validation_data["group_id"] = "XRPUSDT"
        valid_dataset = TimeSeriesDataSet.from_dataset(train_dataset, validation_data, predict=True, stop_randomization=True)
        train_loader = train_dataset.to_dataloader(train=True, batch_size=64, num_workers=0)
        valid_loader = valid_dataset.to_dataloader(train=False, batch_size=64, num_workers=0)
        self.tft_model = TemporalFusionTransformer.from_dataset(
            train_dataset, learning_rate=0.001, hidden_size=16, attention_head_size=2,
            dropout=0.1, hidden_continuous_size=8, output_size=1, loss=MAE(),
            log_interval=-1, reduce_on_plateau_patience=3,
        )
        trainer = pl.Trainer(max_epochs=10, accelerator="cpu", devices=1, logger=False,
                             enable_checkpointing=False, enable_model_summary=False,
                             gradient_clip_val=0.1)
        trainer.fit(self.tft_model, train_dataloaders=train_loader, val_dataloaders=valid_loader)
        predictions = self.tft_model.predict(valid_loader, mode="prediction").detach().cpu().numpy().reshape(-1)
        actual = torch.cat([batch[1][0] if isinstance(batch[1], tuple) else batch[1] for batch in valid_loader])
        actual_values = actual.detach().cpu().numpy().reshape(-1)
        val_loss = float(mean_absolute_error(actual_values, predictions))
        self.metrics = {"val_loss": val_loss, "mae": val_loss,
                        "trained_at": datetime.now(timezone.utc).isoformat()}
        self.last_trained = self.metrics["trained_at"]
        return dict(self.metrics)

    def predict(self, df: pd.DataFrame) -> dict:
        if self.tft_model is None or self.training_dataset is None:
            raise RuntimeError("ModelD no está entrenado ni cargado")
        prepared = self._features(df, include_target=False)
        if len(prepared) < self.SEQUENCE_LENGTH + 1:
            raise ValueError("ModelD necesita al menos 61 velas para predecir")
        prepared = prepared.tail(self.SEQUENCE_LENGTH + 1).assign(target=0.0)
        prepared["time_idx"] = np.arange(len(prepared), dtype=np.int64)
        prepared["group_id"] = "XRPUSDT"
        dataset = TimeSeriesDataSet.from_dataset(self.training_dataset, prepared, predict=True, stop_randomization=True)
        loader = dataset.to_dataloader(train=False, batch_size=1, num_workers=0)
        with torch.no_grad():
            probability = float(self.tft_model.predict(loader, mode="prediction").detach().cpu().numpy().reshape(-1)[0])
        probability = float(np.clip(probability, 0.0, 1.0))
        signal = "ALCISTA" if probability > 0.55 else "BAJISTA" if probability < 0.45 else "NEUTRAL"
        confidence = "alta" if probability >= 0.70 or probability <= 0.30 else "media" if probability >= 0.60 or probability <= 0.40 else "baja"
        return {"model": "model_d", "probability_up": probability, "probability_down": 1.0 - probability,
                "signal": signal, "confidence": confidence, "top_features": [],
                "timestamp": datetime.now(timezone.utc)}

    def save(self, path: str) -> None:
        if self.tft_model is None or self.training_dataset is None:
            raise RuntimeError("No hay un ModelD entrenado para guardar")
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        torch.save({"model_state": self.tft_model.state_dict(), "dataset": self.training_dataset,
                    "feature_names": self.feature_names, "metrics": self.metrics,
                    "config": {"learning_rate": 0.001, "hidden_size": 16,
                               "attention_head_size": 2, "dropout": 0.1,
                               "hidden_continuous_size": 8, "output_size": 1}}, path)

    def load(self, path: str) -> None:
        artifact = torch.load(path, map_location="cpu", weights_only=False)
        self.training_dataset = artifact["dataset"]
        config = artifact.get("config", {"learning_rate": 0.001, "hidden_size": 16,
                                           "attention_head_size": 2, "dropout": 0.1,
                                           "hidden_continuous_size": 8, "output_size": 1})
        self.tft_model = TemporalFusionTransformer.from_dataset(self.training_dataset, loss=MAE(),
                                                                log_interval=-1, **config)
        self.tft_model.load_state_dict(artifact["model_state"])
        self.tft_model.eval()
        self.feature_names = artifact.get("feature_names", list(self.FEATURE_NAMES))
        self.metrics = artifact.get("metrics", {})
        self.last_trained = self.metrics.get("trained_at")

    def get_model_info(self) -> dict:
        return {"nombre": "TFT (Temporal Fusion)", "version": "1.0",
                "ultima_actualizacion": self.last_trained, "accuracy_acumulada": None}
