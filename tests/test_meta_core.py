from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
import threading

import pytest

from database.db_manager import DBManager
from grid.meta import MetaCore, attributable_cash, executable_value
from grid.meta_operator import MetaOperator
from alerts.dispatcher import AlertDispatcher


class FakeTestnet:
    testnet = True

    def __init__(self, free=1000, bid=10):
        self.free, self.bid = free, bid
        self.clock = lambda: datetime(2026, 1, 1, tzinfo=timezone.utc)

    def get_balance(self, asset=None):
        return {"USDT": {"free": self.free, "locked": 0}}

    def get_book_ticker(self, symbol):
        return {"bid_price": self.bid, "ask_price": self.bid + 0.01}

    def get_order_book_depth(self, symbol, limit=5):
        from datetime import timezone
        return {"bids": ([(Decimal(str(self.bid)), Decimal("1000000"))] if self.bid > 0 else []),
                "asks": [], "read_at": self.clock().astimezone(timezone.utc)}


def _core(tmp_path, *, exchange=None, enabled=True, alert=None):
    db = DBManager(f"sqlite:///{tmp_path / 'meta.sqlite'}")
    settings = SimpleNamespace(meta_mode_enabled=enabled, meta_max_active=1, alerts_enabled=False)
    exchange = exchange or FakeTestnet()
    core = MetaCore(db, exchange, settings=settings, clock=lambda: datetime(2026, 1, 1, tzinfo=timezone.utc), alert=alert)
    exchange.clock = lambda: core.clock()
    return db, core


def test_touch_observations_link_to_meta_decision_and_alternative(tmp_path):
    from sqlalchemy import select

    db, core = _core(tmp_path)
    goal = core.create_goal(100, 10)
    operator = MetaOperator(core, db, None, None, core.settings)
    stats = {"touches": 2, "sold_within_horizon": 1, "sell_rate": .5,
             "normalized_value": 0, "censored_touches": 1}
    operator._persist_touch_observations(goal["id"], "decision-40b5", [{
        "alternative_id": "decision-40b5:0",
        "pieces": [{"symbol": "XRPUSDT", "piece_type": "individual_bot_grid"}],
        "_touch_observations": [{"cell_index": 0, "window_start": "2026-01-01",
            "horizon": "24h", "buy_relative": .99, "sell_relative": 1.01, "stats": stats}],
    }])
    with db.engine.connect() as conn:
        row = conn.execute(select(db.meta_signal_observations)).mappings().one()
    assert row["meta_id"] == goal["id"]
    assert row["decision_id"] == "decision-40b5"
    assert row["alternative_id"] == "decision-40b5:0"
    assert row["piece_type"] == "individual_bot_grid"
    assert row["buy_relative"] == pytest.approx(.99)


def test_attributable_cash_fixed_numbers():
    # 100 iniciales - 60 entregados + 70 devueltos - 1 de comisiones = 109.
    assert attributable_cash("100", "60", "70", "1") == Decimal("109")
    # 100 iniciales - 70 entregados + 70.10 devueltos - 0.1814 de comisiones
    # deja 99.9186 USDT tras una pérdida neta realizada de 0.0814.
    assert attributable_cash("100", "70", "70.10", "0.1814") == Decimal("99.9186")


def test_prudent_verification_minimum_is_editable_in_profile_settings(tmp_path):
    db, core = _core(tmp_path)
    updated = core.set_config("PRUDENTE", {"min_effective_verifications": 32})
    assert updated["min_effective_verifications"] == 32


def test_rotation_limits_and_materiality_are_editable_per_profile(tmp_path):
    _db, core = _core(tmp_path)
    updated = core.set_config("RAPIDO", {
        "rotation_min_probability_delta_pp": 6,
        "rotation_probability_tie_tolerance_pp": 1.5,
        "rotation_min_progress_delta_pct": 12,
        "rotation_cooldown_hours": 1.25,
        "max_rotations_per_24h": 3,
    })
    assert updated["rotation_min_probability_delta_pp"] == 6
    assert updated["rotation_probability_tie_tolerance_pp"] == 1.5
    assert updated["rotation_min_progress_delta_pct"] == 12
    assert updated["rotation_cooldown_hours"] == 1.25
    assert updated["max_rotations_per_24h"] == 3


def test_executable_value_consumes_depth_and_marks_partial_unknown_and_stale():
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    known = executable_value("3", [("10", "1"), ("9", "2")], .1, 1, now, now, 60)
    assert known["status"] == "KNOWN"
    assert Decimal(known["received_net"]) == Decimal("27.972")
    partial = executable_value("4", [("10", "1"), ("9", "2")], .1, 1, now, now, 60)
    assert partial["status"] == "PARCIAL"
    assert Decimal(partial["missing_value_est"]) == Decimal("8.90109")
    assert Decimal(partial["missing_value_est"]) > 0
    empty = executable_value("1", [], .1, 1, now, now, 60)
    stale = executable_value("1", [("10", "1")], .1, 1, now - timedelta(seconds=61), now, 60)
    assert empty["status"] == stale["status"] == "DESCONOCIDO"
    assert empty["received_net"] is stale["received_net"] is None


