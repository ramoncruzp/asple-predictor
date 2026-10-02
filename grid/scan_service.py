"""Bounded, per-symbol isolated market scan service with an in-memory TTL cache."""
from __future__ import annotations

import threading
import time
from datetime import datetime, timezone

import pandas as pd
from decimal import Decimal

from data.exchange_filters import SymbolFilters
from config.settings import SCANNER_DEFAULTS
from grid.scanner import score_symbol

DATA_SOURCE = "Binance public spot market data (read-only)"
EXECUTION_WARNING = "Los precios de ejecución en Testnet difieren del mercado público."


class GridScanService:
    def __init__(self, db, public_client, settings, *, clock=time.monotonic, sleep=time.sleep):
        self.db, self.client, self.settings = db, public_client, settings
        self.clock, self.sleep = clock, sleep
        self._cache: dict[tuple, tuple[float, dict]] = {}
        self._lock = threading.RLock()
        self._last_request_at = 0.0
        self.last_scan: dict | None = None

    def _setting(self, name, default):
        return getattr(self.settings, name, default)

    def _request(self, fn):
        retries = max(0, int(self._setting("scanner_retries", 2)))
        last = None
        for attempt in range(retries + 1):
            wait = float(self._setting("scanner_rate_limit_seconds", .2)) - (self.clock() - self._last_request_at)
            if wait > 0:
                self.sleep(wait)
            try:
                result = fn()
                self._last_request_at = self.clock()
                return result
            except Exception as exc:
                last = exc
                if attempt >= retries:
                    break
                self.sleep(min(2 ** attempt, 2))
        raise RuntimeError(f"market data unavailable: {last}") from last

    def _raw_client(self):
        return getattr(self.client, "client", self.client)

    def _market(self, symbol: str, capital: float, deadline: float) -> tuple[dict, SymbolFilters]:
        def check_deadline():
            if self.clock() >= deadline:
                raise TimeoutError("scan total time limit reached")
        raw = self._raw_client()
        status = self._request(lambda: (self.client.get_symbol_status(symbol)
                         if callable(getattr(self.client, "get_symbol_status", None))
                         else (raw.get_symbol_info(symbol) or {}).get("status")))
        check_deadline()
        stats = self._request(lambda: self.client.get_24h_stats(symbol))
        check_deadline()
        info = self._request(lambda: self.client.get_symbol_info(symbol)
                             if callable(getattr(self.client, "get_symbol_info", None))
                             else raw.get_symbol_info(symbol))
        check_deadline()
        book = self._request(lambda: self.client.get_book_ticker(symbol)
                             if callable(getattr(self.client, "get_book_ticker", None))
                             else raw.get_orderbook_ticker(symbol=symbol))
        bid = Decimal(str(book.get("bid_price", book.get("bidPrice"))))
        ask = Decimal(str(book.get("ask_price", book.get("askPrice"))))
        if bid <= 0 or ask <= 0:
            raise ValueError(f"invalid public bid/ask for {symbol}")
        history_days = int(self._setting("scanner_history_days", 30))
        k5 = self._request(lambda: self.client.get_historical_klines(symbol, "5m", history_days))
        check_deadline()
        k1 = self._request(lambda: self.client.get_historical_klines(symbol, "1h", history_days))
        check_deadline()
        if not isinstance(k5, pd.DataFrame):
            k5 = pd.DataFrame(k5)
        if not isinstance(k1, pd.DataFrame):
            k1 = pd.DataFrame(k1)
        filters = SymbolFilters.from_symbol_info(info)
        market = {"symbol": symbol, "active": True, "quote_asset": info.get("quoteAsset", symbol[-4:]), "status": status,
                  "volume_24h_quote": stats["volume_24h_quote"], "bid": bid, "ask": ask,
                  "klines_5m": k5, "klines_1h": k1, "capital": capital}
        return market, filters

    def scan(self, *, symbols=None, capital=100, strategy="simple", with_sim=False) -> dict:
        active = {str(row["symbol"]).upper().replace("/", "") for row in self.db.get_active_coins()}
        requested = sorted(active if symbols is None else {str(value).upper().replace("/", "") for value in symbols})
        cfg = {"min_volume_24h": self._setting("scanner_min_volume_24h", SCANNER_DEFAULTS["min_volume_24h"]),
               "max_spread_bps": self._setting("scanner_max_spread_bps", SCANNER_DEFAULTS["max_spread_bps"]),
               "min_spacing_pct": self._setting("scanner_min_spacing_pct", .8),
               "fee_pct": self._setting("scanner_fee_pct", .1),
               "min_cell_floor_usdt": self._setting("scanner_min_cell_floor_usdt", 5.5),
               "history_days": self._setting("scanner_history_days", 30),
               "weights": {"cost_headroom": self._setting("scanner_weight_cost_headroom", .35),
                   "liquidity": self._setting("scanner_weight_liquidity", .2),
                   "historical_oscillation": self._setting("scanner_weight_historical_oscillation", .35),
                   "trend_penalty": self._setting("scanner_weight_trend_penalty", .1)}}
        key = (tuple(requested), str(capital), str(strategy), bool(with_sim), tuple(sorted((k, str(v)) for k, v in cfg.items())))
        now = self.clock()
        ttl = max(0, int(self._setting("scanner_cache_ttl_seconds", 300)))
        with self._lock:
            cached = self._cache.get(key)
            if cached and now - cached[0] < ttl:
                return {**cached[1], "cached": True}
        deadline = now + max(.01, float(self._setting("scanner_timeout_seconds", 60)))
        rows = []
        for symbol in requested:
            if symbol not in active:
                rows.append({"symbol": symbol, "error": "símbolo no activo en Coin Registry",
                             "data_source": DATA_SOURCE, "warning": EXECUTION_WARNING})
                continue
            try:
                market, filters = self._market(symbol, float(capital), deadline)
                scored = score_symbol(market, filters, cfg)
                if with_sim and scored["eligible"]:
                    check = getattr(self.client, "get_historical_klines")
                    candles = self._request(lambda: check(symbol, "5m", 90))
                    if not isinstance(candles, pd.DataFrame):
                        candles = pd.DataFrame(candles)
                    from grid.sim.data import CandleData
                    if "timestamp" not in candles or not {"open", "high", "low", "close"}.issubset(candles.columns):
                        raise ValueError("90-day simulation data requires timestamp and OHLC columns")
                    timestamps = pd.to_datetime(candles["timestamp"], utc=True).astype("int64").to_numpy() // 1_000_000_000
                    candles = CandleData(timestamp=timestamps.astype("int64"),
                        open=candles["open"].astype(float).to_numpy(),
                        high=candles["high"].astype(float).to_numpy(),
                        low=candles["low"].astype(float).to_numpy(),
                        close=candles["close"].astype(float).to_numpy(), gaps=0)
                    from grid.sim.runner import run_simulation
                    simulated = run_simulation(candles, strategy=strategy, n=scored["suggested_structure"]["n_levels"],
                        capital=capital, low=scored["suggested_structure"]["range_low"],
                        high=scored["suggested_structure"]["range_high"], fee_pct=cfg["fee_pct"], filters=filters)
                    scored["simulation_90d"] = {"label": "histórico, no predictivo", "metrics": simulated["metrics"]}
                rows.append({**scored, "data_source": DATA_SOURCE, "warning": EXECUTION_WARNING})
            except Exception as exc:
                rows.append({"symbol": symbol, "error": str(exc),
                             "data_source": DATA_SOURCE, "warning": EXECUTION_WARNING})
            if self.clock() >= deadline:
                for remaining in requested[len(rows):]:
                    rows.append({"symbol": remaining, "error": "scan total time limit reached"})
                break
        rows.sort(key=lambda item: (not item.get("eligible", False), -(item.get("score") or 0), item["symbol"]))
        result = {"results": rows, "data_source": DATA_SOURCE,
                  "generated_at": datetime.now(timezone.utc).isoformat(),
                  "warning": EXECUTION_WARNING, "cached": False}
        with self._lock:
            self._cache[key] = (self.clock(), result)
            self.last_scan = result
        return result
