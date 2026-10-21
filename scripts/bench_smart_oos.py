"""Offline, walk-forward benchmark for grid strategies using cached candles only."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from api.routes.grid_advisor import RISK_LEVELS, _centered_range, _level
from data.exchange_filters import SymbolFilters
from grid.sim.data import CandleData, ewma_sigma_24h, load_candles
from grid.sim.runner import FILTERS, run_simulation
from grid.structure import select_levels


DEFAULT_VARIANTS = [
    {"name": "simple_hoy", "strategy": "simple", "params": {}, "reference": True,
     "adjust": False, "close_wait": False, "stoploss": False, "vol_spacing": False,
     "loans": False, "compound": False},
    {"name": "simple_vol", "strategy": "simple", "params": {}, "adjust": False,
     "close_wait": False, "stoploss": False, "vol_spacing": True, "loans": False, "compound": False},
    {"name": "smart_hoy", "strategy": "smart", "params": {"adjust_enabled": True, "stop_loss_pct": 5},
     "adjust": True, "close_wait": False, "stoploss": True, "vol_spacing": False,
     "loans": False, "compound": False},
    {"name": "smart_actual", "strategy": "smart", "params": {},
     "adjust": False, "close_wait": True, "stoploss": False, "vol_spacing": True,
     "loans": False, "compound": False},
    {"name": "smart_actual_con_d4", "strategy": "smart", "params": {"trapped_cap_pct": 30, "trapped_age_h": .25, "max_loss_pct": 7},
     "adjust": False, "close_wait": True, "stoploss": False, "vol_spacing": True,
     "loans": False, "compound": False},
    {"name": "smart_actual_loans", "strategy": "smart", "params": {},
     "adjust": False, "close_wait": True, "stoploss": False, "vol_spacing": True,
     "loans": True, "compound": False},
    {"name": "smart_actual_compound", "strategy": "smart", "params": {},
     "adjust": False, "close_wait": True, "stoploss": False, "vol_spacing": True,
     "loans": False, "compound": True},
    {"name": "smart_actual_respace", "strategy": "smart", "params": {},
     "adjust": False, "close_wait": True, "stoploss": False, "vol_spacing": True,
     "loans": False, "compound": False, "respacing": True},
]


def _sigma_hourly(history: CandleData) -> float:
    close = history.close
    if len(close) < 3:
        return float("nan")
    return float(np.std(np.diff(np.log(close)), ddof=1) * np.sqrt(24))


def range_from_history(history: CandleData, mode: str) -> tuple[float, float, float]:
    """Advisor-compatible range using strictly pre-window candles."""
    current = float(history.close[-1])
    sigma = _sigma_hourly(history)
    if mode == "centered":
        bounds = _centered_range(current, sigma, "medium")
        if bounds is None:
            raise ValueError("history has no valid realized volatility")
        return float(bounds["floor"]), float(bounds["ceiling"]), sigma
    frame = pd.DataFrame({"close": history.close, "high": history.high, "low": history.low})
    support, _ = _level(frame["low"].rename("low"), current)
    resistance, _ = _level(frame["high"].rename("high"), current)
    previous = np.r_[history.close[0], history.close[:-1]]
    tr = np.maximum(history.high - history.low,
                    np.maximum(np.abs(history.high - previous), np.abs(history.low - previous)))
    atr = float(pd.Series(tr).rolling(14).mean().iloc[-1])
    risk = RISK_LEVELS["medium"]
    return max(np.finfo(float).tiny, support - risk["floor_atr"] * atr), \
        resistance + risk["ceiling_atr"] * atr, sigma


def history_before_window(candles_1h: CandleData, start_ts: int, history_seconds: int) -> CandleData:
    mask = ((candles_1h.timestamp < int(start_ts))
            & (candles_1h.timestamp >= int(start_ts) - int(history_seconds)))
    return CandleData(*(getattr(candles_1h, key)[mask] for key in
                        ("timestamp", "open", "high", "low", "close")), gaps=0)


def paired_block_bootstrap(differences, *, block: int, samples: int = 3000,
                           seed: int = 3401) -> dict:
    values = np.asarray(differences, dtype=float)
    if values.ndim != 1 or len(values) == 0 or block < 1 or samples < 1:
        raise ValueError("paired differences, positive block and samples required")
    rng = np.random.default_rng(seed)
    n_blocks = math.ceil(len(values) / block)
    draws = np.empty(samples, dtype=float)
    for i in range(samples):
        starts = rng.integers(0, len(values), size=n_blocks)
        indices = np.concatenate([(start + np.arange(block)) % len(values) for start in starts])[:len(values)]
        draws[i] = values[indices].mean()
    low, high = np.quantile(draws, [.025, .975])
    return {"mean_delta": float(values.mean()), "ci95": [float(low), float(high)],
            "wins": int(np.count_nonzero(values > 0)), "windows": len(values),
            "block": block, "samples": samples, "seed": seed}


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def load_symbol_filters(path: Path, symbol: str) -> SymbolFilters:
    """Read Binance symbol filters from either a symbolInfo or exchangeInfo JSON."""
    expected_symbol = symbol.upper() if symbol.upper().endswith("USDT") else f"{symbol.upper()}USDT"
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(payload, dict) and isinstance(payload.get("symbols"), list):
        matches = [item for item in payload["symbols"]
                   if str(item.get("symbol", "")).upper() == expected_symbol]
        if len(matches) != 1:
            raise ValueError(f"expected one {expected_symbol} symbol in {path}; found {len(matches)}")
        payload = matches[0]
    if not isinstance(payload, dict):
        raise ValueError(f"invalid Binance symbol filters JSON: {path}")
    actual_symbol = payload.get("symbol")
    if actual_symbol and str(actual_symbol).upper() != expected_symbol:
        raise ValueError(f"filter symbol {actual_symbol} does not match {expected_symbol}")
    return SymbolFilters.from_symbol_info(payload)


def _variant_run_options(variant: dict) -> dict:
    params = dict(variant.get("params") or {})
    params["loans_enabled"] = bool(variant.get("loans", params.get("loans_enabled", False)))
    params["compound_enabled"] = bool(variant.get("compound", params.get("compound_enabled", False)))
    return {"params": params,
            "adjust_enabled_global": bool(variant.get("adjust", variant.get("adjust_enabled_global", True))),
            "close_wait_enabled": bool(variant.get("close_wait", variant.get("close_wait_enabled", False))),
            "stoploss_enabled": bool(variant.get("stoploss", variant.get("stoploss_enabled", True))),
            "vol_spacing": bool(variant.get("vol_spacing", False))}


def run_benchmark(candles_5m: CandleData, candles_1h: CandleData, *, symbol: str,
                  capital: float, variants: list[dict], window_days: int = 30,
                  step_days: int = 7, history_days: int = 90, range_mode: str = "centered",
                  fee_pct: float = .1, quick: bool = False, csv_hashes: dict | None = None,
                  filters: SymbolFilters | None = None,
                  filters_source: str | None = None) -> dict:
    filters = FILTERS if filters is None else filters
    filters_source = filters_source or "fallback: grid.sim.runner.FILTERS (no cached symbol filters)"
    bar = 300
    window_seconds, history_seconds, step_seconds = window_days * 86400, history_days * 86400, step_days * 86400
    start = int(candles_5m.timestamp[0]) + history_seconds
    final_start = int(candles_5m.timestamp[-1]) - window_seconds + bar
    starts = list(range(start, final_start + 1, step_seconds))
    if quick:
        starts = starts[:10]
    rows = []
    for window_id, start_ts in enumerate(starts):
        history = history_before_window(candles_1h, start_ts, history_seconds)
        if len(history.close) < 3:
            rows.append({"window": window_id, "start": start_ts, "skip_reason": "insufficient_history"})
            continue
        try:
            low, high, sigma = range_from_history(history, range_mode)
        except (ValueError, ArithmeticError) as exc:
            rows.append({"window": window_id, "start": start_ts, "skip_reason": str(exc)})
            continue
        if not np.isfinite(sigma) or sigma <= 0:
            rows.append({"window": window_id, "start": start_ts, "skip_reason": "invalid_sigma"})
            continue
        sim_mask = ((candles_5m.timestamp >= start_ts)
                    & (candles_5m.timestamp < start_ts + window_seconds))
        sim_start_idx = np.flatnonzero(sim_mask)
        if not len(sim_start_idx) or int(candles_5m.timestamp[sim_start_idx[0]]) != start_ts:
            rows.append({"window": window_id, "start": start_ts, "skip_reason": "window_boundary_missing"})
            continue
        first = int(sim_start_idx[0])
        last = int(sim_start_idx[-1]) + 1
        if not low < candles_5m.close[first] < high:
            rows.append({"window": window_id, "start": start_ts, "skip_reason": "start_price_out_of_range"})
            continue
        warm_first = max(0, first - 9 * 24 * 12)
        causal = ewma_sigma_24h(candles_5m.close[warm_first:last], halflife_h=72, bars_per_hour=12)
        sigma_values = causal[first - warm_first:]
        window = CandleData(*(getattr(candles_5m, key)[first:last] for key in
                              ("timestamp", "open", "high", "low", "close")), gaps=0)
        result_variants = {}
        level_counts, level_rules = {}, {}
        window_error = None
        for variant in variants:
            options = _variant_run_options(variant)
            level_selection = select_levels(low, high, float(history.close[-1]), capital, filters,
                fee_pct, .7, sigma, bool(variant.get("vol_spacing", False)),
                float(variant.get("k", .9)))
            variant_n = int(variant.get("n_levels", level_selection["n_levels"]))
            level_counts[variant["name"]] = variant_n
            level_rules[variant["name"]] = level_selection["levels_rule"]
            try:
                result = run_simulation(window, strategy=variant.get("strategy", "simple"), n=variant_n,
                    capital=capital, low=low, high=high, fee_pct=fee_pct,
                    params=options["params"], sigma_values=sigma_values,
                    adjust_enabled_global=options["adjust_enabled_global"],
                    close_wait_enabled=options["close_wait_enabled"],
                    stoploss_enabled=options["stoploss_enabled"],
                    respacing_enabled=bool(variant.get("respacing", False)),
                    filters=filters, include_details=False, csv_hash=(csv_hashes or {}).get("5m"))
            except ValueError as exc:
                window_error = f"infeasible_grid: {exc}"
                break
            result_variants[variant["name"]] = result
        if window_error is not None:
            rows.append({"window": window_id, "start": start_ts, "skip_reason": window_error})
            continue
        rows.append({"window": window_id, "start": start_ts,
                     "start_iso": datetime.fromtimestamp(start_ts, timezone.utc).isoformat(),
                     "end": int(window.timestamp[-1]), "range_low": low, "range_high": high,
                     "history_sigma_24h": sigma, "levels": level_counts,
                     "level_rule": level_rules,
                     "results": result_variants})
    return {"symbol": symbol, "capital": capital, "window_days": window_days,
            "step_days": step_days, "history_days": history_days, "range_mode": range_mode,
            "fee_pct": fee_pct, "quick": quick, "csv_hashes": csv_hashes or {},
            "filters_source": filters_source,
            "variants": variants, "windows": rows,
            "summary": summarize(rows, variants, step_days)}


def summarize(windows: list[dict], variants: list[dict], step_days: int) -> dict:
    usable = [row for row in windows if "results" in row]
    ref = next((v["name"] for v in variants if v.get("reference")), None)
    summary = {}
    for variant in variants:
        name = variant["name"]
        values = [(row, row["results"][name]["metrics"]) for row in usable]
        pnl = np.asarray([m["pnl_total_net_usdt"] for _, m in values], dtype=float)
        dd = np.asarray([m["max_drawdown_pct"] for _, m in values], dtype=float)
        cycle_realized = np.asarray([m.get("cycle_realized_net_usdt", 0.0) for _, m in values], dtype=float)
        cycles = np.asarray([m.get("cycles_completed", 0) for _, m in values], dtype=float)
        worst_idx = int(np.argmin(pnl)) if len(pnl) else None
        item = {"windows": len(values), "mean_pnl": float(pnl.mean()) if len(pnl) else None,
                "median_pnl": float(np.median(pnl)) if len(pnl) else None,
                "pct_positive": float(np.mean(pnl > 0) * 100) if len(pnl) else None,
                "p5_pnl": float(np.quantile(pnl, .05)) if len(pnl) else None,
                "worst_window": (values[worst_idx][0]["start_iso"] if worst_idx is not None else None),
                "worst_pnl": float(pnl[worst_idx]) if worst_idx is not None else None,
                "mean_drawdown_pct": float(dd.mean()) if len(dd) else None,
                "mean_cycle_realized_net_usdt": float(cycle_realized.mean()) if len(cycle_realized) else None,
                "mean_cycles": float(cycles.mean()) if len(cycles) else None,
                "mean_live_time_pct": float(np.mean([100 - m["pause_pct"] for _, m in values])) if values else None}
        if ref and name != ref:
            paired = [(row["results"][name]["metrics"]["pnl_total_net_usdt"]
                       - row["results"][ref]["metrics"]["pnl_total_net_usdt"]) for row in usable]
            item["vs_reference"] = paired_block_bootstrap(paired, block=max(1, math.ceil(30 / step_days))) if paired else None
        summary[name] = item
    return {"usable_windows": len(usable), "skipped_windows": len(windows) - len(usable),
            "reference": ref, "variants": summary}


def write_report(result: dict, out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    (out / "results.json").write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
    lines = [f"# Smart OOS benchmark — {result['symbol']}", "",
             f"Capital: {result['capital']} USDT; rango: {result['range_mode']}; "
             f"ventana: {result['window_days']} d; paso: {result['step_days']} d; "
             f"historial: {result['history_days']} d.", "",
             f"Filtros: {result.get('filters_source', 'fuente no registrada')}.", "",
             f"Ventanas útiles: {result['summary']['usable_windows']}; omitidas: {result['summary']['skipped_windows']}.", "",
             "| Variante | Media USDT | Mediana | Positivas | p5 | Peor ventana | DD medio | Tiempo activo | Delta ref. (IC95%, wins) | Veredicto |",
             "|---|---:|---:|---:|---:|---|---:|---:|---|---|"]
    for variant in result["variants"]:
        item = result["summary"]["variants"][variant["name"]]
        paired = item.get("vs_reference")
        verdict = "referencia" if paired is None else (
            "mejora" if paired["ci95"][0] > 0 else "empeora" if paired["ci95"][1] < 0 else "sin diferencia demostrada")
        delta = "—" if paired is None else f"{paired['mean_delta']:.4f} [{paired['ci95'][0]:.4f}, {paired['ci95'][1]:.4f}], {paired['wins']}/{paired['windows']}"
        lines.append(f"| {variant['name']} | {item['mean_pnl']} | {item['median_pnl']} | {item['pct_positive']}% | "
                     f"{item['p5_pnl']} | {item['worst_window']} ({item['worst_pnl']}) | "
                     f"{item['mean_drawdown_pct']}% | {item['mean_live_time_pct']}% | {delta} | {verdict} |")
    lines += ["", "| Variante | Ganancia neta por ciclos completados (media USDT) | P&L total con inventario (media USDT) | Ciclos medios |",
              "|---|---:|---:|---:|"]
    for variant in result["variants"]:
        item = result["summary"]["variants"][variant["name"]]
        lines.append(f"| {variant['name']} | {item['mean_cycle_realized_net_usdt']:.6f} | "
                     f"{item['mean_pnl']:.6f} | {item['mean_cycles']:.3f} |")
    lines += ["", "Los intervalos pareados usan bootstrap por bloques, 3000 réplicas y semilla fija.",
              "Los resultados son simulaciones históricas y no cambian flags de la aplicación.", ""]
    (out / "summary.md").write_text("\n".join(lines), encoding="utf-8")
    for row in result["windows"]:
        (out / f"window-{row['window']:03d}.json").write_text(json.dumps(row, indent=2), encoding="utf-8")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--data-dir", type=Path, default=Path("data/cache"))
    parser.add_argument("--window-days", type=int, default=30)
    parser.add_argument("--step-days", type=int, default=7)
    parser.add_argument("--history-days", type=int, default=90)
    parser.add_argument("--range-mode", choices=("centered", "structural"), default="centered")
    parser.add_argument("--capital", type=float, default=100)
    parser.add_argument("--fee-pct", type=float, default=.1)
    parser.add_argument("--variants", help="JSON list or path to JSON list")
    parser.add_argument("--reference")
    parser.add_argument("--filters-json", type=Path,
                        help="Binance symbolInfo/exchangeInfo JSON (defaults to data/cache/{symbol}_filters.json)")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--quick", action="store_true")
    args = parser.parse_args(argv)
    symbol = args.symbol.upper().replace("/", "")
    p5 = args.data_dir / f"{symbol.lower()}_5m.csv"
    p1 = args.data_dir / f"{symbol.lower()}_1h.csv"
    if not p5.is_file() or not p1.is_file():
        parser.error(f"cached files required: {p5} and {p1}")
    default_filters_path = args.data_dir / f"{symbol.lower()}_filters.json"
    filters_path = args.filters_json or (default_filters_path if default_filters_path.is_file() else None)
    if args.filters_json is not None and not args.filters_json.is_file():
        parser.error(f"filters JSON does not exist: {args.filters_json}")
    filters = FILTERS if filters_path is None else load_symbol_filters(filters_path, symbol)
    filters_source = ("fallback: grid.sim.runner.FILTERS (no cached symbol filters)"
                      if filters_path is None else str(filters_path))
    variants = json.loads(json.dumps(DEFAULT_VARIANTS))
    if args.variants:
        raw = Path(args.variants)
        variants = json.loads(raw.read_text(encoding="utf-8") if raw.is_file() else args.variants)
    if args.reference:
        if args.reference not in {variant["name"] for variant in variants}:
            parser.error("--reference must name a configured variant")
        for variant in variants:
            variant["reference"] = variant["name"] == args.reference
    candles5, candles1 = load_candles(p5), load_candles(p1, allow_gaps=True)
    hashes = {"5m": _sha(p5), "1h": _sha(p1)}
    result = run_benchmark(candles5, candles1, symbol=symbol, capital=args.capital,
        variants=variants, window_days=args.window_days, step_days=args.step_days,
        history_days=args.history_days, range_mode=args.range_mode, fee_pct=args.fee_pct,
        quick=args.quick, csv_hashes=hashes, filters=filters, filters_source=filters_source)
    write_report(result, args.out)
    print(args.out / "summary.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
