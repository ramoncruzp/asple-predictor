#!/usr/bin/env python3
"""Actualiza fuera del servicio la caché horaria experimental de escenarios."""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from data.market_store import CandleStore, market_db_path
from grid.meta import DEFAULT_PROFILES
from research.scenarios.candidates import build_candidates, build_smart_candidates
from research.scenarios.evaluate import _sigma_estimates, evaluate_pieces
from research.scenarios.objective import objective_limits, rank_alternatives_v2

RETENTION_DAYS = 7
SCENARIO_COUNT = 1000


def _rss_max_mb():
    try:
        if os.name == "posix":
            import resource
            return round(float(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss) / 1024, 2)
        import psutil
        return round(psutil.Process().memory_info().rss / (1024 * 1024), 2)
    except (ImportError, OSError, AttributeError):
        return None


def _contexts(app_db):
    with sqlite3.connect(Path(app_db).resolve().as_uri() + "?mode=ro", uri=True) as conn:
        conn.row_factory = sqlite3.Row
        table_names = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        settings = {}
        if "meta_profile_settings" in table_names:
            settings = {row["profile"]: json.loads(row["settings"] or "{}")
                for row in conn.execute("SELECT profile,settings FROM meta_profile_settings")}
        contexts = []
        if "meta_goals" in table_names:
            goals = conn.execute("SELECT * FROM meta_goals WHERE status IN "
                "('ACTIVE','PIECES_BLOCKED') ORDER BY id").fetchall()
            for goal in goals:
                profile = str(goal["profile"]).upper()
                defaults = DEFAULT_PROFILES.get(profile)
                if defaults:
                    started = goal["started_at"] or goal["created_at"]
                    elapsed = 0
                    if started:
                        try:
                            elapsed = max(0, (datetime.now(timezone.utc) -
                                datetime.fromisoformat(str(started).replace("Z", "+00:00")).replace(
                                    tzinfo=timezone.utc)).days)
                        except ValueError:
                            pass
                    contexts.append({"goal_id": int(goal["id"]), "profile": profile,
                        "capital": float(goal["capital_initial"]),
                        "target": float(goal["target_usdt"]),
                        "horizon": max(1, int(defaults["max_days"]) - elapsed)})
        for profile, defaults in DEFAULT_PROFILES.items():
            if str((settings.get(profile) or {}).get("decision_engine", "v1")).lower() == "v2":
                contexts.append({"goal_id": None, "profile": profile,
                    "capital": 100.0, "target": 3.0,
                    "horizon": int(defaults["max_days"])})
        return contexts


def _read_market_inputs(store):
    with store._connect() as conn:
        symbols = [row[0] for row in conn.execute(
            "SELECT symbol FROM symbols WHERE active=1 AND role IN ('operar','datos') ORDER BY symbol")]
    filters, prices, sigmas, skipped = {}, {}, {}, {}
    for symbol in symbols:
        try:
            data = store.load(symbol, "5m")
            if len(data.close) < 30 * 288 + 1:
                raise ValueError("historia menor a 30 días")
            symbol_filters = store.filters(symbol)
            if symbol_filters is None:
                raise ValueError("filtros ausentes")
            filters[symbol] = symbol_filters
            prices[symbol] = float(data.close[-1])
            sigmas[symbol] = float(_sigma_estimates(data)[0])
        except (KeyError, OSError, ValueError) as exc:
            skipped[symbol] = str(exc)
    return filters, prices, sigmas, skipped


def _active_grid_pieces(app_db, goal_id):
    if goal_id is None:
        return []
    with sqlite3.connect(Path(app_db).resolve().as_uri() + "?mode=ro", uri=True) as conn:
        rows = conn.execute("SELECT g.symbol,g.strategy,g.capital_total,g.range_low,g.range_high,"
            "g.n_levels FROM meta_piece_links l JOIN grids g ON g.id=l.grid_id "
            "WHERE l.meta_id=? AND l.released_at IS NULL AND g.status='active'", (goal_id,)).fetchall()
    output = []
    for symbol, strategy, capital, low, high, levels in rows:
        if str(strategy).lower() not in {"simple", "smart"}:
            continue
        output.append({"symbol": str(symbol).upper(), "strategy": str(strategy).lower(),
            "capital": float(capital), "low": float(low), "high": float(high),
            "n": int(levels), "candidate_key": f"mantener:{goal_id}:{symbol}:{levels}:{low}:{high}"})
    return output


