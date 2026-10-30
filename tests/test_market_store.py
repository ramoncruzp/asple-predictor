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
