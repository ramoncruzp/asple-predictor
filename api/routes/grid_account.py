"""Read-only Testnet account and grid reconciliation endpoints."""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from fastapi import APIRouter, HTTPException, Request

from api.routes.grids import _authorize
from grid.status_view import daily_profit_view, grid_summary

router = APIRouter(prefix="/api/account", tags=["grid-account"])
logger = logging.getLogger(__name__)
GRID_STATES = {"OPENING", "ACTIVE", "PAUSED", "CLOSING", "HOLDING", "CLOSED", "ERROR"}


def _client(request):
    client = getattr(request.app.state, "testnet_client", None)
    if client is None or getattr(getattr(client, "client", None), "testnet", None) is not True:
        return None
    return client


def _sanitize(request, message: str) -> str:
    settings = request.app.state.settings
    for name in ("testnet_api_key", "testnet_api_secret", "binance_api_key", "binance_api_secret"):
        value = str(getattr(settings, name, "") or "")
        if value:
            message = message.replace(value, "[redacted]")
    return message[:240]


def _account_snapshot(request):
    state = request.app.state
    now = time.monotonic()
    cached = getattr(state, "grid_account_cache", None)
    if cached and now - cached[0] < 10:
        return cached[1]
    client = _client(request)
    settings = state.settings
    key = str(getattr(settings, "testnet_api_key", "") or "").strip()
    secret = str(getattr(settings, "testnet_api_secret", "") or "").strip()
    configured = bool(key and secret and not key.casefold().startswith("tu_")
                      and not secret.casefold().startswith("tu_"))
    suffix = key[-4:] if configured and len(key) >= 4 else (key if configured else None)
    checked = datetime.now(timezone.utc).isoformat()
    if client is None:
        result = {"configured": configured, "testnet_confirmed": False, "key_suffix": suffix,
                  "can_trade": None, "account_type": None, "checked_at": checked,
                  "unavailable_reason": "cliente Testnet no disponible"}
    else:
        try:
            account = client._run_read(client.client.get_account)
            result = {"configured": configured, "testnet_confirmed": True, "key_suffix": suffix,
                      "can_trade": bool(account.get("canTrade")),
                      "account_type": account.get("accountType"), "checked_at": checked,
                      "unavailable_reason": None}
        except Exception as exc:
            safe = _sanitize(request, str(exc))
            logger.warning("Testnet account connection check failed: %s", safe)
            result = {"configured": configured, "testnet_confirmed": True, "key_suffix": suffix,
                      "can_trade": None, "account_type": None, "checked_at": checked,
                      "unavailable_reason": safe}
    state.grid_account_cache = (now, result)
    return result


@router.get("/connection")
def connection(request: Request):
    _authorize(request)
    return dict(_account_snapshot(request))


