"""Cálculo y persistencia aislados en el almacén público de mercado."""
from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from data.market_store import market_db_path
from grid.sim.data import CandleData
from research.scorecard.diagnostics import (cycle_count, direction_auc_block_ci,
    efficiency_ratio, monthly_hourly_correlations, regime_features, rupture_evaluate,
    spearman_block_ci, tail_factor_ci)
from research.scorecard.metrics import (block_bootstrap_difference, calibration,
    calibration_factor_ci, causal_interval_coverage, qlike)
from research.scorecard.walkforward import walk_forward


DDL = """
CREATE TABLE IF NOT EXISTS scorecard_runs(
 id INTEGER PRIMARY KEY, started_at TEXT NOT NULL, finished_at TEXT, status TEXT NOT NULL,
 symbols TEXT NOT NULL, params_json TEXT NOT NULL, summary_json TEXT NOT NULL, code_version TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS forecast_scorecard(
 run_id INTEGER NOT NULL, symbol TEXT NOT NULL, target TEXT NOT NULL, horizon_h INTEGER NOT NULL,
 model TEXT NOT NULL, k REAL, n INTEGER NOT NULL, metric TEXT NOT NULL, value REAL,
 ci_low REAL, ci_high REAL);
CREATE TABLE IF NOT EXISTS calibration_factors(
 run_id INTEGER NOT NULL, symbol TEXT NOT NULL, kind TEXT NOT NULL, horizon_h INTEGER NOT NULL,
 model TEXT NOT NULL, factor REAL NOT NULL, n INTEGER NOT NULL, ci_low REAL, ci_high REAL,
 computed_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS scorecard_verdicts(
 run_id INTEGER NOT NULL, symbol TEXT, target TEXT NOT NULL, horizon_h INTEGER NOT NULL,
 model TEXT NOT NULL, verdict TEXT NOT NULL, reason TEXT NOT NULL);
"""


def aggregate_verdict(outcomes, *, target, signs=None):
    total = len(outcomes)
    if not total:
        return "datos_insuficientes", "sin monedas con historia completa"
    wins = sum(value == "sirve" for value in outcomes)
    if target == "regimen":
        signs = signs or []
        positive = sum(value > 0 for value in signs)
        negative = sum(value < 0 for value in signs)
        serves = max(positive, negative) / total >= 2/3
        return ("sirve" if serves else "no_sirve",
            f"sirve en {max(positive, negative)} de {total} monedas con historia completa; mismo signo")
    serves = wins / total >= 2/3
    return ("sirve" if serves else "no_sirve",
        f"sirve en {wins} de {total} monedas con historia completa")


def init_scorecard_tables(path):
    with sqlite3.connect(path) as conn:
        conn.executescript(DDL)
        columns = {row[1] for row in conn.execute("PRAGMA table_info(scorecard_verdicts)")}
        if "symbol" not in columns:
            conn.execute("ALTER TABLE scorecard_verdicts ADD COLUMN symbol TEXT")


def _add(rows, run_id, symbol, target, horizon, model, metric_name, values):
    arr = np.asarray(values, float)
    if len(arr):
        rows.append((run_id, symbol, target, horizon, model, None, len(arr), metric_name,
            float(np.mean(arr)), None, None))


