import math
import sqlite3

import numpy as np

from data.market_store import CandleStore
from grid.sim.data import CandleData


def candle(open_ts, open_price=100, high=102, low=99, close=101,
           volume=1, quote_volume=100, trades=2):
    return {"open_ts": open_ts, "open": open_price, "high": high, "low": low,
        "close": close, "volume": volume, "quote_volume": quote_volume, "trades": trades}


def test_upsert_validates_ohlc_and_replaces_idempotently(tmp_path):
    store = CandleStore(tmp_path / "market.db")
    rows = [candle(0), candle(300, 101, 103, 100, 102),
        candle(600, 100, 99, 101, 102),
        candle(901), candle(1200, close=math.inf)]
    first = store.upsert("xrp/usdt", "5m", rows, "test", role="datos")
    second = store.upsert("XRPUSDT", "5m", rows[:2], "test", role="datos")
    assert first["accepted"] == second["accepted"] == 2
    assert first["rejected"] == 3
    coverage = store.coverage("XRPUSDT")
    assert coverage["role"] == "datos"
    assert coverage["candles"] == 2
    assert coverage["first_ts"] == 0 and coverage["last_ts"] == 300
    assert coverage["last_candle"]["close"] == 102
    with sqlite3.connect(store.path) as conn:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"


def test_upsert_y_registro_sin_papel_conservan_role_explicito(tmp_path):
    store = CandleStore(tmp_path / "roles.sqlite")
    store.upsert("XRPUSDT", "5m", [candle(0)], "fixture", role="datos")
    store.upsert("XRPUSDT", "5m", [candle(300)], "sync")
    store.register_symbol("XRPUSDT")
    assert store.symbol_role("XRPUSDT") == "datos"
    store.register_symbol("XRPUSDT", role="operar")
    assert store.symbol_role("XRPUSDT") == "operar"


def test_load_returns_simulator_candledata_and_counts_missing_bars(tmp_path):
    store = CandleStore(tmp_path / "market.db")
    store.upsert("AAAUSDT", "5m", [candle(0), candle(300, 101, 103, 100, 102),
        candle(900, 102, 104, 101, 103)], "fixture")
    data = store.load("AAAUSDT", start_ts=300, end_ts=900)
    assert isinstance(data, CandleData)
    assert data.timestamp.tolist() == [300, 900]
    assert data.open.tolist() == [101, 102]
    assert data.gaps == 1
    missing = store.load("MISSINGUSDT")
    assert isinstance(missing, CandleData) and len(missing.timestamp) == 0


def test_load_hourly_aggregates_only_complete_hours_with_volume_totals(tmp_path):
    store = CandleStore(tmp_path / "market.db")
    rows = [candle(index * 300, 100 + index, 102 + index, 99 + index,
        101 + index, index + 1, (index + 1) * 10, index) for index in range(12)]
    store.upsert("AAAUSDT", "5m", rows, "fixture")
    hourly = store.load_hourly("AAAUSDT")
    assert hourly.timestamp.tolist() == [0]
    assert hourly.open.tolist() == [100]
    assert hourly.high.tolist() == [113]
    assert hourly.low.tolist() == [99]
    assert hourly.close.tolist() == [112]
    assert hourly.volume.tolist() == [78]
    assert hourly.quote_volume.tolist() == [780]
    assert hourly.trades.tolist() == [66]
    assert hourly.gaps == 0
    assert np.isfinite(hourly.close).all()


def test_gap_is_recorded_and_not_filled_and_coverage_reports_it(tmp_path):
    store = CandleStore(tmp_path / "market.db")
    summary = store.upsert("AAAUSDT", "5m", [candle(0), candle(300), candle(900)], "fixture")
    data = store.load("AAAUSDT")
    coverage = store.coverage("AAAUSDT")
    assert summary["gaps_detected"] == 1
    assert data.timestamp.tolist() == [0, 300, 900]
    assert data.gaps == coverage["gaps"] == 1
    assert coverage["gap_ranges"] == coverage["largest_gap"] == 1
    assert coverage["sync_state"]["last_source"] == "fixture"


def test_symbol_filters_round_trip_in_temporary_market_db(tmp_path):
    store = CandleStore(tmp_path / "filters.sqlite")
    info = {"symbol": "JUPUSDT", "filters": [
        {"filterType": "PRICE_FILTER", "minPrice": "0.0001", "maxPrice": "1000", "tickSize": "0.0001"},
        {"filterType": "LOT_SIZE", "minQty": "0.1", "maxQty": "1000000", "stepSize": "0.1"},
        {"filterType": "MIN_NOTIONAL", "minNotional": "5", "applyToMarket": True},
    ]}
    assert store.filters("JUPUSDT") is None
    saved = store.save_symbol_filters("JUP/USDT", info, fetched_at="2026-10-30T00:00:00+00:00")
    loaded = store.filters("JUPUSDT")
    assert loaded == saved
    assert str(loaded.tick_size) == "0.0001"