def test_meta_target_percent_and_disabled_gate(tmp_path):
    _db, core = _core(tmp_path, enabled=False)
    with pytest.raises(ValueError, match="apagado"):
        core.create_goal(100, 10, "PCT", "PRUDENTE")


def test_start_checks_testnet_free_and_other_grid_reserve(tmp_path):
    db, core = _core(tmp_path, exchange=FakeTestnet(free=90))
    goal = core.create_goal(100, 10, "PCT", "PRUDENTE")
    assert goal["target_usdt"] == 10
    with pytest.raises(ValueError, match="USDT libre insuficiente"):
        core.start_goal(goal["id"])
    assert core.ledger(goal["id"])[-1]["event_type"] == "META_START_REJECTED"


def test_single_active_goal_enforced_transactionally(tmp_path):
    _db, core = _core(tmp_path)
    first = core.create_goal(100, 10)
    core.start_goal(first["id"])
    with pytest.raises(ValueError, match="meta activa"):
        core.create_goal(100, 10)


def test_valuation_uses_piece_cash_bid_and_estimated_sell_fee(tmp_path):
    db, core = _core(tmp_path, exchange=FakeTestnet(bid=10))
    goal = core.create_goal(100, 10)
    now = datetime(2026, 1, 1)
    with db.engine.begin() as conn:
        grid_id = conn.execute(db.grids.insert().values(symbol="XRPUSDT", range_low=1, range_high=2,
            n_levels=1, capital_total=100, reserve=0, status="ACTIVE", created_at=now,
            environment="testnet", strategy="simple", params="{}", dust_qty="0")).inserted_primary_key[0]
        conn.execute(db.grid_levels.insert().values(grid_id=grid_id, level_idx=0, price=1, capital=100,
            state="HOLDING", held_qty=1, cycles_completed=0, pnl=0, fee_paid=0, updated_at=now))
        run_id = conn.execute(db.meta_runs.insert().values(meta_id=goal["id"], status="ACTIVE",
            started_at=now, realized_extra_usdt=0)).inserted_primary_key[0]
        conn.execute(db.meta_piece_links.insert().values(meta_id=goal["id"], run_id=run_id,
            grid_id=grid_id, piece_type="grid", origin_grid_id=grid_id, created_at=now))
    db.add_grid_event(run_id=None, source="MONITOR", ts=now, grid_id=grid_id, level_idx=0, event_type="BUY_FILLED",
        details={"cummulative_quote_qty": "60", "fee_usdt": "0.5"})
    db.add_grid_event(run_id=None, source="MONITOR", ts=now, grid_id=grid_id, level_idx=0, event_type="SELL_FILLED",
        details={"cummulative_quote_qty": "70", "fee_usdt": "0.5"})
    value = core.valuation(goal["id"])
    # Efectivo 100 - 60 + 70 - 1 = 109; inventario 1 * BID 10 * 0.999 = 9.99.
    assert Decimal(value["cash_usdt"]) == Decimal("109.0")
    assert Decimal(value["value_usdt"]) == Decimal("118.990")
    assert Decimal(value["value_best_bid"]) == Decimal("118.990")
    assert Decimal(value["result_pct"]) == Decimal("18.9900")


def test_one_sided_book_returns_unknown_and_records_alert(tmp_path):
    alerts = []
    db, core = _core(tmp_path, exchange=FakeTestnet(bid=0), alert=lambda *args: alerts.append(args))
    clock_now = [datetime(2026, 1, 1, tzinfo=timezone.utc)]
    core.clock = lambda: clock_now[0]
    goal = core.create_goal(100, 10)
    now = datetime(2026, 1, 1)
    with db.engine.begin() as conn:
        grid_id = conn.execute(db.grids.insert().values(symbol="XRPUSDT", range_low=1, range_high=2,
            n_levels=1, capital_total=100, reserve=0, status="ACTIVE", created_at=now,
            environment="testnet", strategy="simple", params="{}", dust_qty="0")).inserted_primary_key[0]
        conn.execute(db.grid_levels.insert().values(grid_id=grid_id, level_idx=0, price=1, capital=100,
            state="HOLDING", held_qty=10, cycles_completed=0, pnl=0, fee_paid=0, updated_at=now))
        run_id = conn.execute(db.meta_runs.insert().values(meta_id=goal["id"], status="ACTIVE",
            started_at=now, realized_extra_usdt=0)).inserted_primary_key[0]
        conn.execute(db.meta_piece_links.insert().values(meta_id=goal["id"], run_id=run_id,
            grid_id=grid_id, piece_type="grid", origin_grid_id=grid_id, created_at=now))
    result = core.valuation(goal["id"])
    assert result["status"] == "DESCONOCIDO"
    assert not any(row["event_type"] == "META_UNPROTECTED" for row in core.ledger(goal["id"]))
    assert not alerts
    clock_now[0] += timedelta(minutes=11)
    core.valuation(goal["id"])
    assert len(alerts) == 1 and alerts[0][0] == "META_UNPROTECTED"
    clock_now[0] += timedelta(minutes=30)
    core.valuation(goal["id"])
    assert len(alerts) == 2
    with db.engine.begin() as conn:
        conn.execute(db.meta_goals.update().where(db.meta_goals.c.id == goal["id"]).values(
            status="ACTIVE", started_at=clock_now[0].replace(tzinfo=None)))
        conn.execute(db.meta_runs.insert().values(meta_id=goal["id"], status="ACTIVE",
            started_at=clock_now[0].replace(tzinfo=None), realized_extra_usdt=0))
    core.run_once()
    assert core.get_goal(goal["id"])["status"] == "PIECES_BLOCKED"


