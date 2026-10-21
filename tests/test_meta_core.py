from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from database.db_manager import DBManager
from grid.meta import MetaCore, attributable_cash
from alerts.dispatcher import AlertDispatcher


class FakeTestnet:
    testnet = True

    def __init__(self, free=1000, bid=10):
        self.free, self.bid = free, bid

    def get_balance(self, asset=None):
        return {"USDT": {"free": self.free, "locked": 0}}

    def get_book_ticker(self, symbol):
        return {"bid_price": self.bid, "ask_price": self.bid + 0.01}


def _core(tmp_path, *, exchange=None, enabled=True, alert=None):
    db = DBManager(f"sqlite:///{tmp_path / 'meta.sqlite'}")
    settings = SimpleNamespace(meta_mode_enabled=enabled, meta_max_active=1, alerts_enabled=False)
    return db, MetaCore(db, exchange or FakeTestnet(), settings=settings, clock=lambda: datetime(2026, 1, 1, tzinfo=timezone.utc), alert=alert)


def test_attributable_cash_fixed_numbers():
    # 100 iniciales - 60 entregados + 70 devueltos - 1 de comisiones = 109.
    assert attributable_cash("100", "60", "70", "1") == Decimal("109")


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
    assert result["status"] == "UNKNOWN"
    assert core.ledger(goal["id"])[-1]["event_type"] == "META_UNKNOWN"
    assert alerts and alerts[0][0] == "META_UNKNOWN"
    # La repetición está limitada a una alerta por cada 30 minutos de estado desconocido.
    clock_now[0] += timedelta(minutes=29)
    core.valuation(goal["id"])
    assert len(alerts) == 1
    clock_now[0] += timedelta(minutes=1)
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

    core.engine = ClosingEngine()
    core.run_once()
    assert core.get_goal(goal["id"])["status"] == "CORTADA_POR_PERDIDA"
    assert any(kind == "META_LOSS_WARNING" for kind, _meta_id, _data in alerts)
    assert any(kind == "META_CORTADA_POR_PERDIDA" for kind, _meta_id, _data in alerts)


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

