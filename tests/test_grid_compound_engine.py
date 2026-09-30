from decimal import Decimal
from types import SimpleNamespace

import pytest

from database.db_manager import DBManager
from grid.engine import GridEngine
from tests.grid_fakes import FakeExchange


def make_compound_engine(*, params=None, fee_rate="0.001", fee_asset="XRP"):
    db = DBManager("sqlite:///:memory:")
    db.add_or_reactivate_coin("XRPUSDT")
    exchange = FakeExchange(fee_rate=fee_rate, fee_asset=fee_asset)
    settings = SimpleNamespace(
        usdt_por_grid=1000.0, max_grids_simultaneos=5,
        capital_max_por_nivel_pct=0.30, grid_min_step_pct=0.003,
    )
    engine = GridEngine(db, exchange, settings)
    smart_params = {"compound_enabled": True, **(params or {})}
    grid = engine.create_grid("XRPUSDT", 80, 120, 5, capital=1000,
                              strategy="smart", params=smart_params)
    return engine, db, exchange, grid


def assert_capital_ledger(db, grid_id):
    grid = db.get_grid(grid_id)
    rows = db.get_grid_levels(grid_id)
    total = Decimal(str(grid["capital_total"]))
    base_sum = Decimal(0)
    compound_sum = Decimal(0)
    capital_sum = Decimal(0)
    for row in rows:
        capital = Decimal(str(row["capital"]))
        compound = Decimal(str(row.get("capital_compound") or 0))
        base = row.get("capital_base")
        base = capital - compound if base is None else Decimal(str(base))
        assert abs(capital - base - compound) <= Decimal("0.00000001")
        assert compound >= 0
        if compound > 0:
            events = db.list_grid_events(grid_id=grid_id, event_type="COMPOUND_APPLIED", limit=1000)
            assert any(
                int(event["level_idx"]) == int(row["level_idx"])
                and Decimal(str(event["details"].get("amount", 0))) > 0
                for event in events
            ), "compound capital must have its COMPOUND_APPLIED audit event"
        base_sum += base
        compound_sum += compound
        capital_sum += capital
    assert base_sum <= total + Decimal("0.00000001")
    assert abs(base_sum - total) <= Decimal("0.00000001")
    assert abs(capital_sum - total - compound_sum) <= Decimal("0.00000001")
    return rows


def _record_seeded_compound(db, grid_id, level_idx, amount):
    db.add_grid_event(
        run_id=None, source="CLI", grid_id=grid_id, level_idx=level_idx,
        event_type="COMPOUND_APPLIED", reason="seeded prior cycle",
        details={"amount": str(amount)},
    )


def _buy_level(engine, db, exchange, grid):
    level = db.get_grid_levels(grid["id"])[2]
    assert level["state"] == "BUY_OPEN"
    exchange.fill(level["order_id"])
    engine.sync_grid(grid["id"])
    return db.get_grid_levels(grid["id"])[2]


def _filled_cycle(engine, db, exchange, grid):
    level = _buy_level(engine, db, exchange, grid)
    assert level["state"] == "SELL_OPEN"
    exchange.fill(level["order_id"])
    summary = engine.sync_grid(grid["id"])
    return summary, db.get_grid_levels(grid["id"])[2]


def _prepare_filled_sell(engine, db, exchange, grid):
    level = _buy_level(engine, db, exchange, grid)
    exchange.fill(level["order_id"])
    return level["order_id"]


def test_profit_compounds_cell_and_next_buy_quantity_increases():
    engine, db, exchange, grid = make_compound_engine()
    before = assert_capital_ledger(db, grid["id"])[2]
    before_qty = exchange.get_order("XRPUSDT", order_id=before["order_id"])["quantity"]
    summary, after = _filled_cycle(engine, db, exchange, grid)
    next_order = exchange.get_order("XRPUSDT", order_id=after["order_id"])
    assert summary["cycles_completed"] == 1
    assert after["capital_compound"] > 0
    assert after["capital"] == pytest.approx(after["capital_base"] + after["capital_compound"])
    assert next_order["quantity"] > before_qty
    assert_capital_ledger(db, grid["id"])
    event = db.get_last_event(grid["id"], "COMPOUND_APPLIED")
    assert event and Decimal(event["details"]["amount"]) == Decimal(str(after["capital_compound"]))


