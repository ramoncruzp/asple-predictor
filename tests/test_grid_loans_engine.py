from decimal import Decimal

import pytest

from tests.test_grid_engine import create, make_engine
from tests.test_grid_adjust_engine import assert_adjust_invariants


class SimulatedProcessCrash(BaseException):
    pass


def assert_loan_ledger(db, grid_id):
    grid = db.get_grid(grid_id)
    cells = db.get_grid_levels(grid_id)
    loans = db.list_grid_loans(grid_id)
    outstanding = sum((Decimal(str(loan["reserve_part"])) for loan in loans
                       if loan["status"] in {"OPEN", "PENDING"}), Decimal(0))
    assert Decimal(str(grid["reserve"])) >= 0
    assert abs(sum((Decimal(str(row["capital_base"])) for row in cells), Decimal(0))
               + Decimal(str(grid["reserve"])) + outstanding
               - Decimal(str(grid["capital_total"]))) < Decimal("0.000001")
    assert abs(sum((Decimal(str(row.get("capital_loan", 0))) for row in cells), Decimal(0))
               - outstanding) < Decimal("0.000001")
    compound = sum((Decimal(str(row.get("capital_compound", 0))) for row in cells), Decimal(0))
    assert abs(sum((Decimal(str(row["capital"])) for row in cells), Decimal(0))
               + Decimal(str(grid["reserve"]))
               - Decimal(str(grid["capital_total"])) - compound) < Decimal("0.000001")
    for row in cells:
        assert Decimal(str(row["capital"])) == (
            Decimal(str(row["capital_base"])) + Decimal(str(row.get("capital_compound", 0)))
            + Decimal(str(row.get("capital_loan", 0)))
        )


@pytest.mark.parametrize("reserve_part,lender_part", [(20, 0), (0, 20), (10, 10)])
def test_loan_funding_sources_resize_orders_and_keep_accounting_balanced(reserve_part, lender_part):
    engine, db, exchange = make_engine(fee_rate="0", fee_asset="USDT")
    grid = create(engine, capital=1000, strategy="smart",
                  params={"loans_enabled": True, "reserve_pct": 10.0})
    levels = db.get_grid_levels(grid["id"])
    borrower, lender = levels[1], levels[0]
    before_orders = {int(row["order_id"]): row for row in exchange.get_open_orders("XRPUSDT")}
    amount = Decimal(reserve_part + lender_part)
    plan = {
        "borrower_idx": borrower["level_idx"],
        "lender_idx": lender["level_idx"] if lender_part else None,
        "amount": str(amount), "reserve_part": str(reserve_part),
        "lender_part": str(lender_part),
        "lender_capital_before": str(lender["capital"]),
        "borrower_capital_before": str(borrower["capital"]),
        "lender_cycles_at_open": lender["cycles_completed"],
        "borrower_cycles_at_open": borrower["cycles_completed"],
    }
    result = engine.lend_from_plan(grid["id"], plan)
    assert result["ok"] is True
    loan = result["loan"]
    after = {int(row["level_idx"]): row for row in db.get_grid_levels(grid["id"])}
    assert loan["status"] == "OPEN"
    assert_loan_ledger(db, grid["id"])
    assert_adjust_invariants(engine, grid["id"])
    assert Decimal(str(db.get_grid(grid["id"])["reserve"])) == Decimal("100") - Decimal(reserve_part)
    assert Decimal(str(after[borrower["level_idx"]]["capital"])) == Decimal(str(borrower["capital"])) + amount
    assert Decimal(str(after[borrower["level_idx"]]["capital_loan"])) == amount
    if lender_part:
        assert Decimal(str(after[lender["level_idx"]]["capital"])) == Decimal(str(lender["capital"])) - Decimal(lender_part)
        assert Decimal(str(after[lender["level_idx"]]["capital_loan"])) == -Decimal(lender_part)
        lender_order = exchange.get_order("XRPUSDT", order_id=after[lender["level_idx"]]["order_id"])
        assert lender_order["quantity"] == exchange.filters.round_qty_down(
            Decimal(str(after[lender["level_idx"]]["capital"])) / Decimal(str(lender["price"]))
        )
    borrower_order = exchange.get_order("XRPUSDT", order_id=after[borrower["level_idx"]]["order_id"])
    assert borrower_order["quantity"] == exchange.filters.round_qty_down(
        Decimal(str(after[borrower["level_idx"]]["capital"])) / Decimal(str(borrower["price"]))
    )
    assert len({row["client_order_id"] for row in exchange.get_open_orders("XRPUSDT")}) == len(
        exchange.get_open_orders("XRPUSDT")
    )
    assert len(exchange.get_open_orders("XRPUSDT")) == len(before_orders)


