"""Motor v2 de solo lectura basado en la caché horaria de escenarios."""
from __future__ import annotations

import json
import math
import sqlite3
import uuid
from pathlib import Path

from sqlalchemy import select

from grid.engines.base import DecisionEngine, EngineUnavailable
from grid.json_utils import json_safe
from grid.market_book import book_is_two_sided, book_mid
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
            symbol_stats = {}
            skipped_symbols = {}
            run_details = {}
        if str(row["status"]) == "parcial" and not bool(
                (run_details.get("pass1_coverage") or {}).get("complete")):
            raise EngineUnavailable("caché parcial incompleta: la pasada 1 no cubrió todas las monedas")
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
    def _reanchor_piece(operator, goal, cached, alternative, candidate, *, book_cache=None):
        """Ancla una geometría seleccionada al libro actual sin mover la geometría v1."""
        piece = (alternative.get("pieces") or [None])[0]
        source = candidate.get("piece") or {}
        if piece is None or source.get("is_current") or source.get("grid_id") is not None:
            return
        symbol = str(piece.get("symbol") or "").upper()
        reader = getattr(operator, "_book_ticker", None)
        if not callable(reader):
            raise EngineUnavailable(f"no se pudo leer el libro actual de {symbol}")
        book_cache = {} if book_cache is None else book_cache
        try:
            if symbol not in book_cache:
                book_cache[symbol] = reader(symbol)
            book = book_cache[symbol]
            mid = book_mid(book) if book_is_two_sided(book) else None
        except Exception as exc:
            raise EngineUnavailable(f"no se pudo leer un libro válido de {symbol}: {exc}") from exc
        if mid is None or float(mid) <= 0:
            raise EngineUnavailable(f"el libro actual de {symbol} no tiene bid/ask válidos")
        mid = float(mid)
        stats = (cached.get("symbol_stats") or {}).get(symbol) or {}
        cache_price = float(stats.get("price") or 0)
        if cache_price <= 0:
            raise EngineUnavailable(f"la caché no tiene precio base para {symbol}")
        movement_pct = (mid / cache_price - 1.0) * 100.0
        if abs(movement_pct) > 3.0:
            raise EngineUnavailable(
                f"la caché está desactualizada para {symbol} (movimiento {movement_pct:+.1f}%); "
                "espera la próxima corrida o córrela a mano")

        strategy = str(source.get("strategy") or "").lower()
        if strategy == "bot" and source.get("buy_offset_pct") is not None and \
                source.get("sell_offset_pct") is not None:
            low = mid * (1.0 + float(source["buy_offset_pct"]) / 100.0)
            high = mid * (1.0 + float(source["sell_offset_pct"]) / 100.0)
        else:
            width_sigma = source.get("width_sigma")
            sigma = stats.get("sigma_24h")
            if width_sigma is not None and sigma is not None and float(sigma) > 0:
                width = float(width_sigma) * float(sigma) * math.sqrt(
                    max(1, int(cached.get("horizon_days") or 1)))
                low, high = mid * math.exp(-width), mid * math.exp(width)
            else:
                old_low = float(source.get("low", source.get("buy_price", 0)) or 0)
                old_high = float(source.get("high", source.get("sell_price", 0)) or 0)
                if old_low <= 0 or old_high <= old_low:
                    raise EngineUnavailable(f"la geometría de {symbol} no tiene parámetros relativos")
                low, high = mid * old_low / cache_price, mid * old_high / cache_price
        if low <= 0 or high <= low:
            raise EngineUnavailable(f"la geometría re-anclada de {symbol} no es válida")
        from datetime import datetime, timezone
        clock = getattr(operator, "clock", None)
        price_at = (datetime.fromtimestamp(float(clock()), timezone.utc).isoformat()
            if callable(clock) else datetime.now(timezone.utc).isoformat())
        piece.update({"floor": low, "ceiling": high, "mid_at_plan": mid,
            "price_source": "book", "price_at": price_at})
        alternative.update({"price_source": "book", "price_at": price_at})

    @staticmethod
    def _profile_config(goal, operator=None):
        from grid.meta import DEFAULT_PROFILES
        profile = str(goal.get("profile") or "").upper()
        config = dict(DEFAULT_PROFILES.get(profile) or {})
        if operator is not None and getattr(operator, "db", None) is not None:
            table = operator.db.meta_profile_settings
            with operator.db.engine.connect() as conn:
                raw = conn.execute(select(table.c.settings).where(
                    table.c.profile == profile)).scalar_one_or_none()
            try:
                config.update(json.loads(raw or "{}"))
            except (TypeError, ValueError, json.JSONDecodeError):
                pass
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
                "pasada": stats.get("pasada", "sin_tiempo"),
                "skipped_reason": stats.get("skipped_reason")})
        for symbol, reason in sorted((cached.get("skipped_symbols") or {}).items()):
            if str(reason).startswith("rol "):
                continue
            if symbol not in grouped and symbol not in stats_by_symbol:
                output.append({"symbol": symbol, "sigma_24h": None, "price": None,
                    "generated_count": 0, "evaluated_count": 0, "discarded_count": 0,
                    "discarded_reason": None, "best_p_target": None,
                    "best_pnl_mean_usdt": None,
                    "pasada": "sin_tiempo",
                    "skipped_reason": ("sin tiempo en la corrida parcial"
                        if cached.get("status") == "parcial" else str(reason))})
        return output

    def plan(self, operator, goal, *, plan_kind, allocation_free_cash=None, record=True,
            require_engine=True):
        from grid.meta_live_pieces import live_meta_grid_ids
        cached = self._load_cache(operator, goal, require_engine=require_engine)
        capital = float(goal.get("capital_initial", 0))
        live_grid_ids = live_meta_grid_ids(getattr(operator, "core", None), int(goal["id"]))
        profile_config = self._profile_config(goal, operator)
        exploration_status = getattr(operator, "v2_exploration_status", None)
        exploration_enabled = bool(exploration_status().get("enabled")) if callable(
            exploration_status) else False
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
            metrics = dict(cached_row["metrics"] or {})
            metrics.setdefault("cut_slippage_expected_usdt",
                float(metrics.get("p_loss", 0.0)) *
                float(metrics.get("cut_slippage_mean_usdt", 0.0)))
            candidates.append({"piece": piece, "metrics": metrics,
                "candidate_key": cached_row["candidate_key"]})
        ranked = rank_alternatives_v2(candidates, perfil=goal["profile"], capital_meta=capital,
            cut_slippage_expected_max_pct=float(profile_config.get(
                "cut_slippage_expected_max_pct", .5)))
        decision_id = uuid.uuid4().hex
        alternatives = []
        prices_by_symbol = cached.get("symbol_stats") or {}
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
            symbol_price = (prices_by_symbol.get(str(piece["symbol"]).upper()) or {}).get("price")
            try:
                market_price = float(symbol_price)
            except (TypeError, ValueError):
                market_price = 0.0
            if market_price > 0:
                piece.update({"mid_at_plan": market_price, "price_source": "scenario_cache",
                    "price_at": cached.get("as_of")})
            metrics = candidate["metrics"]
            probability = float(metrics.get("p_target", 0))
            expected_pnl = float(metrics.get("pnl_mean_usdt", 0))
            alternatives.append({"alternative_id": f"{decision_id}:{index}",
                "symbol": piece["symbol"], "strategy": piece["strategy"], "pieces": [piece],
                "price_source": piece.get("price_source"), "price_at": piece.get("price_at"),
                "estimate": {"probability": probability,
                    "probability_replay": probability,
                    "expected_pnl_usdt": expected_pnl,
                    "pnl_mean_usdt": expected_pnl,
                    "pnl_mean_ci95": metrics.get("pnl_mean_ci95"),
                    "p_loss": metrics.get("p_loss"),
                    "p_loss_ci95": metrics.get("p_loss_ci95"),
                    "portfolio_metrics": metrics.get("portfolio_metrics"),
                    "cut_slippage_mean_usdt": metrics.get("cut_slippage_mean_usdt"),
                    "cut_slippage_p90_usdt": metrics.get("cut_slippage_p90_usdt"),
                    "cut_slippage_expected_usdt": metrics.get("cut_slippage_expected_usdt"),
                    "p_inventory": metrics.get("p_inventory"),
                    "days_p90": metrics.get("days_to_target_p90"),
                    "evidence_sufficient": bool(metrics.get("n")),
                    "days_median": metrics.get("days_to_target_p50"),
                    "median_progress_pct": metrics.get("median_progress_pct")},
                "objective": candidate["objective"], "metrics": metrics,
                "cache_candidate_key": candidate["candidate_key"],
                "relative_params": {key: candidate["params"].get(key) for key in (
                    "width_sigma", "buy_offset_pct", "sell_offset_pct", "range_type", "band")
                    if candidate["params"].get(key) is not None},
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
                horizon_days=cached["horizon_days"], profile_config=profile_config,
                exploration=exploration_enabled)
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
        selected_piece_ids = {id(piece) for piece in selected_pieces}
        ranked_by_key = {str(row.get("candidate_key") or
            (row.get("piece") or {}).get("candidate_key")): row for row in ranked}
        book_cache = {}
        for alternative in alternatives:
            if not any(id(piece) in selected_piece_ids for piece in alternative.get("pieces") or []):
                continue
            candidate = ranked_by_key.get(str(alternative.get("cache_candidate_key")))
            if candidate is not None:
                self._reanchor_piece(operator, goal, cached, alternative, candidate,
                    book_cache=book_cache)
        if is_draft and selected_pieces:
            validator = getattr(operator, "_validate_v2_plan_limits", None)
            if callable(validator):
                validator(goal, {"pieces": selected_pieces})
            openability = getattr(operator, "_testnet_openability", None)
            if callable(openability) and getattr(operator, "engine", None) is not None:
                for piece in selected_pieces:
                    result = openability(piece, goal)
                    if not result.get("allowed"):
                        raise EngineUnavailable(
                            f"la pieza re-anclada de {piece['symbol']} no cumple filtros: "
                            f"{result.get('reason') or 'geometría no abrible'}")
        used = sum(float(piece["capital_usdt"]) for piece in selected_pieces)
        current_pieces = {}
        for row in alternatives:
            piece = row["pieces"][0]
            if row.get("is_current") and piece.get("grid_id") is not None:
                current_pieces[str(piece["grid_id"])] = {
                    "piece": piece, "estimate": row["estimate"],
                    "portfolio_estimate": row["estimate"].get("portfolio_metrics") or {},
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
            "cut_slippage_expected_usdt": selected_metrics.get("cut_slippage_expected_usdt", 0),
            "p_loss": selected_metrics.get("p_loss", 0),
            "p_loss_ci95": selected_metrics.get("p_loss_ci95"),
            "p_inventory": selected_metrics.get("p_inventory"),
            "n": selected_metrics.get("n"), "days_to_target_p50": selected_metrics.get(
                "days_to_target_p50"),
            "metrics": selected_metrics,
            "motivos": (selected.get("objetivo") or {}).get("motivos", []) if selected else [],
            "descartadas": cached["discarded"]}
        v2_summary["cartera_no_agregadas"] = (portfolio or {}).get("not_added_reasons", [])
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
            "profile_config": profile_config, "exploration_enabled": exploration_enabled,
            "candidates": self._candidate_table(cached),
            "discarded_alternatives": discarded_alternatives,
            "alternatives": alternatives, "pieces": selected_pieces,
            "portfolio": portfolio,
            "current_pieces": current_pieces, "estimated_time": (selected_alternative or {}).get("estimate", {}),
            "piece_estimates": [], "target_usdt": float(goal.get("target_usdt", 0)),
            "deadline_days": cached["horizon_days"],
            "loss_max_usdt": capital * float(profile_config.get("max_loss_pct", 0)) / 100,
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
        exploration_enabled = bool((plan or {}).get("exploration_enabled"))
        probability_values = [float((row.get("estimate") or {}).get("probability") or 0)
            for row in alternatives]
        probability_values.extend(float((row.get("estimate") or {}).get("probability") or 0)
            for row in (plan or {}).get("current_pieces", {}).values())
        mode = "exploracion" if exploration_enabled and max(probability_values or [0]) < .05 else "meta"
        profile_config = (plan or {}).get("profile_config") or {}
        if not links:
            return json_safe({"decision": "no_hacer_nada", "reason": "la meta no tiene grids activos"})
        for link in links:
            grid_id = str(int(link["grid_id"]))
            current = (plan.get("current_pieces") or {}).get(grid_id)
            if not current:
                continue
            current_est = current.get("estimate") or {}
            current_alt = next((row for row in alternatives
                if row.get("cache_candidate_key") and row.get("is_current")
                and int((row.get("pieces") or [{}])[0].get("grid_id") or -1) == int(grid_id)), None)
            if current_alt is None:
                continue
            candidates = [row for row in alternatives if not row.get("is_current")
                and row.get("estimate", {}).get("evidence_sufficient")]
            if mode == "exploracion":
                from research.scenarios.objective import exploration_objective_v2
                for row in candidates:
                    row["objective_exploracion"] = exploration_objective_v2(
                        row.get("metrics") or row.get("estimate") or {},
                        perfil=goal["profile"],
                        capital_meta=float(goal.get("capital_initial") or 1),
                        p_limit_cap=profile_config.get("p_limit_cap"),
                        cut_slippage_expected_max_pct=float(profile_config.get(
                            "cut_slippage_expected_max_pct", .5)))
                safe = [row for row in candidates
                    if row["objective_exploracion"].get("factible")]
                best = max(safe, key=lambda row: (
                    float((row.get("estimate") or {}).get("pnl_mean_usdt") or 0),
                    float((row.get("metrics") or {}).get("cycles_expected") or 0)), default=None)
            else:
                best = next((row for row in candidates
                    if row.get("objective", {}).get("factible")), None)
            if best is None:
                continue
            best_est = best.get("estimate") or {}
            current_portfolio_est = current.get("portfolio_estimate") or current_est
            probability_delta_pp = (float(best_est.get("probability") or 0) -
                float(current_portfolio_est.get("probability",
                    current_portfolio_est.get("p_target", 0)) or 0)) * 100
            current_ci = current_portfolio_est.get("pnl_mean_ci95")
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
            new_net = float(best_est.get("pnl_mean_usdt") or 0) - exit_fee - entry_fee
            if not exploration_enabled:
                new_net -= realized_loss
            keep_floor = float(current_portfolio_est.get("pnl_mean_usdt") or 0) - standard_error
            if (mode != "exploracion" and probability_delta_pp < 5) or new_net < keep_floor:
                continue
            reordered = dict(plan)
            reordered["alternatives"] = [best, *[row for row in alternatives if row is not best]]
            # La rutina existente conserva sus gates de materialidad, persistencia,
            # cooldown, pérdida máxima, límites diarios y cierre verificado.
            return json_safe({"decision": "cambiar", "modo": mode, "reason":
                "alternativa con mejora pareada; falta aplicar los frenos de ejecucion",
                "grid_id": int(grid_id), "alternative_id": best.get("alternative_id"),
                "proposed_action": {"type": "rotar", "grid_id": int(grid_id),
                    "alternative": best, "plan": reordered},
                "comparison_v2": {"probability_improvement_pp": probability_delta_pp,
                "expected_net_usdt": new_net, "keep_mean_usdt": current_portfolio_est.get("pnl_mean_usdt"),
                "keep_standard_error_usdt": standard_error,
                "realized_loss_usdt": 0.0 if exploration_enabled else realized_loss,
                "exit_fee_usdt": exit_fee,
                "entry_fee_usdt": entry_fee,
                "modo": mode,
                "keep": {"symbol": (current_alt.get("pieces") or [{}])[0].get("symbol"),
                    "strategy": (current_alt.get("pieces") or [{}])[0].get("strategy"),
                    "piece": (current_alt.get("pieces") or [{}])[0],
                    "estimate": current_est, "portfolio_estimate": current_portfolio_est},
                "alternative": {"symbol": best.get("symbol"), "strategy": best.get("strategy"),
                    "piece": new_piece, "estimate": best_est},
                "cache_run_id": (plan.get("cache") or {}).get("run_id")}})
        portfolio = (plan or {}).get("portfolio") or {}
        added_keys = {str(row.get("candidate_key")) for row in portfolio.get("added", [])}
        if added_keys:
            addition = next((row for row in alternatives
                if str(row.get("cache_candidate_key")) in added_keys
                and not row.get("is_current")), None)
            if addition is not None:
                # La apertura conserva las validaciones de efectivo, perfil y
                # abribilidad que ya aplica el operador para piezas nuevas.
                if mode == "exploracion":
                    before = portfolio.get("metrics_before") or {}
                    after = portfolio.get("metrics") or {}
                    from research.scenarios.objective import exploration_objective_v2
                    add_objective = exploration_objective_v2(after, perfil=goal["profile"],
                        capital_meta=float(goal.get("capital_initial") or 1),
                        cut_slippage_expected_max_pct=float(profile_config.get(
                            "cut_slippage_expected_max_pct", .5)))
                    if (float(after.get("pnl_mean_usdt") or 0) -
                            float(before.get("pnl_mean_usdt") or 0) < .2 or
                            not add_objective.get("factible")):
                        return json_safe({"decision": "mantener", "modo": mode,
                            "reason": "agregar no mejora la ganancia esperada en 0,2 USDT o incumple riesgo",
                            "comparison_v2": {"metrics_before": before,
                                "metrics_after": after, "objective": add_objective}})
                return json_safe({"decision": "agregar", "modo": mode, "reason":
                    "cartera v2 propone agregar una pieza con efectivo libre",
                    "alternative_id": addition.get("alternative_id"),
                    "proposed_action": {"type": "agregar", "alternative": addition},
                    "comparison_v2": {"portfolio_steps": portfolio.get("steps", []),
                        "modo": mode,
                        "alternative": {"symbol": addition.get("symbol"),
                            "strategy": addition.get("strategy"),
                            "piece": (addition.get("pieces") or [{}])[0],
                            "estimate": addition.get("estimate") or {}},
                        "cache_run_id": (plan.get("cache") or {}).get("run_id")}})
        return json_safe({"decision": "mantener", "modo": mode, "reason":
            "ninguna alternativa supera el umbral pareado de probabilidad y ganancia neta",
            "cache_run_id": (plan.get("cache") or {}).get("run_id")})

    def review(self, operator, goal, valuation, plan):
        """Ejecuta una decisión v2 con sus propios frenos y validaciones comunes."""
        from contextlib import nullcontext
        review_at_s = self._operator_now(operator)
        decision = self.decide(operator, goal, valuation, plan)
        action = decision.pop("proposed_action", None)
        exploration_enabled = bool((plan or {}).get("exploration_enabled"))
        action_key = None
        subject = None
        grid_id = None
        if action:
            alternative = action.get("alternative") or {}
            piece = (alternative.get("pieces") or [{}])[0]
            grid_id = action.get("grid_id")
            subject = str(grid_id) if grid_id is not None else str(
                alternative.get("cache_candidate_key") or piece.get("symbol") or "")
            action_key = self._stable_action_key(action, alternative, piece, grid_id)
        if exploration_enabled:
            brake = self._v2_brake(operator, goal, action_key=action_key,
                subject=subject, grid_id=grid_id, review_at_s=review_at_s)
            if brake:
                return json_safe({"decision": "mantener", "modo": decision.get("modo", "meta"),
                    "reason": brake, "comparison_v2": decision.get("comparison_v2"),
                    "freno": brake})
        if not action:
            return json_safe(decision)
        lock = getattr(operator, "_operation_lock", None)
        with lock if lock is not None else nullcontext():
            if not exploration_enabled and action["type"] == "rotar":
                executed = operator._review_allocations(goal, valuation,
                    plan=action.get("plan") or plan)
            elif not exploration_enabled and action["type"] == "agregar":
                from grid.meta_live_pieces import live_meta_grid_links
                links = live_meta_grid_links(operator.core, int(goal["id"]))
                executed = operator._add_piece_from_free_cash(goal, links,
                    {"alternatives": [action["alternative"]]}, valuation=valuation)
            elif action["type"] == "rotar":
                executed = self._execute_rotation_v2(operator, goal, action)
            elif action["type"] == "agregar":
                from grid.meta_live_pieces import live_meta_grid_links
                links = live_meta_grid_links(operator.core, int(goal["id"]))
                validator = getattr(operator, "_validate_v2_plan_limits", None)
                if callable(validator):
                    validator(goal, {"pieces": action["alternative"].get("pieces", [])})
                executed = operator._add_piece_from_free_cash(goal, links,
                    {"alternatives": [action["alternative"]]}, valuation=valuation)
            else:
                return json_safe({"decision": "no_hacer_nada", "reason": "acción v2 desconocida"})
        if not executed or (isinstance(executed, dict) and
                executed.get("decision") == "no_hacer_nada"):
            if isinstance(executed, dict):
                return json_safe({**executed,
                    "comparison_v2": decision.get("comparison_v2")})
            return json_safe({"decision": "mantener",
                "reason": "la ejecución v2 no se completó",
                "comparison_v2": decision.get("comparison_v2")})
        ledger = getattr(operator, "_ledger", None)
        if callable(ledger):
            ledger(int(goal["id"]), "META_V2_ACTION", {
            "action_key": action_key, "subject": subject,
            "action_type": action["type"], "grid_id": grid_id,
            "alternative_id": action.get("alternative", {}).get("alternative_id"),
            "review_at_s": review_at_s, "result": executed}, grid_id=grid_id)
        return json_safe({**executed, "modo": decision.get("modo", "meta"),
            "comparison_v2": decision.get("comparison_v2")})

    @staticmethod
    def _operator_now(operator):
        import time
        clock = getattr(operator, "clock", None)
        return float(clock()) if callable(clock) else time.time()

    @staticmethod
    def _stable_action_key(action, alternative, piece, grid_id):
        """Construye la identidad de una acción con geometría relativa, sin precios."""
        action_type = str(action.get("type") or "")
        symbol = str(piece.get("symbol") or alternative.get("symbol") or "").upper()
        strategy = ("bot" if piece.get("piece_type") == "individual_bot_grid" else
            str(alternative.get("strategy") or piece.get("strategy") or "").lower())
        levels = int(piece.get("n_levels", piece.get("n", 1)) or 1)
        params = alternative.get("relative_params") or alternative.get("params") or piece
        if strategy == "bot":
            width = "bot:" + ":".join(str(params.get(name, "")) for name in
                ("buy_offset_pct", "sell_offset_pct"))
        else:
            width_value = params.get("width_sigma")
            if width_value is None:
                width_value = params.get("band") or params.get("width") or params.get("range_type") or ""
            width = str(width_value)
        grid = str(grid_id if grid_id is not None else "-")
        return f"{action_type}:grid={grid}|{symbol}:{strategy}:{levels}:{width}"

    @classmethod
    def _v2_brake(cls, operator, goal, *, action_key, subject, grid_id, review_at_s=None):
        rows_reader = getattr(operator, "_meta_ledger_rows", None)
        ledger = getattr(operator, "_ledger", None)
        if not callable(rows_reader) or not callable(ledger):
            return None
        rows = list(rows_reader(int(goal["id"]))) if callable(rows_reader) else []
        prior_reviews = [row for row in rows if row.get("event_type") == "META_V2_REVIEW"]
        now = cls._operator_now(operator) if review_at_s is None else float(review_at_s)
        review = {"action_key": action_key, "subject": subject, "grid_id": grid_id,
            "review_at_s": now}
        ledger(int(goal["id"]), "META_V2_REVIEW", review, grid_id=grid_id)
        if not action_key:
            return None
        previous_streak = []
        for row in reversed(prior_reviews):
            details = row.get("details", {})
            if details.get("action_key") != action_key:
                break
            previous_streak.append(details)
        first_at = (min(float(item.get("review_at_s") or now) for item in previous_streak)
            if previous_streak else None)
        if first_at is None or now - first_at < 840:
            reason = "persistencia v2: se requieren dos revisiones consecutivas separadas por 14 minutos"
            ledger(int(goal["id"]), "META_V2_FRENO", {**review, "reason": reason},
                grid_id=grid_id)
            return reason
        actions = [row.get("details", {}) for row in rows
            if row.get("event_type") == "META_V2_ACTION"]
        recent_day = [item for item in actions if now - float(item.get("review_at_s") or 0) < 86400]
        if len(recent_day) >= 2:
            reason = "límite v2: máximo dos cambios o agregados por meta en 24 horas"
        elif any(item.get("subject") == subject and
                now - float(item.get("review_at_s") or 0) < 6 * 3600 for item in actions):
            reason = "enfriamiento v2: esta pieza ya tuvo una acción en las últimas 6 horas"
        else:
            return None
        ledger(int(goal["id"]), "META_V2_FRENO", {**review, "reason": reason},
            grid_id=grid_id)
        return reason

    @staticmethod
    def _execute_rotation_v2(operator, goal, action):
        grid_id = int(action["grid_id"])
        grid = operator.db.get_grid(grid_id)
        if grid is None or str(grid.get("status") or "").upper() not in {"ACTIVE", "PAUSED", "HOLDING"}:
            raise ValueError("la pieza v2 ya no está activa")
        alternative = action["alternative"]
        piece = dict((alternative.get("pieces") or [{}])[0])
        levels = operator.db.get_grid_levels(grid_id)
        operator._validate_v2_plan_limits(goal, {"pieces": [piece]},
            replacing_grid_id=grid_id)
        openability = operator._testnet_openability(piece, goal,
            replacing_grid_id=grid_id)
        if not openability.get("allowed"):
            raise ValueError(f"alternativa no abrible: {openability.get('reason')}")
        has_inventory = any(float(row.get("held_qty") or 0) > 0 for row in levels)
        mode = "liquidate" if has_inventory else "cancel"
        closed = operator.engine.close_grid(grid_id, mode,
            close_reason="META_V2_ROTATION")
        if str((closed or {}).get("status") or "").upper() not in {"CLOSED", "CANCELLED", "DONE"}:
            return {"decision": "no_hacer_nada", "reason": "cierre v2 no verificado",
                "grid_id": grid_id, "close": closed}
        opened = operator._open_replacement(goal, alternative,
            float(piece.get("capital_usdt") or grid.get("capital_total") or 0))
        operator._ledger(int(goal["id"]), "META_V2_ROTATION", {
            "grid_id": grid_id, "closed": closed, "opened": opened,
            "alternative_id": alternative.get("alternative_id"),
            "openability": openability}, grid_id=grid_id)
        return {"decision": "rotar_v2", "grid_id": grid_id, "closed": closed,
            "opened": opened, "alternative_id": alternative.get("alternative_id")}

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
