#!/usr/bin/env python3
"""Valida escenarios FHS contra realidad y baseline historico v1."""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from data.market_store import market_db_path
from research.scenarios.validate import validate_scenarios


def _rss_max_mb():
    from scripts.escenarios_velocidad import _rss_mb
    return _rss_mb()


def _operating_symbol_count(store):
    with store._connect() as conn:
        return int(conn.execute(
            "SELECT COUNT(*) FROM symbols WHERE active=1 AND role='operar'").fetchone()[0])


def _estimate(db_path, *, symbols=None, months=12, step_days=7, commission=.1,
        n_scenarios=1000, n_smart=200, profile="RAPIDO", capital=100.0,
        meta=3.0, library_dir="data/derived/scenario_lib", seed=43,
        include_smart=True):
    """Mide una fecha para dos monedas y proyecta el costo por familias."""
    from data.market_store import CandleStore
    store = CandleStore(market_db_path(db_path))
    if symbols:
        selected = [str(value).upper() for value in symbols[:2]]
    else:
        with store._connect() as conn:
            selected = [row[0] for row in conn.execute(
                "SELECT symbol FROM symbols WHERE active=1 AND role='operar' ORDER BY symbol LIMIT 2")]
    if len(selected) < 2:
        raise ValueError("--estimar requiere dos monedas activas con historia")
    datasets = {symbol: store.load(symbol, "5m") for symbol in selected}
    common = set(map(int, datasets[selected[0]].timestamp))
    for symbol in selected[1:]:
        common.intersection_update(map(int, datasets[symbol].timestamp))
    candidate = sorted(common)
    stamp = next((value for value in reversed(candidate)
        if all(len(data.timestamp) - int(__import__("numpy").searchsorted(data.timestamp, value))
            >= 20 * 288 for data in datasets.values())), None)
    if stamp is None:
        raise ValueError("no hay fecha común con horizonte futuro completo")
    families = [("simple_bot", ("simple", "bot"))]
    if include_smart:
        families.append(("smart", ("smart",)))
    measurements = {}
    for symbol in selected:
        for family, strategies in families:
            started = time.perf_counter()
            validate_scenarios(db_path=db_path, months=months, step_days=step_days,
                symbols=[symbol], commission=commission, n_scenarios=n_scenarios,
                n_smart=n_smart, profile=profile, capital=capital, meta=meta,
                library_dir=library_dir, seed=seed, evaluation_timestamp=stamp,
                strategies=strategies)
            elapsed = time.perf_counter() - started
            measurements[(symbol, family)] = elapsed
            print(f"ESTIMACION symbol={symbol} fecha={stamp} grupo={family} "
                f"segundos={elapsed:.3f} rss_max_mb={_rss_max_mb()}")
    dates = max(1, int(months * 30 / step_days))
    symbols_count = len(selected)
    projected = sum(value for (_symbol, _family), value in measurements.items()) / symbols_count
    symbols_count = _operating_symbol_count(store)
    projected *= symbols_count * dates
    print(f"ESTIMACION proyeccion_monedas_operar={symbols_count} fechas_por_moneda={dates} "
        f"segundos={projected:.1f} horas={projected/3600:.2f} incluye_smart={include_smart}")
    if projected > 3 * 3600:
        print("ESTIMACION sugerencia=usar --paso-dias 14 o menos monedas")
    return 0


