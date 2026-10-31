#!/usr/bin/env python3
"""Actualiza fuera del servicio la caché horaria experimental de escenarios."""
from __future__ import annotations

import argparse
import gc
import json
import os
import sqlite3
import sys
import threading
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
from research.scenarios.evaluate import (_sigma_estimates, evaluate_pieces,
    prepare_scenario_batches)
from research.scenarios.objective import objective_limits, rank_alternatives_v2

RETENTION_DAYS = 7
SCENARIO_COUNT = 1000
MAX_PIECES_PER_BATCH = 6
ABANDON_AFTER_MINUTES = 30
_RSS_PEAK_MB = 0.0


class CacheLimitExceeded(Exception):
    def __init__(self, status, message):
        self.status = status
        super().__init__(message)


class CacheMemoryExceeded(CacheLimitExceeded):
    def __init__(self):
        super().__init__("memoria", "tope RSS excedido por vigilante")


class CacheTimeoutExceeded(CacheLimitExceeded):
    def __init__(self):
        super().__init__("timeout", "tope de tiempo excedido por vigilante")


def _rss_current_mb():
    try:
        if os.name == "posix":
            with open("/proc/self/statm", encoding="ascii") as stream:
                resident = int(stream.read().split()[1])
            return resident * os.sysconf("SC_PAGE_SIZE") / (1024 * 1024)
        import ctypes
        from ctypes import wintypes
        class MemoryCounters(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t),
                ("PrivateUsage", ctypes.c_size_t)]
        counters = MemoryCounters()
        counters.cb = ctypes.sizeof(counters)
        process = ctypes.windll.kernel32.GetCurrentProcess()
        reader = ctypes.windll.psapi.GetProcessMemoryInfo
        reader.argtypes = [wintypes.HANDLE, ctypes.POINTER(MemoryCounters), wintypes.DWORD]
        reader.restype = wintypes.BOOL
        ok = reader(process, ctypes.byref(counters), counters.cb)
        if not ok:
            return None
        return counters.WorkingSetSize / (1024 * 1024)
    except (ImportError, OSError, AttributeError, ValueError, IndexError):
        return None


def _check_limits(started, max_rss_mb, max_seconds):
    if max_rss_mb is not None:
        current = _rss_current_mb()
        if current is not None and current > max_rss_mb:
            raise CacheLimitExceeded("memoria", f"RSS {current:.1f} MB supera {max_rss_mb} MB")
    if max_seconds is not None and time.perf_counter() - started > max_seconds:
        raise CacheLimitExceeded("timeout", f"tiempo supera {max_seconds} s")


def _rss_max_mb():
    global _RSS_PEAK_MB
    try:
        if os.name == "posix":
            import resource
            measured = float(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss) / 1024
        else:
            import ctypes
            from ctypes import wintypes
            class MemoryCounters(ctypes.Structure):
                _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t),
                    ("PrivateUsage", ctypes.c_size_t)]
            counters = MemoryCounters()
            counters.cb = ctypes.sizeof(counters)
            process = ctypes.windll.kernel32.GetCurrentProcess()
            reader = ctypes.windll.psapi.GetProcessMemoryInfo
            reader.argtypes = [wintypes.HANDLE, ctypes.POINTER(MemoryCounters), wintypes.DWORD]
            reader.restype = wintypes.BOOL
            if not reader(process, ctypes.byref(counters), counters.cb):
                return None
            measured = counters.PeakWorkingSetSize / (1024 * 1024)
        _RSS_PEAK_MB = max(_RSS_PEAK_MB, measured)
        return round(_RSS_PEAK_MB, 2)
    except (ImportError, OSError, AttributeError):
        return None


