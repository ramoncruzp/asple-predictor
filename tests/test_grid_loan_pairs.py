from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
import multiprocessing
import random
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import api.routes.grids as grids_api
from api.routes.grids import PairOpenRequest
from data.exchange_filters import SymbolFilters
from tests.grid_fakes import fake_symbol_info


def _pair_rows(differences, *, treated=None, orphan=(), open_arm=()):
    now = datetime(2026, 10, 8, tzinfo=timezone.utc)
    rows, pnl, loans = [], {}, {}
    treated = set(range(len(differences))) if treated is None else set(treated)
    for index, difference in enumerate(differences):
        pair_id = f"pair-{index:03d}"
        for arm, value in (("pair_loans", difference), ("pair_control", 0.0)):
            grid_id = index * 2 + (1 if arm == "pair_loans" else 2)
            status = "ACTIVE" if index in open_arm else "CLOSED"
            params = {"pair_id": pair_id, "pair_arm": arm}
            if index in orphan:
                params["pair_status"] = "orphan"
            rows.append({"id": grid_id, "symbol": "ADAUSDT", "status": status,
                         "capital_total": 100.0, "created_at": now - timedelta(days=3),
                         "closed_at": now - timedelta(days=1), "params": params})
            pnl[grid_id] = 2 * value
            loans[grid_id] = [{"id": grid_id}] if arm == "pair_loans" and index in treated else []
    class DB:
        def get_grid_levels(self, grid_id):
            return [{"pnl": pnl[grid_id]}]
        def list_grid_loans(self, grid_id):
            return loans[grid_id]
    return rows, DB()


def test_paired_summary_calculates_mean_sample_sd_t_and_wins(monkeypatch):
    rows, db = _pair_rows([1.0, 2.0, 3.0, 4.0])
    monkeypatch.setattr(grids_api, "_all_grids", lambda _db: rows)
    result = grids_api._paired_loan_summary(db)
    assert result["n_pairs"] == 4
    assert result["mean_d"] == pytest.approx(2.5)
    assert result["sd_d"] == pytest.approx(1.2909944487)
    assert result["t_paired"] == pytest.approx(3.8729833462)
    assert result["wins"] == 4


def test_paired_summary_excludes_orphan_and_open_pairs_with_reasons(monkeypatch):
    rows, db = _pair_rows([1.0, 2.0, 3.0], orphan={0}, open_arm={1})
    rows[:] = [row for row in rows
               if not (row["params"]["pair_id"] == "pair-000"
                       and row["params"]["pair_arm"] == "pair_control")]
    monkeypatch.setattr(grids_api, "_all_grids", lambda _db: rows)
    result = grids_api._paired_loan_summary(db)
    assert result["n_pairs"] == 1
    assert result["orphan_pairs"] == 1
    assert {row["reason"] for row in result["excluded_pairs"]} == {"par huérfano", "brazos no cerrados"}


@pytest.mark.parametrize("change, expected", [
    (lambda grid: grid.update({"closed_at": None}), "fechas ausentes"),
    (lambda grid: grid.update({"capital_total": 0}), "capital no válido"),
    (lambda grid: grid.update({"closed_at": grid["created_at"]}), "duración no positiva"),
])
def test_paired_summary_excludes_unusable_pair_metrics(monkeypatch, change, expected):
    rows, db = _pair_rows([1.0])
    change(rows[0])
    monkeypatch.setattr(grids_api, "_all_grids", lambda _db: rows)
    result = grids_api._paired_loan_summary(db)
    assert result["n_pairs"] == 0
    assert result["excluded_pairs"] == [{"pair_id": "pair-000", "reason": expected}]


def test_paired_summary_excludes_unknown_realized_pnl(monkeypatch):
    rows, db = _pair_rows([1.0])
    db.get_grid_levels = lambda _grid_id: [{"pnl": None}]
    monkeypatch.setattr(grids_api, "_all_grids", lambda _db: rows)
    result = grids_api._paired_loan_summary(db)
    assert result["n_pairs"] == 0
    assert result["excluded_pairs"][0]["reason"] == "P&L desconocido"


def test_paired_summary_requires_minimum_n_and_real_loans(monkeypatch):
    rows, db = _pair_rows([1.0] * 14)
    monkeypatch.setattr(grids_api, "_all_grids", lambda _db: rows)
    small = grids_api._paired_loan_summary(db)
    assert small["conclusive"] is False
    assert "muestra insuficiente (n<15)" in small["reason"]

    rows, db = _pair_rows([1.0] * 20, treated=())
    monkeypatch.setattr(grids_api, "_all_grids", lambda _db: rows)
    untreated = grids_api._paired_loan_summary(db)
    assert untreated["conclusive"] is False
    assert "pocos pares con préstamos reales" in untreated["reason"]


