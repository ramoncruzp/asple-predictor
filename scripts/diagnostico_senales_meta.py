"""Diagnóstico de señales Meta desde SQLite en modo solo lectura, sin red."""
from __future__ import annotations

import argparse
from contextlib import closing
from collections import Counter
import json
import math
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote


SIGNALS = ("volatility", "break_prob", "direction", "scanner", "advisor", "touch_buy_to_sell")
LABELS = {"volatility": "Volatilidad", "break_prob": "Probabilidad de ruptura",
          "direction": "Dirección (probability_up de model_a_down = P(baja))",
          "scanner": "Scanner", "advisor": "Advisor",
          "touch_buy_to_sell": "Toque compra a venta"}


def _clip(value: float, low: float = -1.0, high: float = 1.0) -> float:
    return max(low, min(high, float(value)))


def compare_volatility_scales(forecast_sigma_per_hour: float, sigma_24h_open: float,
                              horizon_h: int = 24) -> dict:
    """Muestra el efecto de comparar sigma por hora con sigma acumulada 24 h."""
    forecast = float(forecast_sigma_per_hour)
    reference = float(sigma_24h_open)
    if forecast <= 0 or reference <= 0 or int(horizon_h) <= 0:
        raise ValueError("las sigmas y el horizonte deben ser positivos")
    current = _clip(math.log(forecast / reference))
    equivalent_24h = forecast * math.sqrt(int(horizon_h))
    proposed = _clip(math.log(equivalent_24h / reference))
    return {"forecast_sigma_per_hour": forecast, "sigma_24h_open": reference,
            "forecast_horizon_h": int(horizon_h), "forecast_sigma_24h_equivalent": equivalent_24h,
            "normalized_current": current, "normalized_if_scaled_to_24h": proposed}


def _quantile(values, fraction):
    values = sorted(float(value) for value in values)
    if not values:
        return None
    position = (len(values) - 1) * float(fraction)
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return values[lower]
    return values[lower] + (values[upper] - values[lower]) * (position - lower)


def summarize(values):
    values = [float(value) for value in values if value is not None]
    if not values:
        return {"n": 0, "min": None, "p10": None, "median": None, "p90": None, "max": None,
                "at_minus_one_pct": None, "at_plus_one_pct": None}
    return {"n": len(values), "min": min(values), "p10": _quantile(values, .1),
            "median": _quantile(values, .5), "p90": _quantile(values, .9), "max": max(values),
            "at_minus_one_pct": 100 * sum(value <= -1 + 1e-12 for value in values) / len(values),
            "at_plus_one_pct": 100 * sum(value >= 1 - 1e-12 for value in values) / len(values)}


def _connect_read_only(db_path: Path):
    absolute = str(db_path.resolve()).replace("\\", "/")
    connection = sqlite3.connect(f"file:{quote(absolute, safe='/:')}?mode=ro", uri=True)
    connection.execute("PRAGMA query_only=ON")
    return connection


