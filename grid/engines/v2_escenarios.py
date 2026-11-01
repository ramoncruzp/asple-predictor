"""Motor v2 de solo lectura basado en la caché horaria de escenarios."""
from __future__ import annotations

import json
import sqlite3
import uuid
from pathlib import Path

from sqlalchemy import select

from grid.engines.base import DecisionEngine, EngineUnavailable
from grid.json_utils import json_safe
from research.scenarios.portfolio import greedy_portfolio
from research.scenarios.objective import rank_alternatives_v2


class V2ScenarioEngine(DecisionEngine):
    name = "v2"
    version = "2-scenario-cache-1"
    available = True
    cache_max_age_seconds = 2 * 60 * 60

    @staticmethod
    def _global_settings(operator):
        if operator is None or getattr(operator, "db", None) is None:
            raise EngineUnavailable("el contexto de caché del motor v2 no está disponible")
        table = operator.db.meta_profile_settings
        with operator.db.engine.connect() as conn:
            raw = conn.execute(select(table.c.settings).where(
                table.c.profile == "GLOBAL")).scalar_one_or_none()
        return json.loads(raw or "{}")

    @classmethod
    def _load_cache(cls, operator, goal, *, require_engine=True):
        settings = cls._global_settings(operator)
        if require_engine and not settings.get("v2_engine_enabled", False):
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
                    "FROM scenario_eval_runs WHERE profile=? AND status IN ('ok','parcial') AND "
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
        symbol_stats = {}
        skipped_symbols = {}
        try:
            run_details = json.loads(row["error"] or "{}")
            discarded = run_details.get("discarded", {})
            symbol_stats = run_details.get("symbol_stats", {})
            skipped_symbols = run_details.get("skipped", {})
        except (TypeError, ValueError, json.JSONDecodeError):
            discarded = {}
        trajectory_file = Path("data/derived/scenario_cache") / f"{row['run_id']}.npz"
        return {"run_id": row["run_id"], "as_of": row["as_of"],
            "horizon_days": int(row["horizon_days"]), "age_seconds": age,
            "status": str(row["status"]),
            "candidates": candidates, "discarded": discarded,
            "symbol_stats": symbol_stats, "skipped_symbols": skipped_symbols,
            "trajectory_file": str(trajectory_file) if trajectory_file.is_file() else None}

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

    @staticmethod
    def _profile_config(goal):
        from grid.meta import DEFAULT_PROFILES
        profile = str(goal.get("profile") or "").upper()
        config = dict(DEFAULT_PROFILES.get(profile) or {})
        snapshot = goal.get("config_snapshot") or {}
        if isinstance(snapshot, str):
            try:
                snapshot = json.loads(snapshot)
            except (TypeError, ValueError, json.JSONDecodeError):
                snapshot = {}
        config.update(snapshot if isinstance(snapshot, dict) else {})
        return config

    @staticmethod
    def _live_profile_usage(operator, goal_id):
        from grid.meta_live_pieces import live_meta_grid_links
        core = getattr(operator, "core", None)
        links = live_meta_grid_links(core, int(goal_id))
        grids = bots = 0
        by_symbol = {}
        for link in links:
            grid = core.db.get_grid(int(link["grid_id"]))
            if grid is None:
                continue
            if link.get("piece_type") == "individual_bot_grid":
                bots += 1
            else:
                grids += 1
            symbol = str(grid.get("symbol") or "").upper()
            by_symbol[symbol] = by_symbol.get(symbol, 0.0) + float(
                grid.get("capital_total") or 0)
        return links, grids, bots, by_symbol

    @staticmethod
    def _candidate_table(cached):
        grouped = {}
        for row in cached.get("candidates", []):
            symbol = str(row.get("symbol") or "").upper()
            if not symbol or row.get("strategy") == "usdt_quieto":
                continue
            grouped.setdefault(symbol, []).append(row)
        output = []
        stats_by_symbol = cached.get("symbol_stats") or {}
        for symbol in sorted(set(grouped) | set(stats_by_symbol)):
            stats = stats_by_symbol.get(symbol, {})
            rows = grouped.get(symbol, [])
            best_p_target = max((float((item.get("metrics") or {}).get("p_target") or 0)
                for item in rows), default=None)
            best_pnl = max((float((item.get("metrics") or {}).get("pnl_mean_usdt") or 0)
                for item in rows), default=None)
            discarded = stats.get("discarded") or {}
            common_reason = max(discarded, key=discarded.get) if discarded else None
            output.append({"symbol": symbol, "sigma_24h": stats.get("sigma_24h"),
                "price": stats.get("price"),
                "generated_count": int(stats.get("generated_count", len(rows))),
                "evaluated_count": int(stats.get("evaluated_count", len(rows))),
                "discarded_count": sum(int(value) for value in discarded.values()),
                "discarded_reason": common_reason,
                "best_p_target": best_p_target,
                "best_pnl_mean_usdt": best_pnl,
                "skipped_reason": stats.get("skipped_reason")})
        for symbol, reason in sorted((cached.get("skipped_symbols") or {}).items()):
            if str(reason).startswith("rol "):
                continue
            if symbol not in grouped and symbol not in stats_by_symbol:
                output.append({"symbol": symbol, "sigma_24h": None, "price": None,
                    "generated_count": 0, "evaluated_count": 0, "discarded_count": 0,
                    "discarded_reason": None, "best_p_target": None,
                    "best_pnl_mean_usdt": None,
                    "skipped_reason": ("sin tiempo en la corrida parcial"
                        if cached.get("status") == "parcial" else str(reason))})
        return output

    def plan(self, operator, goal, *, plan_kind, allocation_free_cash=None, record=True,
            require_engine=True):
        from grid.meta_live_pieces import live_meta_grid_ids
        cached = self._load_cache(operator, goal, require_engine=require_engine)
        capital = float(goal.get("capital_initial", 0))
        live_grid_ids = live_meta_grid_ids(getattr(operator, "core", None), int(goal["id"]))
        profile_config = self._profile_config(goal)
        _live_links, live_grids, live_bots, used_by_symbol = self._live_profile_usage(
            operator, int(goal["id"]))
        coin_cap = capital * float(profile_config.get("capital_per_coin_pct", 0)) / 100
        is_draft = str(goal.get("status") or "").upper() == "DRAFT"
        candidates = []
        for cached_row in cached["candidates"]:
            if cached_row["strategy"] not in {"simple", "smart", "bot", "usdt_quieto"}:
                continue
            piece = {"strategy": cached_row["strategy"], "symbol": cached_row["symbol"],
                **cached_row["params"], "candidate_key": cached_row["candidate_key"]}
            if piece.get("is_current") and int(piece.get("grid_id") or -1) not in live_grid_ids:
                continue
            if is_draft and not piece.get("is_current") and piece["strategy"] != "usdt_quieto":
                amount = float(piece.get("capital") or 0)
                symbol = str(piece.get("symbol") or "").upper()
                is_bot = piece.get("strategy") == "bot"
                slot_available = (live_bots < int(profile_config.get("max_bots", 0))
                    if is_bot else live_grids < int(profile_config.get("max_grids", 0)))
                if (amount < 10 or not slot_available or
                        amount > coin_cap - used_by_symbol.get(symbol, 0.0) + 1e-8):
                    continue
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
                    "p_inventory": metrics.get("p_inventory"),
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
        portfolio = None
        if cached.get("trajectory_file"):
            portfolio = greedy_portfolio(cached["trajectory_file"],
                profile=goal["profile"], capital=capital,
                free_cash=(float(allocation_free_cash) if allocation_free_cash is not None
                    else capital), target=float(goal.get("target_usdt", 0)),
                max_loss_pct=float(profile_config.get("max_loss_pct", 0)),
                horizon_days=cached["horizon_days"], profile_config=profile_config)
        if is_draft and portfolio is not None:
            selected_pieces = []
            for item in portfolio.get("added", []):
                alternative = next((row for row in alternatives
                    if row.get("cache_candidate_key") == item.get("candidate_key")), None)
                if alternative:
                    selected_pieces.extend(alternative.get("pieces") or [])
            selected_alternative = next((row for row in alternatives
                if row.get("cache_candidate_key") == (portfolio.get("added") or [{}])[0].get(
                    "candidate_key")), None) if portfolio.get("added") else None
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
        selected_metrics = (portfolio.get("metrics") if is_draft and portfolio is not None
            else (selected.get("metrics") or {}) if selected else {})
        v2_summary = {"decision": decision,
            "p_target": selected_metrics.get("p_target", 0),
            "p_target_ci95": selected_metrics.get("p_target_ci95"),
            "pnl_mean_usdt": selected_metrics.get("pnl_mean_usdt", 0),
            "pnl_mean_ci95": selected_metrics.get("pnl_mean_ci95"),
            "cut_slippage_mean_usdt": selected_metrics.get("cut_slippage_mean_usdt", 0),
            "cut_slippage_p90_usdt": selected_metrics.get("cut_slippage_p90_usdt", 0),
            "p_loss": selected_metrics.get("p_loss", 0),
            "p_loss_ci95": selected_metrics.get("p_loss_ci95"),
            "p_inventory": selected_metrics.get("p_inventory"),
            "n": selected_metrics.get("n"), "days_to_target_p50": selected_metrics.get(
                "days_to_target_p50"),
            "metrics": selected_metrics,
            "motivos": (selected.get("objetivo") or {}).get("motivos", []) if selected else [],
            "descartadas": cached["discarded"]}
        discarded_alternatives = [{"symbol": row["symbol"], "strategy": row["strategy"],
            "metrics": row["metrics"], "reason": "; ".join(
                (row.get("objective") or {}).get("motivos") or ["no cumple el objetivo 1 + 3"])}
            for row in alternatives if not (row.get("objective") or {}).get("factible")][:3]
        if cached.get("trajectory_file") and is_draft and portfolio is not None:
            v2_summary["motivos"] = (["cartera voraz: cada incorporación mejora P(meta) al menos 2 pp"]
                if portfolio.get("steps") else ["ninguna pieza mejora P(meta) al menos 2 pp y cumple el objetivo 1 + 3"])
        return json_safe({"profile": str(goal.get("profile") or "").upper(),
            "decision_id": decision_id, "base_method_version": self.version,
            "engine": {"name": self.name, "version": self.version},
            "cache": {key: cached[key] for key in ("run_id", "as_of", "age_seconds", "status")},
            "candidates": self._candidate_table(cached),
            "discarded_alternatives": discarded_alternatives,
            "alternatives": alternatives, "pieces": selected_pieces,
            "portfolio": portfolio,
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
            "use_extended_allocation": False})

    def decide(self, operator, goal, valuation, plan):
        """Decide y prepara una acción sin ejecutar ni persistir cambios."""
        from grid.meta_live_pieces import live_meta_grid_links
        links = live_meta_grid_links(operator.core, int(goal["id"]))
        alternatives = list((plan or {}).get("alternatives") or [])
        if not links:
            return json_safe({"decision": "no_hacer_nada", "reason": "la meta no tiene grids activos"})
        for link in links:
            grid_id = str(int(link["grid_id"]))
            current = (plan.get("current_pieces") or {}).get(grid_id)
            if not current:
                return json_safe({"decision": "no_hacer_nada",
                    "reason": "la caché no contiene una evaluación mantener para cada grid activo",
                    "grid_id": int(grid_id)})
            current_est = current.get("estimate") or {}
            current_alt = next((row for row in alternatives
                if row.get("cache_candidate_key") and row.get("is_current")
                and int((row.get("pieces") or [{}])[0].get("grid_id") or -1) == int(grid_id)), None)
            if current_alt is None:
                return json_safe({"decision": "no_hacer_nada", "reason": "falta alternativa mantener vigente",
                    "grid_id": int(grid_id)})
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
            return json_safe({"decision": "cambiar", "reason":
                "alternativa con mejora pareada; falta aplicar los frenos de ejecucion",
                "grid_id": int(grid_id), "alternative_id": best.get("alternative_id"),
                "proposed_action": {"type": "rotar", "grid_id": int(grid_id),
                    "alternative": best, "plan": reordered},
                "comparison_v2": {"probability_improvement_pp": probability_delta_pp,
                "expected_net_usdt": new_net, "keep_mean_usdt": current_est.get("pnl_mean_usdt"),
                "keep_standard_error_usdt": standard_error,
                "realized_loss_usdt": realized_loss, "exit_fee_usdt": exit_fee,
                "entry_fee_usdt": entry_fee, "cache_run_id": (plan.get("cache") or {}).get("run_id")}})
        portfolio = (plan or {}).get("portfolio") or {}
        added_keys = {str(row.get("candidate_key")) for row in portfolio.get("added", [])}
        if added_keys:
            addition = next((row for row in alternatives
                if str(row.get("cache_candidate_key")) in added_keys
                and not row.get("is_current")), None)
            if addition is not None:
                # La apertura conserva las validaciones de efectivo, perfil y
                # abribilidad que ya aplica el operador para piezas nuevas.
                return json_safe({"decision": "agregar", "reason":
                    "cartera v2 propone agregar una pieza con efectivo libre",
                    "alternative_id": addition.get("alternative_id"),
                    "proposed_action": {"type": "agregar", "alternative": addition},
                    "comparison_v2": {"portfolio_steps": portfolio.get("steps", []),
                        "cache_run_id": (plan.get("cache") or {}).get("run_id")}})
        return json_safe({"decision": "mantener", "reason":
            "ninguna alternativa supera el umbral pareado de probabilidad y ganancia neta",
            "cache_run_id": (plan.get("cache") or {}).get("run_id")})

    def review(self, operator, goal, valuation, plan):
        """Ejecuta la decision pura mediante los frenos actuales del operador."""
        decision = self.decide(operator, goal, valuation, plan)
        action = decision.pop("proposed_action", None)
        if not action:
            return json_safe(decision)
        if action["type"] == "rotar":
            executed = operator._review_allocations(goal, valuation, plan=action["plan"])
        elif action["type"] == "agregar":
            from grid.meta_live_pieces import live_meta_grid_links
            links = live_meta_grid_links(operator.core, int(goal["id"]))
            executed = operator._add_piece_from_free_cash(goal, links,
                {"alternatives": [action["alternative"]]}, valuation=valuation)
        else:
            return json_safe({"decision": "no_hacer_nada", "reason": "accion v2 desconocida"})
        if not executed:
            return json_safe({"decision": "mantener",
                "reason": "la ejecucion fue rechazada por los frenos activos",
                "comparison_v2": decision.get("comparison_v2")})
        return json_safe({**executed, "comparison_v2": decision.get("comparison_v2")})

    def shadow_review(self, operator, goal, valuation, plan):
        try:
            from grid.engines.shadow_guard import shadow_execution_context
            with shadow_execution_context():
                shadow_plan = self.plan(operator, goal, plan_kind="revision", record=False,
                    require_engine=False)
                result = self.decide(operator, goal, valuation, shadow_plan)
            live_metrics = [row.get("estimate") or {} for row in
                (shadow_plan.get("current_pieces") or {}).values()]
            v2_metrics = (live_metrics[0] if result.get("decision") in {"mantener", "no_hacer_nada"}
                and live_metrics else shadow_plan.get("estimated_time") or {})
            return json_safe({**result, "reason": result.get("reason") or "revisión en sombra",
                "cache_run_id": (shadow_plan.get("cache") or {}).get("run_id"),
                "v2_metrics": v2_metrics,
                "pieces": shadow_plan.get("pieces", []),
                "decision_choices": shadow_plan.get("decision_choices", {}),
                "current_pieces": shadow_plan.get("current_pieces", {})})
        except Exception as exc:
            if "shadow_violation" in str(exc):
                return json_safe({"decision": "no_disponible", "reason": str(exc),
                    "shadow_violation": True})
            if not isinstance(exc, EngineUnavailable):
                raise
            return json_safe({"decision": "no_disponible", "reason": str(exc)})