def test_f2_pending_loan_resumes_after_lender_buy_cancel_before_replacement():
    engine, db, exchange = make_engine(fee_rate="0", fee_asset="USDT")
    grid = create(
        engine, capital=1000, strategy="smart",
    )
    db.update_grid(grid["id"], params=db._json({"loans_enabled": True, "reserve_pct": 0.0}))
    levels = db.get_grid_levels(grid["id"])
    lender = next(row for row in levels if row["state"] == "BUY_OPEN")
    borrower = next(row for row in reversed(levels) if row["state"] == "IDLE")
    old_order_id = int(lender["order_id"])
    old_cid = lender["client_order_id"]
    other_order_ids = {int(order["order_id"]) for order in exchange.get_open_orders("XRPUSDT")
                       if int(order["order_id"]) != old_order_id}
    amount = Decimal("20")
    plan = {
        "lender_idx": lender["level_idx"], "borrower_idx": borrower["level_idx"],
        "amount": str(amount), "reserve_part": "0", "lender_part": str(amount),
        "lender_order_id": old_order_id, "lender_cid": old_cid,
        "lender_price": str(lender["price"]),
        "lender_capital_before": str(lender["capital"]),
        "lender_capital_after": str(Decimal(str(lender["capital"])) - amount),
        "borrower_capital_before": str(borrower["capital"]),
        "borrower_capital_after": str(Decimal(str(borrower["capital"])) + amount),
        "lender_cycles_at_open": int(lender["cycles_completed"]),
        "borrower_cycles_at_open": int(borrower["cycles_completed"]),
    }
    original_cancel = exchange.cancel_order

    def cancel_then_crash(symbol, order_id):
        result = original_cancel(symbol, order_id)
        if int(order_id) == old_order_id:
            raise SimulatedProcessCrash("F2 after lender cancel, before replacement")
        return result

    exchange.cancel_order = cancel_then_crash
    with pytest.raises(SimulatedProcessCrash, match="F2"):
        engine.lend_from_plan(grid["id"], plan)

    pending = db.list_grid_loans(grid["id"], statuses={"PENDING"})
    assert len(pending) == 1
    assert_loan_ledger(db, grid["id"])
    assert exchange.get_order("XRPUSDT", order_id=old_order_id)["status"] == "CANCELED"
    assert {int(order["order_id"]) for order in exchange.get_open_orders("XRPUSDT")} == other_order_ids

    exchange.cancel_order = original_cancel
    engine.resume_pending_loans(grid["id"])

    loan = db.list_grid_loans(grid["id"])[0]
    lender_after = db.get_grid_levels(grid["id"])[int(lender["level_idx"])]
    borrower_after = db.get_grid_levels(grid["id"])[int(borrower["level_idx"])]
    assert loan["status"] == "OPEN"
    assert_loan_ledger(db, grid["id"])
    assert_adjust_invariants(engine, grid["id"])
    assert lender_after["order_id"] != old_order_id
    assert lender_after["client_order_id"] != old_cid
    assert Decimal(str(lender_after["capital"])) == Decimal(plan["lender_capital_after"])
    assert Decimal(str(borrower_after["capital"])) == Decimal(plan["borrower_capital_after"])
    assert len({order["client_order_id"] for order in exchange.get_open_orders("XRPUSDT")}) == len(
        exchange.get_open_orders("XRPUSDT")
    )