def test_start_deducts_other_grid_idle_capital(tmp_path):
    db, core = _core(tmp_path, exchange=FakeTestnet(free=120))
    now = datetime(2026, 1, 1)
    with db.engine.begin() as conn:
        grid_id = conn.execute(db.grids.insert().values(symbol="BTCUSDT", range_low=1, range_high=2,
            n_levels=1, capital_total=20, reserve=0, status="ACTIVE", created_at=now,
            environment="testnet", strategy="simple", params="{}", dust_qty="0")).inserted_primary_key[0]
        conn.execute(db.grid_levels.insert().values(grid_id=grid_id, level_idx=0, price=1, capital=20,
            state="IDLE", held_qty=0, cycles_completed=0, pnl=0, fee_paid=0, updated_at=now))
    goal = core.create_goal(105, 10)
    with pytest.raises(ValueError, match="disponible 100"):
        core.start_goal(goal["id"])


def test_meta_piece_links_allow_one_level_bot_grid_and_enforce_exclusive_ownership(tmp_path):
    db, core = _core(tmp_path)
    first = core.create_goal(100, 10)
    second = core.create_goal(100, 10)
    now = datetime(2026, 1, 1)
    with db.engine.begin() as conn:
        conn.execute(db.meta_goals.update().where(db.meta_goals.c.id.in_([first["id"], second["id"]])).values(status="ACTIVE"))
        grid_id = conn.execute(db.grids.insert().values(symbol="XRPUSDT", range_low=1, range_high=2,
            n_levels=1, capital_total=30, reserve=0, status="ACTIVE", created_at=now,
            environment="testnet", strategy="simple", params="{}", dust_qty="0")).inserted_primary_key[0]
        conn.execute(db.grid_levels.insert().values(grid_id=grid_id, level_idx=0, price=1, capital=30,
            state="IDLE", held_qty=0, cycles_completed=0, pnl=0, fee_paid=0, updated_at=now))
    core.link_grid(first["id"], grid_id, "individual_bot_grid")
    with pytest.raises(ValueError, match="ya pertenece"):
        core.link_grid(second["id"], grid_id, "individual_bot_grid")


@pytest.mark.parametrize("profile,bid,close_proceeds", [("PRUDENTE", 9.2, "92"), ("RAPIDO", 8.2, "81.9")])
def test_loss_watch_closes_linked_grid_and_warns(tmp_path, profile, bid, close_proceeds):
    alerts = []
    db, core = _core(tmp_path, exchange=FakeTestnet(bid=bid), alert=lambda *args: alerts.append(args))
    goal = core.create_goal(100, 10, profile=profile)
    now = datetime(2026, 1, 1)
    with db.engine.begin() as conn:
        grid_id = conn.execute(db.grids.insert().values(symbol="XRPUSDT", range_low=1, range_high=2,
            n_levels=1, capital_total=100, reserve=0, status="ACTIVE", created_at=now,
            environment="testnet", strategy="simple", params="{}", dust_qty="0")).inserted_primary_key[0]
        conn.execute(db.grid_levels.insert().values(grid_id=grid_id, level_idx=0, price=10, capital=100,
            state="HOLDING", held_qty=10, cycles_completed=0, pnl=0, fee_paid=0, updated_at=now))
        run_id = conn.execute(db.meta_runs.insert().values(meta_id=goal["id"], status="ACTIVE",
            started_at=now, realized_extra_usdt=0)).inserted_primary_key[0]
        conn.execute(db.meta_goals.update().where(db.meta_goals.c.id == goal["id"]).values(
            status="ACTIVE", started_at=datetime(2025, 12, 15)))
        conn.execute(db.meta_piece_links.insert().values(meta_id=goal["id"], run_id=run_id,
            grid_id=grid_id, piece_type="grid", origin_grid_id=grid_id, created_at=now))
    db.add_grid_event(run_id=None, source="MONITOR", ts=now, grid_id=grid_id, event_type="BUY_FILLED",
        details={"cummulative_quote_qty": "100", "fee_usdt": "0"})

    class ClosingEngine:
        def close_grid(self, grid_id, mode, **kwargs):
            db.update_grid(grid_id, status="CLOSED")
            db.update_level(grid_id, 0, state="DONE", held_qty=0)
            db.add_grid_event(run_id=None, source="CLI", ts=now, grid_id=grid_id, event_type="CELL_LIQUIDATED",
                details={"cash_proceeds_net": close_proceeds, "fee_usdt": "0"})
            return {"status": "CLOSED"}

    closing_engine = ClosingEngine()
    core.engine = closing_engine
    core.run_once()
    # Un proceso nuevo conserva en meta_runs el primer cruce del límite.
    core = MetaCore(db, core.exchange, closing_engine, settings=core.settings,
        clock=lambda: datetime(2026, 1, 1, tzinfo=timezone.utc), alert=lambda *args: alerts.append(args))
    core.run_once()
    assert core.get_goal(goal["id"])["status"] == "CORTADA_POR_PERDIDA"
    assert any(kind == "META_LOSS_WARNING" for kind, _meta_id, _data in alerts)
    assert any(kind == "META_CORTADA_POR_PERDIDA" for kind, _meta_id, _data in alerts)


