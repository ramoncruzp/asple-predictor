import numpy as np

from data.market_store import CandleStore
from research.scenarios.evaluate import evaluate_pieces


def test_evaluate_pieces_uses_temporary_market_and_synthetic_library(tmp_path):
    db = tmp_path / "market_data_synthetic.sqlite"
    store = CandleStore(db)
    rows = []
    previous = 100.0
    for index in range(50 * 288):
        close = 100 + .5 * np.sin(index / 37)
        rows.append({"open_ts": index * 300, "open": previous,
            "high": max(previous, close) + .01, "low": min(previous, close) - .01,
            "close": close, "volume": 1, "quote_volume": 100, "trades": 1})
        previous = close
    store.upsert("SYNTHUSDT", "5m", rows, "test")
    store.save_symbol_filters("SYNTHUSDT", {"filters": [
        {"filterType": "PRICE_FILTER", "minPrice": "0.0001", "maxPrice": "100000", "tickSize": "0.0001"},
        {"filterType": "LOT_SIZE", "minQty": "0.0001", "maxQty": "100000000", "stepSize": "0.0001"},
        {"filterType": "MIN_NOTIONAL", "minNotional": "0.01", "applyToMarket": True},
    ]})
    library = tmp_path / "library"
    library.mkdir()
    values = np.zeros((50, 288, 3), dtype=np.float32)
    values[:, :, 1], values[:, :, 2] = .2, -.2
    dates = np.arange(1_700_000_000 // 86400, 1_700_000_000 // 86400 + 50,
        dtype=np.int64) * 86400
    np.savez_compressed(library / "SYNTHUSDT.npz", values=values, dates=dates,
        daily_z_return_sum=np.zeros(50), mean_z_return=np.float32(0))
    result = evaluate_pieces([
        {"symbol": "SYNTHUSDT", "strategy": "simple", "capital": 40,
            "low": 70, "high": 130, "n": 6},
        {"symbol": "SYNTHUSDT", "strategy": "smart", "capital": 40,
            "low": 70, "high": 130, "n": 6}], perfil="RAPIDO",
        capital=100, meta=3, n_scenarios=2, n_smart=1, seed=43,
        db_path=db, library_dir=library, workers=1)
    assert result["n_scenarios"] == 1
    assert result["pieces"][0]["metrics"]["n"] == 1
    assert result["portfolio"]["capital_unused_usdt"] == 20
    assert len(result["smart_paired"]) == 1
