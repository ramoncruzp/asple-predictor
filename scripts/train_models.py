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

import pandas as pd
import numpy as np
from sklearn.preprocessing import StandardScaler

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
RUN_METADATA_FIELDS = ("n_candles", "first_candle", "last_candle", "days")


def monthly_down_splits(prepared: pd.DataFrame):
    """Yield each test month with only training labels settled before that month."""
    month_keys = prepared["timestamp"].dt.tz_convert(None).dt.to_period("M")
    months = sorted(month_keys.unique())
    for month in months:
        start = month.start_time.tz_localize("UTC")
        train = prepared.loc[prepared["label_end"] < start]
        test = prepared.loc[month_keys == month]
        if len(train) >= 200 and len(test) and train.target.nunique() >= 2:
            yield start, train, test


def walk_forward_down(frame: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Predict each month using only labels whose four-hour outcome predates it."""
    from config.models_config import TARGET_DOWN_HORIZON_CANDLES
    from models.model_a_xgboost import ModelADown
    engineer = ModelADown().feature_engineer
    prepared = engineer.prepare_for_model(frame)
    prepared["timestamp"] = pd.to_datetime(prepared["timestamp"], utc=True)
    prepared["label_end"] = prepared["timestamp"] + pd.Timedelta(hours=TARGET_DOWN_HORIZON_CANDLES)
    outputs = []
    for start, train, test in monthly_down_splits(prepared):
        model = ModelADown()
        x_train = train[model.feature_names].astype("float64")
        scaler = StandardScaler().fit(x_train)
        classifier = model._new_classifier(int((train.target == 0).sum()) /
            max(1, int((train.target == 1).sum())))
        classifier.fit(scaler.transform(x_train), train.target.astype("int8"), verbose=False)
        p_down = classifier.predict_proba(scaler.transform(
            test[model.feature_names].astype("float64")))[:, 1]
        outputs.append(pd.DataFrame({"timestamp": test.timestamp.to_numpy(),
                                     "p_down": p_down, "actual_down": test.target.to_numpy()}))
    oos = pd.concat(outputs, ignore_index=True) if outputs else pd.DataFrame(
        columns=["timestamp", "p_down", "actual_down"])
    # Keep one observation per non-overlapping four-candle block, anchored at UTC hour 00.
    if len(oos):
        oos["timestamp"] = pd.to_datetime(oos.timestamp, utc=True)
        oos = oos.sort_values("timestamp").drop_duplicates("timestamp")
        oos = oos.loc[oos.timestamp.dt.hour.isin((0, 4, 8, 12, 16, 20))].reset_index(drop=True)
    return prepared, oos


def down_oos_metrics(oos: pd.DataFrame, *, samples: int = 2000, seed: int = 3601) -> dict:
    if oos.empty:
        return {"n": 0, "sign_accuracy": None, "thresholds": {}}
    actual = oos.actual_down.to_numpy(dtype=int)
    p = oos.p_down.to_numpy(dtype=float)
    correct = ((p >= .5).astype(int) == actual).astype(float)
    rng = np.random.default_rng(seed)
    def sampled_indices():
        block = 4
        count = int(np.ceil(len(correct) / block))
        starts = rng.integers(0, len(correct), size=count)
        return np.concatenate([(start + np.arange(block)) % len(correct) for start in starts])[:len(correct)]
    accuracy_draws = [correct[idx].mean() for idx in (sampled_indices() for _ in range(samples))]
    result = {"n": len(oos), "sign_accuracy": float(correct.mean()),
              "sign_accuracy_ci95": [float(v) for v in np.quantile(accuracy_draws, [.025, .975])],
              "thresholds": {}}
    for threshold in (.55, .60, .65):
        selected = p >= threshold
        vals = actual[selected]
        draws = []
        for _ in range(samples):
            idx = sampled_indices()
            mask = p[idx] >= threshold
            draws.append(float(actual[idx][mask].mean()) if mask.any() else np.nan)
        valid_draws = np.asarray(draws, dtype=float)
        valid_draws = valid_draws[np.isfinite(valid_draws)]
        result["thresholds"][str(threshold)] = {
            "precision": float(vals.mean()) if len(vals) else None,
            "precision_ci95": ([float(v) for v in np.quantile(valid_draws, [.025, .975])]
                                if len(valid_draws) else None),
            "coverage_pct": float(selected.mean() * 100), "n": int(selected.sum())}
    return result


def train_down_symbol(frame: pd.DataFrame, symbol: str, root: Path) -> dict:
    from models.model_a_xgboost import ModelADown
    _, oos = walk_forward_down(frame)
    metrics = down_oos_metrics(oos)
    base = symbol.lower().removesuffix("usdt")
    derived = root / "data" / "derived"
    derived.mkdir(parents=True, exist_ok=True)
    oos[["timestamp", "p_down"]].to_csv(derived / f"model_a_down_{base}_oos.csv", index=False)
    model = ModelADown()
    final_metrics = model.train(frame)
    saved = root / "models" / "saved"
    saved.mkdir(parents=True, exist_ok=True)
    model.save(str(saved / f"model_a_down_{base}_1h.joblib"))
    payload = {"model_name": "model_a_down", "symbol": symbol, "interval": "1h",
               "target_down_horizon_candles": 4, "target": "close[t+4]/close[t]-1 < 0",
               "oos": metrics, "final_fit": final_metrics}
    (saved / f"model_a_down_{base}_1h.json").write_text(
        json.dumps(payload, indent=2, allow_nan=False), encoding="utf-8")
    return payload


def merge_training_metrics(existing, run_metadata, trained_models):
    """Merge this run's model entries into valid prior metrics without losing peers."""
    prior = existing if isinstance(existing, dict) else {}
    prior_models = prior.get("models") if isinstance(prior.get("models"), dict) else {}
    merged = dict(prior)
    merged.update(run_metadata)
    merged["models"] = {**prior_models, **trained_models}
    return merged


def replace_training_outputs(temporary_artifacts, artifact_paths, temporary_metrics_path, metrics_path):
    """Publish all weights first and the metrics manifest last."""
    for key, temporary_path in temporary_artifacts.items():
        os.replace(temporary_path, artifact_paths[key])
    os.replace(temporary_metrics_path, metrics_path)


def read_existing_metrics(path):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def _binance_client_class():
    return importlib.import_module("data.binance_client").BinanceClient


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Entrena los modelos A, B y C con velas Binance.")
    parser.add_argument("--symbol", default="XRPUSDT")
    binance_client = _binance_client_class()
    parser.add_argument("--interval", default="1h", choices=sorted(binance_client.VALID_INTERVALS))
    parser.add_argument("--days", type=int, default=730)
    parser.add_argument("--models", default="a,b,c", help="Lista separada por comas: a,b,c")
    parser.add_argument("--save-candles", type=Path, help="Guarda las velas cerradas usadas en CSV")
    parser.add_argument("--candles", type=Path, help="Carga velas de un CSV en vez de Binance")
    parser.add_argument("--down-walk-forward", action="store_true",
                        help="Entrena el clasificador de caída usando walk-forward mensual; requiere --candles")
    parser.add_argument("--progress", action="store_true", help="Imprime eventos de progreso para el monitor de trabajos")
    parser.add_argument(
        "--force", action="store_true", help="Permite sobrescribir artefactos 4h existentes"
    )
    args = parser.parse_args()
    if args.days <= 0:
        parser.error("--days debe ser mayor que cero")
    if getattr(args, "down_walk_forward", False):
        if args.interval != "1h" or args.candles is None or args.symbol not in {"XRPUSDT", "PEPEUSDT", "ADAUSDT"}:
            parser.error("--down-walk-forward requiere --candles, --interval 1h y símbolo XRPUSDT, PEPEUSDT o ADAUSDT")
        args.models = []
        return args
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
    if getattr(args, "down_walk_forward", False):
        frame = pd.read_csv(args.candles)
        for column in ("timestamp", "close_time"):
            if column in frame.columns:
                frame[column] = pd.to_datetime(frame[column], utc=True)
        result = train_down_symbol(frame, args.symbol, Path(__file__).resolve().parents[1])
        print(json.dumps(result, indent=2, allow_nan=False))
        return 0
    def progress(value):
        if args.progress:
            print(f"PROGRESS:{value}", flush=True)

    progress("descargando")
    if args.candles:
        frame = pd.read_csv(args.candles)
        for column in ("timestamp", "close_time"):
            if column in frame.columns:
                frame[column] = pd.to_datetime(frame[column], utc=True)
    else:
        binance_client = _binance_client_class()
        client = binance_client("", "")
        frame = client.get_historical_klines(args.symbol, args.interval, args.days)
    if "close_time" in frame.columns and not frame.empty:
        now = datetime.now(timezone.utc)
        close_times = pd.to_datetime(frame["close_time"], utc=True)
        frame = frame.loc[close_times <= now].reset_index(drop=True)
    if frame.empty:
        raise RuntimeError("Binance no devolvió velas cerradas para entrenar")

    if args.save_candles:
        args.save_candles.parent.mkdir(parents=True, exist_ok=True)
        frame.to_csv(args.save_candles, index=False)

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

    metrics_path = saved_dir / f"metrics_{base}_{args.interval}.json"
    temporary_metrics_path = metrics_path.with_suffix(metrics_path.suffix + ".tmp")
    existing_metrics = read_existing_metrics(metrics_path)
    metric_results: dict[str, dict[str, Any]] = {}
    metric_by_model: dict[str, dict[str, Any]] = {}
    temporary_artifacts: dict[str, Path] = {}
    first_candle = frame["timestamp"].iloc[0].isoformat() if "timestamp" in frame else None
    last_candle = frame["timestamp"].iloc[-1].isoformat() if "timestamp" in frame else None
    run_trained_at = datetime.now(timezone.utc).isoformat()
    try:
        for key in args.models:
            model_name = f"model_{key}"
            artifact_path = artifact_paths[key]
            module_name, class_name = MODEL_MODULES[key]
            model_class = getattr(importlib.import_module(module_name), class_name)
            model = model_class()
            progress(f"entrenando:{model_name}")
            metrics = model.train(frame)
            temporary_path = artifact_path.with_suffix(artifact_path.suffix + ".tmp")
            temporary_artifacts[key] = temporary_path
            model.save(str(temporary_path))
            entry = dict(metrics)
            entry.update({
                "trained_at": entry.get("trained_at") or run_trained_at,
                "n_candles": len(frame), "first_candle": first_candle,
                "last_candle": last_candle, "days": args.days,
            })
            metric_results[model_name] = entry
            metric_by_model[model_name] = dict(metrics)

        metadata = {
            "symbol": args.symbol, "interval": args.interval, "days": args.days,
            "first_candle": first_candle, "last_candle": last_candle,
            "n_candles": len(frame), "trained_at": run_trained_at,
        }
        metadata = merge_training_metrics(existing_metrics, metadata, metric_results)
        temporary_metrics_path.write_text(
            json.dumps(metadata, indent=2, allow_nan=False), encoding="utf-8"
        )
        progress("guardando")
        replace_training_outputs(temporary_artifacts, artifact_paths, temporary_metrics_path, metrics_path)
    finally:
        for temporary_path in [*temporary_artifacts.values(), temporary_metrics_path]:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass

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
