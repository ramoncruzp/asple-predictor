"""Operador y estimador aislados del comportamiento de grids normales."""
from __future__ import annotations

import json
import itertools
import math
import threading
import time
import uuid
from datetime import timedelta, datetime, timezone

import numpy as np

from grid.sim.sweep import build_windows, _slice
from grid.sim.runner import run_simulation
from apscheduler.schedulers.background import BackgroundScheduler
from grid.policy import break_prob, DEFAULT_SMART_PARAMS


def summarize_replays(rows, independent_window_days=30):
    """Resume ventanas alineadas; separa cantidad solapada e independiente."""
    hits = [row for row in rows if row.get("reached")]
    times = [float(row["days"]) for row in hits if row.get("days") is not None]
    drawdowns = [float(row.get("drawdown_pct", 0.0)) for row in rows]
    step_days = [float(row.get("step_days", 7.0)) for row in rows]
    step = max(1.0, min(step_days, default=7.0))
    independent = max(1, int(math.ceil(float(independent_window_days) / step)))
    return {"probability": len(hits) / len(rows) if rows else 0.0,
            "days_median": float(np.median(times)) if times else None,
            "days_p10": float(np.percentile(times, 10)) if times else None,
            "days_p90": float(np.percentile(times, 90)) if times else None,
            "worst_drawdown_pct": max(drawdowns, default=0.0),
            "rolling_windows": len(rows),
            "independent_windows": len(rows) // independent,
            "evidence_sufficient": len(rows) // independent >= 3,
            "window_days": float(independent_window_days),
            "evidence_warning": (None if len(rows) // independent >= 3 else
                f"Hay {len(rows) // independent} ventanas independientes de {independent_window_days} días; la evidencia histórica es poca."),
            "low_likelihood": (len(hits) / len(rows) if rows else 0.0) < .25}


def rank_alternatives(alternatives):
    """Criterio común a ambos perfiles: probabilidad y luego mediana de días."""
    return sorted(alternatives, key=lambda row: (-float(row["estimate"]["probability"]),
        float(row["estimate"]["days_median"]) if row["estimate"].get("days_median") is not None else math.inf))


def loss_reallocation_allowed(expected_gain_usdt, realized_loss_usdt, close_fee_usdt, reopen_fee_usdt):
    """Permite rotar solo si la ganancia esperada supera pérdida y ambas comisiones."""
    return float(expected_gain_usdt) > (abs(float(realized_loss_usdt)) +
        float(close_fee_usdt) + float(reopen_fee_usdt))


def actual_close_reallocation_allowed(expected_gain_usdt, close_result, reopen_fee_usdt):
    """Use the verified liquidation P&L, which already includes recorded trade fees."""
    liquidated = close_result.get("liquidated_cells", [])
    realized_pnl = sum(float(row.get("pnl_realized", row.get("cycle_pnl", 0)) or 0)
                       for row in liquidated)
    fees = sum(float(row.get("buy_fee_usdt", 0) or 0) + float(row.get("sell_fee_usdt", 0) or 0)
               for row in liquidated)
    realized_loss = max(0.0, -realized_pnl)
    return float(expected_gain_usdt) > realized_loss + float(reopen_fee_usdt), {
        "realized_pnl_usdt": realized_pnl, "realized_loss_usdt": realized_loss,
        "recorded_trade_fees_usdt": fees, "reopen_fee_estimate_usdt": float(reopen_fee_usdt),
    }


def signal_adjusted_probability(replay_probability, signals, weights, max_abs_logit=1.0):
    """logit(p_meta)=logit(p_replay)+sum(peso_i*señal_i normalizada)."""
    p0 = max(.01, min(.99, float(replay_probability)))
    evidence = sum(float(weights.get(row["name"], 0.25)) * float(row["normalized"])
                   for row in signals)
    cap = max(0.0, float(max_abs_logit))
    evidence = max(-cap, min(cap, evidence))
    logit = math.log(p0 / (1 - p0)) + evidence
    return 1 / (1 + math.exp(-max(-30, min(30, logit))))


def combine_aligned_replays(alternatives, target_usdt, window_days):
    """Suma resultados de piezas por inicio de ventana compartido."""
    maps = []
    for alternative in alternatives:
        maps.append({str(row["window_start"]): row for row in alternative.get("window_results", [])})
    common = set.intersection(*(set(row) for row in maps)) if maps else set()
    rows = []
    for start in sorted(common):
        outcomes = [mapping[start] for mapping in maps]
        pnl = sum(float(row.get("pnl_usdt", 0.0)) for row in outcomes)
        reached_days = [float(row["days"]) for row in outcomes if row.get("days") is not None]
        rows.append({"reached": pnl >= float(target_usdt), "pnl_usdt": pnl,
                     "days": max(reached_days) if reached_days else None,
                     "drawdown_pct": max(float(row.get("drawdown_pct", 0)) for row in outcomes),
                     "step_days": 7})
    return summarize_replays(rows, independent_window_days=window_days), rows


