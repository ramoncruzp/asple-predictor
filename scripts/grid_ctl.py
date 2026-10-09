"""Manual status, close, and one-shot controls for the grid monitor."""

from __future__ import annotations

import argparse
import json
import sys
import warnings
from decimal import Decimal, InvalidOperation
from types import SimpleNamespace
from typing import Any

from config.settings import Settings
from data.testnet_client import TestnetClient
from data.binance_client import BinanceClient
from database.db_manager import DBManager
from grid.engine import GridEngine
from grid.monitor import GridMonitor
from grid.levels import compute_lines, plan_cells
from grid.policy import DEFAULT_SMART_PARAMS, plan_dust_sweep, validate_params
from grid.loan_cohorts import assign_loan_creation_defaults, create_grid_with_loan_cohort
from grid.structure import functional_cell_threshold, functional_cell_warning
from data.exchange_filters import SymbolFilters
from grid.scan_service import GridScanService
from grid.volatility_provider import VolatilityProvider


def build_context() -> dict[str, Any]:
    """Build runtime dependencies; replaceable by tests and operator tooling."""
    settings = Settings()
    db = DBManager(settings.database_url)
    key = (settings.testnet_api_key or "").strip()
    secret = (settings.testnet_api_secret or "").strip()
    if not key or not secret or key.casefold().startswith("tu_") or secret.casefold().startswith("tu_"):
        raise RuntimeError("faltan credenciales válidas de Binance Testnet")
    exchange = TestnetClient(
        key, secret, production_api_key=settings.binance_api_key,
    )
    engine = GridEngine(db, exchange, settings)
    monitor = GridMonitor(db, exchange, engine, settings)
    public_client = BinanceClient("", "")
    app = SimpleNamespace(state=SimpleNamespace(
        settings=settings, db=db, grid_engine=engine, testnet_client=exchange,
        grid_scan_service=GridScanService(db, public_client, settings),
        vol_provider=VolatilityProvider(db, data_client=public_client), vol_registry=None,
    ))
    return {"settings": settings, "db": db, "exchange": exchange,
            "engine": engine, "monitor": monitor, "app": app}


def _cli_loan_creation_params(db, current: dict, explicit: dict, control_every_n: int) -> dict:
    return assign_loan_creation_defaults(db, "smart", current, control_every_n,
                                         explicit_params=explicit)


