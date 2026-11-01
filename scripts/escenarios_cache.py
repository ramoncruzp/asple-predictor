#!/usr/bin/env python3
"""Actualiza fuera del servicio la caché horaria experimental de escenarios."""
from __future__ import annotations

import argparse
import gc
import json
import os
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
import math
import numpy as np
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from data.market_store import CandleStore, market_db_path
from grid.meta import DEFAULT_PROFILES
from grid.json_utils import json_dumps_safe, json_safe
from research.scenarios.candidates import build_candidates, build_smart_candidates
from research.scenarios.evaluate import (_sigma_estimates, evaluate_pieces,
    prepare_scenario_batches, scenario_source_date_plan)
from research.scenarios.metrics import summarize_outcomes
from research.scenarios.objective import objective_limits, rank_alternatives_v2

RETENTION_DAYS = 7
SCENARIO_COUNT = 1000
MAX_PIECES_PER_BATCH = 6
ABANDON_AFTER_MINUTES = 30


def _json_safe(value):
    """Normaliza valores no finitos y tipos numpy para JSON estricto."""
    return json_safe(value)


def _json_dumps(value, **kwargs):
    return json_dumps_safe(value, **kwargs)


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
            with open("/proc/self/status", encoding="ascii") as stream:
                resident_kb = next(float(line.split()[1]) for line in stream
                    if line.startswith("VmRSS:"))
            return resident_kb / 1024
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
    try:
        if os.name == "posix":
            with open("/proc/self/status", encoding="ascii") as stream:
                measured = next(float(line.split()[1]) / 1024 for line in stream
                    if line.startswith("VmHWM:"))
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
        return round(measured, 2)
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
                profile_config = {**defaults, **(settings.get(profile) or {}), **config}
                loss_pct = float(config.get("max_loss_pct", defaults["max_loss_pct"]))
                threshold = initial * (1 - loss_pct / 100)
                loss_headroom = max(0.0, current_equity - threshold)
                effective_loss_pct = min(loss_pct, 100 * loss_headroom / current_equity if current_equity > 0 else 0.0)
                contexts.append({"goal_id": int(goal["id"]), "profile": profile,
                    "decision_engine": str(config.get("decision_engine", "v1")).lower(),
                    "capital": current_equity, "capital_initial": initial, "free_cash": free_cash,
                    "target": _remaining_target(goal["target_usdt"], current_equity, initial),
                    "horizon": max(1, int(defaults["max_days"]) - elapsed),
                    "max_loss_pct": effective_loss_pct, "profile_config": profile_config,
                    "balance_approximate": not balance_known})
        global_settings = settings.get("GLOBAL") or {}
        active_count = len(contexts)
        max_active = int(global_settings.get("max_active", 1))
        drafts = conn.execute("SELECT profile,config_snapshot FROM meta_goals WHERE status='DRAFT'").fetchall() if "meta_goals" in table_names else []
        draft_v2_profiles = {str(row["profile"]).upper() for row in drafts
            if str(json.loads(row["config_snapshot"] or "{}").get("decision_engine", "v1")).lower() == "v2"}
        for profile, defaults in DEFAULT_PROFILES.items():
            profile_v2 = str((settings.get(profile) or {}).get("decision_engine", "v1")).lower() == "v2"
            if profile_v2 and (profile in draft_v2_profiles or active_count < max_active):
                contexts.append({"goal_id": None, "profile": profile, "capital": 100.0,
                    "capital_initial": 100.0,
                    "decision_engine": "v2",
                    "free_cash": 100.0, "target": 3.0, "horizon": int(defaults["max_days"]),
                    "max_loss_pct": float(defaults["max_loss_pct"]),
                    "profile_config": {**defaults, **(settings.get(profile) or {})},
                    "balance_approximate": False})
        return contexts


def _remaining_target(target, current_equity, initial):
    """Ajusta el objetivo por la ganancia o pérdida acumulada de la meta."""
    return max(0.0, float(target) - (float(current_equity) - float(initial)))

def _read_market_inputs(store, *, library_dir="data/derived/scenario_lib", horizon_days=0):
    with store._connect() as conn:
        rows = conn.execute(
            "SELECT symbol,role,active FROM symbols ORDER BY symbol").fetchall()
    filters, prices, sigmas, skipped = {}, {}, {}, {}
    for symbol, role, active in rows:
        if not active:
            skipped[symbol] = "símbolo inactivo"
            continue
        if role != "operar":
            skipped[symbol] = f"rol {role}; se requiere operar"
            continue
        try:
            if not (Path(library_dir) / f"{symbol}.npz").is_file():
                raise ValueError("biblioteca de escenarios ausente")
            data = store.load(symbol, "5m")
            if len(data.close) < 30 * 288 + int(horizon_days) * 288 + 1:
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