def test_slow_operator_review_does_not_block_monitor_loss_close(tmp_path):
    from grid.meta_operator import MetaOperator

    monitor = SimpleNamespace(ops_lock=threading.RLock())
    db = DBManager(f"sqlite:///{tmp_path / 'slow-review.sqlite'}")
    exchange = FakeTestnet(bid=10)
    settings = SimpleNamespace(meta_mode_enabled=True, meta_max_active=1, alerts_enabled=False)
    clock_now = [datetime(2026, 1, 1, tzinfo=timezone.utc)]
    core = MetaCore(db, exchange, settings=settings, monitor=monitor,
        clock=lambda: clock_now[0])
    exchange.clock = lambda: clock_now[0]
    goal = core.create_goal(100, 10, profile="RAPIDO")
    now = datetime(2026, 1, 1)
    with db.engine.begin() as conn:
        grid_id = conn.execute(db.grids.insert().values(symbol="XRPUSDT", range_low=9, range_high=11,
            n_levels=1, capital_total=100, reserve=0, status="ACTIVE", created_at=now,
            environment="testnet", strategy="simple", params="{}", dust_qty="0")).inserted_primary_key[0]
        conn.execute(db.grid_levels.insert().values(grid_id=grid_id, level_idx=0, price=10, capital=100,
            state="HOLDING", held_qty=10, cycles_completed=0, pnl=0, fee_paid=0, updated_at=now))
        run_id = conn.execute(db.meta_runs.insert().values(meta_id=goal["id"], status="ACTIVE",
            started_at=now - timedelta(days=1), realized_extra_usdt=0)).inserted_primary_key[0]
        conn.execute(db.meta_goals.update().where(db.meta_goals.c.id == goal["id"]).values(
            status="ACTIVE", started_at=now - timedelta(days=1)))
        conn.execute(db.meta_piece_links.insert().values(meta_id=goal["id"], run_id=run_id,
            grid_id=grid_id, piece_type="grid", origin_grid_id=grid_id, created_at=now))
    db.add_grid_event(run_id=None, source="MONITOR", ts=now, grid_id=grid_id, level_idx=0,
        event_type="BUY_FILLED", details={"cummulative_quote_qty": "100", "fee_usdt": "0"})

    class ClosingEngine:
        def close_grid(self, close_grid_id, _mode, **_kwargs):
            db.update_grid(close_grid_id, status="CLOSED")
            db.update_level(close_grid_id, 0, state="DONE", held_qty=0)
            db.add_grid_event(run_id=None, source="CLI", ts=now, grid_id=close_grid_id,
                event_type="CELL_LIQUIDATED", details={"cash_proceeds_net": "81.9", "fee_usdt": "0"})
            return {"status": "CLOSED"}

    core.engine = ClosingEngine()
    from grid.monitor import GridMonitor
    grid_monitor = GridMonitor(db, exchange, core.engine, settings,
        clock=lambda: datetime(2026, 1, 1, tzinfo=timezone.utc))
    grid_monitor.ops_lock = monitor.ops_lock
    grid_monitor._run_once_locked = lambda _trigger: {"status": "MONITOR_RAN"}
    core.monitor = grid_monitor
    operator = MetaOperator(core, db, None, core.engine, settings)
    analysis_entered, allow_analysis_to_finish = threading.Event(), threading.Event()

    def slow_plan(_goal):
        analysis_entered.set()
        assert allow_analysis_to_finish.wait(5)
        return {"pieces": [], "alternatives": [], "candidates": [], "shadow_decision": None}

    operator._build_plan = slow_plan
    result_holder = []
    review = threading.Thread(target=lambda: result_holder.append(operator.review_once(goal["id"])))
    review.start()
    assert analysis_entered.wait(2)
    assert grid_monitor.run_once() == {"status": "MONITOR_RAN"}
    # A cadence arriving during a slow pass is logged and does not start a second analysis.
    skipped = operator.review_once(goal["id"])
    assert skipped["decision"] == "no_hacer_nada"
    assert any(row["event_type"] == "META_OPERATOR_REVIEW_SKIPPED" for row in core.ledger(goal["id"]))
    exchange.bid = 8.2  # La pérdida máxima aparece mientras el análisis del operador sigue lento.
    clock_now[0] += timedelta(minutes=1)
    core.run_once()
    clock_now[0] += timedelta(minutes=1)
    core.run_once()
    assert core.get_goal(goal["id"])["status"] == "CORTADA_POR_PERDIDA"
    assert db.get_grid(grid_id)["status"] == "CLOSED"
    allow_analysis_to_finish.set()
    review.join(timeout=5)
    assert not review.is_alive()
    assert result_holder[0]["decision"] == "no_hacer_nada"


