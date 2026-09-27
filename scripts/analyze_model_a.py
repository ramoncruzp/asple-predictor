"""Measure Model A's bullish-signal precision on validation and held-out test data."""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config.models_config import TARGET_HORIZON_CANDLES
from models.model_a_xgboost import ModelA

THRESHOLDS = (0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80)
FEES_RETURN_THRESHOLD = 0.002


def nonoverlapping_signal_indices(signals, horizon: int = TARGET_HORIZON_CANDLES) -> list[int]:
    """Select signal row positions, skipping the next horizon - 1 rows."""
    if horizon < 1:
        raise ValueError("horizon debe ser al menos 1")
    selected: list[int] = []
    next_available = 0
    for index, is_signal in enumerate(np.asarray(signals, dtype=bool)):
        if is_signal and index >= next_available:
            selected.append(index)
            next_available = index + horizon
    return selected


def binomial_p_value(k: int, n: int, p: float) -> float | None:
    """Return the one-sided binomial tail probability P(X >= k)."""
    if n == 0:
        return None
    if n < 0 or k < 0 or k > n or not 0.0 <= p <= 1.0:
        raise ValueError("Se requiere 0 <= k <= n y 0 <= p <= 1")
    try:
        from scipy.stats import binomtest
    except ImportError:
        return float(sum(
            math.comb(n, successes) * p**successes * (1.0 - p) ** (n - successes)
            for successes in range(k, n + 1)
        ))
    return float(binomtest(k, n, p, alternative="greater").pvalue)


def _safe_mean(values) -> float | None:
    clean = pd.Series(values).dropna()
    return float(clean.mean()) if len(clean) else None


def evaluate_threshold(rows: pd.DataFrame, probabilities, threshold: float) -> dict[str, Any]:
    """Compute signal metrics for one chronological partition and threshold."""
    probabilities = np.asarray(probabilities, dtype="float64")
    if len(rows) != len(probabilities):
        raise ValueError("Las probabilidades no están alineadas con las filas")
    signals = probabilities > threshold
    signal_rows = rows.iloc[np.flatnonzero(signals)]
    nonoverlap_positions = nonoverlapping_signal_indices(signals)
    nonoverlap_rows = rows.iloc[nonoverlap_positions]
    target = rows["target"].astype(int)
    base_rate = float(target.mean()) if len(target) else None
    signal_targets = signal_rows["target"].astype(int)
    precision = float(signal_targets.mean()) if len(signal_rows) else None
    nonoverlap_correct = int(nonoverlap_rows["target"].astype(int).sum())
    future_returns = signal_rows["future_return"].dropna()
    mean_future_return = _safe_mean(future_returns)
    return {
        "threshold": float(threshold),
        "n_signals": int(len(signal_rows)),
        "n_signals_nonoverlap": int(len(nonoverlap_rows)),
        "precision": precision,
        "base_rate": base_rate,
        "lift": precision / base_rate if precision is not None and base_rate else None,
        "mean_future_return": mean_future_return,
        "median_future_return": (
            float(future_returns.median()) if len(future_returns) else None
        ),
        "mean_future_return_all": _safe_mean(rows["future_return"]),
        "pct_future_return_gt_fees": (
            float((future_returns > FEES_RETURN_THRESHOLD).mean() * 100.0)
            if len(future_returns) else None
        ),
        "binom_p_value": binomial_p_value(
            nonoverlap_correct, len(nonoverlap_rows), base_rate
        ) if base_rate is not None else None,
    }


def _monthly_test(rows: pd.DataFrame, probabilities, threshold: float) -> list[dict[str, Any]]:
    monthly_frame = rows.copy().reset_index(drop=True)
    monthly_frame["_probability"] = np.asarray(probabilities, dtype="float64")
    monthly_frame["_month"] = pd.to_datetime(monthly_frame["timestamp"], utc=True).dt.strftime("%Y-%m")
    months: list[dict[str, Any]] = []
    for month, group in monthly_frame.groupby("_month", sort=True):
        signals = group["_probability"] > threshold
        signal_rows = group.loc[signals]
        months.append({
            "month": str(month),
            "n_signals": int(len(signal_rows)),
            "precision": (
                float(signal_rows["target"].astype(int).mean()) if len(signal_rows) else None
            ),
            "base_rate": float(group["target"].astype(int).mean()),
            "mean_future_return": _safe_mean(signal_rows["future_return"]),
        })
    return months


