#!/usr/bin/env python3
"""Calcula, guarda y reporta el marcador de pronósticos públicos."""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from research.scorecard.runner import run_scorecard


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="market_data.db")
    parser.add_argument("--registro-db", default="asple_predictor.db")
    parser.add_argument("--symbols", nargs="*")
    parser.add_argument("--horizontes", nargs="+", type=int, default=[24, 72])
    parser.add_argument("--out", default="reports/marcador/")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args(argv)


def _lower_priority():
    if os.name == "posix":
        try:
            os.nice(10)
        except OSError:
            pass


def _write_report(result, output_dir):
    aggregates = [row for row in result["verdicts"] if row[1] is None]
    useful = [row for row in aggregates if row[5] == "sirve"]
    lines = ["# Marcador de pronósticos", "", f"Corrida: {result['run_id']}", "",
        f"Duración total: {result['summary']['seconds']:.2f} s", "",
        "## Resumen ejecutivo (5 líneas)",
        f"1. Lo que v2 puede usar: {', '.join(f'{r[2]} {r[3]} h {r[4]}' for r in useful) or 'nada ha superado los criterios.'}",
        f"2. Volatilidad: {', '.join(f'{r[4]} {r[5]} ({r[6]})' for r in aggregates if r[2] == 'volatilidad') or 'sin veredicto agregado.'}",
        f"3. Ruptura: {', '.join(f'{r[4]} {r[5]}' for r in aggregates if r[2] == 'ruptura') or 'sin veredicto agregado.'}",
        f"4. Régimen y dirección: {', '.join(f'{r[2]} {r[4]} {r[5]}' for r in aggregates if r[2] in {'regimen','direccion'}) or 'sin veredicto agregado.'}",
        "5. Lo que v2 debe ignorar: resultados sin veredicto agregado `sirve`; el marcador no activa cambios.", "",
        "## Veredictos agregados y por moneda", "",
        "| Moneda | Objetivo | H | Modelo | Veredicto | Motivo |", "|---|---|---:|---|---|---|"]
    for row in result["verdicts"]:
        lines.append(f"| {row[1] or 'Agregado'} | {row[2]} | {row[3]} | {row[4]} | {row[5]} | {row[6]} |")
    lines += ["", "## Factores de calibración", "",
        "| Moneda | Tipo | Horizonte | Modelo | Factor | n | IC 95 % |", "|---|---|---:|---|---:|---:|---|"]
    for row in result["factors"]:
        lines.append(f"| {row[1]} | {row[2]} | {row[3]} h | {row[4]} | {row[5]:.6g} | {row[6]} | [{row[7]:.6g}, {row[8]:.6g}] |")
    lines += ["", "## Ruptura por k: deciles predichos frente a observados", "",
        "| Moneda | H | k | Decil | n | Predicho | Observado | Brier | Skill | Pendiente |", "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for key, item in result["summary"].get("diagnostics", {}).items():
        if not isinstance(item, dict) or ":" not in key:
            continue
        symbol, horizon = key.rsplit(":", 1)
        for rupture in item.get("rupture", []):
            for decile_index, decile in enumerate(rupture.get("deciles", []), 1):
                lines.append(f"| {symbol} | {horizon} | {rupture['k']} | {decile_index} | {decile['n']} | {decile['predicted']:.5f} | {decile['observed']:.5f} | {rupture['brier']:.5f} | {rupture['brier_skill']:.5f} | {rupture['calibration_slope']:.5f} |")
    lines += ["", "## Régimen, dirección y correlación mensual", "",
        "| Moneda | Objetivo | H/mes | Modelo | Métrica | Valor | IC 95 % |", "|---|---|---:|---|---|---:|---|"]
    for row in result["records"]:
        if row[2] in {"regimen", "direccion", "correlacion"}:
            lines.append(f"| {row[1]} | {row[2]} | {row[3] or row[4]} | {row[4]} | {row[7]} | {row[8]:.6g} | [{row[9]}, {row[10]}] |")
    lines += ["", "## Métricas detalladas", "",
        "| Moneda | Objetivo | H | Modelo | Métrica | Valor | IC 95 % |", "|---|---|---:|---|---|---:|---|"]
    for row in result["records"]:
        _, symbol, target, horizon, model, _k, n, metric, value, low, high = row
        lines.append(f"| {symbol} | {target} | {horizon} | {model} | {metric} (n={n}) | {value:.6g} | [{low}, {high}] |")
    lines += ["", f"Historia insuficiente: {', '.join(result['summary']['insufficient']) or 'ninguna'}", "",
        f"Tiempo por moneda (s): `{result['summary']['per_symbol_seconds']}`", ""]
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M")
    path = output_dir / f"marcador_{stamp}.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return path


def main(argv=None):
    args = parse_args(argv)
    _lower_priority()
    result = run_scorecard(args.db, symbols=args.symbols, horizons=args.horizontes,
        dry_run=args.dry_run, output_dir=args.out, registry_db_path=args.registro_db)
    path = _write_report(result, args.out)
    print(f"MARCADOR reporte={path}")
    print(f"MARCADOR resumen={result['summary']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
