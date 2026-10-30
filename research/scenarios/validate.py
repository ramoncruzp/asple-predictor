"""Validacion predictiva walk-forward de escenarios, sin conexion operativa."""
from __future__ import annotations

import json
import math
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from data.market_store import CandleStore, market_db_path
from research.scenarios.evaluate import evaluate_history, evaluate_pieces

HORIZON_DAYS = 20
DEFAULT_PIECES = (
    ("simple", 6, 1.0, 70.0), ("simple", 6, 2.0, 70.0),
    ("simple", 10, .5, 70.0), ("bot", 1, None, 30.0),
    ("smart", 6, 2.0, 70.0),
)


def _brier(probability, outcome):
    p, y = np.asarray(probability, float), np.asarray(outcome, float)
    return float(np.mean((p - y) ** 2)) if len(p) else None


def _bootstrap_by_date(rows, value_fn, *, seed=43, replicas=1000):
    dates = sorted({row["date"] for row in rows})
    if not dates:
        return [None, None]
    groups = {date: [row for row in rows if row["date"] == date] for date in dates}
    rng = np.random.default_rng(seed)
    estimates = []
    for _ in range(int(replicas)):
        sample = rng.choice(dates, size=len(dates), replace=True)
        picked = [row for date in sample for row in groups[date]]
        value = value_fn(picked)
        if value is not None and np.isfinite(value):
            estimates.append(float(value))
    if not estimates:
        return [None, None]
    return [float(np.quantile(estimates, .025)), float(np.quantile(estimates, .975))]


def baseline_window_indices(timestamps, target_ts, *, horizon_days=20, step_days=7, count=23):
    """Selecciona inicios con ventana completa y espaciados hacia atras desde t."""
    stamps = np.asarray(timestamps, dtype=np.int64)
    start_limit = int(target_ts) - int(horizon_days) * 86400
    step = int(step_days) * 86400
    selected = []
    for offset in range(int(count)):
        start_ts = start_limit - offset * step
        index = int(np.searchsorted(stamps, start_ts, side="left"))
        if index < len(stamps) and stamps[index] == start_ts:
            selected.append(index)
    return np.asarray(sorted(set(selected)), dtype=np.int64)


def summarize_validation(pairs, *, seed=43, replicas=1000):
    """Calcula cobertura, PIT, sesgo, Brier y calibracion sobre pares guardados."""
    rows = list(pairs)
    if not rows:
        return {"n": 0, "estado": "sin_pares"}
    predicted = np.asarray([row["predicted_pnl"] for row in rows], dtype=float)
    actual = np.asarray([row["actual_pnl"] for row in rows], dtype=float)
    p2 = np.asarray([row["v2_p_target"] for row in rows], dtype=float)
    p1 = np.asarray([row["v1_p_target"] for row in rows], dtype=float)
    target = np.asarray([row["actual_target"] for row in rows], dtype=float)
    p2_loss = np.asarray([row["v2_p_loss"] for row in rows], dtype=float)
    p1_loss = np.asarray([row["v1_p_loss"] for row in rows], dtype=float)
    actual_loss = np.asarray([row["actual_loss"] for row in rows], dtype=float)
    lows = np.asarray([row["predicted_p10"] for row in rows], dtype=float)
    highs = np.asarray([row["predicted_p90"] for row in rows], dtype=float)
    coverage = float(np.mean((actual >= lows) & (actual <= highs)))
    pit = np.mean(actual[:, None] >= np.asarray([row["predicted_samples"] for row in rows]), axis=1)
    histogram, _ = np.histogram(pit, bins=np.linspace(0, 1, 11))
    expected = len(rows) / 10
    chi2 = float(np.sum((histogram - expected) ** 2 / expected)) if expected else None
    bias = float(np.mean(predicted - actual))
    bias_ci = _bootstrap_by_date(rows,
        lambda sample: float(np.mean([r["predicted_pnl"] - r["actual_pnl"] for r in sample])),
        seed=seed, replicas=replicas)
    brier_v2 = _brier(p2, target)
    brier_v1 = _brier(p1, target)
    delta_ci = _bootstrap_by_date(rows,
        lambda sample: _brier([r["v2_p_target"] for r in sample],
            [r["actual_target"] for r in sample]) - _brier(
            [r["v1_p_target"] for r in sample], [r["actual_target"] for r in sample]),
        seed=seed + 1, replicas=replicas)
    calibration = []
    for lo, hi in zip(np.linspace(0, 1, 6)[:-1], np.linspace(0, 1, 6)[1:]):
        mask = (p2 >= lo) & ((p2 < hi) if hi < 1 else (p2 <= hi))
        if np.any(mask):
            calibration.append({"predicted_mean": float(p2[mask].mean()),
                "observed_mean": float(target[mask].mean()), "n": int(mask.sum())})
    interval_ok = delta_ci[0] is not None and delta_ci[0] <= 0
    passes = (.70 <= coverage <= .90 and brier_v2 <= brier_v1 and interval_ok)
    return {"n": len(rows), "coverage_p10_p90": coverage,
        "pit_deciles": histogram.astype(int).tolist(), "pit_chi2": chi2,
        "pnl_bias_predicted_minus_actual": bias, "pnl_bias_ci95": bias_ci,
        "brier_target_v2": brier_v2, "brier_target_v1": brier_v1,
        "brier_target_delta_v2_minus_v1_ci95": delta_ci,
        "brier_loss_v2": _brier(p2_loss, actual_loss),
        "brier_loss_v1": _brier(p1_loss, actual_loss),
        "target_calibration_5_bins": calibration,
        "verdict": "pasa" if passes else "no pasa"}