def _insert_run(conn, context, as_of):
    run_id = str(uuid.uuid4())
    conn.execute("INSERT INTO scenario_eval_runs(run_id,as_of,goal_id,profile,horizon_days,"
        "n_scenarios,seconds,rss_mb,status,error) VALUES(?,?,?,?,?,?,0,NULL,'running',NULL)",
        (run_id, as_of, context["goal_id"], context["profile"], context["horizon"], SCENARIO_COUNT))
    return run_id


def _evaluate_context(context, *, store, app_db, market_path, library_dir,
        filters, prices, sigmas, skipped, evaluator):
    if not filters:
        return {"status": "skipped", "reason": "ningún símbolo con filtros, velas e historia"}
    free = float(context["capital"])
    built = build_candidates(symbols=filters, capital_libre=free,
        perfil=context["profile"], filters_by_symbol=filters,
        price_by_symbol=prices, sigma_by_symbol=sigmas,
        horizon_days=context["horizon"])
    candidates = built["candidates"][:-1]
    candidates.extend(_active_grid_pieces(app_db, context["goal_id"]))
    pieces = [{key: value for key, value in row.items() if key != "candidate_key"}
        for row in candidates]
    first = evaluator(pieces, perfil=context["profile"], capital=free,
        meta=context["target"], n_scenarios=SCENARIO_COUNT, n_smart=200,
        seed=43, db_path=market_path, library_dir=library_dir, mode="alternativas") if pieces else {
            "pieces": [], "skipped_symbols": {}}
    ranked = rank_alternatives_v2(first["pieces"], perfil=context["profile"],
        capital_meta=free) if first["pieces"] else []
    simple_ranked = [row for row in ranked if row.get("piece", {}).get("strategy") == "simple"][:2]
    smart = build_smart_candidates(simple_ranked)
    second_rows = []
    if smart:
        second = evaluator(smart, perfil=context["profile"], capital=free,
            meta=context["target"], n_scenarios=SCENARIO_COUNT, n_smart=200,
            seed=43, db_path=market_path, library_dir=library_dir, mode="alternativas")
        second_rows = second.get("pieces", [])
    results = []
    by_key = {str(row.get("candidate_key")): row for row in candidates}
    for row in [*first.get("pieces", []), *second_rows]:
        piece = row.get("piece", {})
        candidate_key = str(piece.get("candidate_key") or _candidate_key(piece))
        objective = rank_alternatives_v2([row], perfil=context["profile"],
            capital_meta=free)[0]["objetivo"]
        results.append({"candidate_key": candidate_key, "symbol": piece.get("symbol"),
            "strategy": piece.get("strategy", "simple"),
            "params": {key: value for key, value in piece.items()
                if key not in {"symbol", "strategy", "candidate_key"}},
            "metrics": row.get("metrics") or _scenario_metrics(row),
            "objective": objective})
    quiet = {"candidate_key": _candidate_key({"strategy": "usdt_quieto"}),
        "symbol": None, "strategy": "usdt_quieto", "params": {},
        "metrics": {"pnl_mean_usdt": 0, "p_target": 0, "p_loss": 0,
            "p_loss_ci95": [0, 0], "cvar_5_usdt": 0,
            "cut_slippage_mean_usdt": 0, "cut_slippage_p90_usdt": 0},
        "objective": {"factible": True, "motivos": [], "clave_orden": (0, 0, 0)}}
    results.append(quiet)
    return {"status": "ok", "results": results,
        "discarded": built["discarded"], "skipped_symbols": skipped}