def test_75_percent_loss_warning_blocks_new_pieces_without_closing(tmp_path):
    db, core = _core(tmp_path, exchange=FakeTestnet(bid=9.35))
    goal = core.create_goal(100, 10)
    now = datetime(2026, 1, 1)
    with db.engine.begin() as conn:
        grid_id = conn.execute(db.grids.insert().values(symbol="XRPUSDT", range_low=1, range_high=2,
            n_levels=1, capital_total=100, reserve=0, status="ACTIVE", created_at=now,
            environment="testnet", strategy="simple", params="{}", dust_qty="0")).inserted_primary_key[0]
        conn.execute(db.grid_levels.insert().values(grid_id=grid_id, level_idx=0, price=10, capital=100,
            state="HOLDING", held_qty=10, cycles_completed=0, pnl=0, fee_paid=0, updated_at=now))
        run_id = conn.execute(db.meta_runs.insert().values(meta_id=goal["id"], status="ACTIVE",
            started_at=now, realized_extra_usdt=0)).inserted_primary_key[0]
        conn.execute(db.meta_goals.update().where(db.meta_goals.c.id == goal["id"]).values(
            status="ACTIVE", started_at=datetime(2025, 12, 15)))
        conn.execute(db.meta_piece_links.insert().values(meta_id=goal["id"], run_id=run_id,
            grid_id=grid_id, piece_type="grid", origin_grid_id=grid_id, created_at=now))
    db.add_grid_event(run_id=None, source="MONITOR", ts=now, grid_id=grid_id, event_type="BUY_FILLED",
        details={"cummulative_quote_qty": "100", "fee_usdt": "0"})
    core.run_once()
    assert core.get_goal(goal["id"])["status"] == "PIECES_BLOCKED"
    assert db.get_grid(grid_id)["status"] == "ACTIVE"
    with pytest.raises(ValueError, match="meta activa"):
        core.create_goal(100, 10)
    with pytest.raises(ValueError, match="meta activa"):
        core.link_grid(goal["id"], grid_id)


def test_deadline_marks_meta_expired(tmp_path):
    db, core = _core(tmp_path)
    goal = core.create_goal(100, 10)
    started = datetime(2025, 11, 1)
    with db.engine.begin() as conn:
        conn.execute(db.meta_goals.update().where(db.meta_goals.c.id == goal["id"]).values(
            status="ACTIVE", started_at=started))
        conn.execute(db.meta_runs.insert().values(meta_id=goal["id"], status="ACTIVE",
            started_at=started, realized_extra_usdt=0))
    core.engine = SimpleNamespace(close_grid=lambda *_args, **_kwargs: {"status": "CLOSED"})
    core.run_once()
    assert core.get_goal(goal["id"])["status"] == "VENCIDA"


def test_target_met_pauses_grid_cancels_only_buys_and_tracks_extra(tmp_path):
    clock_now = [datetime(2026, 1, 1, tzinfo=timezone.utc)]
    db, core = _core(tmp_path, exchange=FakeTestnet(bid=11))
    core.clock = lambda: clock_now[0]
    goal = core.create_goal(100, 10)
    now = clock_now[0].replace(tzinfo=None)
    with db.engine.begin() as conn:
        grid_id = conn.execute(db.grids.insert().values(symbol="XRPUSDT", range_low=1, range_high=2,
            n_levels=2, capital_total=100, reserve=0, status="ACTIVE", created_at=now,
            environment="testnet", strategy="simple", params="{}", dust_qty="0")).inserted_primary_key[0]
        conn.execute(db.grid_levels.insert().values(grid_id=grid_id, level_idx=0, price=9, capital=50,
            state="IDLE", held_qty=0, cycles_completed=1, pnl=0, fee_paid=0,
            updated_at=now))
        conn.execute(db.grid_levels.insert().values(grid_id=grid_id, level_idx=1, price=10, capital=50,
            state="SELL_OPEN", held_qty=1, entry_price=10, sell_price=11, cycles_completed=0, pnl=0, fee_paid=0,
            order_id=22, updated_at=now))
        run_id = conn.execute(db.meta_runs.insert().values(meta_id=goal["id"], status="ACTIVE",
            started_at=now, realized_extra_usdt=0)).inserted_primary_key[0]
        conn.execute(db.meta_goals.update().where(db.meta_goals.c.id == goal["id"]).values(
            status="ACTIVE", started_at=now))
        conn.execute(db.meta_piece_links.insert().values(meta_id=goal["id"], run_id=run_id,
            grid_id=grid_id, piece_type="grid", origin_grid_id=grid_id, created_at=now))
    db.add_grid_event(run_id=None, source="MONITOR", ts=now, grid_id=grid_id, event_type="BUY_FILLED",
        details={"cummulative_quote_qty": "100", "fee_usdt": "0"})
    db.add_grid_event(run_id=None, source="MONITOR", ts=now, grid_id=grid_id, level_idx=0, event_type="SELL_FILLED",
        details={"cummulative_quote_qty": "110", "fee_usdt": "0"})

    class TargetEngine:
        def __init__(self): self.calls = []
        def cancel_grid_orders(self, grid_id, *, finalize, buy_only):
            self.calls.append((grid_id, finalize, buy_only))
            return {"cancel_errors": [], "pending_order_ids": []}

    engine = TargetEngine()
    core.engine = engine
    core.run_once()
    assert engine.calls == [(grid_id, False, True)]
    assert db.get_grid(grid_id)["status"] == "PAUSED"
    assert core.get_goal(goal["id"])["status"] == "SURPLUS_WAIT"
    db.add_grid_event(run_id=None, source="MONITOR", ts=(now + timedelta(minutes=1)),
        grid_id=grid_id, level_idx=1, event_type="SELL_FILLED",
        details={"cummulative_quote_qty": "11.5", "fee_usdt": "0"})
    db.update_level(grid_id, 1, state="DONE", held_qty=0, order_id=None)
    clock_now[0] += timedelta(minutes=2)
    core.run_once()
    with db.engine.connect() as conn:
        run = conn.execute(db.meta_runs.select().where(db.meta_runs.c.id == run_id)).mappings().one()
    assert run["realized_extra_usdt"] == 11.5
    assert core.get_goal(goal["id"])["status"] == "CUMPLIDA"