def _status(context: dict[str, Any]) -> dict[str, Any]:
    db = context["db"]
    grids = []
    for status in ("OPENING", "ACTIVE", "PAUSED", "CLOSING", "HOLDING", "CLOSED", "CANCELLED", "FAILED"):
        grids.extend(db.list_grids_by_status({status}))
    grids.sort(key=lambda row: int(row["id"]))
    for grid in grids:
        grid["levels"] = db.get_grid_levels(int(grid["id"]))
        grid["compound_total"] = sum(float(row.get("capital_compound") or 0.0) for row in grid["levels"])
        grid["capital_effective"] = sum(float(row.get("capital") or 0.0) for row in grid["levels"])
        grid["reserve"] = float(grid.get("reserve") or 0.0)
        grid["loans"] = db.list_grid_loans(int(grid["id"])) if hasattr(db, "list_grid_loans") else []
        grid["open_loans"] = [row for row in grid["loans"] if row.get("status") in {"OPEN", "PENDING"}]
        snapshots = db.list_grid_snapshots(grid_id=int(grid["id"]), limit=100) if hasattr(db, "list_grid_snapshots") else []
        latest_adjust = db.get_last_event(int(grid["id"]), "GRID_ADJUSTED") \
            if hasattr(db, "get_last_event") else None
        grid["last_adjust_at"] = None if latest_adjust is None else latest_adjust.get("ts")
        grid["adjust_enabled"] = bool((grid.get("params") or {}).get("adjust_enabled", False))
        grid["max_days"] = (grid.get("params") or {}).get("max_days")
        params = grid.get("params") or {}
        if grid.get("strategy") == "smart":
            summaries = [row for row in snapshots if row.get("level_idx") is None]
            latest = summaries[0] if summaries else {}
            mid = latest.get("market_mid")
            cells = grid["levels"]
            realized = sum(float(row.get("pnl") or 0) for row in cells)
            buy_events = (db.list_grid_events(grid_id=int(grid["id"]), event_type="BUY_FILLED", limit=5000)
                          if hasattr(db, "list_grid_events") else [])
            held_basis = 0.0
            for cell in cells:
                qty = float(cell.get("held_qty") or 0)
                if qty <= 0:
                    continue
                current_buy_cid = cell.get("buy_client_order_id")
                fill = next((event for event in buy_events
                             if int(event.get("level_idx", -1)) == int(cell["level_idx"])
                             and (not current_buy_cid or event.get("client_order_id") == current_buy_cid)), None)
                details = (fill or {}).get("details") or {}
                gross_qty = float(details.get("executed_qty") or 0)
                if gross_qty > 0:
                    held_basis += float(cell.get("entry_price") or 0) * gross_qty + float(details.get("fee_usdt") or 0)
                else:
                    held_basis += float(cell.get("entry_price") or 0) * qty * 1.001
            cash_now = (float(grid.get("capital_total") or 0) + realized - held_basis
                        + float(params.get("dust_cash_proceeds", 0)))
            equity_now = (cash_now + sum(float(row.get("held_qty") or 0) * float(mid or 0) * .999
                                         for row in cells)) if mid is not None else None
            target_goals = []
            if params.get("target_pct") is not None:
                target_goals.append(float(grid.get("capital_total") or 0) * float(params["target_pct"]) / 100)
            if params.get("target_usdt") is not None:
                target_goals.append(float(params["target_usdt"]))
            target_usdt = min(target_goals) if target_goals else None
            close_plan = params.get("target_close_plan") or {}
            if close_plan.get("phase") == "COMPLETE":
                cash_now = close_plan.get("cash_total", cash_now)
                equity_now = close_plan.get("equity_total_at_close", equity_now)
            basis_value = cash_now if params.get("target_basis", "cash") == "cash" else equity_now
            grid["target"] = {
                "pct": params.get("target_pct"), "usdt": params.get("target_usdt"),
                "basis": params.get("target_basis", "cash"), "cash_now": cash_now,
                "equity_now": equity_now,
                "cash_total": close_plan.get("cash_total", cash_now),
                "equity_total_at_close": close_plan.get("equity_total_at_close", equity_now),
                "progress_pct": (None if target_usdt is None or basis_value is None else
                                 max(0.0, (basis_value - float(grid.get("capital_total") or 0))
                                     / target_usdt * 100)),
            }
        grid["calibrated"] = False
        calibration_id = grid.get("calibration_id")
        if calibration_id is not None and hasattr(db, "get_grid_calibration"):
            calibration = db.get_grid_calibration(int(calibration_id))
            if calibration:
                grid["calibrated"] = calibration.get("verdict") == "validated_walk_forward"
                if grid["calibrated"]:
                    grid["calibration"] = {"id": int(calibration_id), "verdict": calibration.get("verdict"),
                                            "data_end": calibration.get("data_end")}
        summaries = [row for row in snapshots
                     if row.get("level_idx") is None]
        if summaries:
            latest = summaries[0]
            for name in ("break_prob", "sigma_24h", "trapped_capital_pct", "free_cells"):
                grid[name] = latest.get(name)
    last_run = db.get_last_monitor_run()
    return {"grids": grids, "repositories": [g for g in grids if g["status"] == "HOLDING"],
            "last_monitor_run": last_run}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Manual grid monitor controls")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("status", help="print grids, cells, repositories, and last pass as JSON")
    loans_parser = subparsers.add_parser("loans", help="print read-only loan history for a grid")
    loans_parser.add_argument("--grid-id", required=True, type=int)
    open_parser = subparsers.add_parser("open", help="validate or open a Testnet grid")
    open_parser.add_argument("--symbol", required=True)
    open_parser.add_argument("--low", required=True, type=Decimal)
    open_parser.add_argument("--high", required=True, type=Decimal)
    open_parser.add_argument("--n", required=True, type=int)
    open_parser.add_argument("--capital", required=True, type=Decimal)
    open_parser.add_argument("--strategy", required=True, choices=("simple", "smart"))
    open_parser.add_argument("--shift-half-step", action="store_true")
    open_parser.add_argument("--stop-loss-pct", type=Decimal)
    open_parser.add_argument("--param", action="append", default=[])
    open_parser.add_argument("--target-pct", type=Decimal)
    open_parser.add_argument("--target-usdt", type=Decimal)
    open_parser.add_argument("--target-basis", choices=("cash", "equity"))
    open_parser.add_argument("--max-days", type=Decimal, help="automatic repository close after N calendar days")
    open_parser.add_argument("--calibration", help="use latest or a validated calibration ID")
    open_parser.add_argument("--dry-run", action="store_true")
    open_parser.add_argument("--yes", action="store_true")
    pair_parser = subparsers.add_parser("open-pair", help="preview or open a paired loan/control experiment")
    pair_parser.add_argument("--symbol", required=True)
    pair_parser.add_argument("--range-low", required=True, type=Decimal)
    pair_parser.add_argument("--range-high", required=True, type=Decimal)
    pair_parser.add_argument("--n-levels", required=True, type=int)
    pair_parser.add_argument("--capital-per-arm", required=True, type=Decimal)
    pair_parser.add_argument("--pair-seed", type=int)
    pair_parser.add_argument("--factor", choices=("loans", "idle_shrink", "capital_shrink"), default="loans")
    pair_parser.add_argument("--dry-run", action="store_true", help="preview only; this is the default")
    pair_parser.add_argument("--execute", action="store_true", help="execute both Testnet arms")
    pair_parser.add_argument("--confirm", action="store_true", help="required with --execute")
    close = subparsers.add_parser("close", help="close one grid")
    close.add_argument("grid_id", type=int)
    close.add_argument("--mode", required=True, choices=("cancel", "liquidate", "repository"))
    close.add_argument("--yes", action="store_true", help="required confirmation for market liquidation")
    adjust = subparsers.add_parser("adjust", help="move free cells to a new grid range")
    adjust.add_argument("--grid-id", required=True, type=int)
    adjust.add_argument("--low", required=True, type=Decimal)
    adjust.add_argument("--high", required=True, type=Decimal)
    adjust.add_argument("--n", type=int)
    adjust.add_argument("--dry-run", action="store_true")
    adjust.add_argument("--yes", action="store_true")
    subparsers.add_parser("run-once", help="run one monitor pass without starting the scheduler")
    dust = subparsers.add_parser("sweep-dust", help="preview or sweep one grid's recorded dust")
    dust.add_argument("--grid", required=True, type=int)
    dust.add_argument("--execute", action="store_true", help="send the market sell; default is dry-run")
    args = parser.parse_args(argv)
    if args.command == "close" and args.mode == "liquidate" and not args.yes:
        print("error: close --mode liquidate requiere --yes", file=sys.stderr)
        return 2
    if args.command == "adjust" and not args.dry_run and not args.yes:
        print("error: adjust requiere --yes para enviar órdenes", file=sys.stderr)
        return 2
    if args.command == "open-pair" and args.execute and not args.confirm:
        print("error: open-pair --execute requiere --confirm", file=sys.stderr)
        return 2
    if args.command == "open-pair" and args.confirm and not args.execute:
        print("error: --confirm requiere --execute", file=sys.stderr)
        return 2
    if args.command == "open-pair" and args.dry_run and args.execute:
        print("error: --dry-run y --execute son incompatibles", file=sys.stderr)
        return 2
    try:
        context = build_context()
        if args.command == "status":
            result = _status(context)
        elif args.command == "sweep-dust":
            db, exchange, engine = context["db"], context["exchange"], context["engine"]
            grid = db.get_grid(args.grid)
            if grid is None:
                raise ValueError(f"grid {args.grid} does not exist")
            filters = SymbolFilters.from_symbol_info(exchange.get_symbol_info(grid["symbol"]))
            bid = exchange.get_book_ticker(grid["symbol"])["bid_price"]
            if args.execute:
                result = engine.sweep_grid_dust(args.grid, bid, reason="cli")
            else:
                result = {**plan_dust_sweep(grid.get("dust_qty") or 0, bid, filters, .1),
                          "status": "DRY_RUN", "grid_id": args.grid, "bid": str(bid)}
        elif args.command == "loans":
            grid = context["db"].get_grid(args.grid_id)
            if grid is None:
                raise ValueError(f"grid {args.grid_id} does not exist")
            result = {"grid_id": int(args.grid_id),
                      "loans": context["db"].list_grid_loans(int(args.grid_id))}
        elif args.command == "open-pair":
            from fastapi import Request
            from api.routes.grids import PairOpenRequest, open_loan_pair
            app = context["app"]
            token = str(getattr(context["settings"], "grid_api_token", "") or "").strip()
            headers = [] if not token else [(b"x-api-token", token.encode("utf-8"))]
            scope = {"type": "http", "app": app, "method": "POST", "path": "/api/grids/pair",
                     "headers": headers, "client": ("127.0.0.1", 0), "server": ("127.0.0.1", 80),
                     "scheme": "http", "query_string": b""}
            request = Request(scope)
            body = PairOpenRequest(
                symbol=args.symbol, capital=args.capital_per_arm, range_low=args.range_low,
                range_high=args.range_high, n_levels=args.n_levels, pair_seed=args.pair_seed,
                factor=args.factor,
                dry_run=not args.execute, confirm=args.confirm,
            )
            result = open_loan_pair(request, body)
        elif args.command == "adjust":
            if str(getattr(context["settings"], "environment", "")).casefold() != "testnet":
                raise RuntimeError("grid adjust solo permite environment=testnet")
            grid = context["db"].get_grid(args.grid_id)
            if grid is None:
                raise ValueError(f"grid {args.grid_id} does not exist")
            if args.dry_run:
                result = context["engine"].preview_adjust(args.grid_id, args.low, args.high, args.n)
            else:
                engine, db = context["engine"], context["db"]
                old_sink = getattr(engine, "event_sink", None)
                def cli_event(event):
                    db.add_grid_event(
                        run_id=None, source="CLI", grid_id=event.get("grid_id"),
                        level_idx=event.get("level_idx"),
                        client_order_id=event.get("client_order_id"), order_id=event.get("order_id"),
                        event_type=event["event_type"], reason=event.get("reason"),
                        price=event.get("price"), details=event.get("details"),
                    )
                engine.event_sink = cli_event
                try:
                    result = engine.adjust_grid(args.grid_id, args.low, args.high, args.n,
                                                reason="operator_adjust", details={"source": "CLI"})
                finally:
                    engine.event_sink = old_sink
        elif args.command == "open":
            settings, exchange, engine = context["settings"], context["exchange"], context["engine"]
            if str(getattr(settings, "environment", "")).casefold() != "testnet":
                raise RuntimeError("grid open solo permite environment=testnet")
            if not args.dry_run and not args.yes:
                raise RuntimeError("open requiere --yes para enviar órdenes")
            symbol = args.symbol.replace("/", "").upper()
            low, high = args.low, args.high
            if args.n < 2 or args.capital <= 0 or low <= 0 or high <= low:
                raise ValueError("rango, niveles y capital deben ser positivos y válidos")
            step = (high - low) / Decimal(args.n)
            if args.shift_half_step:
                low += step / 2
                high += step / 2
            params = {}
            for item in args.param:
                if "=" not in item:
                    raise ValueError("--param requiere clave=valor")
                key, raw = item.split("=", 1)
                if key not in DEFAULT_SMART_PARAMS:
                    raise ValueError(f"parámetro smart desconocido: {key}")
                if key in params:
                    raise ValueError(f"parámetro repetido: {key}")
                params[key] = None if raw.casefold() == "none" else json.loads(raw)
            if args.strategy == "simple" and params:
                raise ValueError("--param solo se admite con --strategy smart")
            targets = {"target_pct": args.target_pct, "target_usdt": args.target_usdt,
                       "target_basis": args.target_basis}
            if args.strategy == "simple" and any(value is not None for value in targets.values()):
                raise ValueError("--target-pct/--target-usdt/--target-basis solo se admiten con --strategy smart")
            params.update({key: value for key, value in targets.items() if value is not None})
            if args.max_days is not None:
                if args.max_days <= 0:
                    raise ValueError("--max-days debe ser mayor que cero")
                params["max_days"] = args.max_days
            calibration_id = None
            calibration_params = {}
            if args.calibration is not None:
                if args.strategy != "smart":
                    raise ValueError("--calibration solo se admite con --strategy smart")
                calibration = (context["db"].get_latest_grid_calibration() if args.calibration == "latest"
                               else context["db"].get_grid_calibration(int(args.calibration)))
                if calibration is None:
                    raise ValueError(f"calibración {args.calibration} no existe")
                if calibration.get("verdict") != "validated_walk_forward":
                    raise ValueError(f"calibración {calibration.get('id')} rechazada: verdict defaults_kept")
                calibration_id = int(calibration["id"])
                calibration_params = dict(calibration.get("params") or {})
            calibration_params.update(params)
            effective = (validate_params(calibration_params, args.n)
                         if args.strategy == "smart" or args.max_days is not None else None)
            cohort_base = dict(effective or {})
            control_every_n = int(getattr(settings, "loans_control_every_n", 3))
            if args.strategy == "smart" and args.dry_run and effective is not None:
                effective = _cli_loan_creation_params(
                    context["db"], effective, params, control_every_n)
            if effective and (effective.get("target_pct") is not None or effective.get("target_usdt") is not None):
                target_goals = []
                if effective.get("target_pct") is not None:
                    target_goals.append(float(args.capital) * float(effective["target_pct"]) / 100)
                if effective.get("target_usdt") is not None:
                    target_goals.append(float(effective["target_usdt"]))
                target_amount = min(target_goals)
                round_trip_fee = float(args.capital) * 0.1 / 100 * 2
                if target_amount < round_trip_fee:
                    warnings.warn("target is below an approximate two-sided 0.1% fee on capital", RuntimeWarning)
            stop_loss = args.stop_loss_pct
            if stop_loss is not None and stop_loss <= 0:
                raise ValueError("--stop-loss-pct debe ser mayor que cero")
            if effective is not None and stop_loss is not None:
                effective["stop_loss_pct"] = float(stop_loss)
            filters = SymbolFilters.from_symbol_info(exchange.get_symbol_info(symbol))
            reserve_pct = Decimal(str((effective or {}).get("reserve_pct", 0)))
            reserve = args.capital * reserve_pct / Decimal(100)
            distributable = args.capital - reserve
            minimum_cell = distributable / Decimal(args.n)
            exchange_minimum_cell = filters.min_notional * Decimal("1.1")
            required_cell = functional_cell_threshold(filters, args.strategy, exchange_minimum_cell)
            if minimum_cell < exchange_minimum_cell:
                raise ValueError(f"capital por celda debe ser al menos min_notional ? 1.1")
            if step / low < Decimal("0.003"):
                raise ValueError("paso mínimo entre niveles: 0.30%")
            lines = compute_lines(low, high, args.n, filters)
            book = exchange.get_book_ticker(symbol)
            avg = Decimal(str(exchange.get_avg_price(symbol)))
            snapshot = {"bid_price": Decimal(str(book["bid_price"])),
                        "ask_price": Decimal(str(book["ask_price"])), "avg_price": avg}
            plans = plan_cells(lines, distributable, snapshot, filters, settings)
            if args.dry_run:
                result = {
                    "dry_run": True, "symbol": symbol, "strategy": args.strategy,
                    "range": {"low": str(low), "high": str(high)},
                    "levels": [str(value) for value in lines],
                    "capital_per_cell": str(minimum_cell),
                    "functional_cell_warning": functional_cell_warning(args.capital, args.n, required_cell, args.strategy),
                    "reserve": str(reserve),
                    "distributable_capital": str(distributable),
                    "params": effective, "calibrated": calibration_id is not None,
                    "calibration_id": calibration_id,
                    "loans_group": None if effective is None else effective.get("loans_group"),
                    "loans_enabled": None if effective is None else effective.get("loans_enabled"),
                    "loan_lender_max_pct": None if effective is None else effective.get("loan_lender_max_pct"),
                    "validations": {"min_notional_with_margin": True, "min_step_pct": float(step / low * 100),
                                    "planned_buy_cells": sum(plan.initial_state == "BUY_OPEN" for plan in plans)},
                }
            else:
                effective, grid = create_grid_with_loan_cohort(
                    context["db"], args.strategy, cohort_base, control_every_n,
                    lambda assigned: engine.create_grid(
                        symbol, low, high, args.n, args.capital, strategy=args.strategy,
                        params=assigned or None, stop_loss_pct=stop_loss,
                        calibration_id=calibration_id),
                    explicit_params=params,
                )
                grid = context["db"].get_grid(int(grid["id"]))
                context["db"].add_grid_event(run_id=None, source="CLI", event_type="GRID_OPENED",
                                             grid_id=int(grid["id"]), reason="operator_open",
                                             details={"strategy": args.strategy, "params": effective,
                                                      "loans_group": None if effective is None else effective.get("loans_group"),
                                                      "loans_enabled": None if effective is None else effective.get("loans_enabled"),
                                                      "loan_lender_max_pct": None if effective is None else effective.get("loan_lender_max_pct"),
                                                      "calibrated": calibration_id is not None,
                                                      "calibration_id": calibration_id,
                                                      "explicit_params_prevailed": bool((params or stop_loss is not None) and calibration_id is not None),
                                                      "range_low": str(low), "range_high": str(high)})
                result = {"grid": grid,
                          "loans_group": None if effective is None else effective.get("loans_group"),
                          "loans_enabled": None if effective is None else effective.get("loans_enabled"),
                          "loan_lender_max_pct": None if effective is None else effective.get("loan_lender_max_pct"),
                          "calibrated": calibration_id is not None,
                          "calibration_id": calibration_id,
                          "explicit_params_prevailed": bool((params or stop_loss is not None) and calibration_id is not None)}
        elif args.command == "close":
            result = context["engine"].close_grid(args.grid_id, args.mode)
        else:
            result = context["monitor"].run_once("SCHEDULED")
        print(json.dumps(result, ensure_ascii=False, default=str, indent=2))
        return 0
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
