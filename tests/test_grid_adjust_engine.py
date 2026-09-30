from __future__ import annotations

import hashlib
import threading
from decimal import Decimal

from tests.test_grid_engine import create, make_engine


def assert_adjust_invariants(engine, grid_id, before=None):
    db, exchange = engine.db, engine.exchange
    levels = db.get_grid_levels(grid_id)
    live = exchange.get_open_orders("XRPUSDT")
    owned = {int(row.get("order_id")): row for row in levels if row.get("order_id") is not None}
    assert all(int(order["order_id"]) in owned for order in live)
    assert len({int(order["order_id"]) for order in live}) == len(live)
    buy_cids = [row.get("buy_client_order_id") for row in levels
                if row["state"] == "BUY_OPEN" and row.get("order_id") is not None]
    assert len(buy_cids) == len(set(buy_cids))
    for row in levels:
        if row["state"] == "BUY_OPEN" and row.get("order_id") is not None:
            assert str(exchange.get_order("XRPUSDT", order_id=int(row["order_id"]))["status"]).upper() \
                in {"NEW", "PARTIALLY_FILLED"}
    if before:
        now = {int(row["level_idx"]): row for row in levels}
        for idx, old in before["pinned"].items():
            row = now[idx]
            for key in ("order_id", "sell_price", "entry_price", "held_qty"):
                assert row.get(key) == old.get(key)
        free_capital = sum(float(row["capital"]) for row in levels
                           if row["state"] in {"IDLE", "BUY_OPEN", "DONE"}
                           and float(row.get("held_qty") or 0) <= 0)
        assert abs(free_capital - before["free_capital"]) <= 1e-6
        assert float(db.get_grid(grid_id)["capital_total"]) == before["capital_total"]


def test_f2_canceled_replaced_buy_does_not_error_before_recovery():
    engine, db, exchange = make_engine(fee_rate="0", fee_asset="USDT")
    grid = create(engine)
    cell = db.get_grid_levels(grid["id"])[0]
    old_order_id = cell["order_id"]
    new_client_order_id = f"gA{grid['id']}L{cell['level_idx']}B0"

    # Simulate the crash window after write-ahead updates the CID and the
    # exchange cancels the old order, while order_id still references it.
    db.update_level(
        grid["id"], cell["level_idx"], client_order_id=new_client_order_id,
        buy_client_order_id=new_client_order_id,
    )
    exchange.cancel_order("XRPUSDT", old_order_id)

    engine.sync_grid(grid["id"])

    after_cancel = db.get_grid_levels(grid["id"])[cell["level_idx"]]
    assert after_cancel["state"] == "BUY_OPEN"
    assert after_cancel["order_id"] is None
    engine.sync_grid(grid["id"])

    recovered = db.get_grid_levels(grid["id"])[cell["level_idx"]]
    assert recovered["state"] == "BUY_OPEN"
    assert recovered["client_order_id"] == new_client_order_id
    assert recovered["order_id"] != old_order_id
    assert exchange.find_order_by_client_id("XRPUSDT", new_client_order_id)["order_id"] == recovered["order_id"]


def test_adjust_moves_all_free_cells_and_reconciles_orders():
    engine, db, exchange = make_engine(fee_rate="0", fee_asset="USDT")
    grid = create(engine)
    levels = db.get_grid_levels(grid["id"])
    before = {"pinned": {}, "free_capital": sum(row["capital"] for row in levels),
              "capital_total": db.get_grid(grid["id"])["capital_total"]}
    result = engine.adjust_grid(grid["id"], 95, 105, reason="test", details={"source": "CLI"})
    assert result["ok"] and result["changed"]
    assert db.get_grid(grid["id"])["range_low"] == 95
    assert db.get_grid(grid["id"])["range_high"] == 105
    assert_adjust_invariants(engine, grid["id"], before)