@pytest.mark.parametrize("mode", ["liquidate", "repository"])
def test_cancel_requires_plan_confirmation_and_closes_only_linked_grid(tmp_path, mode):
    db, core = _core(tmp_path)
    goal = core.create_goal(100, 10)
    now = datetime(2026, 1, 1)
    with db.engine.begin() as conn:
        meta_id = goal["id"]
        grid_id = conn.execute(db.grids.insert().values(symbol="XRPUSDT", range_low=1, range_high=2,
            n_levels=1, capital_total=100, reserve=0, status="ACTIVE", created_at=now,
            environment="testnet", strategy="simple", params="{}", dust_qty="0")).inserted_primary_key[0]
        outsider_id = conn.execute(db.grids.insert().values(symbol="BTCUSDT", range_low=1, range_high=2,
            n_levels=1, capital_total=100, reserve=0, status="ACTIVE", created_at=now,
            environment="testnet", strategy="simple", params="{}", dust_qty="0")).inserted_primary_key[0]
        conn.execute(db.meta_goals.update().where(db.meta_goals.c.id == meta_id).values(status="ACTIVE"))
        run_id = conn.execute(db.meta_runs.insert().values(meta_id=meta_id, status="ACTIVE",
            started_at=now, realized_extra_usdt=0)).inserted_primary_key[0]
        conn.execute(db.meta_piece_links.insert().values(meta_id=meta_id, run_id=run_id,
            grid_id=grid_id, piece_type="grid", origin_grid_id=grid_id, created_at=now))

    class ClosingEngine:
        def __init__(self): self.closed = []
        def close_grid(self, grid_id, close_mode, **kwargs):
            self.closed.append((grid_id, close_mode, kwargs.get("meta_id")))
            db.update_grid(grid_id, status="CLOSED")
            return {"status": "CLOSED"}

    engine = ClosingEngine()
    core.engine = engine
    plan = core.cancellation_plan(meta_id)
    with pytest.raises(ValueError, match="confirm=true"):
        core.cancel(meta_id, mode, False, plan["plan_id"])
    with pytest.raises(ValueError, match="Plan de cancelación"):
        core.cancel(meta_id, mode, True, "wrong-plan")
    result = core.cancel(meta_id, mode, True, plan["plan_id"])
    assert result["status"] == "CANCELADA"
    assert engine.closed == [(grid_id, mode, meta_id if mode == "repository" else None)]
    assert db.get_grid(outsider_id)["status"] == "ACTIVE"


def test_meta_telegram_unknown_alert_uses_30_minute_dispatcher_cooldown():
    class AlertDB:
        def __init__(self):
            self.state = {"meta_last_event_id": 0}
            self.events = [{"id": 1, "meta_id": 5, "event_type": "META_UNKNOWN",
                            "details": {"error": "BOOK_ONE_SIDED"}}]
            self.logs = []
        def get_alert_state(self, key): return self.state.get(key)
        def set_alert_state(self, key, value): self.state[key] = value
        def list_meta_ledger_since(self, last_id, limit=5000): return [row for row in self.events if row["id"] > last_id]
        def get_last_alert_sent_at(self, key): return next((row["ts"] for row in reversed(self.logs) if row["key"] == key), None)
        def count_alert_log_since(self, kind, since): return len(self.logs)
        def add_alert_log(self, **row): self.logs.append({**row, "key": row["alert_key"]})
    class Sender:
        def __init__(self): self.messages = []
        def send(self, message): self.messages.append(message); return {"ok": True}

    now = [datetime(2026, 1, 1, tzinfo=timezone.utc)]
    db, sender = AlertDB(), Sender()
    dispatcher = AlertDispatcher(db, lambda: None, clock=lambda: now[0], monotonic=lambda: now[0].timestamp())
    config = SimpleNamespace(alerts_cooldown_min=60, alerts_daily_cap=30)
    assert dispatcher._process_meta_events(sender, config, now[0]) == 1
    db.events.append({"id": 2, "meta_id": 5, "event_type": "META_UNKNOWN", "details": {"error": "still_unknown"}})
    now[0] += timedelta(minutes=29)
    assert dispatcher._process_meta_events(sender, config, now[0]) == 0
    db.events.append({"id": 3, "meta_id": 5, "event_type": "META_UNKNOWN", "details": {"error": "still_unknown"}})
    now[0] += timedelta(minutes=1)
    assert dispatcher._process_meta_events(sender, config, now[0]) == 1
    assert len(sender.messages) == 2



