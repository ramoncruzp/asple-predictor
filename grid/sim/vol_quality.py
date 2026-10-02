"""Forecast-quality study for the grid's 24h sigma: Nexo-HAR vs EWMA vs persistence.

Compares each model's causal per-hour log-vol forecast against the realized
volatility over the following 24 hours (``target_logvol`` from
``data.volatility.build_volatility_frame``), per rolling 30-day window
(``grid.sim.sweep.build_windows``, filtered to windows with enough causal training
history) and pooled. The metric that matters most for grid sizing is the fraction
of forecasts that *underestimate* realized sigma by more than 20% -- an
undersized grid range from an underestimated sigma is the dangerous failure mode,
not an oversized one.

EWMA here uses a single continuously running estimate over the *entire* history
(no per-window restart), unlike ``grid.sim.vol_series``'s ``"ewma"`` variant which
deliberately restarts every window to match what ``run_simulation`` does today.
That restart is a simulator-harness artifact, not a property of EWMA itself, and
restarting it here would unfairly penalize it relative to Nexo-HAR/persistence,
which both see arbitrarily long trailing history. See ``REPORTE_FASE15E.md``.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from grid.sim.data import ewma_sigma_24h
from grid.sim.sweep import block_bootstrap_interval
from grid.sim.vol_series import (
    MIN_TRAIN_DAYS,
    causal_train_predict,
    eligible_windows,
    window_train_cutoff,
)
from models.volatility.evaluation import moving_block_bootstrap_difference
from scripts.vol_research import score_forecast

UNDERESTIMATE_LOG_THRESHOLD = float(np.log(0.8))  # sigma_pred < 0.8*sigma_actual
MODEL_NAMES = ("ewma", "persistence", "nexo_har")


def ewma_hourly_logvol(
    frame: pd.DataFrame, five_min_timestamps: np.ndarray, five_min_closes: np.ndarray,
    *, halflife_h: float = 72.0,
) -> np.ndarray:
    """Continuous (non-restarting) EWMA sigma_24h, mapped onto each hourly row's
    close_time and converted to the same per-hour log-vol units as ``target_logvol``."""
    sigma_24h_5m = ewma_sigma_24h(five_min_closes, halflife_h)
    hourly_close_ts = (pd.to_datetime(frame["close_time"], utc=True).astype("int64") // 10**9).to_numpy()
    five_min_ts = np.asarray(five_min_timestamps, dtype=np.int64)
    idx = np.searchsorted(five_min_ts, hourly_close_ts, side="right") - 1
    idx = np.clip(idx, 0, len(sigma_24h_5m) - 1)
    sigma_per_hour = sigma_24h_5m[idx] / np.sqrt(24.0)
    return np.log(np.clip(sigma_per_hour, 1e-12, None))


def _qlike_loss(target: np.ndarray, pred: np.ndarray) -> np.ndarray:
    ratio = np.exp(2.0 * (target - pred))
    return ratio - np.log(ratio) - 1.0


def evaluate_quality(
    frame: pd.DataFrame, windows: list[dict], *, five_min_timestamps: np.ndarray,
    five_min_closes: np.ndarray, min_train_days: int = MIN_TRAIN_DAYS,
) -> dict:
    elig = eligible_windows(windows, frame, min_train_days=min_train_days)
    ewma_logvol_full = ewma_hourly_logvol(frame, five_min_timestamps, five_min_closes)
    times = pd.to_datetime(frame["close_time"], utc=True)
    target_full = frame["target_logvol"].to_numpy(dtype=float)
    complete_full = frame["complete_hour"].to_numpy(dtype=bool)

    per_window = []
    pooled_rows: dict[str, list[np.ndarray]] = {name: [] for name in MODEL_NAMES}
    pooled_targets: dict[str, list[np.ndarray]] = {name: [] for name in MODEL_NAMES}
    window_data = {}  # window_id -> (mask, {name: pred_array_or_None})

    for window in elig:
        start_dt = pd.Timestamp(int(window["start"]), unit="s", tz="UTC")
        end_dt = pd.Timestamp(int(window["end_exclusive"]), unit="s", tz="UTC")
        base_mask = ((times >= start_dt) & (times < end_dt) & complete_full & np.isfinite(target_full)).to_numpy()
        if not base_mask.any():
            continue
        predictions: dict[str, np.ndarray | None] = {"ewma": ewma_logvol_full}
        for name in ("persistence", "nexo_har"):
            result = causal_train_predict(frame, name, window_train_cutoff(window["start"]))
            predictions[name] = result[0].to_numpy(dtype=float) if result is not None else None
        window_data[window["window_id"]] = (base_mask, predictions)

        row = {"window_id": window["window_id"], "start": window["start"], "n_hours": int(base_mask.sum())}
        for name in MODEL_NAMES:
            pred = predictions[name]
            if pred is None:
                row[name] = None
                continue
            mask = base_mask & np.isfinite(pred)
            if not mask.any():
                row[name] = None
                continue
            pred_window, target_window = pred[mask], target_full[mask]
            score = score_forecast(target_window, pred_window)
            under = float(np.mean((pred_window - target_window) < UNDERESTIMATE_LOG_THRESHOLD))
            row[name] = {**score, "underestimate_gt20pct_fraction": under}
            pooled_rows[name].append(pred_window)
            pooled_targets[name].append(target_window)
        per_window.append(row)

    pooled_summary = {}
    for name in MODEL_NAMES:
        if not pooled_rows[name]:
            pooled_summary[name] = None
            continue
        pred_all = np.concatenate(pooled_rows[name])
        target_all = np.concatenate(pooled_targets[name])
        score = score_forecast(target_all, pred_all)
        under = (pred_all - target_all) < UNDERESTIMATE_LOG_THRESHOLD
        pooled_summary[name] = {
            **score,
            "underestimate_gt20pct_fraction": float(under.mean()),
            "underestimate_gt20pct_ci95_block": block_bootstrap_interval(under.astype(float)),
            "n_pooled": int(len(pred_all)),
        }

    comparisons = {}
    for baseline in ("ewma", "persistence"):
        # Paired, row-aligned QLIKE loss difference (baseline - nexo_har) per window,
        # pooled with a moving-block bootstrap (positive mean_difference => Nexo-HAR wins).
        baseline_losses, nexo_losses = [], []
        for base_mask, predictions in window_data.values():
            nexo_pred, baseline_pred = predictions["nexo_har"], predictions[baseline]
            if nexo_pred is None or baseline_pred is None:
                continue
            mask = base_mask & np.isfinite(nexo_pred) & np.isfinite(baseline_pred)
            if not mask.any():
                continue
            baseline_losses.append(_qlike_loss(target_full[mask], baseline_pred[mask]))
            nexo_losses.append(_qlike_loss(target_full[mask], nexo_pred[mask]))
        if baseline_losses:
            comparisons[f"nexo_har_vs_{baseline}_qlike"] = moving_block_bootstrap_difference(
                np.concatenate(baseline_losses), np.concatenate(nexo_losses),
            )
        else:
            comparisons[f"nexo_har_vs_{baseline}_qlike"] = None

    return {
        "per_window": per_window, "pooled": pooled_summary, "comparisons": comparisons,
        "eligible_window_count": len(elig), "total_window_count": len(windows),
        "min_train_days": min_train_days,
        "underestimate_threshold": "sigma_pred < 0.8 * sigma_realized (more than 20% underestimated)",
    }
