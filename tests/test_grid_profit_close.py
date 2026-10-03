from decimal import Decimal
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import pytest

from grid.policy import build_profit_cells, evaluate_target, plan_profit_close
from grid.control_service import run_action
from grid.monitor import GridMonitor
from tests.test_grid_engine import create, make_engine


def _profit_preview(engine, db, exchange, grid_id):
    client = SimpleNamespace(client=SimpleNamespace(testnet=True),
        get_book_ticker=exchange.get_book_ticker, get_open_orders=exchange.get_open_orders)
    app = SimpleNamespace(state=SimpleNamespace(testnet_client=client, grid_engine=engine,
        grid_monitor=None, db=db))
    return run_action(SimpleNamespace(app=app), grid_id, "close",
                      {"mode": "profit_repository"})["plan"]


def _profit_retry_case():
    engine, db, exchange = make_engine(fee_rate="0", fee_asset="USDT")
    grid = create(engine)
    buy = next(row for row in exchange.get_open_orders("XRPUSDT") if row["side"] == "BUY")
    exchange.fill(buy["order_id"])
    engine.sync_grid(grid["id"])
    now = {"value": datetime(2026, 1, 1, tzinfo=timezone.utc)}
    engine._utcnow = lambda: now["value"]
    reject = {"active": True, "count": 0}
    original_place = exchange.place_order

    def place_order(symbol, side, quantity, price=None, order_type="LIMIT", client_order_id=None):
        if reject["active"] and order_type == "MARKET" and side == "SELL":
            reject["count"] += 1
            exchange.fail_on_create = len(exchange.create_calls) + 1
            exchange.create_failure = RuntimeError("temporary market rejection")
        return original_place(symbol, side, quantity, price, order_type, client_order_id)

    exchange.place_order = place_order
    engine.close_grid(grid["id"], "profit_repository")

    class Vol:
        def get(self, _symbol):
            return SimpleNamespace(sigma_24h=None)

    settings = SimpleNamespace(grid_monitor_enabled=True, grid_policy_enabled=False,
                               grid_monitor_interval=900, grid_monitor_gap_minutes=20)
    monitor = GridMonitor(db, exchange, engine, settings, clock=lambda: now["value"],
                          vol_provider=Vol())
    return engine, db, exchange, grid, now, reject, monitor


def test_profit_close_planner_all_winners_and_parity_with_target_selection():
    engine, _db, _exchange = make_engine(fee_rate="0", fee_asset="USDT")
    filters, _snapshot, _avg = engine._market_context("XRPUSDT")
    cells = [{"level_idx": 1, "held_qty": 1, "entry_cost": 90, "entry_fee_usdt": 0.09}]
    plan = plan_profit_close(cells, 100, filters, .1)
    target = evaluate_target({"target_usdt": .01}, 1, 0, cells, 100, filters, .1)
    assert [row["level_idx"] for row in plan["sell_cells"]] == [1]
    assert [row["level_idx"] for row in target["sell_cells"]] == [1]
    assert plan["repo_cells"] == [] and plan["net_gain_usdt"] > 0


def test_profit_close_planner_all_losers_unsellable_and_gross_but_net_loss():
    engine, _db, _exchange = make_engine(fee_rate="0", fee_asset="USDT")
    filters, _snapshot, _avg = engine._market_context("XRPUSDT")
    cells = [
        {"level_idx": 1, "held_qty": 1, "entry_cost": 101},
        {"level_idx": 2, "held_qty": .001, "entry_cost": .001},
        {"level_idx": 3, "held_qty": 1, "entry_cost": 99.9, "entry_fee_usdt": .2},
        {"level_idx": 4, "held_qty": 1, "entry_cost": 99.9},
    ]
    plan = plan_profit_close(cells, 100, filters, .1)
    assert plan["sell_cells"] == []
    assert {row["level_idx"] for row in plan["repo_cells"]} == {1, 2, 3, 4}
    assert next(row for row in plan["repo_cells"] if row["level_idx"] == 2)["reason"] == "not_sellable"
    assert next(row for row in plan["repo_cells"] if row["level_idx"] == 3)["reason"] == "not_profitable"
    assert next(row for row in plan["repo_cells"] if row["level_idx"] == 4)["reason"] == "not_profitable"