def test_meta_analysis_complete_alert_is_not_throttled_across_different_jobs():
    """Cada corrida de análisis es un hecho propio (lleva job_id): no debe
    enfriarse por el cooldown genérico de 60 minutos entre análisis distintos."""
    class AlertDB:
        def __init__(self):
            self.state = {"meta_last_event_id": 0}
            self.events = []
            self.logs = []
        def get_alert_state(self, key): return self.state.get(key)
        def set_alert_state(self, key, value): self.state[key] = value
        def list_meta_ledger_since(self, last_id, limit=5000): return [row for row in self.events if row["id"] > last_id]
        def get_last_alert_sent_at(self, key): return next((row["ts"] for row in reversed(self.logs) if row["key"] == key), None)
        def count_alert_log_since(self, kind, since): return len(self.logs)
        def add_alert_log(self, **row): self.logs.append({**row, "key": row["alert_key"]})
    class Sender:
        def __init__(self): self.messages = []
        def send(self, message): self.messages.append(message); return {"ok": True}

    now = [datetime(2026, 1, 1, tzinfo=timezone.utc)]
    db, sender = AlertDB(), Sender()
    dispatcher = AlertDispatcher(db, lambda: None, clock=lambda: now[0], monotonic=lambda: now[0].timestamp())
    config = SimpleNamespace(alerts_cooldown_min=60, alerts_daily_cap=30)
    db.events.append({"id": 1, "meta_id": 1, "event_type": "META_ANALYSIS_COMPLETE",
                       "details": {"job_id": "job-a", "analysis_seconds": 1080.2}})
    assert dispatcher._process_meta_events(sender, config, now[0]) == 1
    now[0] += timedelta(minutes=2)
    db.events.append({"id": 2, "meta_id": 1, "event_type": "META_ANALYSIS_COMPLETE",
                       "details": {"job_id": "job-b", "analysis_seconds": 60.0}})
    assert dispatcher._process_meta_events(sender, config, now[0]) == 1
    assert len(sender.messages) == 2
    assert "analysis_seconds: 1080.2" in sender.messages[0]


def test_meta_analysis_failed_alert_is_sent():
    class AlertDB:
        def __init__(self):
            self.state = {"meta_last_event_id": 0}
            self.events = [{"id": 1, "meta_id": 1, "event_type": "META_ANALYSIS_FAILED",
                            "details": {"job_id": "job-x", "error": "Ninguna moneda cumple los límites del perfil."}}]
            self.logs = []
        def get_alert_state(self, key): return self.state.get(key)
        def set_alert_state(self, key, value): self.state[key] = value
        def list_meta_ledger_since(self, last_id, limit=5000): return [row for row in self.events if row["id"] > last_id]
        def get_last_alert_sent_at(self, key): return next((row["ts"] for row in reversed(self.logs) if row["key"] == key), None)
        def count_alert_log_since(self, kind, since): return len(self.logs)
        def add_alert_log(self, **row): self.logs.append({**row, "key": row["alert_key"]})
    class Sender:
        def __init__(self): self.messages = []
        def send(self, message): self.messages.append(message); return {"ok": True}

    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    db, sender = AlertDB(), Sender()
    dispatcher = AlertDispatcher(db, lambda: None, clock=lambda: now, monotonic=lambda: now.timestamp())
    config = SimpleNamespace(alerts_cooldown_min=60, alerts_daily_cap=30)
    assert dispatcher._process_meta_events(sender, config, now) == 1
    assert "Motivo: Ninguna moneda cumple" in sender.messages[0]


def test_ledger_page_hides_valuation_rows_by_default_and_paginates(tmp_path):
    db, core = _core(tmp_path)
    goal = core.create_goal(100, 10)
    for i in range(3):
        core._ledger(goal["id"], None, "META_VALUATION", {"status": "KNOWN", "tick": i})
    core._ledger(goal["id"], None, "META_PIECE_LINKED", {"piece_type": "grid"})
    hidden_page = core.ledger_page(goal["id"], page=1, page_size=50)
    types = {row["event_type"] for row in hidden_page["events"]}
    assert "META_VALUATION" not in types
    assert "META_PIECE_LINKED" in types
    assert "META_CREATED" in types
    assert hidden_page["total"] == 2  # META_CREATED + META_PIECE_LINKED, not the 3 valuations
    shown_page = core.ledger_page(goal["id"], page=1, page_size=50, include_valuation=True)
    assert shown_page["total"] == 5
    first_page = core.ledger_page(goal["id"], page=1, page_size=2, include_valuation=True)
    assert len(first_page["events"]) == 2
    assert first_page["total"] == 5
    second_page = core.ledger_page(goal["id"], page=2, page_size=2, include_valuation=True)
    assert len(second_page["events"]) == 2
    assert {row["id"] for row in first_page["events"]}.isdisjoint({row["id"] for row in second_page["events"]})