@pytest.mark.parametrize("boundary", ["F1", "F3", "F4", "F5", "F8"])
def test_pending_loan_recovers_after_each_additional_write_ahead_boundary(boundary):
    engine, db, exchange = make_engine(fee_rate="0", fee_asset="USDT")
    grid = create(engine, capital=1000, strategy="smart")
    db.update_grid(grid["id"], params=db._json({"loans_enabled": True, "reserve_pct": 0.0}))
    cells = db.get_grid_levels(grid["id"])
    lender, borrower = cells[0], cells[1]
    plan = {
        "lender_idx": lender["level_idx"], "borrower_idx": borrower["level_idx"],
        "amount": "20", "reserve_part": "0", "lender_part": "20",
        "lender_capital_before": str(lender["capital"]),
        "borrower_capital_before": str(borrower["capital"]),
        "lender_cycles_at_open": 0, "borrower_cycles_at_open": 0,
    }
    original_continue = engine._continue_pending_loan
    original_resize = engine._resize_loan_buy
    original_cancel = exchange.cancel_order
    original_place = exchange.place_order
    fired = False
    replacement_count = 0

    if boundary == "F1":
        def crash_once(current_grid, loan):
            nonlocal fired
            if not fired:
                fired = True
                raise SimulatedProcessCrash("F1 after PENDING write-ahead")
            return original_continue(current_grid, loan)
        engine._continue_pending_loan = crash_once
    elif boundary == "F3":
        def crash_after_lender(*args, **kwargs):
            nonlocal fired
            outcome = original_resize(*args, **kwargs)
            if args[3] == "lender" and not fired:
                fired = True
                raise SimulatedProcessCrash("F3 after lender replacement")
            return outcome
        engine._resize_loan_buy = crash_after_lender
    elif boundary == "F4":
        borrower_order_id = int(borrower["order_id"])
        def crash_after_borrower_cancel(symbol, order_id):
            nonlocal fired
            result = original_cancel(symbol, order_id)
            if int(order_id) == borrower_order_id and not fired:
                fired = True
                raise SimulatedProcessCrash("F4 after borrower cancel")
            return result
        exchange.cancel_order = crash_after_borrower_cancel
    elif boundary in {"F5", "F8"}:
        def crash_or_fail_on_borrower_place(*args, **kwargs):
            nonlocal fired, replacement_count
            cid = kwargs.get("client_order_id")
            if cid and str(cid).startswith("gL"):
                replacement_count += 1
                if replacement_count == 2 and boundary == "F8" and not fired:
                    fired = True
                    raise RuntimeError("transient network failure")
                result = original_place(*args, **kwargs)
                if replacement_count == 2 and boundary == "F5" and not fired:
                    fired = True
                    raise RuntimeError("response lost after accepted replacement")
                return result
            return original_place(*args, **kwargs)
        exchange.place_order = crash_or_fail_on_borrower_place

    if boundary in {"F1", "F3", "F4"}:
        with pytest.raises(SimulatedProcessCrash, match=boundary):
            engine.lend_from_plan(grid["id"], plan)
    else:
        with pytest.raises(RuntimeError):
            engine.lend_from_plan(grid["id"], plan)
    assert db.list_grid_loans(grid["id"], statuses={"PENDING"})

    engine._continue_pending_loan = original_continue
    engine._resize_loan_buy = original_resize
    exchange.cancel_order = original_cancel
    exchange.place_order = original_place
    assert engine.resume_pending_loans(grid["id"]) == 1
    loan = db.list_grid_loans(grid["id"])[0]
    assert loan["status"] == "OPEN"
    assert_loan_ledger(db, grid["id"])
    assert_adjust_invariants(engine, grid["id"])


def test_f9_interrupted_run_matches_clean_loan_run_field_by_field():
    def setup():
        engine, db, exchange = make_engine(fee_rate="0", fee_asset="USDT")
        grid = create(engine, capital=1000, strategy="smart")
        db.update_grid(grid["id"], params=db._json({"loans_enabled": True, "reserve_pct": 0.0}))
        lender, borrower = db.get_grid_levels(grid["id"])[0:2]
        plan = {"lender_idx": lender["level_idx"], "borrower_idx": borrower["level_idx"],
                "amount": "20", "reserve_part": "0", "lender_part": "20",
                "lender_capital_before": str(lender["capital"]),
                "borrower_capital_before": str(borrower["capital"]),
                "lender_cycles_at_open": 0, "borrower_cycles_at_open": 0}
        return engine, db, exchange, grid, plan

    clean_engine, clean_db, clean_exchange, clean_grid, clean_plan = setup()
    clean_engine.lend_from_plan(clean_grid["id"], clean_plan)

    resumed_engine, resumed_db, resumed_exchange, resumed_grid, resumed_plan = setup()
    original_resize = resumed_engine._resize_loan_buy
    crashed = False

    def crash_after_lender(*args, **kwargs):
        nonlocal crashed
        outcome = original_resize(*args, **kwargs)
        if args[3] == "lender" and not crashed:
            crashed = True
            raise SimulatedProcessCrash("F9 simulated interruption")
        return outcome

    resumed_engine._resize_loan_buy = crash_after_lender
    with pytest.raises(SimulatedProcessCrash, match="F9"):
        resumed_engine.lend_from_plan(resumed_grid["id"], resumed_plan)
    resumed_engine._resize_loan_buy = original_resize
    assert resumed_engine.resume_pending_loans(resumed_grid["id"]) == 1

    def normalized(engine, db, exchange, grid_id):
        levels = [{key: row.get(key) for key in (
            "level_idx", "state", "capital", "capital_base", "capital_compound", "capital_loan",
            "client_order_id", "buy_client_order_id", "price", "order_id",
        )} for row in db.get_grid_levels(grid_id)]
        orders = [{key: row.get(key) for key in ("client_order_id", "side", "price", "quantity", "status")}
                  for row in exchange.get_open_orders("XRPUSDT")]
        loan = [{key: row.get(key) for key in ("status", "amount", "reserve_part", "lender_idx", "borrower_idx")}
                for row in db.list_grid_loans(grid_id)]
        return levels, orders, loan, db.get_grid(grid_id)["reserve"]

    assert normalized(clean_engine, clean_db, clean_exchange, clean_grid["id"]) == normalized(
        resumed_engine, resumed_db, resumed_exchange, resumed_grid["id"]
    )
    assert_loan_ledger(resumed_db, resumed_grid["id"])
    assert_adjust_invariants(resumed_engine, resumed_grid["id"])


