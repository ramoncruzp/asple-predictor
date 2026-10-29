"""Evaluación horaria en sombra para grids Smart y Simple ajenos a Meta."""
from __future__ import annotations

import json
import logging
import threading
import time
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from statistics import median

from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy import select

from grid.sim.runner import run_simulation

logger = logging.getLogger(__name__)
GRID_INTEL_BUDGET_S = 60.0
GRID_INTEL_PER_GRID_BUDGET_S = 45.0
GRID_INTEL_MIN_GAIN_PCT = 0.3
GRID_INTEL_MAX_DRAWDOWN_DELTA_PP = 1.0
GRID_INTEL_METHOD_VERSION = "grid_shadow_42c39_v2"
GRID_INTEL_RETENTION_DAYS = 30
GRID_INTEL_WINDOW_DAYS = 7
GRID_INTEL_WINDOW_STEP_DAYS = 14
GRID_INTEL_LOOKBACK_DAYS = 180
GRID_INTEL_MIN_BOT_CASH_USDT = 5.5
GRID_INTEL_BREAK_PAUSE_THRESHOLD = 0.70
GRID_INTEL_HIGH_VOLATILITY_THRESHOLD = 0.10
GRID_INTEL_PROCESS_CACHE_MAX = 512
GRID_INTEL_PERSISTENCE_H = 2
GRID_INTEL_COOLDOWN_H = 6
GRID_INTEL_MAX_CHANGES_24H = 2


def _json(value):
    if isinstance(value, dict):
        return value
    try:
        return json.loads(value or "{}")
    except (TypeError, ValueError):
        return {}


def paired_gain_metrics(candidate_gains, baseline_gains):
    candidate = [float(value) for value in candidate_gains]
    baseline = [float(value) for value in baseline_gains]
    paired_count = min(len(candidate), len(baseline))
    if paired_count <= 0:
        raise ValueError("las curvas no tienen ventanas comunes para comparar")
    # Si un presupuesto corta el replay, las listas conservan el mismo orden de ventanas;
    # se compara solo el prefijo común, sin usar ventanas desalineadas.
    differences = sorted(left - right for left, right in zip(candidate[:paired_count], baseline[:paired_count]))
    return {"median": float(median(differences)),
        "p10": float(differences[max(0, int((len(differences) - 1) * .1))]),
        "windows": paired_count}


def recommend_grid_action(candidates, capital):
    """Choose a shadow proposal only when gain and drawdown gates both pass."""
    rows = list(candidates or [])
    baseline = next((row for row in rows if row.get("action") == "mantener"
        and not row.get("error") and not row.get("discard_reason")
        and row.get("expected_gain_7d") is not None
        and row.get("paired_diff_median") is not None), None)
    if baseline is None:
        return None, "línea base no simulable"
    threshold = float(capital or 0) * GRID_INTEL_MIN_GAIN_PCT / 100.0
    eligible = [row for row in rows if row is not baseline and not row.get("error")
        and not row.get("discard_reason")
        and row.get("paired_diff_median") is not None and row.get("drawdown") is not None
        and float(row["paired_diff_median"]) >= threshold
        and float(row["drawdown"]) <= float(baseline["drawdown"]) + GRID_INTEL_MAX_DRAWDOWN_DELTA_PP]
    eligible.sort(key=lambda row: (float(row["paired_diff_median"]), -float(row["drawdown"])), reverse=True)
    if not eligible:
        return baseline, "ninguna alternativa supera los umbrales; mantener"
    return eligible[0], "supera ganancia mínima y límite de drawdown"


def retained_inventory_metrics(candle_timestamps, closes, trace_rows, free_equity, inventory_lots, starting_cash,
                               fee_rate=.001, reference_price=None, window_start_price=None):
    """Ganancia y drawdown con inventario retenido marcado a bid neto de comisión."""
    import numpy as np
    held_qty = sum(float(item.get("held_qty") or 0) for item in inventory_lots)
    held_basis = sum(float(item.get("entry_cost") or 0) + float(item.get("entry_fee_usdt") or 0)
        for item in inventory_lots)
    initial = float(starting_cash) + held_basis
    scale = 1.0
    if reference_price is not None:
        start_price = float(window_start_price if window_start_price is not None else closes[0])
        if start_price <= 0:
            raise ValueError("el precio inicial de la ventana debe ser positivo")
        scale = float(reference_price) / start_price
    path = []
    for item, value in zip(trace_rows, free_equity):
        pos = int(np.searchsorted(candle_timestamps, int(item["timestamp"]), side="right") - 1)
        pos = max(0, min(pos, len(closes) - 1))
        path.append(float(value) + held_qty * float(closes[pos]) * scale * (1 - fee_rate))
    if not path:
        raise ValueError("el simulador no produjo curva de patrimonio")
    peak, max_dd = initial, 0.0
    for value in path:
        peak = max(peak, value)
        if peak > 0:
            max_dd = max(max_dd, (peak - value) / peak * 100)
    return {"gain": path[-1] - initial, "drawdown": max_dd}


