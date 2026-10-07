"""Offline 70/15/15 evaluation for the XRP volatility model consensus."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config.models_config import VOL_ARTIFACT_DIR, VOL_CHAMPIONS, VOL_HORIZONS, VOL_MODELS, VOL_SYMBOL, vol_base, vol_consensus_path
from data.splits import chronological_split
from data.volatility import build_volatility_frame, feature_columns
from models.volatility.consensus import (
    dispersion_confidence, inverse_mse_weights, median_series, weighted_series,
)
from models.volatility.evaluation import moving_block_bootstrap_difference
from scripts.train_vol_models import _load_data, _new_model
from scripts.vol_research import score_forecast

SEED = 42
BOOTSTRAP_REPLICATES = 2000
BLOCK_HOURS = 168
CONFIDENCE_THRESHOLDS = {"high_below": 0.10, "medium_through": 0.25, "minimum_models": 3}


def _scores(target: np.ndarray, prediction: np.ndarray) -> dict:
    valid = np.isfinite(target) & np.isfinite(prediction)
    if not valid.any():
        return {"n": 0, "r2_oos": None, "mse_log": None, "qlike": None, "var_ratio": None}
    return score_forecast(target[valid], prediction[valid])


def _calibrate_on_val(target: np.ndarray, prediction: np.ndarray) -> tuple[np.ndarray, dict]:
    valid = np.isfinite(target) & np.isfinite(prediction)
    if not valid.any():
        return prediction.copy(), {"factor": None, "applied": False, "raw": _scores(target, prediction), "calibrated": None}
    factor = float(np.exp(2.0 * target[valid]).mean() / np.exp(2.0 * prediction[valid]).mean())
    calibrated = prediction + 0.5 * np.log(factor)
    raw_score = _scores(target, prediction)
    calibrated_score = _scores(target, calibrated)
    raw_ratio_error = abs(float(raw_score["var_ratio"]) - 1.0)
    calibrated_ratio_error = abs(float(calibrated_score["var_ratio"]) - 1.0)
    apply = (
        calibrated_score["qlike"] < raw_score["qlike"]
        and calibrated_ratio_error < raw_ratio_error
    )
    return (calibrated if apply else prediction.copy()), {
        "factor": factor, "applied": bool(apply), "raw": raw_score, "calibrated": calibrated_score,
    }


def _aligned_difference(target, reference, candidate) -> dict | None:
    valid = np.isfinite(target) & np.isfinite(reference) & np.isfinite(candidate)
    if not valid.any():
        return None
    ref_loss = np.square(target[valid] - reference[valid])
    candidate_loss = np.square(target[valid] - candidate[valid])
    return moving_block_bootstrap_difference(
        ref_loss, candidate_loss, block_size=BLOCK_HOURS,
        n_bootstrap=BOOTSTRAP_REPLICATES, seed=SEED,
    )


def _validation_selection(target_val: np.ndarray, val_predictions: dict[str, np.ndarray]) -> dict:
    """Determine eligibility and inverse-MSE weights exclusively from VAL."""
    baseline = val_predictions["Persistence"]
    mse_val = {
        name: _scores(target_val, val_predictions[name])["mse_log"]
        for name in VOL_MODELS
    }
    eligibility = {}
    eligible = []
    for name in VOL_MODELS:
        if name == "Persistence":
            eligibility[name] = {"eligible": False, "reason": "referencia"}
            continue
        difference = _aligned_difference(target_val, baseline, val_predictions[name])
        beats = bool(difference and difference["beats"])
        eligibility[name] = {"eligible": beats, "vs_persistence": difference}
        if beats:
            eligible.append(name)
    return {
        "eligible_models": eligible,
        "eligibility": eligibility,
        "validation_mse": mse_val,
        "weights": inverse_mse_weights(mse_val, eligible),
    }


def _select_and_score_splits(
    target_val: np.ndarray, val_predictions: dict[str, np.ndarray],
    target_test: np.ndarray, test_predictions: dict[str, np.ndarray],
) -> dict:
    """Freeze eligibility/weights on VAL, then score model forecasts on TEST."""
    return {
        "selection": _validation_selection(target_val, val_predictions),
        "test_scores": {
            name: _scores(target_test, test_predictions[name]) for name in VOL_MODELS
        },
    }


def _holm(p_values: dict[str, float | None]) -> dict[str, float | None]:
    valid = sorted((key, value) for key, value in p_values.items() if value is not None)
    adjusted = {}
    running = 0.0
    count = len(valid)
    for rank, (key, value) in enumerate(valid):
        running = max(running, min(1.0, (count - rank) * value))
        adjusted[key] = running
    return {key: adjusted.get(key) for key in p_values}


def _confidence_distribution(predictions: dict[str, np.ndarray], eligible: list[str]) -> dict:
    if not eligible:
        return {"alta": 0, "media": 0, "baja": 0, "n": 0}
    matrix = np.vstack([predictions[name] for name in eligible])
    counts = {"alta": 0, "media": 0, "baja": 0}
    for index, column in enumerate(matrix.T):
        point = {name: matrix[i, index] for i, name in enumerate(eligible) if np.isfinite(column[i])}
        if not point:
            continue
        counts[dispersion_confidence(point, eligible)["confidence"]] += 1
    counts["n"] = sum(counts.values())
    return counts


def validate_split_capacity(n_rows: int, horizon: int, symbol: str) -> None:
    try:
        chronological_split(n_rows, train=0.70, val=0.15, embargo=horizon)
    except ValueError as exc:
        raise ValueError(f"Datos insuficientes para {symbol} a {horizon}h: {exc}") from exc


def evaluate_horizon(data: pd.DataFrame, horizon: int, symbol: str = VOL_SYMBOL) -> dict:
    frame = build_volatility_frame(data.attrs["candles"], horizon, intraday=data.attrs["intraday"])
    columns = feature_columns(horizon) + ["target_logvol"]
    valid = np.isfinite(frame[columns].to_numpy(dtype="float64")).all(axis=1)
    valid &= frame["complete_hour"].to_numpy(dtype=bool)
    rows = frame.loc[valid].reset_index(drop=True)
    validate_split_capacity(len(rows), horizon, symbol)
    train_slice, val_slice, test_slice = chronological_split(len(rows), train=0.70, val=0.15, embargo=horizon)
    train, val, test = rows.iloc[train_slice], rows.iloc[val_slice], rows.iloc[test_slice]
    predictions_raw: dict[str, np.ndarray] = {}
    selected_estimators = {}
    for name in VOL_MODELS:
        model = _new_model(name, horizon)
        model.fit(train, val)
        series = model.predict(rows)
        predictions_raw[name] = series.to_numpy(dtype="float64")
        if name == "HAR_range":
            selected_estimators[name] = model.selected_estimator

    val_positions = np.arange(val_slice.start, val_slice.stop)
    test_positions = np.arange(test_slice.start, test_slice.stop)
    target = rows["target_logvol"].to_numpy(dtype="float64")
    target_val, target_test = target[val_positions], target[test_positions]
    val_predictions: dict[str, np.ndarray] = {}
    test_predictions: dict[str, np.ndarray] = {}
    model_metrics, calibration = {}, {}
    for name, prediction in predictions_raw.items():
        val_pred = prediction[val_positions]
        test_pred = prediction[test_positions]
        calibrated_val, calibration_info = _calibrate_on_val(target_val, val_pred)
        calibrated_test = test_pred + 0.5 * np.log(calibration_info["factor"]) if calibration_info["applied"] else test_pred.copy()
        val_predictions[name] = calibrated_val
        test_predictions[name] = calibrated_test
        calibration[name] = calibration_info
        model_metrics[name] = {
            "validation": _scores(target_val, calibrated_val),
        }

    # Eligibility and weights are frozen from VAL before TEST scoring.
    split_result = _select_and_score_splits(
        target_val, val_predictions, target_test, test_predictions
    )
    selection = split_result["selection"]
    for name in VOL_MODELS:
        model_metrics[name]["test"] = split_result["test_scores"][name]
    eligible = selection["eligible_models"]
    weights = selection["weights"]

    variants_val, variants_test, variant_calibration = {}, {}, {}
    if eligible:
        val_map = {name: val_predictions[name] for name in eligible}
        test_map = {name: test_predictions[name] for name in eligible}
        variants_val["P"] = weighted_series(val_map, weights)
        variants_test["P"] = weighted_series(test_map, weights)
        variants_val["M"] = median_series(val_map, eligible)
        variants_test["M"] = median_series(test_map, eligible)
    else:
        variants_val = {"P": np.full(len(target_val), np.nan), "M": np.full(len(target_val), np.nan)}
        variants_test = {"P": np.full(len(target_test), np.nan), "M": np.full(len(target_test), np.nan)}

    ensemble_test_metrics, ensemble_calibration = {}, {}
    for variant in ("P", "M"):
        calibrated_val, info = _calibrate_on_val(target_val, variants_val[variant])
        calibrated_test = variants_test[variant] + 0.5 * np.log(info["factor"]) if info["applied"] and info["factor"] else variants_test[variant]
        ensemble_calibration[variant] = info
        ensemble_test_metrics[variant] = _scores(target_test, calibrated_test)
        variants_val[variant] = calibrated_val
        variants_test[variant] = calibrated_test

    if symbol == VOL_SYMBOL:
        champion = VOL_CHAMPIONS[horizon]
    else:
        champion = min(eligible, key=lambda name: selection["validation_mse"].get(name, float("inf"))) if eligible else "Persistence"
    champion_test = test_predictions[champion]
    comparisons = {}
    model_table = {}
    for name in VOL_MODELS:
        comparison = None if name == champion else _aligned_difference(target_test, champion_test, test_predictions[name])
        comparisons[name] = comparison
        model_table[name] = {
            **model_metrics[name]["test"],
            "diff_vs_champion": comparison,
            "holm_adjusted_p": None,
            "validation_status": "champion" if name == champion else "diagnostic_only",
            "eligible": name in eligible,
            "weight": weights.get(name, 0.0),
        }
    for variant in ("P", "M"):
        comparisons[variant] = _aligned_difference(target_test, champion_test, variants_test[variant]) if eligible else None

    distribution = _confidence_distribution(val_predictions, eligible)
    return {
        "horizon_h": horizon,
        "rows": {"valid": len(rows), "train": len(train), "validation": len(val), "test": len(test), "embargo_each_boundary": horizon},
        "champion": champion,
        "selected_estimators": selected_estimators,
        "eligible_models": eligible,
        "eligibility": selection["eligibility"],
        "weights": weights,
        "validation_mse": selection["validation_mse"],
        "calibration_models": calibration,
        "confidence_distribution_val": distribution,
        "model_results_test": model_table,
        "ensemble_results_test": ensemble_test_metrics,
        "ensemble_calibration_val": ensemble_calibration,
        "comparisons_test": comparisons,
    }


def evaluate_dataset(candles: pd.DataFrame, intraday: pd.DataFrame, symbol: str = VOL_SYMBOL) -> dict:
    """Fit models in memory and evaluate; no artifact writes occur here."""
    results = {}
    for horizon in VOL_HORIZONS:
        prepared = pd.DataFrame()
        prepared.attrs["candles"] = candles
        prepared.attrs["intraday"] = intraday
        results[str(horizon)] = evaluate_horizon(prepared, horizon, symbol)
    p_values = {}
    for key, result in results.items():
        for variant in ("P", "M"):
            comparison = result["comparisons_test"][variant]
            p_values[f"{key}h_{variant}"] = comparison.get("p_value_one_sided", 1.0) if comparison else 1.0
    adjusted = _holm(p_values)
    for key, result in results.items():
        for variant in ("P", "M"):
            comparison = result["comparisons_test"][variant]
            p_adj = adjusted[f"{key}h_{variant}"]
            validated = bool(
                comparison and comparison["beats"] and p_adj is not None and p_adj < 0.05
            )
            result["ensemble_results_test"][variant]["validation_status"] = (
                "validated" if validated else "not_validated"
            )
            result["ensemble_results_test"][variant]["diff_vs_champion"] = comparison
            result["ensemble_results_test"][variant]["holm_adjusted_p"] = p_adj
    return {"horizons": results, "holm_comparisons": 8, "holm_adjusted_p": adjusted}


def _json_safe(value):
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        return float(value) if np.isfinite(value) else None
    if isinstance(value, (np.bool_, bool)):
        return bool(value)
    return value


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", default=VOL_SYMBOL)
    parser.add_argument("--candles", type=Path)
    parser.add_argument("--candles-5m", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--progress", action="store_true")
    args = parser.parse_args(argv)
    base = vol_base(args.symbol)
    hourly_path = args.candles or Path("data/cache") / f"{base}_1h.csv"
    source = args.candles_5m or Path("data/cache") / f"{base}_5m.csv"
    output = args.output or vol_consensus_path(args.symbol)
    if output.exists():
        raise FileExistsError(f"No se sobrescribe el consenso existente para {args.symbol}: {output}")
    source_bytes = source.read_bytes()
    hourly_bytes = hourly_path.read_bytes()
    if args.progress:
        print("PROGRESS:consensuando", flush=True)
    candles, intraday = _load_data(hourly_path, source)
    report = evaluate_dataset(candles, intraday, args.symbol)
    serialized = {
        "symbol": args.symbol,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_csv": str(source),
        "source_csv_sha256": hashlib.sha256(source_bytes).hexdigest(),
        "hourly_csv": str(hourly_path),
        "hourly_csv_sha256": hashlib.sha256(hourly_bytes).hexdigest(),
        "seed": SEED,
        "split": {"train": 0.70, "validation": 0.15, "test": 0.15, "embargo": "horizon rows"},
        "bootstrap": {"block_size_hours": BLOCK_HOURS, "replicates": BOOTSTRAP_REPLICATES, "seed": SEED},
        "confidence_thresholds": CONFIDENCE_THRESHOLDS,
        "selection_basis": "candidate estimators fit on TRAIN; HAR_range selection, eligibility, and weights use VAL; TEST is scored only after selection",
        "test_is_virgin": args.symbol != VOL_SYMBOL,
        "report": report,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    lock = output.with_name(output.name + ".lock")
    temporary = output.with_name(output.name + ".tmp")
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise FileExistsError(f"Ya hay una evaluaci\u00f3n en curso para {args.symbol}: {lock}") from exc
    os.write(descriptor, str(os.getpid()).encode("ascii"))
    os.close(descriptor)
    try:
        if output.exists():
            raise FileExistsError(f"No se sobrescribe el consenso existente para {args.symbol}: {output}")
        if args.progress:
            print("PROGRESS:guardando", flush=True)
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(_json_safe(serialized), handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.write("\n")
        if output.exists():
            raise FileExistsError(f"No se sobrescribe el consenso existente para {args.symbol}: {output}")
        os.replace(temporary, output)
        if args.progress:
            print("PROGRESS:consenso_guardado", flush=True)
    finally:
        temporary.unlink(missing_ok=True)
        lock.unlink(missing_ok=True)
    print(f"JSON: {output}")
    print(f"CSV SHA256: {serialized['source_csv_sha256']}")
    for key, result in report["horizons"].items():
        print(f"{key}h rows={result.get('rows')} eligible={result.get('eligible_models')} weights={result.get('weights')}")
        print(f"  ensemble={result.get('ensemble_results_test')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
