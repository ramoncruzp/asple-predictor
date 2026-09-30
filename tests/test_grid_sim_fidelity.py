import numpy as np

from grid.sim.data import CandleData
from grid.sim.runner import run_simulation
from tests.test_grid_engine import create, make_engine


def test_short_replay_matches_grid_engine_cycle_with_exchange_adapter():
    close = np.array([100., 100., 100., 100., 102.1, 100., 100.])
    low = np.array([99.9, 97., 99.9, 99.9, 99.9, 99.9, 99.9])
    high = np.array([100.1, 100.1, 100.1, 100.1, 103., 100.1, 100.1])
    ts = np.arange(len(close), dtype=np.int64) * 300 + 1_700_000_100
    candles = CandleData(ts, close, high, low, close, 0)
    sim = run_simulation(candles, n=5, capital=1000, low=90, high=110,
                         fee_pct=0, resync_candles=3)

    engine, db, exchange = make_engine(fee_rate="0", fee_asset="USDT")
    grid = create(engine)
    buy = next(row for row in db.get_grid_levels(grid["id"]) if row["level_idx"] == 2)
    # Candle adapter: the production FakeExchange has no OHLC method, so fill
    # only when the same strict low/high penetration rule is observed.
    for candle_idx in range(len(close)):
        if candle_idx == 1 and low[candle_idx] < float(buy["price"]):
            exchange.fill(buy["order_id"])
        if candle_idx == 4:
            level = db.get_grid_levels(grid["id"])[2]
            sell = exchange.get_order("XRPUSDT", order_id=level["order_id"])
            if high[candle_idx] > float(sell["price"]):
                exchange.fill(sell["order_id"])
        if candle_idx % 3 == 0:
            engine.sync_grid(grid["id"])
    real_level = db.get_grid_levels(grid["id"])[2]
    sim_cell = sim["metrics"]["cycles_completed"]
    # Adjacent cells at 90/94/98/102/106 differ in index mapping: assert the
    # actual filled grid cell and aggregate cycle count.
    assert sim_cell == 1
    assert real_level["cycles_completed"] == 1
    assert abs(float(real_level["pnl"]) - float(sim["metrics"]["pnl_realized_net_usdt"])) < .02
