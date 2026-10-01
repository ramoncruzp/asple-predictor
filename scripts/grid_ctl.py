"""Manual status, close, and one-shot controls for the grid monitor."""

from __future__ import annotations

import argparse
import json
import sys
from decimal import Decimal, InvalidOperation
from typing import Any

from config.settings import Settings
from data.testnet_client import TestnetClient
from database.db_manager import DBManager
from grid.engine import GridEngine
from grid.monitor import GridMonitor
from grid.levels import compute_lines, plan_cells
from grid.policy import DEFAULT_SMART_PARAMS, validate_params
from data.exchange_filters import SymbolFilters


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
    return {"settings": settings, "db": db, "exchange": exchange,
            "engine": engine, "monitor": monitor}


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
        latest_adjust = db.get_last_event(int(grid["id"]), "GRID_ADJUSTED") \
            if hasattr(db, "get_last_event") else None
        grid["last_adjust_at"] = None if latest_adjust is None else latest_adjust.get("ts")
        grid["adjust_enabled"] = bool((grid.get("params") or {}).get("adjust_enabled", False))
        grid["calibrated"] = False
        calibration_id = grid.get("calibration_id")
        if calibration_id is not None and hasattr(db, "get_grid_calibration"):
            calibration = db.get_grid_calibration(int(calibration_id))
            if calibration:
                grid["calibrated"] = calibration.get("verdict") == "validated_walk_forward"
                if grid["calibrated"]:
                    grid["calibration"] = {"id": int(calibration_id), "verdict": calibration.get("verdict"),
                                            "data_end": calibration.get("data_end")}
        snapshots = db.list_grid_snapshots(grid_id=int(grid["id"]), limit=100) if hasattr(db, "list_grid_snapshots") else []
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
    open_parser.add_argument("--calibration", help="use latest or a validated calibration ID")
    open_parser.add_argument("--dry-run", action="store_true")
    open_parser.add_argument("--yes", action="store_true")
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
    args = parser.parse_args(argv)
    if args.command == "close" and args.mode == "liquidate" and not args.yes:
        print("error: close --mode liquidate requiere --yes", file=sys.stderr)
        return 2
    if args.command == "adjust" and not args.dry_run and not args.yes:
        print("error: adjust requiere --yes para enviar órdenes", file=sys.stderr)
        return 2
    try:
        context = build_context()
        if args.command == "status":
            result = _status(context)
        elif args.command == "loans":
            grid = context["db"].get_grid(args.grid_id)
            if grid is None:
                raise ValueError(f"grid {args.grid_id} does not exist")
            result = {"grid_id": int(args.grid_id),
                      "loans": context["db"].list_grid_loans(int(args.grid_id))}
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
            effective = validate_params(calibration_params, args.n) if args.strategy == "smart" else None
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
            if minimum_cell < filters.min_notional * Decimal("1.1"):
                raise ValueError("capital por celda debe ser al menos min_notional × 1.1")
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
                    "reserve": str(reserve),
                    "distributable_capital": str(distributable),
                    "params": effective, "calibrated": calibration_id is not None,
                    "calibration_id": calibration_id,
                    "validations": {"min_notional_with_margin": True, "min_step_pct": float(step / low * 100),
                                    "planned_buy_cells": sum(plan.initial_state == "BUY_OPEN" for plan in plans)},
                }
            else:
                grid = engine.create_grid(symbol, low, high, args.n, args.capital,
                                          strategy=args.strategy, params=effective,
                                          stop_loss_pct=stop_loss, calibration_id=calibration_id)
                context["db"].add_grid_event(run_id=None, source="CLI", event_type="GRID_OPENED",
                                             grid_id=int(grid["id"]), reason="operator_open",
                                             details={"strategy": args.strategy, "params": effective,
                                                      "calibrated": calibration_id is not None,
                                                      "calibration_id": calibration_id,
                                                      "explicit_params_prevailed": bool((params or stop_loss is not None) and calibration_id is not None),
                                                      "range_low": str(low), "range_high": str(high)})
                result = {"grid": grid, "calibrated": calibration_id is not None,
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
