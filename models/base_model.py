"""Abstract interface shared by prediction models."""

from __future__ import annotations

from abc import ABC, abstractmethod

import pandas as pd


class BaseModel(ABC):
    """Contract for trainable, serializable prediction models."""

    @abstractmethod
    def train(self, df: pd.DataFrame) -> dict:
        """Train the model and return evaluation metrics."""
        raise NotImplementedError

    @abstractmethod
    def predict(self, df: pd.DataFrame) -> dict:
        """Predict the latest observation and return signal details."""
        raise NotImplementedError

    @abstractmethod
    def save(self, path: str) -> None:
        """Persist the model artifact."""
        raise NotImplementedError

    @abstractmethod
    def load(self, path: str) -> None:
        """Load a model artifact."""
        raise NotImplementedError

    @abstractmethod
    def get_model_info(self) -> dict:
        """Return model identity and cumulative performance metadata."""
        raise NotImplementedError