def test_paired_summary_can_be_conclusive_only_with_treated_pairs(monkeypatch):
    rows, db = _pair_rows([1.0] * 20, treated=range(10))
    monkeypatch.setattr(grids_api, "_all_grids", lambda _db: rows)
    result = grids_api._paired_loan_summary(db)
    assert result["conclusive"] is True
    assert result["treated_pairs"] == 10
    assert result["n_treated"] == 10
    assert result["detectable_effect_80pct"] == 0


class _PairDB:
    engine = SimpleNamespace(url="sqlite:///pair-test.sqlite")
    def __init__(self, count=0):
        self.count = count
        self.grids = {}
        self.events = []
        self.closed = []
    def count_open_grids(self): return self.count
    def get_coin(self, symbol): return {"symbol": symbol, "active": 1}
    def get_readiness(self, symbol): return {"state": "lista"}
    def update_grid(self, grid_id, **fields):
        self.grids[grid_id].update(fields)
        return self.grids[grid_id]
    def merge_grid_params(self, grid_id, updates, *, allowed):
        assert set(updates) <= set(allowed)
        self.grids[grid_id].setdefault("params", {}).update(updates)
        return self.grids[grid_id]["params"]
    def get_grid(self, grid_id): return self.grids.get(grid_id)
    def add_grid_event(self, **event): self.events.append(event)


def _pair_request(db, *, count=0):
    settings = SimpleNamespace(max_grids_simultaneos=5, scanner_timeout_seconds=60,
                               same_coin_sell_tolerance_pct=0.05)
    filters = SymbolFilters.from_symbol_info(fake_symbol_info())
    service = SimpleNamespace(clock=lambda: 0,
                              _market=lambda *_args: ({}, filters))
    app = SimpleNamespace(state=SimpleNamespace(
        db=db, settings=settings, grid_scan_service=service, vol_registry=None))
    return SimpleNamespace(app=app)


def _sell_prices(body):
    low, high = body.range_low, body.range_high
    return [low + (high - low) * Decimal(index) / Decimal(5) for index in range(1, 6)]


def test_pair_dry_run_returns_two_plans_and_never_creates(monkeypatch):
    db = _PairDB()
    request = _pair_request(db)
    monkeypatch.setattr(grids_api, "_authorize", lambda _request: None)
    monkeypatch.setattr(grids_api, "coin_is_ready", lambda *_args: True)
    creates = []
    def fake_open(_request, arm_body, *, pair_metadata=None, pair_created_callback=None):
        assert arm_body.strategy == "smart"
        assert arm_body.capital == Decimal("1000")
        assert arm_body.n_levels == 5
        creates.append((arm_body.dry_run, pair_metadata, arm_body.params))
        return {"dry_run": True, "filters": {"tick_size": "0.01"},
                "cells": [{"sell_price": str(value)} for value in _sell_prices(arm_body)]}
    monkeypatch.setattr(grids_api, "_open_grid", fake_open)
    body = PairOpenRequest(symbol="ADAUSDT", capital=1000, range_low=90, range_high=110,
                           n_levels=5, dry_run=True, pair_seed=23, pair_id="fixed-pair")
    result = grids_api.open_loan_pair(request, body)
    assert result["dry_run"] is True
    assert result["pair_id"] == "fixed-pair"
    assert result["pair_seed"] == 23
    assert len(result["arms"]) == 2
    assert result["pair_offset_pct"] >= 0.10
    assert len(creates) == 2
    assert all(call[0] is True and call[1] is None for call in creates)
    assert db.grids == {} and db.events == []


def test_pair_plan_tries_next_offset_after_sell_conflict(monkeypatch):
    db = _PairDB()
    request = _pair_request(db)
    monkeypatch.setattr(grids_api, "_authorize", lambda _request: None)
    monkeypatch.setattr(grids_api, "coin_is_ready", lambda *_args: True)
    seen = []
    def fake_open(_request, arm_body, *, pair_metadata=None, pair_created_callback=None):
        seen.append(arm_body.range_low)
        if arm_body.range_low == Decimal("90.090"):
            raise HTTPException(409, "sell level conflict: simulated")
        return {"filters": {"tick_size": "0.01"},
                "cells": [{"sell_price": str(value)} for value in _sell_prices(arm_body)]}
    monkeypatch.setattr(grids_api, "_open_grid", fake_open)
    body = PairOpenRequest(symbol="ADAUSDT", capital=1000, range_low=90, range_high=110,
                           n_levels=5, dry_run=True, pair_seed=23)
    result = grids_api.open_loan_pair(request, body)
    assert result["pair_offset_pct"] == 0.15
    assert Decimal("90.090") in seen and Decimal("90.135") in seen