def _candidate_key(piece):
    import hashlib
    raw = json.dumps(piece, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def _scenario_metrics(row):
    scenario_results = row.get("scenario_results") or []
    pnls = [float(item["pnl_usdt"]) for item in scenario_results]
    return {"n": len(pnls), "pnl_mean_usdt": sum(pnls) / len(pnls) if pnls else 0,
        "p_target": sum(bool(item["reached"]) for item in scenario_results) / len(pnls) if pnls else 0,
        "p_loss": sum(bool(item["loss_cut"]) for item in scenario_results) / len(pnls) if pnls else 0,
        "p_loss_ci95": [0, 1] if pnls else [None, None], "cvar_5_usdt": 0,
        "cut_slippage_mean_usdt": 0, "cut_slippage_p90_usdt": 0}


def run_cache(app_db, market_db, *, library_dir="data/derived/scenario_lib",
        evaluator=evaluate_pieces, now=None):
    started = time.perf_counter()
    app_path = Path(app_db).resolve()
    market_path = market_db_path(market_db)
    store = CandleStore(market_path)
    as_of = (now or datetime.now(timezone.utc)).isoformat()
    filters, prices, sigmas, skipped = _read_market_inputs(store)
    contexts = _contexts(app_path)
    with store._connect() as conn:
        conn.execute("DELETE FROM scenario_eval_results WHERE run_id IN "
            "(SELECT run_id FROM scenario_eval_runs WHERE as_of < datetime('now', ?))",
            (f"-{RETENTION_DAYS} days",))
        conn.execute("DELETE FROM scenario_eval_runs WHERE as_of < datetime('now', ?)",
            (f"-{RETENTION_DAYS} days",))
        run_ids = [_insert_run(conn, context, as_of) for context in contexts]
    results_summary = []
    for context, run_id in zip(contexts, run_ids):
        try:
            evaluated = _evaluate_context(context, store=store, app_db=app_path,
                market_path=market_path, library_dir=library_dir, filters=filters,
                prices=prices, sigmas=sigmas, skipped=skipped, evaluator=evaluator)
            with store._connect() as conn:
                for row in evaluated.get("results", []):
                    conn.execute("INSERT OR REPLACE INTO scenario_eval_results VALUES(?,?,?,?,?,?,?)",
                        (run_id, row["candidate_key"], row.get("symbol"), row["strategy"],
                         json.dumps(row["params"], ensure_ascii=False),
                         json.dumps(row["metrics"], ensure_ascii=False),
                         json.dumps(row["objective"], ensure_ascii=False)))
                conn.execute("UPDATE scenario_eval_runs SET status=?,error=?,seconds=?,rss_mb=? "
                    "WHERE run_id=?", (evaluated["status"],
                        json.dumps({"skipped": evaluated.get("skipped_symbols", skipped),
                            "discarded": evaluated.get("discarded", {})}, ensure_ascii=False),
                        time.perf_counter() - started, _rss_max_mb(), run_id))
            results_summary.append({"run_id": run_id, **evaluated})
        except Exception as exc:
            with store._connect() as conn:
                conn.execute("UPDATE scenario_eval_runs SET status='failed',error=?,seconds=?,rss_mb=? "
                    "WHERE run_id=?", (str(exc), time.perf_counter() - started,
                        _rss_max_mb(), run_id))
            raise
    return {"contexts": len(contexts), "runs": results_summary,
        "seconds": time.perf_counter() - started, "rss_mb": _rss_max_mb()}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app-db", required=True)
    parser.add_argument("--market-db", required=True)
    parser.add_argument("--biblioteca", default="data/derived/scenario_lib")
    args = parser.parse_args(argv)
    if os.name == "posix":
        try:
            os.nice(10)
        except OSError:
            pass
    started = time.perf_counter()
    try:
        result = run_cache(args.app_db, args.market_db, library_dir=args.biblioteca)
        print(f"SCENARIO_CACHE estado=completed contextos={result['contexts']} "
            f"segundos={time.perf_counter()-started:.3f} rss_max_mb={result['rss_mb']}")
        return 0
    except Exception as exc:
        print(f"SCENARIO_CACHE estado=failed error={exc} "
            f"segundos={time.perf_counter()-started:.3f} rss_max_mb={_rss_max_mb()}",
            file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