def test_profit_close_engine_sells_winner_and_repositories_loser_without_target_event():
    engine, db, exchange = make_engine(fee_rate="0", fee_asset="USDT")
    grid = create(engine)
    buy_orders = {row["client_order_id"]: row for row in exchange.get_open_orders("XRPUSDT")
                  if row["side"] == "BUY"}
    for cid in ("g1L0B0", "g1L1B0"):
        exchange.fill(buy_orders[cid]["order_id"])
    exchange.orders[buy_orders["g1L1B0"]["order_id"]]["_trades"][0]["price"] = Decimal("110")
    engine.sync_grid(grid["id"])
    held = [row for row in db.get_grid_levels(grid["id"]) if row["held_qty"] > 0]
    assert len(held) == 2
    winner_idx = int(held[0]["level_idx"])
    loser_cid = held[1].get("buy_client_order_id")
    db.update_level(grid["id"], winner_idx, entry_price=80)
    db.update_level(grid["id"], int(held[1]["level_idx"]), entry_price=110)
    original_place = exchange.place_order
    def require_buys_canceled(symbol, side, quantity, price=None, order_type="LIMIT", client_order_id=None):
        if order_type == "MARKET" and side == "SELL":
            assert not [row for row in exchange.get_open_orders(symbol) if row["side"] == "BUY"]
        return original_place(symbol, side, quantity, price, order_type, client_order_id)
    exchange.place_order = require_buys_canceled
    result = engine.close_grid(grid["id"], "profit_repository")
    assert result["status"] == "CLOSED"
    assert db.get_last_event(grid["id"], "PROFIT_CLOSE") is not None
    assert db.get_last_event(grid["id"], "TARGET_REACHED") is None
    market_sells = [row for row in exchange.orders.values()
                    if row.get("type") == "MARKET" and row.get("side") == "SELL"]
    assert len(market_sells) == 1
    repository = next(row for row in db.list_grids_by_status({"HOLDING"})
                      if row["symbol"] == grid["symbol"])
    repo_cells = [row for row in db.get_grid_levels(repository["id"]) if Decimal(str(row["held_qty"])) > 0]
    assert len(repo_cells) == 1 and repo_cells[0]["buy_client_order_id"] == loser_cid


def test_profit_preview_caches_trades_but_execution_reads_fresh_fees():
    engine, db, exchange = make_engine(fee_rate="0", fee_asset="USDT")
    grid = create(engine)
    buy = next(row for row in exchange.get_open_orders("XRPUSDT") if row["side"] == "BUY")
    exchange.fill(buy["order_id"])
    engine.sync_grid(grid["id"])
    counts = {"order": 0, "trades": 0}
    original_get_order, original_get_trades = exchange.get_order, exchange.get_my_trades

    def get_order(symbol, order_id=None, client_order_id=None):
        if client_order_id == buy["client_order_id"] or order_id == buy["order_id"]:
            counts["order"] += 1
        return original_get_order(symbol, order_id, client_order_id)

    def get_trades(symbol, order_id):
        if order_id == buy["order_id"]:
            counts["trades"] += 1
        return original_get_trades(symbol, order_id)

    exchange.get_order, exchange.get_my_trades = get_order, get_trades
    first = _profit_preview(engine, db, exchange, grid["id"])
    assert first["cells_to_sell"] == 1
    first_counts = dict(counts)
    second = _profit_preview(engine, db, exchange, grid["id"])
    assert second["cells_to_sell"] == 1 and counts == first_counts
    exchange.orders[buy["order_id"]]["_trades"][0]["commission"] = Decimal("30")
    result = engine.close_grid(grid["id"], "profit_repository")
    assert result["status"] == "CLOSED"
    assert counts["order"] == first_counts["order"] + 1
    assert counts["trades"] == first_counts["trades"] + 1
    persisted = db.get_grid(grid["id"])["params"]["target_close_plan"]
    assert persisted["sell_cells"] == [] and len(persisted["repo_cells"]) == 1


def test_profit_preview_deadline_marks_remaining_cells_as_estimated_without_sleep():
    engine, db, exchange = make_engine(fee_rate="0", fee_asset="USDT")
    grid = create(engine)
    buys = exchange.get_open_orders("XRPUSDT")
    for buy in buys:
        exchange.fill(buy["order_id"])
    engine.sync_grid(grid["id"])
    clock = {"now": 0.0}
    engine._monotonic = lambda: clock["now"]
    original_get_trades = exchange.get_my_trades

    def delayed_trades(symbol, order_id):
        trades = original_get_trades(symbol, order_id)
        clock["now"] += 7.0
        return trades

    exchange.get_my_trades = delayed_trades
    plan = _profit_preview(engine, db, exchange, grid["id"])
    assert plan["estimated_fee_cells"] == len(buys) - 1


def test_profit_cell_builder_uses_fee_estimate_only_without_stored_trades():
    engine, _db, exchange = make_engine(fee_rate="0", fee_asset="USDT")
    grid = create(engine)
    buy = next(row for row in exchange.get_open_orders("XRPUSDT") if row["side"] == "BUY")
    exchange.fill(buy["order_id"])
    engine.sync_grid(grid["id"])
    level = next(row for row in engine.db.get_grid_levels(grid["id"]) if row["held_qty"] > 0)
    exchange.orders[buy["order_id"]]["_trades"] = []
    cell = engine._build_profit_cells(grid["id"], "XRPUSDT", [level])[0]
    assert cell["entry_cost"] == pytest.approx(level["entry_price"] * level["held_qty"])
    assert cell["entry_fee_usdt"] == pytest.approx(cell["entry_cost"] * .001)


