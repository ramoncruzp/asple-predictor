import sqlite3
from datetime import datetime, timezone

from data.market_store import CandleStore
from scripts.verificar_velas import compare_api, inspect_db


def test_verificador_lectura_y_comparacion_sintetica(tmp_path):
    path = tmp_path / "market_data.db"
    store = CandleStore(path)
    last = int(datetime.now(timezone.utc).timestamp()) // 300 * 300 - 300
    stamps = [last - 600 + index*300 for index in range(3)]
    rows = [{"open_ts": ts, "open":100, "high":101, "low":99, "close":100,
        "volume":1, "quote_volume":100, "trades":1} for ts in stamps]
    store.upsert("XRPUSDT", "5m", rows, "fixture")
    before = path.read_bytes()
    report = inspect_db(path)[0]
    assert report["symbol"] == "XRPUSDT" and report["candles"] == 3
    class Client:
        def get_historical_klines(self, *args, **kwargs):
            import pandas as pd
            return pd.DataFrame([{"timestamp": ts, "open":100, "high":101, "low":99,
                "close":100, "volume":1, "quote_volume":100, "trades":1} for ts in stamps])
    assert compare_api(path, 7, Client()) == {"XRPUSDT": 0}
    assert path.read_bytes() == before