def test_adjust_changes_cell_count_by_appending_and_retiring_without_deleting_rows():
    engine, db, exchange = make_engine(fee_rate="0", fee_asset="USDT")
    grid = create(engine)
    assert engine.adjust_grid(grid["id"], 94, 106, 7)["ok"]
    rows = db.get_grid_levels(grid["id"])
    assert len(rows) == 7 and [row["level_idx"] for row in rows] == list(range(7))
    assert_adjust_invariants(engine, grid["id"])

    # A second adjustment can retire a free slot while retaining its row.
    assert engine.adjust_grid(grid["id"], 96, 104, 4)["ok"]
    rows = db.get_grid_levels(grid["id"])
    assert len(rows) == 7
    assert sum(row["state"] == "DONE" and row["capital"] == 0 for row in rows) == 3
    assert_adjust_invariants(engine, grid["id"])


def test_adjust_preserves_filled_cell_order_and_materializes_old_sell_geometry():
    engine, db, exchange = make_engine(fee_rate="0", fee_asset="USDT")
    grid = create(engine)
    pinned = db.get_grid_levels(grid["id"])[0]
    exchange.fill(pinned["order_id"])
    engine.sync_grid(grid["id"])
    pinned = db.get_grid_levels(grid["id"])[0]
    before = {"pinned": {0: dict(pinned)},
              "free_capital": sum(row["capital"] for row in db.get_grid_levels(grid["id"])
                                  if row["state"] in {"IDLE", "BUY_OPEN", "DONE"}
                                  and row["held_qty"] <= 0),
              "capital_total": db.get_grid(grid["id"])["capital_total"]}
    # Simulate a legacy null sell price; it must be resolved with old geometry.
    db.update_level(grid["id"], 4, sell_price=None)
    result = engine.adjust_grid(grid["id"], 94, 106, 5)
    assert result["ok"]
    after = db.get_grid_levels(grid["id"])[0]
    assert after["order_id"] == before["pinned"][0]["order_id"]
    assert after["sell_price"] == before["pinned"][0]["sell_price"]
    assert db.get_grid_levels(grid["id"])[4]["sell_price"] is not None
    assert_adjust_invariants(engine, grid["id"], before)


def test_adjust_retries_after_cancel_failure_without_orphaning_old_order():
    engine, db, exchange = make_engine(fee_rate="0", fee_asset="USDT")
    grid = create(engine)
    cancel = exchange.cancel_order
    exchange.cancel_order = lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("temporary"))
    try:
        engine.adjust_grid(grid["id"], 95, 105)
    except RuntimeError:
        pass
    else:
        raise AssertionError("cancel failure should leave the adjustment resumable")
    exchange.cancel_order = cancel
    assert engine.adjust_grid(grid["id"], 95, 105)["ok"]
    assert_adjust_invariants(engine, grid["id"])


def test_f3_crash_after_cancel_before_order_id_clear_is_resumable():
    engine, db, exchange = make_engine(fee_rate="0", fee_asset="USDT")
    grid = create(engine)
    update = db.update_level
    injected = {"done": False}
    def fail_clear(grid_id, level_idx, **fields):
        if fields == {"order_id": None} and not injected["done"]:
            injected["done"] = True
            raise RuntimeError("crash after cancel")
        return update(grid_id, level_idx, **fields)
    db.update_level = fail_clear
    try:
        engine.adjust_grid(grid["id"], 95, 105)
    except RuntimeError:
        pass
    else:
        raise AssertionError("injected post-cancel crash did not happen")
    db.update_level = update
    assert engine.adjust_grid(grid["id"], 95, 105)["ok"]
    assert_adjust_invariants(engine, grid["id"])


def test_f4_lost_place_response_recovers_existing_new_buy_without_duplicate():
    engine, db, exchange = make_engine(fee_rate="0", fee_asset="USDT")
    grid = create(engine)
    exchange.lose_next_response = True
    try:
        engine.adjust_grid(grid["id"], 95, 105)
    except RuntimeError:
        pass
    else:
        raise AssertionError("injected lost placement response did not happen")
    assert engine.adjust_grid(grid["id"], 95, 105)["ok"]
    assert_adjust_invariants(engine, grid["id"])


