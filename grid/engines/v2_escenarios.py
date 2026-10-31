"""Motor v2 de solo lectura basado en la caché horaria de escenarios."""
from __future__ import annotations

import json
import sqlite3
import uuid

from sqlalchemy import select

from grid.engines.base import DecisionEngine, EngineUnavailable
from research.scenarios.objective import rank_alternatives_v2


class V2ScenarioEngine(DecisionEngine):
    name = "v2"
    version = "2-scenario-cache-1"
    available = True
    cache_max_age_seconds = 2 * 60 * 60

    @staticmethod
    def _global_settings(operator):
        table = operator.db.meta_profile_settings
        with operator.db.engine.connect() as conn:
            raw = conn.execute(select(table.c.settings).where(
                table.c.profile == "GLOBAL")).scalar_one_or_none()
        return json.loads(raw or "{}")

    @classmethod
    def _load_cache(cls, operator, goal):
        settings = cls._global_settings(operator)
        if not settings.get("v2_engine_enabled", False):
            raise EngineUnavailable("el interruptor global del motor v2 está apagado")
        if not settings.get("scenario_cache_enabled", False):
            raise EngineUnavailable("la caché de escenarios está apagada")
        from data.market_store import market_db_path
        path = market_db_path()
        if not path.is_file():
            raise EngineUnavailable("no existe la base de la caché de escenarios")
        profile = str(goal.get("profile") or "").upper()
        goal_id = goal.get("id")
        try:
            with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as conn:
                conn.row_factory = sqlite3.Row
                row = conn.execute("SELECT run_id,as_of,goal_id,profile,horizon_days,status,error "
                    "FROM scenario_eval_runs WHERE profile=? AND status='ok' AND "
                    "(goal_id=? OR goal_id IS NULL) ORDER BY CASE WHEN goal_id=? THEN 0 "
                    "ELSE 1 END, as_of DESC LIMIT 1", (profile, goal_id, goal_id)).fetchone()
                if row is None:
                    raise EngineUnavailable("no hay una corrida completa en caché para este perfil/meta")
                from datetime import datetime, timezone
                created = datetime.fromisoformat(str(row["as_of"]).replace("Z", "+00:00"))
                if created.tzinfo is None:
                    created = created.replace(tzinfo=timezone.utc)
                age = max(0.0, (datetime.now(timezone.utc) - created).total_seconds())
                if age > cls.cache_max_age_seconds:
                    raise EngineUnavailable(f"caché de escenarios vencida ({age:.0f} s)")
                results = conn.execute("SELECT candidate_key,symbol,strategy,params_json,"
                    "metrics_json,objective_json FROM scenario_eval_results WHERE run_id=?",
                    (row["run_id"],)).fetchall()
        except sqlite3.Error as exc:
            raise EngineUnavailable(f"no se pudo leer la caché de escenarios: {exc}") from exc
        candidates = []
        for item in results:
            try:
                candidates.append({"candidate_key": item["candidate_key"],
                    "symbol": item["symbol"], "strategy": item["strategy"],
                    "params": json.loads(item["params_json"] or "{}"),
                    "metrics": json.loads(item["metrics_json"] or "{}"),
                    "objective": json.loads(item["objective_json"] or "{}")})
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
        discarded = {}
        try:
            discarded = json.loads(row["error"] or "{}").get("discarded", {})
        except (TypeError, ValueError, json.JSONDecodeError):
            discarded = {}
        return {"run_id": row["run_id"], "as_of": row["as_of"],
            "horizon_days": int(row["horizon_days"]), "age_seconds": age,
            "candidates": candidates, "discarded": discarded}

    @staticmethod
    def _to_piece(candidate, capital):
        if candidate["strategy"] == "usdt_quieto":
            return None
        params = candidate["params"]
        strategy = candidate["strategy"]
        low = float(params.get("low", params.get("buy_price", 0)))
        high = float(params.get("high", params.get("sell_price", 0)))
        if low <= 0 or high <= low:
            return None
        individual = strategy == "bot"
        return {"symbol": candidate["symbol"], "strategy": "simple" if individual else strategy,
            "floor": low, "ceiling": high,
            "n_levels": 1 if individual else int(params.get("n", 6)),
            "capital_usdt": float(params.get("capital", capital)),
            "piece_type": "individual_bot_grid" if individual else "grid",
            "mid_at_plan": float(params.get("mid_at_plan", (low + high) / 2)),
            **({"grid_id": int(params["grid_id"])} if params.get("grid_id") else {})}

    def plan(self, operator, goal, *, plan_kind, allocation_free_cash=None, record=True):
        cached = self._load_cache(operator, goal)
        capital = float(goal.get("capital_initial", 0))
        candidates = []
        for cached_row in cached["candidates"]:
            if cached_row["strategy"] not in {"simple", "smart", "bot", "usdt_quieto"}:
                continue
            piece = {"strategy": cached_row["strategy"], "symbol": cached_row["symbol"],
                **cached_row["params"], "candidate_key": cached_row["candidate_key"]}
            candidates.append({"piece": piece, "metrics": cached_row["metrics"],
                "candidate_key": cached_row["candidate_key"]})
        ranked = rank_alternatives_v2(candidates, perfil=goal["profile"], capital_meta=capital)
        decision_id = uuid.uuid4().hex
        alternatives = []
        for index, candidate in enumerate(ranked):
            source_piece = candidate.get("piece") or {}
            candidate = {"candidate_key": candidate.get("candidate_key") or source_piece.get("candidate_key"),
                "symbol": source_piece.get("symbol"), "strategy": source_piece.get("strategy"),
                "params": {key: value for key, value in source_piece.items()
                    if key not in {"symbol", "strategy", "candidate_key"}},
                "metrics": candidate.get("metrics") or {},
                "objective": candidate.get("objetivo") or {}}
            piece = self._to_piece(candidate, capital)
            if piece is None:
                continue
            metrics = candidate["metrics"]
            probability = float(metrics.get("p_target", 0))
            expected_pnl = float(metrics.get("pnl_mean_usdt", 0))
            alternatives.append({"alternative_id": f"{decision_id}:{index}",
                "symbol": piece["symbol"], "strategy": piece["strategy"], "pieces": [piece],
                "estimate": {"probability": probability,
                    "probability_replay": probability,
                    "expected_pnl_usdt": expected_pnl,
                    "pnl_mean_usdt": expected_pnl,
                    "pnl_mean_ci95": metrics.get("pnl_mean_ci95"),
                    "p_loss": metrics.get("p_loss"),
                    "p_loss_ci95": metrics.get("p_loss_ci95"),
                    "cut_slippage_mean_usdt": metrics.get("cut_slippage_mean_usdt"),
                    "cut_slippage_p90_usdt": metrics.get("cut_slippage_p90_usdt"),
                    "days_p90": metrics.get("days_to_target_p90"),
                    "evidence_sufficient": bool(metrics.get("n")),
                    "days_median": metrics.get("days_to_target_p50"),
                    "median_progress_pct": metrics.get("median_progress_pct")},
                "objective": candidate["objective"], "metrics": metrics,
                "cache_candidate_key": candidate["candidate_key"],
                "is_current": bool(candidate["params"].get("is_current"))})
        selected = ranked[0] if ranked else None
        selected_key = (selected.get("candidate_key") or
            (selected.get("piece") or {}).get("candidate_key")) if selected else None
        selected_alternative = next((row for row in alternatives
            if row.get("cache_candidate_key") == selected_key), None)
        selected_pieces = selected_alternative["pieces"] if selected_alternative else []
        selected_piece = selected_pieces[0] if selected_pieces else None
        used = sum(float(piece["capital_usdt"]) for piece in selected_pieces)
        current_pieces = {}
        for row in alternatives:
            piece = row["pieces"][0]
            if row.get("is_current") and piece.get("grid_id") is not None:
                current_pieces[str(piece["grid_id"])] = {
                    "piece": piece, "estimate": row["estimate"],
                    "alternative_id": row["alternative_id"]}
        decision = ("mantener_usdt" if selected is None or not selected_pieces else
            "seleccionar_alternativa")
        selected_metrics = (selected.get("metrics") or {}) if selected else {}
        v2_summary = {"decision": decision,
            "p_target": selected_metrics.get("p_target", 0),
            "pnl_mean_usdt": selected_metrics.get("pnl_mean_usdt", 0),
            "cut_slippage_mean_usdt": selected_metrics.get("cut_slippage_mean_usdt", 0),
            "cut_slippage_p90_usdt": selected_metrics.get("cut_slippage_p90_usdt", 0),
            "motivos": (selected.get("objetivo") or {}).get("motivos", []) if selected else [],
            "descartadas": cached["discarded"]}
        return {"profile": str(goal.get("profile") or "").upper(),
            "decision_id": decision_id, "base_method_version": self.version,
            "engine": {"name": self.name, "version": self.version},
            "cache": {key: cached[key] for key in ("run_id", "as_of", "age_seconds")},
            "candidates": [{"symbol": row["symbol"], "scanner_eligible": True,
                "eligible": True} for row in cached["candidates"] if row["symbol"]],
            "alternatives": alternatives, "pieces": selected_pieces,
            "current_pieces": current_pieces, "estimated_time": (selected_alternative or {}).get("estimate", {}),
            "piece_estimates": [], "target_usdt": float(goal.get("target_usdt", 0)),
            "deadline_days": cached["horizon_days"],
            "selection_reason": ("selección por caché de escenarios v2" if selected_pieces else
                "ninguna geometría factible; conservar capital en USDT"),
            "v2": v2_summary,
            "capital_usage": {"initial_capital_usdt": capital, "used_usdt": used,
                "unused_usdt": max(0.0, capital - used)},
            "decision_choices": {"comparison_version": 2,
                "real": {"decision": decision,
                    "alternative_id": selected_alternative.get("alternative_id") if selected_alternative else None,
                    "symbol": selected_alternative.get("symbol") if selected_alternative else None},
                "scanner_advisor": {"decision": "no_disponible_en_cache"}},
            "control_plan": {"symbol": selected_piece["symbol"],
                "capital_usdt": selected_piece["capital_usdt"], "strategy": selected_piece["strategy"],
                "range_low": selected_piece["floor"], "range_high": selected_piece["ceiling"],
                "n_levels": selected_piece["n_levels"]} if selected_piece else {},
            "signals_used": [], "signals_omitted": ["señales v1 fuera del método v2"],
            "shadow_decision": None, "warnings": [],
            "use_extended_allocation": False}

    def review(self, operator, goal, valuation, plan):
        links = operator.core._owned_grids(int(goal["id"]))
        alternatives = list((plan or {}).get("alternatives") or [])
        if not links:
            return {"decision": "no_hacer_nada", "reason": "la meta no tiene grids activos"}
        for link in links:
            grid_id = str(int(link["grid_id"]))
            current = (plan.get("current_pieces") or {}).get(grid_id)
            if not current:
                return {"decision": "no_hacer_nada",
                    "reason": "la caché no contiene una evaluación mantener para cada grid activo",
                    "grid_id": int(grid_id)}
            current_est = current.get("estimate") or {}
            current_alt = next((row for row in alternatives
                if row.get("cache_candidate_key") and row.get("is_current")
                and int((row.get("pieces") or [{}])[0].get("grid_id") or -1) == int(grid_id)), None)
            if current_alt is None:
                return {"decision": "no_hacer_nada", "reason": "falta alternativa mantener vigente",
                    "grid_id": int(grid_id)}
            candidates = [row for row in alternatives if not row.get("is_current")
                and row.get("estimate", {}).get("evidence_sufficient")]
            best = next((row for row in candidates
                if row.get("objective", {}).get("factible")), None)
            if best is None:
                continue
            best_est = best.get("estimate") or {}
            probability_delta_pp = (float(best_est.get("probability") or 0) -
                float(current_est.get("probability") or 0)) * 100
            current_ci = current_est.get("pnl_mean_ci95")
            if not isinstance(current_ci, (list, tuple)) or len(current_ci) != 2:
                continue
            standard_error = abs(float(current_ci[1]) - float(current_ci[0])) / (2 * 1.96)
            valuation_piece = next((row for row in (valuation or {}).get("pieces", [])
                if int(row.get("grid_id") or -1) == int(grid_id)), {})
            levels = valuation_piece.get("levels") or []
            symbol_value = ((valuation or {}).get("symbol_valuations") or {}).get(
                valuation_piece.get("symbol") or "", {})
            current_bid = float(symbol_value.get("best_bid") or 0)
            realized_loss = sum(max(0.0, float(level.get("entry_price") or level.get("price") or 0) *
                float(level.get("held_qty") or 0) - float(level.get("held_qty") or 0) *
                float(level.get("current_bid") or current_bid) * .999) for level in levels)
            exit_fee = sum(float(level.get("held_qty") or 0) *
                float(level.get("current_bid") or current_bid) * .001 for level in levels)
            new_piece = (best.get("pieces") or [{}])[0]
            entry_fee = float(new_piece.get("capital_usdt") or 0) * .001
            new_net = float(best_est.get("pnl_mean_usdt") or 0) - realized_loss - exit_fee - entry_fee
            keep_floor = float(current_est.get("pnl_mean_usdt") or 0) - standard_error
            if probability_delta_pp < 5 or new_net < keep_floor:
                continue
            reordered = dict(plan)
            reordered["alternatives"] = [best, *[row for row in alternatives if row is not best]]
            # La rutina existente conserva sus gates de materialidad, persistencia,
            # cooldown, pérdida máxima, límites diarios y cierre verificado.
            result = operator._review_allocations(goal, valuation, plan=reordered)
            return {**result, "comparison_v2": {"probability_improvement_pp": probability_delta_pp,
                "expected_net_usdt": new_net, "keep_mean_usdt": current_est.get("pnl_mean_usdt"),
                "keep_standard_error_usdt": standard_error,
                "realized_loss_usdt": realized_loss, "exit_fee_usdt": exit_fee,
                "entry_fee_usdt": entry_fee, "cache_run_id": (plan.get("cache") or {}).get("run_id")}}
        return {"decision": "mantener", "reason":
            "ninguna alternativa supera el umbral pareado de probabilidad y ganancia neta",
            "cache_run_id": (plan.get("cache") or {}).get("run_id")}

    def shadow_review(self, operator, goal, valuation, plan):
        try:
            shadow_plan = self.plan(operator, goal, plan_kind="revision", record=False)
            result = self.review(operator, goal, valuation, shadow_plan)
            return {**result, "reason": result.get("reason") or "revisión en sombra",
                "cache_run_id": (shadow_plan.get("cache") or {}).get("run_id")}
        except EngineUnavailable as exc:
            return {"decision": "no_disponible", "reason": str(exc)}