def test_pair_creation_persists_pair_metadata_for_both_arms(monkeypatch):
    db = _PairDB()
    request = _pair_request(db)
    monkeypatch.setattr(grids_api, "_authorize", lambda _request: None)
    monkeypatch.setattr(grids_api, "coin_is_ready", lambda *_args: True)
    monkeypatch.setattr(grids_api, "create_loan_pair", lambda _db, create: create())
    next_id = 0
    created_bodies = []
    first_conflict = True
    def fake_open(_request, arm_body, *, pair_metadata=None, pair_created_callback=None):
        nonlocal next_id, first_conflict
        if arm_body.dry_run:
            return {"dry_run": True, "filters": {"tick_size": "0.01"},
                    "cells": [{"sell_price": str(value)} for value in _sell_prices(arm_body)]}
        if pair_metadata["pair_arm"] != pair_metadata["pair_first_arm"] and first_conflict:
            first_conflict = False
            raise HTTPException(409, "sell level conflict: simulated at first candidate")
        next_id += 1
        created_bodies.append((arm_body, pair_metadata))
        db.grids[next_id] = {"id": next_id, "params": {**arm_body.params}}
        if pair_created_callback is not None:
            pair_created_callback(next_id)
        grids_api._persist_pair_metadata(db, {"id": next_id}, pair_metadata)
        return {"grid_id": next_id, "status": "ACTIVE", **pair_metadata}
    monkeypatch.setattr(grids_api, "_open_grid", fake_open)
    body = PairOpenRequest(symbol="ADAUSDT", capital=1000, range_low=90, range_high=110,
                           n_levels=5, dry_run=False, confirm=True, pair_seed=23, pair_id="pair-success")
    result = grids_api.open_loan_pair(request, body)
    assert len(result["grids"]) == 2
    assert [row["params"]["loans_group"] for row in db.grids.values()] == [
        item[1]["pair_arm"] for item in created_bodies]
    assert {row["params"]["pair_id"] for row in db.grids.values()} == {"pair-success"}
    loans_body = next(body for body, meta in created_bodies if meta["pair_arm"] == "pair_loans")
    control_body = next(body for body, meta in created_bodies if meta["pair_arm"] == "pair_control")
    assert loans_body.params["loans_enabled"] is True
    assert loans_body.params["loan_topup_pct"] == loans_body.params["loan_lender_max_pct"] == 70.0
    assert control_body.params["loans_enabled"] is False
    assert result["pair_offset_pct"] == 0.15


def test_pair_second_arm_failure_marks_first_orphan_without_closing(monkeypatch):
    db = _PairDB()
    request = _pair_request(db)
    monkeypatch.setattr(grids_api, "_authorize", lambda _request: None)
    monkeypatch.setattr(grids_api, "coin_is_ready", lambda *_args: True)
    monkeypatch.setattr(grids_api, "create_loan_pair", lambda _db, create: create())
    created = 0
    def fake_open(_request, arm_body, *, pair_metadata=None, pair_created_callback=None):
        nonlocal created
        if arm_body.dry_run:
            return {"dry_run": True, "filters": {"tick_size": "0.01"},
                    "cells": [{"sell_price": str(value)} for value in _sell_prices(arm_body)]}
        if not created:
            created = 1
            db.grids[1] = {"id": 1, "params": dict(arm_body.params)}
            pair_created_callback(1)
            return {"grid_id": 1, "status": "ACTIVE", **pair_metadata}
        raise HTTPException(422, "second arm failure")
    monkeypatch.setattr(grids_api, "_open_grid", fake_open)
    body = PairOpenRequest(symbol="ADAUSDT", capital=1000, range_low=90, range_high=110,
                           n_levels=5, dry_run=False, confirm=True, pair_seed=23, pair_id="pair-orphan")
    with pytest.raises(HTTPException) as error:
        grids_api.open_loan_pair(request, body)
    assert error.value.status_code == 409
    assert "PAIR_ORPHAN" in error.value.detail and "grid_id(s)=1" in error.value.detail
    assert db.grids[1]["params"]["pair_status"] == "orphan"
    assert any(event["event_type"] == "PAIR_ORPHAN" for event in db.events)
    assert db.closed == []