def test_f6_and_f7_buy_fill_race_is_settled_and_protected():
    for partial in (False, True):
        engine, db, exchange = make_engine(fee_rate="0", fee_asset="USDT")
        grid = create(engine)
        cell = db.get_grid_levels(grid["id"])[0]
        cancel = exchange.cancel_order
        def fill_then_cancel(symbol, order_id):
            exchange.fill(order_id, partial=partial)
            return cancel(symbol, order_id)
        exchange.cancel_order = fill_then_cancel
        result = engine.adjust_grid(grid["id"], 95, 105)
        assert result["ok"]
        pinned = db.get_grid_levels(grid["id"])[cell["level_idx"]]
        assert pinned["state"] == "SELL_OPEN" and pinned["held_qty"] > 0
        assert pinned["order_id"] != cell["order_id"]
        assert_adjust_invariants(engine, grid["id"])


def test_repriced_buy_cycle_uses_persisted_buy_client_id_for_realized_pnl():
    engine, db, exchange = make_engine(fee_rate="0", fee_asset="USDT")
    grid = create(engine)
    assert engine.adjust_grid(grid["id"], 95, 105)["ok"]
    buy_cell = next(row for row in db.get_grid_levels(grid["id"]) if row["state"] == "BUY_OPEN")
    exchange.fill(buy_cell["order_id"])
    engine.sync_grid(grid["id"])
    held = next(row for row in db.get_grid_levels(grid["id"])
                if int(row["level_idx"]) == int(buy_cell["level_idx"]))
    assert held["state"] == "SELL_OPEN"
    assert held["buy_client_order_id"] == buy_cell["buy_client_order_id"]
    exchange.fill(held["order_id"])
    engine.sync_grid(grid["id"])
    settled = db.get_grid_levels(grid["id"])[int(buy_cell["level_idx"])]
    assert settled["cycles_completed"] == 1
    assert settled["pnl"] > 0


def test_repriced_buy_client_id_survives_repository_move_and_realizes_pnl():
    engine, db, exchange = make_engine(fee_rate="0", fee_asset="USDT")
    grid = create(engine)
    assert engine.adjust_grid(grid["id"], 95, 105)["ok"]
    buy_cell = next(row for row in db.get_grid_levels(grid["id"]) if row["state"] == "BUY_OPEN")
    exchange.fill(buy_cell["order_id"])
    engine.sync_grid(grid["id"])
    held = next(row for row in db.get_grid_levels(grid["id"])
                if int(row["level_idx"]) == int(buy_cell["level_idx"]))
    buy_cid, sell_id = held["buy_client_order_id"], int(held["order_id"])
    result = engine.close_grid(grid["id"], "repository")
    repo_id = int(result["repository_grid_id"])
    moved = next(row for row in db.get_grid_levels(repo_id)
                 if int(row["level_idx"]) == int(buy_cell["level_idx"]))
    assert moved["buy_client_order_id"] == buy_cid
    exchange.fill(sell_id)
    engine.sync_repository(repo_id)
    settled = next(row for row in db.get_grid_levels(repo_id)
                   if int(row["level_idx"]) == int(buy_cell["level_idx"]))
    assert settled["state"] == "DONE" and settled["pnl"] > 0


def test_f5_kth_cell_failure_resumes_to_same_state_as_clean_adjustment():
    engine, db, exchange = make_engine(fee_rate="0", fee_asset="USDT")
    grid = create(engine)
    cancel = exchange.cancel_order
    count = {"value": 0}
    def fail_second(symbol, order_id):
        count["value"] += 1
        if count["value"] == 2:
            raise RuntimeError("injected cell-two cancellation failure")
        return cancel(symbol, order_id)
    exchange.cancel_order = fail_second
    try:
        engine.adjust_grid(grid["id"], 95, 105)
    except RuntimeError:
        pass
    else:
        raise AssertionError("second-cell failure did not interrupt adjustment")
    exchange.cancel_order = cancel
    assert engine.adjust_grid(grid["id"], 95, 105)["ok"]
    assert_adjust_invariants(engine, grid["id"])

    clean, clean_db, clean_exchange = make_engine(fee_rate="0", fee_asset="USDT")
    clean_grid = create(clean)
    assert clean.adjust_grid(clean_grid["id"], 95, 105)["ok"]
    fields = ("level_idx", "price", "capital", "sell_price", "state", "client_order_id",
              "buy_client_order_id", "order_id")
    actual = [{key: row[key] for key in fields} for row in db.get_grid_levels(grid["id"])]
    expected = [{key: row[key] for key in fields} for row in clean_db.get_grid_levels(clean_grid["id"])]
    assert actual == expected