def allocation_vectors(piece_count, per_coin_cap_pct):
    """Repartos en pasos de 10 % y acotados al capital máximo por moneda."""
    cap = int(float(per_coin_cap_pct) // 10) * 10
    max_total = min(100, piece_count * cap)
    allowed_totals = {max_total, max_total - 10}
    result = []
    def visit(values, left):
        if len(values) == piece_count:
            if sum(values) in allowed_totals and sum(values) >= piece_count * 10:
                result.append(tuple(values))
            return
        for share in range(10, min(cap, left - 10 * (piece_count - len(values) - 1)) + 1, 10):
            visit([*values, share], left - share)
    visit([], 100)
    return result


def profile_candidate_exclusion(profile, sigma, q33, q67, model_ready):
    """Retorna solo exclusiones propias del perfil; Scanner mantiene sus gates."""
    if sigma is None:
        return "sigma realizada no disponible"
    if str(profile).upper() == "PRUDENTE":
        if q33 is None or q67 is None:
            return "evidencia insuficiente para terciles (mínimo 3 candidatas)"
        if not float(q33) <= float(sigma) <= float(q67):
            return "sigma fuera del tercil medio Prudente"
        if not model_ready:
            return "Prudente requiere readiness y al menos 50 verificaciones"
    return None


class MetaOperator:
    """Planes de cinco minutos, análisis asíncrono y revisiones de quince minutos."""
    PLAN_TTL_SECONDS = 300

    def __init__(self, core, db, scanner, engine, settings, *, advisor=None, alert=None,
                 clock=time.time, price_drift_pct=1.0):
        self.core, self.db, self.scanner = core, db, scanner
        self.engine, self.settings, self.advisor = engine, settings, advisor
        self.alert = alert or (lambda *_args: None)
        self.clock, self.price_drift_pct = clock, float(price_drift_pct)
        self._lock = threading.RLock()
        self._jobs = {}
        self._plans = {}
        self._cache = {}
        self._candles_cache = {}
        self._above_ceiling_count = {}
        self._hard_filter_misses = {}
        self._thread = None
        self._opener = None
        self.scheduler = BackgroundScheduler(timezone="UTC")
        self._operation_lock = getattr(getattr(self.core, "monitor", None), "ops_lock", None) or threading.RLock()

    def _ledger(self, meta_id, event, details, **kwargs):
        self.core._ledger(meta_id, None, event, details, **kwargs)

    def analyze(self, meta_id):
        goal = self.core.get_goal(meta_id)
        if goal["status"] != "DRAFT":
            raise ValueError("El análisis solo está disponible para una meta DRAFT.")
        job_id = uuid.uuid4().hex
        with self._lock:
            self._jobs[job_id] = {"status": "PENDING", "meta_id": int(meta_id), "created_at": self.clock()}
            self._thread = threading.Thread(target=self._run_analysis, args=(job_id, goal), daemon=True,
                                            name=f"meta-analysis-{meta_id}")
            self._thread.start()
        return {"job_id": job_id, "status": "PENDING"}

    def job(self, job_id):
        with self._lock:
            row = self._jobs.get(str(job_id))
            if row is None:
                raise ValueError("Análisis no encontrado.")
            return dict(row)

    def _run_analysis(self, job_id, goal):
        started = time.perf_counter()
        try:
            data = self._build_plan(goal)
            plan_id = uuid.uuid4().hex
            now = self.clock()
            data.update({"plan_id": plan_id, "meta_id": int(goal["id"]),
                         "created_at": now, "expires_at": now + self.PLAN_TTL_SECONDS,
                         "analysis_seconds": round(time.perf_counter() - started, 4)})
            with self._lock:
                self._plans[plan_id] = data
                self._jobs[job_id] = {"job_id": job_id, "meta_id": int(goal["id"]),
                                      "status": "COMPLETE", "plan": data}
        except Exception as exc:
            with self._lock:
                self._jobs[job_id] = {"job_id": job_id, "status": "FAILED", "error": str(exc),
                                      "analysis_seconds": round(time.perf_counter() - started, 4)}

    def _candidate_alternatives(self, row, capital, target_usdt, cfg):
        symbol = row["symbol"]
        book = self.scanner.client.get_book_ticker(symbol)
        price = (float(book["bid_price"]) + float(book["ask_price"])) / 2
        row = {**row, "book": book}
        margins = [float(cfg.get("min_margin_pct", .3)) + extra for extra in (0, .2, .4)]
        advisor_rows = []
        if self.advisor is not None:
            for margin in margins:
                key = (symbol, round(float(capital), 4), round(float(margin), 4),
                       int(cfg.get("max_days", 20)))
                if key in self._advisor_cache:
                    advisor_rows.append({"margin_target_pct": margin,
                        "recommendation": self._advisor_cache[key], "cached": True})
                    continue
                try:
                    timer = time.perf_counter()
                    advisory = self.advisor(symbol, capital, margin, int(cfg.get("max_days", 20)))
                    self._analysis_timing["advisor_s"] += time.perf_counter() - timer
                    self._advisor_cache[key] = advisory
                    advisor_rows.append({"margin_target_pct": margin, "recommendation": advisory})
                except Exception as exc:
                    advisor_rows.append({"margin_target_pct": margin, "error": str(exc)})
        else:
            structure = row.get("suggested_structure") or {}
            advisor_rows.append({"margin_target_pct": float(cfg.get("min_margin_pct", .3)),
                "recommendation": {"recommended_floor": structure.get("range_low"),
                    "recommended_ceiling": structure.get("range_high"),
                    "suggested_grids": structure.get("n_levels", 4),
                    "suggested_strategy": "simple", "net_per_cycle_after_dust_usdt":
                    structure.get("net_edge_pct_per_cycle", 0)}})
        alternatives = []
        for advisor_row in advisor_rows:
            advisory = advisor_row.get("recommendation") or {}
            low, high = advisory.get("recommended_floor"), advisory.get("recommended_ceiling")
            if low is None or high is None or not float(low) < price < float(high):
                continue
            levels = min(int(advisory.get("suggested_grids") or 4), int(float(capital) // 5.5))
            planned = levels if levels >= 4 else 1
            strategies = ("simple", "smart") if planned >= 4 else ("individual_bot",)
            for strategy in strategies:
                alternative = self._evaluate(row, strategy, capital, target_usdt, low, high,
                    planned, cfg, margin_pct=advisor_row["margin_target_pct"])
                alternative["mid_at_plan"] = price
                alternative["advisor"] = advisory
                alternative["advisor_margin_target_pct"] = advisor_row["margin_target_pct"]
                for piece in alternative["pieces"]:
                    piece["mid_at_plan"] = price
                self._apply_intelligence(alternative, row, price, low, high, cfg,
                    advisor_result=advisory if self.advisor is not None else None)
                alternatives.append(alternative)
        return alternatives, [{"symbol": symbol, **item} for item in advisor_rows]

    def _build_plan(self, goal):
        timing_started = time.perf_counter()
        self._analysis_timing = {"advisor_s": 0.0, "replay_s": 0.0}
        self._advisor_cache = {}
        scan = self.scanner.scan(capital=float(goal["capital_initial"]), with_sim=False)
        scan_seconds = time.perf_counter() - timing_started
        candidates = list(scan.get("results", []))
        preparation_started = time.perf_counter()
        active_rows = self.db.get_active_coins()
        active = {str(row["symbol"]).upper() for row in active_rows}
        usable = [row for row in candidates if row.get("eligible") and row.get("symbol") in active]
        sigmas = sorted(float(row["sigma_24h_open"]) for row in usable
                        if row.get("sigma_24h_open") is not None and float(row["sigma_24h_open"]) > 0)
        profile = str(goal["profile"]).upper()
        cfg = dict(goal.get("config_snapshot") or {})
        sigma_cfg = cfg.get("sigma_quantiles") or [cfg.get("sigma_q33", 1/3), cfg.get("sigma_q67", 2/3)]
        q33, q67 = (np.quantile(sigmas, sigma_cfg).tolist() if len(sigmas) >= 3 else (None, None))
        prepared = []
        for row in candidates:
            symbol = row.get("symbol")
            sigma = row.get("sigma_24h_open")
            reason = None
            if not row.get("eligible"):
                reason = "; ".join(row.get("reasons") or [row.get("error") or "filtro duro Scanner"])
            elif str(symbol).upper() not in active:
                reason = "moneda inactiva o ausente de Coin Registry"
            else:
                model_ready = self._model_ready(symbol) if profile == "PRUDENTE" else False
                reason = profile_candidate_exclusion(profile, sigma, q33, q67, model_ready)
            if reason is None:
                prepared.append({**row, "scanner_eligible": True, "book_state": "two_sided",
                                 "model_statistics": self._model_snapshot(symbol), "excluded_reason": None})
            else:
                prepared.append({"symbol": symbol, "eligible": bool(row.get("eligible")),
                                 "scanner_eligible": bool(row.get("eligible")),
                                 "hard_filters": row.get("hard_filters", []),
                                 "sigma_24h": sigma, "score": row.get("score"),
                                 "book_state": "two_sided" if row.get("eligible") else "excluded",
                                 "model_statistics": self._model_snapshot(symbol) if symbol else None,
                                 "excluded_reason": reason})
        eligible = [r for r in prepared if r.get("excluded_reason") is None]
        preparation_seconds = time.perf_counter() - preparation_started
        if not eligible:
            raise ValueError("Ninguna moneda cumple los límites del perfil.")
        capital = float(goal["capital_initial"])
        target_usdt = float(goal["target_usdt"])
        portfolio_started = time.perf_counter()
        shortlist = sorted(eligible, key=lambda item: float(item.get("score") or 0), reverse=True)[:3]
        alternatives, advisor_comparisons, evaluated_combinations = [], [], []
        profile_cap = float(cfg.get("capital_per_coin_pct", 100))
        option_cache = {}
        max_pieces = max(1, min(3, int(cfg.get("max_grids", 1)) + int(cfg.get("max_bots", 0))))
        for piece_count in range(1, max_pieces + 1):
            target_piece = target_usdt / piece_count
            for selected_rows in itertools.combinations(shortlist, piece_count):
                symbols = [row["symbol"] for row in selected_rows]
                for shares in allocation_vectors(piece_count, profile_cap):
                    combination = []
                    for row, share in zip(selected_rows, shares):
                        cache_key = (row["symbol"], share, piece_count)
                        if cache_key not in option_cache:
                            options, comparisons = self._candidate_alternatives(row,
                                capital * share / 100, target_piece, cfg)
                            option_cache[cache_key] = rank_alternatives(options)
                            advisor_comparisons.extend(item for item in comparisons if not item.get("cached"))
                        options = option_cache[cache_key]
                        if not options:
                            combination = []
                            break
                        combination.append(options[0])
                    if len(combination) != piece_count:
                        continue
                    grid_count = sum(item["pieces"][0]["piece_type"] == "grid" for item in combination)
                    bot_count = piece_count - grid_count
                    if grid_count > int(cfg.get("max_grids", 0)) or bot_count > int(cfg.get("max_bots", 0)):
                        continue
                    estimate, combined_rows = combine_aligned_replays(combination, target_usdt,
                        int(cfg.get("max_days", 20)))
                    if not combined_rows:
                        continue
                    signals = []
                    for name in ("volatility", "break_prob", "direction", "scanner", "advisor"):
                        values = [(float(item["pieces"][0]["capital_usdt"]), signal)
                            for item in combination for signal in item.get("signals_used", [])
                            if signal["name"] == name]
                        if values:
                            denominator = sum(amount for amount, _ in values)
                            signals.append({"name": name, "normalized": sum(amount * signal["normalized"]
                                for amount, signal in values) / denominator,
                                "value_by_piece": [signal.get("value") for _, signal in values]})
                    replay_probability = estimate["probability"]
                    cap = float(cfg.get("signal_logit_cap", 1.0))
                    probability = (signal_adjusted_probability(replay_probability, signals,
                        cfg.get("signal_weights") or {}, cap) if estimate["evidence_sufficient"]
                        else replay_probability)
                    estimate.update({"probability_replay": replay_probability,
                        "probability_adjusted": probability, "probability": probability,
                        "signal_logit_adjustment_cap": cap, "low_likelihood": probability < .25})
                    shadow_signals = [signal for signal in signals if signal["name"] in {"scanner", "advisor"}]
                    shadow = (signal_adjusted_probability(replay_probability, shadow_signals,
                        cfg.get("signal_weights") or {}, cap) if estimate["evidence_sufficient"]
                        else replay_probability)
                    pieces = [dict(item["pieces"][0]) for item in combination]
                    row = {"symbol": pieces[0]["symbol"], "symbols": symbols,
                        "strategy": ",".join(item["strategy"] for item in combination),
                        "pieces": pieces, "estimate": estimate, "replay_probability": replay_probability,
                        "window_results": combined_rows, "signals_used": signals,
                        "signals_omitted": [entry for item in combination for entry in item.get("signals_omitted", [])],
                        "shadow_probability": shadow, "shadow_signals": shadow_signals,
                        "advisor_recommendations": [item.get("advisor") for item in combination],
                        "reason": f"cartera de {piece_count} pieza(s), reparto {shares} %"}
                    alternatives.append(row)
                    evaluated_combinations.append({"symbols": symbols, "shares_pct": shares,
                        "piece_types": [piece["piece_type"] for piece in pieces],
                        "probability_replay": replay_probability, "probability_adjusted": probability,
                        "days_median": estimate["days_median"], "days_p10": estimate["days_p10"],
                        "days_p90": estimate["days_p90"],
                        "independent_windows": estimate["independent_windows"],
                        "worst_drawdown_pct": estimate["worst_drawdown_pct"]})
        if not alternatives:
            raise ValueError("No hay estructuras del Advisor para comparar.")
        alternatives = rank_alternatives(alternatives)
        shadow = sorted(alternatives, key=lambda row: (-row["shadow_probability"],
            row["estimate"].get("days_median") if row["estimate"].get("days_median") is not None else math.inf))[0]
        best = alternatives[0]
        pieces = best["pieces"]
        control_started = time.perf_counter()
        control_plan = self._control_plan(best, capital)
        if self.advisor is not None:
            try:
                control_advisor = self.advisor(pieces[0]["symbol"], capital,
                    float(cfg.get("min_margin_pct", .3)), int(cfg.get("max_days", 20)))
                control_plan.update({"advisor_recommendation": control_advisor,
                    "range_low": control_advisor.get("recommended_floor"),
                    "range_high": control_advisor.get("recommended_ceiling"),
                    "n_levels": control_advisor.get("suggested_grids", 4),
                    "smart_comparison": (control_advisor.get("simulations", {}).get("strategies", {}).get("smart"))})
            except Exception as exc:
                control_plan["warning"] = f"Advisor de control no disponible: {exc}"
        control_seconds = time.perf_counter() - control_started
        selected_public = []
        for item in alternatives:
            if len(item.get("pieces", [])) == 1 or len(selected_public) < 30:
                selected_public.append(item)
        public_alternatives = [{key: value for key, value in item.items() if key != "window_results"}
                               for item in selected_public]
        plan = {"profile": profile, "candidates": prepared, "sigma_bands": {"q33": q33, "q67": q67},
                "alternatives": public_alternatives, "combinations_evaluated": evaluated_combinations,
                "pieces": pieces, "estimated_time": best["estimate"],
                "advisor_comparisons": advisor_comparisons,
                "timings_s": {"scanner": round(scan_seconds, 3), "candidate_filter": round(preparation_seconds, 3),
                    "advisor": round(self._analysis_timing["advisor_s"], 3),
                    "replays": round(self._analysis_timing["replay_s"], 3),
                    "portfolio_selection": round(time.perf_counter() - portfolio_started - control_seconds, 3),
                    "control_advisor": round(control_seconds, 3)},
                "warnings": (["El replay no modela los pr?stamos de grids Smart; la estimaci?n no incluye capital prestado."]
                    if any(piece["strategy"] == "smart" for piece in pieces) else []),
                "target_usdt": float(goal["target_usdt"]), "loss_max_usdt": capital * float(cfg.get("max_loss_pct", 0)) / 100,
                "deadline_days": int(cfg.get("max_days", 0)),
                "selection_reason": "mayor probabilidad de alcanzar la meta; empate por menor mediana de días",
                "signals_used": best.get("signals_used", []),
                "signals_omitted": best.get("signals_omitted", []),
                "historical_evidence_note": best["estimate"].get("evidence_warning"),
                "shadow_decision": {"symbol": shadow["symbol"], "strategy": shadow["strategy"],
                    "probability": shadow["shadow_probability"], "signals": shadow["shadow_signals"]},
                "control_plan": control_plan,
                "probability_under_25_pct": best["estimate"]["low_likelihood"]}
        self._ledger(goal["id"], "META_PLAN_EVALUATED", {"alternatives": public_alternatives,
                      "advisor_comparisons": advisor_comparisons,
                      "combinations_evaluated": evaluated_combinations,
                      "shadow_decision": plan["shadow_decision"],
                      "selected": {key: value for key, value in best.items() if key != "window_results"},
                      "sigma_bands": plan["sigma_bands"]})
        return plan

    def _model_snapshot(self, symbol):
        readiness = self.db.get_readiness(symbol) or {}
        query = getattr(self.db, "get_latest_vol_model_stats_forecast_at", None)
        latest = query(symbol, 24) if query else None
        stats_rows = getattr(self.db, "get_vol_model_stats_rows", lambda *_: [])(symbol, 24)
        verified_times = {str(row.get("forecast_at")) for row in stats_rows
                          if row.get("realized_logvol") is not None and row.get("verified_at") is not None}
        effective = len(verified_times) / 24
        forecasts = {}
        now = datetime.now(timezone.utc)
        for item in stats_rows:
            when = item.get("forecast_at")
            if when is None:
                continue
            when = when.replace(tzinfo=timezone.utc) if when.tzinfo is None else when
            age = (now - when).total_seconds()
            name = str(item.get("model_name"))
            if name not in forecasts or when > forecasts[name]["_when"]:
                forecasts[name] = {"sigma_24h": (math.exp(float(item["pred_logvol_cal"]))
                                   if item.get("pred_logvol_cal") is not None else None),
                                   "forecast_at": when.isoformat(), "age_seconds": age,
                                   "is_champion": bool(item.get("is_champion")), "_when": when}
        for item in forecasts.values():
            item.pop("_when", None)
            if item["age_seconds"] < 0 or item["age_seconds"] > 7200:
                item["used"] = False
                item["omission_reason"] = "forecast stale or future-dated"
            else:
                item["used"] = item.get("sigma_24h") is not None
        return {"readiness": readiness.get("state"), "effective_verifications": effective,
                "latest_forecast_at": None if latest is None else str(latest),
                "complete": bool(readiness.get("state") == "lista" and latest is not None),
                "volatility_forecasts": forecasts}

    def _model_ready(self, symbol):
        data = self._model_snapshot(symbol)
        try:
            count = float(data.get("effective_verifications") or 0)
        except (TypeError, ValueError):
            count = 0
        return data["complete"] and count >= 50

    def _evaluate(self, row, strategy, capital, target_usdt, low, high, n, cfg, *,
                  margin_pct=None):
        symbol = row["symbol"]
        # Historial público del Scanner; la ventana sigue el plazo del perfil y avanza 7d.
        candle_key = (symbol, self.clock() // 86400)
        candles = self._candles_cache.get(candle_key)
        if candles is None:
            candles_raw = self.scanner.client.get_historical_klines(symbol, "5m", 180)
            from grid.sim.data import CandleData
            import pandas as pd
            frame = candles_raw if isinstance(candles_raw, pd.DataFrame) else pd.DataFrame(candles_raw)
            timestamps = pd.to_datetime(frame["timestamp"], utc=True).astype("int64").to_numpy() // 1_000_000_000
            candles = CandleData(timestamp=timestamps.astype("int64"), open=frame["open"].to_numpy(float),
                                 high=frame["high"].to_numpy(float), low=frame["low"].to_numpy(float),
                                 close=frame["close"].to_numpy(float), gaps=0)
            self._candles_cache[candle_key] = candles
        window_days = int(cfg.get("max_days", 20))
        key = (symbol, self.clock() // 86400, strategy, round(target_usdt, 4), n,
               round(capital, 4), window_days, round(float(low), 8), round(float(high), 8))
        cached = self._cache.get(key)
        if cached is None:
            replay_started = time.perf_counter()
            windows = build_windows(candles, window_days, 7)
            rows, window_results = [], []
            for window in windows:
                sample = _slice(candles, window["start_idx"], window["end_idx"])
                start_ts = datetime.fromtimestamp(int(window["start"]), timezone.utc).date().isoformat()
                if strategy == "individual_bot":
                    from grid.sim.meta_bot import run_meta_bot
                    replay = run_meta_bot(sample, capital=capital, buy_price=low, sell_price=high,
                                          target_usdt=target_usdt, fee_pct=.1)
                    result_row = {"window_start": start_ts, "reached": replay["reached"],
                                  "days": replay["days"], "pnl_usdt": replay["pnl_usdt"],
                                  "drawdown_pct": replay["drawdown_pct"], "step_days": 7}
                else:
                    params = {"target_usdt": target_usdt, "target_basis": "cash"}
                    simulation = run_simulation(sample, strategy=strategy, n=n, capital=capital,
                        low=low, high=high, width_pct=None, params=params, fee_pct=.1,
                        close_wait_enabled=True, adjust_enabled_global=False, stoploss_enabled=False)
                    event = next((e for e in simulation["events"] if e.get("type") == "TARGET_REACHED"), None)
                    days = None if event is None else (int(event["ts"]) - int(sample.timestamp[0])) / 86400
                    metrics = simulation["metrics"]
                    result_row = {"window_start": start_ts, "reached": event is not None,
                                  "days": days,
                                  "pnl_usdt": float(metrics.get("equity_final_after_repository_liquidation", capital)) - capital,
                                  "drawdown_pct": max(0.0, float(metrics.get("max_drawdown_pct", 0))),
                                  "step_days": 7}
                rows.append(result_row)
                window_results.append(result_row)
            cached = {"summary": summarize_replays(rows, independent_window_days=window_days),
                      "window_results": window_results}
            self._cache[key] = cached
            self._analysis_timing["replay_s"] += time.perf_counter() - replay_started
        cached = json.loads(json.dumps(cached))
        window_results = cached["window_results"]
        estimate = cached["summary"]
        piece_type = "individual_bot_grid" if strategy == "individual_bot" else "grid"
        piece_levels = max(4, n) if piece_type == "grid" else 1
        strategy_name = "simple" if piece_type == "individual_bot_grid" else strategy
        pieces = [{"symbol": symbol, "strategy": strategy_name,
                   "piece_type": piece_type,
                   "capital_usdt": capital, "n_levels": piece_levels, "floor": low, "ceiling": high,
                   "target_usdt": float(target_usdt),
                   "mid_at_plan": (float((row.get("book") or {}).get("bid_price", 0)) +
                                   float((row.get("book") or {}).get("ask_price", 0))) / 2 or
                                  (float(low) + float(high)) / 2,
                   "margin_pct": float(margin_pct if margin_pct is not None else cfg.get("min_margin_pct", .7)),
                   "loans_pct": float(cfg.get("loan_cap_pct", 0)) if strategy_name == "smart" else 0}]
        return {"symbol": symbol, "strategy": strategy_name, "pieces": pieces, "estimate": estimate,
                "scanner_score": row.get("score"), "signals_used": [],
                "replay_probability": estimate["probability"], "window_results": window_results,
                "shadow_decision": {"scanner_score": row.get("score"), "advisor_strategy": strategy},
                "reason": "alternativa de replay histórico"}

    def _apply_intelligence(self, alternative, row, price, low, high, cfg, *, advisor_result=None):
        """Ajuste logit aditivo: logit(p_meta)=logit(p_replay)+sum(peso*señal)."""
        signals, omitted = [], []
        model = row.get("model_statistics") or self._model_snapshot(row["symbol"])
        usable_sigmas = [float(value["sigma_24h"]) for value in (model.get("volatility_forecasts") or {}).values()
                         if value.get("used") and value.get("sigma_24h") is not None]
        if usable_sigmas and row.get("sigma_24h_open"):
            forecast_sigma = float(np.median(usable_sigmas))
            realized = float(row["sigma_24h_open"])
            signals.append({"name": "volatility", "value": forecast_sigma,
                            "normalized": max(-1.0, min(1.0, math.log(max(1e-9, forecast_sigma / realized)))),
                            "models": model.get("volatility_forecasts")})
        else:
            stale = [name for name, value in (model.get("volatility_forecasts") or {}).items()
                     if not value.get("used")]
            omitted.append({"name": "volatility", "reason": "no hay pronóstico fresco de modelos activos",
                            "models_omitted": stale})
        try:
            params = dict(DEFAULT_SMART_PARAMS)
            params["min_free_cells"] = min(params["min_free_cells"], 1)
            sigma_for_break = float(np.median(usable_sigmas)) if usable_sigmas else float(row.get("sigma_24h_open") or 0)
            probability = break_prob(price, low, high, sigma_for_break, params)[0]
            signals.append({"name": "break_prob", "value": probability,
                            "sigma_24h": sigma_for_break,
                            "normalized": max(-1.0, min(1.0, 1 - 2 * float(probability)))})
        except Exception as exc:
            omitted.append({"name": "break_prob", "reason": str(exc)})
        prediction = self.db.get_latest_prediction(row["symbol"], "1h", "model_a_down")
        fresh_direction = False
        if prediction and prediction.get("predicted_at") is not None:
            predicted_at = prediction["predicted_at"]
            predicted_at = predicted_at.replace(tzinfo=timezone.utc) if predicted_at.tzinfo is None else predicted_at
            fresh_direction = 0 <= (datetime.now(timezone.utc) - predicted_at).total_seconds() <= 7200
        if fresh_direction and prediction.get("probability_up") is not None:
            p_up = float(prediction["probability_up"])
            direction = max(-1.0, min(1.0, (p_up - .5) / .1)) if p_up >= .60 else 0.0
            signals.append({"name": "direction", "value": p_up, "threshold": .60, "normalized": direction})
        else:
            omitted.append({"name": "direction", "reason": "predicción ausente o con más de 2 h"})
        score = row.get("score")
        if score is not None:
            signals.append({"name": "scanner", "value": score,
                            "normalized": max(-1.0, min(1.0, 2 * float(score) - 1))})
        else:
            omitted.append({"name": "scanner", "reason": "score ausente"})
        advisor_value = (advisor_result or {}).get("net_per_cycle_after_dust_usdt")
        if advisor_value is not None:
            advisor_value = float(advisor_value) / max(1.0, float(alternative["pieces"][0]["capital_usdt"])) * 100
            signals.append({"name": "advisor", "value": advisor_value,
                            "normalized": max(-1.0, min(1.0, float(advisor_value) / 2.0))})
        else:
            omitted.append({"name": "advisor", "reason": "recomendación sin margen neto"})
        weights = (cfg.get("signal_weights") or {})
        enough_evidence = bool(alternative["estimate"].get("evidence_sufficient"))
        logit_cap = float(cfg.get("signal_logit_cap", 1.0))
        adjusted = (signal_adjusted_probability(alternative["replay_probability"], signals, weights, logit_cap)
                    if enough_evidence else float(alternative["replay_probability"]))
        shadow_signals = [item for item in signals if item["name"] in {"scanner", "advisor"}]
        shadow_probability = (signal_adjusted_probability(alternative["replay_probability"],
            shadow_signals, weights, logit_cap) if enough_evidence else float(alternative["replay_probability"]))
        alternative["signals_used"] = signals
        alternative["signals_omitted"] = omitted
        alternative["estimate"]["probability_adjusted"] = adjusted
        alternative["estimate"]["probability_replay"] = alternative["replay_probability"]
        alternative["estimate"]["probability"] = adjusted
        alternative["estimate"]["signal_logit_adjustment_cap"] = logit_cap
        alternative["estimate"]["low_likelihood"] = adjusted < .25
        alternative["shadow_probability"] = shadow_probability
        alternative["shadow_signals"] = shadow_signals
        alternative["shadow_decision"] = {"probability": shadow_probability,
            "scanner_score": score, "advisor_net_edge_pct_per_cycle": advisor_value}

    def _control_plan(self, best, capital):
        return {"type": "normal_smart_advisor_control", "symbol": best["symbol"], "capital_usdt": capital,
                "strategy": "smart", "same_analysis_date": True, "opened": False,
                "range_low": best["pieces"][0]["floor"], "range_high": best["pieces"][0]["ceiling"],
                "n_levels": max(4, int(best["pieces"][0]["n_levels"]))}

    def start_plan(self, meta_id, plan_id, opener):
        with self._operation_lock:
            self._opener = opener
            return self._start_plan_locked(meta_id, plan_id, opener)

    def _start_plan_locked(self, meta_id, plan_id, opener):
        with self._lock:
            plan = self._plans.get(str(plan_id))
        if plan is None or int(plan.get("meta_id", -1)) != int(meta_id):
            raise ValueError("Plan no encontrado; solicite un análisis nuevo.")
        if self.clock() > plan["expires_at"]:
            raise ValueError("Plan vencido; solicite un análisis nuevo.")
        goal = self.core.get_goal(meta_id)
        cfg = goal.get("config_snapshot") or {}
        max_drift = float(cfg.get("plan_price_drift_pct", self.price_drift_pct))
        for symbol, planned in self._plan_prices(plan).items():
            current = self.scanner.client.get_book_ticker(symbol)
            mid = (float(current["bid_price"]) + float(current["ask_price"])) / 2
            if abs(mid / planned - 1) * 100 > max_drift:
                raise ValueError(f"El precio de {symbol} se movió más de {max_drift:g}%; solicite un análisis nuevo.")
        self.core.start_goal(meta_id)
        run_id = self._active_run_id(meta_id)
        with self.db.engine.begin() as conn:
            conn.execute(self.db.meta_runs.update().where(self.db.meta_runs.c.id == run_id).values(
                control_plan_json=json.dumps(plan.get("control_plan"), sort_keys=True)))
        opened = []
        for piece in plan["pieces"]:
            try:
                if piece["piece_type"] == "individual_bot_grid":
                    result = self.engine.create_grid(piece["symbol"], piece["floor"], piece["ceiling"], 1,
                        capital=piece["capital_usdt"], strategy="simple", meta_id=int(meta_id),
                        meta_piece_type="individual_bot_grid")
                    result = {"grid_id": int(result["id"]), "symbol": piece["symbol"]}
                else:
                    result = opener(piece)
                grid_id = int(result["grid_id"])
                self.core.link_grid(meta_id, grid_id, piece["piece_type"])
                opened.append({**piece, "grid_id": grid_id})
                self._ledger(meta_id, "META_OPERATOR_OPEN", {"piece": piece, "grid_id": grid_id})
                self.alert("META_PIECE_OPENED", meta_id, {"symbol": piece["symbol"], "reason": "inicio del plan Meta"})
            except Exception as exc:
                self._ledger(meta_id, "META_OPERATOR_OPEN_FAILED", {"piece": piece, "error": str(exc)})
        if not opened:
            with self.db.engine.begin() as conn:
                conn.execute(self.db.meta_goals.update().where(self.db.meta_goals.c.id == int(meta_id)).values(status="DRAFT", started_at=None))
                conn.execute(self.db.meta_runs.update().where(self.db.meta_runs.c.id == run_id).values(status="NO_PIECES", finished_at=self.core._now()))
            raise ValueError("No se pudo abrir ninguna pieza; la meta volvió a DRAFT.")
        return {"meta": self.core.get_goal(meta_id), "opened": opened, "failed": len(plan["pieces"]) - len(opened)}

    def _plan_prices(self, plan):
        prices = {}
        for piece in plan["pieces"]:
            prices.setdefault(piece["symbol"], float(piece.get("mid_at_plan") or
                ((float(piece["floor"]) + float(piece["ceiling"])) / 2)))
        return prices

    def _active_run_id(self, meta_id):
        from sqlalchemy import select
        with self.db.engine.connect() as conn:
            row = conn.execute(select(self.db.meta_runs.c.id).where(self.db.meta_runs.c.meta_id == int(meta_id),
                self.db.meta_runs.c.status == "ACTIVE").order_by(self.db.meta_runs.c.id.desc())).first()
        return None if row is None else int(row[0])

    def review_once(self, meta_id):
        with self._operation_lock:
            return self._review_once_locked(meta_id)

    def _review_once_locked(self, meta_id):
        goal = self.core.get_goal(meta_id)
        value = self.core.valuation(meta_id)
        if goal["status"] != "ACTIVE" or value.get("status") != "KNOWN":
            result = {"decision": "no_hacer_nada", "reason": "meta no activa o valoración DESCONOCIDO"}
            self._ledger(meta_id, "META_OPERATOR_NOOP", result)
            self._ledger(meta_id, "META_OPERATOR_REVIEW_DECISION", {
                "decision": result, "shadow_decision": None, "valuation": value})
            return result
        cfg = goal.get("config_snapshot") or {}
        if float(value["result_pct"]) <= -float(cfg.get("warning_loss_pct", 0)):
            result = {"decision": "no_hacer_nada", "reason": "bloqueo 75% del límite de pérdida"}
        else:
            result = self._review_allocations(goal, value)
        self._ledger(meta_id, "META_OPERATOR_REVIEW_DECISION", {
            "decision": result, "shadow_decision": getattr(self, "_last_review_shadow", None),
            "valuation": value})
        if result.get("decision") == "no_hacer_nada":
            self._ledger(meta_id, "META_OPERATOR_NOOP", {**result, "valuation": value})
        return result

    def _review_allocations(self, goal, valuation):
        """Compara alternativa y pieza activa; toda rotación liquida por el cierre verificado."""
        links = self.core._owned_grids(int(goal["id"]))
        try:
            plan = self._build_plan(goal)
        except Exception as exc:
            self._last_review_shadow = None
            return {"decision": "no_hacer_nada", "reason": f"análisis de revisión no disponible: {exc}"}
        self._last_review_shadow = plan.get("shadow_decision")
        best = next((item for item in plan["alternatives"]
                     if len(item.get("pieces", [])) == 1
                     and item["estimate"].get("evidence_sufficient")), None)
        if best is None:
            return {"decision": "no_hacer_nada", "reason": "evidencia independiente insuficiente"}
        scan_rows = {row.get("symbol"): row for row in plan["candidates"]}
        for link in links:
            grid = self.db.get_grid(int(link["grid_id"]))
            if grid is None or grid.get("status") not in {"ACTIVE", "PAUSED"}:
                continue
            levels = self.db.get_grid_levels(int(grid["id"]))
            has_inventory = any(float(level.get("held_qty") or 0) > 0 for level in levels)
            book = self.engine.exchange.get_book_ticker(grid["symbol"])
            bid = float(book.get("bid_price") or 0)
            above = bid > float(grid["range_high"])
            scan_row = scan_rows.get(grid["symbol"], {})
            hard_fail = not bool(scan_row) or not bool(scan_row.get("scanner_eligible"))
            key = (int(goal["id"]), int(grid["id"]))
            self._above_ceiling_count[key] = self._above_ceiling_count.get(key, 0) + 1 if above else 0
            self._hard_filter_misses[key] = self._hard_filter_misses.get(key, 0) + 1 if hard_fail else 0
            must_withdraw = self._above_ceiling_count[key] >= 2 or self._hard_filter_misses[key] >= 2
            is_alternative = best["symbol"] != grid["symbol"] or best["strategy"] != grid["strategy"]
            loss = 0.0
            close_fee = 0.0
            if has_inventory and is_alternative:
                loss = sum(max(0.0, float(level.get("entry_price") or level["price"]) * float(level["held_qty"]) -
                    float(level["held_qty"]) * bid * .999) for level in levels if float(level.get("held_qty") or 0) > 0)
                close_fee = sum(float(level.get("held_qty") or 0) * bid * .001
                                for level in levels if float(level.get("held_qty") or 0) > 0)
            expected = float(best["estimate"]["probability"]) * float(goal["target_usdt"])
            reopen_fee = float(grid["capital_total"]) * .001
            acceptable = loss_reallocation_allowed(expected, loss, close_fee, reopen_fee)
            current_alternative = next((item for item in plan["alternatives"]
                if item["symbol"] == grid["symbol"] and item["strategy"] == grid["strategy"]), None)
            current_p90 = math.inf if current_alternative is None else float(
                current_alternative["estimate"].get("days_p90") or math.inf)
            time_faster = (best["estimate"].get("days_median") is not None and
                (best["estimate"].get("days_p90") or math.inf) < current_p90)
            reason = ("precio sobre techo en dos revisiones" if self._above_ceiling_count[key] >= 2 else
                      "fallo de filtros duros del Scanner en dos revisiones" if self._hard_filter_misses[key] >= 2 else
                      "alternativa estimada más rápida y ganancia esperada cubre pérdida y comisiones")
            if must_withdraw and has_inventory and (self._hard_filter_misses[key] >= 2 or
                                                     self._above_ceiling_count[key] >= 2):
                self.engine.cancel_grid_orders(int(grid["id"]), finalize=False, buy_only=True)
                self._ledger(goal["id"], "META_OPERATOR_WITHDRAW", {"reason": reason,
                    "scanner": scan_row, "price_bid": bid,
                    "decision": "cancelar_compras_conservar_ventas"}, grid_id=int(grid["id"]))
                self.alert("META_OPERATOR_WITHDRAW", goal["id"], {"symbol": grid["symbol"], "reason": reason})
                return {"decision": "retirar_con_ventas_vivas", "reason": reason,
                        "grid_id": int(grid["id"]), "capital_reusable": False}
            if must_withdraw and not has_inventory:
                close = self.engine.close_grid(int(grid["id"]), "cancel", close_reason="META_OPERATOR_WITHDRAW")
                if close.get("status") != "CLOSED":
                    return {"decision": "no_hacer_nada", "reason": "el retiro verificado quedó pendiente",
                            "grid_id": int(grid["id"]), "close": close}
                self._ledger(goal["id"], "META_OPERATOR_WITHDRAW", {"reason": reason,
                    "scanner": scan_row, "price_bid": bid}, grid_id=int(grid["id"]))
                self.alert("META_OPERATOR_WITHDRAW", goal["id"], {"symbol": grid["symbol"], "reason": reason})
                replacement = best
                if self._above_ceiling_count[key] >= 2 and scan_row.get("eligible"):
                    replacement = self._recenter_alternative(best, grid, bid)
                try:
                    opened = self._open_replacement(goal, replacement, float(grid["capital_total"]))
                except Exception as exc:
                    self._ledger(goal["id"], "META_OPERATOR_OPEN_FAILED", {"reason": reason,
                        "piece": replacement.get("pieces", [None])[0], "error": str(exc)}, grid_id=int(grid["id"]))
                    return {"decision": "retirada_sin_reapertura", "reason": reason,
                            "error": str(exc), "closed_grid_id": int(grid["id"])}
                return {"decision": "retirar_y_abrir", "reason": reason, "closed_grid_id": int(grid["id"]),
                        "opened": opened}
            if has_inventory and is_alternative and time_faster and acceptable:
                close = self.engine.close_grid(int(grid["id"]), "liquidate", close_reason="META_OPERATOR_ROTATION")
                if close.get("status") != "CLOSED":
                    return {"decision": "no_hacer_nada", "reason": "cierre verificado pendiente",
                            "grid_id": int(grid["id"]), "close": close}
                actual_allowed, actual_costs = actual_close_reallocation_allowed(expected, close, reopen_fee)
                self._ledger(goal["id"], "META_OPERATOR_CLOSE", {"reason": reason,
                    "estimated_gain_usdt": expected, "estimated_realized_loss_usdt": loss,
                    "estimated_close_fee_usdt": close_fee, "reopen_fee_usdt": reopen_fee,
                    "actual_costs": actual_costs, "reallocation_allowed_after_verified_close": actual_allowed,
                    "alternative": best}, grid_id=int(grid["id"]))
                self.alert("META_OPERATOR_CLOSE", goal["id"], {"symbol": grid["symbol"], "reason": reason})
                if not actual_allowed:
                    self._ledger(goal["id"], "META_OPERATOR_ROTATION_REJECTED", {
                        "reason": "la ganancia estimada no compensa el resultado liquidado y la reapertura",
                        "actual_costs": actual_costs, "expected_gain_usdt": expected,
                    }, grid_id=int(grid["id"]))
                    return {"decision": "cerrar_sin_reasignar", "reason": reason,
                            "actual_costs": actual_costs, "expected_gain_usdt": expected}
                try:
                    opened = self._open_replacement(goal, best, float(grid["capital_total"]))
                except Exception as exc:
                    self._ledger(goal["id"], "META_OPERATOR_OPEN_FAILED", {"reason": reason,
                        "piece": best.get("pieces", [None])[0], "error": str(exc)}, grid_id=int(grid["id"]))
                    return {"decision": "cierre_sin_reapertura", "reason": reason,
                            "error": str(exc), "closed_grid_id": int(grid["id"])}
                return {"decision": "cerrar_y_reasignar", "reason": reason, "loss_usdt": loss,
                        "expected_gain_usdt": expected, "close_fee_usdt": close_fee,
                        "reopen_fee_usdt": reopen_fee, "opened": opened}
        added = self._add_piece_from_free_cash(goal, links, plan)
        if added is not None:
            return added
        return {"decision": "no_hacer_nada", "reason": "ninguna alternativa supera los límites de reasignación",
                "alternatives": plan["alternatives"], "signals_used": plan.get("signals_used"),
                "shadow_decision": plan.get("shadow_decision")}

    def _add_piece_from_free_cash(self, goal, links, plan):
        cfg = goal.get("config_snapshot") or {}
        initial = float(goal["capital_initial"])
        active, used_by_symbol = [], {}
        for link in links:
            grid = self.db.get_grid(int(link["grid_id"]))
            if grid is None or grid.get("status") not in {"OPENING", "ACTIVE", "PAUSED", "CLOSING", "HOLDING"}:
                continue
            active.append((link, grid))
            used_by_symbol[grid["symbol"]] = used_by_symbol.get(grid["symbol"], 0.0) + float(grid["capital_total"])
        used = sum(float(grid["capital_total"]) for _, grid in active)
        free = max(0.0, initial - used)
        grids = sum(link["piece_type"] == "grid" for link, _ in active)
        bots = len(active) - grids
        if free < 5.5 or (grids >= int(cfg.get("max_grids", 0)) and bots >= int(cfg.get("max_bots", 0))):
            return None
        for alternative in plan.get("alternatives", []):
            pieces = alternative.get("pieces", [])
            if len(pieces) != 1:
                continue
            piece = dict(pieces[0])
            kind = piece["piece_type"]
            if kind == "grid" and grids >= int(cfg.get("max_grids", 0)):
                continue
            if kind == "individual_bot_grid" and bots >= int(cfg.get("max_bots", 0)):
                continue
            coin_cap = initial * float(cfg.get("capital_per_coin_pct", 0)) / 100
            available_coin = max(0.0, coin_cap - used_by_symbol.get(piece["symbol"], 0.0))
            needed = float(piece["capital_usdt"])
            if needed < 5.5 or needed > free + 1e-9 or needed > available_coin + 1e-9:
                continue
            opened = self._open_replacement(goal, alternative, needed)
            decision = {"decision": "abrir_pieza_con_efectivo_libre",
                "reason": "revisión de 15 minutos; hay capital atribuible libre",
                "free_before_usdt": free, "piece": opened,
                "profile_limits": {"capital_per_coin_pct": cfg.get("capital_per_coin_pct"),
                    "max_grids": cfg.get("max_grids"), "max_bots": cfg.get("max_bots")}}
            self._ledger(goal["id"], "META_OPERATOR_OPEN", decision, grid_id=opened["grid_id"])
            self.alert("META_OPERATOR_OPEN", goal["id"], {"symbol": piece["symbol"], "reason": decision["reason"]})
            return decision
        return None

    def _open_replacement(self, goal, alternative, capital):
        piece = dict(alternative["pieces"][0])
        piece["capital_usdt"] = min(float(capital), float(goal["capital_initial"]) *
                                     float((goal.get("config_snapshot") or {}).get("capital_per_coin_pct", 100)) / 100)
        if piece["capital_usdt"] < 5.5:
            raise ValueError("capital libre inferior al mínimo de una pieza")
        if piece["piece_type"] == "individual_bot_grid":
            grid = self.engine.create_grid(piece["symbol"], piece["floor"], piece["ceiling"], 1,
                capital=piece["capital_usdt"], strategy="simple", meta_id=int(goal["id"]),
                meta_piece_type="individual_bot_grid")
            grid_id = int(grid["id"])
        elif self._opener is not None:
            result = self._opener(piece)
            grid_id = int(result["grid_id"])
        else:
            if piece["strategy"] == "smart":
                from grid.meta import smart_grid_params_for_goal
                params = smart_grid_params_for_goal(goal, target_usdt=piece.get("target_usdt"))
            else:
                params = None
            grid = self.engine.create_grid(piece["symbol"], piece["floor"], piece["ceiling"],
                piece["n_levels"], capital=piece["capital_usdt"], strategy=piece["strategy"],
                params=params, meta_id=int(goal["id"]))
            grid_id = int(grid["id"])
        self.core.link_grid(int(goal["id"]), grid_id, piece["piece_type"])
        self._ledger(goal["id"], "META_OPERATOR_OPEN", {"piece": piece, "grid_id": grid_id},)
        self.alert("META_OPERATOR_OPEN", goal["id"], {"symbol": piece["symbol"], "reason": alternative["reason"]})
        return {**piece, "grid_id": grid_id}

    @staticmethod
    def _recenter_alternative(alternative, grid, price):
        result = json.loads(json.dumps(alternative))
        piece = result["pieces"][0]
        width_ratio = (float(grid["range_high"]) - float(grid["range_low"])) / float(grid["open_price"])
        piece["symbol"] = grid["symbol"]
        piece["strategy"] = grid["strategy"]
        piece["floor"] = price * (1 - width_ratio / 2)
        piece["ceiling"] = price * (1 + width_ratio / 2)
        piece["mid_at_plan"] = price
        piece["piece_type"] = "grid" if int(grid["n_levels"]) >= 4 else "individual_bot_grid"
        piece["n_levels"] = int(grid["n_levels"]) if piece["piece_type"] == "grid" else 1
        result["symbol"] = grid["symbol"]
        result["strategy"] = grid["strategy"]
        result["pieces"] = [piece]
        return result

    def review_active(self):
        """Revisa una sola meta activa; el watchdog 60 s sigue siendo de MetaCore."""
        from sqlalchemy import select
        with self.db.engine.connect() as conn:
            row = conn.execute(select(self.db.meta_goals.c.id).where(
                self.db.meta_goals.c.status.in_(["ACTIVE", "PIECES_BLOCKED"])).order_by(
                    self.db.meta_goals.c.id)).first()
        return None if row is None else self.review_once(int(row[0]))

    def start(self):
        self.scheduler.add_job(self.review_active, "interval", minutes=15, id="meta_operator_review",
                               replace_existing=True, max_instances=1, coalesce=True)
        if not self.scheduler.running:
            self.scheduler.start()

    def stop(self):
        if self.scheduler.running:
            self.scheduler.shutdown(wait=True)