def analyze(candles_path: Path, out_path: Path) -> dict[str, Any]:
    frame = pd.read_csv(candles_path)
    for column in ("timestamp", "close_time"):
        if column in frame.columns:
            frame[column] = pd.to_datetime(frame[column], utc=True)

    model = ModelA()
    training_metrics = model.train(frame)
    evaluation = model.evaluation_
    if evaluation is None:
        raise RuntimeError("ModelA.train no expuso evaluation_")

    val_results = [
        evaluate_threshold(evaluation["val_rows"], evaluation["val_proba"], threshold)
        for threshold in THRESHOLDS
    ]
    test_results = [
        evaluate_threshold(evaluation["test_rows"], evaluation["test_proba"], threshold)
        for threshold in THRESHOLDS
    ]
    val_base_rate = val_results[0]["base_rate"]
    selected_threshold = next((
        threshold for threshold, result in zip(THRESHOLDS, val_results)
        if result["n_signals"] >= 100
        and result["precision"] is not None
        and val_base_rate is not None
        and result["precision"] >= val_base_rate + 0.10
    ), None)

    monthly_test: list[dict[str, Any]] = []
    selected_test = None
    if selected_threshold is not None:
        test_index = THRESHOLDS.index(selected_threshold)
        selected_test = test_results[test_index]
        monthly_test = _monthly_test(
            evaluation["test_rows"], evaluation["test_proba"], selected_threshold
        )
        qualifying_months = sum(
            month["n_signals"] >= 20
            and month["precision"] is not None
            and month["precision"] >= month["base_rate"] + 0.10
            for month in monthly_test
        )
        verdict = {
            "c1": selected_test["n_signals"] >= 100,
            "c2": (
                selected_test["precision"] is not None
                and selected_test["base_rate"] is not None
                and selected_test["precision"] >= selected_test["base_rate"] + 0.10
            ),
            "c3": (
                selected_test["mean_future_return"] is not None
                and selected_test["mean_future_return"] >= 0.0025
            ),
            "c4": qualifying_months >= 2,
        }
    else:
        verdict = {"c1": False, "c2": False, "c3": False, "c4": False}
    verdict["pass"] = all(verdict.values())

    result = {
        "candles": str(candles_path),
        "thresholds": list(THRESHOLDS),
        "training_metrics": training_metrics,
        "validation": val_results,
        "test": test_results,
        "selected_threshold": selected_threshold,
        "selected_test": selected_test,
        "monthly_test": monthly_test,
        "verdict": verdict,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, indent=2, allow_nan=False), encoding="utf-8")
    _print_report(result)
    return result


def _format(value) -> str:
    return "N/A" if value is None else f"{value:.4f}"


def _print_report(result: dict[str, Any]) -> None:
    for label in ("validation", "test"):
        print(f"\n{label.upper()}")
        print(
            "threshold n_signals n_nonoverlap precision base_rate lift mean_return "
            "median_return mean_return_all pct_gt_fees p_value"
        )
        for row in result[label]:
            print(
                f"{row['threshold']:.2f} {row['n_signals']:>9} "
                f"{row['n_signals_nonoverlap']:>12} {_format(row['precision']):>9} "
                f"{_format(row['base_rate']):>9} {_format(row['lift']):>7} "
                f"{_format(row['mean_future_return']):>11} "
                f"{_format(row['median_future_return']):>13} "
                f"{_format(row['mean_future_return_all']):>15} "
                f"{_format(row['pct_future_return_gt_fees']):>12} "
                f"{_format(row['binom_p_value']):>8}"
            )
    print(f"\nselected_threshold: {result['selected_threshold']}")
    print("\nTEST MONTHLY")
    print("month n_signals precision base_rate mean_future_return")
    for row in result["monthly_test"]:
        print(
            f"{row['month']} {row['n_signals']:>9} {_format(row['precision']):>9} "
            f"{_format(row['base_rate']):>9} {_format(row['mean_future_return']):>18}"
        )
    print(f"\nverdict: {json.dumps(result['verdict'], sort_keys=True)}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candles", type=Path, required=True)
    parser.add_argument(
        "--out", type=Path,
        default=Path("models/saved/analysis_model_a_xrp_1h.json"),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    analyze(args.candles, args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
