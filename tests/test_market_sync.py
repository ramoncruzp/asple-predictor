from datetime import datetime, timezone

import pandas as pd

from data.market_store import CandleStore
from data.market_sync import MarketSyncJob


class FakeClient:
    def __init__(self, frames=None):
        self.frames = frames or {}
        self.calls = []

    def get_historical_klines(self, symbol, interval, lookback, **kwargs):
        self.calls.append((symbol, kwargs))
        if symbol == "BADUSDT":
            raise RuntimeError("offline fixture")
        return self.frames.get(symbol, pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume", "quote_volume", "trades"]))


def _frame(stamps):
    return pd.DataFrame([{"timestamp": datetime.fromtimestamp(ts, timezone.utc), "open": 100,
        "high": 101, "low": 99, "close": 100, "volume": 1, "quote_volume": 100, "trades": 1}
        for ts in stamps])


def test_sync_recupera_hueco_y_omite_vela_actual_y_continua_errores(tmp_path):
    store = CandleStore(tmp_path / "market_data.db")
    now = datetime(2026, 1, 1, 3, 3, tzinfo=timezone.utc)
    end = int(now.timestamp()) // 300 * 300 - 300
    begin = end - 3 * 3600 + 300
    store.upsert("GOODUSDT", "5m", [{"open_ts": begin - 300, "open":100,"high":101,"low":99,"close":100}], "fixture")
    store.upsert("BADUSDT", "5m", [{"open_ts": begin - 300, "open":100,"high":101,"low":99,"close":100}], "fixture")
    good = [begin + i*300 for i in range(37)]
    client = FakeClient({"GOODUSDT": _frame(good)})
    job = MarketSyncJob(store, client, clock=lambda: now)
    result = job.run_once()
    assert result["candles"] == len(good) - 1
    assert result["errors"] == 1
    assert store.sync_position("GOODUSDT") == good[-2]
    assert store.sync_position("GOODUSDT") <= end
    assert "offline fixture" in store.coverage("BADUSDT")["sync_state"]["last_error"]


def test_job_registra_cron_minuto_dos_y_limites():
    class Scheduler:
        def add_job(self, fn, trigger, **kwargs):
            self.job = (fn, trigger, kwargs)
        def start(self):
            self.started = True
    scheduler = Scheduler()
    MarketSyncJob(None, None, scheduler).start()
    assert scheduler.job[1] == "cron"
    assert scheduler.job[2]["minute"] == 2
    assert scheduler.job[2]["max_instances"] == 1
    assert scheduler.job[2]["coalesce"] is True
