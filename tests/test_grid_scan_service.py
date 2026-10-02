from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pandas as pd

from database.db_manager import DBManager
from grid.scan_service import GridScanService
from tests.grid_fakes import fake_symbol_info


def candles(interval):
    count = 30 * 24 * (12 if interval == "5m" else 1)
    i = np.arange(count)
    close = 100 + 8 * np.sin(i / (55 if interval == "5m" else 4))
    stamps = pd.date_range("2026-01-01", periods=count,
        freq="5min" if interval == "5m" else "1h", tz="UTC")
    return pd.DataFrame({"timestamp": stamps, "open": close, "high": close,
                         "low": close, "close": close})


class Client:
    def __init__(self):
        self.calls = 0
        self.fail = set()

    def get_symbol_status(self, symbol):
        self.calls += 1
        if symbol in self.fail:
            raise RuntimeError("public endpoint unavailable")
        return "TRADING"

    def get_24h_stats(self, symbol):
        return {"volume_24h_quote": 5_000_000}

    def get_symbol_info(self, symbol):
        return fake_symbol_info()

    def get_book_ticker(self, symbol):
        return {"bid_price": 99.99, "ask_price": 100.01}

    def get_historical_klines(self, symbol, interval, lookback_days):
        return candles(interval)


def setup(tmp_path, client=None, **overrides):
    db = DBManager(f"sqlite:///{tmp_path}/scan.db")
    db.add_or_reactivate_coin("XRPUSDT")
    client = client or Client()
    values = dict(scanner_min_volume_24h=1_000_000, scanner_max_spread_bps=15,
        scanner_min_spacing_pct=.8, scanner_fee_pct=.1, scanner_history_days=30,
        scanner_cache_ttl_seconds=300, scanner_rate_limit_seconds=0,
        scanner_retries=0, scanner_timeout_seconds=60)
    values.update(overrides)
    settings = SimpleNamespace(**values)
    return GridScanService(db, client, settings, sleep=lambda _: None), client


def test_one_symbol_failure_does_not_abort_scan_and_cache_honors_ttl(tmp_path):
    client = Client()
    service, _ = setup(tmp_path, client)
    client.fail.add("ETHUSDT")
    # ETH has no active registry row, so explicitly activate it to exercise isolation.
    service.db.add_or_reactivate_coin("ETHUSDT")
    first = service.scan()
    assert {row["symbol"] for row in first["results"]} == {"XRPUSDT", "ETHUSDT"}
    assert any("error" in row for row in first["results"])
    count = client.calls
    cached = service.scan()
    assert cached["cached"] is True
    assert client.calls == count


def test_timeout_and_missing_symbol_data_are_reported_per_symbol(tmp_path):
    ticks = [0.0]
    client = Client()
    original = client.get_historical_klines

    def slow(symbol, interval, days):
        ticks[0] += 100
        return original(symbol, interval, days)

    client.get_historical_klines = slow
    service, _ = setup(tmp_path, client, scanner_timeout_seconds=10)
    service.clock = lambda: ticks[0]
    row = service.scan()["results"][0]
    assert "error" in row and "time limit" in row["error"]

    client.get_symbol_status = lambda symbol: (_ for _ in ()).throw(RuntimeError("no market data"))
    service._cache.clear()
    row = service.scan()["results"][0]
    assert "error" in row and "market data unavailable" in row["error"]


def test_real_binance_client_shape_normalizes_string_book_prices(tmp_path):
    from decimal import Decimal

    class Raw:
        def get_symbol_info(self, symbol):
            return {"symbol": symbol, "status": "TRADING", "quoteAsset": "USDT", "filters": [
                {"filterType": "PRICE_FILTER", "minPrice": "0.00000001", "maxPrice": "1", "tickSize": "0.00000001"},
                {"filterType": "LOT_SIZE", "minQty": "1", "maxQty": "10000000000", "stepSize": "1"},
                {"filterType": "NOTIONAL", "minNotional": "5", "applyMinToMarket": True},
                {"filterType": "MAX_NUM_ORDERS", "maxNumOrders": 200}]}
        def get_orderbook_ticker(self, symbol):
            return {"bidPrice": "0.00001000", "askPrice": "0.00001002"}

    class PublicClient:
        def __init__(self): self.client = Raw()
        def get_symbol_status(self, symbol): return "TRADING"
        def get_24h_stats(self, symbol): return {"volume_24h_quote": 5_000_000}
        def get_historical_klines(self, symbol, interval, days): return candles(interval)

    service, _ = setup(tmp_path, PublicClient(), scanner_timeout_seconds=60)
    market, _ = service._market("XRPUSDT", 1000, service.clock() + 60)
    assert isinstance(market["bid"], Decimal) and isinstance(market["ask"], Decimal)
    assert market["bid"] == Decimal("0.00001000")
    assert market["ask"] == Decimal("0.00001002")


def test_with_sim_is_labeled_historical_not_predictive(tmp_path):
    service, _ = setup(tmp_path)
    result = service.scan(capital=1000, with_sim=True)
    row = result["results"][0]
    assert "simulation_90d" in row, row
    assert row["simulation_90d"]["label"] == "histórico, no predictivo"
    assert row["data_source"].startswith("Binance public")
