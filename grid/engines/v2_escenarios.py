"""Motor v2 de solo lectura basado en la caché horaria de escenarios."""
from __future__ import annotations

import json
import sqlite3
import uuid

from sqlalchemy import select

from grid.engines.base import DecisionEngine, EngineUnavailable


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
                row = conn.execute("SELECT run_id,as_of,goal_id,profile,horizon_days,status "
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
        return {"run_id": row["run_id"], "as_of": row["as_of"],
            "horizon_days": int(row["horizon_days"]), "age_seconds": age,
            "candidates": candidates}

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
            "mid_at_plan": (low + high) / 2}

    def plan(self, operator, goal, *, plan_kind, allocation_free_cash=None, record=True):
        cached = self._load_cache(operator, goal)
        capital = float(goal.get("capital_initial", 0))
        candidates = [row for row in cached["candidates"]
            if row["objective"].get("factible") and row["strategy"] in
                {"simple", "smart", "bot", "usdt_quieto"}]
        candidates.sort(key=lambda row: tuple(row["objective"].get("clave_orden") or
            (float("inf"), float("inf"), float("inf"))))
        selected = candidates[0] if candidates else None
        selected_piece = self._to_piece(selected, capital) if selected else None
        decision_id = uuid.uuid4().hex
        alternatives = []
        for index, candidate in enumerate(candidates):
            piece = self._to_piece(candidate, capital)
            if piece is None:
                continue
            metrics = candidate["metrics"]
            alternatives.append({"alternative_id": f"{decision_id}:{index}",
                "symbol": piece["symbol"], "strategy": piece["strategy"], "pieces": [piece],
                "estimate": {"probability": float(metrics.get("p_target", 0)),
                    "probability_replay": float(metrics.get("p_target", 0)),
                    "days_p90": metrics.get("days_to_target_p90"),
                    "evidence_sufficient": bool(metrics.get("n")),
                    "expected_pnl_usdt": metrics.get("pnl_mean_usdt")},
                "objective": candidate["objective"], "metrics": metrics,
                "cache_candidate_key": candidate["candidate_key"]})
        selected_alternative = alternatives[0] if alternatives else None
        selected_pieces = selected_alternative["pieces"] if selected_alternative else []
        used = sum(float(piece["capital_usdt"]) for piece in selected_pieces)
        return {"profile": str(goal.get("profile") or "").upper(),
            "decision_id": decision_id, "base_method_version": self.version,
            "engine": {"name": self.name, "version": self.version},
            "cache": {key: cached[key] for key in ("run_id", "as_of", "age_seconds")},
            "candidates": [{"symbol": row["symbol"], "scanner_eligible": True,
                "eligible": True} for row in cached["candidates"] if row["symbol"]],
            "alternatives": alternatives, "pieces": selected_pieces,
            "current_pieces": {}, "estimated_time": (selected_alternative or {}).get("estimate", {}),
            "piece_estimates": [], "target_usdt": float(goal.get("target_usdt", 0)),
            "deadline_days": cached["horizon_days"],
            "selection_reason": ("selección por caché de escenarios v2" if selected_pieces else
                "ninguna geometría factible; conservar capital en USDT"),
            "capital_usage": {"initial_capital_usdt": capital, "used_usdt": used,
                "unused_usdt": max(0.0, capital - used)},
            "decision_choices": {"comparison_version": 2,
                "real": {"decision": "seleccionar_alternativa" if selected_pieces else "mantener_usdt",
                    "alternative_id": selected_alternative.get("alternative_id") if selected_alternative else None,
                    "symbol": selected_alternative.get("symbol") if selected_alternative else None},
                "scanner_advisor": {"decision": "no_disponible_en_cache"}},
            "control_plan": {"symbol": selected_piece["symbol"],
                "capital_usdt": selected_piece["capital_usdt"], "strategy": selected_piece["strategy"],
                "range_low": selected_piece["floor"], "range_high": selected_piece["ceiling"],
                "n_levels": selected_piece["n_levels"]} if selected_piece else {},
            "signals_used": [], "signals_omitted": ["señales v1 fuera del método v2"],
            "shadow_decision": None, "warnings": [], "current_pieces": {},
            "use_extended_allocation": False}

    def review(self, operator, goal, valuation, plan):
        # La primera integración no permite reconfigurar/rotar una pieza viva.
        # La caché informa, pero no ejecuta ni altera órdenes existentes.
        cached = self._load_cache(operator, goal)
        return {"decision": "no_hacer_nada", "reason":
            f"revisión v2 en observación; caché {cached['run_id']} vigente; "
            "esta versión no reconfigura grids activos"}

    def shadow_review(self, operator, goal, valuation, plan):
        try:
            cached = self._load_cache(operator, goal)
            best = next((row for row in cached["candidates"]
                if row["objective"].get("factible") and row["strategy"] != "usdt_quieto"), None)
            return {"decision": "seleccionar_alternativa" if best else "mantener_usdt",
                "reason": "lectura de caché en sombra", "symbol": best["symbol"] if best else None,
                "cache_run_id": cached["run_id"]}
        except EngineUnavailable as exc:
            return {"decision": "no_disponible", "reason": str(exc)}
