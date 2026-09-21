"""Public and authenticated Binance market-data client."""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, TypeVar

import pandas as pd
from binance.client import Client


logger = logging.getLogger(__name__)
T = TypeVar("T")


class BinanceClient:
    """Small wrapper around python-binance with resilient market-data access."""

    VALID_INTERVALS = {"15m", "1h", "4h", "12h", "1d", "1w"}
    INTERVAL_MINUTES = {"15m": 15, "1h": 60, "4h": 240, "12h": 720, "1d": 1440, "1w": 10080}
    KLINE_COLUMNS = [
        "timestamp", "open", "high", "low", "close", "volume", "close_time",
        "quote_volume", "trades", "taker_buy_base", "taker_buy_quote",
    ]

    def __init__(self, api_key: str | None, api_secret: str | None):
        """Initialize an authenticated or public-only Binance client."""
        key = (api_key or "").strip()
        secret = (api_secret or "").strip()
        self.public_only = not key or not secret
        last_error: Exception | None = None
        for attempt in range(4):
            try:
                self.client = Client(key or None, secret or None)
                break
            except Exception as exc:
                last_error = exc
                if attempt == 3:
                    raise RuntimeError(
                        "Binance no respondió al inicializar el cliente en modo "
                        f"{'público' if self.public_only else 'autenticado'}: {last_error}"
                    ) from last_error
                delay = 2**attempt
                logger.warning(
                    "Binance client initialization failed, retry %d/3 in %ss: %s",
                    attempt + 1, delay, exc,
                )
                time.sleep(delay)

    @staticmethod
    def _binance_symbol(symbol: str) -> str:
        normalized = symbol.strip().upper().replace("/", "")
        if not normalized.endswith("USDT") or len(normalized) <= 4:
            raise ValueError(f"Símbolo no válido: {symbol}. Debe ser un par USDT.")
        return normalized

    @classmethod
    def _validate_interval(cls, interval: str) -> None:
        if interval not in cls.VALID_INTERVALS:
            valid = ", ".join(sorted(cls.VALID_INTERVALS))
            raise ValueError(f"Intervalo no válido: {interval}. Valores permitidos: {valid}")

    @staticmethod
    def _display_symbol(symbol: str) -> str:
        normalized = symbol.upper().replace("/", "")
        return f"{normalized[:-4]}/USDT"

    def _request_with_retries(
        self, operation: Callable[[], T], *, symbol: str, interval: str = "market data"
    ) -> T:
        last_error: Exception | None = None
        for attempt in range(4):
            try:
                return operation()
            except Exception as exc:
                last_error = exc
                if attempt == 3:
                    break
                delay = 2**attempt
                logger.warning(
                    "Binance request failed for %s (%s), retry %d/3 in %ss: %s",
                    symbol, interval, attempt + 1, delay, exc,
                )
                time.sleep(delay)
        raise RuntimeError(
            f"Binance no respondió después de 3 reintentos para {symbol} en {interval}: {last_error}"
        ) from last_error

    def get_historical_klines(
        self, symbol: str, interval: str, lookback_days: int = 90
    ) -> pd.DataFrame:
        """Download historical candles, paging at Binance's 1000-candle limit."""
        binance_symbol = self._binance_symbol(symbol)
        self._validate_interval(interval)
        if lookback_days <= 0:
            raise ValueError("lookback_days debe ser mayor que cero")

        end_time = datetime.now(timezone.utc)
        start_time = end_time - timedelta(days=lookback_days)
        start_ms = int(start_time.timestamp() * 1000)
        end_ms = int(end_time.timestamp() * 1000)
        rows: list[list[Any]] = []
        cursor_ms = start_ms

        while cursor_ms < end_ms:
            page = self._request_with_retries(
                lambda: self.client.get_klines(
                    symbol=binance_symbol, interval=interval,
                    startTime=cursor_ms, endTime=end_ms, limit=1000,
                ),
                symbol=binance_symbol, interval=interval,
            )
            if not page:
                break
            rows.extend(page)
            next_cursor = int(page[-1][0]) + 1
            if next_cursor <= cursor_ms:
                break
            cursor_ms = next_cursor
            if len(page) < 1000:
                break

        frame = self._klines_to_dataframe(rows)
        logger.info(
            "Downloaded %d Binance candles for %s at %s",
            len(frame), self._display_symbol(binance_symbol), interval,
        )
        return frame

    @classmethod
    def _klines_to_dataframe(cls, rows: list[list[Any]]) -> pd.DataFrame:
        if not rows:
            return pd.DataFrame(columns=cls.KLINE_COLUMNS)
        frame = pd.DataFrame([row[:11] for row in rows], columns=cls.KLINE_COLUMNS)
        frame["timestamp"] = pd.to_datetime(frame["timestamp"], unit="ms", utc=True)
        frame["close_time"] = pd.to_datetime(frame["close_time"], unit="ms", utc=True)
        numeric = [column for column in cls.KLINE_COLUMNS if column not in {"timestamp", "close_time"}]
        frame[numeric] = frame[numeric].astype("float64")
        return frame

    def get_current_price(self, symbol: str) -> dict[str, Any]:
        """Return the latest public ticker price."""
        binance_symbol = self._binance_symbol(symbol)
        ticker = self._request_with_retries(
            lambda: self.client.get_symbol_ticker(symbol=binance_symbol),
            symbol=binance_symbol,
        )
        return {
            "symbol": self._display_symbol(binance_symbol),
            "price": float(ticker["price"]),
            "timestamp": datetime.now(timezone.utc),
        }

    def get_supported_symbols(self) -> list[str]:
        """Return currently trading USDT spot pairs."""
        exchange_info = self._request_with_retries(
            self.client.get_exchange_info, symbol="USDT pairs"
        )
        return sorted(
            f"{item['baseAsset']}/USDT"
            for item in exchange_info.get("symbols", [])
            if item.get("quoteAsset") == "USDT" and item.get("status") == "TRADING"
        )