def _prediction_piece(symbol, spec):
    strategy, levels, width, capital = spec
    piece = {"symbol": symbol, "strategy": strategy, "n": levels, "capital": capital}
    if strategy == "bot":
        piece.update(buy_offset_pct=-1.5, sell_offset_pct=1.5)
    else:
        piece["width_sigma"] = width
    return piece


def validate_scenarios(*, db_path="market_data.db", months=12, step_days=7,
        symbols=None, commission=.1, n_scenarios=1000, n_smart=200,
        profile="RAPIDO", capital=100.0, meta=3.0,
        library_dir="data/derived/scenario_lib", seed=43, replicas=1000,
        progress=None):
    """Evalua distribuciones FHS contra la realidad y baseline v1 de 23 ventanas.

    Cada prediccion usa evaluate_pieces(before=t). La realidad es el siguiente
    horizonte real; la referencia v1 usa hasta 23 ventanas de 20 dias que
    terminan antes de t. `progress` recibe (fecha_actual, total_fechas).
    """
    if months < 1 or step_days < 1 or n_scenarios < 1 or n_smart < 1:
        raise ValueError("meses, paso y escenarios deben ser positivos")
    store = CandleStore(market_db_path(db_path))
    if symbols:
        selected = [str(item).upper() for item in symbols]
    else:
        with store._connect() as conn:
            selected = [row[0] for row in conn.execute(
                "SELECT symbol FROM symbols WHERE active=1 AND role='operar' ORDER BY symbol")]
    now_rows = {}
    dates = set()
    for symbol in selected:
        data = store.load(symbol, "5m")
        stamps = np.asarray(data.timestamp, dtype=np.int64)
        first = max(30 * 288 + 1, int(np.searchsorted(stamps, stamps[-1] - months * 30 * 86400)))
        last = len(stamps) - HORIZON_DAYS * 288
        indices = np.arange(first, last + 1, step_days * 288, dtype=int)
        now_rows[symbol] = (data, indices)
        dates.update(int(stamps[index]) for index in indices)
    all_dates = sorted(dates)
    output = {"horizon_days": HORIZON_DAYS, "months": months, "step_days": step_days,
        "n_scenarios": n_scenarios, "n_smart": n_smart, "symbols": {}, "pairs": []}
    by_geometry = {}
    for date_index, stamp in enumerate(all_dates):
        date_key = datetime.fromtimestamp(stamp, timezone.utc).date().isoformat()
        for symbol, (data, indices) in now_rows.items():
            at = int(np.searchsorted(data.timestamp, stamp))
            if at not in indices or at + HORIZON_DAYS * 288 > len(data.close):
                continue
            specs = DEFAULT_PIECES
            pieces = [_prediction_piece(symbol, spec) for spec in specs]
            prediction = evaluate_pieces(pieces, perfil=profile, capital=capital, meta=meta,
                n_scenarios=n_scenarios, n_smart=n_smart, seed=seed + stamp,
                before=stamp, db_path=db_path, library_dir=library_dir,
                mode="alternativas", fee_pct=commission)
            baseline_since = stamp - (HORIZON_DAYS + 22 * 7) * 86400
            baseline_indexes = baseline_window_indices(data.timestamp, stamp)
            required = [int(data.timestamp[index]) for index in baseline_indexes]
            required.append(stamp)
            history = evaluate_history(pieces, perfil=profile, capital=capital, meta=meta,
                db_path=db_path, months=months, step_days=1,
                since=baseline_since, fee_pct=commission, seed=seed + stamp,
                replicas=replicas, n_smart=max(n_smart, 200), library_dir=library_dir,
                required_starts={symbol: required}, until=stamp)
            symbol_history = history["symbols"].get(symbol, {})
            for piece_index, (piece_spec, predicted_piece) in enumerate(zip(specs, prediction["pieces"])):
                hist_piece = next((row for row in symbol_history.get("pieces", [])
                    if row["piece"]["strategy"] == piece_spec[0] and
                    row["piece"].get("n", 6) == piece_spec[1] and
                    row["piece"]["capital"] == piece_spec[3] and
                    row["piece"].get("width_sigma") == piece_spec[2]), None)
                if not hist_piece:
                    continue
                real_starts = hist_piece.get("variantes", {}).get("real", {}).get("starts", [])
                actual = next((row for row in real_starts if row["timestamp"] == stamp), None)
                if actual is None:
                    continue
                samples = np.asarray([row["pnl_usdt"] for row in predicted_piece["scenario_results"]], float)
                if not len(samples):
                    continue
                baseline_stamps = {int(data.timestamp[index]) for index in baseline_indexes}
                history_starts = [row for row in real_starts if row["timestamp"] in baseline_stamps]
                baseline_target = [row["reached"] for row in history_starts]
                baseline_loss = [row["loss_cut"] for row in history_starts]
                if not baseline_target:
                    baseline_target, baseline_loss = [False], [False]
                pair = {"symbol": symbol, "date": date_key, "geometry": f"{piece_spec[0]}_n{piece_spec[1]}_w{piece_spec[2]}",
                    "predicted_samples": samples.tolist(), "predicted_p10": float(np.quantile(samples, .1)),
                    "predicted_p90": float(np.quantile(samples, .9)), "predicted_pnl": float(samples.mean()),
                    "v2_p_target": float(np.mean([row["reached"] for row in predicted_piece["scenario_results"]])),
                    "v2_p_loss": float(np.mean([row["loss_cut"] for row in predicted_piece["scenario_results"]])),
                    "v1_p_target": float(np.mean(baseline_target)), "v1_p_loss": float(np.mean(baseline_loss)),
                    "actual_pnl": float(actual["pnl_usdt"]), "actual_target": float(actual["reached"]),
                    "actual_loss": float(actual["loss_cut"]), "v1_windows": len(history_starts)}
                output["pairs"].append(pair)
                by_geometry.setdefault(pair["geometry"], []).append(pair)
        if progress and ((date_index + 1) % 10 == 0 or date_index + 1 == len(all_dates)):
            progress(date_index + 1, len(all_dates))
    output["summary"] = {key: summarize_validation(rows, seed=seed, replicas=replicas)
        for key, rows in by_geometry.items()}
    output["summary"]["agregado"] = summarize_validation(
        output["pairs"], seed=seed, replicas=replicas)
    return output
