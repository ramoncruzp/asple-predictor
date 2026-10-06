"""Train production volatility artifacts from local hourly and optional 5m candles."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config.models_config import VOL_ARTIFACT_DIR, VOL_CHAMPIONS, VOL_HORIZONS, VOL_MODELS, VOL_SYMBOL
from data.volatility import aggregate_intraday_to_hourly, build_volatility_frame, feature_columns
from models.volatility import (
    EWMAModel, GARCHModel, GBMModel, HARAsymModel, HARModel, HARRangeModel,
    NexoHARModel, PersistenceModel,
)
from scripts.vol_research import score_forecast
from scripts.download_candles import download_closed_candles


MODEL_TYPES = {
    "Persistence": PersistenceModel,
    "EWMA": EWMAModel,
    "HAR": HARModel,
    "HAR_range": HARRangeModel,
    "HAR_asym": HARAsymModel,
    "GBM": GBMModel,
    "NexoHAR": NexoHARModel,
    "GARCH_t": GARCHModel,
}
VOL_TRAIN_DAYS = 730
MIN_HOURLY_ROWS = int(730 * 24 * 0.95)
MIN_5M_ROWS = int(730 * 288 * 0.95)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candles", type=Path, default=Path("data/cache/xrp_1h.csv"))
    parser.add_argument("--candles-5m", type=Path, default=Path("data/cache/xrp_5m.csv"))
    parser.add_argument("--refresh-candles", action="store_true")
    parser.add_argument("--candles-dir", type=Path, default=Path("data/cache/vol_train"))
    parser.add_argument("--progress", action="store_true")
    args = parser.parse_args(argv)
    provided = list(sys.argv[1:] if argv is None else argv)
    if args.refresh_candles and any(flag in provided for flag in ("--candles", "--candles-5m")):
        parser.error("--refresh-candles no se puede combinar con --candles ni --candles-5m")
    return args


def validate_refresh_csv(path: Path, interval: str, min_rows: int, *, now=None) -> pd.DataFrame:
    frame = pd.read_csv(path)
    if len(frame) < min_rows:
        raise ValueError(f"Datos insuficientes en {interval}: {len(frame)} filas; se requieren {min_rows}")
    if "close_time" not in frame:
        raise ValueError(f"Falta close_time en el CSV de {interval}")
    latest = pd.to_datetime(frame["close_time"], utc=True, errors="coerce").max()
    current = pd.Timestamp(now or datetime.now(timezone.utc))
    if current.tzinfo is None:
        current = current.tz_localize("UTC")
    else:
        current = current.tz_convert("UTC")
    age = current - latest
    if pd.isna(latest) or age < pd.Timedelta(0) or age > pd.Timedelta(hours=2):
        raise ValueError(f"Datos de {interval} obsoletos: última close_time={latest}, antigüedad máxima 2 horas")
    return frame


def _load_data(candles_path: Path, candles_5m_path: Path | None):
    candles = pd.read_csv(candles_path)
    for column in ("timestamp", "close_time"):
        if column in candles:
            candles[column] = pd.to_datetime(candles[column], utc=True)
    intraday = None
    if candles_5m_path is not None:
        candles_5m = pd.read_csv(candles_5m_path)
        for column in ("timestamp", "close_time"):
            if column in candles_5m:
                candles_5m[column] = pd.to_datetime(candles_5m[column], utc=True)
        intraday = aggregate_intraday_to_hourly(candles_5m)
        candles["timestamp"] = pd.to_datetime(candles["timestamp"], utc=True).dt.floor("h")
        common_start = max(candles["timestamp"].min(), intraday["timestamp"].min())
        common_end = min(candles["timestamp"].max(), intraday["timestamp"].max())
        candles = candles.loc[candles["timestamp"].between(common_start, common_end)].reset_index(drop=True)
        intraday = intraday.loc[intraday["timestamp"].between(common_start, common_end)].reset_index(drop=True)
    if candles.empty:
        raise ValueError("No hay velas 1h en el rango de datos")
    return candles, intraday


def _new_model(name: str, horizon: int):
    try:
        model_type = MODEL_TYPES[name]
    except KeyError as exc:
        raise ValueError(f"Modelo de volatilidad no soportado: {name}") from exc
    return model_type(horizon)


def _train_volatility_models_impl(
    candles: pd.DataFrame,
    intraday: pd.DataFrame | None = None,
    *,
    artifact_dir: Path | str = VOL_ARTIFACT_DIR,
    horizons: list[int] | None = None,
    model_names: list[str] | None = None,
    symbol: str = VOL_SYMBOL,
    progress=None,
) -> dict:
    horizons = list(VOL_HORIZONS if horizons is None else horizons)
    model_names = list(VOL_MODELS if model_names is None else model_names)
    artifact_dir = Path(artifact_dir)
    artifact_dir.mkdir(parents=True, exist_ok=True)
    trained_at = datetime.now(timezone.utc)
    hourly_times = pd.to_datetime(candles["timestamp"], utc=True)
    manifest = {
        "symbol": symbol,
        "trained_at": trained_at.isoformat(),
        "data_range": {"start": hourly_times.min().isoformat(), "end": hourly_times.max().isoformat()},
        "champions": {str(key): value for key, value in VOL_CHAMPIONS.items()},
        "horizons": {},
    }
    pending_artifacts: list[tuple[Path, Path]] = []

    for horizon in horizons:
        frame = build_volatility_frame(candles, horizon, intraday=intraday)
        columns = feature_columns(horizon) + ["target_logvol"]
        valid = np.isfinite(frame[columns].to_numpy(dtype="float64")).all(axis=1)
        valid &= frame["complete_hour"].to_numpy(dtype=bool)
        data = frame.loc[valid].reset_index(drop=True)
        train_end = int(len(data) * 0.85)
        calibration_start = train_end + horizon
        train = data.iloc[:train_end]
        calibration = data.iloc[calibration_start:]
        if len(train) < 50 or len(calibration) < 30:
            raise ValueError(
                f"Datos insuficientes para H={horizon}: train={len(train)}, calibration={len(calibration)}"
            )
        horizon_results = {}
        for name in model_names:
            if progress:
                progress(f"entrenando:H{horizon}:{name}")
            model = _new_model(name, horizon)
            # Keep calibration fully held out; HAR_range defaults to Parkinson without VAL.
            model.fit(train)
            if name == "HAR_range" and model.selected_estimator != "parkinson":
                raise RuntimeError("HAR_range no seleccionó Parkinson")
            prediction = model.predict(calibration)
            target = calibration["target_logvol"]
            raw_score = score_forecast(target, prediction)
            if raw_score["n"] == 0:
                raise ValueError(f"No hay predicciones de calibración válidas para {name} H={horizon}")
            var_factor = float(np.exp(2.0 * target).mean() / np.exp(2.0 * prediction).mean())
            calibrated = prediction + 0.5 * np.log(var_factor)
            calibrated_score = score_forecast(target, calibrated)
            horizon_results[name] = {
                "var_factor": var_factor,
                "r2_cal": calibrated_score["r2_oos"],
                "mse_cal": calibrated_score["mse_log"],
                "qlike_cal": calibrated_score["qlike"],
            }
            artifact_path = artifact_dir / f"{name}_{horizon}h.joblib"
            temporary_path = artifact_path.with_name(artifact_path.name + ".tmp")
            model_payload = {
                "model": model,
                "symbol": symbol,
                "horizon_h": horizon,
                "model_name": name,
                "var_factor": var_factor,
            }
            joblib.dump(model_payload, temporary_path)
            pending_artifacts.append((temporary_path, artifact_path))
        manifest["horizons"][str(horizon)] = horizon_results

    if progress:
        progress("calibrando_regimen")
    if intraday is not None:
        frame_24 = build_volatility_frame(candles, 24, intraday=intraday)
        cutoff = hourly_times.max() - pd.Timedelta(days=365)
        recent = frame_24.loc[
            (pd.to_datetime(frame_24["timestamp"], utc=True) >= cutoff)
            & frame_24["target_logvol"].notna()
            & frame_24["complete_hour"]
            & frame_24["future_var_24"].notna(),
            "target_logvol",
        ]
        realized_24 = np.exp(recent.to_numpy(dtype="float64"))
    else:
        frame_24 = build_volatility_frame(candles, 24)
        cutoff = hourly_times.max() - pd.Timedelta(days=365)
        recent = frame_24.loc[
            (pd.to_datetime(frame_24["timestamp"], utc=True) >= cutoff)
            & frame_24["target_logvol"].notna(),
            "target_logvol",
        ]
        realized_24 = np.exp(recent.to_numpy(dtype="float64"))
    if len(realized_24) == 0:
        raise ValueError("No hay volatilidad realizada a 24h para calcular percentiles de régimen")
    manifest["regime_percentiles_24h"] = {
        "p33": float(np.quantile(realized_24, 0.33)),
        "p66": float(np.quantile(realized_24, 0.66)),
        "sample_rows": int(len(realized_24)),
        "lookback_days": 365,
    }
    manifest_tmp = artifact_dir / "manifest_xrp.json.tmp"
    manifest_path = artifact_dir / "manifest_xrp.json"
    if progress:
        progress("guardando")
    manifest_tmp.write_text(json.dumps(manifest, indent=2, allow_nan=False), encoding="utf-8")
    for temporary_path, artifact_path in pending_artifacts:
        os.replace(temporary_path, artifact_path)
    os.replace(manifest_tmp, manifest_path)
    return manifest


def train_volatility_models(
    candles: pd.DataFrame,
    intraday: pd.DataFrame | None = None,
    *,
    artifact_dir: Path | str = VOL_ARTIFACT_DIR,
    horizons: list[int] | None = None,
    model_names: list[str] | None = None,
    symbol: str = VOL_SYMBOL,
    progress=None,
) -> dict:
    artifact_dir = Path(artifact_dir)
    try:
        return _train_volatility_models_impl(
            candles, intraday, artifact_dir=artifact_dir, horizons=horizons,
            model_names=model_names, symbol=symbol, progress=progress,
        )
    except Exception:
        for temporary in artifact_dir.glob("*.tmp"):
            temporary.unlink(missing_ok=True)
        raise


def main(argv=None) -> int:
    args = parse_args(argv)
    progress = (lambda phase: print(f"PROGRESS:{phase}", flush=True)) if args.progress else None
    if args.refresh_candles:
        directory = args.candles_dir
        hourly_path, five_path = directory / "xrp_1h.csv", directory / "xrp_5m.csv"
        if progress:
            progress("descargando_1h")
        download_closed_candles(VOL_SYMBOL, "1h", VOL_TRAIN_DAYS, hourly_path)
        if progress:
            progress("descargando_5m")
        download_closed_candles(VOL_SYMBOL, "5m", VOL_TRAIN_DAYS, five_path)
        if progress:
            progress("validando_datos")
        validate_refresh_csv(hourly_path, "1h", MIN_HOURLY_ROWS)
        validate_refresh_csv(five_path, "5m", MIN_5M_ROWS)
        candles_path, candles_5m_path = hourly_path.resolve(), five_path.resolve()
    else:
        candles_path = args.candles.resolve()
        candles_5m_path = args.candles_5m.resolve() if args.candles_5m and args.candles_5m.is_file() else None
    candles, intraday = _load_data(candles_path, candles_5m_path)
    manifest = train_volatility_models(candles, intraday, progress=progress)
    print(f"Modelos guardados en {VOL_ARTIFACT_DIR}; horizontes={','.join(manifest['horizons'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
