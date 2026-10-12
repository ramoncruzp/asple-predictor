"""Read-only inventory of open exchange orders and their local grid owners."""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
ROOT_STRING = str(ROOT)
if ROOT_STRING in sys.path:
    sys.path.remove(ROOT_STRING)
sys.path.insert(0, ROOT_STRING)

_SYSTEM_CLIENT_ID = re.compile(r"g(?P<grid>\d+)L(?P<level>\d+)(?P<side>[BS])(?P<cycle>\d+)")
_SYSTEM_ADJUSTMENT_CLIENT_ID = re.compile(r"gA[0-9a-f]{30}")
_SYSTEM_LOAN_CLIENT_ID = re.compile(r"gL[0-9a-f]{30}")
_SYMBOL = re.compile(r"[A-Z0-9]{6,20}")


def parse_symbols(value: str) -> list[str]:
    """Parse extra uppercase alphanumeric trading-pair symbols."""
    parts = [part.strip() for part in str(value).split(",")]
    if not parts or any(not part or _SYMBOL.fullmatch(part) is None for part in parts):
        raise argparse.ArgumentTypeError(
            "symbols must be comma-separated uppercase trading pairs (6-20 letters or digits).")
    return list(dict.fromkeys(parts))


def _safe_error_label(exc: Exception) -> str:
    if isinstance(exc, ModuleNotFoundError):
        missing = str(exc.name or "").strip()
        return f"ModuleNotFoundError (módulo faltante: {missing})" if missing else "ModuleNotFoundError"
    if type(exc).__name__ == "BinanceAPIException":
        code = getattr(exc, "code", None)
        if type(code) is int or (isinstance(code, str) and re.fullmatch(r"-?\d+", code)):
            return f"BinanceAPIException (code={code})"
        return "BinanceAPIException"
    return type(exc).__name__