def test_loss_does_not_reduce_or_add_compound():
    engine, db, exchange, grid = make_compound_engine()
    before = db.get_grid_levels(grid["id"])[2]
    level = _buy_level(engine, db, exchange, grid)
    exchange.orders[int(level["order_id"])]["price"] = Decimal("90")
    exchange.fill(level["order_id"])
    engine.sync_grid(grid["id"])
    after = db.get_grid_levels(grid["id"])[2]
    assert after["capital"] == before["capital"]
    assert after["capital_base"] == before["capital_base"]
    assert after["capital_compound"] == before["capital_compound"] == 0
    event = db.get_last_event(grid["id"], "COMPOUND_SKIPPED")
    assert event and event["reason"] == "no_profit"
    assert_capital_ledger(db, grid["id"])


def test_compound_ratio_half_is_applied_to_cycle_profit():
    engine, db, exchange, grid = make_compound_engine(params={"compound_ratio": 0.5})
    _summary, after = _filled_cycle(engine, db, exchange, grid)
    pnl = Decimal(str(after["pnl"]))
    assert Decimal(str(after["capital_compound"])) == (pnl * Decimal("0.5")).quantize(Decimal("0.00000001"))
    assert_capital_ledger(db, grid["id"])


def test_growth_cap_limits_compound_to_configured_base_percentage():
    engine, db, exchange, grid = make_compound_engine(params={"compound_max_growth_pct": 2.0})
    _summary, after = _filled_cycle(engine, db, exchange, grid)
    assert after["capital_compound"] <= after["capital_base"] * 0.02
    assert after["capital_compound"] == pytest.approx(4.0)
    assert_capital_ledger(db, grid["id"])


@pytest.mark.parametrize("params", [{}, {"adjust_enabled": True}])
def test_compound_is_off_by_default_and_for_legacy_params(params):
    engine, db, exchange, grid = make_compound_engine(params={"compound_enabled": False})
    db.update_grid(grid["id"], params=db._json(params or None))
    before = db.get_grid_levels(grid["id"])[2]
    _summary, after = _filled_cycle(engine, db, exchange, grid)
    assert after["capital"] == before["capital"]
    assert after["capital_compound"] == 0
    assert db.get_last_event(grid["id"], "COMPOUND_APPLIED") is None
    assert_capital_ledger(db, grid["id"])


def test_simple_grid_never_compounds_even_if_legacy_row_has_flag():
    engine, db, exchange, grid = make_compound_engine(params={"compound_enabled": False})
    db.update_grid(grid["id"], strategy="simple", params=db._json({"compound_enabled": True}))
    _summary, after = _filled_cycle(engine, db, exchange, grid)
    assert after["pnl"] > 0
    assert after["capital_compound"] == 0 and after["capital"] == after["capital_base"]
    assert db.get_last_event(grid["id"], "COMPOUND_APPLIED") is None
    assert_capital_ledger(db, grid["id"])


def test_paused_sell_compounds_then_resume_rearms_with_new_capital():
    engine, db, exchange, grid = make_compound_engine()
    original_buy = db.get_grid_levels(grid["id"])[2]
    original_buy_qty = exchange.get_order("XRPUSDT", order_id=original_buy["order_id"])["quantity"]
    level = _buy_level(engine, db, exchange, grid)
    assert engine.pause_grid(grid["id"], "test", {})["ok"]
    exchange.fill(level["order_id"])
    summary = engine.sync_paused(grid["id"])
    paused_cell = db.get_grid_levels(grid["id"])[2]
    assert summary["cycles_completed"] == 1
    assert paused_cell["state"] == "IDLE" and paused_cell["capital_compound"] > 0
    compounded_capital = paused_cell["capital"]
    engine.resume_grid(grid["id"], "test", {})
    engine.sync_grid(grid["id"])
    resumed = db.get_grid_levels(grid["id"])[2]
    assert resumed["state"] == "BUY_OPEN"
    assert resumed["capital"] == compounded_capital
    assert exchange.get_order("XRPUSDT", order_id=resumed["order_id"])["quantity"] > original_buy_qty
    assert_capital_ledger(db, grid["id"])


