"""Medición sintética reproducible de selección de cartera, sin red ni BD real."""
import argparse
from collections import Counter, defaultdict
import copy
import json
from pathlib import Path
import subprocess
import sys
import time
import types

import numpy as np

from tests.test_meta_own_grid_evaluation import plan_setup
from grid.sim.data import CandleData


def scenario(revision="a062e09", latency_s=0.0):
    source = (Path("grid/meta_operator.py").read_text(encoding="utf-8") if revision == "working"
        else subprocess.check_output(["git", "show", revision + ":grid/meta_operator.py"]).decode("utf-8"))
    module = types.ModuleType("meta_portfolio_benchmark_" + revision)
    sys.modules[module.__name__] = module
    exec(compile(source, revision + ":grid/meta_operator.py", "exec"), module.__dict__)
    module.uuid = types.SimpleNamespace(uuid4=lambda: types.SimpleNamespace(hex="plan-sintetico"))
    operator, core, goal, _, _ = plan_setup()
    operator.__class__ = module.MetaOperator
    # El constructor del checkout vigente inicializa también los campos nuevos.
    del operator._candidate_alternatives
    counts, timings = Counter(), defaultdict(float)

    def delayed(obj, method):
        original = getattr(obj, method)
        label = type(obj).__name__ + "." + method
        def call(*args, **kwargs):
            counts[label] += 1
            if latency_s:
                time.sleep(latency_s)
            return original(*args, **kwargs)
        setattr(obj, method, call)

    def timed(obj, method, label=None):
        original = getattr(obj, method)
        label = label or method
        def call(*args, **kwargs):
            started = time.perf_counter()
            try:
                return original(*args, **kwargs)
            finally:
                timings[label] += time.perf_counter() - started
                counts[label] += 1
        setattr(obj, method, call)

    symbols = ("JUPUSDT", "XRPUSDT", "ZROUSDT", "ADAUSDT", "PEPEUSDT", "SOLUSDT")
    operator.db.get_active_coins = lambda: [{"symbol": symbol} for symbol in symbols]
    operator.scanner.scan = lambda **_kwargs: {"results": [{"symbol": symbol,
        "eligible": i < 4, "score": .9 - i * .05, "sigma_24h_open": .03 + i * .01,
        "reasons": [] if i < 4 else ["filtro sintético"]} for i, symbol in enumerate(symbols)]}
    end = 1_800_000_000
    timestamps = np.arange(end - 180 * 86400, end, 300, dtype=np.int64)
    prices = 100 + 2 * np.sin(np.arange(len(timestamps)) / 800)
    candles = CandleData(timestamp=timestamps, open=prices, high=prices + .1,
        low=prices - .1, close=prices, gaps=0)
    operator._load_candles = lambda _symbol: candles
    goal["config_snapshot"].update({"max_grids": 4, "max_bots": 8})

    def replay_cache_hit(row, strategy, capital, target, low, high, n, cfg, **kwargs):
        # Representa la revisión medida en S1: replay y estadísticas ya en caché.
        operator._analysis_timing["replay_cache_hits"] += 1
        rows = [{"window_start": f"ventana-{i:02d}", "reached": capital * .12 >= target,
            "pnl_usdt": capital * .12, "days": 8 + i % 3, "drawdown_pct": 2, "step_days": 7}
            for i in range(12)]
        estimate = module.summarize_replays(rows, independent_window_days=20, target_usdt=target)
        return {"symbol": row["symbol"], "strategy": "simple" if strategy == "individual_bot" else strategy,
            "pieces": [{"symbol": row["symbol"], "strategy": "simple" if strategy == "individual_bot" else strategy,
                "piece_type": "individual_bot_grid" if strategy == "individual_bot" else "grid",
                "capital_usdt": capital, "n_levels": n, "floor": low, "ceiling": high}],
            "estimate": estimate, "replay_probability": estimate["probability"], "window_results": rows,
            "signals_used": [], "signals_omitted": [], "touch_signal": {}, "_touch_observations": []}
    operator._evaluate = replay_cache_hit
    operator.advisor = lambda *_args: {"recommended_floor": 90, "recommended_ceiling": 110,
        "suggested_grids": 4, "suggested_strategy": "simple", "net_per_cycle_after_dust_usdt": .5}
    for symbol in symbols[:3]:
        for margin in (.3, .5, .7):
            key = (operator.clock() // 86400, symbol, 70.0, margin, 20)
            operator._advisor_cache[key] = operator._store_advisor_cache_entry(operator.advisor(), 100.005)
    for method in ("get_active_coins", "get_grid", "get_grid_levels", "list_grids_by_status",
        "count_open_grids", "get_readiness", "get_latest_vol_model_stats_forecast_at",
        "get_vol_model_stats_rows", "get_latest_prediction"):
        delayed(operator.db, method)
    for method in ("get_open_orders", "get_balance", "get_book_ticker"):
        delayed(operator.engine.exchange, method)
    for name in ("allocation_vectors", "combine_aligned_replays", "combine_piece_signals",
        "signal_adjusted_probability", "rank_alternatives"):
        timed(module, name)
    for name in ("_candidate_alternatives", "_testnet_openability", "_apply_intelligence"):
        timed(operator, name)
    started = time.perf_counter()
    plan = operator._build_plan(goal, record=False, plan_kind="revision")
    total = time.perf_counter() - started
    operator._add_piece_from_free_cash = lambda *_args, **_kwargs: None
    decision_started = time.perf_counter()
    decision = operator._review_allocations(goal, core.valuation(3), plan=plan)
    decision_s = time.perf_counter() - decision_started
    return {"revision": revision, "latency_s": latency_s, "total_s": total,
        "decision_s": decision_s, "review_total_s": total + decision_s,
        "timings_s": dict(timings), "counts": dict(counts),
        "candles_per_symbol": len(timestamps), "combinations": len(plan["combinations_evaluated"]),
        "plan": copy.deepcopy(plan), "decision": decision}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--revision", default="a062e09")
    parser.add_argument("--latency", type=float, default=.05)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = scenario(args.revision, args.latency)
    args.output.write_text(json.dumps(result, ensure_ascii=True, default=str), encoding="utf-8")
    print(json.dumps({key: value for key, value in result.items() if key not in {"plan", "decision"}}, indent=2))


if __name__ == "__main__":
    main()
