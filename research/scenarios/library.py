"""Biblioteca FHS compacta por moneda, construida desde velas públicas."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from data.market_store import CandleStore

METHOD_VERSION = "scenario_fhs_v1"
PER_DAY = 288
LAMBDA_HOURLY = 0.97


def standardized_library(data):
    """Devuelve días completos de z_r/z_u/z_d y fechas; excluye los 30 días iniciales."""
    stamps = np.asarray(data.timestamp, dtype=np.int64)
    opn, high, low, close = (np.asarray(getattr(data, name), dtype=float)
        for name in ("open", "high", "low", "close"))
    if len(stamps) < 31 * PER_DAY or not all(len(x) == len(stamps) for x in (opn, high, low, close)):
        return np.empty((0, PER_DAY, 3), np.float32), np.asarray([], np.int64), 0.0
    day_ids = stamps // 86400
    unique_days, counts = np.unique(day_ids, return_counts=True)
    valid_days, excluded = [], 0
    lam = LAMBDA_HOURLY ** (1 / 12)
    returns = np.diff(np.log(close))
    var = float(np.mean(returns[:30 * PER_DAY] ** 2)) if len(returns) >= 30 * PER_DAY else 0.0
    rows = []
    previous_day = None
    for day, count in zip(unique_days, counts):
        indices = np.flatnonzero(day_ids == day)
        if len(indices) != PER_DAY or count != PER_DAY or np.any(np.diff(stamps[indices]) != 300):
            excluded += 1
            continue
        first = int(indices[0])
        if first == 0 or stamps[first] - stamps[first - 1] != 300:
            excluded += 1
            previous_day = int(day)
            continue
        sigma24 = max(float(np.sqrt(max(var, 1e-16) * 288)), 1e-8)
        prev_close = np.r_[close[first - 1], close[indices[:-1]]]
        r = np.log(close[indices] / prev_close)
        u = np.maximum(np.log(high[indices] / prev_close), np.maximum(0, r))
        d = np.minimum(np.log(low[indices] / prev_close), np.minimum(0, r))
        scale = sigma24 / np.sqrt(288)
        rows.append(np.column_stack((r / scale, u / scale, d / scale)))
        valid_days.append(int(day * 86400))
        # Actualizaciones EWMA se incorporan después de estimar el día entero.
        for value in r:
            var = lam * var + (1 - lam) * float(value * value)
        previous_day = int(day)
    if not rows:
        return np.empty((0, PER_DAY, 3), np.float32), np.asarray([], np.int64), 0.0
    result = np.asarray(rows, dtype=np.float32)
    mean_return = float(np.mean(result[:, :, 0], dtype=np.float64))
    result[:, :, 0] -= np.float32(mean_return)
    result[:, :, 1] -= np.float32(mean_return)
    result[:, :, 2] -= np.float32(mean_return)
    return result, np.asarray(valid_days, dtype=np.int64), mean_return


def build_library(db_path, *, symbols=None, output_dir="data/derived/scenario_lib"):
    store = CandleStore(db_path)
    with store._connect() as conn:
        query = "SELECT symbol FROM symbols WHERE active=1 AND role IN ('operar','datos') ORDER BY symbol"
        available = [row[0] for row in conn.execute(query)]
    selected = list(symbols or available)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest = {"method_version": METHOD_VERSION, "built_at": datetime.now(timezone.utc).isoformat(),
        "symbols": {}}
    for symbol in selected:
        data = store.load(symbol, "5m")
        values, dates, mean_return = standardized_library(data)
        if len(dates) < 365:
            continue
        target = output_dir / f"{symbol.upper()}.npz"
        np.savez_compressed(target, values=values, dates=dates, mean_z_return=np.float32(mean_return))
        manifest["symbols"][symbol.upper()] = {"file": target.name,
            "first_date": datetime.fromtimestamp(int(dates[0]), timezone.utc).date().isoformat(),
            "last_date": datetime.fromtimestamp(int(dates[-1]), timezone.utc).date().isoformat(),
            "valid_days": int(len(dates)), "excluded_gap_days": int(max(0, len(np.unique(data.timestamp // 86400)) - len(dates))),
            "mean_z_return": mean_return}
    (output_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8", newline="\n")
    return manifest


def load_library(symbol, *, before=None, directory="data/derived/scenario_lib"):
    path = Path(directory) / f"{str(symbol).upper()}.npz"
    with np.load(path, allow_pickle=False) as archive:
        values, dates = archive["values"], archive["dates"]
    if before is not None:
        if isinstance(before, str):
            stamp = int(datetime.fromisoformat(before.replace("Z", "+00:00")).replace(
                tzinfo=timezone.utc).timestamp()) if "T" not in before else int(datetime.fromisoformat(
                    before.replace("Z", "+00:00")).timestamp())
        else:
            stamp = int(before)
        mask = dates < stamp
        values, dates = values[mask], dates[mask]
    return values, dates
