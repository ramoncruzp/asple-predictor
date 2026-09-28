"""Live inference wrapper for calibrated volatility research artifacts."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

from config.models_config import VOL_ARTIFACT_DIR, VOL_CHAMPIONS, VOL_HORIZONS, VOL_MODELS, VOL_SYMBOL
from data.volatility import aggregate_intraday_to_hourly, build_volatility_frame


class VolPredictor:
    def __init__(
        self,
        artifact_dir: str | Path = VOL_ARTIFACT_DIR,
        *,
        symbol: str = VOL_SYMBOL,
        horizons: list[int] | None = None,
        model_names: list[str] | None = None,
    ):
        self.artifact_dir = Path(artifact_dir)
        self.symbol = symbol
        self.horizons = list(VOL_HORIZONS if horizons is None else horizons)
        self.model_names = list(VOL_MODELS if model_names is None else model_names)
        self.logger = logging.getLogger(__name__)
        manifest_path = self.artifact_dir / "manifest_xrp.json"
        if not manifest_path.is_file():
            raise FileNotFoundError(f"No existe el manifest de volatilidad: {manifest_path}")
        self.manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.models: dict[tuple[int, str], dict] = {}
        for horizon in self.horizons:
            for model_name in self.model_names:
                path = self.artifact_dir / f"{model_name}_{horizon}h.joblib"
                try:
                    payload = joblib.load(path)
                    model = payload.get("model", payload) if isinstance(payload, dict) else payload
                    var_factor = (
                        payload.get("var_factor") if isinstance(payload, dict)
                        else self.manifest["horizons"][str(horizon)][model_name]["var_factor"]
                    )
                    if var_factor is None:
                        var_factor = self.manifest["horizons"][str(horizon)][model_name]["var_factor"]
                    self.models[(horizon, model_name)] = {"model": model, "var_factor": float(var_factor)}
                except Exception:
                    self.logger.exception("No se pudo cargar artefacto de volatilidad %s", path)

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
        for horizon in self.horizons:
            frame = prepared_by_horizon[horizon]
            for model_name in self.model_names:
                item = self.models.get((horizon, model_name))
                if item is None:
                    continue
                try:
                    predictions = item["model"].predict(frame)
                    raw_value = float(predictions.iloc[latest_index])
                    if not np.isfinite(raw_value):
                        raise ValueError("el pronóstico más reciente es NaN/inf")
                    var_factor = float(item["var_factor"])
                    champion = VOL_CHAMPIONS.get(horizon) == model_name
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