def test_f8_transient_place_error_recovers_without_duplicate_buy():
    from requests.exceptions import Timeout

    engine, db, exchange = make_engine(fee_rate="0", fee_asset="USDT")
    grid = create(engine)
    place = exchange.place_order
    failed = {"once": False}
    def timeout_once(*args, **kwargs):
        if not failed["once"]:
            failed["once"] = True
            raise Timeout("temporary network timeout")
        return place(*args, **kwargs)
    exchange.place_order = timeout_once
    try:
        engine.adjust_grid(grid["id"], 95, 105)
    except Timeout:
        pass
    else:
        raise AssertionError("transient order placement did not interrupt adjustment")
    exchange.place_order = place
    assert engine.adjust_grid(grid["id"], 95, 105)["ok"]
    assert_adjust_invariants(engine, grid["id"])
    assert len(exchange.get_open_orders("XRPUSDT")) == len({
        row.get("buy_client_order_id") for row in db.get_grid_levels(grid["id"])
        if row["state"] == "BUY_OPEN" and row.get("order_id") is not None
    })


def test_rejected_status_and_double_call_have_no_duplicate_orders():
    for status in ("PAUSED", "CLOSING", "HOLDING"):
        engine, db, exchange = make_engine(fee_rate="0", fee_asset="USDT")
        grid = create(engine)
        db.update_grid(grid["id"], status=status)
        before = len(exchange.create_calls)
        assert not engine.adjust_grid(grid["id"], 95, 105)["ok"]
        assert len(exchange.create_calls) == before
    engine, db, exchange = make_engine(fee_rate="0", fee_asset="USDT")
    grid = create(engine)
    first = engine.adjust_grid(grid["id"], 95, 105)
    before = len(exchange.create_calls)
    second = engine.adjust_grid(grid["id"], 95, 105)
    assert first["ok"] and not second["changed"]
    assert len(exchange.create_calls) == before
    assert_adjust_invariants(engine, grid["id"])


def test_concurrent_adjust_for_same_grid_is_rejected_until_first_finishes():
    engine, db, exchange = make_engine(fee_rate="0", fee_asset="USDT")
    grid = create(engine)
    work_started = threading.Event()
    allow_work = threading.Event()
    result = {}

    def blocking_adjust(*_args, **_kwargs):
        work_started.set()
        assert allow_work.wait(timeout=5)
        return {"ok": True, "changed": True}

    engine._adjust_grid_locked = blocking_adjust
    worker = threading.Thread(
        target=lambda: result.setdefault("first", engine.adjust_grid(grid["id"], 95, 105))
    )
    worker.start()
    try:
        assert work_started.wait(timeout=5)
        second = engine.adjust_grid(grid["id"], 94, 106)
        assert second == {"ok": False, "reason": "adjust_in_progress"}
    finally:
        allow_work.set()
        worker.join(timeout=5)

    assert not worker.is_alive()
    assert result["first"]["ok"]


def test_adjust_client_id_is_never_reused_when_range_oscillates_a_to_b_to_a():
    engine, db, exchange = make_engine(fee_rate="0", fee_asset="USDT")
    grid = create(engine)
    seen, last = {}, {}

    for low, high in ((95, 105), (94, 106), (95, 105)):
        result = engine.adjust_grid(grid["id"], low, high)
        assert result["ok"]
        assert_adjust_invariants(engine, grid["id"])
        for row in db.get_grid_levels(grid["id"]):
            cid = row.get("buy_client_order_id")
            if cid:
                idx = int(row["level_idx"])
                history = seen.setdefault(idx, set())
                previous = last.get(idx)
                assert cid not in history or (previous == cid and last[(idx, "price")] == row["price"]), \
                    f"CID reused by cell {idx}: {cid}"
                assert len(cid) <= 36
                history.add(cid)
                last[idx] = cid
                last[(idx, "price")] = row["price"]


