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
    for symbol in selected:
        symbol_start = time.perf_counter()
        data = _load_data_readonly(path, symbol)
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
            for model in ("ewma", "har"):
                score = np.asarray(qlike(models[model], real))
                improvement = (float(np.mean(baseline)) - float(np.mean(score))) / max(float(np.mean(baseline)), 1e-12)
                low, high = block_bootstrap_difference(baseline, score, block=7, replicas=1000)
                verdict = "sirve" if improvement >= .05 and low > 0 else "no_sirve"
                reason = f"mejora_qlike={improvement:.4f}; IC95_delta=[{low:.6g},{high:.6g}]"
                verdicts.append((run_id, symbol, "volatilidad", int(horizon), model, verdict, reason))
                verdict_counts.setdefault((int(horizon), model), []).append(verdict)
            verdicts.append((run_id, symbol, "volatilidad", int(horizon), "simple", "no_sirve",
                "línea base; no supera a sí mismo"))
            verdict_counts.setdefault((int(horizon), "simple"), []).append("no_sirve")
            if discarded:
                records.append((run_id, symbol, "volatilidad", int(horizon), "simple", None,
                    discarded, "puntos_descartados_huecos", float(discarded), None, None))
        per_symbol_seconds[symbol] = time.perf_counter() - symbol_start
        print(f"MARCADOR moneda={symbol} puntos=" + ",".join(
            f"{h}h:{point_counts.get(int(h), 0)}" for h in horizons) +
            f" duración_s={per_symbol_seconds[symbol]:.3f}")
    for (horizon, model), outcomes in sorted(verdict_counts.items()):
        valid = len(outcomes)
        wins = sum(outcome == "sirve" for outcome in outcomes)
        serves = valid > 0 and wins / valid >= 2 / 3
        verdicts.append((run_id, None, "volatilidad", horizon, model,
            "sirve" if serves else "no_sirve", f"sirve en {wins} de {valid} monedas con historia completa"))
    summary = {"symbols": len(selected), "insufficient": insufficient,
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
    return {"run_id": run_id, "records": records, "summary": summary}
