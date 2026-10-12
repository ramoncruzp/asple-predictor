"""Read-only inventory of open exchange orders and their local grid owners."""

from __future__ import annotations

import re
import sqlite3
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any


_SYSTEM_CLIENT_ID = re.compile(r"g(?P<grid>\d+)L(?P<level>\d+)(?P<side>[BS])(?P<cycle>\d+)")


def classify_open_order(
    order: dict[str, Any],
    grids: dict[int, dict[str, Any]],
    levels: dict[tuple[int, int], list[dict[str, Any]]],
    expected_symbol: str | None = None,
) -> dict[str, Any]:
    """Classify one normalized open order using only supplied local snapshots."""
    client_id = str(order.get("client_order_id") or order.get("clientOrderId") or "")
    match = _SYSTEM_CLIENT_ID.fullmatch(client_id)
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
    if match is None:
        return result

    grid_id = int(match.group("grid"))
    level_idx = int(match.group("level"))
    result.update(grid_id=grid_id, level_idx=level_idx, side=match.group("side"))
    grid = grids.get(grid_id)
    candidates = levels.get((grid_id, level_idx), [])
    order_id = result["order_id"]
    referenced = any(
        (order_id is not None and row.get("order_id") is not None
         and str(row.get("order_id")) == str(order_id))
        or client_id in {
            str(row.get("client_order_id") or ""),
            str(row.get("buy_client_order_id") or ""),
        }
        for row in candidates
    )
    same_symbol = bool(grid and str(grid.get("symbol") or "").upper() == symbol)
    if grid and str(grid.get("status") or "").upper() == "ACTIVE" and referenced and same_symbol:
        result["classification"] = "OK"
    else:
        result["classification"] = "HUÉRFANA"
    return result


def classify_open_orders(
    orders: list[dict[str, Any]],
    grids: dict[int, dict[str, Any]],
    levels: dict[tuple[int, int], list[dict[str, Any]]],
    expected_symbol: str | None = None,
) -> list[dict[str, Any]]:
    """Classify an inventory snapshot without network or database access."""
    return [classify_open_order(order, grids, levels, expected_symbol) for order in orders]


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


def _read_database(path: Path) -> tuple[dict[int, dict[str, Any]], dict[tuple[int, int], list[dict[str, Any]]], list[str]]:
    connection = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
    try:
        connection.execute("PRAGMA query_only = ON")
        grid_rows = connection.execute("SELECT id, symbol, status FROM grids ORDER BY id").fetchall()
        level_rows = connection.execute(
            "SELECT grid_id, level_idx, order_id, client_order_id, buy_client_order_id FROM grid_levels"
        ).fetchall()
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
    return grids, levels, symbols


def _number(value: Any) -> Decimal:
    try:
        return Decimal(str(value or "0"))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal(0)


def _format(value: Any) -> str:
    return str(value if value is not None else "")


def main() -> int:
    try:
        from config.settings import Settings
        from data.testnet_client import TestnetClient

        settings = Settings()
        database_path = _readonly_database_path(settings.database_url)
        grids, levels, symbols = _read_database(database_path)
        key = str(settings.testnet_api_key or "").strip()
        secret = str(settings.testnet_api_secret or "").strip()
        if not key or not secret or key.casefold().startswith("tu_") or secret.casefold().startswith("tu_"):
            raise ValueError("Credenciales de Testnet no configuradas.")
        exchange = TestnetClient(key, secret, production_api_key=settings.binance_api_key)
    except Exception as exc:
        print(f"No se pudo preparar el reporte ({type(exc).__name__}).", file=sys.stderr)
        return 2

    rows: list[dict[str, Any]] = []
    for symbol in symbols:
        try:
            orders = exchange.get_open_orders(symbol)
        except Exception as exc:
            print(f"Consulta de órdenes no disponible para {symbol} ({type(exc).__name__}).", file=sys.stderr)
            return 2
        rows.extend(classify_open_orders(orders, grids, levels, symbol))

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
        quote_value = sum((_number(row["price"]) * _number(row["quantity"]) for row in orphaned), Decimal(0))
        usdt_value = quote_value if symbol.endswith("USDT") else None
        value_text = f"{usdt_value:.8f} USDT" if usdt_value is not None else "N/D (par no cotizado en USDT)"
        print(f"{symbol}: abiertas={len(symbol_rows)}, OK={sum(r['classification'] == 'OK' for r in symbol_rows)}, "
              f"huérfanas={len(orphaned)}, desconocidas={sum(r['classification'] == 'DESCONOCIDA' for r in symbol_rows)}, "
              f"valor_huérfanas={value_text}")
    total_usdt = sum((
        _number(row["price"]) * _number(row["quantity"])
        for row in rows if row["classification"] == "HUÉRFANA" and row["symbol"].endswith("USDT")
    ), Decimal(0))
    print(f"TOTAL huérfanas en pares USDT: {total_usdt:.8f} USDT")
    print("Este reporte no cancela órdenes. La limpieza requiere una fase aparte y confirmación de Ramón.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
