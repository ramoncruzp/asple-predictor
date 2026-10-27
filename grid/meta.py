"""Núcleo aislado de metas: valoración, propiedad, vigilancia y bitácora."""
from __future__ import annotations

import json
import uuid
import threading
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from apscheduler.schedulers.background import BackgroundScheduler

FEE = Decimal("0.001")
VALUATION_STATES = {"KNOWN": 0, "PARCIAL": 1, "DESCONOCIDO": 2}
DEFAULT_PROFILES = {
    "PRUDENTE": {"capital_per_coin_pct": 40, "max_grids": 2, "max_bots": 4,
                 "max_loss_pct": 8, "warning_loss_pct": 6, "max_days": 45, "loan_cap_pct": 30,
                 "min_effective_verifications": 30,
                 "sigma_q33": 0.333333, "sigma_q67": 0.666667, "min_margin_pct": 0.7,
                 "plan_price_drift_pct": 1.0, "plan_ttl_seconds": 600, "signal_logit_cap": 1.0,
                 "learning_min_effective_samples": 30, "learning_weight_max_abs": 1.0,
                 "learning_max_weight_delta": 0.05, "learning_regularization": 1.0,
                 "learning_alpha": 0.05,
                 "use_learned_weights": False, "use_extended_allocation": False,
                 "use_centered_signals": False, "use_touch_bots": False,
                 "allocation_tie_tolerance_pp": 5.0,
                 "signal_weights": {"volatility": 0.25, "break_prob": 0.25,
                 "direction": 0.25, "scanner": 0.25, "advisor": 0.25},
                 "valuation_haircut_pct": 1.0, "loss_confirm_minutes": 2,
                 "valuation_unknown_alert_after_min": 10, "valuation_unknown_repeat_min": 30,
                 "valuation_partial_alert_after_min": 30, "valuation_partial_missing_pct": 25,
                 "valuation_partial_repeat_h": 6,
                 "reconfig_min_probability_delta_pp": 5.0,
                 "reconfig_probability_tie_tolerance_pp": 2.0,
                 "reconfig_min_progress_delta_pct": 10.0,
                 "reconfig_persistence_reviews": 1,
                 "max_reconfigurations_per_24h": 4,
                 "rotation_min_probability_delta_pp": 5.0,
                 "rotation_probability_tie_tolerance_pp": 2.0,
                 "rotation_min_progress_delta_pct": 10.0,
                 "rotation_persistence_reviews": 2,
                 "rotation_cooldown_hours": 1.0,
                 "max_rotations_per_24h": 2},
    "RAPIDO": {"capital_per_coin_pct": 70, "max_grids": 4, "max_bots": 8,
               "max_loss_pct": 18, "warning_loss_pct": 13.5, "max_days": 20, "loan_cap_pct": 50,
               "min_effective_verifications": 30,
               "sigma_q33": 0.333333, "sigma_q67": 0.666667, "min_margin_pct": 0.3,
               "plan_price_drift_pct": 1.0, "plan_ttl_seconds": 600, "signal_logit_cap": 1.0,
               "learning_min_effective_samples": 30, "learning_weight_max_abs": 1.0,
               "learning_max_weight_delta": 0.05, "learning_regularization": 1.0,
               "learning_alpha": 0.05,
                 "use_learned_weights": False, "use_extended_allocation": False,
                 "use_centered_signals": False, "use_touch_bots": False,
                 "allocation_tie_tolerance_pp": 5.0,
               "signal_weights": {"volatility": 0.25, "break_prob": 0.25,
               "direction": 0.25, "scanner": 0.25, "advisor": 0.25},
               "valuation_haircut_pct": 1.0, "loss_confirm_minutes": 2,
               "valuation_unknown_alert_after_min": 10, "valuation_unknown_repeat_min": 30,
               "valuation_partial_alert_after_min": 30, "valuation_partial_missing_pct": 25,
               "valuation_partial_repeat_h": 6,
               "reconfig_min_probability_delta_pp": 5.0,
               "reconfig_probability_tie_tolerance_pp": 2.0,
               "reconfig_min_progress_delta_pct": 10.0,
               "reconfig_persistence_reviews": 1,
               "max_reconfigurations_per_24h": 4,
               "rotation_min_probability_delta_pp": 5.0,
               "rotation_probability_tie_tolerance_pp": 2.0,
               "rotation_min_progress_delta_pct": 10.0,
               "rotation_persistence_reviews": 2,
               "rotation_cooldown_hours": 1.0,
               "max_rotations_per_24h": 2},
}