def test_adjust_client_id_is_never_reused_when_n_repeats_a_previous_line():
    engine, db, _exchange = make_engine(fee_rate="0", fee_asset="USDT")
    grid = create(engine)
    seen, last = {}, {}

    for count in (5, 7, 5):
        result = engine.adjust_grid(grid["id"], 95, 105, count)
        assert result["ok"]
        assert_adjust_invariants(engine, grid["id"])
        for row in db.get_grid_levels(grid["id"]):
            cid = row.get("buy_client_order_id")
            if cid:
                idx = int(row["level_idx"])
                history = seen.setdefault(idx, set())
                previous = last.get(idx)
                assert cid not in history or (previous == cid and last[(idx, "price")] == row["price"]), \
                    f"CID reused by cell {idx}: {cid}"
                history.add(cid)
                last[idx] = cid
                last[(idx, "price")] = row["price"]


def test_adjust_retry_reuses_write_ahead_cid_after_crash_without_duplicate():
    engine, db, exchange = make_engine(fee_rate="0", fee_asset="USDT")
    grid = create(engine)
    update = db.update_level
    interrupted = {"done": False}

    def fail_after_old_order_cancel(grid_id, level_idx, **fields):
        if fields == {"order_id": None} and not interrupted["done"]:
            interrupted["done"] = True
            raise RuntimeError("crash after old order cancellation")
        return update(grid_id, level_idx, **fields)

    db.update_level = fail_after_old_order_cancel
    try:
        engine.adjust_grid(grid["id"], 95, 105)
    except RuntimeError:
        pass
    else:
        raise AssertionError("injected crash did not interrupt adjustment")
    db.update_level = update

    intent = next(row for row in db.get_grid_levels(grid["id"])
                  if row["state"] == "BUY_OPEN" and str(row.get("client_order_id", "")).startswith("gA")
                  and row.get("order_id") is not None
                  and exchange.get_order("XRPUSDT", order_id=int(row["order_id"]))["status"] == "CANCELED"
                  and exchange.find_order_by_client_id("XRPUSDT", row["client_order_id"]) is None)
    intent_cid = intent["client_order_id"]
    result = engine.adjust_grid(grid["id"], 95, 105)
    assert result["ok"]
    recovered = db.get_grid_levels(grid["id"])[int(intent["level_idx"])]
    assert recovered["client_order_id"] == intent_cid
    assert recovered["order_id"] is not None
    assert sum(call[4] == intent_cid for call in exchange.create_calls) == 1
    assert_adjust_invariants(engine, grid["id"])


def test_adjust_cancels_unlinked_live_buy_intent_before_replacing_client_id():
    engine, db, exchange = make_engine(fee_rate="0", fee_asset="USDT")
    grid = create(engine)
    cell = next(row for row in db.get_grid_levels(grid["id"])
                if row["state"] == "BUY_OPEN" and row.get("order_id") is not None)
    old_order_id = int(cell["order_id"])
    old_cid = cell["client_order_id"]
    assert exchange.find_order_by_client_id("XRPUSDT", old_cid)["status"] == "NEW"

    # Simulate an accepted order whose response was lost before DB linkage.
    db.update_level(grid["id"], int(cell["level_idx"]), order_id=None)
    result = engine.adjust_grid(grid["id"], 95, 105)
    assert result["ok"]

    open_ids = {int(order["order_id"]) for order in exchange.get_open_orders("XRPUSDT")}
    owned_ids = {int(row["order_id"]) for row in db.get_grid_levels(grid["id"])
                 if row.get("order_id") is not None}
    assert old_order_id not in open_ids or old_order_id in owned_ids
    assert open_ids <= owned_ids
    assert_adjust_invariants(engine, grid["id"])