def _load_data_readonly(path, symbol):
    uri = path.as_uri() + "?mode=ro"
    with sqlite3.connect(uri, uri=True) as conn:
        row = conn.execute("SELECT id FROM symbols WHERE symbol=?", (symbol,)).fetchone()
        if row is None:
            return CandleData(*(np.asarray([], dtype=np.int64 if i == 0 else float)
                for i in range(5)), 0)
        rows = conn.execute("SELECT open_ts,open,high,low,close FROM candles "
            "WHERE symbol_id=? AND interval='5m' ORDER BY open_ts", (row[0],)).fetchall()
    timestamps = np.asarray([r[0] for r in rows], dtype=np.int64)
    arrays = [np.asarray([r[i] for r in rows], float) for i in range(1, 5)]
    gaps = int(sum(max(0, (b-a)//300-1) for a, b in zip(timestamps, timestamps[1:])))
    return CandleData(timestamps, *arrays, gaps)


def _load_live_forecasts(db_path, symbol, horizon_h):
    if not db_path:
        return []
    path = Path(db_path).expanduser().resolve()
    if not path.is_file():
        return []
    try:
        with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as conn:
            tables = {row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            if "vol_forecasts" not in tables:
                return []
            rows = conn.execute("SELECT forecast_at,model_name,pred_logvol_cal,realized_logvol "
                "FROM vol_forecasts WHERE symbol=? AND horizon_h=? AND verified_at IS NOT NULL "
                "AND pred_logvol_cal IS NOT NULL AND realized_logvol IS NOT NULL",
                (symbol, int(horizon_h))).fetchall()
    except sqlite3.Error:
        return []
    output = []
    for forecast_at, model, predicted_hourly, realized_hourly in rows:
        try:
            stamp_dt = datetime.fromisoformat(str(forecast_at).replace("Z", "+00:00"))
            if stamp_dt.tzinfo is None:
                stamp_dt = stamp_dt.replace(tzinfo=timezone.utc)
            stamp = int(stamp_dt.timestamp())
            scale = np.sqrt(float(horizon_h))
            output.append((stamp, f"vivo_{model}", float(predicted_hourly) * scale,
                float(realized_hourly) * scale))
        except (TypeError, ValueError, OverflowError):
            continue
    return output


def run_scorecard(db_path, *, symbols=None, horizons=(24, 72), dry_run=False,
        output_dir=None, registry_db_path=None):
    path = market_db_path(db_path)
    if not path.is_file():
        raise FileNotFoundError(path)
    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as conn:
        available = [row[0] for row in conn.execute("SELECT symbol FROM symbols WHERE active=1 ORDER BY symbol")]
    selected = list(symbols or available)
    started = datetime.now(timezone.utc).isoformat()
    t0 = time.perf_counter()
    run_id = int(time.time())
    records, factors, verdicts, insufficient, per_symbol_seconds = [], [], [], [], {}
    verdict_counts = {}
    regime_signs = {}
    diagnostic_summary = {}
    hourly_returns_by_symbol = {}
    for symbol in selected:
        symbol_start = time.perf_counter()
        data = _load_data_readonly(path, symbol)
        close_indexes = np.arange(11, len(data.timestamp), 12)
        if len(close_indexes) > 1:
            hourly_values = np.log(data.close[close_indexes][1:] / data.close[close_indexes][:-1])
            hourly_stamps = data.timestamp[close_indexes][1:]
            hourly_returns_by_symbol[symbol] = {int(stamp): float(value)
                for stamp, value in zip(hourly_stamps, hourly_values)}
        point_counts = {}
        if len(data.timestamp) < 180 * 288:
            insufficient.append(symbol)
            per_symbol_seconds[symbol] = time.perf_counter() - symbol_start
            print(f"MARCADOR moneda={symbol} puntos=0 duración_s={per_symbol_seconds[symbol]:.3f}")
            continue
        for horizon in horizons:
            points, discarded = walk_forward(data, horizon_h=int(horizon), step_h=int(horizon))
            point_counts[int(horizon)] = len(points)
            if not points:
                if symbol not in insufficient:
                    insufficient.append(symbol)
                for model in ("simple", "ewma", "har"):
                    verdicts.append((run_id, symbol, "volatilidad", int(horizon), model,
                        "datos_insuficientes", "sin puntos walk-forward completos"))
                continue
            real = np.asarray([p.realized_sigma for p in points])
            models = {"simple": [p.forecast_simple for p in points],
                "ewma": [p.forecast_ewma for p in points], "har": [p.forecast_har for p in points]}
            timestamp_index = {p.timestamp: i for i, p in enumerate(points)}
            for live_model in sorted({row[1] for row in _load_live_forecasts(
                    registry_db_path, symbol, horizon)}):
                live_rows = [row for row in _load_live_forecasts(registry_db_path, symbol, horizon)
                    if row[1] == live_model and row[0] in timestamp_index]
                if live_rows:
                    models[live_model] = [row[2] for row in live_rows]
            for model, predictions in models.items():
                pred = np.asarray(predictions)
                if model.startswith("vivo_"):
                    matching = [row for row in _load_live_forecasts(registry_db_path, symbol, horizon)
                        if row[1] == model and row[0] in timestamp_index]
                    model_real = np.asarray([row[3] for row in matching])
                else:
                    model_real = real
                if len(model_real) != len(pred):
                    continue
                model_qlike = qlike(pred, model_real)
                _add(records, run_id, symbol, "volatilidad", int(horizon), model, "qlike", model_qlike)
                stats = calibration(pred, model_real)
                ci_low, ci_high = calibration_factor_ci(pred, model_real, block=7, replicas=1000, seed=43)
                factors.append((run_id, symbol, "sigma", int(horizon), model,
                    stats["factor_calibracion"], stats["n"], ci_low, ci_high,
                    datetime.now(timezone.utc).isoformat()))
                _add(records, run_id, symbol, "volatilidad", int(horizon), model, "sesgo_log_mediano",
                    [stats["log_error_median"]])
                _add(records, run_id, symbol, "volatilidad", int(horizon), model, "sesgo_log_medio",
                    [stats["log_error_mean"]])
                coverage, coverage_n = causal_interval_coverage(pred, model_real, minimum_history=30)
                if coverage_n:
                    records.append((run_id, symbol, "volatilidad", int(horizon), model, None,
                        coverage_n, "cobertura_80", coverage, None, None))
            baseline = np.asarray(qlike(models["simple"], real))
            model_verdicts = {"simple": "no_sirve"}
            for model in ("ewma", "har"):
                score = np.asarray(qlike(models[model], real))
                improvement = (float(np.mean(baseline)) - float(np.mean(score))) / max(float(np.mean(baseline)), 1e-12)
                low, high = block_bootstrap_difference(baseline, score, block=7, replicas=1000)
                verdict = "sirve" if improvement >= .05 and low > 0 else "no_sirve"
                reason = f"mejora_qlike={improvement:.4f}; IC95_delta=[{low:.6g},{high:.6g}]"
                verdicts.append((run_id, symbol, "volatilidad", int(horizon), model, verdict, reason))
                model_verdicts[model] = verdict
                verdict_counts.setdefault((int(horizon), model), []).append(verdict)
            verdicts.append((run_id, symbol, "volatilidad", int(horizon), "simple", "no_sirve",
                "línea base; no supera a sí mismo"))
            verdict_counts.setdefault((int(horizon), "simple"), []).append("no_sirve")
            valid_models = [name for name in ("ewma", "har") if model_verdicts.get(name) == "sirve"]
            chosen_model = min(valid_models, key=lambda name: float(np.mean(qlike(models[name], real))) \
                ) if valid_models else "simple"
            forecast = np.asarray(models[chosen_model], float)
            point_prices, highs, lows = [], [], []
            for point in points:
                start_index = int(np.searchsorted(data.timestamp, point.timestamp, side="left"))
                point_prices.append(float(data.close[start_index - 1]))
                indices = np.asarray(point.target_indices, dtype=int)
                highs.append(float(np.max(data.high[indices])))
                lows.append(float(np.min(data.low[indices])))
            rupture_rows, tail_factor = rupture_evaluate(point_prices, forecast, highs, lows, int(horizon))
            factor_low, factor_high = tail_factor_ci(point_prices, forecast,
                rupture_rows[0]["actual"], int(horizon), replicas=1000, seed=43)
            factors.append((run_id, symbol, "colas", int(horizon), chosen_model, tail_factor,
                len(points), factor_low, factor_high, datetime.now(timezone.utc).isoformat()))
            diagnostic_summary[f"{symbol}:{horizon}"] = {"best_sigma_model": chosen_model,
                "tail_factor": tail_factor, "rupture": []}
            for row in rupture_rows:
                diff_ci = block_bootstrap_difference(row["base_error"], row["brier_error"],
                    block=7, replicas=1000, seed=43)
                skill_ci = tuple(value / row["brier_base_causal"] for value in diff_ci) \
                    if row["brier_base_causal"] > 0 else (None, None)
                verdict = "sirve" if row["brier_skill"] >= .05 and .8 <= row["calibration_slope"] <= 1.2 else "no_sirve"
                key = (int(horizon), f"ruptura_k_{row['k']:g}")
                verdicts.append((run_id, symbol, "ruptura", int(horizon), f"k_{row['k']:g}", verdict,
                    f"Brier skill={row['brier_skill']:.4f}; pendiente={row['calibration_slope']:.4f}"))
                verdict_counts.setdefault(key, []).append(verdict)
                for metric, value in (("brier", row["brier"]),
                    ("brier_base_causal", row["brier_base_causal"]),
                    ("brier_skill", row["brier_skill"]),
                    ("pendiente_calibracion", row["calibration_slope"])):
                    records.append((run_id, symbol, f"ruptura_k_{row['k']:g}", int(horizon),
                        chosen_model, row["k"], row["n"], metric, float(value),
                        skill_ci[0] if metric == "brier_skill" else None,
                        skill_ci[1] if metric == "brier_skill" else None))
                diagnostic_summary[f"{symbol}:{horizon}"]["rupture"].append({
                    **{key: value for key, value in row.items() if key not in {
                        "actual", "probability", "brier_error", "base_error"}},
                    "brier_skill_ci_low": skill_ci[0], "brier_skill_ci_high": skill_ci[1]})
            regime_er24, regime_er72, variance_ratios, cycles, future_ers = [], [], [], [], []
            for point, price, sigma, high, low in zip(points, point_prices, forecast, highs, lows):
                history_end = int(np.searchsorted(data.timestamp, point.timestamp, side="left"))
                past = data.close[:history_end]
                feature24 = regime_features(past, length_h=24)
                feature72 = regime_features(past, length_h=72)
                regime_er24.append(feature24["er"])
                regime_er72.append(feature72["er"])
                variance_ratios.append(feature72["variance_ratio"])
                indices = np.asarray(point.target_indices, dtype=int)
                target_close = np.concatenate(([data.close[history_end-1]], data.close[indices]))
                cycles.append(cycle_count(target_close[1:], data.high[indices], data.low[indices], price, sigma)
                    / float(horizon))
                future_ers.append(efficiency_ratio(target_close, len(target_close)-1))
            for label, predictor in (("er24", regime_er24), ("er72", regime_er72),
                    ("variance_ratio", variance_ratios)):
                rho, ci_low, ci_high = spearman_block_ci(predictor, cycles, block=7, replicas=1000, seed=43)
                verdict = "sirve" if abs(rho) >= .15 and (ci_low > 0 or ci_high < 0) else "no_sirve"
                verdicts.append((run_id, symbol, "regimen", int(horizon), label, verdict,
                    f"rho_ciclos={rho:.4f}; IC95=[{ci_low:.4f},{ci_high:.4f}]"))
                verdict_counts.setdefault((int(horizon), f"regimen_{label}"), []).append(verdict)
                regime_signs.setdefault((int(horizon), f"regimen_{label}"), []).append(
                    float(np.sign(rho)) if verdict == "sirve" else 0.0)
                records.append((run_id, symbol, "regimen", int(horizon), chosen_model, None,
                    len(points), f"rho_ciclos_{label}", rho, ci_low, ci_high))
            rho, ci_low, ci_high = spearman_block_ci(regime_er24, future_ers,
                block=7, replicas=1000, seed=43)
            records.append((run_id, symbol, "regimen", int(horizon), chosen_model, None,
                len(points), "rho_er24_vs_er_futuro", rho, ci_low, ci_high))
            if int(horizon) == 24:
                past_returns, future_returns = [], []
                for point in points:
                    idx = int(np.searchsorted(data.timestamp, point.timestamp, side="left"))
                    target = np.asarray(point.target_indices[:288], dtype=int)
                    past_returns.append(float(np.log(data.close[idx-1] / data.close[idx-289])))
                    future_returns.append(float(np.log(data.close[target[-1]] / data.close[idx-1])))
                auc, auc_low, auc_high = direction_auc_block_ci(past_returns, future_returns,
                    block=7, replicas=1000, seed=43)
                direction_verdict = "sirve" if auc >= .55 and auc_low > .5 else "no_sirve"
                verdicts.append((run_id, symbol, "direccion", 24, "momento_24h", direction_verdict,
                    f"AUC={auc:.4f}; IC95=[{auc_low:.4f},{auc_high:.4f}]"))
                verdict_counts.setdefault((24, "direccion_momento_24h"), []).append(direction_verdict)
                records.append((run_id, symbol, "direccion", 24, "momentum_24h", None,
                    len(past_returns), "auc", auc, auc_low, auc_high))
            if discarded:
                records.append((run_id, symbol, "volatilidad", int(horizon), "simple", None,
                    discarded, "puntos_descartados_huecos", float(discarded), None, None))
        per_symbol_seconds[symbol] = time.perf_counter() - symbol_start
        print(f"MARCADOR moneda={symbol} puntos=" + ",".join(
            f"{h}h:{point_counts.get(int(h), 0)}" for h in horizons) +
            f" duración_s={per_symbol_seconds[symbol]:.3f}")
    for (horizon, model), outcomes in sorted(verdict_counts.items()):
        target = ("ruptura" if model.startswith("ruptura_") else
            "regimen" if model.startswith("regimen_") else
            "direccion" if model.startswith("direccion_") else "volatilidad")
        verdict, reason = aggregate_verdict(outcomes, target=target,
            signs=regime_signs.get((horizon, model)))
        verdicts.append((run_id, None, target, horizon, model, verdict, reason))
    correlation_summary = {}
    if len(hourly_returns_by_symbol) >= 2:
        correlation_symbols, correlation_matrices = monthly_hourly_correlations(hourly_returns_by_symbol)
        current_month = datetime.now(timezone.utc).strftime("%Y-%m")
        complete_months = [month for month in correlation_matrices if month < current_month]
        for month, matrix in sorted(correlation_matrices.items()):
            off_diagonal = matrix[np.triu_indices(len(correlation_symbols), 1)]
            off_diagonal = off_diagonal[np.isfinite(off_diagonal)]
            if len(off_diagonal):
                records.append((run_id, "TODAS", "correlacion", 0, month, None,
                    len(off_diagonal), "media_mes", float(np.mean(off_diagonal)), None, None))
                records.append((run_id, "TODAS", "correlacion", 0, month, None,
                    len(off_diagonal), "mediana_mes", float(np.median(off_diagonal)), None, None))
            correlation_summary[month] = {"symbols": correlation_symbols,
                "matrix": [[float(value) if np.isfinite(value) else None for value in row]
                    for row in matrix],
                "media": float(np.nanmean(off_diagonal)) if len(off_diagonal) else None,
                "mediana": float(np.nanmedian(off_diagonal)) if len(off_diagonal) else None}
        if complete_months:
            last = complete_months[-1]
            diagnostic_summary["correlation_last_complete_month"] = {
                "month": last, **correlation_summary[last]}
    summary = {"symbols": len(selected), "insufficient": insufficient,
        "diagnostics": diagnostic_summary,
        "correlations_by_month": correlation_summary,
        "seconds": time.perf_counter() - t0, "per_symbol_seconds": per_symbol_seconds}
    if not dry_run:
        init_scorecard_tables(path)
        with sqlite3.connect(path) as conn:
            conn.execute("INSERT INTO scorecard_runs VALUES(?,?,?,?,?,?,?,?)",
                (run_id, started, datetime.now(timezone.utc).isoformat(), "completed",
                    json.dumps(selected), json.dumps({"horizons": list(horizons)}),
                    json.dumps(summary), "43c-v1"))
            conn.executemany("INSERT INTO forecast_scorecard VALUES(?,?,?,?,?,?,?,?,?,?,?)", records)
            conn.executemany("INSERT INTO calibration_factors VALUES(?,?,?,?,?,?,?,?,?,?)", factors)
            conn.executemany("INSERT INTO scorecard_verdicts "
                "(run_id,symbol,target,horizon_h,model,verdict,reason) VALUES(?,?,?,?,?,?,?)", verdicts)
    return {"run_id": run_id, "records": records, "factors": factors,
        "verdicts": verdicts, "summary": summary}