def _contexts(app_db, prices=None):
    prices = prices or {}
    with sqlite3.connect(Path(app_db).resolve().as_uri() + "?mode=ro", uri=True) as conn:
        conn.row_factory = sqlite3.Row
        table_names = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        settings = {}
        if "meta_profile_settings" in table_names:
            settings = {row["profile"]: json.loads(row["settings"] or "{}")
                for row in conn.execute("SELECT profile,settings FROM meta_profile_settings")}
        contexts = []
        if "meta_goals" in table_names:
            goals = conn.execute("SELECT * FROM meta_goals WHERE status IN ('ACTIVE','PIECES_BLOCKED') ORDER BY id").fetchall()
            for goal in goals:
                profile = str(goal["profile"]).upper()
                defaults = DEFAULT_PROFILES.get(profile)
                if not defaults:
                    continue
                initial = float(goal["capital_initial"])
                links = conn.execute("SELECT g.id,g.symbol FROM meta_piece_links l JOIN grids g ON g.id=l.grid_id WHERE l.meta_id=? AND l.released_at IS NULL", (int(goal["id"]),)).fetchall()
                delivered = returned = fees = inventory = reserved_buys = 0.0
                balance_known = True
                for link in links:
                    grid_id = int(link["id"])
                    symbol = str(link["symbol"]).upper()
                    events = conn.execute("SELECT event_type,price,details FROM grid_events WHERE grid_id=? AND event_type IN ('BUY_FILLED','SELL_FILLED','CELL_LIQUIDATED') ORDER BY id", (grid_id,)).fetchall()
                    for event in events:
                        details = json.loads(event["details"] or "{}")
                        quote = details.get("cummulative_quote_qty", details.get("quote_qty", details.get("executed_quote_qty")))
                        if event["event_type"] == "CELL_LIQUIDATED":
                            quote = details.get("cash_proceeds_net", quote)
                        qty = details.get("executed_qty", details.get("qty", details.get("quantity")))
                        if quote is None and qty is not None and event["price"] is not None:
                            quote = float(qty) * float(event["price"])
                        if quote is None:
                            balance_known = False
                            continue
                        quote = float(quote)
                        fee = details.get("commission_usdt", details.get("fee_usdt"))
                        fees += quote * .001 if fee is None else float(fee)
                        if event["event_type"] == "BUY_FILLED":
                            delivered += quote
                        else:
                            returned += quote
                    current_price = prices.get(symbol)
                    if current_price is None:
                        balance_known = False
                    levels = conn.execute("SELECT held_qty,state,order_id,capital FROM grid_levels WHERE grid_id=?", (grid_id,)).fetchall()
                    inventory += sum(float(level["held_qty"] or 0) for level in levels) * float(current_price or 0) * .999
                    reserved_buys += sum(float(level["capital"] or 0) for level in levels
                        if level["order_id"] is not None and "BUY" in str(level["state"]).upper())
                free_cash = max(0.0, initial - delivered + returned - fees - reserved_buys)
                current_equity = free_cash + inventory
                started = goal["started_at"] or goal["created_at"]
                elapsed = 0
                if started:
                    try:
                        elapsed = max(0, (datetime.now(timezone.utc) - datetime.fromisoformat(str(started).replace("Z", "+00:00")).replace(tzinfo=timezone.utc)).days)
                    except ValueError:
                        pass
                config = json.loads(goal["config_snapshot"] or "{}")
                loss_pct = float(config.get("max_loss_pct", defaults["max_loss_pct"]))
                threshold = initial * (1 - loss_pct / 100)
                loss_headroom = max(0.0, current_equity - threshold)
                effective_loss_pct = min(loss_pct, 100 * loss_headroom / current_equity if current_equity > 0 else 0.0)
                contexts.append({"goal_id": int(goal["id"]), "profile": profile,
                    "capital": current_equity, "free_cash": free_cash,
                    "target": max(0.0, float(goal["target_usdt"]) - max(0.0, current_equity - initial)),
                    "horizon": max(1, int(defaults["max_days"]) - elapsed),
                    "max_loss_pct": effective_loss_pct, "balance_approximate": not balance_known})
        for profile, defaults in DEFAULT_PROFILES.items():
            if str((settings.get(profile) or {}).get("decision_engine", "v1")).lower() == "v2":
                contexts.append({"goal_id": None, "profile": profile, "capital": 100.0,
                    "free_cash": 100.0, "target": 3.0, "horizon": int(defaults["max_days"]),
                    "max_loss_pct": float(defaults["max_loss_pct"]), "balance_approximate": False})
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
        rows = conn.execute("SELECT g.id,g.symbol,g.strategy,g.capital_total,g.range_low,g.range_high,"
            "g.n_levels FROM meta_piece_links l JOIN grids g ON g.id=l.grid_id "
            "WHERE l.meta_id=? AND l.released_at IS NULL AND g.status='active'", (goal_id,)).fetchall()
    output = []
    for grid_id, symbol, strategy, capital, low, high, levels in rows:
        if str(strategy).lower() not in {"simple", "smart"}:
            continue
        output.append({"symbol": str(symbol).upper(), "strategy": str(strategy).lower(),
            "capital": float(capital), "low": float(low), "high": float(high),
            "n": int(levels), "grid_id": int(grid_id), "is_current": True,
            "candidate_key": f"mantener:{goal_id}:{grid_id}:{symbol}:{levels}:{low}:{high}"})
    return output