class GridIntelligenceJob:
    """Replay de baja prioridad y escritura de recomendaciones, sin candados de órdenes."""

    def __init__(self, db, market_client, filters_resolver, *, advisor=None, settings=None,
                 vol_provider=None, scheduler=None,
                 clock=None, budget_s=GRID_INTEL_BUDGET_S,
                 per_grid_budget_s=GRID_INTEL_PER_GRID_BUDGET_S, apply_callback=None,
                 valuation_checker=None, ops_lock=None):
        self.db, self.market_client, self.filters_resolver = db, market_client, filters_resolver
        self.advisor = advisor
        self.settings = settings
        self.vol_provider = vol_provider
        self.apply_callback, self.valuation_checker = apply_callback, valuation_checker
        self.ops_lock = ops_lock
        self.scheduler = scheduler or BackgroundScheduler(timezone="UTC")
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.budget_s = float(budget_s)
        self.per_grid_budget_s = float(per_grid_budget_s)
        self._grid_intel_guard = threading.Lock()
        self.last_summary = {"status": "not_run"}
        self._replay_cache = OrderedDict()
        self._candles_cache = OrderedDict()

    def start(self):
        self.scheduler.add_job(self.run_once, "cron", minute=7, second=0, timezone="UTC",
            id="grid_intelligence_shadow", replace_existing=True, max_instances=1,
            coalesce=True, misfire_grace_time=1800)
        self.scheduler.add_job(self.label_mature_recommendations, "cron", hour=9, minute=0,
            second=0, timezone="UTC", id="grid_intelligence_labels", replace_existing=True,
            max_instances=1, coalesce=True, misfire_grace_time=3600)
        if not self.scheduler.running:
            self.scheduler.start()

    def stop(self):
        if getattr(self.scheduler, "running", False):
            self.scheduler.shutdown(wait=True)

    def run_once(self):
        if not self._grid_intel_guard.acquire(blocking=False):
            return {"status": "skipped", "reason": "ejecución horaria anterior activa"}
        started = time.perf_counter()
        summary = {"status": "completed", "included": [], "included_details": [], "excluded": [],
            "recommendations": 0, "candidates_evaluated": 0, "budget_exceeded": False,
            "per_grid_budget_s": self.per_grid_budget_s, "grid_results": [],
            "duration_s": 0.0, "timings_s": {"candles": 0.0, "advisor": 0.0,
                "replay": 0.0, "persistence": 0.0}, "cache_hits": 0}
        try:
            self._prune_process_caches()
            grids = self.db.list_grids_by_status({"OPENING", "ACTIVE", "PAUSED", "CLOSING",
                "HOLDING", "CLOSED", "CANCELLED", "FAILED", "ERROR"})
            for grid in grids:
                reason = self._excluded_reason(grid)
                if reason:
                    summary["excluded"].append({"grid_id": grid["id"], "symbol": grid.get("symbol"), "reason": reason})
                    continue
                summary["included"].append(int(grid["id"]))
                grid_started = time.perf_counter()
                summary["included_details"].append({"grid_id": int(grid["id"]), "symbol": grid.get("symbol")})
                try:
                    if time.perf_counter() - started >= self.budget_s:
                        summary.update(status="budget_exceeded", budget_exceeded=True)
                        record = self._partial_record("presupuesto total agotado antes de evaluar")
                    else:
                        deadline = min(started + self.budget_s, grid_started + self.per_grid_budget_s)
                        record = self._evaluate_grid(grid, started, summary, deadline=deadline)
                    if record:
                        persist_started = time.perf_counter()
                        self._persist(grid, record)
                        summary["timings_s"]["persistence"] += time.perf_counter() - persist_started
                        summary["recommendations"] += 1
                        summary["grid_results"].append({"grid_id": int(grid["id"]),
                            "symbol": grid.get("symbol"), "status": "partial" if record.get("partial") else "completed"})
                        applied = self._apply_if_eligible(grid, record)
                        if applied:
                            summary.setdefault("applied", []).append(applied)
                except Exception as exc:
                    summary.setdefault("errors", []).append({"grid_id": grid["id"],
                        "error": f"{type(exc).__name__}: {exc}"})
                    logger.exception("Falló evaluación de sombra del grid %s", grid["id"])
                    try:
                        fallback = self._partial_record(f"{type(exc).__name__}: {exc}")
                        self._persist(grid, fallback)
                        summary["recommendations"] += 1
                        summary["grid_results"].append({"grid_id": int(grid["id"]),
                            "symbol": grid.get("symbol"), "status": "partial", "error": fallback["signals"]["partial_reason"]})
                    except Exception:
                        logger.exception("No se pudo guardar la recomendación parcial del grid %s", grid["id"])
            self._prune()
            if summary.get("budget_exceeded"):
                summary["unprocessed_grid_ids"] = [int(row["id"]) for row in grids
                    if int(row["id"]) not in set(summary["included"])
                    and int(row["id"]) not in {int(item["grid_id"]) for item in summary["excluded"]}]
        except Exception as exc:
            summary.update(status="failed", error=f"{type(exc).__name__}: {exc}")
            logger.exception("Falló el job horario de inteligencia de grids")
        finally:
            summary["duration_s"] = round(time.perf_counter() - started, 3)
            summary["timings_s"] = {key: round(float(value), 3)
                for key, value in summary.get("timings_s", {}).items()}
            try:
                self.db.add_grid_event(run_id=None, source="CLI", grid_id=None,
                    event_type="GRID_INTEL_JOB_SUMMARY", reason=str(summary.get("status")),
                    details=summary)
            except Exception:
                logger.exception("No se pudo guardar el resumen del job de Inteligencia de grids")
            self.last_summary = summary
            self._grid_intel_guard.release()
        return summary

    def _excluded_reason(self, grid):
        status = str(grid.get("status", "")).upper()
        if status == "HOLDING":
            return "repositorio/HOLDING"
        strategy = str(grid.get("strategy", "")).lower()
        if strategy not in {"smart", "simple"}:
            return "estrategia no Smart/Simple"
        params = _json(grid.get("params"))
        if self.db.is_meta_grid(int(grid["id"])):
            return "pieza ligada a Meta"
        if params.get("meta_piece_type") or params.get("meta_id"):
            return "bot o pieza Meta"
        if status != "ACTIVE":
            return f"estado {status or 'desconocido'}"
        return None

    @staticmethod
    def _slice(candles, start, end):
        from grid.sim.data import CandleData
        return CandleData(timestamp=candles.timestamp[start:end], open=candles.open[start:end],
            high=candles.high[start:end], low=candles.low[start:end],
            close=candles.close[start:end], gaps=0)

    def _candles(self, symbol):
        cache_key = (str(symbol), int(self.clock().timestamp() // 86400))
        cached = self._candles_cache.get(cache_key)
        if cached is not None:
            self._candles_cache.move_to_end(cache_key)
            return cached
        from grid.sim.data import CandleData
        import pandas as pd
        frame = self.market_client.get_historical_klines(symbol, "5m", GRID_INTEL_LOOKBACK_DAYS)
        if not isinstance(frame, pd.DataFrame):
            frame = pd.DataFrame(frame)
        stamps = pd.to_datetime(frame["timestamp"], utc=True).astype("int64").to_numpy() // 1_000_000_000
        candles = CandleData(timestamp=stamps.astype("int64"), open=frame.open.to_numpy(float),
            high=frame.high.to_numpy(float), low=frame.low.to_numpy(float),
            close=frame.close.to_numpy(float), gaps=0)
        self._candles_cache[cache_key] = candles
        while len(self._candles_cache) > GRID_INTEL_PROCESS_CACHE_MAX:
            self._candles_cache.popitem(last=False)
        return candles

    def _prune_process_caches(self):
        today = int(self.clock().timestamp() // 86400)
        self._candles_cache = OrderedDict((key, value) for key, value in self._candles_cache.items()
            if key[1] == today)
        # La caché de replay se retiene por LRU: su día es el corte histórico
        # de la ventana, no el día UTC de hoy.

    @staticmethod
    def _partial_record(reason):
        fallback = {"action": "mantener", "partial": True, "windows": 0,
            "expected_gain_7d": None, "p10_gain_7d": None, "drawdown": None,
            "discard_reason": reason}
        return {"action": "mantener", "candidate": fallback, "baseline": fallback,
            "candidates": [fallback], "partial": True,
            "reason": f"recomendación parcial: {reason}", "signals": {"partial_reason": reason}}

    def _evaluate_grid(self, grid, started, summary, *, deadline=None):
        from grid.sim.data import ewma_sigma_24h
        grid_id, symbol = int(grid["id"]), str(grid["symbol"])
        strategy = str(grid["strategy"]).lower()
        params = _json(grid.get("params"))
        levels = self.db.get_grid_levels(grid_id)
        inventory_cells = [row for row in levels if float(row.get("held_qty") or 0) > 0]
        free_cells = [row for row in levels if float(row.get("held_qty") or 0) <= 0]
        capital = float(grid.get("capital_total") or sum(float(row.get("capital") or 0) for row in levels))
        free_cash = sum(float(row.get("capital") or 0) for row in free_cells
            if str(row.get("state", "")).upper() in {"IDLE", "DONE"} and row.get("order_id") is None)
        free_cash_cells = [row for row in free_cells if str(row.get("state", "")).upper() in {"IDLE", "DONE"}
            and row.get("order_id") is None]
        free_cash_level_ids = [int(row["level_idx"]) for row in free_cash_cells]
        candles_started = time.perf_counter()
        candles = self._candles(symbol)
        summary["timings_s"]["candles"] += time.perf_counter() - candles_started
        filters = self.filters_resolver(symbol)
        low, high = float(grid["range_low"]), float(grid["range_high"])
        n = int(grid["n_levels"])
        baseline_geometry = {"low": low, "high": high, "n": n}
        geometries = [("mantener", baseline_geometry)]
        step = (high - low) / max(1, n - 1)
        for label, shift in (("mover_rango_menos_un_paso", -step), ("mover_rango_mas_un_paso", step)):
            geometries.append((label, {"low": low + shift, "high": high + shift, "n": n}))
        for new_n in (n - 1, n + 1):
            geometries.append((f"cambiar_n_{new_n}", {"low": low, "high": high, "n": new_n}))
        current_price = float(candles.close[-1])
        sigma = 0.0
        sigma_source = "realized_ewma"
        break_probability = 0.0
        sigma = float(ewma_sigma_24h(candles.close, 72, bars_per_hour=12)[-1])
        if self.advisor is not None:
            try:
                mid = float(candles.close[-1])
                if self.vol_provider is not None:
                    forecast = self.vol_provider.get(symbol, 24)
                    if forecast is not None:
                        sigma = float(forecast.sigma_24h)
                        sigma_source = str(getattr(forecast, "source", "model"))
                advisor_started = time.perf_counter()
                suggested = self.advisor(symbol, capital, .3, 7)
                summary["timings_s"]["advisor"] += time.perf_counter() - advisor_started
                a_low, a_high = suggested.get("recommended_floor"), suggested.get("recommended_ceiling")
                if a_low and a_high and float(a_low) < mid < float(a_high):
                    geometries.append(("advisor", {"low": float(a_low), "high": float(a_high),
                        "n": int(suggested.get("suggested_grids") or n), "sigma_24h": sigma}))
            except Exception as exc:
                geometries.append(("advisor", {"error": f"{type(exc).__name__}: {exc}"}))
        try:
            from grid.meta_learning import meta_break_prob_v2
            break_probability = float(meta_break_prob_v2(current_price, low, high, sigma))
        except Exception:
            break_probability = 0.0
        if free_cash >= GRID_INTEL_MIN_BOT_CASH_USDT:
            for drop_pct in (.5, 1.5, 3.0):
                buy = current_price * (1 - drop_pct / 100)
                sell = buy * (1 + .0115)  # +0.95 % net after two 0.1 % fees.
                geometries.append((f"agregar_bot_menos_{drop_pct:g}", {"low": buy, "high": sell,
                    "n": 1, "bot": True, "affected_capital": free_cash,
                    "source_level_idxs": free_cash_level_ids, "buy_drop_pct": drop_pct}))
        if strategy == "smart" and (break_probability >= GRID_INTEL_BREAK_PAUSE_THRESHOLD
                or sigma >= GRID_INTEL_HIGH_VOLATILITY_THRESHOLD):
            geometries.append(("pausar_compras", {**baseline_geometry, "pause_buys": True,
                "break_prob_v2": break_probability, "sigma_24h": sigma}))
        # Existing inventory remains outside every branch: simulations cover free cells/cash only.
        affected_capital = sum(float(row.get("capital") or 0) for row in free_cells)
        if affected_capital <= 0:
            affected_capital = max(0.0, capital - sum(float(row.get("entry_cost") or 0) for row in inventory_cells))
        affected_capital = min(capital, max(0.0, affected_capital))
        candidates = []
        from grid.adjust import plan_adjust_with_shrink
        engine_settings = getattr(self, "settings", None)
        if engine_settings is None:
            # Los valores de producción de GridEngine, sin activar su interruptor global.
            class _PlannerSettings:
                grid_min_step_pct = .003
                capital_max_por_nivel_pct = .30
            engine_settings = _PlannerSettings()
        window_seconds = GRID_INTEL_WINDOW_DAYS * 86400
        timestamps = candles.timestamp
        step_bars = GRID_INTEL_WINDOW_STEP_DAYS * 24 * 12
        start_indices = [i for i, stamp in enumerate(timestamps)
            if int(stamp) + window_seconds <= int(timestamps[-1]) and i % step_bars == 0]
        if not start_indices:
            start_indices = [0] if len(timestamps) > 2 else []
        import numpy as np
        preload_started = time.perf_counter()
        window_samples = []
        for idx in start_indices:
            end_idx = int(np.searchsorted(timestamps, int(timestamps[idx]) + window_seconds, side="right"))
            sample = self._slice(candles, idx, end_idx)
            if len(sample.timestamp) >= 2:
                window_samples.append((idx, sample))
        summary.setdefault("timings_s", {}).setdefault("window_preload", 0.0)
        summary["timings_s"]["window_preload"] += time.perf_counter() - preload_started
        summary.setdefault("window_counts", {})[str(grid_id)] = {
            "step_days": GRID_INTEL_WINDOW_STEP_DAYS, "windows_available": len(window_samples)}
        candidate_timings = []
        partial = False
        baseline_window_gains = None
        for action, geometry in geometries:
            if deadline is not None and time.perf_counter() >= deadline:
                summary.update(status="budget_exceeded", budget_exceeded=True)
                candidates.append({"action": action, "discard_reason": "presupuesto por grid/total agotado"})
                partial = True
                break
            if geometry.get("error"):
                candidates.append({"action": action, "discard_reason": geometry["error"]})
                continue
            if action != "mantener" and not geometry.get("bot") and not geometry.get("pause_buys"):
                planning_grid = {**grid, "capital_total": capital, "n_levels": n, "params": params}
                current_price = float(candles.close[-1])
                plan = plan_adjust_with_shrink(planning_grid, levels, geometry["low"],
                    geometry["high"], geometry["n"], current_price, filters, engine_settings)
                if not plan.ok:
                    candidates.append({"action": action, "geometry": geometry,
                        "discard_reason": f"plan_adjust: {plan.reason}"})
                    continue
                geometry = {**geometry, "planned_range": plan.details.get("range"),
                    "planned_n": plan.details.get("n_levels"),
                    "free_capital": float(plan.free_capital)}
                if geometry.get("planned_range"):
                    geometry["low"] = float(geometry["planned_range"]["low"])
                    geometry["high"] = float(geometry["planned_range"]["high"])
                    geometry["n"] = int(geometry.get("planned_n") or geometry["n"])
            metrics = []
            replay_started = time.perf_counter()
            try:
                for idx, sample in window_samples:
                    if deadline is not None and time.perf_counter() >= deadline:
                        partial = True
                        summary.update(status="budget_exceeded", budget_exceeded=True)
                        break
                    scale = float(sample.open[0]) / max(1e-12, (float(geometry["low"]) + float(geometry["high"])) / 2)
                    replay_capital = (min(float(geometry.get("affected_capital") or 0), affected_capital)
                        if geometry.get("bot") else max(affected_capital, 5.5))
                    # El resultado depende de la entrada simulada, no del rótulo del candidato.
                    # Así se comparte una curva exacta cuando dos acciones proponen la misma geometría.
                    key = (symbol, strategy, round(replay_capital, 6),
                        round(geometry["low"] * scale, 10), round(geometry["high"] * scale, 10),
                        int(geometry["n"]), bool(geometry.get("pause_buys")), int(timestamps[idx] // 86400))
                    sim = self._replay_cache.get(key)
                    if sim is None:
                        if geometry.get("bot"):
                            from grid.sim.meta_bot import run_meta_bot
                            bot_capital = replay_capital
                            if bot_capital < GRID_INTEL_MIN_BOT_CASH_USDT:
                                raise ValueError("efectivo libre inferior al mínimo operable del bot")
                            bot_buy, bot_sell = float(geometry["low"]) * scale, float(geometry["high"]) * scale
                            trace_rows = []
                            sim = {"metrics": run_meta_bot(sample, capital=bot_capital,
                                buy_price=bot_buy, sell_price=bot_sell, target_usdt=None,
                                filters=filters, min_margin_pct=.3, trace_callback=trace_rows.append),
                                "trace": trace_rows}
                        else:
                            trace_rows = []
                            sim = run_simulation(sample, strategy=strategy, n=max(4, int(geometry["n"])),
                                capital=replay_capital, low=float(geometry["low"]) * scale,
                                high=float(geometry["high"]) * scale, params=params if strategy == "smart" else None,
                                filters=filters, adjust_enabled_global=False, stoploss_enabled=False,
                                close_wait_enabled=True,
                                direction_signal=([1.0] * len(sample.timestamp) if geometry.get("pause_buys") else None),
                                trace_callback=trace_rows.append, meta_portfolio_piece=True)
                            sim["trace"] = trace_rows
                        self._replay_cache[key] = sim
                        self._replay_cache.move_to_end(key)
                        while len(self._replay_cache) > GRID_INTEL_PROCESS_CACHE_MAX:
                            self._replay_cache.popitem(last=False)
                    else:
                        self._replay_cache.move_to_end(key)
                        summary["cache_hits"] += 1
                    result = sim["metrics"]
                    trace = sim.get("trace") or []
                    if geometry.get("bot"):
                        free_equity = [float(item.get("equity") or 0) for item in trace]
                        fees = float(replay_capital) * .001 * (1 + int(bool(result.get("sold"))))
                    else:
                        free_equity = [float(item.get("equity_incl_dust", item.get("equity", 0)) or 0)
                            for item in trace]
                        fees = float(result.get("fees_usdt") or 0)
                    combined = retained_inventory_metrics(sample.timestamp, sample.close, trace,
                        free_equity, [{"held_qty": row.get("held_qty"),
                            "entry_cost": row.get("entry_cost"),
                            "entry_fee_usdt": row.get("entry_fee_usdt")} for row in inventory_cells],
                        replay_capital, reference_price=current_price, window_start_price=float(sample.open[0]))
                    gain, worst_dd = combined["gain"], combined["drawdown"]
                    metrics.append({"gain": gain,
                        "drawdown": worst_dd,
                        "fees": fees})
                if not metrics:
                    raise ValueError("sin ventanas históricas simulables")
                gains = sorted(row["gain"] for row in metrics)
                p10 = gains[max(0, int((len(gains) - 1) * .1))]
                if action == "mantener":
                    baseline_window_gains = [row["gain"] for row in metrics]
                baseline_gains = baseline_window_gains or [row["gain"] for row in metrics]
                paired = paired_gain_metrics([row["gain"] for row in metrics], baseline_gains)
                candidates.append({"action": action, "geometry": geometry,
                    "expected_gain_7d": float(median(gains)), "p10_gain_7d": p10,
                    "expected_gain_7d_baseline": float(median(baseline_gains)),
                    "paired_diff_median": paired["median"], "paired_diff_p10": paired["p10"],
                    "drawdown": max(row["drawdown"] for row in metrics),
                    "fees_median": float(median(row["fees"] for row in metrics)),
                    "windows": len(metrics), "windows_expected": len(window_samples),
                    "partial": len(metrics) < len(window_samples),
                    "timing_s": round(time.perf_counter() - replay_started, 3),
                    "affected_capital": float(geometry.get("affected_capital") or affected_capital)})
                partial = partial or len(metrics) < len(window_samples)
                summary["candidates_evaluated"] += 1
            except Exception as exc:
                candidates.append({"action": action, "geometry": geometry,
                    "discard_reason": f"{type(exc).__name__}: {exc}"})
            finally:
                summary["timings_s"]["replay"] += time.perf_counter() - replay_started
                candidate_timings.append({"action": action,
                    "duration_s": round(time.perf_counter() - replay_started, 3),
                    "windows": len(metrics)})
        summary.setdefault("candidate_timings_s", {})[str(grid_id)] = candidate_timings
        selected, reason = recommend_grid_action(candidates, capital)
        if selected is None:
            return self._partial_record("no se completó una ventana antes del presupuesto")
        baseline = next((item for item in candidates if item.get("action") == "mantener"), {})
        return {"action": selected.get("action", "mantener"), "candidate": selected,
            "baseline": baseline, "candidates": candidates, "reason": reason,
            "partial": partial or bool(selected.get("partial")),
            "signals": {"inventory_cells_retained": len(inventory_cells), "free_cells": len(free_cells),
                "free_cash_usdt": free_cash, "sigma_24h": sigma, "sigma_source": sigma_source,
                "break_prob_v2": break_probability,
                "inventory_lots": [{"held_qty": float(row.get("held_qty") or 0),
                    "entry_cost": float(row.get("entry_cost") or 0),
                    "entry_fee_usdt": float(row.get("entry_fee_usdt") or 0)} for row in inventory_cells],
                "simulation_scope": "curva total: parte afectada simulada + inventario retenido marcado a bid neto de 0,1 %"}}

    def _persist(self, grid, record):
        now = self.clock()
        candidate = dict(record["candidate"])
        baseline = dict(record["baseline"])
        values = {"grid_id": int(grid["id"]), "symbol": str(grid["symbol"]),
            "strategy": str(grid["strategy"]).lower(), "created_at": now,
            "action": record["action"], "candidate_json": json.dumps({**candidate,
                "partial": bool(record.get("partial")),
                "reason": record["reason"], "all_candidates": record["candidates"]}, ensure_ascii=False),
            "baseline_json": json.dumps(baseline, ensure_ascii=False),
            "expected_gain_7d": candidate.get("expected_gain_7d"),
            "expected_gain_7d_baseline": baseline.get("expected_gain_7d"),
            "drawdown": candidate.get("drawdown"), "signals_json": json.dumps(record["signals"], ensure_ascii=False),
            "method_version": GRID_INTEL_METHOD_VERSION, "would_apply": False}
        with self.db.engine.begin() as conn:
            rec_id = conn.execute(self.db.grid_intel_recommendations.insert().values(**values)).inserted_primary_key[0]
        add_event = getattr(self.db, "add_grid_event", None)
        if callable(add_event):
            add_event(run_id=None, source="CLI", grid_id=int(grid["id"]),
                event_type="GRID_INTEL_RECOMMENDATION", reason=record["reason"],
                details={"recommendation_id": int(rec_id), "action": record["action"],
                    "expected_gain_7d": candidate.get("expected_gain_7d"),
                    "baseline_gain_7d": baseline.get("expected_gain_7d"),
                    "paired_diff_median": candidate.get("paired_diff_median"),
                    "paired_diff_p10": candidate.get("paired_diff_p10"), "would_apply": False})

    def _apply_if_eligible(self, grid, record):
        grid_id = int(grid["id"])
        params = _json(grid.get("params"))
        if not params.get("intel_apply_enabled") or self.apply_callback is None:
            return None
        action = str(record.get("action") or "mantener")
        if action == "mantener":
            return None
        from api.routes.intelligence import _grid_intelligence_summary
        evidence = next((item for item in _grid_intelligence_summary(self.db, target_grid_id=grid_id)["grids"]
            if int(item["grid_id"]) == grid_id), None)
        mature = (evidence or {}).get("mature_labels") or {}
        readiness = mature.get("readiness_by_action") or {} if isinstance(mature, dict) else {}
        if not readiness.get(action) or readiness[action].get("state") != "Listo para encender":
            return {"grid_id": grid_id, "status": "blocked",
                "reason": "la acción aún no tiene evidencia madura suficiente"}
        now = self.clock()
        with self.db.engine.connect() as conn:
            recent = conn.execute(select(self.db.grid_intel_recommendations.c.action,
                self.db.grid_intel_recommendations.c.created_at).where(
                    self.db.grid_intel_recommendations.c.grid_id == grid_id,
                    self.db.grid_intel_recommendations.c.created_at >= now - timedelta(hours=GRID_INTEL_PERSISTENCE_H))
                .order_by(self.db.grid_intel_recommendations.c.created_at.desc()).limit(2)).all()
        if len(recent) < 2 or any(str(row[0]) != action for row in recent):
            return {"grid_id": grid_id, "status": "blocked", "reason": "acción no persistió en dos revisiones"}
        events = self.db.list_grid_events(grid_id=grid_id, limit=5000)
        applied_events = [event for event in events if event.get("event_type") == "GRID_INTEL_APPLIED"]
        applied_events.sort(key=lambda event: self._event_age_hours(event.get("ts"), now))
        recent_changes = [event for event in applied_events if self._event_age_hours(event.get("ts"), now) < 24]
        if len(recent_changes) >= GRID_INTEL_MAX_CHANGES_24H:
            return {"grid_id": grid_id, "status": "blocked", "reason": "máximo de cambios en 24 horas"}
        if applied_events and self._event_age_hours(applied_events[0].get("ts"), now) < GRID_INTEL_COOLDOWN_H:
            return {"grid_id": grid_id, "status": "blocked", "reason": "enfriamiento de seis horas"}
        candidate = record.get("candidate") or {}
        from contextlib import nullcontext
        guard = self.ops_lock if self.ops_lock is not None else nullcontext()
        with guard:
            current = self.db.get_grid(grid_id)
            if not current or str(current.get("status", "")).upper() != "ACTIVE":
                return {"grid_id": grid_id, "status": "blocked", "reason": "grid no ACTIVE"}
            current_params = _json(current.get("params"))
            if (not current_params.get("intel_apply_enabled")
                    or current_params.get("meta_piece_type") or current_params.get("meta_id")
                    or self.db.is_meta_grid(grid_id)):
                return {"grid_id": grid_id, "status": "blocked",
                    "reason": "interruptor apagado o grid pertenece a Meta"}
            if self.valuation_checker is None or not self.valuation_checker(current):
                return {"grid_id": grid_id, "status": "blocked", "reason": "valoración no conocida"}
            before = {key: current.get(key) for key in ("range_low", "range_high", "n_levels", "status")}
            try:
                result = self.apply_callback(current, action, candidate)
            except Exception as exc:
                logger.exception("No se pudo aplicar la recomendación del grid %s", grid_id)
                return {"grid_id": grid_id, "status": "failed", "reason": f"{type(exc).__name__}: {exc}"}
            if not result or result.get("ok") is False:
                return {"grid_id": grid_id, "status": "blocked", "reason": (result or {}).get("reason", "aplicación rechazada")}
            after_grid = self.db.get_grid(grid_id) or {}
            after = {key: after_grid.get(key) for key in before}
            self.db.add_grid_event(run_id=None, source="CLI", grid_id=grid_id,
                event_type="GRID_INTEL_APPLIED", reason="recomendación confirmada por evidencia y puertas", details={
                    "action": action, "before": before, "after": after,
                    "recommendation_id": result.get("recommendation_id"), "gates": {
                        "two_hour_persistence": True, "cooldown_hours": GRID_INTEL_COOLDOWN_H,
                        "max_changes_24h": GRID_INTEL_MAX_CHANGES_24H, "valuation_known": True},
                    "child_bot_grid_id": result.get("bot_grid_id")})
        return {"grid_id": grid_id, "status": "applied", "action": action}

    @staticmethod
    def _event_age_hours(value, now):
        if isinstance(value, str):
            try:
                value = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                return float("inf")
        if not isinstance(value, datetime):
            return float("inf")
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return max(0.0, (now - value).total_seconds() / 3600)

    def _prune(self):
        cutoff = self.clock() - timedelta(days=GRID_INTEL_RETENTION_DAYS)
        with self.db.engine.begin() as conn:
            old_ids = select(self.db.grid_intel_recommendations.c.id).where(
                self.db.grid_intel_recommendations.c.created_at < cutoff)
            conn.execute(self.db.grid_intel_labels.delete().where(
                self.db.grid_intel_labels.c.recommendation_id.in_(old_ids)))
            conn.execute(self.db.grid_intel_recommendations.delete().where(
                self.db.grid_intel_recommendations.c.created_at < cutoff))

    def label_mature_recommendations(self):
        """Compara mantener y la rama propuesta con las velas reales posteriores."""
        if not self._grid_intel_guard.acquire(blocking=False):
            return {"status": "skipped", "reason": "evaluación de grids activa"}
        started = time.perf_counter()
        now = self.clock()
        cutoff = now - timedelta(days=GRID_INTEL_WINDOW_DAYS)
        try:
            with self.db.engine.connect() as conn:
                rows = conn.execute(select(self.db.grid_intel_recommendations).where(
                    self.db.grid_intel_recommendations.c.created_at <= cutoff).order_by(
                    self.db.grid_intel_recommendations.c.created_at)).mappings().all()
                existing = {int(row[0]) for row in conn.execute(select(
                    self.db.grid_intel_labels.c.recommendation_id)).all()}
            labeled = pending = failed = 0
            for row in rows:
                rec_id = int(row["id"])
                if rec_id in existing:
                    continue
                try:
                    start_at = row["created_at"]
                    if start_at.tzinfo is None:
                        start_at = start_at.replace(tzinfo=timezone.utc)
                    import numpy as np
                    full = self._candles(row["symbol"])
                    end_at = start_at + timedelta(days=GRID_INTEL_WINDOW_DAYS)
                    start_s, end_s = int(start_at.timestamp()), int(end_at.timestamp())
                    left = int(np.searchsorted(full.timestamp, start_s, side="left"))
                    right = int(np.searchsorted(full.timestamp, end_s, side="right"))
                    candles = self._slice(full, left, right)
                    if len(candles.timestamp) < 2 or int(candles.timestamp[-1]) < end_s - 300:
                        pending += 1
                        continue
                    filters = self.filters_resolver(row["symbol"])
                    selected = _json(row["candidate_json"])
                    alternatives = selected.get("all_candidates") or []
                    baseline = _json(row["baseline_json"])
                    proposed = selected
                    if row["action"] == "mantener":
                        proposed = max((item for item in alternatives if item.get("action") != "mantener"
                            and item.get("geometry") and not item.get("discard_reason")),
                            key=lambda item: float(item.get("expected_gain_7d") or -1e9), default=baseline)
                    base_geometry = baseline.get("geometry") or {}
                    proposed_geometry = proposed.get("geometry") or base_geometry
                    capital = float(selected.get("affected_capital") or
                        (selected.get("geometry") or {}).get("affected_capital") or
                        baseline.get("affected_capital") or (baseline.get("geometry") or {}).get("affected_capital") or 0)
                    if capital <= 0:
                        raise ValueError("capital afectado no está guardado")
                    grid = self.db.get_grid(int(row["grid_id"]))
                    params = _json((grid or {}).get("params"))
                    signals = _json(row["signals_json"])
                    inventory_lots = signals.get("inventory_lots") or []
                    base_result = self._simulate_label(candles, row["strategy"], capital,
                        base_geometry, filters, params, inventory_lots)
                    proposed_result = self._simulate_label(candles, row["strategy"], capital,
                        proposed_geometry, filters, params, inventory_lots)
                    payload = {"horizon_days": GRID_INTEL_WINDOW_DAYS,
                        "maintain_gain_usdt": base_result["gain"],
                        "action_gain_usdt": proposed_result["gain"],
                        "gain_difference_usdt": proposed_result["gain"] - base_result["gain"],
                        "maintain_drawdown_pct": base_result["drawdown"],
                        "action_drawdown_pct": proposed_result["drawdown"],
                        "evaluated_action": proposed.get("action"),
                        "scope": "curva total: rama afectada simulada e inventario retenido valorado a bid neto de 0,1 %"}
                    with self.db.engine.begin() as conn:
                        conn.execute(self.db.grid_intel_labels.insert().values(
                            recommendation_id=rec_id, labeled_at=now, status="mature",
                            label_json=json.dumps(payload, ensure_ascii=False)))
                    labeled += 1
                except Exception:
                    failed += 1
                    logger.exception("Falló el etiquetado de recomendación de grid %s", rec_id)
            return {"status": "completed", "labeled": labeled, "pending": pending,
                "failed": failed, "duration_s": round(time.perf_counter() - started, 3)}
        finally:
            self._grid_intel_guard.release()

    @staticmethod
    def _simulate_label(candles, strategy, capital, geometry, filters, params, inventory_lots=()):
        trace = []
        if geometry.get("bot"):
            from grid.sim.meta_bot import run_meta_bot
            bot = run_meta_bot(candles, capital=capital, buy_price=float(geometry["low"]),
                sell_price=float(geometry["high"]), target_usdt=None, filters=filters,
                min_margin_pct=.3, trace_callback=trace.append)
            fee = .001
        else:
            result = run_simulation(candles, strategy=strategy, n=max(4, int(geometry.get("n") or 4)),
            capital=capital, low=float(geometry["low"]), high=float(geometry["high"]),
            params=params if strategy == "smart" else None, filters=filters,
            adjust_enabled_global=False, stoploss_enabled=False, close_wait_enabled=True,
            direction_signal=([1.0] * len(candles.timestamp) if geometry.get("pause_buys") else None),
            trace_callback=trace.append, meta_portfolio_piece=True)
            metric = result["metrics"]
            fee = .001
        values = [float(item.get("equity_incl_dust", item.get("equity", 0)) or 0)
            for item in trace]
        return retained_inventory_metrics(candles.timestamp, candles.close, trace, values,
            inventory_lots, capital, fee_rate=fee)
