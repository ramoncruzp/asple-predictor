"""Live inference wrapper for calibrated volatility research artifacts."""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

from config.models_config import (
    VOL_ARTIFACT_DIR, VOL_CHAMPIONS, VOL_HORIZONS, VOL_MODELS, VOL_SYMBOL,
    vol_artifact_dir, vol_base, vol_champions, vol_manifest_path,
)
from data.volatility import aggregate_intraday_to_hourly, build_volatility_frame


class VolPredictor:
    def __init__(
        self,
        artifact_dir: str | Path | None = None,
        *,
        symbol: str = VOL_SYMBOL,
        horizons: list[int] | None = None,
        model_names: list[str] | None = None,
    ):
        vol_base(symbol)
        self.artifact_dir = Path(artifact_dir if artifact_dir is not None else vol_artifact_dir(symbol))
        self.symbol = symbol
        self.champions, self.selection_provisional = vol_champions(symbol)
        self.horizons = list(VOL_HORIZONS if horizons is None else horizons)
        self.model_names = list(VOL_MODELS if model_names is None else model_names)
        self.logger = logging.getLogger(__name__)
        self.manifest_path = self.artifact_dir / Path(vol_manifest_path(symbol)).name
        self._lock = threading.RLock()
        self.manifest: dict = {}
        self.models: dict[tuple[int, str], dict] = {}
        self.manifest_mtime: int | None = None
        self.reload_error: str | None = None
        manifest, models, mtime = self._read_snapshot(strict_models=False)
        with self._lock:
            self.manifest, self.models, self.manifest_mtime = manifest, models, mtime

    def _read_snapshot(self, *, strict_models: bool) -> tuple[dict, dict, int]:
        before = self.manifest_path.stat().st_mtime_ns
        manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        models: dict[tuple[int, str], dict] = {}
        for horizon in self.horizons:
            for model_name in self.model_names:
                path = self.artifact_dir / f"{model_name}_{horizon}h.joblib"
                try:
                    payload = joblib.load(path)
                    model = payload.get("model", payload) if isinstance(payload, dict) else payload
                    var_factor = (
                        payload.get("var_factor") if isinstance(payload, dict)
                        else manifest["horizons"][str(horizon)][model_name]["var_factor"]
                    )
                    if var_factor is None:
                        var_factor = manifest["horizons"][str(horizon)][model_name]["var_factor"]
                    models[(horizon, model_name)] = {"model": model, "var_factor": float(var_factor)}
                except Exception:
                    if strict_models:
                        raise
                    self.logger.exception("No se pudo cargar artefacto de volatilidad %s", path)
        after = self.manifest_path.stat().st_mtime_ns
        if before != after:
            raise RuntimeError("El manifest cambió mientras se cargaban los artefactos")
        return manifest, models, after

    def reload(self) -> bool:
        """Atomically reload manifest and artifacts; retain the active snapshot on failure."""
        with self._lock:
            try:
                manifest, models, mtime = self._read_snapshot(strict_models=True)
            except Exception as exc:
                self.reload_error = str(exc)
                self.logger.exception("No se pudo recargar volatilidad para %s", self.symbol)
                return False
            self.manifest, self.models, self.manifest_mtime = manifest, models, mtime
            self.champions, self.selection_provisional = vol_champions(self.symbol)
            self.reload_error = None
            return True

    def is_champion(self, horizon: int, model_name: str) -> bool:
        return self.champions.get(horizon) == model_name

    @staticmethod
    def _utc(value) -> datetime:
        timestamp = pd.Timestamp(value)
        if timestamp.tzinfo is None:
            timestamp = timestamp.tz_localize("UTC")
        else:
            timestamp = timestamp.tz_convert("UTC")
        return timestamp.to_pydatetime()

    def predict_latest(
        self,
        df_1h: pd.DataFrame,
        df_5m: pd.DataFrame,
        *,
        now: datetime | None = None,
    ) -> list[dict]:
        now_utc = self._utc(now or datetime.now(timezone.utc))
        hourly = df_1h.copy(deep=True)
        five_minute = df_5m.copy(deep=True)
        for frame in (hourly, five_minute):
            for column in ("timestamp", "close_time"):
                if column in frame:
                    frame[column] = pd.to_datetime(frame[column], utc=True)
        if "close_time" in five_minute:
            five_minute = five_minute.loc[five_minute["close_time"] <= now_utc]
        if "close_time" in hourly:
            hourly = hourly.loc[hourly["close_time"] <= now_utc]
        hourly = hourly.reset_index(drop=True)
        intraday = aggregate_intraday_to_hourly(five_minute)
        prepared_by_horizon = {}
        for horizon in self.horizons:
            prepared_by_horizon[horizon] = build_volatility_frame(hourly, horizon, intraday=intraday)
        base_frame = prepared_by_horizon[self.horizons[0]]
        close_times = (
            pd.to_datetime(base_frame["close_time"], utc=True)
            if "close_time" in base_frame
            else pd.to_datetime(base_frame["timestamp"], utc=True) + pd.Timedelta(hours=1) - pd.Timedelta(milliseconds=1)
        )
        candidates = np.flatnonzero(
            base_frame["complete_hour"].to_numpy(dtype=bool)
            & (close_times <= pd.Timestamp(now_utc)).to_numpy()
        )
        if not len(candidates):
            raise ValueError("No hay una última hora completa y cerrada para predecir volatilidad")
        latest_index = int(candidates[-1])
        forecast_at = self._utc(close_times.iloc[latest_index])
        made_at = now_utc
        rows = []
        with self._lock:
            loaded_models = self.models
        for horizon in self.horizons:
            frame = prepared_by_horizon[horizon]
            for model_name in self.model_names:
                item = loaded_models.get((horizon, model_name))
                if item is None:
                    continue
                try:
                    predictions = item["model"].predict(frame)
                    raw_value = float(predictions.iloc[latest_index])
                    if not np.isfinite(raw_value):
                        raise ValueError("el pronóstico más reciente es NaN/inf")
                    var_factor = float(item["var_factor"])
                    champion = self.is_champion(horizon, model_name)
                    rows.append({
                        "symbol": self.symbol,
                        "horizon_h": horizon,
                        "model_name": model_name,
                        "forecast_at": forecast_at,
                        "made_at": made_at,
                        "pred_logvol_raw": raw_value,
                        "pred_logvol_cal": raw_value + 0.5 * float(np.log(var_factor)),
                        "var_factor": var_factor,
                        "is_champion": champion,
                        "price": float(hourly.iloc[latest_index]["close"]),
                    })
                except Exception:
                    self.logger.exception("Predicción live falló para %s H=%dh", model_name, horizon)
        return rows


