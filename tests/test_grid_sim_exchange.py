from decimal import Decimal

from grid.sim.exchange import SimExchange


def _cell():
    return {"state": "BUY_OPEN", "held_qty": Decimal(0), "cycles_completed": 0,
            "pnl": Decimal(0), "entry_price": None, "bought_at": None}


def test_limit_fill_requires_strict_penetration_and_not_same_candle_cycle():
    ex = SimExchange(100, .1)
    cell = _cell()
    ex.place(0, "BUY", "10", "2", 0, 0)
    assert ex.process(0, 10, 11, 10.5, [cell]) == []
    assert ex.process(1, 9.99, 11, 10.5, [cell])[0][1] == "BUY"
    ex.place(0, "SELL", "11", cell["held_qty"], 2, 1)
    assert ex.process(1, 9, 12, 11.5, [cell]) == []
    assert ex.process(2, 9, 11, 11, [cell]) == []
    assert ex.process(2, 9, 11.01, 11, [cell])[0][1] == "SELL"
    assert cell["cycles_completed"] == 1


def test_buy_fee_is_base_and_sell_fee_is_quote():
    ex = SimExchange(100, .1)
    cell = _cell()
    ex.place(0, "BUY", "10", "2", 1, 0)
    ex.process(1, 9, 11, 10, [cell])
    assert cell["held_qty"] == Decimal("1.998")
    assert ex.usdt == Decimal("80")
    assert ex.base == Decimal("1.998")
    before = ex.usdt
    ex.place(0, "SELL", "12", cell["held_qty"], 2, 1)
    ex.process(2, 10, 13, 12, [cell])
    assert ex.usdt - before == Decimal("23.952024")


def test_balances_never_negative():
    ex = SimExchange(5, .1)
    try:
        ex.place(0, "BUY", "10", "1", 0, 0)
    except ValueError:
        return
    raise AssertionError("buy order without reserved quote funds was accepted")


def test_open_buy_reserves_quote_funds_until_fill_or_cancel():
    ex = SimExchange(20, 0)
    cells = [_cell(), _cell(), _cell()]
    ex.place(0, "BUY", "10", "1", 1, 0)
    ex.place(1, "BUY", "10", "1", 1, 0)
    try:
        ex.place(2, "BUY", "10", "1", 1, 0)
    except ValueError:
        pass
    else:
        raise AssertionError("over-committed buy order was accepted")
    ex.cancel(1, "BUY")
    ex.place(2, "BUY", "10", "1", 1, 0)
    fills = ex.process(1, 9, 11, 10, cells)
    assert [row[0] for row in fills] == [0, 2]
    assert ex.usdt == Decimal("0")
    assert ex.usdt >= 0 and ex.base >= 0