def _d(value: Any) -> Decimal:
    return Decimal(str(value or 0))


def attributable_cash(capital_initial, delivered, returned, commissions) -> Decimal:
    """Efectivo lógico atribuible: capital − entregas + devoluciones − comisiones."""
    return _d(capital_initial) - _d(delivered) + _d(returned) - _d(commissions)


def executable_value(qty, bids, fee_pct, haircut_pct, read_at, now, max_age_s):
    """Valora una venta contra niveles de bids; una lectura dudosa nunca equivale a cero."""
    try:
        quantity = _d(qty)
        fee = _d(fee_pct) / Decimal(100)
        haircut = _d(haircut_pct) / Decimal(100)
        if quantity < 0 or fee < 0 or not 0 <= haircut < 1 or not bids:
            raise ValueError("cantidad, comisión, descuento o bids inválidos")
        read = read_at if read_at.tzinfo else read_at.replace(tzinfo=timezone.utc)
        current = now if now.tzinfo else now.replace(tzinfo=timezone.utc)
        age = (current - read).total_seconds()
        if age < -1 or age > float(max_age_s):
            raise ValueError("lectura de profundidad vieja")
        remaining, gross, absorbed, last_price = quantity, Decimal(0), Decimal(0), Decimal(0)
        for row in bids:
            price, available = _d(row[0]), _d(row[1])
            if price <= 0 or available <= 0:
                raise ValueError("nivel de bid inválido")
            take = min(remaining, available)
            gross += take * price
            absorbed += take
            remaining -= take
            last_price = price
            if remaining <= 0:
                break
        missing_value = (remaining * last_price * (1 - haircut) * (1 - fee)
                         if remaining > 0 and last_price > 0 else Decimal(0))
        if quantity > 0 and absorbed <= 0:
            raise ValueError("sin profundidad compradora válida")
        state = "KNOWN" if remaining == 0 else "PARCIAL"
        return {"status": state, "reason": None if state == "KNOWN" else "profundidad insuficiente",
                "received_net": str(gross * (1 - fee)), "absorbed_qty": str(absorbed),
                "missing_qty": str(remaining), "missing_value_est": str(missing_value),
                "last_absorbed_price": str(last_price), "age_s": age}
    except Exception as exc:
        return {"status": "DESCONOCIDO", "reason": str(exc), "received_net": None,
                "absorbed_qty": "0", "missing_qty": str(qty), "missing_value_est": None,
                "last_absorbed_price": None, "age_s": None}