def render_report(result):
    lines = ["# Validación predictiva de escenarios v2 frente a v1", "",
        f"Horizonte: {result['horizon_days']} días. Fechas evaluadas: {len(result['pairs'])} pares.", "",
        "| Geometría | n | Cobertura P10–P90 | Brier meta v2 | Brier meta v1 | Δ Brier IC 95 % | PIT χ² | Veredicto |",
        "|---|---:|---:|---:|---:|---|---:|---|"]
    for name, row in result.get("summary", {}).items():
        ci = row.get("brier_target_delta_v2_minus_v1_ci95", [None, None])
        lines.append(f"| {name} | {row.get('n', 0)} | {row.get('coverage_p10_p90', 0):.3f} | "
            f"{row.get('brier_target_v2', 0):.4f} | {row.get('brier_target_v1', 0):.4f} | "
            f"[{ci[0]}, {ci[1]}] | {row.get('pit_chi2', 0):.3f} | {row.get('verdict', 'sin datos')} |")
    lines.extend(["", "La corrida es diagnóstica y no habilita decisiones de v2.", ""])
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True)
    parser.add_argument("--meses", type=int, default=12)
    parser.add_argument("--paso-dias", type=int, default=7)
    parser.add_argument("--symbols", nargs="*")
    parser.add_argument("--comision", type=float, default=.1)
    parser.add_argument("--escenarios", type=int, default=1000)
    parser.add_argument("--n-smart", type=int, default=200)
    parser.add_argument("--perfil", choices=("RAPIDO", "PRUDENTE"), default="RAPIDO")
    parser.add_argument("--capital", type=float, default=100.0)
    parser.add_argument("--meta", type=float, default=3.0)
    parser.add_argument("--out", default="reports/escenarios")
    parser.add_argument("--biblioteca", default="data/derived/scenario_lib")
    parser.add_argument("--semilla", type=int, default=43)
    parser.add_argument("--estimar", action="store_true")
    parser.add_argument("--sin-smart", action="store_true")
    args = parser.parse_args(argv)
    if args.estimar:
        return _estimate(args.db, symbols=args.symbols, months=args.meses,
            step_days=args.paso_dias, commission=args.comision,
            n_scenarios=args.escenarios, n_smart=args.n_smart, profile=args.perfil,
            capital=args.capital, meta=args.meta, library_dir=args.biblioteca,
            seed=args.semilla, include_smart=not args.sin_smart)
    if args.meses < 1 or args.paso_dias < 1 or args.escenarios < 1 or args.comision < 0:
        parser.error("meses, paso y escenarios deben ser positivos; comisión no negativa")
    estimated_dates = int(args.meses * 30 / args.paso_dias)
    symbol_count = len(args.symbols or [])
    if not symbol_count:
        db = market_db_path(args.db)
        if db.exists():
            try:
                with sqlite3.connect(db.as_uri() + "?mode=ro", uri=True) as conn:
                    symbol_count = int(conn.execute(
                        "SELECT COUNT(*) FROM symbols WHERE active=1 AND role='operar'").fetchone()[0])
            except sqlite3.Error:
                symbol_count = 0
    estimated_cases = estimated_dates * symbol_count * 5
    estimated_seconds = estimated_cases * 2.0
    print(f"VALIDACION estimación_fechas_por_moneda={estimated_dates} monedas={symbol_count} geometrías=5 "
        f"casos_estimados={estimated_cases} segundos_estimados={estimated_seconds:.0f}")
    if estimated_seconds > 3 * 3600:
        print("VALIDACION aviso=la estimación supera 3 h; usar --paso-dias 14 o menos monedas")
    if os.name == "posix":
        try:
            os.nice(10)
        except OSError:
            pass
    started = time.perf_counter()
    result = validate_scenarios(db_path=args.db, months=args.meses, step_days=args.paso_dias,
        symbols=args.symbols, commission=args.comision, n_scenarios=args.escenarios,
        n_smart=args.n_smart, profile=args.perfil, capital=args.capital, meta=args.meta,
        library_dir=args.biblioteca, seed=args.semilla,
        strategies=("simple", "bot") if args.sin_smart else None,
        progress=lambda done, total: print(f"VALIDACION progreso_fechas={done}/{total}", flush=True))
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M")
    report_path = out / f"validacion_{stamp}.md"
    json_path = out / f"validacion_{stamp}.json"
    report_path.write_text(render_report(result), encoding="utf-8", newline="\n")
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8", newline="\n")
    print(f"VALIDACION archivos={report_path},{json_path} segundos={time.perf_counter()-started:.3f} "
        f"rss_max_mb={_rss_max_mb()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
