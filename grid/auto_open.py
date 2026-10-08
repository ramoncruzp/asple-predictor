"""Opt-in scheduled Testnet auto-open, guarded by persisted execution-slot events."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from apscheduler.schedulers.background import BackgroundScheduler

from config.settings import Settings
from models.coin_onboarding import coin_is_ready
from grid.loan_cohorts import create_grid_with_loan_cohort
from grid.structure import functional_cell_threshold
from grid.sim.runner import FILTERS as DEFAULT_FILTERS


class GridAutoOpen:
    def __init__(self, scan_service, db, engine, testnet_client, settings, *, settings_factory=Settings):
        self.scan_service, self.db, self.engine = scan_service, db, engine
        self.testnet_client, self.settings, self.settings_factory = testnet_client, settings, settings_factory
        self.scheduler = BackgroundScheduler(timezone="UTC")
        self._coin_not_ready_notified: dict[str, str] = {}
        hours = max(1, int(getattr(settings, "scanner_auto_open_interval_hours", 6)))
        self.scheduler.add_job(self.run_once, "interval", hours=hours, id="grid_auto_open",
                               max_instances=1, coalesce=True, replace_existing=True)

    def start(self):
        if not self.scheduler.running:
            self.scheduler.start()

    def stop(self):
        if self.scheduler.running:
            self.scheduler.shutdown(wait=True)

    @staticmethod
    def _testnet(client) -> bool:
        return getattr(getattr(client, "client", None), "testnet", False) is True

    def run_once(self):
        cfg = self.settings_factory()
        if not bool(cfg.scanner_auto_open):
            return {"opened": [], "disabled": True}
        if self.engine is None or self.testnet_client is None or not self._testnet(self.testnet_client):
            return {"opened": [], "error": "auto-open requiere cliente Testnet verificado"}
        interval = max(1, int(cfg.scanner_auto_open_interval_hours))
        now = datetime.now(timezone.utc)
        slot = int(now.timestamp()) // (interval * 3600)
        events = self.db.list_grid_events(limit=10000)
        slot_events = [event for event in events if event.get("event_type") in {"AUTO_OPEN_STARTED", "AUTO_OPEN"}
                       and (event.get("details") or {}).get("slot") == slot]
        if slot_events:
            return {"opened": [], "duplicate_slot": slot}
        today_count = sum(event.get("event_type") == "AUTO_OPEN"
            and (event.get("details") or {}).get("phase") == "COMPLETED"
            and str(event.get("ts", ""))[:10] == now.date().isoformat() for event in events)
        cap = max(0, int(cfg.scanner_auto_open_daily_cap))
        if today_count >= cap:
            return {"opened": [], "daily_cap": cap}
        scan = self.scan_service.scan(capital=cfg.usdt_por_grid)
        eligible = [row for row in scan["results"] if row.get("eligible")
                    and (row.get("score") or 0) >= float(cfg.scanner_auto_open_min_score)]
        per_run = min(max(0, int(cfg.scanner_auto_open_max_per_run)),
                      max(0, int(cfg.max_grids_simultaneos) - self.db.count_open_grids()),
                      cap - today_count)
        opened = []
        for row in eligible:
            if len(opened) >= per_run:
                break
            symbol = row["symbol"]
            if self.db.has_open_grid(symbol):
                continue
            if not coin_is_ready(self.db, None, symbol):
                readiness_getter = getattr(self.db, "get_readiness", lambda _symbol: None)
                readiness = readiness_getter(symbol) or {}
                readiness_state = readiness.get("state", "pendiente")
                if self._coin_not_ready_notified.get(symbol) != readiness_state:
                    self.db.add_grid_event(
                        run_id=None,
                        source="CLI",
                        event_type="AUTO_OPEN",
                        details={
                            "slot": slot,
                            "phase": "FAILED",
                            "who": "auto",
                            "symbol": symbol,
                            "reason": f"coin_not_ready:{readiness_state}",
                            "scan": row,
                        },
                    )
                    self._coin_not_ready_notified[symbol] = readiness_state
                continue
            self._coin_not_ready_notified.pop(symbol, None)
            strategy = str(cfg.scanner_auto_open_strategy).lower()
            params = {}
            if cfg.scanner_auto_open_target_pct is not None:
                params["target_pct"] = cfg.scanner_auto_open_target_pct
            if cfg.scanner_auto_open_max_days is not None:
                params["max_days"] = cfg.scanner_auto_open_max_days
            self.db.add_grid_event(run_id=None, source="CLI", event_type="AUTO_OPEN_STARTED",
                details={"slot": slot, "symbol": symbol, "phase": "STARTED",
                         "who": "auto", "scan": row, "params": params, "strategy": strategy})
            try:
                structure = row["suggested_structure"]
                if not structure.get("feasible"):
                    self.db.add_grid_event(run_id=None, source="CLI", event_type="AUTO_OPEN",
                        details={"slot": slot, "phase": "FAILED", "who": "auto", "symbol": symbol,
                                 "reason": "structure_infeasible", "scan": row, "params": params})
                    continue
                if strategy == "smart":
                    info_getter = getattr(self.testnet_client, "get_symbol_info", None)
                    if info_getter is None:
                        info_getter = getattr(getattr(self.engine, "exchange", None), "get_symbol_info", None)
                    filters = (type(DEFAULT_FILTERS).from_symbol_info(info_getter(symbol))
                               if info_getter is not None else DEFAULT_FILTERS)
                    minimum_cell = functional_cell_threshold(filters, strategy)
                    capital = Decimal(str(cfg.usdt_por_grid))
                    feasible_levels = int(capital // minimum_cell)
                    if feasible_levels < 4:
                        self.db.add_grid_event(run_id=None, source="CLI", event_type="AUTO_OPEN",
                            details={"slot": slot, "phase": "FAILED", "who": "auto", "symbol": symbol,
                                "reason": "capital_below_smart_functional_cell_floor", "minimum_cell_usdt": str(minimum_cell)})
                        continue
                    if int(structure["n_levels"]) > feasible_levels:
                        structure = dict(structure)
                        old_levels = int(structure["n_levels"])
                        structure["n_levels"] = feasible_levels
                        structure["cell_usdt"] = capital / feasible_levels
                        structure["spacing_pct"] = float(structure["spacing_pct"]) * old_levels / feasible_levels
                minimum_margin = float(getattr(cfg, "grid_min_margin_after_fees_pct",
                                                getattr(cfg, "grid_min_net_margin_pct", .7)))
                fee = float(row.get("fee_pct", getattr(cfg, "scanner_fee_pct", .1)))
                spacing = structure.get("spacing_pct")
                gross_margin = None if spacing is None else float(spacing) - 2 * fee
                net_margin = structure.get("net_edge_pct_per_cycle")
                dust_pct = structure.get("dust_estimate_pct")
                dust_warning = bool(gross_margin is not None and dust_pct is not None
                                    and float(dust_pct) > gross_margin * .5)
                warning_text = ("El polvo estimado es alto para esta celda; sube el capital por celda o reduce niveles. "
                    "Es un tope pesimista, aún no medido en Testnet." if dust_warning else None)
                reasons = []
                if gross_margin is None or gross_margin < minimum_margin:
                    reasons.append("margin_after_fees_below_minimum")
                if reasons:
                    self.db.add_grid_event(run_id=None, source="CLI", event_type="AUTO_OPEN",
                        details={"slot": slot, "phase": "FAILED", "who": "auto", "symbol": symbol,
                                  "reason": reasons, "minimum_pct": minimum_margin,
                                  "actual_pct": gross_margin, "net_after_dust_pct": net_margin,
                                  "dust_estimate_pct": dust_pct, "dust_warning": dust_warning,
                                  "dust_warning_message": warning_text, "scan": row, "params": params})
                    continue
                effective_params, result = create_grid_with_loan_cohort(
                    self.db, strategy, params,
                    int(getattr(cfg, "loans_control_every_n", 3)),
                    lambda assigned: self.engine.create_grid(
                        symbol, structure["range_low"], structure["range_high"],
                        int(structure["n_levels"]), capital=Decimal(str(cfg.usdt_por_grid)),
                        strategy=strategy, params=assigned or None),
                    explicit_params=params,
                )
                grid_id = int(result["id"])
                self.db.add_grid_event(run_id=None, source="CLI", event_type="AUTO_OPEN", grid_id=grid_id,
                    details={"slot": slot, "phase": "COMPLETED", "who": "auto", "symbol": symbol,
                        "scan": row, "params": effective_params, "strategy": strategy,
                        "margin_after_fees_pct": gross_margin, "net_after_dust_pct": net_margin,
                        "dust_estimate_pct": dust_pct, "dust_warning": dust_warning,
                        "dust_warning_message": warning_text})
                opened.append({"symbol": symbol, "grid_id": grid_id})
            except Exception as exc:
                self.db.add_grid_event(run_id=None, source="CLI", event_type="AUTO_OPEN",
                    details={"slot": slot, "phase": "FAILED", "who": "auto", "symbol": symbol,
                             "reason": str(exc), "scan": row, "params": params})
        return {"opened": opened, "slot": slot}
