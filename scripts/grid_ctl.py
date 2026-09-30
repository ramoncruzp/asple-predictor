"""Manual status, close, and one-shot controls for the grid monitor."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from config.settings import Settings
from data.testnet_client import TestnetClient
from database.db_manager import DBManager
from grid.engine import GridEngine
from grid.monitor import GridMonitor


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
    last_run = db.get_last_monitor_run()
    return {"grids": grids, "repositories": [g for g in grids if g["status"] == "HOLDING"],
            "last_monitor_run": last_run}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Manual grid monitor controls")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("status", help="print grids, cells, repositories, and last pass as JSON")
    close = subparsers.add_parser("close", help="close one grid")
    close.add_argument("grid_id", type=int)
    close.add_argument("--mode", required=True, choices=("cancel", "liquidate", "repository"))
    close.add_argument("--yes", action="store_true", help="required confirmation for market liquidation")
    subparsers.add_parser("run-once", help="run one monitor pass without starting the scheduler")
    args = parser.parse_args(argv)
    if args.command == "close" and args.mode == "liquidate" and not args.yes:
        print("error: close --mode liquidate requiere --yes", file=sys.stderr)
        return 2
    try:
        context = build_context()
        if args.command == "status":
            result = _status(context)
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