def test_closing_and_done_sell_paths_never_compound():
    engine, db, exchange, grid = make_compound_engine()
    level = _buy_level(engine, db, exchange, grid)
    exchange.fill(level["order_id"])
    db.update_grid(grid["id"], status="CLOSING")
    engine.sync_closing(grid["id"])
    closed = db.get_grid_levels(grid["id"])[2]
    assert closed["state"] == "DONE" and closed["capital_compound"] == 0
    assert db.get_last_event(grid["id"], "COMPOUND_APPLIED") is None
    assert_capital_ledger(db, grid["id"])


def test_repository_sell_path_never_compounds_and_keeps_cell_capital():
    engine, db, exchange, grid = make_compound_engine()
    level = _buy_level(engine, db, exchange, grid)
    exchange.fill(level["order_id"])
    repo = db.create_grid_with_levels(
        {"symbol": "XRPUSDT", "range_low": 80, "range_high": 120, "n_levels": 0,
         "capital_total": 0, "status": "HOLDING", "strategy": "repository"}, [],
    )
    db.update_grid(repo["id"], params=db._json({"compound_enabled": True}))
    db.move_level_to_grid(grid["id"], 2, repo["id"], 0)
    before = db.get_grid_levels(repo["id"])[0]
    exchange.fill(before["order_id"])
    engine.sync_repository(repo["id"])
    after = db.get_grid_levels(repo["id"])[0]
    assert after["capital"] == before["capital"]
    assert after["capital_compound"] == 0
    assert db.get_last_event(repo["id"], "COMPOUND_APPLIED") is None


def test_insufficient_free_usdt_skips_compound_without_failing_sync():
    engine, db, exchange, grid = make_compound_engine()
    events = []
    engine.event_sink = events.append
    before = db.get_grid_levels(grid["id"])[2]
    exchange.free_usdt = Decimal("1")
    _summary, after = _filled_cycle(engine, db, exchange, grid)
    assert after["capital"] == before["capital"] and after["capital_compound"] == 0
    assert any(event["event_type"] == "COMPOUND_SKIPPED" and event["reason"] == "insufficient_usdt" for event in events)
    assert_capital_ledger(db, grid["id"])


def test_balance_lookup_error_skips_compound_without_failing_sync():
    engine, db, exchange, grid = make_compound_engine()
    events = []
    engine.event_sink = events.append
    before = db.get_grid_levels(grid["id"])[2]
    exchange.get_balance = lambda _asset=None: (_ for _ in ()).throw(RuntimeError("temporary network failure"))
    _summary, after = _filled_cycle(engine, db, exchange, grid)
    assert after["capital"] == before["capital"] and after["capital_compound"] == 0
    assert any(event["event_type"] == "COMPOUND_SKIPPED" and event["reason"] == "balance_unavailable" for event in events)
    assert_capital_ledger(db, grid["id"])


def test_sell_cycle_write_failure_before_commit_retries_without_loss_or_double_compound():
    engine, db, exchange, grid = make_compound_engine()
    _prepare_filled_sell(engine, db, exchange, grid)
    sell = db.get_grid_levels(grid["id"])[2]
    exchange.fill(sell["order_id"])
    original = db.update_level_with_event
    db.update_level_with_event = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("before commit"))
    with pytest.raises(RuntimeError, match="before commit"):
        engine.sync_grid(grid["id"])
    untouched = db.get_grid_levels(grid["id"])[2]
    assert untouched["cycles_completed"] == 0 and untouched["capital_compound"] == 0
    db.update_level_with_event = original
    engine.sync_grid(grid["id"])
    committed = db.get_grid_levels(grid["id"])[2]
    amount = committed["capital_compound"]
    engine.sync_grid(grid["id"])
    assert db.get_grid_levels(grid["id"])[2]["cycles_completed"] == 1
    assert db.get_grid_levels(grid["id"])[2]["capital_compound"] == amount
    assert_capital_ledger(db, grid["id"])