def _active_grid_pieces(app_db, goal_id, prices=None, filters=None):
    if goal_id is None:
        return []
    from grid.meta_live_pieces import is_live_meta_grid
    with sqlite3.connect(Path(app_db).resolve().as_uri() + "?mode=ro", uri=True) as conn:
        conn.row_factory = sqlite3.Row
        grids = conn.execute("SELECT g.id,g.symbol,g.strategy,g.capital_total,g.range_low,g.range_high,"
            "g.n_levels,g.status,l.piece_type FROM meta_piece_links l JOIN grids g ON g.id=l.grid_id "
            "WHERE l.meta_id=? AND l.released_at IS NULL "
            "ORDER BY g.id", (goal_id,)).fetchall()
        output = []
        for grid in grids:
            strategy = str(grid['strategy']).lower()
            if strategy not in {'simple', 'smart', 'bot'}:
                continue
            grid_id = int(grid['id'])
            symbol = str(grid['symbol']).upper()
            price = float((prices or {}).get(symbol, 0))
            levels = conn.execute("SELECT level_idx,price,sell_price,held_qty,state,capital "
                "FROM grid_levels WHERE grid_id=? ORDER BY level_idx", (grid_id,)).fetchall()
            if not is_live_meta_grid(grid['status'], levels):
                continue
            events = conn.execute("SELECT event_type,price,details FROM grid_events WHERE grid_id=? "
                "AND event_type IN ('BUY_FILLED','SELL_FILLED','CELL_LIQUIDATED') ORDER BY id",
                (grid_id,)).fetchall()
            delivered = returned = commissions = 0.0
            for event in events:
                details = json.loads(event['details'] or '{}')
                quote = details.get('cummulative_quote_qty', details.get('quote_qty',
                    details.get('executed_quote_qty', details.get('cash_proceeds_net'))))
                if quote is None and event['price'] is not None:
                    qty_event = details.get('executed_qty', details.get('qty', details.get('quantity')))
                    quote = float(qty_event or 0) * float(event['price'])
                if quote is None:
                    continue
                quote = float(quote)
                fee_value = details.get('commission_usdt', details.get('fee_usdt'))
                commissions += float(fee_value if fee_value is not None else quote * .001)
                if event['event_type'] == 'BUY_FILLED':
                    delivered += quote
                else:
                    returned += quote
            held_qty = sum(float(level['held_qty'] or 0) for level in levels)
            buy_open_capital = sum(float(level['capital'] or 0) for level in levels
                if str(level['state']).upper() == 'BUY_OPEN')
            piece_cash = max(0.0, float(grid['capital_total']) - delivered + returned - commissions - buy_open_capital)
            inventory_value = max(0.0, held_qty * price * .999)
            cell_rows = []
            symbol_filters = (filters or {}).get(symbol)
            for level in levels:
                buy_price = float(level['price'])
                qty = 0.0
                if symbol_filters is not None:
                    qty = float(symbol_filters.round_qty_down(Decimal(str(float(level['capital'] or 0))) /
                        Decimal(str(buy_price))))
                state = str(level['state']).upper()
                if float(level['held_qty'] or 0) > 0 and state != 'SELL_OPEN':
                    state = 'SELL_OPEN'
                cell_rows.append({'buy_price': buy_price,
                    'sell_price': float(level['sell_price'] or buy_price), 'qty': qty,
                    'state': state, 'held_qty': float(level['held_qty'] or 0),
                    'reserved_cash': float(level['capital'] or 0) * 1.001 if state == 'BUY_OPEN' else 0,
                    'active_from': 0})
            status = str(grid['status']).upper()
            piece = {'symbol': symbol, 'strategy': strategy,
                'capital': piece_cash + inventory_value, 'capital_nominal': float(grid['capital_total']),
                'cash_usdt': piece_cash, 'inventory_value_executable': inventory_value,
                'liquidation_value': inventory_value + buy_open_capital,
                'low': float(grid['range_low']), 'high': float(grid['range_high']),
                'initial_cells': {'cells': cell_rows, 'cash_usdt': piece_cash,
                    'wait_outside_range': status == 'PAUSED'},
                'n': int(grid['n_levels']), 'grid_id': grid_id, 'grid_status': status,
                'piece_type': str(grid['piece_type']), 'is_current': True,
                'candidate_key': f"mantener:{goal_id}:{grid_id}:{symbol}:{grid['n_levels']}:{grid['range_low']}:{grid['range_high']}",
                'valuation_warning': ('mejor bid no disponible en la cach\u00e9 offline; se usa el cierre '
                    'm\u00e1s reciente como aproximaci\u00f3n' if held_qty > 0 else None)}
            if strategy == 'smart' and status == 'PAUSED':
                piece['simulation_approximation'] = (
                    'Smart pausado: FastSim desde celdas e inventario actuales; '
                    'espera fuera del rango y no modela pausas por sigma')
            output.append(piece)
    return output

def _insert_run(conn, context, as_of, n_scenarios=SCENARIO_COUNT):
    run_id = str(uuid.uuid4())
    conn.execute("INSERT INTO scenario_eval_runs(run_id,as_of,goal_id,profile,horizon_days,"
        "n_scenarios,seconds,rss_mb,rss_current_mb,rss_hwm_mb,status,error) "
        "VALUES(?,?,?,?,?,?,0,NULL,NULL,NULL,'running',NULL)",
        (run_id, as_of, context["goal_id"], context["profile"], context["horizon"], int(n_scenarios)))
    return run_id


def _fit_live_piece_capital(current_pieces, *, capital, free_cash):
    """Evita doble conteo de capital nominal y limita mantener al valor liquidable."""
    nominal = sum(float(piece.get("capital_nominal", piece["capital"]))
        for piece in current_pieces)
    current_value = sum(float(piece["capital"]) for piece in current_pieces)
    if max(nominal, current_value) <= float(capital) + 1e-6:
        return None
    liquidation_total = sum(float(piece.get("liquidation_value", 0))
        for piece in current_pieces)
    budget = min(max(0.0, float(capital) - float(free_cash)), liquidation_total)
    factor = budget / liquidation_total if liquidation_total > 0 else 0.0
    for piece in current_pieces:
        piece.setdefault("capital_nominal", float(piece["capital"]))
        piece["capital"] = float(piece.get("liquidation_value", 0)) * factor
        initial_cells = piece.get("initial_cells")
        if isinstance(initial_cells, dict):
            released_reserves = sum(float(cell.get("reserved_cash", 0)) / 1.001
                for cell in initial_cells.get("cells", [])
                if str(cell.get("state", "")).upper() == "BUY_OPEN") * factor
            piece["cash_usdt"] = released_reserves
            initial_cells["cash_usdt"] = released_reserves
            for cell in initial_cells.get("cells", []):
                if str(cell.get("state", "")).upper() == "BUY_OPEN":
                    cell.update({"state": "IDLE", "reserved_cash": 0.0})
                else:
                    cell["held_qty"] = float(cell.get("held_qty", 0)) * factor
                    cell["qty"] = float(cell.get("qty", 0)) * factor
        else:
            piece["cash_usdt"] = 0.0
    return {"motivo": ("capital nominal vivo superior al capital asignable de la meta; "
            "se usa el valor de liquidación" if liquidation_total > 0 else
            "capital nominal vivo superior al capital asignable de la meta; "
            "sin inventario liquidable ni compras abiertas"),
        "capital_nominal_usdt": nominal,
        "capital_liquidacion_usdt": liquidation_total,
        "capital_aplicado_usdt": budget}