def classify_open_order(
    order: dict[str, Any],
    grids: dict[int, dict[str, Any]],
    levels: dict[tuple[int, int], list[dict[str, Any]]],
    expected_symbol: str | None = None,
    loans: dict[str, list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    """Classify one normalized open order using only supplied local snapshots."""
    client_id = str(order.get("client_order_id") or order.get("clientOrderId") or "")
    match = _SYSTEM_CLIENT_ID.fullmatch(client_id)
    is_adjustment_id = _SYSTEM_ADJUSTMENT_CLIENT_ID.fullmatch(client_id) is not None
    is_loan_id = _SYSTEM_LOAN_CLIENT_ID.fullmatch(client_id) is not None
    is_own_id = match is not None or is_adjustment_id or is_loan_id
    symbol = str(order.get("symbol") or expected_symbol or "").upper()
    result = {
        "symbol": symbol,
        "grid_id": None,
        "level_idx": None,
        "side": str(order.get("side") or "").upper(),
        "price": order.get("price"),
        "quantity": order.get("quantity", order.get("origQty")),
        "order_id": order.get("order_id", order.get("orderId")),
        "client_order_id": client_id,
        "classification": "DESCONOCIDA",
    }
    if match is not None:
        result.update(grid_id=int(match.group("grid")), level_idx=int(match.group("level")),
                      side=match.group("side"))
    if not is_own_id:
        return result

    order_id = result["order_id"]
    owners: set[int] = set()
    for (grid_id, _level_idx), rows in levels.items():
        if any(
            (order_id is not None and row.get("order_id") is not None
             and str(row.get("order_id")) == str(order_id))
            or client_id in {
                str(row.get("client_order_id") or ""),
                str(row.get("buy_client_order_id") or ""),
            }
            for row in rows
        ):
            owners.add(int(grid_id))
    for loan in (loans or {}).get(client_id, []):
        try:
            owners.add(int(loan["grid_id"]))
        except (KeyError, TypeError, ValueError):
            continue

    if match is not None:
        grid_id = int(match.group("grid"))
        level_idx = int(match.group("level"))
        grid = grids.get(grid_id)
        referenced = grid_id in owners
        same_symbol = bool(grid and str(grid.get("symbol") or "").upper() == symbol)
        if grid and str(grid.get("status") or "").upper() == "ACTIVE" and referenced and same_symbol:
            result["classification"] = "OK"
            return result

    matching_grid_ids = [
        grid_id for grid_id in owners
        if grid_id in grids and str(grids[grid_id].get("symbol") or "").upper() == symbol
    ]
    matching_owners = [grids[grid_id] for grid_id in matching_grid_ids]
    if len(matching_grid_ids) == 1:
        result["grid_id"] = matching_grid_ids[0]
    if any(str(grid.get("status") or "").upper() == "ACTIVE" for grid in matching_owners):
        result["classification"] = "OK"
    elif owners:
        result["classification"] = "HUÉRFANA"
    elif match is not None:
        # Legacy IDs encode their grid even when the local grid row is gone.
        result["classification"] = "HUÉRFANA"
    else:
        result["classification"] = "PROPIA_SIN_REGISTRO"
    return result


def classify_open_orders(
    orders: list[dict[str, Any]],
    grids: dict[int, dict[str, Any]],
    levels: dict[tuple[int, int], list[dict[str, Any]]],
    expected_symbol: str | None = None,
    loans: dict[str, list[dict[str, Any]]] | None = None,
) -> list[dict[str, Any]]:
    """Classify an inventory snapshot without network or database access."""
    return [classify_open_order(order, grids, levels, expected_symbol, loans) for order in orders]


def _readonly_database_path(database_url: str) -> Path:
    from sqlalchemy.engine import make_url

    url = make_url(database_url)
    if url.get_backend_name() != "sqlite" or not url.database or url.database == ":memory:":
        raise ValueError("DATABASE_URL debe apuntar a un archivo SQLite existente.")
    path = Path(url.database)
    if not path.is_absolute():
        path = Path.cwd() / path
    path = path.resolve()
    if not path.is_file():
        raise FileNotFoundError("El archivo SQLite configurado no existe.")
    return path


def _read_database(path: Path) -> tuple[
    dict[int, dict[str, Any]], dict[tuple[int, int], list[dict[str, Any]]], list[str],
    dict[str, list[dict[str, Any]]],
]:
    connection = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
    try:
        connection.execute("PRAGMA query_only = ON")
        grid_rows = connection.execute("SELECT id, symbol, status FROM grids ORDER BY id").fetchall()
        level_rows = connection.execute(
            "SELECT grid_id, level_idx, order_id, client_order_id, buy_client_order_id FROM grid_levels"
        ).fetchall()
        loan_rows: list[dict[str, Any]] = []
        loan_columns = [str(row[1]) for row in connection.execute("PRAGMA table_info(grid_loans)")]
        if {"grid_id", "details"} <= set(loan_columns):
            loan_rows = [dict(zip(loan_columns, row)) for row in connection.execute("SELECT * FROM grid_loans")]
    finally:
        connection.close()

    grids = {
        int(grid_id): {"symbol": str(symbol), "status": str(status)}
        for grid_id, symbol, status in grid_rows
    }
    levels: dict[tuple[int, int], list[dict[str, Any]]] = {}
    for grid_id, level_idx, order_id, client_order_id, buy_client_order_id in level_rows:
        levels.setdefault((int(grid_id), int(level_idx)), []).append({
            "order_id": order_id,
            "client_order_id": client_order_id,
            "buy_client_order_id": buy_client_order_id,
        })
    symbols = sorted({str(symbol).upper() for _, symbol, _ in grid_rows})
    loans: dict[str, list[dict[str, Any]]] = {}
    for row in loan_rows:
        details = row.get("details")
        try:
            details = json.loads(details) if isinstance(details, (str, bytes, bytearray)) else details
        except (json.JSONDecodeError, UnicodeDecodeError, TypeError):
            continue
        if not isinstance(details, dict):
            continue
        for key in ("lender_replacement_cid", "borrower_replacement_cid"):
            client_id = details.get(key)
            if isinstance(client_id, str) and client_id:
                loans.setdefault(client_id, []).append({
                    "grid_id": row.get("grid_id"), "status": row.get("status"),
                })
    return grids, levels, symbols, loans


def _number(value: Any) -> Decimal:
    try:
        return Decimal(str(value or "0"))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal(0)


def _format(value: Any) -> str:
    return str(value if value is not None else "")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only inventory of open Testnet orders.")
    parser.add_argument(
        "--symbols", type=parse_symbols, default=None,
        help="add comma-separated uppercase trading pairs, e.g. ADAUSDT,BTCUSDT",
    )
    args = parser.parse_args(argv)
    try:
        from config.settings import Settings
        from data.testnet_client import TestnetClient

        settings = Settings()
        database_path = _readonly_database_path(settings.database_url)
        grids, levels, symbols, loans = _read_database(database_path)
        symbols = sorted(set(symbols).union(args.symbols or []))
        key = str(settings.testnet_api_key or "").strip()
        secret = str(settings.testnet_api_secret or "").strip()
        if not key or not secret or key.casefold().startswith("tu_") or secret.casefold().startswith("tu_"):
            raise ValueError("Credenciales de Testnet no configuradas.")
        exchange = TestnetClient(key, secret, production_api_key=settings.binance_api_key)
    except Exception as exc:
        print(f"No se pudo preparar el reporte ({_safe_error_label(exc)}).", file=sys.stderr)
        return 2

    rows: list[dict[str, Any]] = []
    for symbol in symbols:
        try:
            orders = exchange.get_open_orders(symbol)
        except Exception as exc:
            print(f"Consulta de órdenes no disponible para {symbol} ({_safe_error_label(exc)}).", file=sys.stderr)
            return 2
        rows.extend(classify_open_orders(orders, grids, levels, symbol, loans))

    print("símbolo | grid | nivel | lado | precio | cantidad | order_id | clasificación")
    for row in rows:
        print(" | ".join((
            _format(row["symbol"]), _format(row["grid_id"]), _format(row["level_idx"]),
            _format(row["side"]), _format(row["price"]), _format(row["quantity"]),
            _format(row["order_id"]), _format(row["classification"]),
        )))

    print("\nTotales por símbolo:")
    for symbol in symbols:
        symbol_rows = [row for row in rows if row["symbol"] == symbol]
        orphaned = [row for row in symbol_rows if row["classification"] == "HUÉRFANA"]
        own_unregistered = [row for row in symbol_rows
                            if row["classification"] == "PROPIA_SIN_REGISTRO"]
        quote_value = sum((_number(row["price"]) * _number(row["quantity"]) for row in orphaned), Decimal(0))
        usdt_value = quote_value if symbol.endswith("USDT") else None
        value_text = f"{usdt_value:.8f} USDT" if usdt_value is not None else "N/D (par no cotizado en USDT)"
        print(f"{symbol}: abiertas={len(symbol_rows)}, OK={sum(r['classification'] == 'OK' for r in symbol_rows)}, "
              f"huérfanas={len(orphaned)}, desconocidas={sum(r['classification'] == 'DESCONOCIDA' for r in symbol_rows)}, "
              f"propias_sin_registro={len(own_unregistered)}, "
              f"valor_huérfanas={value_text}")
        if own_unregistered:
            print(f"AVISO: {symbol} tiene {len(own_unregistered)} orden(es) PROPIA_SIN_REGISTRO; revise manualmente.")
    total_usdt = sum((
        _number(row["price"]) * _number(row["quantity"])
        for row in rows if row["classification"] == "HUÉRFANA" and row["symbol"].endswith("USDT")
    ), Decimal(0))
    print(f"TOTAL huérfanas en pares USDT: {total_usdt:.8f} USDT")
    print("Ninguna clase se cancela automáticamente. Revise manualmente toda orden DESCONOCIDA o PROPIA_SIN_REGISTRO antes de cancelar.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