def test_profit_cell_fee_conversion_failure_falls_back_atomically_and_counts_estimate():
    estimated = []
    cell = build_profit_cells(
        [{"level_idx": 4, "held_qty": 2, "entry_price": 12, "buy_client_order_id": "buy-4"}],
        lambda _cid: [{"quoteQty": "20", "qty": "2", "price": "10"}],
        lambda _trades: (_ for _ in ()).throw(ValueError("fee conversion unavailable")),
        on_fee_estimated=lambda: estimated.append(True),
    )[0]
    assert cell["entry_cost"] == 24
    assert cell["entry_fee_usdt"] == pytest.approx(24 * .001)
    assert len(estimated) == 1


def test_monitor_does_not_reach_cash_target_when_actual_buy_fee_makes_cell_unprofitable():
    engine, db, exchange = make_engine(fee_rate="0", fee_asset="USDT")
    grid = create(engine, strategy="smart", params={"target_usdt": 1, "adjust_enabled": False})
    buy = next(row for row in exchange.get_open_orders("XRPUSDT") if row["side"] == "BUY")
    exchange.fill(buy["order_id"])
    exchange.orders[buy["order_id"]]["_trades"][0]["commission"] = Decimal("30")
    engine.sync_grid(grid["id"])

    class Vol:
        def get(self, _symbol):
            return SimpleNamespace(sigma_24h=None)

    settings = SimpleNamespace(grid_monitor_enabled=True, grid_policy_enabled=True,
                               grid_monitor_interval=900, grid_monitor_gap_minutes=20)
    GridMonitor(db, exchange, engine, settings,
        clock=lambda: engine._utcnow() + timedelta(seconds=60), vol_provider=Vol()).run_once("SCHEDULED")
    assert db.get_last_event(grid["id"], "TARGET_REACHED") is None
    assert db.get_grid(grid["id"])["status"] == "ACTIVE"


def test_profit_close_market_sell_failure_keeps_profit_reason_and_event():
    engine, db, exchange = make_engine(fee_rate="0", fee_asset="USDT")
    grid = create(engine)
    buy = next(row for row in exchange.get_open_orders("XRPUSDT") if row["side"] == "BUY")
    exchange.fill(buy["order_id"])
    engine.sync_grid(grid["id"])
    for level in db.get_grid_levels(grid["id"]):
        if level["held_qty"] > 0:
            db.update_level(grid["id"], int(level["level_idx"]), entry_price=80)
    exchange.fail_on_create = len(exchange.create_calls) + 1
    exchange.create_failure = RuntimeError("simulated MARKET rejection")
    engine.close_grid(grid["id"], "profit_repository")
    assert db.get_grid(grid["id"])["status"] == "CLOSING"
    assert db.get_grid(grid["id"])["fail_reason"] == "PROFIT_CLOSE"
    assert db.get_last_event(grid["id"], "PROFIT_CLOSE_MARKET_SELL_FAILED") is not None
    assert db.get_last_event(grid["id"], "TARGET_MARKET_SELL_FAILED") is None


def test_monitor_retries_closing_profit_plan_to_completion():
    engine, db, exchange = make_engine(fee_rate="0", fee_asset="USDT")
    grid = create(engine)
    buy = next(row for row in exchange.get_open_orders("XRPUSDT") if row["side"] == "BUY")
    exchange.fill(buy["order_id"])
    engine.sync_grid(grid["id"])
    exchange.fail_on_create = len(exchange.create_calls) + 1
    exchange.create_failure = RuntimeError("temporary MARKET rejection")
    first = engine.close_grid(grid["id"], "profit_repository")
    assert first["status"] == "CLOSING"
    assert db.get_grid(grid["id"])["fail_reason"] == "PROFIT_CLOSE"
    exchange.fail_on_create = None

    class Vol:
        def get(self, _symbol):
            return SimpleNamespace(sigma_24h=None)

    settings = SimpleNamespace(grid_monitor_enabled=True, grid_policy_enabled=False,
                               grid_monitor_interval=900, grid_monitor_gap_minutes=20)
    GridMonitor(db, exchange, engine, settings,
        clock=lambda: engine._utcnow() + timedelta(seconds=60), vol_provider=Vol()).run_once("SCHEDULED")
    assert db.get_grid(grid["id"])["status"] == "CLOSED"
    assert db.get_grid(grid["id"])["params"]["target_close_plan"]["phase"] == "COMPLETE"