def _insert_run(conn, context, as_of):
    run_id = str(uuid.uuid4())
    conn.execute("INSERT INTO scenario_eval_runs(run_id,as_of,goal_id,profile,horizon_days,"
        "n_scenarios,seconds,rss_mb,status,error) VALUES(?,?,?,?,?,?,0,NULL,'running',NULL)",
        (run_id, as_of, context["goal_id"], context["profile"], context["horizon"], SCENARIO_COUNT))
    return run_id


def _evaluate_context(context, *, store, app_db, market_path, library_dir,
        filters, prices, sigmas, skipped, evaluator, check_limits=None,
        on_progress=None, progress_state=None):
    progress_state = {} if progress_state is None else progress_state
    if context.get("balance_approximate"):
        return {"status": "skipped", "reason": "efectivo o inventario sin atribución completa"}
    if not filters:
        return {"status": "skipped", "reason": "ningún símbolo con filtros, velas e historia"}
    free = float(context["free_cash"])
    capital = float(context["capital"])
    built = build_candidates(symbols=filters, capital_libre=free,
        perfil=context["profile"], filters_by_symbol=filters,
        price_by_symbol=prices, sigma_by_symbol=sigmas,
        horizon_days=context["horizon"])
    candidates = built["candidates"][:-1]
    candidates.extend(_active_grid_pieces(app_db, context["goal_id"]))
    progress_state.update({"generated_count": len(candidates) + int(built.get("invalid_count", 0)),
        "generated_candidates": len(candidates), "evaluated_count": 0,
        "discarded": dict(built["discarded"]), "candidate_seconds": {}})
    pieces = [dict(row) for row in candidates]
    eval_kwargs = dict(perfil=context["profile"], capital=capital,
        meta=context["target"], horizon_days=context["horizon"],
        max_loss_pct=context["max_loss_pct"], n_scenarios=SCENARIO_COUNT, n_smart=200,
        seed=43, db_path=market_path, library_dir=library_dir, mode="alternativas")
    first_rows, failed_candidates = [], {}
    candidate_seconds = {}
    for symbol in sorted({str(piece["symbol"]).upper() for piece in pieces}):
        symbol_pieces = [piece for piece in pieces if str(piece["symbol"]).upper() == symbol]
        symbol_started = time.perf_counter()
        batches = (prepare_scenario_batches(symbol, horizon_days=context["horizon"],
            n_scenarios=SCENARIO_COUNT, seed=43, db_path=market_path,
            library_dir=library_dir) if evaluator is evaluate_pieces else None)
        for start in range(0, len(symbol_pieces), MAX_PIECES_PER_BATCH):
            chunk = symbol_pieces[start:start + MAX_PIECES_PER_BATCH]
            kwargs = dict(eval_kwargs)
            if batches is not None:
                kwargs["scenario_batches"] = batches
                kwargs["keep_curves"] = False
            rows, errors = _evaluate_with_isolation(evaluator, chunk, **kwargs)
            first_rows.extend(rows)
            failed_candidates.update(errors)
            progress_state["evaluated_count"] += len(rows)
            for reason in errors.values():
                label = reason.split(":", 1)[0]
                progress_state["discarded"][label] = progress_state["discarded"].get(label, 0) + 1
            if on_progress and rows:
                on_progress(rows)
            del rows, errors
            gc.collect()
            if check_limits:
                check_limits()
            candidate_seconds[symbol] = round(time.perf_counter() - symbol_started, 3)
            progress_state["candidate_seconds"] = dict(candidate_seconds)
        del batches, symbol_pieces
        gc.collect()
    first = {"pieces": first_rows, "skipped_symbols": skipped}
    ranked = rank_alternatives_v2(first["pieces"], perfil=context["profile"],
        capital_meta=capital) if first["pieces"] else []
    simple_ranked = [row for row in ranked if row.get("piece", {}).get("strategy") == "simple"][:2]
    smart = build_smart_candidates(simple_ranked)
    progress_state["generated_count"] += len(smart)
    progress_state["generated_candidates"] += len(smart)
    second_rows = []
    for symbol in sorted({str(piece["symbol"]).upper() for piece in smart}):
        smart_started = time.perf_counter()
        symbol_pieces = [piece for piece in smart if str(piece["symbol"]).upper() == symbol]
        batches = (prepare_scenario_batches(symbol, horizon_days=context["horizon"],
            n_scenarios=200, seed=43, db_path=market_path, library_dir=library_dir)
            if evaluator is evaluate_pieces else None)
        for start in range(0, len(symbol_pieces), MAX_PIECES_PER_BATCH):
            kwargs = dict(perfil=context["profile"], capital=capital, meta=context["target"],
                horizon_days=context["horizon"], max_loss_pct=context["max_loss_pct"],
                n_scenarios=200, n_smart=200, seed=43, db_path=market_path,
                library_dir=library_dir, mode="alternativas")
            if batches is not None:
                kwargs["scenario_batches"] = batches
                kwargs["keep_curves"] = False
            rows, errors = _evaluate_with_isolation(evaluator,
                symbol_pieces[start:start + MAX_PIECES_PER_BATCH], **kwargs)
            second_rows.extend(rows)
            failed_candidates.update(errors)
            progress_state["evaluated_count"] += len(rows)
            for reason in errors.values():
                label = reason.split(":", 1)[0]
                progress_state["discarded"][label] = progress_state["discarded"].get(label, 0) + 1
            if on_progress and rows:
                on_progress(rows)
            del rows, errors
            gc.collect()
            if check_limits:
                check_limits()
            candidate_seconds[symbol] = round(candidate_seconds.get(symbol, 0.0) +
                time.perf_counter() - smart_started, 3)
            smart_started = time.perf_counter()
            progress_state["candidate_seconds"] = dict(candidate_seconds)
        del batches, symbol_pieces
        gc.collect()
    results = []
    by_key = {str(row.get("candidate_key")): row for row in candidates}
    for row in [*first.get("pieces", []), *second_rows]:
        piece = row.get("piece", {})
        candidate_key = str(piece.get("candidate_key") or _candidate_key(piece))
        objective = rank_alternatives_v2([row], perfil=context["profile"],
            capital_meta=capital)[0]["objetivo"]
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
    discarded = dict(built["discarded"])
    for reason in failed_candidates.values():
        label = reason.split(":", 1)[0]
        discarded[label] = discarded.get(label, 0) + 1
    evaluated_count = len(first_rows) + len(second_rows)
    return {"status": "ok" if evaluated_count else "failed", "results": results,
        "evaluated_count": evaluated_count, "discarded": discarded,
        "failed_candidates": failed_candidates, "skipped_symbols": skipped,
        "candidate_seconds": candidate_seconds,
        "generated_count": len(candidates) + int(built.get("invalid_count", 0)) + len(smart),
        "generated_candidates": len(candidates) + len(smart),
        "discarded_count": sum(discarded.values())}


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


