"""Estudios Meta de solo lectura. Los resultados se guardan como JSON y CSV."""
from __future__ import annotations

import argparse
import csv
import json
import math
import sqlite3
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _connect_read_only(path: Path):
    absolute = str(path.resolve()).replace("\\", "/")
    connection = sqlite3.connect(f"file:{quote(absolute, safe='/:')}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    return connection


def _write(rows, out: Path, stem: str, metadata=None):
    out.mkdir(parents=True, exist_ok=True)
    payload = {"study": stem, "metadata": metadata or {}, "rows": rows}
    (out / f"{stem}.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8")
    fields = list(dict.fromkeys(key for row in rows for key in row))
    with (out / f"{stem}.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields or ["status"])
        writer.writeheader()
        writer.writerows(rows)
    return payload


def _auc(labels, scores):
    positive = [score for label, score in zip(labels, scores) if label == 1]
    negative = [score for label, score in zip(labels, scores) if label == 0]
    if not positive or not negative:
        return None
    wins = sum(1 if p > n else .5 if p == n else 0 for p in positive for n in negative)
    return wins / (len(positive) * len(negative))


def calibrate_direction(db_path: Path, days: float = 30):
    cutoff = (datetime.now(timezone.utc) - timedelta(days=max(0, days))).replace(tzinfo=None).isoformat(sep=" ")
    with _connect_read_only(db_path) as conn:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {"predictions", "outcomes"} <= tables:
            raise RuntimeError("La base requiere predictions y outcomes")
        rows = conn.execute("""SELECT p.symbol, p.probability_up, o.actual_direction, o.price_change_pct
            FROM predictions p JOIN outcomes o ON o.prediction_id=p.prediction_id
            WHERE p.model_name='model_a_down' AND p.interval='1h'
              AND datetime(p.predicted_at)>=datetime(?) AND o.was_correct IS NOT NULL
            ORDER BY p.symbol,p.predicted_at""", (cutoff,)).fetchall()
    grouped = defaultdict(list)
    for row in rows:
        p_down = row["probability_up"]
        if p_down is None:
            continue
        direction = str(row["actual_direction"] or "").upper()
        if direction in {"DOWN", "BAJA"}:
            label = 1
        elif direction in {"UP", "SUBE"}:
            label = 0
        elif row["price_change_pct"] is not None and float(row["price_change_pct"]) != 0:
            label = int(float(row["price_change_pct"]) < 0)
        else:
            continue
        grouped[str(row["symbol"])].append((float(p_down), label))
    result = []
    for symbol, sample in sorted(grouped.items()):
        ps = np.asarray([item[0] for item in sample], dtype=float)
        ys = np.asarray([item[1] for item in sample], dtype=int)
        clipped = np.clip(ps, 1e-12, 1 - 1e-12)
        base_accuracy = float(np.mean((ps >= .5) == ys))
        threshold_accuracy = float(np.mean((ps >= .60) == ys))
        thresholds = sorted({.5, .6, *(float(x) for x in ps)})
        best_threshold = max(thresholds, key=lambda t: (float(np.mean((ps >= t) == ys)), -abs(t - .6)))
        deciles = []
        for index in range(10):
            mask = (ps >= index / 10) & (ps < (index + 1) / 10 if index < 9 else ps <= 1)
            if np.any(mask):
                deciles.append({"decile": index + 1, "n": int(mask.sum()),
                    "mean_p_down": float(ps[mask].mean()), "observed_down_rate": float(ys[mask].mean())})
        result.append({"symbol": symbol, "n": len(sample), "down_rate": float(ys.mean()),
            "brier": float(np.mean((ps - ys) ** 2)),
            "log_loss": float(-np.mean(ys * np.log(clipped) + (1 - ys) * np.log(1 - clipped))),
            "auc": _auc(ys.tolist(), ps.tolist()), "accuracy_threshold_0_60": threshold_accuracy,
            "accuracy_threshold_0_50": base_accuracy,
            "best_threshold": float(best_threshold),
            "best_threshold_accuracy": float(np.mean((ps >= best_threshold) == ys)),
            "deciles": deciles,
            "note": "P(baja) en Spot sirve para no comprar o para gestionar inventario; no implica vender en corto."})
    return result


def persistence_study(values, symbols, days, profile):
    return [{"symbol": symbol.strip().upper().replace("/", ""), "days": int(days),
        "profile": profile, "persistence_reviews": int(value), "status": "not_modeled",
        "reason": "No se puede comparar persistencia sin replay activo que conserve órdenes e inventario."}
        for value in str(values).split(",") if value.strip()
        for symbol in str(symbols).split(",") if symbol.strip()]


def _read_candles_csv(path: Path):
    from grid.sim.data import CandleData
    timestamps, o, h, l, c = [], [], [], [], []
    with path.open("r", newline="", encoding="utf-8-sig") as stream:
        reader = csv.DictReader(stream)
        for row in reader:
            stamp = datetime.fromisoformat(row["timestamp"].replace("Z", "+00:00"))
            if stamp.tzinfo is None: stamp = stamp.replace(tzinfo=timezone.utc)
            timestamps.append(int(stamp.timestamp()))
            o.append(float(row["open"])); h.append(float(row["high"]))
            l.append(float(row["low"])); c.append(float(row["close"]))
    if len(timestamps) < 2 or np.any(np.diff(timestamps) != 300):
        raise ValueError(f"{path}: se requieren al menos dos velas 5m consecutivas")
    return CandleData(np.asarray(timestamps), np.asarray(o), np.asarray(h), np.asarray(l), np.asarray(c), 0)


def passive_baseline(symbols, days, profile, source, csv_dir, persistence_values=(1,)):
    """Compara baseline pasivo y replay activo aproximado, sin escritura de BD."""
    from grid.sim.runner import FILTERS
    from grid.meta import DEFAULT_PROFILES
    from scripts.meta_active_study import active_replay, evaluate_active_curve
    from grid.sim.sweep import build_windows, _slice
    from grid.sim.data import ewma_sigma_24h
    horizon = 20 if profile == "RAPIDO" else 45
    loaded = {}
    if source == "csv":
        for symbol in symbols:
            loaded[symbol] = _read_candles_csv(csv_dir / f"{symbol}_5m.csv")
    else:
        from data.binance_client import BinanceClient
        client = BinanceClient(None, None)
        begin = datetime.now(timezone.utc) - timedelta(days=days)
        for symbol in symbols:
            frame = client.get_historical_klines(symbol, "5m", days, start_time=begin)
            from scripts.medir_orden_intravela import _from_frame
            loaded[symbol] = _from_frame(frame, 300)
    rows = []
    profile_cfg = DEFAULT_PROFILES[profile]
    cooldown_s = int(float(profile_cfg.get("rotation_cooldown_hours", 1.0)) * 3600)
    max_reconfigurations = int(profile_cfg.get("max_reconfigurations_per_24h", 4))
    loss_pct = float(profile_cfg["max_loss_pct"])
    for symbol, candles in loaded.items():
        windows = build_windows(candles, horizon, 7)
        for number, window in enumerate(windows, 1):
            sample = _slice(candles, window["start_idx"], window["end_idx"])
            start_index = window["start_idx"]
            reference = float(sample.close[0])
            if start_index >= 2:
                sigma = float(ewma_sigma_24h(candles.close[:start_index], bars_per_hour=12)[-1])
            else:
                sigma = 0.0
            half_width = min(.12, max(.02, 1.5 * sigma * math.sqrt(horizon / 20)))
            low, high = reference * (1 - half_width), reference * (1 + half_width)
            base_row = {"symbol": symbol, "window_start": int(window["start"]),
                "window_end": int(window["end_exclusive"]), "profile": profile,
                "passive_status": "not_run", "active_status": "not_run",
                "low": low, "high": high, "n_levels": 6, "days_outside_range": None}
            try:
                passive = active_replay(sample, low=low, high=high, n=6, capital=100,
                    persistence=len(sample.timestamp) + 1, cooldown_s=cooldown_s,
                    max_per_24h=max_reconfigurations, filters=FILTERS)
                passive_metrics = evaluate_active_curve(passive, capital=100,
                    target_usdt=3, max_loss_pct=loss_pct)
                base_row.update({"passive_status": "measured", "passive_reached_plus_3": passive_metrics["reached"],
                    "passive_days_to_target": passive_metrics["days"],
                    "passive_progress_usdt": passive_metrics["max_advance_usdt"],
                    "passive_drawdown_pct": passive_metrics["drawdown_pct"],
                    "passive_worst_loss_pct": passive_metrics["worst_loss_vs_capital_pct"],
                    "passive_pnl_usdt": passive_metrics["pnl_usdt"]})
            except Exception as exc:
                base_row["passive_status"] = f"error: {type(exc).__name__}: {exc}"
            for persistence in persistence_values:
                row = {**base_row, "persistence": int(persistence),
                    "active_status": "not_run", "approximation": "aproximación: política simplificada por salida de rango; reajuste = liquidar y reabrir; no modela rotación a otra moneda ni decisión por probabilidad"}
                try:
                    active = active_replay(sample, low=low, high=high, n=6, capital=100,
                        persistence=int(persistence), cooldown_s=cooldown_s,
                        max_per_24h=max_reconfigurations, filters=FILTERS)
                    active_metrics = evaluate_active_curve(active, capital=100,
                        target_usdt=3, max_loss_pct=loss_pct)
                    row.update({"active_status": "approximated", "active_reached_plus_3": active_metrics["reached"],
                        "active_days_to_target": active_metrics["days"],
                        "active_progress_usdt": active_metrics["max_advance_usdt"],
                        "active_drawdown_pct": active_metrics["drawdown_pct"],
                        "active_worst_loss_pct": active_metrics["worst_loss_vs_capital_pct"],
                        "active_pnl_usdt": active_metrics["pnl_usdt"],
                        "reconfigurations": active_metrics["reconfigurations"],
                        "commissions_usdt": float(active["commissions_usdt"]),
                        "continuity_checks": len(active["reconfigurations"])})
                except Exception as exc:
                    row["active_status"] = f"error: {type(exc).__name__}: {exc}"
                rows.append(row)
                print(f"[{number}/{len(windows)}] {symbol} {profile} N={persistence}: pasivo {row['passive_status']}; activo {row['active_status']}", flush=True)
    return rows


def replay_summary(rows):
    grouped = defaultdict(list)
    for row in rows:
        grouped[(row["symbol"], row["profile"], int(row.get("persistence") or 1))].append(row)
    summary = []
    for (symbol, profile, persistence), items in sorted(grouped.items()):
        good = [row for row in items if row.get("active_status") == "approximated"]
        summary.append({"symbol": symbol, "profile": profile, "persistence": persistence,
            "windows": len(good), "probability_reached_pct": 100 * sum(bool(row.get("active_reached_plus_3")) for row in good) / len(good) if good else None,
            "passive_probability_pct": 100 * sum(bool(row.get("passive_reached_plus_3")) for row in good) / len(good) if good else None,
            "median_days": float(np.median([row["active_days_to_target"] for row in good if row.get("active_days_to_target") is not None])) if any(row.get("active_days_to_target") is not None for row in good) else None,
            "median_advance_usdt": float(np.median([row.get("active_progress_usdt", 0) for row in good])) if good else None,
            "worst_drawdown_pct": max((row.get("active_drawdown_pct", 0) for row in good), default=None),
            "reconfigurations": sum(int(row.get("reconfigurations", 0)) for row in good),
            "commissions_usdt": sum(float(row.get("commissions_usdt", 0)) for row in good),
            "approximation": "aproximación: política simplificada por salida de rango; reajuste = liquidar y reabrir; no modela rotación a otra moneda ni decisión por probabilidad"})
    return summary


def calibrate_grid(grid_id: int, db_path: Path):
    """Compara fills persistidos del grid con replay pÃºblico de 5m/1m, sin escritura."""
    with _connect_read_only(db_path) as conn:
        grid = conn.execute("SELECT * FROM grids WHERE id=?", (int(grid_id),)).fetchone()
        if grid is None:
            raise ValueError(f"grid {grid_id} no existe")
        opened = datetime.fromisoformat(str(grid["created_at"]).replace("Z", "+00:00"))
        if opened.tzinfo is None:
            opened = opened.replace(tzinfo=timezone.utc)
        levels = conn.execute("SELECT COALESCE(SUM(cycles_completed),0) cycles, "
            "COALESCE(SUM(pnl),0) pnl FROM grid_levels WHERE grid_id=?", (int(grid_id),)).fetchone()
        events = conn.execute("SELECT event_type,COUNT(*) n FROM grid_events WHERE grid_id=? "
            "GROUP BY event_type", (int(grid_id),)).fetchall()
    from data.binance_client import BinanceClient
    from data.exchange_filters import SymbolFilters
    from grid.sim.data import CandleData
    from grid.sim.runner import run_simulation
    client = BinanceClient(None, None)
    info = client.client.get_symbol_info(str(grid["symbol"]))
    if not info:
        raise ValueError(f"Binance no reconoce {grid['symbol']}")
    filters = SymbolFilters.from_symbol_info(info)
    now = datetime.now(timezone.utc)
    span_days = max(1, (now - opened).days)
    start = opened.replace(microsecond=0)
    frame5 = client.get_historical_klines(str(grid["symbol"]), "5m", span_days, start_time=start)
    frame1 = []
    cursor = int(start.timestamp() * 1000)
    end_ms = int(now.timestamp() * 1000)
    raw = []
    while cursor < end_ms:
        page = client._request_with_retries(lambda: client.client.get_klines(symbol=str(grid["symbol"]),
            interval="1m", startTime=cursor, endTime=end_ms, limit=1000),
            symbol=str(grid["symbol"]), interval="1m")
        if not page: break
        raw.extend(page)
        next_cursor = int(page[-1][0]) + 1
        if next_cursor <= cursor or len(page) < 1000: break
        cursor = next_cursor
    frame1 = client._klines_to_dataframe(raw)
    from scripts.medir_orden_intravela import _from_frame
    candles5, candles1 = _from_frame(frame5, 300), _from_frame(frame1, 60)
    strategy = str(grid["strategy"] or "simple").lower()
    if strategy not in {"simple", "smart"}:
        raise ValueError(f"estrategia no compatible con replay de grid: {strategy}")
    geometry = {"low": float(grid["range_low"]), "high": float(grid["range_high"]),
        "n": int(grid["n_levels"])}
    def replay(candles):
        return run_simulation(candles, strategy=strategy, n=geometry["n"],
            capital=float(grid["capital_total"]), low=geometry["low"], high=geometry["high"],
            params=json.loads(grid["params"] or "{}"), fee_pct=.1, filters=filters,
            close_wait_enabled=True, adjust_enabled_global=False, stoploss_enabled=False,
            meta_max_loss_pct=None, sigma_values=np.full(len(candles.timestamp), np.nan))
    result5, result1 = replay(candles5), replay(candles1)
    return [{"grid_id": int(grid_id), "symbol": grid["symbol"], "strategy": strategy,
        "geometry_same": True, "range_low": geometry["low"], "range_high": geometry["high"],
        "n_levels": geometry["n"], "opened_at": opened.isoformat(), "days": span_days,
        "real_cycles": int(levels["cycles"]), "real_pnl_usdt": float(levels["pnl"]),
        "real_buy_fills": sum(int(row["n"]) for row in events if row["event_type"] == "BUY_FILLED"),
        "real_sell_fills": sum(int(row["n"]) for row in events if row["event_type"] == "SELL_FILLED"),
        "sim_cycles_5m": int(result5["metrics"].get("cycles", 0)),
        "sim_cycles_1m": int(result1["metrics"].get("cycles", 0)),
        "sim_pnl_5m_usdt": float(result5["metrics"].get("pnl_total_net_usdt", 0)),
        "sim_pnl_1m_usdt": float(result1["metrics"].get("pnl_total_net_usdt", 0)),
        "buy_fills_5m": int(result5["metrics"].get("buys", 0)),
        "sell_fills_5m": int(result5["metrics"].get("sells", 0)),
        "buy_fills_1m": int(result1["metrics"].get("buys", 0)),
        "sell_fills_1m": int(result1["metrics"].get("sells", 0))}]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    direction = sub.add_parser("calibrar-direccion")
    direction.add_argument("--db", required=True, type=Path)
    direction.add_argument("--days", type=float, default=30)
    direction.add_argument("--out", type=Path, default=Path("./estudios_meta"))
    active = sub.add_parser("replay-activo")
    active.add_argument("--symbols", required=True)
    active.add_argument("--days", type=int, default=180)
    active.add_argument("--profile", choices=("RAPIDO", "PRUDENTE"), default="RAPIDO")
    active.add_argument("--source", choices=("binance", "csv"), default="binance")
    active.add_argument("--csv-dir", type=Path, default=Path("."))
    active.add_argument("--out", type=Path, default=Path("./estudios_meta"))
    persist = sub.add_parser("persistencia-reajuste", aliases=["persistencia"])
    persist.add_argument("--valores", default="1,2")
    persist.add_argument("--symbols", required=True)
    persist.add_argument("--days", type=int, default=180)
    persist.add_argument("--profile", choices=("RAPIDO", "PRUDENTE"), default="RAPIDO")
    persist.add_argument("--source", choices=("binance", "csv"), default="binance")
    persist.add_argument("--csv-dir", type=Path, default=Path("."))
    persist.add_argument("--out", type=Path, default=Path("./estudios_meta"))
    grid = sub.add_parser("calibrar-grid")
    grid.add_argument("--grid-id", required=True, type=int)
    grid.add_argument("--db", required=True, type=Path)
    grid.add_argument("--out", type=Path, default=Path("./estudios_meta"))
    args = parser.parse_args(argv)
    started = time.perf_counter()
    try:
        if args.command == "calibrar-direccion":
            rows = calibrate_direction(args.db, args.days)
            _write(rows, args.out, "calibrar_direccion", {"days": args.days, "read_only": True})
            for row in rows:
                print(f"{row['symbol']}: n={row['n']} Brier={row['brier']:.4f} log-loss={row['log_loss']:.4f} "
                    f"AUC={row['auc']} acierto@0,60={row['accuracy_threshold_0_60']:.3f} "
                    f"umbral mejor={row['best_threshold']:.3f}", flush=True)
        elif args.command in {"replay-activo", "persistencia-reajuste", "persistencia"}:
            if args.command == "replay-activo":
                rows = passive_baseline([s.strip().upper().replace("/", "") for s in args.symbols.split(",")],
                    args.days, args.profile, args.source, args.csv_dir)
            else:
                values = [int(value) for value in str(args.valores).split(",") if value.strip()]
                rows = passive_baseline([s.strip().upper().replace("/", "") for s in args.symbols.split(",")],
                    args.days, args.profile, args.source, args.csv_dir, persistence_values=values)
            summary = replay_summary(rows)
            _write(rows, args.out, "persistencia_reajuste" if args.command != "replay-activo" else "replay_activo",
                {"read_only": True, "summary": summary, "active_status": "approximated",
                 "approximation": "aproximación: política simplificada por salida de rango; reajuste = liquidar y reabrir; no modela rotación a otra moneda ni decisión por probabilidad"})
            for row in summary:
                print(f"{row['symbol']} N={row['persistence']}: activo {row['probability_reached_pct']} %; pasivo {row['passive_probability_pct']} %; reajustes {row['reconfigurations']}; {row['approximation']}", flush=True)
        else:
            rows = calibrate_grid(args.grid_id, args.db)
            _write(rows, args.out, "calibrar_grid", {"read_only_db": True, "public_market_data": True})
            print(json.dumps(rows[0], ensure_ascii=False), flush=True)
    except Exception as exc:
        print(f"Estudio no completado: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    print(f"Tiempo: {time.perf_counter()-started:.3f} s; resultados en {args.out.resolve()}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
