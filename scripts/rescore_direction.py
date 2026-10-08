"""Re-score historical direction outcomes against the candle containing verify_at.

Dry-run is the default. Use --apply only against an explicitly confirmed copy
of the application database after reviewing the JSON report.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

import pandas as pd

from database.learning_engine import price_at

from config.models_config import TARGET_UP_THRESHOLD


MODEL_NAMES = ("model_a", "model_b", "model_c")


def _utc(value):
    parsed = pd.Timestamp(value)
    if parsed.tzinfo is None:
        return parsed.tz_localize("UTC")
    return parsed.tz_convert("UTC")


def _load_candles(path: Path):
    frame = pd.read_csv(path)
    if "timestamp" not in frame or "close" not in frame:
        raise ValueError("El CSV debe incluir las columnas timestamp y close")
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True, errors="coerce")
    if "close_time" in frame:
        frame["close_time"] = pd.to_datetime(frame["close_time"], utc=True, errors="coerce")
    else:
        frame["close_time"] = frame["timestamp"] + pd.Timedelta(hours=1)
    frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
    if "open" in frame:
        frame["open"] = pd.to_numeric(frame["open"], errors="coerce")
    return frame.dropna(subset=["timestamp", "close_time", "close"]).sort_values("timestamp")


def _close_at(frame, verify_at):
    return price_at(verify_at, frame)


def _connect_readonly(path: Path):
    uri = path.resolve().as_uri() + "?mode=ro"
    return sqlite3.connect(uri, uri=True)


def _open_db(path: Path, writable: bool):
    return sqlite3.connect(path.resolve()) if writable else _connect_readonly(path)


def _rows(connection):
    connection.row_factory = sqlite3.Row
    return connection.execute(
        "SELECT p.prediction_id, p.model_name, p.symbol, p.signal, p.verify_at, "
        "p.verification_status, p.verify_delay_h AS prediction_delay, "
        "p.price_at_prediction, o.verified_at, o.price_at_verification, "
        "o.price_change_pct, o.actual_direction, o.was_correct, "
        "o.verify_delay_h AS outcome_delay "
        "FROM predictions p JOIN outcomes o USING(prediction_id) "
        "WHERE p.model_name IN (?, ?, ?) ORDER BY p.model_name, p.predicted_at",
        MODEL_NAMES,
    ).fetchall()


def _label(signal, change_pct):
    is_up = change_pct / 100.0 > TARGET_UP_THRESHOLD
    direction = "UP" if is_up else "NOT_UP"
    correct = 1 if is_up else 0
    if signal == "ALCISTA":
        was_correct = correct
    elif signal == "BAJISTA":
        was_correct = 1 - correct
    else:
        was_correct = None
    return direction, was_correct


def _metrics(rows):
    n = len(rows)
    n_accuracy = sum(row["was_correct"] is not None for row in rows)
    return {
        "n": n,
        "n_accuracy": n_accuracy,
        "base_rate_up": (sum(row["actual_direction"] == "UP" for row in rows) / n) if n else None,
        "accuracy": (sum(int(row["was_correct"]) for row in rows if row["was_correct"] is not None) / n_accuracy) if n_accuracy else None,
    }


def _process_owns_db(db_path: Path):
    try:
        import psutil
    except ImportError:
        return None
    wanted = str(db_path.resolve()).casefold()
    unknown = False
    for process in psutil.process_iter(["pid"]):
        try:
            for opened in process.open_files():
                if str(Path(opened.path).resolve()).casefold() == wanted:
                    return process.pid
        except (psutil.AccessDenied, psutil.NoSuchProcess, OSError):
            unknown = True
    return None if not unknown else None


def _apply_safety(db_path: Path, confirmed_copy: bool):
    owner = _process_owns_db(db_path)
    if owner is not None:
        raise RuntimeError(f"La base parece abierta por el proceso {owner}; aplica solo sobre una copia cerrada")
    try:
        import psutil  # noqa: F401
        can_check = True
    except ImportError:
        can_check = False
    if not can_check and not confirmed_copy:
        raise RuntimeError("No se pudo comprobar si la base está abierta; añade --i-know-this-is-a-copy")
    if not confirmed_copy:
        raise RuntimeError("--apply exige --i-know-this-is-a-copy para confirmar que --db es una copia")


def rescore(db_path: Path, candles_path: Path, apply_changes=False, confirmed_copy=False):
    candles = _load_candles(candles_path)
    connection = _open_db(db_path, writable=apply_changes)
    try:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(predictions)")}
        outcome_columns = {row[1] for row in connection.execute("PRAGMA table_info(outcomes)")}
        if apply_changes and (not {"verification_status", "verify_delay_h"} <= columns
                              or "verify_delay_h" not in outcome_columns):
            raise RuntimeError("Faltan columnas de verificación; inicia primero la aplicación en la copia")
        source_rows = _rows(connection)
        reports = {}
        updates = []
        missing = {name: 0 for name in MODEL_NAMES}
        late_missing_dates = []
        before_by_model = {name: [] for name in MODEL_NAMES}
        after_by_model = {name: [] for name in MODEL_NAMES}
        for row in source_rows:
            item = dict(row)
            model = item["model_name"]
            before_by_model[model].append({
                "actual_direction": item["actual_direction"], "was_correct": item["was_correct"],
            })
            close = _close_at(candles, item["verify_at"])
            if close is None:
                missing[model] += 1
                delays = [float(value) for value in (item["prediction_delay"], item["outcome_delay"])
                          if value is not None]
                if delays and max(delays) > 1.0:
                    late_missing_dates.append(_utc(item["verify_at"]))
                after_by_model[model].append({
                    "actual_direction": item["actual_direction"], "was_correct": item["was_correct"],
                })
                continue
            change_pct = (close - float(item["price_at_prediction"])) / float(item["price_at_prediction"]) * 100.0
            direction, correct = _label(item["signal"], change_pct)
            verified_at = _utc(item["verified_at"])
            verify_at = _utc(item["verify_at"])
            delay_h = max(0.0, (verified_at - verify_at).total_seconds() / 3600.0)
            rescored = {"actual_direction": direction, "was_correct": correct}
            after_by_model[model].append(rescored)
            updates.append((item["prediction_id"], close, change_pct, direction, correct, delay_h))

        if apply_changes:
            connection.execute("BEGIN IMMEDIATE")
            for prediction_id, close, change_pct, direction, correct, delay_h in updates:
                previous = connection.execute(
                    "SELECT p.verification_status, p.verify_delay_h, o.price_at_verification, "
                    "o.price_change_pct, o.actual_direction, o.was_correct, o.verify_delay_h "
                    "FROM predictions p JOIN outcomes o USING(prediction_id) WHERE p.prediction_id=?",
                    (prediction_id,),
                ).fetchone()
                wanted = ("verified", delay_h, close, change_pct, direction, correct, delay_h)
                if tuple(previous) == wanted:
                    continue
                connection.execute(
                    "UPDATE outcomes SET price_at_verification=?, price_change_pct=?, actual_direction=?, "
                    "was_correct=?, verify_delay_h=? WHERE prediction_id=?",
                    (close, change_pct, direction, correct, delay_h, prediction_id),
                )
                connection.execute(
                    "UPDATE predictions SET verification_status='verified', verify_delay_h=? WHERE prediction_id=?",
                    (delay_h, prediction_id),
                )
            connection.commit()

        for model in MODEL_NAMES:
            reports[model] = {
                "before": _metrics(before_by_model[model]),
                "after": _metrics(after_by_model[model]),
                "missing_candles": missing[model],
                "eligible_rows": len(before_by_model[model]),
            }
        latest_candle = None if candles.empty else candles.iloc[-1]["timestamp"].isoformat()
        first_missing = min(late_missing_dates).isoformat() if late_missing_dates else None
        last_missing = max(late_missing_dates).isoformat() if late_missing_dates else None
        return {"mode": "apply" if apply_changes else "dry-run",
            "candles_last_timestamp": latest_candle,
            "late_rows_missing_candles": {
                "count": len(late_missing_dates),
                "first_verify_at": first_missing,
                "last_verify_at": last_missing,
                "latest_candle_timestamp": latest_candle,
                "message": "estas filas no se corrigen con este CSV; actualiza el caché de velas y vuelve a correr el dry-run",
            }, "models": [
            {"model_name": name, **reports[name]} for name in MODEL_NAMES
        ], "updated_rows": len(updates) if apply_changes else 0}
    except Exception:
        if apply_changes:
            connection.rollback()
        raise
    finally:
        connection.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, type=Path, help="Ruta de la base SQLite")
    parser.add_argument("--candles", type=Path, default=Path("data/cache/xrp_1h.csv"), help="CSV local de velas 1h")
    parser.add_argument("--apply", action="store_true", help="Aplicar resultados a la base indicada")
    parser.add_argument("--i-know-this-is-a-copy", action="store_true", help="Confirma que --db es una copia cerrada")
    args = parser.parse_args(argv)
    try:
        if args.apply:
            _apply_safety(args.db, args.i_know_this_is_a_copy)
        report = rescore(args.db, args.candles, args.apply, args.i_know_this_is_a_copy)
    except Exception as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
