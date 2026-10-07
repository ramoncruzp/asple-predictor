from decimal import Decimal

from grid.guards import sell_level_conflicts
from tests.test_grid_engine import make_engine


def test_sell_level_conflicts_uses_tolerance_tick_and_ignores_done():
    levels = [{"sell_price": "100", "state": "SELL_OPEN"},
              {"sell_price": "110", "state": "DONE"}]
    assert sell_level_conflicts(["100.04"], levels, "0.01", "0.05")
    assert not sell_level_conflicts(["100.06"], levels, "0.01", "0.05")
    assert sell_level_conflicts(["100.01"], levels, "0.02", "0")


def test_engine_allows_same_coin_sell_distinct_but_rejects_holding_overlap():
    engine, db, _exchange = make_engine(fee_rate="0", fee_asset="USDT")
    first = engine.create_grid("XRPUSDT", 90, 110, 5, capital=1000)
    engine.exchange.bid = engine.exchange.avg = engine.exchange.ask = Decimal("125")
    second = engine.create_grid("XRPUSDT", 115, 135, 5, capital=1000, strategy="smart",
                                params={"adjust_enabled": False})
    assert first["id"] != second["id"]
    db.update_grid(first["id"], status="HOLDING")
    try:
        engine.create_grid("XRPUSDT", 115, 135, 5, capital=1000, strategy="smart",
                           params={"adjust_enabled": False})
    except Exception as exc:
        assert "sell level conflict" in str(exc)
    else:
        raise AssertionError("HOLDING SELL collision must be rejected")


def test_injected_engine_provider_blocks_smart_without_sigma():
    engine, _db, _exchange = make_engine(fee_rate="0", fee_asset="USDT")
    engine.vol_provider = type("NoSigma", (), {"get": lambda self, symbol, horizon_h=24: None})()
    try:
        engine.create_grid("XRPUSDT", 90, 110, 5, capital=1000, strategy="smart",
                           params={"adjust_enabled": False})
    except Exception as exc:
        assert "sigma disponible" in str(exc)
    else:
        raise AssertionError("smart grid without sigma must be rejected")
