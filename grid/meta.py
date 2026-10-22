"""Núcleo aislado de metas: valoración, propiedad, vigilancia y bitácora."""
from __future__ import annotations

import json
import uuid
import threading
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from apscheduler.schedulers.background import BackgroundScheduler

FEE = Decimal("0.001")
DEFAULT_PROFILES = {
    "PRUDENTE": {"capital_per_coin_pct": 40, "max_grids": 2, "max_bots": 4,
                 "max_loss_pct": 8, "warning_loss_pct": 6, "max_days": 45, "loan_cap_pct": 30,
                 "sigma_q33": 0.333333, "sigma_q67": 0.666667, "min_margin_pct": 0.7,
                 "plan_price_drift_pct": 1.0, "signal_logit_cap": 1.0,
                 "signal_weights": {"volatility": 0.25, "break_prob": 0.25,
                 "direction": 0.25, "scanner": 0.25, "advisor": 0.25}},
    "RAPIDO": {"capital_per_coin_pct": 70, "max_grids": 4, "max_bots": 8,
               "max_loss_pct": 18, "warning_loss_pct": 13.5, "max_days": 20, "loan_cap_pct": 50,
               "sigma_q33": 0.333333, "sigma_q67": 0.666667, "min_margin_pct": 0.3,
               "plan_price_drift_pct": 1.0, "signal_logit_cap": 1.0,
               "signal_weights": {"volatility": 0.25, "break_prob": 0.25,
               "direction": 0.25, "scanner": 0.25, "advisor": 0.25}},
}


def _d(value: Any) -> Decimal:
    return Decimal(str(value or 0))


def attributable_cash(capital_initial, delivered, returned, commissions) -> Decimal:
    """Efectivo lógico atribuible: capital − entregas + devoluciones − comisiones."""
    return _d(capital_initial) - _d(delivered) + _d(returned) - _d(commissions)


def smart_grid_params_for_goal(goal: dict, *, target_usdt: float | None = None) -> dict:
    """Parámetros de crédito propios del perfil Meta para un grid Smart."""
    config = goal.get("config_snapshot") or {}
    loan_cap = float(config.get("loan_cap_pct", 0))
    params = {"target_usdt": float(goal["target_usdt"] if target_usdt is None else target_usdt), "target_basis": "cash",
              "loans_enabled": loan_cap > 0}
    if loan_cap > 0:
        params.update({"loan_cap_pct": loan_cap, "loan_topup_pct": loan_cap,
                       "loan_lender_max_pct": loan_cap, "reserve_pct": 0.0})
    return params