def test_sell_cycle_write_failure_after_commit_is_idempotent():
    engine, db, exchange, grid = make_compound_engine()
    _prepare_filled_sell(engine, db, exchange, grid)
    sell = db.get_grid_levels(grid["id"])[2]
    exchange.fill(sell["order_id"])
    original = db.update_level_with_event
    def commit_then_fail(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("after commit")
    db.update_level_with_event = commit_then_fail
    with pytest.raises(RuntimeError, match="after commit"):
        engine.sync_grid(grid["id"])
    committed = db.get_grid_levels(grid["id"])[2]
    amount = committed["capital_compound"]
    assert committed["cycles_completed"] == 1 and committed["state"] == "IDLE"
    db.update_level_with_event = original
    engine.sync_grid(grid["id"])
    assert db.get_grid_levels(grid["id"])[2]["cycles_completed"] == 1
    assert db.get_grid_levels(grid["id"])[2]["capital_compound"] == amount
    assert_capital_ledger(db, grid["id"])


def test_stoploss_never_changes_compound_capital():
    engine, db, exchange, grid = make_compound_engine()
    level = _buy_level(engine, db, exchange, grid)
    before = (level["capital"], level["capital_base"], level["capital_compound"])
    engine.stoploss_cell(grid["id"], 2, "test stop", {})
    after = db.get_grid_levels(grid["id"])[2]
    assert (after["capital"], after["capital_base"], after["capital_compound"]) == before
    assert_capital_ledger(db, grid["id"])


def test_adjust_normalizes_free_compound_and_preserves_ledger():
    engine, db, _exchange, grid = make_compound_engine()
    db.update_level(grid["id"], 0, capital=220, capital_base=200, capital_compound=20)
    _record_seeded_compound(db, grid["id"], 0, 20)
    # Keep sum(base) at the original grid capital while one free cell carries profit.
    db.update_level(grid["id"], 1, capital=200, capital_base=200, capital_compound=0)
    db.update_level(grid["id"], 2, capital=200, capital_base=200, capital_compound=0)
    db.update_level(grid["id"], 3, capital=200, capital_base=200, capital_compound=0)
    db.update_level(grid["id"], 4, capital=200, capital_base=200, capital_compound=0)
    assert_capital_ledger(db, grid["id"])
    result = engine.adjust_grid(grid["id"], 82, 122, reason="compound-test")
    assert result["ok"]
    rows = db.get_grid_levels(grid["id"])
    assert result["details"]["compound_normalized"] == "20.00000000"
    persisted_adjustment = db.get_last_event(grid["id"], "GRID_ADJUSTED")
    assert persisted_adjustment["details"]["compound_normalized"] == "20.00000000"
    assert db.get_grid(grid["id"])["capital_total"] == pytest.approx(1020)
    assert all(row["capital_compound"] == 0 and row["capital_base"] == row["capital"] for row in rows)
    assert_capital_ledger(db, grid["id"])


def test_adjust_rejects_repartition_over_30_percent_base_cap():
    engine, db, _exchange, grid = make_compound_engine()
    db.update_level(grid["id"], 0, capital=500, capital_base=100, capital_compound=400)
    _record_seeded_compound(db, grid["id"], 0, 400)
    db.update_level(grid["id"], 1, capital=225, capital_base=225, capital_compound=0)
    db.update_level(grid["id"], 2, capital=225, capital_base=225, capital_compound=0)
    db.update_level(grid["id"], 3, capital=225, capital_base=225, capital_compound=0)
    db.update_level(grid["id"], 4, capital=225, capital_base=225, capital_compound=0)
    result = engine.adjust_grid(grid["id"], 82, 122, new_n=4, reason="compound-cap-test")
    assert not result["ok"]
    assert "capital_per_cell_exceeds_CAPITAL_MAX_POR_NIVEL_PCT" in result["reason"]
    assert_capital_ledger(db, grid["id"])


def test_rearm_plan_allows_effective_cell_capital_above_thirty_percent_base():
    engine, db, exchange, grid = make_compound_engine(params={"compound_max_growth_pct": 200})
    cell = db.get_grid_levels(grid["id"])[0]
    exchange.cancel_order("XRPUSDT", cell["order_id"])
    db.update_level(grid["id"], 0, state="IDLE", order_id=None, client_order_id=None,
                    buy_client_order_id=None, cycles_completed=1,
                    capital=600, capital_base=200, capital_compound=400)
    _record_seeded_compound(db, grid["id"], 0, 400)
    assert_capital_ledger(db, grid["id"])
    engine.sync_grid(grid["id"])
    armed = db.get_grid_levels(grid["id"])[0]
    assert armed["state"] == "BUY_OPEN" and armed["capital"] == 600
    assert exchange.get_order("XRPUSDT", order_id=armed["order_id"])["quantity"] > Decimal("5")
    assert_capital_ledger(db, grid["id"])