def _evaluate_context(context, *, store, app_db, market_path, library_dir,
        filters, prices, sigmas, skipped, evaluator, check_limits=None,
        on_progress=None, progress_state=None, n_scenarios=SCENARIO_COUNT,
        symbol_filter=None, perfilar=False, remaining_seconds=None,
        trajectory_path=None):
    progress_state = {} if progress_state is None else progress_state
    if context.get("balance_approximate"):
        return {"status": "skipped", "reason": "efectivo o inventario sin atribución completa"}
    if not filters:
        return {"status": "skipped", "reason": "ningún símbolo con filtros, velas e historia"}
    free = float(context["free_cash"])
    capital = float(context["capital"])
    current_pieces = _active_grid_pieces(app_db, context["goal_id"], prices, filters)
    live_capital_warning = _fit_live_piece_capital(current_pieces,
        capital=capital, free_cash=free)
    profile_config = {**DEFAULT_PROFILES[str(context["profile"]).upper()],
        **(context.get("profile_config") or {})}
    capital_initial = float(context.get("capital_initial") or capital)
    coin_limit = capital_initial * float(profile_config.get("capital_per_coin_pct", 0)) / 100
    live_capital_by_symbol = {}
    for piece in current_pieces:
        symbol = str(piece.get("symbol") or "").upper()
        live_capital_by_symbol[symbol] = live_capital_by_symbol.get(symbol, 0.0) + float(
            piece.get("capital_nominal", piece.get("capital")) or 0)
    capital_max_by_symbol = {symbol: max(0.0, coin_limit - live_capital_by_symbol.get(symbol, 0.0))
        for symbol in filters}
    current_grids = sum(piece.get("piece_type") != "individual_bot_grid" for piece in current_pieces)
    current_bots = sum(piece.get("piece_type") == "individual_bot_grid" for piece in current_pieces)
    built = build_candidates(symbols=filters, capital_libre=free,
        perfil=context["profile"], filters_by_symbol=filters,
        price_by_symbol=prices, sigma_by_symbol=sigmas,
        horizon_days=context["horizon"], capital_max_by_symbol=capital_max_by_symbol,
        max_grids=profile_config.get("max_grids"), max_bots=profile_config.get("max_bots"),
        current_grids=current_grids, current_bots=current_bots)
    live_piece_details = [{"grid_id": int(piece["grid_id"]),
        "status": piece["grid_status"]} for piece in current_pieces]
    candidates = current_pieces + built["candidates"][:-1]
    if symbol_filter:
        candidates = [piece for piece in candidates
            if str(piece.get("symbol", "")).upper() == str(symbol_filter).upper()]
    symbol_stats = {}
    for symbol in filters:
        symbol_stats[symbol] = {"symbol": symbol, "price": prices.get(symbol),
            "sigma_24h": sigmas.get(symbol),
            "generated_count": (sum(str(piece.get("symbol", "")).upper() == symbol
                for piece in candidates) + sum(built.get("discarded_by_symbol", {}).get(
                    symbol, {}).values())), "evaluated_count": 0,
            "discarded": dict(built.get("discarded_by_symbol", {}).get(symbol, {})),
            "skipped_reason": skipped.get(symbol)}
    progress_state.update({"generated_count": len(candidates) + int(built.get("invalid_count", 0)),
        "generated_candidates": len(candidates), "evaluated_count": 0,
        "discarded": dict(built["discarded"]), "candidate_seconds": {},
        "live_piece_count": len(live_piece_details), "live_pieces": live_piece_details,
        "live_capital_warning": live_capital_warning, "symbol_stats": symbol_stats})
    pieces = [dict(row) for row in candidates]
    eval_kwargs = dict(perfil=context["profile"], capital=capital,
        meta=context["target"], horizon_days=context["horizon"],
        max_loss_pct=context["max_loss_pct"], n_scenarios=n_scenarios, n_smart=200,
        seed=43, db_path=market_path, library_dir=library_dir, mode="alternativas")
    profile_times = {"carga_biblioteca_s": 0.0, "generacion_escenarios_s": 0.0,
        "simulacion_fastsim_s": 0.0, "simulacion_smart_s": 0.0,
        "metricas_bootstrap_s": 0.0,
        "objetivo_s": 0.0, "escritura_bd_s": 0.0,
        "lotes_fastsim": 0, "filas_por_lote": [], "segundos_por_lote": []}
    first_rows, failed_candidates = [], {}
    candidate_seconds = {}
    retained_live, retained_candidates = {}, []

    def retain_trajectory(row):
        piece = row.get("piece") or {}
        outcomes = row.get("scenario_outcomes") or {}
        cash = outcomes.get("trajectory_cash_f32")
        equity = outcomes.get("trajectory_equity_f32")
        low_equity_5m = outcomes.get("trajectory_low_equity_5m_f32")
        timestamps_5m = outcomes.get("trajectory_timestamps_5m")
        timestamps = outcomes.get("trajectory_timestamps")
        if cash is None or equity is None or timestamps is None:
            return
        entry = {"candidate_key": str(piece.get("candidate_key") or _candidate_key(piece)),
            "piece": dict(piece), "metrics": dict(row.get("metrics") or {}),
            "cash": np.asarray(cash, dtype=np.float32).copy(),
            "equity": np.asarray(equity, dtype=np.float32).copy(),
            "inventory_qty": np.asarray(outcomes.get("inventory_qty", []), dtype=np.float32).copy(),
            "inventory_value": np.asarray(outcomes.get("inventory_value", []), dtype=np.float32).copy(),
            "timestamps": np.asarray(timestamps, dtype=np.int64).copy(),
            "low_equity_5m": (np.asarray(low_equity_5m, dtype=np.float32)
                if low_equity_5m is not None else None),
            "timestamps_5m": (np.asarray(timestamps_5m, dtype=np.int64).copy()
                if timestamps_5m is not None else None),
            "is_current": bool(piece.get("is_current"))}
        if entry["is_current"]:
            retained_live[entry["candidate_key"]] = entry
            return
        ranked_row = rank_alternatives_v2([row], perfil=context["profile"],
            capital_meta=capital)[0]
        entry["order_key"] = tuple(ranked_row["objetivo"]["clave_orden"])
        if any(old["candidate_key"] == entry["candidate_key"] for old in retained_candidates):
            retained_candidates[:] = [old for old in retained_candidates
                if old["candidate_key"] != entry["candidate_key"]]
        retained_candidates.append(entry)
        retained_candidates.sort(key=lambda item: item["order_key"])
        del retained_candidates[12:]

    def write_trajectory_archive():
        if trajectory_path is None:
            return None
        selected_rows = list(retained_live.values()) + retained_candidates
        if not selected_rows:
            return None
        timestamps = selected_rows[0]["timestamps"]
        selected_rows = [row for row in selected_rows if np.array_equal(
            row["timestamps"], timestamps)]
        if not selected_rows:
            return None
        archive = {"timestamps": timestamps.astype(np.int64, copy=False),
            "timestamps_5m": (selected_rows[0]["timestamps_5m"]
                if selected_rows[0].get("timestamps_5m") is not None else np.asarray([], dtype=np.int64)),
            "metadata_json": np.asarray(_json_dumps([{
                "candidate_key": row["candidate_key"], "piece": row["piece"],
                "metrics": row["metrics"], "is_current": row["is_current"]}
                for row in selected_rows], ensure_ascii=False))}
        for index, row in enumerate(selected_rows):
            archive[f"cash_{index}"] = row["cash"]
            archive[f"equity_{index}"] = row["equity"]
            archive[f"inventory_qty_{index}"] = row["inventory_qty"]
            archive[f"inventory_value_{index}"] = row["inventory_value"]
            if row.get("low_equity_5m") is not None:
                archive[f"low_equity_5m_{index}"] = row["low_equity_5m"]
        output_path = Path(trajectory_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = output_path.with_name(f"{output_path.stem}.tmp.npz")
        np.savez_compressed(temporary_path, **archive)
        os.replace(temporary_path, output_path)
        return str(output_path)
    live_symbols = {str(piece["symbol"]).upper() for piece in current_pieces}
    with store._connect() as conn:
        latest = conn.execute("SELECT MAX(open_ts) FROM candles WHERE interval='5m'").fetchone()[0]
        liquidity_rows = conn.execute("SELECT s.symbol,SUM(c.quote_volume) FROM candles c "
            "JOIN symbols s ON s.id=c.symbol_id WHERE c.interval='5m' AND c.open_ts>=? "
            "GROUP BY s.symbol", (int(latest or 0) - 24 * 3600,)).fetchall()
    liquidity = {str(row[0]).upper(): float(row[1] or 0) for row in liquidity_rows}
    symbol_order = sorted({str(piece["symbol"]).upper() for piece in pieces},
        key=lambda symbol: (symbol not in live_symbols, -liquidity.get(symbol, 0), symbol))
    scenario_dates = (scenario_source_date_plan(symbol_order,
        horizon_days=context["horizon"], n_scenarios=n_scenarios, seed=43,
        library_dir=library_dir) if evaluator is evaluate_pieces and symbol_order else None)
    current_keys = {str(piece["candidate_key"]) for piece in current_pieces}
    phases = (
        [piece for piece in pieces if str(piece.get("candidate_key")) in current_keys],
        [piece for piece in pieces if str(piece.get("candidate_key")) not in current_keys],
    )
    baseline_failed = False
    for phase_index, phase_pieces in enumerate(phases):
        if phase_index == 1 and baseline_failed:
            for piece in phase_pieces:
                failed_candidates[str(piece.get("candidate_key") or _candidate_key(piece))] = (
                    "sin_baseline: falló mantener; alternativa no evaluada")
            continue
        phase_symbols = [symbol for symbol in symbol_order if any(
            str(piece["symbol"]).upper() == symbol for piece in phase_pieces)]
        for symbol in phase_symbols:
            symbol_pieces = [piece for piece in phase_pieces
                if str(piece["symbol"]).upper() == symbol]
            symbol_started = time.perf_counter()
            generation_timings = {}
            batches = (prepare_scenario_batches(symbol, horizon_days=context["horizon"],
                n_scenarios=n_scenarios, seed=43, db_path=market_path,
                library_dir=library_dir, timings=generation_timings,
                scenario_dates=scenario_dates) if evaluator is evaluate_pieces else None)
            profile_times["carga_biblioteca_s"] += generation_timings.get("library_load_s", 0.0)
            profile_times["generacion_escenarios_s"] += generation_timings.get("generation_s", 0.0)
            evaluator_timings = {}
            kwargs = dict(eval_kwargs)
            if batches is not None:
                kwargs["scenario_batches"] = batches
                kwargs["keep_curves"] = False
                kwargs["keep_5m"] = True
                kwargs["timings"] = evaluator_timings
            rows, errors = _evaluate_with_isolation(evaluator, symbol_pieces, **kwargs)
            if phase_index == 1 and not baseline_failed:
                baseline_rows = [row for row in first_rows
                    if (row.get("piece") or {}).get("is_current")]
                if baseline_rows:
                    _apply_live_portfolio_metrics(rows, baseline_rows,
                        capital=capital, target=context["target"],
                        max_loss_pct=context["max_loss_pct"], horizon=context["horizon"],
                        trajectory_sink=retain_trajectory,
                        loss_confirm_minutes=(context.get("profile_config") or {}).get(
                            "loss_confirm_minutes", 2))
            first_rows.extend(rows)
            failed_candidates.update(errors)
            if phase_index == 0 and errors:
                baseline_failed = True
            progress_state["evaluated_count"] += len(rows)
            symbol_stats[symbol]["evaluated_count"] += len(rows)
            for reason in errors.values():
                label = reason.split(":", 1)[0]
                progress_state["discarded"][label] = progress_state["discarded"].get(label, 0) + 1
                symbol_stats[symbol]["discarded"][label] = symbol_stats[symbol]["discarded"].get(label, 0) + 1
            if on_progress and rows:
                on_progress(rows)
            del rows, errors, batches, symbol_pieces
            gc.collect()
            if check_limits:
                check_limits()
            candidate_seconds[symbol] = round(candidate_seconds.get(symbol, 0.0) +
                time.perf_counter() - symbol_started, 3)
            progress_state["candidate_seconds"] = dict(candidate_seconds)
            profile_times["simulacion_fastsim_s"] += evaluator_timings.get("fastsim_s", 0.0)
            profile_times["metricas_bootstrap_s"] += evaluator_timings.get("metrics_s", 0.0)
            profile_times["lotes_fastsim"] += evaluator_timings.get("batch_count", 0)
            profile_times["filas_por_lote"].extend(evaluator_timings.get("batch_rows", []))
            profile_times["segundos_por_lote"].extend(evaluator_timings.get("batch_seconds", []))
    first = {"pieces": first_rows, "skipped_symbols": skipped}
    objective_started = time.perf_counter()
    ranked = rank_alternatives_v2(first["pieces"], perfil=context["profile"],
        capital_meta=capital) if first["pieces"] else []
    profile_times["objetivo_s"] += time.perf_counter() - objective_started
    simple_ranked = [row for row in ranked
        if row.get("piece", {}).get("strategy") == "simple"
        and not row.get("piece", {}).get("is_current")][:2]
    smart = build_smart_candidates(simple_ranked)
    smart_generated = len(smart)
    if smart and remaining_seconds is not None and remaining_seconds() < 120:
        progress_state["smart_omitido_por_tiempo"] = len(smart)
        smart = []
    progress_state["generated_count"] += smart_generated
    progress_state["generated_candidates"] += smart_generated
    second_rows = []
    for symbol in sorted({str(piece["symbol"]).upper() for piece in smart}):
        smart_started = time.perf_counter()
        symbol_pieces = [piece for piece in smart if str(piece["symbol"]).upper() == symbol]
        smart_generation_timings = {}
        batches = (prepare_scenario_batches(symbol, horizon_days=context["horizon"],
            n_scenarios=200, seed=43, db_path=market_path,
            library_dir=library_dir, timings=smart_generation_timings,
            scenario_dates=scenario_dates[:200] if scenario_dates is not None else None)
            if evaluator is evaluate_pieces else None)
        profile_times["carga_biblioteca_s"] += smart_generation_timings.get("library_load_s", 0.0)
        profile_times["generacion_escenarios_s"] += smart_generation_timings.get("generation_s", 0.0)
        smart_timings = {}
        for start in range(0, len(symbol_pieces), MAX_PIECES_PER_BATCH):
            kwargs = dict(perfil=context["profile"], capital=capital, meta=context["target"],
                horizon_days=context["horizon"], max_loss_pct=context["max_loss_pct"],
                n_scenarios=200, n_smart=200, workers=2, seed=43, db_path=market_path,
                library_dir=library_dir, mode="alternativas")
            if batches is not None:
                kwargs["scenario_batches"] = batches
                kwargs["keep_curves"] = False
                kwargs["keep_5m"] = True
                kwargs["timings"] = smart_timings
            smart_eval_started = time.perf_counter()
            metrics_before = smart_timings.get("metrics_s", 0.0)
            rows, errors = _evaluate_with_isolation(evaluator,
                symbol_pieces[start:start + MAX_PIECES_PER_BATCH], **kwargs)
            profile_times["simulacion_smart_s"] += max(0.0,
                time.perf_counter() - smart_eval_started -
                (smart_timings.get("metrics_s", 0.0) - metrics_before))
            second_rows.extend(rows)
            failed_candidates.update(errors)
            progress_state["evaluated_count"] += len(rows)
            symbol_stats[symbol]["generated_count"] += len(symbol_pieces[start:start + MAX_PIECES_PER_BATCH])
            symbol_stats[symbol]["evaluated_count"] += len(rows)
            for reason in errors.values():
                label = reason.split(":", 1)[0]
                progress_state["discarded"][label] = progress_state["discarded"].get(label, 0) + 1
                symbol_stats[symbol]["discarded"][label] = symbol_stats[symbol]["discarded"].get(label, 0) + 1
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
        profile_times["metricas_bootstrap_s"] += smart_timings.get("metrics_s", 0.0)
    live_rows = [row for row in first_rows
        if (row.get("piece") or {}).get("is_current")]
    if not baseline_failed:
        if live_rows:
            _apply_live_portfolio_metrics(live_rows, live_rows,
            capital=capital, target=context["target"],
            max_loss_pct=context["max_loss_pct"], horizon=context["horizon"],
            trajectory_sink=retain_trajectory,
            loss_confirm_minutes=(context.get("profile_config") or {}).get(
                "loss_confirm_minutes", 2))
            _apply_live_portfolio_metrics(second_rows, live_rows,
            capital=capital, target=context["target"],
            max_loss_pct=context["max_loss_pct"], horizon=context["horizon"],
            trajectory_sink=retain_trajectory,
            loss_confirm_minutes=(context.get("profile_config") or {}).get(
                "loss_confirm_minutes", 2))
        else:
            for row in [*first_rows, *second_rows]:
                row["metrics_pieza_sola"] = row.get("metrics")
                combined = _portfolio_outcome_metrics([row], capital=capital,
                    target=context["target"], max_loss_pct=context["max_loss_pct"],
                    horizon=context["horizon"], loss_confirm_minutes=(
                        context.get("profile_config") or {}).get("loss_confirm_minutes", 2))
                if combined is not None:
                    row["metrics"] = combined
                retain_trajectory(row)
                outcomes = row.get("scenario_outcomes") or {}
                for key in ("trajectory_cash_f32", "trajectory_equity_f32",
                        "trajectory_timestamps", "trajectory_low_equity_5m_f32",
                        "trajectory_timestamps_5m"):
                    outcomes.pop(key, None)
    else:
        for row in second_rows:
            retain_trajectory(row)
    trajectory_file = write_trajectory_archive()
    if on_progress and live_rows:
        on_progress([*first_rows, *second_rows])
    results = []
    by_key = {str(row.get("candidate_key")): row for row in candidates}
    evaluated_keys = {str((row.get("piece") or {}).get("candidate_key"))
        for row in [*first.get("pieces", []), *second_rows]}
    output_rows = [*first.get("pieces", []), *second_rows]
    if baseline_failed:
        for candidate in candidates:
            key = str(candidate.get("candidate_key") or _candidate_key(candidate))
            if candidate.get("is_current") and key not in evaluated_keys:
                output_rows.append({"piece": candidate, "error": failed_candidates.get(key,
                    "mantener_fallido: no se pudo evaluar la pieza viva")})
            elif not candidate.get("is_current") and key not in evaluated_keys:
                output_rows.append({"piece": candidate, "sin_baseline": True})
    for row in output_rows:
        piece = row.get("piece", {})
        candidate_key = str(piece.get("candidate_key") or _candidate_key(piece))
        objective_started = time.perf_counter()
        objective = rank_alternatives_v2([row], perfil=context["profile"],
            capital_meta=capital)[0]["objetivo"]
        if row.get("sin_baseline"):
            objective = {**objective, "factible": False,
                "motivos": [*objective.get("motivos", []), "sin_baseline"]}
        profile_times["objetivo_s"] += time.perf_counter() - objective_started
        metrics = row.get("metrics") or _scenario_metrics(row)
        if row.get("sin_baseline"):
            metrics = {**metrics, "sin_baseline": True}
        if row.get("error"):
            metrics = {**metrics, "mantener_fallido": row["error"]}
        if row.get("metrics_pieza_sola") is not None:
            metrics = {**metrics, "metrics_pieza_sola": row["metrics_pieza_sola"]}
        results.append({"candidate_key": candidate_key, "symbol": piece.get("symbol"),
            "strategy": piece.get("strategy", "simple"),
            "params": {key: value for key, value in piece.items()
                if key not in {"symbol", "strategy", "candidate_key"}},
            "metrics": metrics,
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
        "live_piece_count": len(live_piece_details), "live_pieces": live_piece_details,
        "live_capital_warning": live_capital_warning,
        "failed_candidates": failed_candidates, "skipped_symbols": skipped,
        "symbol_stats": symbol_stats,
        "trajectory_file": trajectory_file,
        "candidate_seconds": candidate_seconds,
        "generated_count": len(candidates) + int(built.get("invalid_count", 0)) + smart_generated,
        "generated_candidates": len(candidates) + smart_generated,
        "discarded_count": sum(discarded.values()), "profile_times": profile_times,
        "smart_omitido_por_tiempo": progress_state.get("smart_omitido_por_tiempo", 0)}


def _candidate_key(piece):
    import hashlib
    raw = _json_dumps(piece, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def _scenario_metrics(row):
    scenario_results = row.get("scenario_results") or []
    pnls = [float(item["pnl_usdt"]) for item in scenario_results]
    return {"n": len(pnls), "pnl_mean_usdt": sum(pnls) / len(pnls) if pnls else 0,
        "p_target": sum(bool(item["reached"]) for item in scenario_results) / len(pnls) if pnls else 0,
        "p_loss": sum(bool(item["loss_cut"]) for item in scenario_results) / len(pnls) if pnls else 0,
        "p_loss_ci95": [0, 1] if pnls else [None, None], "cvar_5_usdt": 0,
        "cut_slippage_mean_usdt": 0, "cut_slippage_p90_usdt": 0}


def _portfolio_outcome_metrics(rows, *, capital, target, max_loss_pct, horizon,
        loss_confirm_minutes=2):
    """Combina trayectorias pareadas y aplica corte en velas de cinco minutos."""
    usable = [row.get("scenario_outcomes") or {} for row in rows]
    if not usable or any(item.get("trajectory_equity_f32") is None for item in usable):
        return None
    count = min(len(item["trajectory_equity_f32"]) for item in usable)
    timestamps = np.asarray(usable[0]["trajectory_timestamps"], dtype=np.int64)
    if any(not np.array_equal(timestamps,
            np.asarray(item["trajectory_timestamps"], dtype=np.int64)) for item in usable[1:]):
        return None
    used = sum(float((row.get("piece") or {}).get("capital", 0)) for row in rows)
    idle = float(capital) - used
    cash = np.full((count, len(timestamps)), idle, dtype=np.float64)
    equity = cash.copy()
    for item in usable:
        cash += np.asarray(item["trajectory_cash_f32"][:count], dtype=np.float64)
        equity += np.asarray(item["trajectory_equity_f32"][:count], dtype=np.float64)
    low_items = [item.get("trajectory_low_equity_5m_f32") for item in usable]
    has_low_path = all(value is not None for value in low_items)
    if has_low_path:
        timestamps_5m = np.asarray(usable[0].get("trajectory_timestamps_5m"), dtype=np.int64)
        has_low_path = bool(len(timestamps_5m)) and all(np.array_equal(
            timestamps_5m, np.asarray(item.get("trajectory_timestamps_5m"), dtype=np.int64))
            for item in usable[1:])
    if has_low_path:
        equity_low = np.full((count, len(timestamps_5m)), idle, dtype=np.float64)
        for values in low_items:
            equity_low += np.asarray(values[:count], dtype=np.float64)
    hit_mask = cash >= float(capital) + float(target)
    hit_exists = hit_mask.any(axis=1)
    hit = np.where(hit_exists, hit_mask.argmax(axis=1), len(timestamps))
    if max_loss_pct is not None and float(max_loss_pct) > 0:
        if has_low_path:
            breach = equity_low <= float(capital) * (1 - float(max_loss_pct) / 100)
            confirmations = max(1, int(np.ceil(max(0, int(loss_confirm_minutes)) / 5)))
            if confirmations > 1:
                kernel = np.ones(confirmations, dtype=np.int8)
                breach_count = np.apply_along_axis(lambda row: np.convolve(
                    row.astype(np.int8), kernel, mode="valid"), 1, breach)
                found = breach_count >= confirmations
                loss_exists = found.any(axis=1)
                loss = np.where(loss_exists, found.argmax(axis=1) + confirmations - 1,
                    len(timestamps_5m))
            else:
                loss_exists = breach.any(axis=1)
                loss = np.where(loss_exists, breach.argmax(axis=1), len(timestamps_5m))
            cut_times = np.where(loss_exists, timestamps_5m[np.minimum(loss,
                len(timestamps_5m) - 1)], np.iinfo(np.int64).max)
            hourly_hit_time = timestamps[np.minimum(hit, len(timestamps) - 1)]
            cut = loss_exists & ((~hit_exists) | (cut_times <= hourly_hit_time))
        else:
            loss_mask = equity <= float(capital) * (1 - float(max_loss_pct) / 100)
            loss_exists = loss_mask.any(axis=1)
            loss = np.where(loss_exists, loss_mask.argmax(axis=1), len(timestamps))
            cut = loss_exists & ((~hit_exists) | (loss <= hit))
    else:
        loss_exists = np.zeros(count, dtype=bool)
        loss = np.full(count, len(timestamps), dtype=int)
        cut = np.zeros(count, dtype=bool)
    end_index = np.full(count, len(timestamps) - 1, dtype=int)
    if not has_low_path:
        end_index = np.where(cut, loss, end_index)
    if has_low_path and max_loss_pct is not None and float(max_loss_pct) > 0:
        cut_value = equity_low[np.arange(count), np.minimum(loss, len(timestamps_5m) - 1)]
        pnl = np.where(cut, cut_value, equity[np.arange(count), end_index]) - float(capital)
    else:
        pnl = equity[np.arange(count), end_index] - float(capital)
    reached = hit_exists & ~cut
    days = np.where(reached, (timestamps[np.minimum(hit, len(timestamps)-1)] - timestamps[0]) / 86400, np.nan)
    inventory_qty = sum((np.asarray(item.get("inventory_qty", [0] * count)[:count],
        dtype=np.float64) for item in usable), np.zeros(count, dtype=np.float64))
    inventory_value = sum((np.asarray(item.get("inventory_value", [0] * count)[:count],
        dtype=np.float64) for item in usable), np.zeros(count, dtype=np.float64))
    outcomes = {"pnl_usdt": pnl, "loss_cut": cut, "reached": reached,
        "days": days, "capital_unused_usdt": max(0.0, float(capital) -
            used)}
    return summarize_outcomes(outcomes, capital_initial=capital,
        max_loss_pct=max_loss_pct, horizon_days=horizon,
        inventory_qty=inventory_qty, inventory_value=inventory_value)


def _apply_live_portfolio_metrics(rows, live_rows, *, capital, target,
        max_loss_pct, horizon, trajectory_sink=None, loss_confirm_minutes=2):
    """Asigna el baseline vivo y las alternativas sobre los mismos índices."""
    if not live_rows:
        return
    baseline = _portfolio_outcome_metrics(live_rows, capital=capital,
        target=target, max_loss_pct=max_loss_pct, horizon=horizon,
        loss_confirm_minutes=loss_confirm_minutes)
    if baseline is None:
        return
    for row in rows:
        piece = row.get("piece") or {}
        if piece.get("is_current"):
            row["metrics_pieza_sola"] = row.get("metrics")
            row["metrics"] = baseline
            if trajectory_sink is not None:
                trajectory_sink(row)
            continue
        combined = _portfolio_outcome_metrics([*live_rows, row], capital=capital,
            target=target, max_loss_pct=max_loss_pct, horizon=horizon,
            loss_confirm_minutes=loss_confirm_minutes)
        if combined is not None:
            row["metrics_pieza_sola"] = row.get("metrics")
            row["metrics"] = combined
        if trajectory_sink is not None:
            trajectory_sink(row)
        outcomes = row.get("scenario_outcomes") or {}
        outcomes.pop("trajectory_cash_f32", None)
        outcomes.pop("trajectory_equity_f32", None)
        outcomes.pop("trajectory_timestamps", None)
        outcomes.pop("trajectory_low_equity_5m_f32", None)
        outcomes.pop("trajectory_timestamps_5m", None)


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


def _context_priority(context):
    if context.get("goal_id") is None:
        return (2, int(context.get("goal_id") or 0))
    return (0 if context.get("decision_engine", "v1") == "v2" else 1,
        int(context.get("goal_id") or 0))


def run_cache(app_db, market_db, *, library_dir="data/derived/scenario_lib",
        evaluator=evaluate_pieces, now=None, max_rss_mb=600, max_seconds=540,
        max_total_seconds=1500,
        n_scenarios=SCENARIO_COUNT, symbol_filter=None, perfilar=False,
        context_selector=None):
    started = time.perf_counter()
    app_path = Path(app_db).resolve()
    market_path = market_db_path(market_db)
    store = CandleStore(market_path)
    as_of = (now or datetime.now(timezone.utc)).isoformat()
    prior_contexts = _contexts(app_path, {})
    filters, prices, sigmas, skipped = _read_market_inputs(store,
        library_dir=library_dir, horizon_days=max((int(row["horizon"]) for row in
            prior_contexts), default=20))
    contexts = sorted(_contexts(app_path, prices), key=_context_priority)
    if context_selector is not None:
        if str(context_selector).startswith("perfil:"):
            selected_profile = str(context_selector).split(":", 1)[1].upper()
            contexts = [row for row in contexts if row.get("goal_id") is None and
                row["profile"] == selected_profile]
        else:
            selected_id = int(context_selector)
            contexts = [row for row in contexts if row.get("goal_id") == selected_id]
    with store._connect() as conn:
        conn.execute("UPDATE scenario_eval_runs SET status='abandonada',error=? "
            "WHERE status='running' AND julianday(as_of) < julianday('now', ?)",
            (_json_dumps({"motivo": "running anterior a 30 minutos"}),
             f"-{ABANDON_AFTER_MINUTES} minutes"))
        conn.execute("DELETE FROM scenario_eval_results WHERE run_id IN "
            "(SELECT run_id FROM scenario_eval_runs WHERE as_of < datetime('now', ?))",
            (f"-{RETENTION_DAYS} days",))
        conn.execute("DELETE FROM scenario_eval_runs WHERE as_of < datetime('now', ?)",
            (f"-{RETENTION_DAYS} days",))
    trajectory_directory = Path("data/derived/scenario_cache")
    if trajectory_directory.is_dir():
        expiration = time.time() - RETENTION_DAYS * 86400
        for archive_path in trajectory_directory.glob("*.npz"):
            try:
                if archive_path.stat().st_mtime < expiration:
                    archive_path.unlink()
            except OSError:
                pass
    watchdog_stop = threading.Event()
    main_thread_id = threading.get_ident()
    active_context_started = [None]
    def watchdog():
        interval = min(2.0, max(.05, float(max_seconds) / 10)) if max_seconds else 2.0
        while not watchdog_stop.wait(interval):
            try:
                _check_limits(started, max_rss_mb, max_total_seconds)
                if active_context_started[0] is not None:
                    _check_limits(active_context_started[0], max_rss_mb, max_seconds)
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
    skipped_contexts = []
    for context in contexts:
        if max_total_seconds is not None and time.perf_counter() - started >= max_total_seconds:
            skipped_contexts.extend(row.get("goal_id") or row["profile"] for row in contexts[len(results_summary):])
            print(f"SCENARIO_CACHE contextos_omitidos_por_presupuesto={json.dumps(skipped_contexts, ensure_ascii=False)}")
            break
        with store._connect() as conn:
            run_id = _insert_run(conn, context, as_of, n_scenarios)
        context_started = time.perf_counter()
        active_context_started[0] = context_started
        progress_state = {}
        persistence_seconds = [0.0]
        def check_context_limits():
            _check_limits(context_started, max_rss_mb, max_seconds)
            _check_limits(started, max_rss_mb, max_total_seconds)
        def remaining_context_seconds():
            context_remaining = math.inf if max_seconds is None else max_seconds - (time.perf_counter() - context_started)
            total_remaining = math.inf if max_total_seconds is None else max_total_seconds - (time.perf_counter() - started)
            return min(context_remaining, total_remaining)
        def persist_progress(rows):
            persist_started = time.perf_counter()
            with store._connect() as conn:
                for row in rows:
                    piece = row.get("piece", {})
                    key = str(piece.get("candidate_key") or _candidate_key(piece))
                    ranked_row = rank_alternatives_v2([row], perfil=context["profile"],
                        capital_meta=float(context["capital"]))[0]
                    conn.execute("INSERT OR REPLACE INTO scenario_eval_results VALUES(?,?,?,?,?,?,?)",
                        (run_id, key, piece.get("symbol"), piece.get("strategy", "simple"),
                         _json_dumps({k: v for k, v in piece.items()
                             if k not in {"symbol", "strategy", "candidate_key"}}, ensure_ascii=False),
                         _json_dumps(row.get("metrics") or {}, ensure_ascii=False),
                         _json_dumps(ranked_row.get("objetivo") or {}, ensure_ascii=False)))
            persistence_seconds[0] += time.perf_counter() - persist_started
        try:
            evaluated = _evaluate_context(context, store=store, app_db=app_path,
                market_path=market_path, library_dir=library_dir, filters=filters,
                prices=prices, sigmas=sigmas, skipped=skipped, evaluator=evaluator,
                check_limits=check_context_limits, on_progress=persist_progress,
                progress_state=progress_state, n_scenarios=n_scenarios,
                symbol_filter=symbol_filter, perfilar=perfilar,
                remaining_seconds=remaining_context_seconds,
                trajectory_path=trajectory_directory / f"{run_id}.npz")
            evaluated.setdefault("profile_times", {})["escritura_bd_s"] = persistence_seconds[0]
            with store._connect() as conn:
                for row in evaluated.get("results", []):
                    conn.execute("INSERT OR REPLACE INTO scenario_eval_results VALUES(?,?,?,?,?,?,?)",
                        (run_id, row["candidate_key"], row.get("symbol"), row["strategy"],
                         _json_dumps(row["params"], ensure_ascii=False),
                         _json_dumps(row["metrics"], ensure_ascii=False),
                         _json_dumps(row["objective"], ensure_ascii=False)))
                conn.execute("UPDATE scenario_eval_runs SET status=?,error=?,seconds=?,rss_mb=?,"
                    "rss_current_mb=?,rss_hwm_mb=? WHERE run_id=?", (evaluated["status"],
                        _json_dumps({"skipped": evaluated.get("skipped_symbols", skipped),
                            "discarded": evaluated.get("discarded", {}),
                            "evaluated_count": evaluated.get("evaluated_count", 0),
                            "live_piece_count": evaluated.get("live_piece_count", 0),
                            "live_pieces": evaluated.get("live_pieces", []),
                            "live_capital_warning": evaluated.get("live_capital_warning"),
                            "symbol_stats": evaluated.get("symbol_stats", {}),
                            "trajectory_file": evaluated.get("trajectory_file"),
                            "failed_candidates": evaluated.get("failed_candidates", {}),
                            "smart_omitido_por_tiempo": evaluated.get("smart_omitido_por_tiempo", 0)},
                            ensure_ascii=False),
                        time.perf_counter() - context_started, _rss_max_mb(), _rss_current_mb(),
                        _rss_max_mb(), run_id))
            results_summary.append({"run_id": run_id, "context_seconds":
                time.perf_counter() - context_started, **evaluated})
            print(f"SCENARIO_CACHE contexto={context['goal_id'] or context['profile']} "
                f"estado={evaluated['status']} generados={evaluated.get('generated_count', 0)} "
                f"evaluados={evaluated.get('evaluated_count', 0)} "
                f"descartados={json.dumps(evaluated.get('discarded', {}), ensure_ascii=False)} "
                f"smart_omitido_por_tiempo={evaluated.get('smart_omitido_por_tiempo', 0)} "
                f"segundos_moneda={json.dumps(evaluated.get('candidate_seconds', {}), ensure_ascii=False)} "
                f"segundos={time.perf_counter()-context_started:.3f} "
                f"rss_actual_mb={_rss_current_mb()} rss_hwm_mb={_rss_max_mb()}")
            if perfilar:
                print(f"SCENARIO_CACHE perfil={symbol_filter or 'todos'} "
                    f"tiempos={_json_dumps(evaluated.get('profile_times', {}), ensure_ascii=False)}")
        except Exception as exc:
            status = exc.status if isinstance(exc, CacheLimitExceeded) else "failed"
            error_details = {"motivo": str(exc), **progress_state}
            error_details["discarded_count"] = sum(progress_state.get("discarded", {}).values())
            with store._connect() as conn:
                conn.execute("UPDATE scenario_eval_runs SET status=?,error=?,seconds=?,rss_mb=?,"
                    "rss_current_mb=?,rss_hwm_mb=? WHERE run_id=?", (status, _json_dumps(error_details, ensure_ascii=False),
                        time.perf_counter() - context_started,
                        _rss_max_mb(), _rss_current_mb(), _rss_max_mb(), run_id))
            if isinstance(exc, CacheLimitExceeded):
                with store._connect() as conn:
                    count = conn.execute("SELECT COUNT(*) FROM scenario_eval_results WHERE run_id=?",
                        (run_id,)).fetchone()[0]
                    symbols_done = conn.execute("SELECT COUNT(DISTINCT symbol) FROM scenario_eval_results "
                        "WHERE run_id=? AND symbol IS NOT NULL", (run_id,)).fetchone()[0]
                if context.get("goal_id") is None and count >= 20 and symbols_done >= 3:
                    status = "parcial"
                    error_details["status_limite"] = exc.status
                    with store._connect() as conn:
                        conn.execute("UPDATE scenario_eval_runs SET status=?,error=? WHERE run_id=?",
                            (status, _json_dumps(error_details, ensure_ascii=False), run_id))
                results_summary.append({"run_id": run_id, "status": status,
                    **progress_state, "discarded_count": error_details["discarded_count"],
                    "evaluated_count": count})
                print(f"SCENARIO_CACHE contexto={context['goal_id'] or context['profile']} "
                    f"estado={status} generados={progress_state.get('generated_count', 0)} "
                    f"evaluados={count} descartados={json.dumps(progress_state.get('discarded', {}), ensure_ascii=False)} "
                    f"segundos_moneda={json.dumps(progress_state.get('candidate_seconds', {}), ensure_ascii=False)} "
                    f"segundos={time.perf_counter()-context_started:.3f} rss_max_mb={_rss_max_mb()}")
                active_context_started[0] = None
                if max_total_seconds is None or time.perf_counter() - started < max_total_seconds:
                    continue
                skipped_contexts.extend(row.get("goal_id") or row["profile"] for row in contexts[len(results_summary):])
                print(f"SCENARIO_CACHE contextos_omitidos_por_presupuesto={json.dumps(skipped_contexts, ensure_ascii=False)}")
                break
            watchdog_stop.set()
            watcher.join(timeout=2)
            raise
        finally:
            active_context_started[0] = None
    watchdog_stop.set()
    watcher.join(timeout=2)
    return {"contexts": len(contexts), "runs": results_summary,
        "skipped_contexts": skipped_contexts,
        "seconds": time.perf_counter() - started, "rss_mb": _rss_max_mb(),
        "rss_current_mb": _rss_current_mb(), "rss_hwm_mb": _rss_max_mb()}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app-db", required=True)
    parser.add_argument("--market-db", required=True)
    parser.add_argument("--biblioteca", default="data/derived/scenario_lib")
    parser.add_argument("--max-rss-mb", type=float, default=600)
    parser.add_argument("--max-segundos", type=float, default=540)
    parser.add_argument("--max-segundos-total", type=float, default=1500)
    parser.add_argument("--escenarios", type=int, default=SCENARIO_COUNT)
    parser.add_argument("--perfilar", action="store_true")
    parser.add_argument("--symbol")
    parser.add_argument("--contexto", help="ID de meta o perfil:<NOMBRE>")
    args = parser.parse_args(argv)
    if os.name == "posix":
        try:
            os.nice(10)
        except OSError:
            pass
    started = time.perf_counter()
    try:
        if args.contexto is None:
            app_path = Path(args.app_db).resolve()
            market_path = market_db_path(args.market_db)
            lightweight_contexts = sorted(_contexts(app_path, {}), key=_context_priority)
            selectors = [str(row["goal_id"]) if row.get("goal_id") is not None else
                f"perfil:{row['profile']}" for row in lightweight_contexts]
            completed = []
            for selector in selectors:
                remaining = args.max_segundos_total - (time.perf_counter() - started)
                if remaining <= 0:
                    break
                command = [sys.executable, str(Path(__file__).resolve()), "--app-db",
                    str(app_path), "--market-db", str(market_path), "--biblioteca",
                    args.biblioteca, "--max-rss-mb", str(args.max_rss_mb),
                    "--max-segundos", str(args.max_segundos), "--max-segundos-total",
                    str(remaining), "--escenarios", str(args.escenarios), "--contexto", selector]
                if args.perfilar:
                    command.append("--perfilar")
                if args.symbol:
                    command.extend(["--symbol", args.symbol])
                if os.name == "posix":
                    command = ["nice", "-n", "10", *command]
                child = subprocess.run(command, capture_output=True, text=True,
                    check=False, timeout=remaining)
                print(child.stdout or "", end="")
                if child.stderr:
                    print(child.stderr, file=sys.stderr, end="")
                completed.append(child.returncode)
                if child.returncode:
                    return child.returncode
            if len(completed) < len(selectors):
                print(f"SCENARIO_CACHE contextos_pendientes={json.dumps(selectors[len(completed):])}")
            print(f"SCENARIO_CACHE estado=completed contextos={len(completed)} "
                f"segundos={time.perf_counter()-started:.3f}")
            return 0
        result = run_cache(args.app_db, args.market_db, library_dir=args.biblioteca,
            max_rss_mb=args.max_rss_mb, max_seconds=args.max_segundos,
            max_total_seconds=args.max_segundos_total,
            n_scenarios=args.escenarios, symbol_filter=args.symbol, perfilar=args.perfilar,
            context_selector=args.contexto)
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