def test_adjust_discards_historical_terminal_order_found_for_candidate_cid():
    engine, db, exchange = make_engine(fee_rate="0", fee_asset="USDT")
    grid = create(engine)
    real_find = exchange.find_order_by_client_id
    injected = {"done": False, "cid": None}

    def find_with_historical_cancel(symbol, cid):
        if not injected["done"] and str(cid).startswith("gA"):
            injected.update(done=True, cid=cid)
            return {"order_id": 999999, "client_order_id": cid, "status": "CANCELED",
                    "symbol": symbol, "side": "BUY", "executed_qty": 0}
        return real_find(symbol, cid)

    exchange.find_order_by_client_id = find_with_historical_cancel
    result = engine.adjust_grid(grid["id"], 95, 105)
    assert result["ok"] and injected["cid"]
    row = next(row for row in db.get_grid_levels(grid["id"])
               if row.get("buy_client_order_id") and row["state"] == "BUY_OPEN")
    assert row["client_order_id"] != injected["cid"]
    assert row["order_id"] != 999999
    assert_adjust_invariants(engine, grid["id"])


def test_adjust_after_completed_cycle_does_not_reuse_filled_buy_cid():
    engine, db, exchange = make_engine(fee_rate="0", fee_asset="USDT")
    grid = create(engine)
    level_idx = 0
    initial = db.get_grid_levels(grid["id"])[level_idx]
    exchange.cancel_order("XRPUSDT", int(initial["order_id"]))
    db.update_level(grid["id"], level_idx, state="IDLE", order_id=None,
                    client_order_id=None, buy_client_order_id=None)

    target_low, target_high = Decimal("95.00"), Decimal("105.00")
    # The first reposition starts from a never-armed cell, so it has no prior CID.
    assert engine.adjust_grid(grid["id"], target_low, target_high, 5)["ok"]
    first_cycle_buy = db.get_grid_levels(grid["id"])[level_idx]
    first_cid = first_cycle_buy["client_order_id"]
    first_order_id = int(first_cycle_buy["order_id"])
    assert_adjust_invariants(engine, grid["id"])

    exchange.fill(first_order_id)
    engine.sync_grid(grid["id"])
    held = db.get_grid_levels(grid["id"])[level_idx]
    assert held["state"] == "SELL_OPEN" and held["buy_client_order_id"] == first_cid
    sell_order_id = int(held["order_id"])

    # Leave only this cell in the exchange while the market moves below its buy
    # level; completing the sell then correctly leaves it IDLE with cleared CIDs.
    for row in db.get_grid_levels(grid["id"]):
        if int(row["level_idx"]) == level_idx or row["state"] != "BUY_OPEN":
            continue
        exchange.cancel_order("XRPUSDT", int(row["order_id"]))
        db.update_level(grid["id"], int(row["level_idx"]), state="IDLE", order_id=None,
                        client_order_id=None, buy_client_order_id=None)
    exchange.move_price("90", "90.01", "90")
    exchange.fill(sell_order_id)
    engine.sync_grid(grid["id"])
    sold = db.get_grid_levels(grid["id"])[level_idx]
    assert sold["state"] == "IDLE" and sold["cycles_completed"] == 1
    assert sold["held_qty"] == 0 and sold["client_order_id"] is None
    assert sold["buy_client_order_id"] is None

    # N changes while the endpoint line T remains 95 for this same cell.
    exchange.move_price("100", "100.01", "100")
    assert engine.adjust_grid(grid["id"], target_low, target_high, 7)["ok"]
    reopened = db.get_grid_levels(grid["id"])[level_idx]
    assert reopened["state"] == "BUY_OPEN"
    assert reopened["held_qty"] == 0
    assert reopened["client_order_id"] != first_cid
    assert int(reopened["order_id"]) != first_order_id
    assert exchange.get_order("XRPUSDT", order_id=int(reopened["order_id"]))["status"] == "NEW"
    assert not any(row["state"] == "SELL_OPEN" and int(row["level_idx"]) == level_idx
                   for row in db.get_grid_levels(grid["id"]))
    assert_adjust_invariants(engine, grid["id"])