def _evaluate_with_isolation(evaluator, pieces, **kwargs):
    """Aísla geometrías que hacen fallar FastSim sin perder las válidas."""
    if not pieces:
        return [], {}
    try:
        result = evaluator(pieces, **kwargs)
        return list(result.get("pieces", [])), {}
    except (ValueError, ArithmeticError) as exc:
        if len(pieces) == 1:
            piece = pieces[0]
            key = str(piece.get("candidate_key") or _candidate_key(piece))
            return [], {key: str(exc)[:240]}
        middle = len(pieces) // 2
        left, left_errors = _evaluate_with_isolation(evaluator, pieces[:middle], **kwargs)
        right, right_errors = _evaluate_with_isolation(evaluator, pieces[middle:], **kwargs)
        return left + right, {**left_errors, **right_errors}


def run_cache(app_db, market_db, *, library_dir="data/derived/scenario_lib",
        evaluator=evaluate_pieces, now=None, max_rss_mb=600, max_seconds=540):
    started = time.perf_counter()
    app_path = Path(app_db).resolve()
    market_path = market_db_path(market_db)
    store = CandleStore(market_path)
    as_of = (now or datetime.now(timezone.utc)).isoformat()
    filters, prices, sigmas, skipped = _read_market_inputs(store)
    contexts = _contexts(app_path, prices)
    with store._connect() as conn:
        conn.execute("UPDATE scenario_eval_runs SET status='abandonada',error=? "
            "WHERE status='running' AND julianday(as_of) < julianday('now', ?)",
            (json.dumps({"motivo": "running anterior a 30 minutos"}),
             f"-{ABANDON_AFTER_MINUTES} minutes"))
        conn.execute("DELETE FROM scenario_eval_results WHERE run_id IN "
            "(SELECT run_id FROM scenario_eval_runs WHERE as_of < datetime('now', ?))",
            (f"-{RETENTION_DAYS} days",))
        conn.execute("DELETE FROM scenario_eval_runs WHERE as_of < datetime('now', ?)",
            (f"-{RETENTION_DAYS} days",))
    watchdog_stop = threading.Event()
    main_thread_id = threading.get_ident()
    def watchdog():
        while not watchdog_stop.wait(2):
            try:
                _check_limits(started, max_rss_mb, max_seconds)
            except CacheLimitExceeded as exc:
                error_type = CacheMemoryExceeded if exc.status == "memoria" else CacheTimeoutExceeded
                import ctypes
                result = ctypes.pythonapi.PyThreadState_SetAsyncExc(
                    ctypes.c_ulong(main_thread_id), ctypes.py_object(error_type))
                if result > 1:
                    ctypes.pythonapi.PyThreadState_SetAsyncExc(
                        ctypes.c_ulong(main_thread_id), None)
                return
    watcher = threading.Thread(target=watchdog, name="scenario-cache-limits", daemon=True)
    watcher.start()
    results_summary = []
    for context in contexts:
        with store._connect() as conn:
            run_id = _insert_run(conn, context, as_of)
        context_started = time.perf_counter()
        progress_state = {}
        def check_context_limits():
            _check_limits(started, max_rss_mb, max_seconds)
        def persist_progress(rows):
            with store._connect() as conn:
                for row in rows:
                    piece = row.get("piece", {})
                    key = str(piece.get("candidate_key") or _candidate_key(piece))
                    ranked_row = rank_alternatives_v2([row], perfil=context["profile"],
                        capital_meta=float(context["capital"]))[0]
                    conn.execute("INSERT OR REPLACE INTO scenario_eval_results VALUES(?,?,?,?,?,?,?)",
                        (run_id, key, piece.get("symbol"), piece.get("strategy", "simple"),
                         json.dumps({k: v for k, v in piece.items()
                             if k not in {"symbol", "strategy", "candidate_key"}}, ensure_ascii=False),
                         json.dumps(row.get("metrics") or {}, ensure_ascii=False),
                         json.dumps(ranked_row.get("objetivo") or {}, ensure_ascii=False)))
        try:
            evaluated = _evaluate_context(context, store=store, app_db=app_path,
                market_path=market_path, library_dir=library_dir, filters=filters,
                prices=prices, sigmas=sigmas, skipped=skipped, evaluator=evaluator,
                check_limits=check_context_limits, on_progress=persist_progress,
                progress_state=progress_state)
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
                            "discarded": evaluated.get("discarded", {}),
                            "evaluated_count": evaluated.get("evaluated_count", 0),
                            "failed_candidates": evaluated.get("failed_candidates", {})},
                            ensure_ascii=False),
                        time.perf_counter() - context_started, _rss_max_mb(), run_id))
            results_summary.append({"run_id": run_id, "context_seconds":
                time.perf_counter() - context_started, **evaluated})
            print(f"SCENARIO_CACHE contexto={context['goal_id'] or context['profile']} "
                f"estado={evaluated['status']} generados={evaluated.get('generated_count', 0)} "
                f"evaluados={evaluated.get('evaluated_count', 0)} "
                f"descartados={json.dumps(evaluated.get('discarded', {}), ensure_ascii=False)} "
                f"segundos_moneda={json.dumps(evaluated.get('candidate_seconds', {}), ensure_ascii=False)} "
                f"segundos={time.perf_counter()-context_started:.3f} rss_max_mb={_rss_max_mb()}")
        except Exception as exc:
            status = exc.status if isinstance(exc, CacheLimitExceeded) else "failed"
            error_details = {"motivo": str(exc), **progress_state}
            error_details["discarded_count"] = sum(progress_state.get("discarded", {}).values())
            with store._connect() as conn:
                conn.execute("UPDATE scenario_eval_runs SET status=?,error=?,seconds=?,rss_mb=? "
                    "WHERE run_id=?", (status, json.dumps(error_details, ensure_ascii=False),
                        time.perf_counter() - started,
                        _rss_max_mb(), run_id))
            if isinstance(exc, CacheLimitExceeded):
                with store._connect() as conn:
                    count = conn.execute("SELECT COUNT(*) FROM scenario_eval_results WHERE run_id=?",
                        (run_id,)).fetchone()[0]
                results_summary.append({"run_id": run_id, "status": status,
                    **progress_state, "discarded_count": error_details["discarded_count"],
                    "evaluated_count": count})
                print(f"SCENARIO_CACHE contexto={context['goal_id'] or context['profile']} "
                    f"estado={status} generados={progress_state.get('generated_count', 0)} "
                    f"evaluados={count} descartados={json.dumps(progress_state.get('discarded', {}), ensure_ascii=False)} "
                    f"segundos_moneda={json.dumps(progress_state.get('candidate_seconds', {}), ensure_ascii=False)} "
                    f"segundos={time.perf_counter()-context_started:.3f} rss_max_mb={_rss_max_mb()}")
                break
            watchdog_stop.set()
            watcher.join(timeout=2)
            raise
    watchdog_stop.set()
    watcher.join(timeout=2)
    return {"contexts": len(contexts), "runs": results_summary,
        "seconds": time.perf_counter() - started, "rss_mb": _rss_max_mb()}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app-db", required=True)
    parser.add_argument("--market-db", required=True)
    parser.add_argument("--biblioteca", default="data/derived/scenario_lib")
    parser.add_argument("--max-rss-mb", type=float, default=600)
    parser.add_argument("--max-segundos", type=float, default=540)
    args = parser.parse_args(argv)
    if os.name == "posix":
        try:
            os.nice(10)
        except OSError:
            pass
    started = time.perf_counter()
    try:
        result = run_cache(args.app_db, args.market_db, library_dir=args.biblioteca,
            max_rss_mb=args.max_rss_mb, max_seconds=args.max_segundos)
        limit_status = next((row.get("status") for row in result["runs"]
            if row.get("status") in {"memoria", "timeout"}), None)
        if limit_status:
            print(f"SCENARIO_CACHE estado={limit_status} segundos={result['seconds']:.3f} "
                f"rss_max_mb={result['rss_mb']}")
            return 3 if limit_status == "memoria" else 4
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
