"""Download closed Binance candles and train selected A/B/C models."""

from __future__ import annotations

import argparse
import importlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    import torch as _torch  # noqa: F401  precarga DLLs antes de xgboost (Windows)
except (ImportError, OSError):
    _torch = None

MODEL_MODULES = {
    "a": ("models.model_a_xgboost", "ModelA"),
    "b": ("models.model_b_gru", "ModelB"),
    "c": ("models.model_c_prophet", "ModelC"),
}


def _binance_client_class():
    return importlib.import_module("data.binance_client").BinanceClient


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Entrena los modelos A, B y C con velas Binance.")
    parser.add_argument("--symbol", default="XRPUSDT")
    binance_client = _binance_client_class()
    parser.add_argument("--interval", default="1h", choices=sorted(binance_client.VALID_INTERVALS))
    parser.add_argument("--days", type=int, default=730)
    parser.add_argument("--models", default="a,b,c", help="Lista separada por comas: a,b,c")
    parser.add_argument(
        "--force", action="store_true", help="Permite sobrescribir artefactos 4h existentes"
    )
    args = parser.parse_args()
    if args.days <= 0:
        parser.error("--days debe ser mayor que cero")
    selected = [name.strip().lower() for name in args.models.split(",") if name.strip()]
    if not selected or len(set(selected)) != len(selected) or set(selected) - MODEL_MODULES.keys():
        parser.error("--models debe contener uno o más valores únicos de a,b,c")
    args.models = selected
    args.symbol = args.symbol.strip().upper().replace("/", "")
    try:
        args.symbol = binance_client._binance_symbol(args.symbol)
        binance_client._validate_interval(args.interval)
    except ValueError as exc:
        parser.error(str(exc))
    return args


def main() -> int:
    args = parse_args()
    binance_client = _binance_client_class()
    client = binance_client("", "")
    frame = client.get_historical_klines(args.symbol, args.interval, args.days)
    if "close_time" in frame.columns and not frame.empty:
        now = datetime.now(timezone.utc)
        close_times = frame["close_time"]
        if close_times.dt.tz is None:
            close_times = close_times.dt.tz_localize("UTC")
        frame = frame.loc[close_times <= now].reset_index(drop=True)
    if frame.empty:
        raise RuntimeError("Binance no devolvió velas cerradas para entrenar")

    root = Path(__file__).resolve().parents[1]
    saved_dir = root / "models" / "saved"
    saved_dir.mkdir(parents=True, exist_ok=True)
    base = args.symbol[:-4].lower()
    metrics_path = saved_dir / f"metrics_{base}_{args.interval}.json"
    artifact_paths = {
        key: saved_dir / f"model_{key}_{base}_{args.interval}{'.pt' if key == 'b' else '.joblib'}"
        for key in args.models
    }
    if args.interval == "4h" and not args.force:
        protected = [path for path in [*artifact_paths.values(), metrics_path] if path.exists()]
        if protected:
            raise FileExistsError(
                f"Se rechaza sobrescribir artefactos 4h existentes: {protected[0]}"
            )

    metric_results: dict[str, dict[str, Any]] = {}
    metric_by_model: dict[str, dict[str, Any]] = {}
    temporary_artifacts: dict[str, Path] = {}
    for key in args.models:
        model_name = f"model_{key}"
        artifact_path = artifact_paths[key]
        module_name, class_name = MODEL_MODULES[key]
        model_class = getattr(importlib.import_module(module_name), class_name)
        model = model_class()
        metrics = model.train(frame)
        temporary_path = artifact_path.with_suffix(artifact_path.suffix + ".tmp")
        model.save(str(temporary_path))
        temporary_artifacts[key] = temporary_path
        metric_results[model_name] = dict(metrics)
        metric_by_model[model_name] = dict(metrics)

    metrics_path = saved_dir / f"metrics_{base}_{args.interval}.json"
    metadata = {
        "symbol": args.symbol,
        "interval": args.interval,
        "days": args.days,
        "first_candle": frame["timestamp"].iloc[0].isoformat() if "timestamp" in frame else None,
        "last_candle": frame["timestamp"].iloc[-1].isoformat() if "timestamp" in frame else None,
        "n_candles": len(frame),
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "models": metric_results,
    }
    temporary_metrics_path = metrics_path.with_suffix(metrics_path.suffix + ".tmp")
    temporary_metrics_path.write_text(
        json.dumps(metadata, indent=2, allow_nan=False), encoding="utf-8"
    )
    os.replace(temporary_metrics_path, metrics_path)
    for key, temporary_path in temporary_artifacts.items():
        os.replace(temporary_path, artifact_paths[key])

    print("Modelo         accuracy  baseline  roc_auc  signal_coverage  signal_accuracy")
    for model_name, metrics in metric_by_model.items():
        signal_accuracy = metrics.get("signal_accuracy")
        signal_text = "N/A" if signal_accuracy is None else f"{signal_accuracy:.4f}"
        print(
            f"{model_name:<14} {metrics['accuracy']:.4f}    "
            f"{metrics['baseline_accuracy']:.4f}    {metrics['roc_auc']:.4f}    "
            f"{metrics['signal_coverage']:.4f}           {signal_text}"
        )
    print(f"Métricas guardadas: {metrics_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