def test_unarmed_cell_with_no_prior_cid_uses_cycle_to_avoid_old_filled_order():
    engine, db, exchange = make_engine(fee_rate="0", fee_asset="USDT")
    grid = create(engine)
    level_idx = 0
    initial = db.get_grid_levels(grid["id"])[level_idx]
    exchange.cancel_order("XRPUSDT", int(initial["order_id"]))
    db.update_level(grid["id"], level_idx, state="IDLE", order_id=None,
                    client_order_id=None, buy_client_order_id=None)

    target_low, target_high = Decimal("95.00"), Decimal("105.00")
    assert engine.adjust_grid(grid["id"], target_low, target_high, 5)["ok"]
    first = db.get_grid_levels(grid["id"])[level_idx]
    first_cid, first_order_id = first["client_order_id"], int(first["order_id"])
    exchange.fill(first_order_id)
    for row in db.get_grid_levels(grid["id"]):
        if int(row["level_idx"]) == level_idx or row["state"] != "BUY_OPEN":
            continue
        exchange.cancel_order("XRPUSDT", int(row["order_id"]))
        db.update_level(grid["id"], int(row["level_idx"]), state="IDLE", order_id=None,
                        client_order_id=None, buy_client_order_id=None)

    # Represent a completed earlier cycle whose terminal exchange order remains
    # queryable while the local cycle state has cleared both CIDs.
    db.update_level(grid["id"], level_idx, state="IDLE", order_id=None,
                    client_order_id=None, buy_client_order_id=None, held_qty=0,
                    entry_price=None, bought_at=None, cycles_completed=1)
    assert exchange.get_order("XRPUSDT", order_id=first_order_id)["status"] == "FILLED"

    assert engine.adjust_grid(grid["id"], target_low, target_high, 7)["ok"]
    current = db.get_grid_levels(grid["id"])[level_idx]
    assert current["client_order_id"] != first_cid
    assert current["state"] == "BUY_OPEN" and current["held_qty"] == 0
    assert exchange.get_order("XRPUSDT", order_id=int(current["order_id"]))["status"] == "NEW"
    assert_adjust_invariants(engine, grid["id"])


def test_adjust_does_not_accept_filled_cid_if_cycle_advanced_after_write_ahead():
    engine, db, exchange = make_engine(fee_rate="0", fee_asset="USDT")
    grid = create(engine)
    level_idx = 0
    initial = db.get_grid_levels(grid["id"])[level_idx]
    exchange.cancel_order("XRPUSDT", int(initial["order_id"]))
    db.update_level(grid["id"], level_idx, state="IDLE", order_id=None,
                    client_order_id=None, buy_client_order_id=None)

    target_low, target_high = Decimal("95.00"), Decimal("105.00")
    target = target_low
    digest = hashlib.sha256(
        f"{grid['id']}:{level_idx}:cycle:0:previous:<none>:{target}".encode("ascii")
    ).hexdigest()[:30]
    historical_cid = "gA" + digest
    historical = exchange.place_order("XRPUSDT", "BUY", Decimal("2.1"), target,
                                       client_order_id=historical_cid)
    exchange.fill(historical["order_id"])
    real_find = exchange.find_order_by_client_id
    advanced = {"done": False}

    def advance_cycle_after_write_ahead(symbol, cid):
        if cid == historical_cid and not advanced["done"]:
            advanced["done"] = True
            # Simulate another synchronizer finishing the prior cycle after the
            # new write-ahead CID is persisted but before lookup returns FILLED.
            db.update_level(grid["id"], level_idx, cycles_completed=1)
        return real_find(symbol, cid)

    exchange.find_order_by_client_id = advance_cycle_after_write_ahead
    result = engine.adjust_grid(grid["id"], target_low, target_high, 5)
    assert result["ok"] and advanced["done"]
    current = db.get_grid_levels(grid["id"])[level_idx]
    assert current["client_order_id"] != historical_cid
    assert current["cycles_completed"] == 1
    assert current["state"] == "BUY_OPEN" and current["held_qty"] == 0
    assert exchange.get_order("XRPUSDT", order_id=int(current["order_id"]))["status"] == "NEW"
    assert_adjust_invariants(engine, grid["id"])