def smart_grid_params_for_goal(goal: dict) -> dict:
    """Parámetros de crédito propios del perfil Meta para un grid Smart."""
    config = goal.get("config_snapshot") or {}
    loan_cap = float(config.get("loan_cap_pct", 0))
    # El objetivo y el plazo pertenecen a la Meta completa. No asignarlos a cada
    # grid evita que la política normal cierre una pieza por separado.
    params = {"loans_enabled": loan_cap > 0}
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
                "learning_limits": {"max_templates_per_run": int(profiles.get("GLOBAL", {}).get(
                    "learning_max_templates_per_run", 30)), "time_budget_minutes": float(
                    profiles.get("GLOBAL", {}).get("learning_time_budget_minutes", 20))},
                "maintenance": {"cache_retention_days": int(profiles.get("GLOBAL", {}).get(
                    "cache_retention_days", 7)), "ledger_plan_detail_days": int(profiles.get("GLOBAL", {}).get(
                    "ledger_plan_detail_days", 14))},
                "profiles": {k: v for k, v in profiles.items() if k in DEFAULT_PROFILES}}

    def set_maintenance_settings(self, *, cache_retention_days, ledger_plan_detail_days):
        from sqlalchemy import select
        cache_days, ledger_days = int(cache_retention_days), int(ledger_plan_detail_days)
        if not 1 <= cache_days <= 90 or not 1 <= ledger_days <= 365:
            raise ValueError("La retención de caché debe estar entre 1 y 90 días y la de planes entre 1 y 365.")
        with self.db.engine.begin() as conn:
            raw = conn.execute(select(self.db.meta_profile_settings.c.settings).where(
                self.db.meta_profile_settings.c.profile == "GLOBAL")).scalar_one_or_none()
            values = json.loads(raw or "{}")
            values.update({"cache_retention_days": cache_days, "ledger_plan_detail_days": ledger_days})
            found = conn.execute(select(self.db.meta_profile_settings.c.profile).where(
                self.db.meta_profile_settings.c.profile == "GLOBAL")).first()
            if found:
                conn.execute(self.db.meta_profile_settings.update().where(
                    self.db.meta_profile_settings.c.profile == "GLOBAL").values(
                        settings=json.dumps(values, sort_keys=True), updated_at=self._now()))
            else:
                conn.execute(self.db.meta_profile_settings.insert().values(profile="GLOBAL",
                    settings=json.dumps({"max_active": 1, **values}, sort_keys=True), updated_at=self._now()))
        return {"cache_retention_days": cache_days, "ledger_plan_detail_days": ledger_days}

    def set_max_active(self, value):
        from sqlalchemy import select
        value = int(value)
        if value < 1 or value > 10:
            raise ValueError("MAX_METAS_ACTIVAS debe estar entre 1 y 10.")
        with self.db.engine.begin() as conn:
            present = conn.execute(select(self.db.meta_profile_settings.c.profile).where(
                self.db.meta_profile_settings.c.profile == "GLOBAL")).first()
            raw = conn.execute(select(self.db.meta_profile_settings.c.settings).where(
                self.db.meta_profile_settings.c.profile == "GLOBAL")).scalar_one_or_none()
            values = json.loads(raw or "{}")
            values["max_active"] = value
            if present:
                conn.execute(self.db.meta_profile_settings.update().where(
                    self.db.meta_profile_settings.c.profile == "GLOBAL").values(
                        settings=json.dumps(values), updated_at=self._now()))
            else:
                conn.execute(self.db.meta_profile_settings.insert().values(
                    profile="GLOBAL", settings=json.dumps(values), updated_at=self._now()))
        return value

    def set_learning_limits(self, *, max_templates_per_run, time_budget_minutes):
        from sqlalchemy import select
        maximum, minutes = int(max_templates_per_run), float(time_budget_minutes)
        if not 1 <= maximum <= 500 or not 1 <= minutes <= 240:
            raise ValueError("Límites de aprendizaje fuera de rango (1–500 plantillas, 1–240 minutos).")
        with self.db.engine.begin() as conn:
            raw = conn.execute(select(self.db.meta_profile_settings.c.settings).where(
                self.db.meta_profile_settings.c.profile == "GLOBAL")).scalar_one_or_none()
            values = json.loads(raw or "{}")
            values.update({"learning_max_templates_per_run": maximum,
                "learning_time_budget_minutes": minutes})
            found = conn.execute(select(self.db.meta_profile_settings.c.profile).where(
                self.db.meta_profile_settings.c.profile == "GLOBAL")).first()
            if found:
                conn.execute(self.db.meta_profile_settings.update().where(
                    self.db.meta_profile_settings.c.profile == "GLOBAL").values(
                        settings=json.dumps(values, sort_keys=True), updated_at=self._now()))
            else:
                conn.execute(self.db.meta_profile_settings.insert().values(profile="GLOBAL",
                    settings=json.dumps({"max_active": 1, **values}, sort_keys=True), updated_at=self._now()))
        return {"max_templates_per_run": maximum, "time_budget_minutes": minutes}

    def set_config(self, profile: str, values: dict):
        from sqlalchemy import select
        profile = profile.upper()
        if profile not in DEFAULT_PROFILES:
            raise ValueError("Perfil inválido.")
        allowed = set(DEFAULT_PROFILES[profile])
        if set(values) - allowed:
            raise ValueError("Parámetros no permitidos.")
        merged = {**DEFAULT_PROFILES[profile], **values}
        if "reconfig_persistence_reviews" in values:
            persistence = values["reconfig_persistence_reviews"]
            if isinstance(persistence, bool) or int(persistence) != float(persistence) or int(persistence) < 1:
                raise ValueError("reconfig_persistence_reviews debe ser un entero igual o mayor que 1.")
        if "signal_weights" in values:
            if not isinstance(values["signal_weights"], dict):
                raise ValueError("signal_weights debe ser un objeto de pesos.")
            merged["signal_weights"] = {**DEFAULT_PROFILES[profile]["signal_weights"],
                                         **values["signal_weights"]}
        scalar_values = [v for k, v in merged.items() if k != "signal_weights" and not isinstance(v, bool)]
        scalar_values.extend((merged.get("signal_weights") or {}).values())
        if any(Decimal(str(v)) < 0 for v in scalar_values):
            raise ValueError("Los parámetros deben ser positivos.")
        if float(merged.get("allocation_tie_tolerance_pp", 5)) > 100:
            raise ValueError("allocation_tie_tolerance_pp no puede superar 100.")
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

    def discard_goal(self, meta_id):
        """Descarta un borrador (DRAFT): no toca el exchange, no cuenta como meta activa."""
        goal = self.get_goal(meta_id)
        if goal["status"] != "DRAFT":
            raise ValueError("Solo se puede descartar una meta en DRAFT.")
        now = self._now()
        with self.db.engine.begin() as conn:
            conn.execute(self.db.meta_goals.update().where(self.db.meta_goals.c.id == int(meta_id)).values(
                status="DESCARTADA", finished_at=now))
        self._ledger(meta_id, None, "META_DISCARDED", {})
        return self.get_goal(meta_id)

    def list_active_piece_links(self):
        """Mapa grid_id -> meta_id de piezas ligadas vivas, para que la
        pantalla Grids pueda mostrar la etiqueta "Meta #N" sin duplicar la
        lógica de propiedad de MetaCore."""
        from sqlalchemy import select
        with self.db.engine.connect() as conn:
            rows = conn.execute(select(self.db.meta_piece_links.c.grid_id, self.db.meta_piece_links.c.meta_id,
                self.db.meta_piece_links.c.piece_type).where(
                    self.db.meta_piece_links.c.released_at.is_(None))).all()
        return [{"grid_id": int(row[0]), "meta_id": int(row[1]), "piece_type": row[2]} for row in rows]

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
        # MetaCore.run_once y el operador comparten este cliente y esta caché.
        # Serializar la lectura evita carreras de libro y de _depth_cache.
        with self._lock:
            return self._valuation_locked(meta_id)

    def _valuation_locked(self, meta_id):
        from sqlalchemy import select
        goal = self.get_goal(meta_id)
        links = self._owned_grids(meta_id)
        delivered = returned = commissions = Decimal(0)
        try:
            pieces = []
            quantities_by_symbol = defaultdict(Decimal)
            books_by_symbol = {}
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
                if repo_id is None:
                    inventory_levels = self.db.get_grid_levels(int(grid["id"]))
                else:
                    mapped_ids = {int(idx) for idx in level_map.values()}
                    inventory_levels = [level for level in self.db.get_grid_levels(int(repo_id))
                                        if int(level["level_idx"]) in mapped_ids]
                held = sum((_d(level.get("held_qty")) for level in inventory_levels), Decimal(0))
                pieces.append({"symbol": grid["symbol"], "grid_id": int(link["grid_id"]),
                               "grid_status": grid.get("status"), "qty": held, "levels": inventory_levels})
                quantities_by_symbol[grid["symbol"]] += held
            cash = attributable_cash(goal["capital_initial"], delivered, returned, commissions)
            cfg_raw = goal.get("config_snapshot") or {}
            cfg = json.loads(cfg_raw) if isinstance(cfg_raw, str) else dict(cfg_raw)
            profile_cfg = self._profile_settings().get(goal["profile"], {})
            haircut = cfg.get("valuation_haircut_pct", profile_cfg.get("valuation_haircut_pct", 1.0))
            coin_values, state_rows, symbol_diagnostics = {}, [], {}
            for symbol, quantity in quantities_by_symbol.items():
                if quantity <= 0:
                    empty_value = {"status": "KNOWN", "reason": None, "received_net": "0",
                        "absorbed_qty": "0", "missing_qty": "0", "missing_value_est": "0",
                        "best_bid_gross": Decimal(0)}
                    coin_values[symbol] = empty_value
                    state_rows.append((symbol, empty_value))
                    continue
                now = self.clock()
                cached = getattr(self, "_depth_cache", {}).get(symbol)
                if cached and (now - cached[0]).total_seconds() < 60:
                    book = cached[1]
                else:
                    getter = getattr(self.exchange, "get_order_book_depth", None)
                    if callable(getter):
                        book = getter(symbol, limit=5)
                    else:
                        ticker = self.exchange.get_book_ticker(symbol)
                        book = {"bids": [(ticker.get("bid_price"), ticker.get("bid_qty"))],
                                "asks": [(ticker.get("ask_price"), ticker.get("ask_qty"))],
                                "read_at": now}
                    if not hasattr(self, "_depth_cache"):
                        self._depth_cache = {}
                    self._depth_cache[symbol] = (now, book)
                bid = _d(book.get("bids", [(0, 0)])[0][0]) if book.get("bids") else Decimal(0)
                best_gross = quantity * bid if bid > 0 else None
                executable = executable_value(quantity, book.get("bids"), 0.1, haircut,
                    book.get("read_at"), now, 60)
                coin_values[symbol] = {**executable, "best_bid_gross": best_gross}
                state_rows.append((symbol, executable))
                read_at = book.get("read_at")
                if read_at is not None and read_at.tzinfo is None:
                    read_at = read_at.replace(tzinfo=timezone.utc)
                age_s = (now - read_at).total_seconds() if read_at is not None else None
                reason = executable.get("reason")
                if executable["status"] == "DESCONOCIDO" and reason:
                    if not book.get("bids"):
                        reason = f"libro sin compradores; {reason}"
                    elif age_s is not None and age_s > 60:
                        reason = f"lectura de profundidad vieja ({age_s:.1f} s); {reason}"
                symbol_diagnostics[symbol] = {"status": executable["status"], "reason": reason,
                    "book_read_at": read_at.isoformat() if read_at is not None else None,
                    "age_s": age_s, "bid_levels": len(book.get("bids") or []),
                    "ask_levels": len(book.get("asks") or []),
                    "best_bid": str(book.get("bids")[0][0]) if book.get("bids") else None,
                    "best_ask": str(book.get("asks")[0][0]) if book.get("asks") else None}
            status = "KNOWN"
            for _symbol, result in state_rows:
                if VALUATION_STATES[result["status"]] > VALUATION_STATES[status]:
                    status = result["status"]
            inventory_best = sum((value["best_bid_gross"] or Decimal(0) for value in coin_values.values()), Decimal(0))
            inventory_exec = sum((_d(value["received_net"]) + _d(value["missing_value_est"])
                                  for value in coin_values.values() if value["status"] != "DESCONOCIDO"), Decimal(0))
            for piece in pieces:
                coin = coin_values.get(piece["symbol"], {})
                total_qty = quantities_by_symbol[piece["symbol"]]
                ratio = piece["qty"] / total_qty if total_qty else Decimal(0)
                piece["absorbed_qty"] = str(_d(coin.get("absorbed_qty")) * ratio)
                piece["missing_qty"] = str(_d(coin.get("missing_qty")) * ratio)
                piece["value_executable"] = (str((_d(coin.get("received_net")) + _d(coin.get("missing_value_est"))) * ratio)
                                              if coin.get("status") != "DESCONOCIDO" else None)
                piece["value_best_bid"] = str(_d(coin.get("best_bid_gross")) * ratio) if coin.get("best_bid_gross") is not None else None
                piece["status"] = coin.get("status", "KNOWN")
                piece["valuation_reason"] = symbol_diagnostics.get(piece["symbol"], {}).get("reason") or coin.get("reason")
                piece["book_read_at"] = coin.get("book_read_at")
            self._update_valuation_state(meta_id, state_rows, profile_cfg)
            value_best = cash + inventory_best * (Decimal(1) - FEE)
            value_exec = cash + inventory_exec
            result_best = (value_best - _d(goal["capital_initial"])) / _d(goal["capital_initial"]) * 100
            result_exec = (value_exec - _d(goal["capital_initial"])) / _d(goal["capital_initial"]) * 100
            allocated = sum((_d(self.db.get_grid(int(link["grid_id"])).get("capital_total"))
                             for link in links if self.db.get_grid(int(link["grid_id"]))
                             and self.db.get_grid(int(link["grid_id"])).get("status")
                             in {"OPENING", "ACTIVE", "PAUSED", "CLOSING", "HOLDING"}), Decimal(0))
            free_capital = max(Decimal(0), _d(goal["capital_initial"]) - allocated)
            with self.db.engine.connect() as conn:
                run = conn.execute(select(self.db.meta_runs).where(
                    self.db.meta_runs.c.meta_id == int(meta_id),
                    self.db.meta_runs.c.status == "ACTIVE").order_by(
                        self.db.meta_runs.c.id.desc())).mappings().first()
            reasons = [f"{symbol}: {item['reason']}" for symbol, item in symbol_diagnostics.items()
                if item["status"] == "DESCONOCIDO" and item.get("reason")]
            return {"status": status, "reason": "; ".join(reasons) if reasons else None,
                    "symbol_valuations": symbol_diagnostics,
                    "cash_usdt": str(cash), "free_capital_usdt": str(free_capital),
                    "realized_extra_usdt": str(_d(run.get("realized_extra_usdt")) if run else Decimal(0)),
                    "inventory_bid_gross_usdt": str(inventory_best),
                    "inventory_executable_usdt": str(inventory_exec), "value_usdt": str(value_exec),
                    "value_best_bid": str(value_best), "value_executable": str(value_exec),
                    "result_pct": str(result_exec) if status != "DESCONOCIDO" else None,
                    "result_pct_best_bid": str(result_best),
                    "result_pct_executable": str(result_exec) if status != "DESCONOCIDO" else None,
                    "estimated_sell_fee_pct": "0.1", "pieces": pieces}
        except Exception as exc:
            symbol = next((self.db.get_grid(int(link["grid_id"])).get("symbol") for link in links
                           if self.db.get_grid(int(link["grid_id"]))), "UNKNOWN")
            self._update_valuation_state(meta_id, [(symbol, {"status": "DESCONOCIDO", "reason": str(exc),
                "missing_qty": "0", "missing_value_est": None})], self._profile_settings().get(goal["profile"], {}))
            return {"status": "DESCONOCIDO", "symbol": symbol, "reason": str(exc), "value_best_bid": None,
                    "value_executable": None, "result_pct": None, "pieces": []}

    def _update_valuation_state(self, meta_id, states, cfg):
        """Persiste transiciones y cadencias de alertas, incluso tras reiniciar el proceso."""
        from sqlalchemy import select
        now = self._now()
        for symbol, result in states:
            state = result["status"]
            ledger_events = []
            alert_event = None
            with self.db.engine.begin() as conn:
                old = conn.execute(select(self.db.meta_valuation_states).where(
                    self.db.meta_valuation_states.c.meta_id == int(meta_id),
                    self.db.meta_valuation_states.c.symbol == symbol)).mappings().first()
                if old is None or old["state"] != state:
                    conn.execute(self.db.meta_valuation_states.delete().where(
                        self.db.meta_valuation_states.c.meta_id == int(meta_id),
                        self.db.meta_valuation_states.c.symbol == symbol))
                    conn.execute(self.db.meta_valuation_states.insert().values(meta_id=int(meta_id), symbol=symbol,
                        state=state, state_since=now, last_alert_at=None, alert_sent=False,
                        last_reason=result.get("reason")))
                    if old is not None and old["alert_sent"] and state == "KNOWN":
                        ledger_events.append(("META_VALUATION_RECOVERED", {"symbol": symbol}))
                    ledger_events.append(("META_VALUATION_STATE", {"symbol": symbol, "status": state,
                        "reason": result.get("reason")}))
                    old = None
                row = conn.execute(select(self.db.meta_valuation_states).where(
                    self.db.meta_valuation_states.c.meta_id == int(meta_id),
                    self.db.meta_valuation_states.c.symbol == symbol)).mappings().first()
                duration = max(0, (now - row["state_since"]).total_seconds())
                alert_kind, due, interval = None, False, 0
                if state == "DESCONOCIDO":
                    alert_kind = "META_UNPROTECTED"
                    due = duration > float(cfg.get("valuation_unknown_alert_after_min", 10)) * 60
                    interval = float(cfg.get("valuation_unknown_repeat_min", 30)) * 60
                elif state == "PARCIAL":
                    best = _d(result.get("best_bid_gross"))
                    missing_pct = (_d(result.get("missing_value_est")) / best * 100) if best > 0 else Decimal(0)
                    due = duration > float(cfg.get("valuation_partial_alert_after_min", 30)) * 60 and missing_pct > _d(cfg.get("valuation_partial_missing_pct", 25))
                    alert_kind = "META_VALUATION_PARTIAL"
                    interval = float(cfg.get("valuation_partial_repeat_h", 6)) * 3600
                if due and (row["last_alert_at"] is None or (now - row["last_alert_at"]).total_seconds() >= interval):
                    details = {"symbol": symbol, "duration_min": round(duration / 60, 1),
                               "reason": result.get("reason"), "missing_qty": result.get("missing_qty")}
                    alert_event = (alert_kind, details)
                    conn.execute(self.db.meta_valuation_states.update().where(
                        self.db.meta_valuation_states.c.meta_id == int(meta_id),
                        self.db.meta_valuation_states.c.symbol == symbol).values(last_alert_at=now, alert_sent=True))
            for kind, details in ledger_events:
                self._ledger(meta_id, None, kind, details)
                if kind == "META_VALUATION_RECOVERED":
                    self.alert(kind, int(meta_id), details)
            if alert_event:
                kind, details = alert_event
                self._ledger(meta_id, None, kind, details)
                self.alert(kind, int(meta_id), details)

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
                if valuation["status"] == "DESCONOCIDO":
                    with self.db.engine.begin() as conn:
                        conn.execute(self.db.meta_runs.update().where(
                            self.db.meta_runs.c.meta_id == goal["id"],
                            self.db.meta_runs.c.status == "ACTIVE").values(loss_limit_breach_count=0))
                    if goal["status"] == "ACTIVE":
                        with self.db.engine.begin() as conn:
                            conn.execute(self.db.meta_goals.update().where(self.db.meta_goals.c.id == goal["id"]).values(status="PIECES_BLOCKED"))
                        self._ledger(goal["id"], None, "META_PIECES_BLOCKED", {"reason": "valuation_unknown"})
                    results.append({"meta_id": goal["id"], "valuation": valuation, "new_pieces_allowed": False})
                    continue
                if goal["status"] == "PIECES_BLOCKED":
                    with self.db.engine.begin() as conn:
                        conn.execute(self.db.meta_goals.update().where(self.db.meta_goals.c.id == goal["id"]).values(status="ACTIVE"))
                cfg = goal["config_snapshot"]
                result_pct = _d(valuation["result_pct"])
                with self.db.engine.begin() as conn:
                    active_run = conn.execute(select(self.db.meta_runs).where(
                        self.db.meta_runs.c.meta_id == goal["id"],
                        self.db.meta_runs.c.status == "ACTIVE").order_by(self.db.meta_runs.c.id.desc())).mappings().first()
                    if active_run:
                        breached = result_pct <= -_d(cfg.get("max_loss_pct", 8))
                        breach_count = int(active_run.get("loss_limit_breach_count") or 0) + 1 if breached else 0
                        conn.execute(self.db.meta_runs.update().where(self.db.meta_runs.c.id == active_run["id"]).values(
                            worst_result_pct=min(float(active_run.get("worst_result_pct") or 0), float(result_pct)),
                            loss_limit_breach_count=breach_count))
                    else:
                        breach_count = 0
                if goal["status"] == "SURPLUS_WAIT":
                    self._ledger(goal["id"], None, "META_SURPLUS_VALUATION", {
                        "value_usdt": valuation["value_usdt"], "result_pct": valuation["result_pct"],
                        "value_best_bid": valuation["value_best_bid"],
                        "value_executable": valuation["value_executable"],
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
                    "status": valuation["status"], "value_usdt": valuation["value_usdt"],
                    "value_best_bid": valuation["value_best_bid"],
                    "value_executable": valuation["value_executable"],
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
                elif goal["status"] in {"ACTIVE", "PIECES_BLOCKED"} and result_pct <= limit and breach_count >= int(cfg.get("loss_confirm_minutes", 2)):
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
        if final_value.get("status") not in {"KNOWN", "PARCIAL"}:
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
        by_grid = {int(item["grid_id"]): item for item in valuation.get("pieces", [])}
        for link in links:
            grid = self.db.get_grid(int(link["grid_id"]))
            if grid is None:
                continue
            valuation_piece = by_grid.get(int(link["grid_id"]), {})
            levels = self.db.get_grid_levels(int(link.get("repository_grid_id") or link["grid_id"]))
            estimate = Decimal(0)
            for level in levels:
                entry = _d(level.get("entry_price"))
                estimate += _d(level.get("pnl")) - _d(level.get("held_qty")) * entry
            if valuation_piece.get("value_executable") is not None:
                estimate += _d(valuation_piece["value_executable"])
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

    LEDGER_HIDDEN_BY_DEFAULT = ("META_VALUATION", "META_SURPLUS_VALUATION")

    def ledger_page(self, meta_id, *, page=1, page_size=50, include_valuation=False):
        """Bitácora paginada; por defecto oculta META_VALUATION (una fila por
        minuto) para que lo legible (decisiones, aperturas/retiros/cierres,
        alertas) no quede enterrado detrás de ellas."""
        from sqlalchemy import func, select
        page = max(1, int(page))
        page_size = max(1, min(200, int(page_size)))
        statement = select(self.db.meta_ledger).where(self.db.meta_ledger.c.meta_id == int(meta_id))
        count_statement = select(func.count()).select_from(self.db.meta_ledger).where(
            self.db.meta_ledger.c.meta_id == int(meta_id))
        if not include_valuation:
            statement = statement.where(self.db.meta_ledger.c.event_type.notin_(self.LEDGER_HIDDEN_BY_DEFAULT))
            count_statement = count_statement.where(
                self.db.meta_ledger.c.event_type.notin_(self.LEDGER_HIDDEN_BY_DEFAULT))
        statement = statement.order_by(self.db.meta_ledger.c.id.desc()).offset(
            (page - 1) * page_size).limit(page_size)
        with self.db.engine.connect() as conn:
            rows = conn.execute(statement).mappings().all()
            total = conn.execute(count_statement).scalar_one()
        result = [dict(row) for row in rows]
        for row in result:
            row["details"] = json.loads(row["details"] or "{}")
        return {"events": result, "page": page, "page_size": page_size, "total": int(total)}

    def list_runs(self, meta_id):
        from sqlalchemy import select
        with self.db.engine.connect() as conn:
            rows = conn.execute(select(self.db.meta_runs).where(
                self.db.meta_runs.c.meta_id == int(meta_id)).order_by(
                    self.db.meta_runs.c.id.desc())).mappings().all()
        result = []
        for row in rows:
            item = dict(row)
            item["control_plan"] = json.loads(item.pop("control_plan_json") or "null")
            item["control_result"] = json.loads(item.pop("control_result_json") or "null")
            result.append(item)
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
        self.scheduler.add_job(self.run_once, "cron", minute="*", second=0, timezone="UTC", id="meta_watchdog",
                               replace_existing=True, max_instances=1, coalesce=True)
        if not self.scheduler.running:
            self.scheduler.start()

    def stop(self):
        if self.scheduler.running:
            self.scheduler.shutdown(wait=True)