@router.get("/summary")
def summary(request: Request):
    _authorize(request)
    state, client, db = request.app.state, _client(request), request.app.state.db
    cached_summary = getattr(state, "grid_account_summary_cache", None)
    if cached_summary and time.monotonic() - cached_summary[0] < 10:
        return cached_summary[1]
    now = datetime.now(timezone.utc)
    grids = db.list_grids_by_status(GRID_STATES)
    prices = {}
    price_budget = 20
    balances = None
    unavailable = None
    if client is not None:
        try:
            balances = client.get_balance()
        except Exception as exc:
            unavailable = _sanitize(request, str(exc))
            logger.warning("Testnet balance read failed: %s", unavailable)
            balances = None
    else:
        unavailable = "cliente Testnet no disponible"
    summaries, summaries_open, summaries_closed, summaries_repo = [], [], [], []
    per_grid = []
    for grid in grids:
        grid_id = int(grid["id"])
        levels = db.get_grid_levels(grid_id)
        price = None
        if client is not None:
            symbol = str(grid["symbol"])
            if symbol not in prices:
                if price_budget > 0:
                    price_budget -= 1
                    try:
                        prices[symbol] = float(client.get_avg_price(symbol))
                    except Exception:
                        prices[symbol] = None
                else: prices[symbol] = None
            price = prices[symbol]
        view = grid_summary(grid, levels, price, now=now,
                            fee_pct=float(getattr(state.settings, "scanner_fee_pct", .1)),
                            price_as_of=now if price is not None else None)
        summaries.append(view)
        if grid["status"] == "HOLDING": summaries_repo.append(view)
        elif grid["status"] in {"CLOSED", "ERROR"}: summaries_closed.append(view)
        else: summaries_open.append(view)
        per_grid.append({"id": grid_id, "symbol": grid["symbol"], "strategy": grid.get("strategy"),
                         "status": grid["status"], "capital_usdt": float(grid.get("capital_total") or 0),
                         "realized_usdt": view["net_realized_usdt"],
                         "unrealized_usdt": view["inventory"]["unrealized_pnl_usdt"],
                         "total_with_inventory_usdt": view["total_with_inventory_usdt"],
                         "capital_share_pct": None})
    capital = sum(Decimal(str(row["capital_usdt"])) for row in per_grid)
    for row in per_grid:
        row["capital_share_pct"] = (float(Decimal(str(row["capital_usdt"])) / capital * 100)
                                     if capital else None)
    # Aggregate daily rows from each grid's own event stream; identities stay grid-scoped.
    daily_by_window = {}
    for days in (7, 30):
        events_all, net_all = [], Decimal(0)
        cutoff = now - timedelta(days=days)
        for grid in grids:
            events = db.list_grid_events(grid_id=int(grid["id"]), limit=5000)
            for event in events:
                event_ts = event.get("ts")
                if event_ts is not None:
                    event_ts = event_ts.replace(tzinfo=timezone.utc) if event_ts.tzinfo is None else event_ts.astimezone(timezone.utc)
                    if event_ts >= cutoff: events_all.append(event)
            net_all += Decimal(str(next((g["net_realized_usdt"] or 0 for g in summaries
                                          if g["id"] == int(grid["id"])), 0)))
        daily_by_window[f"{days}d"] = daily_profit_view(events_all, net_realized_usdt=None,
                                                        reconcile_available=False)["daily"]
    account_balance = None
    if balances is not None:
        assets, unvalued, equity = [], [], Decimal(0)
        for asset_index, (asset, amounts) in enumerate(balances.items()):
            free, locked = Decimal(str(amounts.get("free", 0))), Decimal(str(amounts.get("locked", 0)))
            if asset == "USDT": price = Decimal(1)
            elif price_budget > 0:
                price_budget -= 1
                try: price = client.get_avg_price(f"{asset}USDT")
                except Exception: price = None
            else: price = None
            item = {"asset": asset, "free": str(free), "locked": str(locked),
                    "price_usdt": None if price is None else str(price),
                    "value_usdt": None if price is None else str((free + locked) * Decimal(str(price)))}
            if price is None: unvalued.append(item)
            else: assets.append(item); equity += (free + locked) * Decimal(str(price))
        account_balance = {"assets": assets, "unvalued": unvalued,
                           "usdt_free": str(Decimal(str(balances.get("USDT", {}).get("free", 0)))),
                           "usdt_locked": str(Decimal(str(balances.get("USDT", {}).get("locked", 0)))),
                           "equity_usdt": str(equity), "price_as_of": now.isoformat()}
    held_by_asset = {}
    for grid in grids:
        if grid["status"] in {"CLOSED", "ERROR"}: continue
        asset = str(grid["symbol"]).removesuffix("USDT")
        qty = Decimal(str(grid.get("dust_qty") or 0)) + sum(
            (Decimal(str(level.get("held_qty") or 0)) for level in db.get_grid_levels(int(grid["id"]))), Decimal(0))
        held_by_asset[asset] = held_by_asset.get(asset, Decimal(0)) + qty
    reconciliation = []
    if balances is not None:
        for asset in sorted(set(balances) | set(held_by_asset)):
            exchange_qty = Decimal(str(balances.get(asset, {}).get("free", 0))) + Decimal(str(balances.get(asset, {}).get("locked", 0)))
            diff = exchange_qty - held_by_asset.get(asset, Decimal(0))
            reconciliation.append({"asset": asset, "balance_exchange": str(exchange_qty),
                "assigned_to_grids": str(held_by_asset.get(asset, 0)), "difference": str(diff),
                "severity": "ok" if diff == 0 else "unassigned" if diff > 0 else "inconsistency",
                "explanation": "saldo no asignado a ningún grid" if diff > 0 else
                    "los grids dicen tener más de lo que hay en la cuenta" if diff < 0 else "conciliado"})
    result = {"balance": account_balance, "unavailable_reason": unavailable,
            "gains": {"realized_usdt": float(sum((Decimal(str(x["net_realized_usdt"] or 0)) for x in summaries), Decimal(0))),
                      "fee_real_usdt": float(sum((Decimal(str(x["fee_real_usdt"] or 0)) for x in summaries), Decimal(0))),
                      "fee_estimated_0_1pct_usdt": float(sum((Decimal(str(x["fees_estimated_usdt"] or 0)) for x in summaries), Decimal(0))),
                      "unrealized_usdt": float(sum((Decimal(str(x["inventory"]["unrealized_pnl_usdt"] or 0)) for x in summaries), Decimal(0))),
                      "total_with_inventory_usdt": float(sum((Decimal(str(x["total_with_inventory_usdt"] or 0)) for x in summaries), Decimal(0))),
                      "label": "realizada junto al total con inventario", "operations": sum(x["cycles_completed"] for x in summaries),
                      "daily": daily_by_window},
            "grids": {"open": [x for x in per_grid if x["status"] not in {"HOLDING", "CLOSED", "ERROR"}],
                      "repository": [x for x in per_grid if x["status"] == "HOLDING"],
                      "closed": [x for x in per_grid if x["status"] in {"CLOSED", "ERROR"}]},
            "reconciliation": {"assets": reconciliation,
                "grid_capital_usdt": str(capital),
                "usdt_free": None if account_balance is None else account_balance["usdt_free"],
                "free_minus_grid_capital_usdt": None if account_balance is None else str(
                    Decimal(str(account_balance["usdt_free"])) - capital)}}
    state.grid_account_summary_cache = (time.monotonic(), result)
    return result
