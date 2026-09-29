"""Explicit Binance spot-testnet client with guarded, duplicate-safe order retries."""

from __future__ import annotations

import time
import uuid
from math import isfinite
from typing import Any, Callable, TypeVar

from binance.client import Client
from binance.exceptions import (
    BinanceAPIException,
    BinanceOrderException,
    BinanceRequestException,
)
from requests.exceptions import RequestException

from data.binance_client import BinanceClient


TESTNET_API_URL = "https://testnet.binance.vision/api"
_RETRY_DELAYS = (1, 2, 4)
_RETRYABLE_HTTP_STATUS = {418, 429}
T = TypeVar("T")


class TestnetOrderError(Exception):
    """A non-retryable order or account error returned by the testnet API."""

    def __init__(self, code: int | str | None, message: str):
        self.code = code
        self.message = str(message)
        super().__init__(f"Testnet API error {code}: {self.message}")


class TestnetClient:
    """Small explicit client for Binance spot testnet; never starts at app startup."""

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        *,
        production_api_key: str | None = None,
        client: Any | None = None,
    ):
        key = (api_key or "").strip()
        secret = (api_secret or "").strip()
        if not key or not secret:
            raise ValueError("Testnet API key and secret must both be configured.")
        if key.casefold().startswith("tu_") or secret.casefold().startswith("tu_"):
            raise ValueError("Testnet API key and secret cannot be placeholders.")
        production_key = (production_api_key or "").strip()
        if production_key and key == production_key:
            raise ValueError("Testnet API key must differ from the production API key.")

        self.client = client if client is not None else Client(key, secret, testnet=True)
        # python-binance never rewrites Client.API_URL for testnet=True; it only
        # swaps the URL per-request inside _create_api_uri() based on the
        # boolean `testnet` attribute. That attribute is the only reliable signal.
        if getattr(self.client, "testnet", None) is not True:
            raise ValueError("Client must be configured with testnet=True (spot testnet).")

    def __repr__(self) -> str:
        return "<TestnetClient testnet=True>"

    @staticmethod
    def _symbol(symbol: str) -> str:
        if not isinstance(symbol, str):
            raise ValueError("symbol must be a USDT pair.")
        return BinanceClient._binance_symbol(symbol)

    @staticmethod
    def _retryable(error: Exception) -> bool:
        if isinstance(error, (BinanceRequestException, RequestException)):
            return True
        return isinstance(error, BinanceAPIException) and (
            error.status_code >= 500 or error.status_code in _RETRYABLE_HTTP_STATUS
        )

    @staticmethod
    def _business_error(error: Exception) -> TestnetOrderError:
        return TestnetOrderError(
            getattr(error, "code", None),
            getattr(error, "message", str(error)),
        )

    @staticmethod
    def _normalize_order(order: dict[str, Any]) -> dict[str, Any]:
        return {
            "order_id": order.get("orderId"),
            "client_order_id": order.get("clientOrderId"),
            "symbol": str(order.get("symbol", "")),
            "side": str(order.get("side", "")),
            "type": str(order.get("type", "")),
            "status": str(order.get("status", "")),
            "price": float(order.get("price") or 0.0),
            "quantity": float(order.get("origQty") or order.get("quantity") or 0.0),
            "executed_qty": float(order.get("executedQty") or 0.0),
        }

    @staticmethod
    def _normalize_order_list(orders: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [TestnetClient._normalize_order(order) for order in orders]

    def _run_read(self, operation: Callable[[], T]) -> T:
        last_error: Exception | None = None
        for attempt in range(len(_RETRY_DELAYS) + 1):
            try:
                return operation()
            except Exception as error:
                if not self._retryable(error):
                    if isinstance(error, (BinanceAPIException, BinanceOrderException)):
                        raise self._business_error(error) from error
                    raise
                last_error = error
                if attempt < len(_RETRY_DELAYS):
                    time.sleep(_RETRY_DELAYS[attempt])
        raise RuntimeError(f"Testnet request failed after 3 retries: {last_error}") from last_error

    def _find_by_client_order_id(self, symbol: str, client_order_id: str) -> dict[str, Any] | None:
        try:
            order = self.client.get_order(
                symbol=symbol,
                origClientOrderId=client_order_id,
            )
            return self._normalize_order(order) if order else None
        except BinanceAPIException as error:
            if error.code == -2013:
                return None
            raise

    def place_order(
        self,
        symbol: str,
        side: str,
        quantity: float,
        price: float | None = None,
        order_type: str = "LIMIT",
        client_order_id: str | None = None,
    ) -> dict[str, Any]:
        normalized_symbol = self._symbol(symbol)
        normalized_side = str(side).upper()
        normalized_type = str(order_type).upper()
        if normalized_side not in {"BUY", "SELL"}:
            raise ValueError("side must be BUY or SELL.")
        if normalized_type not in {"LIMIT", "MARKET"}:
            raise ValueError("order_type must be LIMIT or MARKET.")
        try:
            normalized_quantity = float(quantity)
        except (TypeError, ValueError) as error:
            raise ValueError("quantity must be greater than zero.") from error
        if not isfinite(normalized_quantity) or normalized_quantity <= 0:
            raise ValueError("quantity must be greater than zero.")
        if normalized_type == "LIMIT" and price is None:
            raise ValueError("price is required for LIMIT orders.")
        normalized_price = None
        if normalized_type == "LIMIT":
            try:
                normalized_price = float(price)  # type: ignore[arg-type]
            except (TypeError, ValueError) as error:
                raise ValueError("price must be a positive number for LIMIT orders.") from error
            if not isfinite(normalized_price) or normalized_price <= 0:
                raise ValueError("price must be a positive number for LIMIT orders.")

        order_client_id = client_order_id or uuid.uuid4().hex
        params: dict[str, Any] = {
            "symbol": normalized_symbol,
            "side": normalized_side,
            "type": normalized_type,
            "quantity": normalized_quantity,
            "newClientOrderId": order_client_id,
        }
        if normalized_type == "LIMIT":
            params.update({"timeInForce": "GTC", "price": normalized_price})

        last_error: Exception | None = None
        for attempt in range(len(_RETRY_DELAYS) + 1):
            if attempt:
                time.sleep(_RETRY_DELAYS[attempt - 1])
                try:
                    existing = self._find_by_client_order_id(normalized_symbol, order_client_id)
                except Exception as error:
                    if not self._retryable(error):
                        if isinstance(error, (BinanceAPIException, BinanceOrderException)):
                            raise self._business_error(error) from error
                        raise
                    last_error = error
                    continue
                if existing is not None:
                    return existing
            try:
                return self._normalize_order(self.client.create_order(**params))
            except Exception as error:
                if not self._retryable(error):
                    if isinstance(error, (BinanceAPIException, BinanceOrderException)):
                        raise self._business_error(error) from error
                    raise
                last_error = error
        raise RuntimeError(f"Testnet order failed after 3 retries: {last_error}") from last_error

    def cancel_order(self, symbol: str, order_id: int | str) -> dict[str, Any]:
        normalized_symbol = self._symbol(symbol)
        last_error: Exception | None = None
        for attempt in range(len(_RETRY_DELAYS) + 1):
            if attempt:
                time.sleep(_RETRY_DELAYS[attempt - 1])
            try:
                result = self.client.cancel_order(symbol=normalized_symbol, orderId=order_id)
                return self._normalize_order(result)
            except BinanceAPIException as error:
                if error.code == -2011 and attempt > 0:
                    try:
                        actual = self.client.get_order(symbol=normalized_symbol, orderId=order_id)
                        if actual:
                            return self._normalize_order(actual)
                    except BinanceAPIException as status_error:
                        if self._retryable(status_error):
                            last_error = status_error
                            continue
                        if status_error.code != -2013:
                            raise self._business_error(status_error) from status_error
                    except Exception as status_error:
                        if not self._retryable(status_error):
                            raise
                        last_error = status_error
                        continue
                    raise self._business_error(error) from error
                if not self._retryable(error):
                    raise self._business_error(error) from error
                last_error = error
            except Exception as error:
                if not self._retryable(error):
                    raise
                last_error = error
        raise RuntimeError(f"Testnet cancel failed after 3 retries: {last_error}") from last_error

    def get_open_orders(self, symbol: str | None = None) -> list[dict[str, Any]]:
        normalized_symbol = self._symbol(symbol) if symbol is not None else None
        orders = self._run_read(
            lambda: self.client.get_open_orders(**({"symbol": normalized_symbol} if normalized_symbol else {}))
        )
        return self._normalize_order_list(orders)

    def get_balance(self, asset: str | None = None) -> dict[str, dict[str, float]]:
        normalized_asset = asset.strip().upper() if asset is not None else None
        account = self._run_read(self.client.get_account)
        balances: dict[str, dict[str, float]] = {}
        for balance in account.get("balances", []):
            name = str(balance.get("asset", ""))
            if normalized_asset is not None and name != normalized_asset:
                continue
            free = float(balance.get("free") or 0.0)
            locked = float(balance.get("locked") or 0.0)
            if free == 0.0 and locked == 0.0:
                continue
            balances[name] = {"free": free, "locked": locked}
        return balances