def diagnose(db_path: Path, days: float = 1.0, profile: str | None = None):
    cutoff = (datetime.now(timezone.utc) - timedelta(days=max(0.0, float(days)))).isoformat()
    with closing(_connect_read_only(db_path)) as connection:
        tables = {row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        if "meta_learning_samples" not in tables:
            raise RuntimeError("la BD no tiene la tabla meta_learning_samples")
        params = [cutoff]
        clause = "source_kind='live' AND datetime(sample_at)>=datetime(?)"
        if profile:
            clause += " AND profile=?"
            params.append(profile.upper())
        rows = connection.execute(
            f"SELECT symbol, signals_json FROM meta_learning_samples WHERE {clause}", params).fetchall()
        omitted_rows = []
        if "meta_ledger" in tables:
            omission_sql = """SELECT ledger.details FROM meta_ledger AS ledger
                JOIN meta_goals AS goal ON goal.id=ledger.meta_id
                WHERE ledger.event_type='META_PLAN_EVALUATED' AND datetime(ledger.ts)>=datetime(?)"""
            omission_params = [cutoff]
            if profile and "meta_goals" in tables:
                omission_sql += " AND goal.profile=?"
                omission_params.append(profile.upper())
            omission_sql += " ORDER BY ledger.id DESC"
            if "meta_goals" in tables:
                omitted_rows = connection.execute(omission_sql, omission_params).fetchall()

    grouped = defaultdict(lambda: {"raw": [], "normalized": [], "references": []})
    sample_symbols = defaultdict(int)
    present = defaultdict(int)
    version_counts = Counter()
    distinct_values = defaultdict(set)
    direction_active = Counter()
    version_sample_counts = defaultdict(Counter)
    version_present = defaultdict(Counter)
    for symbol, raw_json in rows:
        symbol = str(symbol or "?")
        sample_symbols[symbol] += 1
        try:
            payload = json.loads(raw_json or "{}")
        except (TypeError, ValueError):
            continue
        try:
            version = int(payload.get("signals_version") or 1)
        except (TypeError, ValueError):
            version = 1
        version_counts["v2" if version >= 2 else "v1"] += 1
        version_label = "v2" if version >= 2 else "v1"
        version_sample_counts[symbol][version_label] += 1
        for signal in payload.get("signals_used") or []:
            name = signal.get("name")
            if name not in SIGNALS:
                continue
            raw, normalized = signal.get("value"), signal.get("normalized")
            if normalized is None:
                continue
            key = (symbol, name)
            if raw is not None:
                grouped[key]["raw"].append(float(raw))
            grouped[key]["normalized"].append(float(normalized))
            distinct_values[key].add(float(normalized))
            present[key] += 1
            version_present[(symbol, name)][version_label] += 1
            if name == "direction":
                try:
                    p_down = float(signal.get("p_down", raw))
                    if p_down >= .60:
                        direction_active[symbol] += 1
                except (TypeError, ValueError):
                    pass
            if name == "volatility":
                diagnostic = signal.get("diagnostic") or {}
                reference = diagnostic.get("reference_sigma_24h")
                horizon = diagnostic.get("forecast_horizon_h", 24)
                if reference is not None:
                    grouped[key]["references"].append(compare_volatility_scales(
                        float(raw), float(reference), int(horizon)))

    total = len(rows)
    touch_omissions = Counter()
    for (raw_details,) in omitted_rows:
        try:
            details = json.loads(raw_details or "{}")
        except (TypeError, ValueError):
            continue
        summarized = details.get("signals_omitted_summary")
        if summarized is not None:
            for item in summarized:
                if item.get("name") != "touch_buy_to_sell":
                    continue
                symbol = str(item.get("symbol") or "?")
                if profile and str(details.get("profile") or profile).upper() != profile.upper():
                    continue
                touch_omissions[(symbol, str(item.get("reason") or "sin motivo guardado"))] += int(
                    item.get("count") or 0)
            continue
        for alternative in details.get("alternatives") or []:
            symbol = str(alternative.get("symbol") or "?")
            if profile and str(alternative.get("profile") or profile).upper() != profile.upper():
                continue
            for item in alternative.get("signals_omitted") or []:
                if item.get("name") == "touch_buy_to_sell":
                    touch_omissions[(symbol, str(item.get("reason") or "sin motivo guardado"))] += 1
    print(f"Diagnóstico Meta: {total} muestras live de los últimos {days:g} días; BD abierta mode=ro.")
    print(f"Versiones de señales: v1={version_counts['v1']}, v2={version_counts['v2']}")
    if profile:
        print(f"Perfil: {profile.upper()}")
    if not rows:
        print("No hay muestras en ese intervalo.")
    for symbol in sorted(sample_symbols):
        print(f"\n{symbol}: {sample_symbols[symbol]} alternativas")
        for name in SIGNALS:
            item = grouped.get((symbol, name), {"raw": [], "normalized": [], "references": []})
            raw_stats = summarize(item["raw"])
            normalized_stats = summarize(item["normalized"])
            coverage = present[(symbol, name)]
            print(f"  {LABELS[name]}: cobertura {coverage}/{sample_symbols[symbol]} "
                  f"({(100*coverage/sample_symbols[symbol]):.1f} %)")
            if name == "direction":
                active = direction_active[symbol]
                print(f"    cobertura activa P(baja) ≥ 0,60: {active}/{sample_symbols[symbol]} "
                      f"({(100*active/sample_symbols[symbol]):.1f} %)")
            v1_total = version_sample_counts[symbol]["v1"]
            v2_total = version_sample_counts[symbol]["v2"]
            print(f"    cobertura por versión: v1 {version_present[(symbol, name)]['v1']}/{v1_total}; "
                  f"v2 {version_present[(symbol, name)]['v2']}/{v2_total}")
            unique = sorted(distinct_values[(symbol, name)])
            print("    valores normalizados distintos: " +
                  (", ".join(f"{value:.6g}" for value in unique) if unique else "ninguno"))
            print("    crudo min/p10/mediana/p90/max: " + "/".join(
                "—" if raw_stats[k] is None else f"{raw_stats[k]:.6g}"
                for k in ("min", "p10", "median", "p90", "max")))
            print("    normalizado min/p10/mediana/p90/max: " + "/".join(
                "—" if normalized_stats[k] is None else f"{normalized_stats[k]:.6g}"
                for k in ("min", "p10", "median", "p90", "max")) +
                f"; en -1: {normalized_stats['at_minus_one_pct'] if normalized_stats['at_minus_one_pct'] is not None else '—'} %; "
                f"en +1: {normalized_stats['at_plus_one_pct'] if normalized_stats['at_plus_one_pct'] is not None else '—'} %")
            if name == "volatility" and item["references"]:
                pairs = item["references"]
                print("    sigmas por muestra: pronóstico por hora / referencia sigma 24 h / "
                      "equivalente pronóstico 24 h: " + "; ".join(
                          f"{p['forecast_sigma_per_hour']:.6g} / {p['sigma_24h_open']:.6g} / "
                          f"{p['forecast_sigma_24h_equivalent']:.6g}" for p in pairs[:8]))
                print(f"    normalización actual mediana { _quantile([p['normalized_current'] for p in pairs], .5):.6g}; "
                      f"si se iguala el horizonte 24 h { _quantile([p['normalized_if_scaled_to_24h'] for p in pairs], .5):.6g}")
    if touch_omissions:
        print("\nMotivos guardados para omitir toque compra a venta:")
        for (symbol, reason), count in sorted(touch_omissions.items()):
            print(f"  {symbol}: {count} alternativas — {reason}")
    else:
        print("\nNo hay motivos de omisión de toque guardados en la bitácora para el intervalo consultado.")
    if "predictions" in tables and "outcomes" in tables:
        try:
            with closing(_connect_read_only(db_path)) as connection:
                result = connection.execute("""SELECT p.symbol, COUNT(*),
                    SUM(CASE WHEN p.probability_up >= 0.60 THEN 1 ELSE 0 END),
                    MIN(p.probability_up), MAX(p.probability_up)
                    FROM predictions p WHERE p.model_name='model_a_down' AND p.interval='1h'
                    AND datetime(p.predicted_at)>=datetime(?) GROUP BY p.symbol ORDER BY p.symbol""", (cutoff,)).fetchall()
            print("\nPredicciones model_a_down 1h (probability_up representa P(baja)): ")
            for symbol, n, qualifying, minimum, maximum in result:
                print(f"  {symbol}: {qualifying}/{n} con P(baja) ≥ 0,60; min {minimum}; max {maximum}")
        except sqlite3.Error as exc:
            print(f"\nNo se pudo leer la tabla de predicciones: {exc}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, type=Path,
        help="ruta directa al archivo SQLite de la aplicación; no se lee .env")
    parser.add_argument("--days", type=float, default=1,
        help="ventana de muestras live en días (por defecto: hoy, 1 día)")
    parser.add_argument("--profile", choices=("RAPIDO", "PRUDENTE"))
    args = parser.parse_args(argv)
    try:
        diagnose(args.db, args.days, args.profile)
    except (OSError, sqlite3.Error, RuntimeError, ValueError) as exc:
        print(f"Error de diagnóstico: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