def test_repay_reserve_loan_returns_reserve_and_restores_borrower_capital_and_order():
    engine, db, exchange = make_engine(fee_rate="0", fee_asset="USDT")
    grid = create(engine, capital=1000, strategy="smart",
                  params={"loans_enabled": True, "reserve_pct": 10.0})
    borrower = db.get_grid_levels(grid["id"])[1]
    before_capital = Decimal(str(borrower["capital"]))
    plan = {"borrower_idx": borrower["level_idx"], "lender_idx": None,
            "amount": "10", "reserve_part": "10", "lender_part": "0",
            "borrower_capital_before": str(before_capital),
            "borrower_cycles_at_open": 0, "lender_cycles_at_open": 0}
    opened = engine.lend_from_plan(grid["id"], plan)
    funded = next(row for row in db.get_grid_levels(grid["id"])
                  if row["level_idx"] == borrower["level_idx"])
    funded_order_id = funded["order_id"]
    db.update_level(grid["id"], borrower["level_idx"], cycles_completed=1)
    repaid = engine.repay_loan(opened["loan"]["id"])
    restored = next(row for row in db.get_grid_levels(grid["id"])
                    if row["level_idx"] == borrower["level_idx"])
    assert repaid["ok"] is True
    assert exchange.get_order("XRPUSDT", order_id=funded_order_id)["status"] == "CANCELED"
    assert restored["capital"] == float(before_capital)
    assert restored["capital_loan"] == 0
    assert db.get_grid(grid["id"])["reserve"] == 100
    assert db.get_grid_loan(opened["loan"]["id"])["status"] == "REPAID"
    assert_loan_ledger(db, grid["id"])
    assert_adjust_invariants(engine, grid["id"])


def test_adjust_transfers_open_reserve_loans_and_consumes_only_reserve_excess():
    engine, db, exchange = make_engine(fee_rate="0", fee_asset="USDT")
    grid = create(engine, capital=1000, strategy="smart",
                  params={"loans_enabled": True, "reserve_pct": 10.0})
    borrower = db.get_grid_levels(grid["id"])[1]
    loan_plan = {"borrower_idx": borrower["level_idx"], "lender_idx": None,
                 "amount": "10", "reserve_part": "10", "lender_part": "0",
                 "borrower_capital_before": str(borrower["capital"]),
                 "borrower_cycles_at_open": 0, "lender_cycles_at_open": 0}
    loan = engine.lend_from_plan(grid["id"], loan_plan)["loan"]
    # Simulate free reserve accumulated by the grid, above the configured 10% target.
    db.update_grid(grid["id"], reserve=110, capital_total=1020)
    result = engine.adjust_grid(grid["id"], 95, 105, reason="loan-adjust")
    assert result["ok"] and result["changed"]
    assert db.get_grid_loan(loan["id"])["status"] == "TRANSFERRED"
    assert db.get_grid(grid["id"])["reserve"] == 102
    assert sum(Decimal(str(row["capital"])) for row in db.get_grid_levels(grid["id"])) == Decimal("918")
    assert_loan_ledger(db, grid["id"])
    assert_adjust_invariants(engine, grid["id"])


def test_done_cell_returns_its_capital_to_reserve_only_when_loans_enabled():
    engine, db, _exchange = make_engine(fee_rate="0", fee_asset="USDT")
    grid = create(engine, capital=1000, strategy="smart",
                  params={"loans_enabled": True, "reserve_pct": 10.0})
    cell = db.get_grid_levels(grid["id"])[-1]
    amount = Decimal(str(cell["capital"]))
    db.update_level(grid["id"], cell["level_idx"], state="DONE")
    result = engine.return_cell_to_reserve(grid["id"], cell["level_idx"], "test_stoploss")
    returned = next(row for row in db.get_grid_levels(grid["id"])
                    if row["level_idx"] == cell["level_idx"])
    assert result["returned"] == float(amount)
    assert returned["capital"] == returned["capital_base"] == returned["capital_compound"] == 0
    assert db.get_grid(grid["id"])["reserve"] == float(Decimal("100") + amount)
    assert db.get_last_event(grid["id"], "RESERVE_RETURNED")["details"]["amount"] == str(amount)