class MetaCore:
    def __init__(self, db, exchange, engine=None, *, settings=None, monitor=None,
                 scheduler=None, clock=None, alert=None):
        self.db, self.exchange, self.engine = db, exchange, engine
        self.settings, self.monitor = settings, monitor
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.alert = alert or (lambda _kind, _meta, _details: None)
        self.scheduler = scheduler or BackgroundScheduler(timezone="UTC")
        self._lock = threading.RLock()
        self._cancel_plans: dict[str, tuple[datetime, tuple[int, ...]]] = {}

    def _now(self):
        return self.clock().astimezone(timezone.utc).replace(tzinfo=None)

    def _profile_settings(self):
        from sqlalchemy import select
        with self.db.engine.connect() as conn:
            rows = conn.execute(select(self.db.meta_profile_settings)).mappings().all()
        values = {r["profile"]: json.loads(r["settings"]) for r in rows}
        merged = {}
        for profile, defaults in DEFAULT_PROFILES.items():
            stored = values.get(profile, {})
            merged[profile] = {**defaults, **stored}
            if isinstance(defaults.get("signal_weights"), dict):
                merged[profile]["signal_weights"] = {**defaults["signal_weights"],
                    **(stored.get("signal_weights") or {})}
        return {**merged, **{k: v for k, v in values.items() if k not in DEFAULT_PROFILES}}

    def config(self):
        from sqlalchemy import select
        with self.db.engine.connect() as conn:
            rows = conn.execute(select(self.db.meta_profile_settings)).mappings().all()
        profiles = self._profile_settings()
        return {"enabled": bool(getattr(self.settings, "meta_mode_enabled", False)),
                "max_active": int(profiles.get("GLOBAL", {}).get("max_active", 1)),
                "profiles": {k: v for k, v in profiles.items() if k in DEFAULT_PROFILES}}

    def set_max_active(self, value):
        from sqlalchemy import select
        value = int(value)
        if value < 1 or value > 10:
            raise ValueError("MAX_METAS_ACTIVAS debe estar entre 1 y 10.")
        with self.db.engine.begin() as conn:
            present = conn.execute(select(self.db.meta_profile_settings.c.profile).where(
                self.db.meta_profile_settings.c.profile == "GLOBAL")).first()
            values = {"max_active": value}
            if present:
                conn.execute(self.db.meta_profile_settings.update().where(
                    self.db.meta_profile_settings.c.profile == "GLOBAL").values(
                        settings=json.dumps(values), updated_at=self._now()))
            else:
                conn.execute(self.db.meta_profile_settings.insert().values(
                    profile="GLOBAL", settings=json.dumps(values), updated_at=self._now()))
        return value

    def set_config(self, profile: str, values: dict):
        from sqlalchemy import select
        profile = profile.upper()
        if profile not in DEFAULT_PROFILES:
            raise ValueError("Perfil inválido.")
        allowed = set(DEFAULT_PROFILES[profile])
        if set(values) - allowed:
            raise ValueError("Parámetros no permitidos.")
        merged = {**DEFAULT_PROFILES[profile], **values}
        if "signal_weights" in values:
            if not isinstance(values["signal_weights"], dict):
                raise ValueError("signal_weights debe ser un objeto de pesos.")
            merged["signal_weights"] = {**DEFAULT_PROFILES[profile]["signal_weights"],
                                         **values["signal_weights"]}
        scalar_values = [v for k, v in merged.items() if k != "signal_weights"]
        scalar_values.extend((merged.get("signal_weights") or {}).values())
        if any(Decimal(str(v)) < 0 for v in scalar_values):
            raise ValueError("Los parámetros deben ser positivos.")
        weights = merged.get("signal_weights")
        if not isinstance(weights, dict) or set(weights) - {"volatility", "break_prob", "direction", "scanner", "advisor"}:
            raise ValueError("signal_weights debe contener solo señales Meta conocidas.")
        if not 0 < float(merged["sigma_q33"]) < float(merged["sigma_q67"]) < 1:
            raise ValueError("sigma_q33 y sigma_q67 deben ser cuantiles crecientes entre 0 y 1.")
        with self.db.engine.begin() as conn:
            found = conn.execute(select(self.db.meta_profile_settings.c.profile).where(
                self.db.meta_profile_settings.c.profile == profile)).first()
            payload = json.dumps(merged, sort_keys=True)
            if found:
                conn.execute(self.db.meta_profile_settings.update().where(
                    self.db.meta_profile_settings.c.profile == profile).values(settings=payload, updated_at=self._now()))
            else:
                conn.execute(self.db.meta_profile_settings.insert().values(
                    profile=profile, settings=payload, updated_at=self._now()))
        return merged

    def _enabled_testnet(self):
        if not bool(getattr(self.settings, "meta_mode_enabled", False)):
            raise ValueError("Modo Meta apagado.")
        client = self.exchange
        if getattr(client, "testnet", getattr(getattr(client, "client", None), "testnet", False)) is not True:
            raise ValueError("Modo Meta solo está disponible en Testnet.")

    def _lock_capacity_row(self, conn):
        """Serializa create/start por la fila global de capacidad en la BD."""
        from sqlalchemy import update
        table = self.db.meta_profile_settings
        conn.execute(update(table).where(table.c.profile == "GLOBAL").values(
            updated_at=table.c.updated_at))

    def create_goal(self, capital, target_value, target_type="USDT", profile="PRUDENTE"):
        from sqlalchemy import func, select
        self._enabled_testnet()
        capital, target_value = _d(capital), _d(target_value)
        profile, target_type = profile.upper(), target_type.upper()
        if capital <= 0 or target_value <= 0 or profile not in DEFAULT_PROFILES or target_type not in {"USDT", "PCT"}:
            raise ValueError("Capital, objetivo, perfil o tipo inválido.")
        target = target_value if target_type == "USDT" else capital * target_value / 100
        if target <= 0:
            raise ValueError("El objetivo convertido debe ser positivo.")
        now = self._now()
        with self.db.engine.begin() as conn:
            # Serializa la reserva en la base: no crear una segunda meta activa.
            self._lock_capacity_row(conn)
            active = conn.execute(select(func.count()).select_from(self.db.meta_goals).where(
                self.db.meta_goals.c.status.in_(["ACTIVE", "PIECES_BLOCKED", "CLOSING", "TARGET_CLOSING"]))).scalar_one()
            maximum = int(self.config()["max_active"])
            if active >= maximum:
                raise ValueError("Ya existe una meta activa.")
            goal_id = conn.execute(self.db.meta_goals.insert().values(
                capital_initial=float(capital), target_usdt=float(target), target_input_type=target_type,
                target_input_value=float(target_value), profile=profile, status="DRAFT", environment="testnet",
                created_at=now, config_snapshot=json.dumps(self._profile_settings()[profile], sort_keys=True)
            )).inserted_primary_key[0]
        self._ledger(goal_id, None, "META_CREATED", {"target_usdt": str(target), "profile": profile})
        return self.get_goal(goal_id)

    def _ledger(self, meta_id, run_id, event_type, details=None, reason=None, grid_id=None, level_idx=None):
        with self.db.engine.begin() as conn:
            conn.execute(self.db.meta_ledger.insert().values(
                meta_id=int(meta_id), run_id=run_id, ts=self._now(), event_type=event_type, reason=reason,
                grid_id=grid_id, level_idx=level_idx, details=json.dumps(details or {}, sort_keys=True, default=str)))
        if event_type in {"META_LOSS_WARNING", "META_LOSS_CUT", "META_CORTADA_POR_PERDIDA", "META_VENCIDA", "META_TARGET_MET", "META_EXPIRED"}:
            self.alert(event_type, meta_id, details or {})

    def get_goal(self, meta_id):
        from sqlalchemy import func, select
        with self.db.engine.connect() as conn:
            row = conn.execute(select(self.db.meta_goals).where(self.db.meta_goals.c.id == int(meta_id))).mappings().first()
        if row is None:
            raise ValueError("Meta no encontrada.")
        result = dict(row)
        result["config_snapshot"] = json.loads(result["config_snapshot"] or "{}")
        return result

    def list_goals(self):
        from sqlalchemy import select
        with self.db.engine.connect() as conn:
            rows = conn.execute(select(self.db.meta_goals).order_by(self.db.meta_goals.c.id.desc())).mappings().all()
        return [dict(row) for row in rows]

    def _owned_grids(self, meta_id):
        from sqlalchemy import select
        with self.db.engine.connect() as conn:
            rows = conn.execute(select(self.db.meta_piece_links).where(
                self.db.meta_piece_links.c.meta_id == int(meta_id),
                self.db.meta_piece_links.c.released_at.is_(None))).mappings().all()
        return [dict(row) for row in rows]

    def _available_usdt(self):
        balances = self.exchange.get_balance("USDT")
        free = _d(balances.get("USDT", {}).get("free"))
        # Reservas contabilizadas por grids ajenos, además de órdenes ya bloqueadas por el exchange.
        from sqlalchemy import select
        with self.db.engine.connect() as conn:
            rows = conn.execute(select(self.db.grids).where(self.db.grids.c.status.in_(["ACTIVE", "PAUSED", "CLOSING"]))).mappings().all()
        reserved = Decimal(0)
        for row in rows:
            grid = dict(row)
            levels = self.db.get_grid_levels(int(grid["id"]))
            reserved += sum((_d(level.get("capital")) for level in levels
                             if level.get("state") in {"IDLE", "WAITING", "BUY_PENDING"}
                             and not level.get("order_id") and _d(level.get("held_qty")) <= 0), Decimal(0))
        return max(Decimal(0), free - reserved), reserved

    def start_goal(self, meta_id):
        from sqlalchemy import func, select
        self._enabled_testnet()
        goal = self.get_goal(meta_id)
        if goal["status"] != "DRAFT":
            raise ValueError("La meta no está en estado DRAFT.")
        free, reserved = self._available_usdt()
        capital = _d(goal["capital_initial"])
        if free < capital:
            self._ledger(meta_id, None, "META_START_REJECTED", {"free_usdt": str(free), "other_grids_reserved_usdt": str(reserved)})
            raise ValueError(f"USDT libre insuficiente: disponible {free}; capital requerido {capital}.")
        now = self._now()
        with self.db.engine.begin() as conn:
            self._lock_capacity_row(conn)
            active = conn.execute(select(func.count()).select_from(self.db.meta_goals).where(
                self.db.meta_goals.c.status.in_(["ACTIVE", "PIECES_BLOCKED", "CLOSING", "TARGET_CLOSING"]))).scalar_one()
            if active >= int(self.config()["max_active"]):
                raise ValueError("Ya existe una meta activa.")
            conn.execute(self.db.meta_goals.update().where(self.db.meta_goals.c.id == int(meta_id),
                self.db.meta_goals.c.status == "DRAFT").values(status="ACTIVE", started_at=now))
            run_id = conn.execute(self.db.meta_runs.insert().values(meta_id=int(meta_id), status="ACTIVE",
                started_at=now, worst_result_pct=0.0, realized_extra_usdt=0.0)).inserted_primary_key[0]
        self._ledger(meta_id, run_id, "META_STARTED", {"reserved_capital_usdt": str(capital),
                     "exchange_free_usdt": str(free), "other_grids_reserved_usdt": str(reserved)})
        return self.get_goal(meta_id)

    def link_grid(self, meta_id, grid_id, piece_type="grid"):
        from sqlalchemy import func, select
        self._enabled_testnet()
        if piece_type not in {"grid", "individual_bot_grid"}:
            raise ValueError("Tipo de pieza Meta no permitido.")
        goal = self.get_goal(meta_id)
        if goal["status"] != "ACTIVE":
            raise ValueError("Solo se pueden ligar piezas a una meta activa.")
        grid = self.db.get_grid(int(grid_id))
        if grid is None or grid.get("environment") != "testnet" or grid.get("status") not in {"ACTIVE", "PAUSED"}:
            raise ValueError("La pieza debe ser un grid Testnet abierto.")
        levels = self.db.get_grid_levels(int(grid_id))
        if not levels:
            raise ValueError("El grid no contiene niveles ligables.")
        cfg = goal.get("config_snapshot") or {}
        linked = [row for row in self._owned_grids(meta_id)
                  if (self.db.get_grid(int(row["grid_id"])) or {}).get("status")
                  in {"OPENING", "ACTIVE", "PAUSED", "CLOSING", "HOLDING"}]
        bot_piece = piece_type == "individual_bot_grid"
        if bot_piece and len(levels) != 1:
            raise ValueError("Una pieza bot individual debe ser un grid de un solo nivel.")
        max_count = int(cfg.get("max_bots" if bot_piece else "max_grids", 0))
        count = sum(1 for row in linked if (row["piece_type"] == "individual_bot_grid") == bot_piece)
        if count >= max_count:
            raise ValueError("Se alcanzó el máximo de piezas del perfil.")
        symbol_cap = _d(goal["capital_initial"]) * _d(cfg.get("capital_per_coin_pct", 0)) / 100
        same_symbol_cap = _d(grid["capital_total"])
        for row in linked:
            existing = self.db.get_grid(int(row["grid_id"]))
            if existing and existing["symbol"] == grid["symbol"]:
                same_symbol_cap += _d(existing["capital_total"])
        if same_symbol_cap > symbol_cap:
            raise ValueError("El capital ligado en esta moneda supera el límite del perfil.")
        with self.db.engine.begin() as conn:
            prior = conn.execute(select(self.db.meta_piece_links.c.id).where(
                self.db.meta_piece_links.c.grid_id == int(grid_id),
                self.db.meta_piece_links.c.released_at.is_(None))).first()
            if prior:
                raise ValueError("El grid ya pertenece a una meta.")
            run = conn.execute(select(self.db.meta_runs.c.id).where(
                self.db.meta_runs.c.meta_id == int(meta_id), self.db.meta_runs.c.status == "ACTIVE").order_by(
                    self.db.meta_runs.c.id.desc())).first()
            conn.execute(self.db.meta_piece_links.insert().values(meta_id=int(meta_id), run_id=run[0] if run else None,
                grid_id=int(grid_id), piece_type=piece_type, origin_grid_id=int(grid_id),
                created_at=self._now()))
        self._ledger(meta_id, run[0] if run else None, "META_PIECE_LINKED", {"piece_type": piece_type}, grid_id=int(grid_id))
        return {"meta_id": int(meta_id), "grid_id": int(grid_id), "piece_type": piece_type}

    def valuation(self, meta_id):
        goal = self.get_goal(meta_id)
        links = self._owned_grids(meta_id)
        delivered = returned = commissions = inventory = Decimal(0)
        try:
            for link in links:
                grid = self.db.get_grid(int(link["grid_id"]))
                if grid is None:
                    raise ValueError("grid eliminado")
                events = list(reversed(self.db.list_grid_events(grid_id=int(grid["id"]), limit=100000)))
                repo_id = link.get("repository_grid_id")
                level_map = json.loads(link.get("repository_level_map") or "{}")
                if repo_id is not None:
                    repo_events = self.db.list_grid_events(grid_id=int(repo_id), limit=100000)
                    moved_ids = {int(idx) for idx in level_map.values()}
                    events.extend(event for event in reversed(repo_events)
                                  if event.get("level_idx") is not None and int(event["level_idx"]) in moved_ids)
                for event in events:
                    details = event.get("details") or {}
                    if event.get("event_type") == "BUY_FILLED":
                        quote, fee = self._fill_cash(grid, event, details)
                        delivered += quote
                        commissions += fee
                    elif event.get("event_type") in {"SELL_FILLED", "CELL_LIQUIDATED"}:
                        if event.get("event_type") == "CELL_LIQUIDATED" and details.get("cash_proceeds_net") is not None:
                            returned += _d(details["cash_proceeds_net"])
                        else:
                            quote, fee = self._fill_cash(grid, event, details)
                            returned += quote
                            commissions += fee
                book = self.exchange.get_book_ticker(grid["symbol"])
                bid = _d(book.get("bid_price"))
                if bid <= 0:
                    raise ValueError(f"BOOK_ONE_SIDED:{grid['symbol']}")
                if repo_id is None:
                    inventory_levels = self.db.get_grid_levels(int(grid["id"]))
                else:
                    mapped_ids = {int(idx) for idx in level_map.values()}
                    inventory_levels = [level for level in self.db.get_grid_levels(int(repo_id))
                                        if int(level["level_idx"]) in mapped_ids]
                for level in inventory_levels:
                    inventory += _d(level.get("held_qty")) * bid
            cash = attributable_cash(goal["capital_initial"], delivered, returned, commissions)
            value = cash + inventory * (Decimal(1) - FEE)
            result = (value - _d(goal["capital_initial"])) / _d(goal["capital_initial"]) * 100
            return {"status": "KNOWN", "cash_usdt": str(cash), "inventory_bid_gross_usdt": str(inventory),
                    "value_usdt": str(value), "result_pct": str(result), "estimated_sell_fee_pct": "0.1"}
        except Exception as exc:
            now = self._now()
            self._ledger(meta_id, None, "META_UNKNOWN", {"error": str(exc)})
            # Se vuelve a alertar si el estado desconocido persiste 30 minutos.
            previous = getattr(self, "_unknown_since", {}).get(int(meta_id))
            if not hasattr(self, "_unknown_since"):
                self._unknown_since = {}
            if previous is None or (now - previous).total_seconds() >= 1800:
                self._unknown_since[int(meta_id)] = now
                self.alert("META_UNKNOWN", int(meta_id), {"error": str(exc), "since": str(now)})
            return {"status": "UNKNOWN", "reason": str(exc)}

    def _fill_cash(self, grid, event, details):
        quote_value = details.get("cummulative_quote_qty", details.get("quote_qty", details.get("executed_quote_qty")))
        fee_value = details.get("commission_usdt", details.get("fee_usdt"))
        if quote_value is None and event.get("order_id") is not None:
            order = self.exchange.get_order(grid["symbol"], order_id=int(event["order_id"]))
            quote_value = order.get("cummulative_quote_qty")
            if fee_value is None and self.engine is not None:
                trades = self.exchange.get_my_trades(grid["symbol"], int(event["order_id"]))
                fee_value = self.engine._fee_value_usdt(trades, grid["symbol"])
        if quote_value is None or fee_value is None:
            raise ValueError("fill sin atribución de USDT o comisión verificable")
        return _d(quote_value), _d(fee_value)

    def run_once(self):
        from sqlalchemy import select
        with self._lock:
            with self.db.engine.connect() as conn:
                goals = conn.execute(select(self.db.meta_goals).where(
                self.db.meta_goals.c.status.in_(["ACTIVE", "PIECES_BLOCKED", "SURPLUS_WAIT", "CLOSING", "TARGET_CLOSING"]))).mappings().all()
            results = []
            for raw in goals:
                goal = dict(raw)
                goal["config_snapshot"] = json.loads(goal.get("config_snapshot") or "{}")
                if goal["status"] == "TARGET_CLOSING":
                    self._target_met(goal)
                    results.append({"meta_id": goal["id"], "status": "TARGET_CLOSING_RETRIED"})
                    continue
                if goal["status"] == "CLOSING":
                    self._resume_close(goal)
                    results.append({"meta_id": goal["id"], "status": "CLOSING_RETRIED"})
                    continue
                valuation = self.valuation(goal["id"])
                if valuation["status"] != "KNOWN":
                    if goal["status"] == "ACTIVE":
                        with self.db.engine.begin() as conn:
                            conn.execute(self.db.meta_goals.update().where(self.db.meta_goals.c.id == goal["id"]).values(status="PIECES_BLOCKED"))
                        self._ledger(goal["id"], None, "META_PIECES_BLOCKED", {"reason": "valuation_unknown"})
                    results.append({"meta_id": goal["id"], "valuation": valuation, "new_pieces_allowed": False})
                    continue
                if goal["status"] == "PIECES_BLOCKED":
                    with self.db.engine.begin() as conn:
                        conn.execute(self.db.meta_goals.update().where(self.db.meta_goals.c.id == goal["id"]).values(status="ACTIVE"))
                    self._ledger(goal["id"], None, "META_VALUATION_RECOVERED", {})
                    self._unknown_since.pop(int(goal["id"]), None)
                result_pct = _d(valuation["result_pct"])
                with self.db.engine.begin() as conn:
                    active_run = conn.execute(select(self.db.meta_runs).where(
                        self.db.meta_runs.c.meta_id == goal["id"],
                        self.db.meta_runs.c.status == "ACTIVE").order_by(self.db.meta_runs.c.id.desc())).mappings().first()
                    if active_run:
                        conn.execute(self.db.meta_runs.update().where(self.db.meta_runs.c.id == active_run["id"]).values(
                            worst_result_pct=min(float(active_run.get("worst_result_pct") or 0), float(result_pct))))
                cfg = goal["config_snapshot"]
                if goal["status"] == "SURPLUS_WAIT":
                    self._ledger(goal["id"], None, "META_SURPLUS_VALUATION", {
                        "value_usdt": valuation["value_usdt"], "result_pct": valuation["result_pct"],
                        "cash_usdt": valuation["cash_usdt"], "inventory_bid_gross_usdt": valuation["inventory_bid_gross_usdt"],
                        "decision": "mantener_ventas_vivas_sin_recomprar"})
                    self._update_realized_extra(goal)
                    if not self._surplus_inventory_remains(goal["id"]):
                        with self.db.engine.begin() as conn:
                            conn.execute(self.db.meta_goals.update().where(
                                self.db.meta_goals.c.id == goal["id"]).values(status="CUMPLIDA", finished_at=self._now()))
                        self._ledger(goal["id"], None, "META_SURPLUS_FINISHED", {})
                    results.append({"meta_id": goal["id"], "valuation": valuation, "surplus_wait": True})
                    continue
                self._ledger(goal["id"], None, "META_VALUATION", {
                    "status": goal["status"], "value_usdt": valuation["value_usdt"],
                    "result_pct": valuation["result_pct"], "cash_usdt": valuation["cash_usdt"],
                    "inventory_bid_gross_usdt": valuation["inventory_bid_gross_usdt"],
                    "profile": goal["profile"], "decision": "revisar_limites_y_objetivo"})
                warn = -_d(cfg.get("max_loss_pct", 8)) * Decimal("0.75")
                if goal["status"] == "ACTIVE" and result_pct <= warn:
                    with self.db.engine.begin() as conn:
                        conn.execute(self.db.meta_goals.update().where(self.db.meta_goals.c.id == goal["id"]).values(status="PIECES_BLOCKED"))
                    self._ledger(goal["id"], None, "META_LOSS_WARNING", {"result_pct": str(result_pct), "threshold_pct": str(warn)})
                limit = -_d(cfg.get("max_loss_pct", 8))
                if goal["status"] == "TARGET_CLOSING":
                    self._target_met(goal)
                elif goal["status"] in {"ACTIVE", "PIECES_BLOCKED"} and result_pct <= limit:
                    self._close_owned(goal, "CORTADA_POR_PERDIDA")
                elif goal["status"] in {"ACTIVE", "PIECES_BLOCKED"} and _d(valuation["cash_usdt"]) >= _d(goal["capital_initial"]) + _d(goal["target_usdt"]):
                    self._target_met(goal)
                elif goal["status"] in {"ACTIVE", "PIECES_BLOCKED"} and self._now() - goal["started_at"] >= timedelta(days=int(cfg.get("max_days", 45))):
                    self._close_owned(goal, "VENCIDA")
                results.append({"meta_id": goal["id"], "valuation": valuation})
            return results

    def _close_owned(self, goal, result_status):
        if self.engine is None:
            raise RuntimeError("Grid Engine no está disponible para cierre verificado.")
        lock = getattr(self.monitor, "ops_lock", self._lock)
        pending = []
        with self.db.engine.begin() as conn:
            conn.execute(self.db.meta_goals.update().where(self.db.meta_goals.c.id == goal["id"]).values(
                status="CLOSING", close_mode="liquidate", close_result=result_status))
        with lock:
            for link in self._owned_grids(goal["id"]):
                outcome = self.engine.close_grid(int(link["grid_id"]), "liquidate", close_reason=f"meta_{result_status.lower()}")
                if outcome.get("status") != "CLOSED":
                    pending.append({"grid_id": link["grid_id"], "status": outcome.get("status")})
            if pending:
                with self.db.engine.begin() as conn:
                    conn.execute(self.db.meta_goals.update().where(self.db.meta_goals.c.id == goal["id"]).values(status="CLOSING"))
                self._ledger(goal["id"], None, "META_CLOSE_PENDING", {"result": result_status, "grids": pending})
            else:
                if result_status in {"CORTADA_POR_PERDIDA", "VENCIDA"}:
                    self._record_close_slippage(goal)
                self._finish(goal["id"], result_status)

    def _record_close_slippage(self, goal):
        final_value = self.valuation(goal["id"])
        if final_value.get("status") != "KNOWN":
            return
        threshold = _d(goal["capital_initial"]) * (Decimal(1) - _d((goal.get("config_snapshot") or {}).get("max_loss_pct", 8)) / 100)
        slip = max(Decimal(0), threshold - _d(final_value.get("value_usdt")))
        with self.db.engine.begin() as conn:
            conn.execute(self.db.meta_runs.update().where(
                self.db.meta_runs.c.meta_id == goal["id"], self.db.meta_runs.c.status == "ACTIVE"
            ).values(close_slippage_usdt=float(slip)))

    def _target_met(self, goal):
        # Se cancela solo el tramo comprador y se conservan ventas vivas.
        pending = []
        lock = getattr(self.monitor, "ops_lock", self._lock)
        with lock:
            for link in self._owned_grids(goal["id"]):
                grid = self.db.get_grid(int(link["grid_id"]))
                if grid and grid.get("status") == "ACTIVE":
                    self.db.update_grid(int(link["grid_id"]), status="PAUSED")
                result = self.engine.cancel_grid_orders(int(link["grid_id"]), finalize=False, buy_only=True)
                if result.get("cancel_errors") or result.get("pending_order_ids"):
                    pending.append({"grid_id": link["grid_id"], "errors": result.get("cancel_errors"),
                                    "pending_order_ids": result.get("pending_order_ids")})
        if pending:
            with self.db.engine.begin() as conn:
                conn.execute(self.db.meta_goals.update().where(self.db.meta_goals.c.id == goal["id"]).values(status="TARGET_CLOSING"))
            self._ledger(goal["id"], None, "META_TARGET_CANCEL_PENDING", {"grids": pending})
            return
        self._finish(goal["id"], "CUMPLIDA", keep_surplus=True)
        self.alert("META_TARGET_MET", goal["id"], {})

    def _update_realized_extra(self, goal):
        """Acumula ventas netas posteriores a cumplir, sin volver a contar eventos."""
        from sqlalchemy import select
        with self.db.engine.connect() as conn:
            run = conn.execute(select(self.db.meta_runs).where(
                self.db.meta_runs.c.meta_id == int(goal["id"])).order_by(
                    self.db.meta_runs.c.id.desc())).mappings().first()
        if run is None or run["finished_at"] is None:
            return Decimal(0)
        total = Decimal(0)
        for link in self._owned_grids(goal["id"]):
            grids = [int(link["grid_id"])]
            level_map = json.loads(link.get("repository_level_map") or "{}")
            if link.get("repository_grid_id") is not None:
                grids.append(int(link["repository_grid_id"]))
            for grid_id in grids:
                for event in self.db.list_grid_events(grid_id=grid_id, limit=100000):
                    if event.get("event_type") not in {"SELL_FILLED", "CELL_LIQUIDATED"}:
                        continue
                    if event.get("ts") and event["ts"] <= run["finished_at"]:
                        continue
                    if grid_id != int(link["grid_id"]) and event.get("level_idx") not in {
                            int(value) for value in level_map.values()}:
                        continue
                    details = event.get("details") or {}
                    total += _d(details.get("cummulative_quote_qty", details.get("cash_proceeds_net", 0)))
                    total -= _d(details.get("fee_usdt"))
        previous = _d(run.get("realized_extra_usdt"))
        if total != previous:
            with self.db.engine.begin() as conn:
                conn.execute(self.db.meta_runs.update().where(self.db.meta_runs.c.id == run["id"]).values(
                    realized_extra_usdt=float(total)))
            self._ledger(goal["id"], run["id"], "META_EXTRA_PROFIT_UPDATED",
                         {"previous_usdt": str(previous), "total_usdt": str(total)})
        return total

    def _surplus_inventory_remains(self, meta_id):
        for link in self._owned_grids(meta_id):
            if link.get("repository_grid_id") is None:
                grid_id = int(link["grid_id"])
                indices = None
            else:
                grid_id = int(link["repository_grid_id"])
                indices = {int(value) for value in json.loads(link.get("repository_level_map") or "{}").values()}
            for level in self.db.get_grid_levels(grid_id):
                if indices is not None and int(level["level_idx"]) not in indices:
                    continue
                if _d(level.get("held_qty")) > 0 or level.get("state") == "SELL_OPEN":
                    return True
        return False

    def _finish(self, meta_id, status, keep_surplus=False):
        now = self._now()
        state = "SURPLUS_WAIT" if keep_surplus else status
        with self.db.engine.begin() as conn:
            conn.execute(self.db.meta_goals.update().where(self.db.meta_goals.c.id == int(meta_id)).values(
                status=state, finished_at=None if keep_surplus else now, close_mode=None, close_result=None))
            conn.execute(self.db.meta_runs.update().where(self.db.meta_runs.c.meta_id == int(meta_id),
                self.db.meta_runs.c.status == "ACTIVE").values(status=status, finished_at=now))
        self._ledger(meta_id, None, "META_" + status, {"surplus_wait": keep_surplus})

    def cancellation_plan(self, meta_id):
        valuation = self.valuation(meta_id)
        links = self._owned_grids(meta_id)
        pieces = []
        for link in links:
            grid = self.db.get_grid(int(link["grid_id"]))
            if grid is None:
                continue
            book = self.exchange.get_book_ticker(grid["symbol"])
            bid = _d(book.get("bid_price"))
            levels = self.db.get_grid_levels(int(link.get("repository_grid_id") or link["grid_id"]))
            estimate = Decimal(0)
            for level in levels:
                qty, entry = _d(level.get("held_qty")), _d(level.get("entry_price"))
                if qty > 0 and bid > 0:
                    estimate += qty * bid * (Decimal(1) - FEE) - qty * entry
                estimate += _d(level.get("pnl"))
            pieces.append({"grid_id": link["grid_id"], "symbol": grid["symbol"],
                           "estimated_pnl_usdt": str(estimate), "repository_grid_id": link.get("repository_grid_id")})
        token = uuid.uuid4().hex
        ids = tuple(sorted(int(link["grid_id"]) for link in links))
        self._cancel_plans[token] = (self._now() + timedelta(minutes=5), ids)
        return {"meta_id": int(meta_id), "valuation": valuation, "pieces": pieces,
                "choices": ["liquidate", "repository"], "plan_id": token, "expires_in_seconds": 300}

    def cancel(self, meta_id, mode, confirm=False, plan_id=None):
        self._enabled_testnet()
        goal = self.get_goal(meta_id)
        if goal["status"] not in {"ACTIVE", "PIECES_BLOCKED", "SURPLUS_WAIT", "CLOSING"}:
            raise ValueError("La meta ya no admite cancelación.")
        if not confirm:
            raise ValueError("Se requiere confirm=true después de consultar el plan.")
        plan = self._cancel_plans.pop(str(plan_id or ""), None)
        if plan is None or plan[0] < self._now():
            raise ValueError("Plan de cancelación ausente o vencido; consulta uno nuevo.")
        current_ids = tuple(sorted(int(link["grid_id"]) for link in self._owned_grids(meta_id)))
        if plan[1] != current_ids:
            raise ValueError("Las piezas cambiaron desde el plan; consulta uno nuevo.")
        if mode not in {"liquidate", "repository"}:
            raise ValueError("Modo de cierre inválido.")
        if any(link.get("repository_grid_id") is not None for link in self._owned_grids(meta_id)):
            raise ValueError("El repositorio contiene propiedad compartida; cierre posterior requiere cierre verificado por nivel.")
        lock = getattr(self.monitor, "ops_lock", self._lock)
        pending = []
        with self.db.engine.begin() as conn:
            conn.execute(self.db.meta_goals.update().where(self.db.meta_goals.c.id == int(meta_id)).values(
                status="CLOSING", close_mode=mode, close_result="CANCELADA"))
        with lock:
            for link in self._owned_grids(meta_id):
                outcome = self.engine.close_grid(int(link["grid_id"]), mode, close_reason="meta_cancel",
                    meta_id=int(meta_id) if mode == "repository" else None)
                if outcome.get("status") != "CLOSED":
                    pending.append({"grid_id": link["grid_id"], "status": outcome.get("status")})
        if pending:
            self._ledger(meta_id, None, "META_CANCEL_PENDING", {"mode": mode, "grids": pending})
            return self.get_goal(meta_id)
        self._finish(meta_id, "CANCELADA")
        self._ledger(meta_id, None, "META_CANCELLED", {"mode": mode})
        return self.get_goal(meta_id)

    def ledger(self, meta_id):
        from sqlalchemy import select
        with self.db.engine.connect() as conn:
            rows = conn.execute(select(self.db.meta_ledger).where(
                self.db.meta_ledger.c.meta_id == int(meta_id)).order_by(self.db.meta_ledger.c.id)).mappings().all()
        result = [dict(row) for row in rows]
        for row in result:
            row["details"] = json.loads(row["details"] or "{}")
        return result

    def _resume_close(self, goal):
        mode = goal.get("close_mode") or "liquidate"
        terminal = goal.get("close_result") or "CORTADA_POR_PERDIDA"
        pending = []
        lock = getattr(self.monitor, "ops_lock", self._lock)
        with lock:
            for link in self._owned_grids(goal["id"]):
                if link.get("repository_grid_id") is not None:
                    continue
                outcome = self.engine.close_grid(int(link["grid_id"]), mode,
                    close_reason="meta_resume_close", meta_id=int(goal["id"]) if mode == "repository" else None)
                if outcome.get("status") != "CLOSED":
                    pending.append({"grid_id": link["grid_id"], "status": outcome.get("status")})
        if pending:
            self._ledger(goal["id"], None, "META_CLOSE_PENDING", {"result": terminal, "grids": pending})
        else:
            if terminal in {"CORTADA_POR_PERDIDA", "VENCIDA"}:
                self._record_close_slippage(goal)
            self._finish(goal["id"], terminal)

    def start(self):
        self.scheduler.add_job(self.run_once, "interval", seconds=60, id="meta_watchdog",
                               replace_existing=True, max_instances=1, coalesce=True)
        if not self.scheduler.running:
            self.scheduler.start()

    def stop(self):
        if self.scheduler.running:
            self.scheduler.shutdown(wait=True)
