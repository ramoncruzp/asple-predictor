#!/usr/bin/env python3
"""Resumen de ventanas históricas para la validación offline de escenarios."""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from data.market_store import CandleStore, market_db_path
from research.scenarios.evaluate import evaluate_history


def _rss_max_mb():
    try:
        import psutil
        return psutil.Process().memory_info().rss / (1024 * 1024)
    except ImportError:
        return None


def _report_markdown(result):
    lines = ["# Validación histórica de escenarios", "",
        "Resumen descriptivo; no sustituye la aceptación walk-forward con escenarios FHS y baseline v1.", "",
        f"Horizonte: {result['horizon_days']} días. Comisión: {result['fee_pct']:.4f} % por lado.", ""]
    for symbol, data in result["symbols"].items():
        lines.extend([f"## {symbol}", ""])
        if data.get("estado"):
            lines.extend([f"Estado: {data['estado']}", ""])
            continue
        lines.append(f"Ventanas candidatas: {data['ventanas_candidatas']}.")
        lines.append("")
        for piece in data["pieces"]:
            lines.extend([f"### {piece['piece']}", "", "| Variante | n | PnL medio | PnL/día | P10 | P50 | P90 | P(límite) | P(meta) | IC 95 % media |",
                "|---|---:|---:|---:|---:|---:|---:|---:|---:|---|"])
            for variant, values in piece["variantes"].items():
                if values.get("n", 0) == 0:
                    lines.append(f"| {variant} | 0 | — | — | — | — | — | — | — | — |")
                    continue
                ci = values["ic95_pnl_medio"]
                lines.append(f"| {variant} | {values['n']} | {values['pnl_medio_usdt']:.4f} | "
                    f"{values['pnl_diario_usdt']:.4f} | {values['pnl_p10_usdt']:.4f} | "
                    f"{values['pnl_p50_usdt']:.4f} | {values['pnl_p90_usdt']:.4f} | "
                    f"{values['p_loss']:.4f} | {values['p_target']:.4f} | [{ci[0]:.4f}, {ci[1]:.4f}] |")
            lines.append("")
    return "\n".join(lines) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True)
    parser.add_argument("--perfil", choices=("RAPIDO", "PRUDENTE"), default="RAPIDO")
    parser.add_argument("--capital", type=float, default=100.0)
    parser.add_argument("--meta", type=float, default=3.0)
    parser.add_argument("--meses", type=int, default=12)
    parser.add_argument("--paso-dias", type=int, default=7)
    parser.add_argument("--symbols", nargs="*")
    parser.add_argument("--desde")
    parser.add_argument("--comision", type=float, default=.1)
    parser.add_argument("--out", default="reports/escenarios")
    parser.add_argument("--semilla", type=int, default=43)
    args = parser.parse_args(argv)
    if args.meses < 1 or args.paso_dias < 1 or args.comision < 0:
        parser.error("meses/paso deben ser positivos y comisión no negativa")
    started = time.perf_counter()
    if os.name == "posix":
        try:
            os.nice(10)
        except OSError:
            pass
    db_path = market_db_path(args.db)
    store = CandleStore(db_path)
    symbols = [str(item).upper() for item in args.symbols or []]
    if not symbols:
        with store._connect() as conn:
            symbols = [str(row[0]) for row in conn.execute(
                "SELECT symbol FROM symbols WHERE active=1 AND role='operar' ORDER BY symbol")]
    pieces = [{"symbol": symbol, "strategy": "simple", "capital": args.capital,
        "n": 6, "width_sigma": 2.0} for symbol in symbols]
    if not pieces:
        parser.error("no hay monedas activas role=operar para validar")
    result = evaluate_history(pieces, perfil=args.perfil, capital=args.capital,
        meta=args.meta, db_path=db_path, months=args.meses, step_days=args.paso_dias,
        since=args.desde, fee_pct=args.comision, seed=args.semilla)
    output_dir = Path(args.out)
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M")
    report = output_dir / f"validacion_{stamp}.md"
    report.write_text(_report_markdown(result), encoding="utf-8", newline="\n")
    print(json.dumps(result, ensure_ascii=False))
    print(f"VALIDACION informe={report} segundos={time.perf_counter()-started:.3f} rss_max_mb={_rss_max_mb()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