class VolPredictorRegistry:
    """Thread-safe registry for predictors whose artifacts are ready on disk."""

    def __init__(
        self, artifact_root: str | Path | None = None, *,
        horizons: list[int] | None = None, model_names: list[str] | None = None,
    ):
        self.horizons = None if horizons is None else list(horizons)
        self.model_names = None if model_names is None else list(model_names)
        self._default_root = artifact_root is None
        self._artifact_root = Path(VOL_ARTIFACT_DIR if artifact_root is None else artifact_root)
        self._predictors: dict[str, VolPredictor] = {}
        self._lock = threading.RLock()
        self.logger = logging.getLogger(__name__)

    def _directory(self, symbol: str) -> Path:
        base = vol_base(symbol)
        if self._default_root:
            return Path(vol_artifact_dir(symbol))
        return self._artifact_root if symbol == VOL_SYMBOL else self._artifact_root / base

    def get(self, symbol: str) -> VolPredictor | None:
        vol_base(symbol)
        with self._lock:
            return self._predictors.get(symbol)

    def load(self, symbol: str) -> VolPredictor | None:
        vol_base(symbol)
        with self._lock:
            existing = self._predictors.get(symbol)
            if existing is not None:
                return existing
            directory = self._directory(symbol)
            manifest = directory / Path(vol_manifest_path(symbol)).name
            if not manifest.is_file():
                return None
            predictor = VolPredictor(directory, symbol=symbol, horizons=self.horizons, model_names=self.model_names)
            self._predictors[symbol] = predictor
            return predictor

    def load_available(self) -> list[str]:
        """Load XRP legacy artifacts and valid symbol subdirectories found on disk."""
        symbols = [VOL_SYMBOL]
        if self._artifact_root.is_dir():
            for directory in self._artifact_root.iterdir():
                if not directory.is_dir():
                    continue
                candidate = directory.name.upper() + "USDT"
                try:
                    vol_base(candidate)
                except ValueError:
                    continue
                if (directory / Path(vol_manifest_path(candidate)).name).is_file():
                    symbols.append(candidate)
        loaded = []
        for symbol in dict.fromkeys(symbols):
            try:
                if self.load(symbol) is not None:
                    loaded.append(symbol)
            except Exception:
                self.logger.exception("No se pudo cargar volatilidad para %s", symbol)
        return loaded

    def reload(self, symbol: str):
        """Force reload after a per-symbol consensus has been written."""
        vol_base(symbol)
        predictor = self.get(symbol)
        if predictor is None:
            return self.load(symbol)
        return predictor if predictor.reload() else None

    def reload_if_changed(self, symbol: str) -> bool:
        predictor = self.get(symbol)
        if predictor is None:
            return False
        try:
            current_mtime = predictor.manifest_path.stat().st_mtime_ns
        except OSError:
            self.logger.exception("No se pudo consultar el manifest de %s", symbol)
            return False
        if current_mtime == predictor.manifest_mtime:
            return False
        return predictor.reload()

    def ready_symbols(self) -> list[str]:
        with self._lock:
            return sorted(self._predictors)