def test_pair_rejects_insufficient_slots_and_functional_cell_capital(monkeypatch):
    monkeypatch.setattr(grids_api, "_authorize", lambda _request: None)
    monkeypatch.setattr(grids_api, "coin_is_ready", lambda *_args: True)
    body = PairOpenRequest(symbol="ADAUSDT", capital=1000, range_low=90, range_high=110,
                           n_levels=5, dry_run=True)
    with pytest.raises(HTTPException) as slots:
        grids_api.open_loan_pair(_pair_request(_PairDB(count=4)), body)
    assert slots.value.status_code == 409
    low_capital = body.model_copy(update={"capital": Decimal("10")})
    with pytest.raises(HTTPException) as floor:
        grids_api.open_loan_pair(_pair_request(_PairDB()), low_capital)
    assert floor.value.status_code == 422
    assert "piso funcional" in floor.value.detail


def test_pair_seed_is_reproducible_and_can_assign_either_first_arm():
    assert random.Random(23).choice(("pair_loans", "pair_control")) == random.Random(23).choice(
        ("pair_loans", "pair_control"))
    first_arms = {random.Random(seed).choice(("pair_loans", "pair_control")) for seed in range(20)}
    assert first_arms == {"pair_loans", "pair_control"}


def test_pair_api_dry_run_and_execution_reuse_grid_open_guards(tmp_path, monkeypatch):
    from tests.test_grids_api import app as make_test_app
    client, db, exchange = make_test_app(tmp_path)
    body = {"symbol": "XRPUSDT", "capital": 1000, "range_low": 90,
            "range_high": 110, "n_levels": 5}
    dry = client.post("/api/grids/pair", json=body)
    assert dry.status_code == 200, dry.text
    assert len(dry.json()["arms"]) == 2
    assert dry.json()["pair_offset_pct"] >= 0.10
    assert exchange.create_calls == []

    monkeypatch.setattr(grids_api, "create_loan_pair", lambda _db, create: create())
    body.update({"dry_run": False, "confirm": True,
                 "pair_seed": dry.json()["pair_seed"], "pair_id": "api-pair-test"})
    opened = client.post("/api/grids/pair", json=body)
    assert opened.status_code == 200, opened.text
    assert len(opened.json()["grids"]) == 2
    grids = [db.get_grid(item["grid_id"]) for item in opened.json()["grids"]]
    params = [grid["params"] for grid in grids]
    assert {item["pair_id"] for item in params} == {"api-pair-test"}
    assert {item["loans_group"] for item in params} == {"pair_loans", "pair_control"}
    assert all(item["horizon_h"] == 4 for item in params)
    assert len(exchange.create_calls) > 0


def test_pair_api_orphan_is_recorded_without_closing_first_grid(tmp_path, monkeypatch):
    from tests.test_grids_api import app as make_test_app
    client, db, _exchange = make_test_app(tmp_path)
    monkeypatch.setattr(grids_api, "create_loan_pair", lambda _db, create: create())
    original_open = grids_api._open_grid
    def fail_second(request, body, *, pair_metadata=None, pair_created_callback=None):
        if not body.dry_run and pair_metadata["pair_arm"] != pair_metadata["pair_first_arm"]:
            raise HTTPException(422, "simulated second-arm failure")
        return original_open(request, body, pair_metadata=pair_metadata,
                             pair_created_callback=pair_created_callback)
    monkeypatch.setattr(grids_api, "_open_grid", fail_second)
    response = client.post("/api/grids/pair", json={
        "symbol": "XRPUSDT", "capital": 1000, "range_low": 90, "range_high": 110,
        "n_levels": 5, "dry_run": False, "confirm": True, "pair_seed": 23,
        "pair_id": "api-pair-orphan"})
    assert response.status_code == 409, response.text
    assert "PAIR_ORPHAN" in response.json()["detail"]
    grids = db.list_grids_by_status({"ACTIVE"})
    first = next(row for row in grids if row["params"].get("pair_id") == "api-pair-orphan")
    assert first["status"] == "ACTIVE"
    assert first["params"]["pair_status"] == "orphan"
    event = db.list_grid_events(grid_id=int(first["id"]), event_type="PAIR_ORPHAN")[0]
    assert event["source"] == "CLI"


