#!/usr/bin/env python3
"""Evalúa piezas de grid sobre escenarios FHS sin conectarse al operador."""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from research.scenarios.evaluate import evaluate_history, evaluate_pieces, paired_fee_sensitivity
from research.scenarios.objective import objective_limits, rank_alternatives_v2
from scripts.escenarios_velocidad import _rss_mb


def _parse_piece(value):
    parts = str(value).split(":")
    if len(parts) < 3:
        raise ValueError("pieza debe ser SÍMBOLO:estrategia:capital[:opciones]")
    piece = {"symbol": parts[0].upper(), "strategy": parts[1].lower(),
        "capital": float(parts[2]), "n": 6}
    for option in parts[3:]:
        key, separator, raw = option.partition("=")
        if not separator:
            raise ValueError(f"opción de pieza inválida: {option}")
        if key == "ancho":
            piece["width_sigma"] = float(raw.lower().removesuffix("sigma"))
        elif key == "n": piece["n"] = int(raw)
        elif key in {"low", "high"}: piece[key] = float(raw)
        elif key == "compra": piece["buy_offset_pct"] = float(raw)
        elif key == "venta": piece["sell_offset_pct"] = float(raw)
        else: raise ValueError(f"opción no reconocida: {key}")
    if piece["strategy"] == "bot" and "buy_offset_pct" not in piece:
        raise ValueError("pieza bot requiere compra=<porcentaje>")
    return piece


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True)
    parser.add_argument("--perfil", required=True, choices=("RAPIDO", "PRUDENTE"))
    parser.add_argument("--capital", required=True, type=float)
    parser.add_argument("--meta", required=True, type=float)
    parser.add_argument("--pieza", action="append", required=True)
    parser.add_argument("--modo", choices=("alternativas", "cartera", "historia"), default="alternativas")
    parser.add_argument("--meses", type=int, default=24)
    parser.add_argument("--paso-dias", type=int, default=7)
    parser.add_argument("--desde", help="fecha ISO inicial para la historia")
    parser.add_argument("--escenarios", type=int, default=1000)
    parser.add_argument("--n-smart", type=int, default=200)
    parser.add_argument("--comision", type=float, default=.1,
        help="comisión de cada lado en porcentaje (por defecto: 0.1)")
    parser.add_argument("--comparar-comision", type=float,
        help="compara pareado con otra comisión usando la misma semilla y escenarios")
    parser.add_argument("--semilla", type=int, default=43)
    parser.add_argument("--library-dir", default="data/derived/scenario_lib")
    parser.add_argument("--settings-db", help="SQLite de perfiles; solo se abre en lectura")
    args = parser.parse_args(argv)
    started = time.perf_counter()
    if args.comision < 0 or (args.comparar_comision is not None and args.comparar_comision < 0):
        parser.error("las comisiones no pueden ser negativas")
    if args.modo == "historia":
        if args.comparar_comision is not None:
            parser.error("--comparar-comision no se combina con --modo historia")
        history = evaluate_history([_parse_piece(item) for item in args.pieza],
            perfil=args.perfil, capital=args.capital, meta=args.meta, db_path=args.db,
            months=args.meses, step_days=args.paso_dias, since=args.desde,
            fee_pct=args.comision, library_dir=args.library_dir, seed=args.semilla)
        print(json.dumps(history, ensure_ascii=False, indent=2))
        print(f"EVALUACION segundos={time.perf_counter()-started:.3f} rss_max_mb={_rss_mb()}")
        return 0
    result = evaluate_pieces([_parse_piece(item) for item in args.pieza],
        perfil=args.perfil, capital=args.capital, meta=args.meta,
        n_scenarios=args.escenarios, n_smart=args.n_smart, seed=args.semilla,
        db_path=args.db, library_dir=args.library_dir, mode=args.modo,
        fee_pct=args.comision)
    for index, row in enumerate(result["pieces"], 1):
        print(f"PIEZA {index} {row['piece']} {json.dumps(row['metrics'], ensure_ascii=False)}"
            f" fills_promedio={row['fills_mean']:.3f} aviso={row.get('aviso') or 'ninguno'}")
    if args.modo == "cartera":
        print(f"CARTERA {json.dumps(result['portfolio'], ensure_ascii=False)}")
    else:
        print("COMPARACIÓN PAREADA | tipo | n | pieza base | alternativa | "
            "media Simple pareada/base | media alternativa pareada | Δ ganancia [IC 95 %] | "
            "Δ P(límite) | Δ P(meta)")
        for item in result["smart_paired"]:
            pnl = item["pnl_usdt"]
            loss = item["loss_probability"]
            target = item["target_probability"]
            simple_mean = item.get("simple_mean_pnl_usdt")
            base_mean = item["from_mean_pnl_usdt"]
            alternative_mean = item["to_mean_pnl_usdt"]
            base_display = f"{(simple_mean if simple_mean is not None else base_mean):.4f}"
            print(f"{item['label']} | {item['n']} | {item['from_index'] + 1} | "
                f"{item['to_index'] + 1} | "
                f"{base_display} | "
                f"{alternative_mean:.4f} | {pnl['mean']:.4f} "
                f"[{pnl['ci_low']:.4f}, {pnl['ci_high']:.4f}] | "
                f"{loss['mean']:.4f} | {target['mean']:.4f}")
    if args.comparar_comision is not None:
        if args.modo != "alternativas":
            parser.error("--comparar-comision requiere --modo alternativas")
        comparison = evaluate_pieces([_parse_piece(item) for item in args.pieza],
            perfil=args.perfil, capital=args.capital, meta=args.meta,
            n_scenarios=args.escenarios, n_smart=args.n_smart, seed=args.semilla,
            db_path=args.db, library_dir=args.library_dir, mode=args.modo,
            fee_pct=args.comparar_comision)
        fee_rows = paired_fee_sensitivity(result, comparison, seed=args.semilla)
        print("COMISION_PAREADA | comisión base | comisión comparación | n | Δ ganancia | Δ P(meta) | Δ P(límite)")
        for row in fee_rows:
            print(f"pieza={row['piece_index'] + 1} | {row['fee_base_pct']:.4f}% | "
                f"{row['fee_comparison_pct']:.4f}% | {row['n']} | "
                f"{row['pnl_usdt']['mean']:.4f} | {row['p_target']['mean']:.4f} | "
                f"{row['p_loss']['mean']:.4f}")
        limits = objective_limits(args.perfil,
            settings_db=args.settings_db or args.db)
        ranked = rank_alternatives_v2(result["pieces"], perfil=args.perfil,
            capital_meta=args.capital, max_loss_pct=limits["max_loss_pct"],
            p_limit_cap=limits["p_limit_cap"], ev_floor_pct=limits["ev_floor_pct"])
        print("OBJETIVO_V2 | posición | pieza | estado | P(meta) | motivos")
        for position, row in enumerate(ranked, 1):
            name = (row.get("piece") or {}).get("label") or (row.get("piece") or {}).get("symbol") or "USDT quieto"
            status = "factible" if row["objetivo"]["factible"] else "no factible"
            print(f"{position} | {name} | {status} | "
                f"{float((row.get('metrics') or {}).get('p_target', 0)):.4f} | "
                f"{'; '.join(row['objetivo']['motivos']) or 'cumple los límites'}")
    print(f"EVALUACION segundos={time.perf_counter()-started:.3f} rss_max_mb={_rss_mb()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
