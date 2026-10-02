from decimal import Decimal
from types import SimpleNamespace

import pytest
from database.db_manager import DBManager
from grid.engine import GridEngine
from grid.policy import plan_dust_sweep
from grid.sim.exchange import SimExchange
from tests.grid_fakes import FakeExchange


def _filters(exchange):
    return exchange.filters


def test_plan_dust_sweep_obeys_market_minima_and_preserves_step_residual():
    exchange = FakeExchange()
    filters = _filters(exchange)
    assert not plan_dust_sweep("0.09", "100", filters, "0.1")["sweepable"]
    assert not plan_dust_sweep("0.1", "10", filters, "0.1")["sweepable"]
    plan = plan_dust_sweep("10.05", "100", filters, "0.1")
    assert plan == {"qty": Decimal("10.0"), "sweepable": True,
                    "proceeds_net": Decimal("999.000"), "residual": Decimal("0.05")}


def test_grid_dust_migration_defaults_and_ledger_are_idempotent():
    db = DBManager("sqlite:///:memory:")
    db.add_or_reactivate_coin("XRPUSDT")
    grid = db.create_grid_with_levels({"symbol": "XRPUSDT", "range_low": 90,
        "range_high": 110, "n_levels": 2, "capital_total": 100,
        "status": "ACTIVE", "environment": "testnet"}, [
            {"level_idx": i, "price": 90+i, "sell_price": 91+i,
             "capital": 50, "state": "IDLE"} for i in range(2)])
    assert Decimal(str(grid["dust_qty"])) == 0
    assert db.add_grid_dust_once(grid["id"], "buy:77", "0.03")
    assert not db.add_grid_dust_once(grid["id"], "buy:77", "0.03")
    assert Decimal(db.get_grid(grid["id"])["dust_qty"]) == Decimal("0.03")


def test_buy_fill_records_exact_decimal_dust_and_replay_does_not_add_twice():
    exchange = FakeExchange(fee_rate="0.001", fee_asset="XRP")
    db = DBManager("sqlite:///:memory:")
    db.add_or_reactivate_coin("XRPUSDT")
    settings = SimpleNamespace(usdt_por_grid=1000.0, max_grids_simultaneos=5,
        capital_max_por_nivel_pct=.30, grid_min_step_pct=.003, fee_pct=.1)
    engine = GridEngine(db, exchange, settings)
    grid = engine.create_grid("XRPUSDT", 90, 110, 5, capital=1000)
    buy = exchange.get_open_orders("XRPUSDT")[-1]
    exchange.fill(buy["order_id"])
    engine.sync_grid(grid["id"])
    dust = Decimal(db.get_grid(grid["id"])["dust_qty"])
    assert dust == Decimal("0.098")
    assert not db.record_grid_buy_fill_dust(grid["id"], 2,
        f"buy:{buy['order_id']}", Decimal("0.098"), {})
    assert Decimal(db.get_grid(grid["id"])["dust_qty"]) == dust


def test_real_engine_and_simulator_sweep_same_owned_dust_and_cash():
    exchange = FakeExchange(fee_rate="0.001", fee_asset="USDT")
    exchange.get_my_trades = lambda symbol, order_id: [{
        "commission": Decimal("1"), "commission_asset": "USDT", "price": Decimal("100")
    }]
    db = DBManager("sqlite:///:memory:")
    db.add_or_reactivate_coin("XRPUSDT")
    grid = db.create_grid_with_levels({"symbol": "XRPUSDT", "range_low": 90,
        "range_high": 110, "n_levels": 2, "capital_total": 100,
        "status": "ACTIVE", "environment": "testnet", "params": {}}, [
            {"level_idx": i, "price": 90+i, "sell_price": 91+i,
             "capital": 50, "state": "IDLE"} for i in range(2)])
    db.update_grid(grid["id"], dust_qty="10.05")
    # The account may contain more XRP; the recorded amount caps the order at 10.0.
    engine = GridEngine(db, exchange, SimpleNamespace(fee_pct=.1))
    result = engine.sweep_grid_dust(grid["id"], Decimal("100"))
    sim = SimExchange(0, fee_pct=.1, filters=exchange.filters, fee_asset="USDT")
    sim.base = sim.dust_qty = Decimal("10.05")
    sim_result = sim.sweep_dust(Decimal("100"), exchange.filters)
    assert result["status"] == "FILLED"
    assert Decimal(result["qty"]) == Decimal("10.0")
    assert Decimal(str(db.get_grid(grid["id"])["dust_qty"])) == Decimal("0.05")
    assert sim_result["qty"] == Decimal("10.0")
    assert Decimal(result["proceeds_net"]) == sim_result["proceeds_net"] == Decimal("999.000")
    assert Decimal(str((db.get_grid(grid["id"])["params"] or {})["dust_cash_proceeds"])) == sim.usdt
    assert sim.dust_qty == Decimal("0.05")


def test_sweep_recovers_filled_order_after_lost_response_without_duplicate_sell():
    exchange = FakeExchange(fee_rate="0.001", fee_asset="USDT")
    exchange.get_my_trades = lambda symbol, order_id: [{
        "commission": Decimal("1"), "commission_asset": "USDT", "price": Decimal("100")
    }]
    db = DBManager("sqlite:///:memory:")
    db.add_or_reactivate_coin("XRPUSDT")
    grid = db.create_grid_with_levels({"symbol": "XRPUSDT", "range_low": 90,
        "range_high": 110, "n_levels": 2, "capital_total": 100,
        "status": "ACTIVE", "environment": "testnet", "params": {}}, [
            {"level_idx": i, "price": 90+i, "sell_price": 91+i,
             "capital": 50, "state": "IDLE"} for i in range(2)])
    db.update_grid(grid["id"], dust_qty="10.05")
    engine = GridEngine(db, exchange, SimpleNamespace(fee_pct=.1))
    exchange.lose_next_response = True
    with pytest.raises(RuntimeError, match="lost response"):
        engine.sweep_grid_dust(grid["id"], Decimal("100"))
    restarted = GridEngine(db, exchange, engine.settings)
    result = restarted.sweep_grid_dust(grid["id"], Decimal("100"))
    assert result["status"] == "FILLED"
    assert len([order for order in exchange.orders.values() if order["side"] == "SELL"]) == 1
    assert Decimal(db.get_grid(grid["id"])["dust_qty"]) == Decimal("0.05")


def test_insufficient_balance_defers_dust_sweep_and_keeps_registered_residual():
    exchange = FakeExchange(fee_rate="0.001", fee_asset="USDT")
    db = DBManager("sqlite:///:memory:")
    db.add_or_reactivate_coin("XRPUSDT")
    grid = db.create_grid_with_levels({"symbol": "XRPUSDT", "range_low": 90,
        "range_high": 110, "n_levels": 2, "capital_total": 100,
        "status": "ACTIVE", "environment": "testnet", "params": {}}, [
            {"level_idx": i, "price": 90+i, "sell_price": 91+i,
             "capital": 50, "state": "IDLE"} for i in range(2)])
    db.update_grid(grid["id"], dust_qty="10.05")
    engine = GridEngine(db, exchange, SimpleNamespace(fee_pct=.1))
    exchange.fail_on_create = 1
    exchange.create_failure = exchange.insufficient_balance()
    result = engine.sweep_grid_dust(grid["id"], Decimal("100"), reason="max_days")
    assert result["status"] == "DEFERRED"
    assert Decimal(db.get_grid(grid["id"])["dust_qty"]) == Decimal("10.05")
    event = db.get_last_event(grid["id"], "DUST_SWEEP_DEFERRED")
    assert event and event["details"]["client_order_id"]
