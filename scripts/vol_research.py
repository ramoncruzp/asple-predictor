"""Research realized-volatility forecasts without changing runtime models."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from data.splits import chronological_split
from data.volatility import aggregate_intraday_to_hourly, build_volatility_frame, feature_columns
from models.volatility import (
    EWMAModel,
    GARCHModel,
    GBMModel,
    HARAsymModel,
    HARModel,
    HARQModel,
    HARRangeModel,
    NexoHARModel,
    PersistenceModel,
)
from models.volatility.evaluation import moving_block_bootstrap_difference


def score_forecast(actual, predicted) -> dict[str, float | None]:
    y = np.asarray(actual, dtype="float64")
    forecast = np.asarray(predicted, dtype="float64")
    valid = np.isfinite(y) & np.isfinite(forecast)
    y, forecast = y[valid], forecast[valid]
    if not len(y):
        return {
            "n": 0, "r2_oos": None, "mse_log": None, "qlike": None,
            "bias_log": None, "var_ratio": None,
        }
    residual = y - forecast
    sst = np.square(y - y.mean()).sum()
    r2 = 1.0 - float(np.square(residual).sum()) / float(sst) if sst > 0.0 else None
    variance_real = np.exp(2.0 * y)
    variance_pred = np.exp(2.0 * forecast)
    ratio = variance_real / variance_pred
    qlike = np.mean(ratio - np.log(ratio) - 1.0)
    return {
        "n": int(len(y)),
        "r2_oos": float(r2) if r2 is not None else None,
        "mse_log": float(np.mean(np.square(residual))),
        "qlike": float(qlike),
        "bias_log": float(np.mean(forecast - y)),
        "var_ratio": float(variance_real.mean() / variance_pred.mean()),
    }


def validation_calibration_offset(actual, predicted) -> float:
    """Return the additive log-vol offset estimated from validation rows only."""
    aligned = pd.concat(
        [pd.Series(actual, name="actual"), pd.Series(predicted, name="predicted")], axis=1
    ).replace([np.inf, -np.inf], np.nan).dropna()
    if aligned.empty:
        raise ValueError("No hay predicciones válidas en VAL para calibrar")
    return float((aligned["actual"] - aligned["predicted"]).mean())


def _aligned_losses(actual: pd.Series, left: pd.Series, right: pd.Series):
    frame = pd.concat(
        [actual.rename("actual"), left.rename("left"), right.rename("right")], axis=1
    ).replace([np.inf, -np.inf], np.nan).dropna()
    return np.square(frame["actual"] - frame["left"]), np.square(frame["actual"] - frame["right"])


def _format(value) -> str:
    return "N/A" if value is None else f"{value:.4f}"


def research_horizon(
    candles: pd.DataFrame, horizon: int, intraday: pd.DataFrame | None = None
) -> dict[str, Any]:
    frame = build_volatility_frame(candles, horizon, intraday=intraday)
    columns = feature_columns(horizon) + ["target_logvol"]
    valid_mask = np.isfinite(frame[columns].to_numpy(dtype="float64")).all(axis=1)
    valid_mask &= frame["complete_hour"].to_numpy(dtype=bool)
    data = frame.loc[valid_mask].reset_index(drop=True)
    train_slice, val_slice, test_slice = chronological_split(len(data), embargo=horizon)
    train, val, test = data.iloc[train_slice], data.iloc[val_slice], data.iloc[test_slice]

    models = {
        "Persistence": PersistenceModel(horizon),
        "EWMA": EWMAModel(horizon),
        "HAR": HARModel(horizon),
        "HAR_range": HARRangeModel(horizon),
        "HAR_asym": HARAsymModel(horizon),
        "GBM": GBMModel(horizon),
        "GARCH_t": GARCHModel(horizon),
        "HARQ": HARQModel(horizon),
        "NexoHAR": NexoHARModel(horizon),
    }
    predictions: dict[str, pd.Series] = {}
    model_results: dict[str, Any] = {}
    actual = data["target_logvol"]
    for name, model in models.items():
        model.fit(train, val)
        predictions[name] = model.predict(data)
        val_score = score_forecast(val["target_logvol"], predictions[name].loc[val.index])
        test_score = score_forecast(test["target_logvol"], predictions[name].loc[test.index])
        model_results[name] = {
            "validation": val_score,
            "test": test_score,
        }
        if name == "HAR_range":
            model_results[name]["selected_estimator"] = model.selected_estimator
            model_results[name]["selection_mse_val"] = model.selection_mse_val
        if name == "GBM":
            model_results[name]["best_iteration"] = model.best_iteration_

        offset = validation_calibration_offset(
            val["target_logvol"], predictions[name].loc[val.index]
        )
        calibrated_name = f"{name}_cal"
        predictions[calibrated_name] = predictions[name] + offset
        model_results[calibrated_name] = {
            "validation": score_forecast(
                val["target_logvol"], predictions[calibrated_name].loc[val.index]
            ),
            "test": score_forecast(
                test["target_logvol"], predictions[calibrated_name].loc[test.index]
            ),
            "calibration_offset_val": offset,
            "calibrated_from": name,
        }
        if name == "HAR_range":
            model_results[calibrated_name]["selected_estimator"] = model.selected_estimator
        if name == "GBM":
            model_results[calibrated_name]["best_iteration"] = model.best_iteration_

    comparisons: dict[str, Any] = {}
    persistence_loss, har_loss = _aligned_losses(
        actual.iloc[test_slice], predictions["Persistence"].iloc[test_slice],
        predictions["HAR"].iloc[test_slice],
    )
    comparison = moving_block_bootstrap_difference(
        persistence_loss, har_loss, block_size=168, n_bootstrap=2000, seed=42
    )
    comparison["comparison"] = "loss_persistence - loss_har"
    comparison["har_beats_persistence"] = comparison["beats"]
    comparisons["har_beats_persistence"] = comparison

    for name in ("EWMA", "HAR_range", "HAR_asym", "GBM", "GARCH_t", "HARQ", "NexoHAR"):
        har_loss, challenger_loss = _aligned_losses(
            actual.iloc[test_slice], predictions["HAR"].iloc[test_slice],
            predictions[name].iloc[test_slice],
        )
        comparison = moving_block_bootstrap_difference(
            har_loss, challenger_loss, block_size=168, n_bootstrap=2000, seed=42
        )
        comparison["comparison"] = "loss_har - loss_challenger"
        comparison["beats_har"] = comparison["beats"]
        comparisons[name] = comparison

    for name in models:
        har_loss, calibrated_loss = _aligned_losses(
            actual.iloc[test_slice], predictions["HAR"].iloc[test_slice],
            predictions[f"{name}_cal"].iloc[test_slice],
        )
        comparison = moving_block_bootstrap_difference(
            har_loss, calibrated_loss, block_size=168, n_bootstrap=2000, seed=42
        )
        comparison["comparison"] = "loss_har - loss_calibrated_model"
        comparison["beats_har"] = comparison["beats"]
        comparisons[f"{name}_cal"] = comparison

    table_rows = []
    for name in models:
        for output_name in (name, f"{name}_cal"):
            result = model_results[output_name]
            comparison = comparisons.get(output_name)
            if output_name == "HAR":
                comparison = comparisons["har_beats_persistence"]
                verdict = comparison["har_beats_persistence"]
                diff = comparison["mean_difference"]
            elif comparison:
                verdict = comparison.get("beats_har", False)
                diff = comparison["mean_difference"]
            else:
                verdict, diff = None, None
            table_rows.append({
                "model": output_name,
                "range_estimator": result.get("selected_estimator"),
                "r2_val": result["validation"]["r2_oos"],
                "r2_test": result["test"]["r2_oos"],
                "mse_val": result["validation"]["mse_log"],
                "mse_test": result["test"]["mse_log"],
                "qlike_val": result["validation"]["qlike"],
                "qlike_test": result["test"]["qlike"],
                "bias_log_val": result["validation"]["bias_log"],
                "bias_log_test": result["test"]["bias_log"],
                "var_ratio_val": result["validation"]["var_ratio"],
                "var_ratio_test": result["test"]["var_ratio"],
                "calibration_offset_val": result.get("calibration_offset_val"),
                "diff_vs_reference": diff,
                "ci95_low": comparison["ci95_low"] if comparison else None,
                "ci95_high": comparison["ci95_high"] if comparison else None,
                "verdict": verdict,
            })

    return {
        "horizon": horizon,
        "rows": {"valid": len(data), "train": len(train), "validation": len(val), "test": len(test)},
        "range_selection": model_results["HAR_range"]["selected_estimator"],
        "models": model_results,
        "comparisons": comparisons,
        "table": table_rows,
    }


def print_table(result: dict[str, Any]) -> None:
    print(f"\nHORIZON {result['horizon']}h")
    print(
        "model range r2_val r2_test mse_val mse_test qlike_val qlike_test "
        "bias_val bias_test var_ratio_val var_ratio_test diff_vs_ref "
        "ci95_low ci95_high verdict"
    )
    for row in result["table"]:
        label = row["model"]
        if row["range_estimator"]:
            label = f"{label}({row['range_estimator']})"
        verdict = "reference" if row["verdict"] is None else str(row["verdict"]).lower()
        print(
            f"{label} {_format(row['r2_val'])} {_format(row['r2_test'])} "
            f"{_format(row['mse_val'])} {_format(row['mse_test'])} "
            f"{_format(row['qlike_val'])} {_format(row['qlike_test'])} "
            f"{_format(row['bias_log_val'])} {_format(row['bias_log_test'])} "
            f"{_format(row['var_ratio_val'])} {_format(row['var_ratio_test'])} "
            f"{_format(row['diff_vs_reference'])} {_format(row['ci95_low'])} "
            f"{_format(row['ci95_high'])} {verdict}"
        )


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candles", type=Path, default=Path("data/cache/xrp_1h.csv"))
    parser.add_argument("--horizons", default="4,24")
    parser.add_argument("--candles-5m", type=Path, default=None)
    parser.add_argument("--out", type=Path, default=None)
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    horizons = [int(value.strip()) for value in args.horizons.split(",") if value.strip()]
    if not horizons or any(horizon < 1 for horizon in horizons):
        raise ValueError("--horizons debe contener enteros positivos separados por coma")
    candles = pd.read_csv(args.candles)
    for column in ("timestamp", "close_time"):
        if column in candles:
            candles[column] = pd.to_datetime(candles[column], utc=True)
    intraday = None
    if args.candles_5m is not None:
        candles_5m = pd.read_csv(args.candles_5m)
        for column in ("timestamp", "close_time"):
            if column in candles_5m:
                candles_5m[column] = pd.to_datetime(candles_5m[column], utc=True)
        intraday = aggregate_intraday_to_hourly(candles_5m)
        candles["timestamp"] = pd.to_datetime(candles["timestamp"], utc=True).dt.floor("h")
        intraday["timestamp"] = pd.to_datetime(intraday["timestamp"], utc=True).dt.floor("h")
        common_start = max(candles["timestamp"].min(), intraday["timestamp"].min())
        common_end = min(candles["timestamp"].max(), intraday["timestamp"].max())
        candles = candles.loc[candles["timestamp"].between(common_start, common_end)].reset_index(drop=True)
        intraday = intraday.loc[intraday["timestamp"].between(common_start, common_end)].reset_index(drop=True)
        if candles.empty or intraday.empty:
            raise ValueError("No hay rango de fechas común entre los datos 1h y 5m")
    out_path = args.out or Path(
        "models/saved/vol_research_xrp_5m.json"
        if args.candles_5m is not None
        else "models/saved/vol_research_xrp_1h.json"
    )
    report = {
        "candles": str(args.candles),
        "candles_5m": str(args.candles_5m) if args.candles_5m is not None else None,
        "measurement": "intraday 5m aggregated to UTC hours" if intraday is not None else "hourly",
        "metrics": {
            "r2_oos": "1 - SSE / within-partition SST",
            "mse_log": "mean squared error of log realized volatility",
            "qlike": "mean(var_real / var_pred - log(var_real / var_pred) - 1)",
        },
        "bootstrap": {"method": "circular moving blocks", "block_size": 168, "n_bootstrap": 2000, "seed": 42},
        "horizons": {},
    }
    for horizon in horizons:
        result = research_horizon(candles, horizon, intraday=intraday)
        report["horizons"][str(horizon)] = result
        print_table(result)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2, allow_nan=False), encoding="utf-8")
    print(f"\nJSON: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
