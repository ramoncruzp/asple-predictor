"""Integration tests for public Binance market data."""

from data.binance_client import BinanceClient


def test_public_binance_xrp_4h_data() -> None:
    client = BinanceClient("", "")
    frame = client.get_historical_klines("XRP/USDT", "4h", lookback_days=7)

    expected_columns = [
        "timestamp", "open", "high", "low", "close", "volume",
        "close_time", "quote_volume", "trades", "taker_buy_base", "taker_buy_quote",
    ]
    assert list(frame.columns) == expected_columns
    assert len(frame) >= 40