def test_f6_lender_fill_during_cancel_is_protected_and_loan_is_cancelled():
    engine, db, exchange = make_engine(fee_rate="0", fee_asset="USDT")
    grid = create(engine, capital=1000, strategy="smart")
    db.update_grid(grid["id"], params=db._json({"loans_enabled": True, "reserve_pct": 0.0}))
    lender, borrower = db.get_grid_levels(grid["id"])[0:2]
    plan = {"lender_idx": lender["level_idx"], "borrower_idx": borrower["level_idx"],
            "amount": "20", "reserve_part": "0", "lender_part": "20",
            "lender_capital_before": str(lender["capital"]),
            "borrower_capital_before": str(borrower["capital"]),
            "lender_cycles_at_open": 0, "borrower_cycles_at_open": 0}
    original_cancel = exchange.cancel_order

    def fill_before_cancel(symbol, order_id):
        if int(order_id) == int(lender["order_id"]):
            exchange.fill(order_id)
        return original_cancel(symbol, order_id)

    exchange.cancel_order = fill_before_cancel
    result = engine.lend_from_plan(grid["id"], plan)
    exchange.cancel_order = original_cancel
    assert result["ok"] is False
    assert result["loan"]["status"] == "CANCELLED"
    levels = {row["level_idx"]: row for row in db.get_grid_levels(grid["id"])}
    assert levels[lender["level_idx"]]["state"] == "SELL_OPEN"
    assert levels[lender["level_idx"]]["held_qty"] > 0
    assert levels[borrower["level_idx"]]["capital"] == borrower["capital"]
    assert any(int(order["order_id"]) == int(levels[lender["level_idx"]]["order_id"])
               and order["side"] == "SELL" for order in exchange.get_open_orders("XRPUSDT"))
    assert_loan_ledger(db, grid["id"])
    assert_adjust_invariants(engine, grid["id"])


def test_f7_borrower_fill_after_lender_resize_rolls_lender_back_and_protects_inventory():
    engine, db, exchange = make_engine(fee_rate="0", fee_asset="USDT")
    grid = create(engine, capital=1000, strategy="smart")
    db.update_grid(grid["id"], params=db._json({"loans_enabled": True, "reserve_pct": 0.0}))
    lender, borrower = db.get_grid_levels(grid["id"])[0:2]
    original_lender_qty = exchange.get_order("XRPUSDT", order_id=lender["order_id"])["quantity"]
    plan = {"lender_idx": lender["level_idx"], "borrower_idx": borrower["level_idx"],
            "amount": "20", "reserve_part": "0", "lender_part": "20",
            "lender_capital_before": str(lender["capital"]),
            "borrower_capital_before": str(borrower["capital"]),
            "lender_cycles_at_open": 0, "borrower_cycles_at_open": 0}
    original_resize = engine._resize_loan_buy
    crashed = False

    def crash_after_lender(*args, **kwargs):
        nonlocal crashed
        outcome = original_resize(*args, **kwargs)
        if args[3] == "lender" and not crashed:
            crashed = True
            raise SimulatedProcessCrash("F7 after lender resize")
        return outcome

    engine._resize_loan_buy = crash_after_lender
    with pytest.raises(SimulatedProcessCrash, match="F7"):
        engine.lend_from_plan(grid["id"], plan)
    engine._resize_loan_buy = original_resize
    current_borrower = db.get_grid_levels(grid["id"])[borrower["level_idx"]]
    exchange.fill(current_borrower["order_id"])
    engine.sync_grid(grid["id"])
    assert db.get_grid_levels(grid["id"])[borrower["level_idx"]]["state"] == "SELL_OPEN"
    engine.resume_pending_loans(grid["id"])
    loan = db.list_grid_loans(grid["id"])[0]
    lender_after = db.get_grid_levels(grid["id"])[lender["level_idx"]]
    restored_order = exchange.get_order("XRPUSDT", order_id=lender_after["order_id"])
    assert loan["status"] == "CANCELLED"
    assert Decimal(str(lender_after["capital"])) == Decimal(str(lender["capital"]))
    assert restored_order["quantity"] == original_lender_qty
    assert db.get_grid_levels(grid["id"])[borrower["level_idx"]]["held_qty"] > 0
    assert_loan_ledger(db, grid["id"])
    assert_adjust_invariants(engine, grid["id"])