def test_profit_retry_backoff_and_third_attempt_success_clears_retry_state():
    _engine, db, _exchange, grid, now, reject, monitor = _profit_retry_case()
    plan = db.get_grid(grid["id"])["params"]["target_close_plan"]
    assert plan["retry_count"] == 0 and plan["last_retry_at"] == now["value"].isoformat()
    now["value"] += timedelta(seconds=59)
    monitor.run_once("SCHEDULED")
    assert reject["count"] == 1
    now["value"] += timedelta(seconds=1)
    monitor.run_once("SCHEDULED")
    assert reject["count"] == 2
    plan = db.get_grid(grid["id"])["params"]["target_close_plan"]
    assert plan["retry_count"] == 1
    now["value"] += timedelta(seconds=119)
    monitor.run_once("SCHEDULED")
    assert reject["count"] == 2
    now["value"] += timedelta(seconds=1)
    reject["active"] = False
    monitor.run_once("SCHEDULED")
    plan = db.get_grid(grid["id"])["params"]["target_close_plan"]
    assert db.get_grid(grid["id"])["status"] == "CLOSED"
    assert plan["retry_count"] == 0 and plan["last_retry_at"] is None


def test_profit_retry_exhaustion_emits_once_stops_monitor_and_manual_close_resets():
    engine, db, _exchange, grid, now, reject, monitor = _profit_retry_case()
    for retry_number in range(1, 9):
        count = retry_number - 1
        delay = min(60 * (2 ** count), 900)
        now["value"] += timedelta(seconds=delay)
        monitor.run_once("SCHEDULED")
    current = db.get_grid(grid["id"])
    plan = current["params"]["target_close_plan"]
    assert current["status"] == "CLOSING"
    assert current["fail_reason"] == "PROFIT_CLOSE_RETRY_EXHAUSTED"
    assert plan["retry_count"] == 8 and plan["retry_exhausted_event_emitted"] is True
    exhausted = [event for event in db.list_grid_events(grid_id=grid["id"],
        event_type="PROFIT_CLOSE_MARKET_SELL_FAILED") if event.get("reason") == "retry_exhausted"]
    assert len(exhausted) == 1 and reject["count"] == 9
    now["value"] += timedelta(seconds=1800)
    monitor.run_once("SCHEDULED")
    assert reject["count"] == 9
    reject["active"] = False
    engine.close_grid(grid["id"], "profit_repository")
    final = db.get_grid(grid["id"])
    plan = final["params"]["target_close_plan"]
    assert final["status"] == "CLOSED"
    assert plan["retry_count"] == 0 and plan["last_retry_at"] is None


@pytest.mark.parametrize("phase", ["MARKED", "BUYS_CANCELED", "MARKET_SELLS_DONE", "REPOSITORY"])
def test_profit_close_resumes_from_each_persisted_phase(phase):
    engine, db, exchange = make_engine(fee_rate="0", fee_asset="USDT")
    grid = create(engine)
    order = next(row for row in exchange.get_open_orders("XRPUSDT")
                 if row["side"] == "BUY")
    exchange.fill(order["order_id"])
    engine.sync_grid(grid["id"])
    level = next(row for row in db.get_grid_levels(grid["id"]) if row["held_qty"] > 0)
    db.update_level(grid["id"], int(level["level_idx"]), entry_price=80)
    filters, _snapshot, _avg = engine._market_context("XRPUSDT")
    selection = plan_profit_close([{"level_idx": int(level["level_idx"]), "held_qty": level["held_qty"],
        "entry_cost": 80 * float(level["held_qty"]), "entry_fee_usdt": 0}], 100, filters, .1)
    plan = {**selection, "reason": "PROFIT_CLOSE", "phase": phase, "cash_now": 0,
        "target_threshold": 0, "sold_done": [], "sale_failures": [], "actual_market_proceeds": [],
        "target_missed_after_fills": False}
    if phase != "MARKED":
        engine.cancel_grid_orders(grid["id"], finalize=False)
    if phase in {"MARKET_SELLS_DONE", "REPOSITORY"}:
        db.update_level(grid["id"], int(level["level_idx"]), state="DONE", held_qty=0, order_id=None)
    params = dict(grid.get("params") or {})
    params["target_close_plan"] = plan
    db.update_grid(grid["id"], status="CLOSING", params=db._json(params))
    before = len([row for row in exchange.orders.values()
                  if row.get("type") == "MARKET" and row.get("side") == "SELL"])
    result = engine.close_grid_target(grid["id"], plan, 100)
    after = len([row for row in exchange.orders.values()
                 if row.get("type") == "MARKET" and row.get("side") == "SELL"])
    assert result["status"] == "CLOSED"
    assert after - before == (1 if phase in {"MARKED", "BUYS_CANCELED"} else 0)
    assert db.get_last_event(grid["id"], "TARGET_REACHED") is None
    assert db.get_grid(grid["id"])["params"]["target_close_plan"]["phase"] == "COMPLETE"