def _pair_lock_worker(database_path, lock_path, pair_id, barrier):
    import sqlite3
    import time
    import grid.loan_cohorts as cohorts
    from types import SimpleNamespace
    cohorts._lock_file_path = lambda _db: Path(lock_path)
    class DB:
        engine = SimpleNamespace(url=f"sqlite:///{database_path}")
    db = DB()
    barrier.wait()
    def create():
        with sqlite3.connect(database_path) as conn:
            conn.execute("INSERT INTO pair_events(pair_id, arm) VALUES(?, 'first')", (pair_id,))
        time.sleep(.04)
        with sqlite3.connect(database_path) as conn:
            conn.execute("INSERT INTO pair_events(pair_id, arm) VALUES(?, 'second')", (pair_id,))
    cohorts.create_loan_pair(db, create)


def test_two_process_pair_creations_do_not_interleave(tmp_path, monkeypatch):
    database_path = tmp_path / "pairs.sqlite"
    lock_path = tmp_path / "pairs.lock"
    with sqlite3.connect(database_path) as conn:
        conn.execute("CREATE TABLE pair_events(id INTEGER PRIMARY KEY, pair_id TEXT, arm TEXT)")
    ctx = multiprocessing.get_context("spawn")
    barrier = ctx.Barrier(2)
    workers = [ctx.Process(target=_pair_lock_worker,
                           args=(str(database_path), str(lock_path), f"p{index}", barrier))
               for index in range(2)]
    for worker in workers: worker.start()
    for worker in workers:
        worker.join(15)
        assert worker.exitcode == 0
    with sqlite3.connect(database_path) as conn:
        events = conn.execute("SELECT pair_id, arm FROM pair_events ORDER BY id").fetchall()
    assert len(events) == 4
    assert events[0][0] == events[1][0] and events[2][0] == events[3][0]
    assert [event[1] for event in events] == ["first", "second", "first", "second"]


def test_pair_creation_fails_closed_when_process_lock_is_unavailable(monkeypatch):
    import grid.loan_cohorts as cohorts
    from contextlib import contextmanager
    db = SimpleNamespace(engine=SimpleNamespace(url="sqlite:///pair-lock-unavailable"))
    called = []
    @contextmanager
    def unavailable(_db):
        yield False
    monkeypatch.setattr(cohorts, "_process_creation_lock", unavailable)
    with pytest.raises(RuntimeError, match="candado entre procesos no disponible"):
        cohorts.create_loan_pair(db, lambda: called.append("created"))
    assert called == []


def test_grid_ctl_open_pair_defaults_to_dry_run_and_requires_confirm(tmp_path, monkeypatch, capsys):
    from scripts import grid_ctl
    from tests.test_grids_api import app as make_test_app
    client, db, exchange = make_test_app(tmp_path)
    context = {"settings": client.app.state.settings, "db": db,
               "exchange": exchange, "engine": client.app.state.grid_engine,
               "app": client.app}
    monkeypatch.setattr(grid_ctl, "build_context", lambda: context)
    args = ["open-pair", "--symbol", "XRPUSDT", "--range-low", "90",
            "--range-high", "110", "--n-levels", "5", "--capital-per-arm", "1000"]

    assert grid_ctl.main(args) == 0
    dry = json.loads(capsys.readouterr().out)
    assert dry["dry_run"] is True and len(dry["arms"]) == 2
    assert exchange.create_calls == [] and db.count_open_grids() == 0

    assert grid_ctl.main(args + ["--execute"]) == 2
    assert "--confirm" in capsys.readouterr().err
    assert db.count_open_grids() == 0


def test_grid_ctl_open_pair_confirm_creates_two_simulated_grids(tmp_path, monkeypatch, capsys):
    from scripts import grid_ctl
    from tests.test_grids_api import app as make_test_app
    client, db, exchange = make_test_app(tmp_path)
    context = {"settings": client.app.state.settings, "db": db,
               "exchange": exchange, "engine": client.app.state.grid_engine,
               "app": client.app}
    monkeypatch.setattr(grid_ctl, "build_context", lambda: context)
    args = ["open-pair", "--symbol", "XRPUSDT", "--range-low", "90",
            "--range-high", "110", "--n-levels", "5", "--capital-per-arm", "1000",
            "--execute", "--confirm", "--pair-seed", "23"]

    assert grid_ctl.main(args) == 0
    opened = json.loads(capsys.readouterr().out)
    assert opened["dry_run"] is False and len(opened["grids"]) == 2
    assert db.count_open_grids() == 2
    assert len(exchange.create_calls) > 0