def test_list_runs_exposes_control_plan_and_result(tmp_path):
    from sqlalchemy import select
    db, core = _core(tmp_path)
    goal = core.create_goal(100, 10)
    core.start_goal(goal["id"])
    with db.engine.connect() as conn:
        run_id = conn.execute(select(db.meta_runs.c.id).where(
            db.meta_runs.c.meta_id == goal["id"])).scalar_one()
    with db.engine.begin() as conn:
        conn.execute(db.meta_runs.update().where(db.meta_runs.c.id == run_id).values(
            control_plan_json='{"symbol": "XRPUSDT", "capital_usdt": 100}'))
    runs = core.list_runs(goal["id"])
    assert len(runs) == 1
    assert runs[0]["control_plan"] == {"symbol": "XRPUSDT", "capital_usdt": 100}
    assert runs[0]["control_result"] is None
    assert runs[0]["status"] == "ACTIVE"


def test_discard_goal_only_works_on_draft_and_does_not_touch_exchange(tmp_path):
    db, core = _core(tmp_path)
    goal = core.create_goal(100, 10)
    assert goal["status"] == "DRAFT"
    discarded = core.discard_goal(goal["id"])
    assert discarded["status"] == "DESCARTADA"
    assert core.ledger(goal["id"])[-1]["event_type"] == "META_DISCARDED"
    with pytest.raises(ValueError, match="DRAFT"):
        core.discard_goal(goal["id"])
    # A discarded DRAFT must free the "one active goal" slot for a new one.
    second = core.create_goal(100, 10)
    assert second["id"] != goal["id"]


def test_compare_control_replays_exact_run_dates_and_caches_result(tmp_path):
    import json
    import pandas as pd
    import numpy as np
    from sqlalchemy import select
    from grid.meta_operator import MetaOperator

    db, core = _core(tmp_path)
    goal = core.create_goal(100, 10)
    core.start_goal(goal["id"])
    with db.engine.connect() as conn:
        run_id = conn.execute(select(db.meta_runs.c.id).where(
            db.meta_runs.c.meta_id == goal["id"])).scalar_one()
    started = datetime(2026, 1, 1, tzinfo=timezone.utc)
    finished = datetime(2026, 1, 3, tzinfo=timezone.utc)
    with db.engine.begin() as conn:
        conn.execute(db.meta_runs.update().where(db.meta_runs.c.id == run_id).values(
            started_at=started.replace(tzinfo=None), finished_at=finished.replace(tzinfo=None),
            control_plan_json=json.dumps({"symbol": "XRPUSDT", "capital_usdt": 1000,
                "strategy": "smart", "range_low": 90, "range_high": 110, "n_levels": 4})))

    n_candles = 600
    prices = 100 + 2 * np.sin(np.linspace(0, 12, n_candles))
    calls = []

    class Client:
        def get_historical_klines(self, symbol, interval, days, start_time=None):
            calls.append((symbol, interval, days, start_time))
            return pd.DataFrame({
                "timestamp": pd.date_range(started, periods=n_candles, freq="5min", tz="UTC"),
                "open": prices, "high": prices + 1, "low": prices - 1, "close": prices})

        def get_symbol_info(self, _symbol):
            raise RuntimeError("sin filtros en esta prueba")

    class Scanner:
        client = Client()

    operator = MetaOperator(core, db, Scanner(), None, core.settings)
    result = operator.compare_control(run_id)
    assert result["control_plan"]["symbol"] == "XRPUSDT"
    assert result["control_result"] is not None
    assert "result_pct" in result["control_result"]
    assert calls[0][3] == started.replace(tzinfo=None) or calls[0][3] == started

    # Second call must reuse the cached result (no second klines request).
    result_again = operator.compare_control(run_id)
    assert result_again["control_result"] == result["control_result"]
    assert len(calls) == 1


def test_compare_control_shows_only_plan_while_run_is_active(tmp_path):
    import json
    db, core = _core(tmp_path)
    goal = core.create_goal(100, 10)
    core.start_goal(goal["id"])
    from sqlalchemy import select
    with db.engine.connect() as conn:
        run_id = conn.execute(select(db.meta_runs.c.id).where(
            db.meta_runs.c.meta_id == goal["id"])).scalar_one()
    with db.engine.begin() as conn:
        conn.execute(db.meta_runs.update().where(db.meta_runs.c.id == run_id).values(
            control_plan_json=json.dumps({"symbol": "XRPUSDT", "capital_usdt": 100,
                "strategy": "smart", "range_low": 90, "range_high": 110, "n_levels": 5})))
    from grid.meta_operator import MetaOperator
    operator = MetaOperator(core, db, None, None, core.settings)
    result = operator.compare_control(run_id)
    assert result["control_plan"] is not None
    assert result["control_result"] is None
    assert "activa" in result["note"]
