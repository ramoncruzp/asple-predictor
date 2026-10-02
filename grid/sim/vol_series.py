"""Causal, per-window 24h sigma series derived from real volatility forecasts.

Produces one ``sigma_24h`` value per 5-minute candle, in the same units as
``grid.sim.data.ewma_sigma_24h`` (the standard deviation of a 24h log return), so
it can be passed straight into ``grid.sim.runner.run_simulation(sigma_values=...)``
without touching the runner. Three variants are supported:

- ``"ewma"``: exactly today's substitute -- ``ewma_sigma_24h`` applied to the
  window's own 5-minute closes, restarting at every window (faithfully matching
  what ``run_simulation`` already does when ``sigma_values`` is omitted).
- ``"persistence"``: hourly ``models.volatility.PersistenceModel``, retrained
  causally per window (its ``fit`` is a no-op, but it still gets a causal
  calibration offset from held-out history).
- ``"nexo_har"``: hourly ``models.volatility.NexoHARModel`` (the H=24 champion in
  the live volatility system, see ``config.models_config.VOL_CHAMPIONS``),
  retrained per window on data strictly before ``window.start - EMBARGO_HOURS``
  hours and calibrated on a held-out causal slice, mirroring
  ``scripts/train_vol_models.py``'s train/calibration split.

Causality is enforced two ways: (1) ``data.volatility.build_volatility_frame``
only uses backward-looking rolling windows, so a row's *features* never depend on
rows after it, and (2) each window's model is *fit* only on rows strictly before
its embargo cutoff. See ``tests/test_vol_series_causal.py`` for the mutation test
that exercises both.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from data.volatility import aggregate_intraday_to_hourly, build_volatility_frame, feature_columns
from grid.sim.data import ewma_sigma_24h
from models.volatility import NexoHARModel, PersistenceModel

EMBARGO_HOURS = 24
MIN_TRAIN_DAYS = 90
HORIZON_H = 24
TRAIN_FRACTION = 0.85
DEFAULT_CACHE_DIR = Path("data/cache/sim/vol_series")

MODEL_TYPES = {"persistence": PersistenceModel, "nexo_har": NexoHARModel}
HOURLY_MODEL_NAMES = tuple(MODEL_TYPES)
ALL_MODEL_NAMES = ("ewma", *HOURLY_MODEL_NAMES)


def _hash_bytes(*blobs: bytes) -> str:
    digest = hashlib.sha256()
    for blob in blobs:
        digest.update(blob)
    return digest.hexdigest()[:16]


def load_hourly_history(
    hourly_csv: str | Path = "data/cache/xrp_1h.csv",
    five_min_csv: str | Path = "data/cache/xrp_5m.csv",
) -> tuple[pd.DataFrame, pd.DataFrame, str]:
    """Load hourly OHLCV plus intraday realized-variance features, and a content hash."""
    hourly_path, five_min_path = Path(hourly_csv), Path(five_min_csv)
    hourly = pd.read_csv(hourly_path)
    five_min = pd.read_csv(five_min_path)
    for frame in (hourly, five_min):
        for column in ("timestamp", "close_time"):
            if column in frame:
                frame[column] = pd.to_datetime(frame[column], utc=True)
    intraday = aggregate_intraday_to_hourly(five_min)
    csv_hash = _hash_bytes(hourly_path.read_bytes(), five_min_path.read_bytes())
    return hourly, intraday, csv_hash


def build_full_frame(
    hourly: pd.DataFrame, intraday: pd.DataFrame, *, horizon: int = HORIZON_H,
    csv_hash: str | None = None, cache_dir: str | Path = DEFAULT_CACHE_DIR,
) -> pd.DataFrame:
    """Build (or load from cache) the causal hourly feature frame for the whole history.

    Safe to compute once for the entire dataset: every column only looks backward
    (rolling/shift), so no row's features depend on rows after it. Per-window
    causality still requires fitting each model on a slice strictly before that
    window's embargo cutoff (see ``causal_train_predict``).
    """
    cache_dir = Path(cache_dir)
    cache_path = cache_dir / f"frame_{csv_hash}_{horizon}.pkl" if csv_hash else None
    if cache_path is not None and cache_path.is_file():
        return pd.read_pickle(cache_path)
    frame = build_volatility_frame(hourly, horizon, intraday=intraday)
    if cache_path is not None:
        cache_dir.mkdir(parents=True, exist_ok=True)
        frame.to_pickle(cache_path)
    return frame


def window_train_cutoff(window_start_ts: int, *, embargo_hours: int = EMBARGO_HOURS) -> int:
    return int(window_start_ts) - embargo_hours * 3600


def has_sufficient_training_history(
    frame: pd.DataFrame, window_start_ts: int, *, min_train_days: int = MIN_TRAIN_DAYS,
    embargo_hours: int = EMBARGO_HOURS,
) -> bool:
    """True if at least ``min_train_days`` of hourly history exist before the embargo cutoff."""
    cutoff_ts = window_train_cutoff(window_start_ts, embargo_hours=embargo_hours)
    times = pd.to_datetime(frame["close_time"], utc=True)
    earliest = times.min()
    cutoff_dt = pd.Timestamp(cutoff_ts, unit="s", tz="UTC")
    available_days = (cutoff_dt - earliest).total_seconds() / 86400.0
    return available_days >= min_train_days


def eligible_windows(windows: list[dict], frame: pd.DataFrame, **kwargs) -> list[dict]:
    """Filter windows (from ``grid.sim.sweep.build_windows``) with enough causal history."""
    return [w for w in windows if has_sufficient_training_history(frame, w["start"], **kwargs)]


def causal_train_predict(
    frame: pd.DataFrame, model_name: str, train_cutoff_ts: int, *,
    horizon: int = HORIZON_H, train_fraction: float = TRAIN_FRACTION,
) -> tuple[pd.Series, float] | None:
    """Fit ``model_name`` on data strictly before ``train_cutoff_ts`` and predict on the whole frame.

    Returns ``(pred_logvol_cal, var_factor)`` or ``None`` if there is not enough
    causal history (fewer than 50 training rows or 30 calibration rows), mirroring
    the minimums in ``scripts/train_vol_models.py``.
    """
    if model_name not in MODEL_TYPES:
        raise ValueError(f"unsupported hourly model: {model_name}")
    columns = feature_columns(horizon) + ["target_logvol"]
    valid = np.isfinite(frame[columns].to_numpy(dtype="float64")).all(axis=1)
    valid &= frame["complete_hour"].to_numpy(dtype=bool)
    times = pd.to_datetime(frame["close_time"], utc=True)
    before_cutoff = (times < pd.Timestamp(int(train_cutoff_ts), unit="s", tz="UTC")).to_numpy()
    causal_index = np.flatnonzero(valid & before_cutoff)
    data = frame.iloc[causal_index].reset_index(drop=True)
    train_end = int(len(data) * train_fraction)
    calibration_start = train_end + horizon
    train = data.iloc[:train_end]
    calibration = data.iloc[calibration_start:]
    if len(train) < 50 or len(calibration) < 30:
        return None
    model = MODEL_TYPES[model_name](horizon)
    model.fit(train)
    prediction = model.predict(calibration)
    target = calibration["target_logvol"].astype("float64")
    pred_values, target_values = prediction.to_numpy(dtype="float64"), target.to_numpy(dtype="float64")
    finite = np.isfinite(pred_values) & np.isfinite(target_values)
    if not finite.any():
        return None
    var_factor = float(
        np.exp(2.0 * target_values[finite]).mean() / np.exp(2.0 * pred_values[finite]).mean()
    )
    full_prediction = model.predict(frame)
    calibrated = (full_prediction + 0.5 * float(np.log(var_factor))).rename("pred_logvol_cal")
    return calibrated, var_factor


def _cache_path(cache_dir: Path, model_name: str, csv_hash: str, window_id: int) -> Path:
    return cache_dir / f"{model_name}_{csv_hash}_{window_id}.json"


def hourly_forecast_for_window(
    frame: pd.DataFrame, model_name: str, window: dict, *, horizon: int = HORIZON_H,
    csv_hash: str | None = None, cache_dir: str | Path = DEFAULT_CACHE_DIR,
) -> pd.DataFrame | None:
    """Return hourly ``close_time``/``pred_logvol_cal`` rows covering ``window``, cached by (model, window)."""
    cache_dir = Path(cache_dir)
    cache_file = _cache_path(cache_dir, model_name, csv_hash, window["window_id"]) if csv_hash else None
    if cache_file is not None and cache_file.is_file():
        payload = json.loads(cache_file.read_text(encoding="utf-8"))
        if payload["train_rows"] is None:
            return None
        return pd.DataFrame({
            "close_time": pd.to_datetime(payload["close_time"], utc=True),
            "pred_logvol_cal": payload["pred_logvol_cal"],
        })

    train_cutoff_ts = window_train_cutoff(window["start"])
    result = causal_train_predict(frame, model_name, train_cutoff_ts, horizon=horizon)
    times = pd.to_datetime(frame["close_time"], utc=True)
    window_start_dt = pd.Timestamp(int(window["start"]), unit="s", tz="UTC") - pd.Timedelta(hours=1)
    window_end_dt = pd.Timestamp(int(window["end_exclusive"]), unit="s", tz="UTC")
    in_window = (times >= window_start_dt) & (times < window_end_dt)
    if result is None:
        if cache_file is not None:
            cache_dir.mkdir(parents=True, exist_ok=True)
            cache_file.write_text(json.dumps({"train_rows": None}), encoding="utf-8")
        return None
    calibrated, _var_factor = result
    sliced_times = times.loc[in_window]
    sliced_values = calibrated.loc[in_window]
    finite = np.isfinite(sliced_values.to_numpy(dtype="float64"))
    out = pd.DataFrame({
        "close_time": sliced_times.loc[finite].to_numpy(),
        "pred_logvol_cal": sliced_values.loc[finite].to_numpy(dtype="float64"),
    })
    if cache_file is not None:
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps({
            "train_rows": int(len(out)),
            "close_time": [ts.isoformat() for ts in pd.to_datetime(out["close_time"], utc=True)],
            "pred_logvol_cal": out["pred_logvol_cal"].tolist(),
        }), encoding="utf-8")
    return out


def _broadcast_hourly_to_5m(hourly_rows: pd.DataFrame, five_min_timestamps: np.ndarray) -> np.ndarray:
    """Map each 5-minute candle to the latest hourly forecast whose close_time precedes it.

    Causal: a forecast made at an hour's close is usable starting the next 5-minute
    candle, consistent with the live system's hourly cadence (``VolLoop``).
    """
    if hourly_rows is None or hourly_rows.empty:
        return np.full(len(five_min_timestamps), np.nan, dtype=float)
    hourly_rows = hourly_rows.sort_values("close_time")
    hourly_close_ts = (
        pd.to_datetime(hourly_rows["close_time"], utc=True).astype("int64") // 10**9
    ).to_numpy()
    values = hourly_rows["pred_logvol_cal"].to_numpy(dtype=float)
    candle_ts = np.asarray(five_min_timestamps, dtype=np.int64)
    idx = np.searchsorted(hourly_close_ts, candle_ts, side="right") - 1
    out = np.full(len(candle_ts), np.nan, dtype=float)
    ok = idx >= 0
    out[ok] = values[idx[ok]]
    return out


def vol_series_24h(
    candles_5m, model_name: str, window: dict, *, frame: pd.DataFrame | None = None,
    halflife_h: float = 72.0, horizon: int = HORIZON_H, csv_hash: str | None = None,
    cache_dir: str | Path = DEFAULT_CACHE_DIR,
) -> np.ndarray:
    """Causal sigma_24h per 5-minute candle in ``candles_5m`` (one window's slice).

    ``candles_5m`` must be the window's own 5-minute ``CandleData`` slice (same
    object ``run_simulation`` receives). For ``"ewma"`` this reduces to exactly
    what ``run_simulation`` does by default. For the hourly models, ``frame`` (the
    whole-history causal feature frame from ``build_full_frame``) is required.
    """
    if model_name == "ewma":
        return ewma_sigma_24h(candles_5m.close, halflife_h)
    if frame is None:
        raise ValueError(f"frame is required for hourly model {model_name!r}")
    hourly_rows = hourly_forecast_for_window(
        frame, model_name, window, horizon=horizon, csv_hash=csv_hash, cache_dir=cache_dir,
    )
    pred_logvol_cal_5m = _broadcast_hourly_to_5m(hourly_rows, candles_5m.timestamp)
    sigma_per_hour = np.exp(pred_logvol_cal_5m)
    return sigma_per_hour * np.sqrt(24.0)
