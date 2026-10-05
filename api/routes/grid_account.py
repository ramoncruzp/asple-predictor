"""Read-only Testnet account and grid reconciliation endpoints."""
from __future__ import annotations

import logging
import os
import shutil
import tempfile
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from ipaddress import ip_address
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from api.routes.grids import _authorize
from grid.status_view import daily_profit_view, grid_summary
from grid.reconciliation import expected_inventory_by_asset

router = APIRouter(prefix="/api/account", tags=["grid-account"])
logger = logging.getLogger(__name__)
GRID_STATES = {"OPENING", "ACTIVE", "PAUSED", "CLOSING", "HOLDING", "CLOSED", "ERROR"}


class TestnetCredentials(BaseModel):
    model_config = ConfigDict(extra="forbid")
    api_key: SecretStr = Field(min_length=1)
    api_secret: SecretStr = Field(min_length=1)


def _assert_loopback(request: Request) -> None:
    if request.headers.get("x-forwarded-for") is not None:
        raise HTTPException(status_code=403, detail="Solo se permite desde una conexión local directa.")
    host = request.client.host if request.client else ""
    try:
        local = ip_address(host).is_loopback
    except ValueError:
        local = False
    if not local:
        raise HTTPException(status_code=403, detail="Solo se permite desde una conexión local directa.")


def _has_blocking_grid_work(db) -> bool:
    statuses = {"OPENING", "ACTIVE", "PAUSED", "CLOSING", "HOLDING", "CLOSED", "ERROR"}
    grids = db.list_grids_by_status(statuses)
    if any(str(grid.get("status", "")).upper() in {"OPENING", "ACTIVE", "PAUSED", "CLOSING", "HOLDING"}
           for grid in grids):
        return True
    for grid in grids:
        loans = db.list_grid_loans(int(grid["id"]))
        if any(str(loan.get("status", "")).upper() in {"PENDING", "OPEN"} for loan in loans):
            return True
    return False


def _replace_env_credentials(path: Path, api_key: str, api_secret: str) -> None:
    original = path.read_text(encoding="utf-8") if path.exists() else ""
    replacements = {"TESTNET_API_KEY": api_key, "TESTNET_SECRET": api_secret}
    lines = original.splitlines(keepends=True)
    found = set()
    output = []
    for line in lines:
        stripped = line.lstrip()
        name = stripped.split("=", 1)[0].strip() if "=" in stripped and not stripped.startswith("#") else ""
        if name not in replacements:
            output.append(line)
            continue
        newline = "\r\n" if line.endswith("\r\n") else "\n" if line.endswith("\n") else ""
        indent = line[:len(line) - len(stripped)]
        value = replacements[name].replace("\\", "\\\\").replace('"', '\\"')
        output.append(f'{indent}{name}="{value}"{newline}')
        found.add(name)
    for name, value in replacements.items():
        if name not in found:
            if output and not output[-1].endswith(("\n", "\r")):
                output.append("\n")
            escaped = value.replace("\\", "\\\\").replace('"', '\\"')
            output.append(f'{name}="{escaped}"\n')
    updated = "".join(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        shutil.copy2(path, path.with_name(path.name + ".bak"))
    temp_name = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="", dir=path.parent,
                                         prefix=path.name + ".", suffix=".tmp", delete=False) as handle:
            temp_name = handle.name
            handle.write(updated)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
        temp_name = None
    finally:
        if temp_name and os.path.exists(temp_name):
            os.unlink(temp_name)


@router.post("/testnet-credentials")
def update_testnet_credentials(request: Request, body: TestnetCredentials):
    _authorize(request)
    _assert_loopback(request)
    db = request.app.state.db
    if _has_blocking_grid_work(db):
        raise HTTPException(status_code=409, detail="No se pueden cambiar claves mientras haya grids, cierres o reposiciones pendientes.")
    key, secret = body.api_key.get_secret_value(), body.api_secret.get_secret_value()
    validator = getattr(request.app.state, "testnet_credentials_validator", None)
    try:
        if validator is not None:
            validator(key, secret)
        else:
            from data.testnet_client import TestnetClient
            settings = request.app.state.settings
            candidate = TestnetClient(key, secret, production_api_key=getattr(settings, "binance_api_key", None))
            candidate._run_read(candidate.client.get_account)
    except Exception as exc:
        logger.warning("Testnet credential validation failed (%s)", type(exc).__name__)
        raise HTTPException(status_code=422, detail="Testnet rechazó las credenciales; verifica la clave, el secreto y sus permisos.") from None
    env_path = Path(os.environ.get("ASPLE_ENV_FILE", ".env")).expanduser().resolve()
    try:
        _replace_env_credentials(env_path, key, secret)
    except Exception as exc:
        logger.error("Could not atomically save Testnet credentials (%s)", type(exc).__name__)
        raise HTTPException(status_code=500, detail="No se pudieron guardar las credenciales; el archivo original permanece intacto.") from None
    return {"saved": True, "key_suffix": key[-4:], "message": "Reinicia el servidor para aplicar el cambio."}


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
    share_rows = [row for row in per_grid if row["status"] not in {"CLOSED", "ERROR"}]
    capital = sum(Decimal(str(row["capital_usdt"])) for row in share_rows)
    for row in per_grid:
        row["capital_share_basis"] = "abiertos+repositorio"
        row["capital_share_pct"] = (float(Decimal(str(row["capital_usdt"])) / capital * 100)
                                     if capital and row in share_rows else None)
    # Aggregate daily rows from each grid's own event stream; identities stay grid-scoped.
    daily_by_window = {}
    events_all = [event for grid in grids
                  for event in db.list_grid_events(grid_id=int(grid["id"]), limit=5000)]
    for days in (7, 30):
        cutoff = now - timedelta(days=days)
        in_window = []
        for event in events_all:
            event_ts = event.get("ts")
            if event_ts is not None:
                event_ts = event_ts.replace(tzinfo=timezone.utc) if event_ts.tzinfo is None else event_ts.astimezone(timezone.utc)
                if event_ts >= cutoff: in_window.append(event)
        daily_by_window[f"{days}d"] = daily_profit_view(in_window, net_realized_usdt=None,
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
    held_by_asset, _grid_ids_by_asset = expected_inventory_by_asset(db, grids)
    registered_assets = {str(row["symbol"]).removesuffix("USDT")
                         for row in db.get_all_coins()} if hasattr(db, "get_all_coins") else set()
    reconciliation = []
    if balances is not None:
        for asset in sorted((set(balances) | set(held_by_asset)) - {"USDT"}):
            exchange_qty = Decimal(str(balances.get(asset, {}).get("free", 0))) + Decimal(str(balances.get(asset, {}).get("locked", 0)))
            diff = exchange_qty - held_by_asset.get(asset, Decimal(0))
            reconciliation.append({"asset": asset, "balance_exchange": str(exchange_qty),
                "assigned_to_grids": str(held_by_asset.get(asset, 0)), "difference": str(diff),
                "severity": "ok" if diff == 0 else "unassigned" if diff > 0 else "inconsistency",
                "relevant": asset in registered_assets or asset in held_by_asset,
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
                "grid_capital_usdt": str(capital), "capital_share_basis": "abiertos+repositorio",
                "usdt_free": None if account_balance is None else account_balance["usdt_free"],
                "free_minus_grid_capital_usdt": None if account_balance is None else str(
                    Decimal(str(account_balance["usdt_free"])) - capital)}}
    state.grid_account_summary_cache = (time.monotonic(), result)
    return result
