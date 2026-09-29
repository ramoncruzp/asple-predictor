from decimal import Decimal

import pytest

from data.exchange_filters import SymbolFilters
from grid import levels as levels_module
from grid.levels import GridConfigError, compute_lines, plan_cells


def filters(min_notional="5"):
    return SymbolFilters(
        tick_size=Decimal("0.01"), min_price=Decimal("0.01"), max_price=Decimal("100000"),
        step_size=Decimal("0.1"), min_qty=Decimal("0.1"), max_qty=Decimal("100000"),
        min_notional=Decimal(min_notional), apply_min_to_market=True, max_num_orders=200,
    )


def cfg(**overrides):
    return {
        "grid_min_step_pct": Decimal("0.003"),
        "capital_max_por_nivel_pct": Decimal("0.30"),
        **overrides,
    }


def snapshot(bid="100", ask="100.01", avg="100"):
    return {"bid_price": Decimal(bid), "ask_price": Decimal(ask), "avg_price": Decimal(avg)}


def test_lines_are_ordered_and_cell_capital_does_not_exceed_total():
    f = filters()
    lines = compute_lines("90", "110", 5, f)
    assert len(lines) == 6
    assert all(a < b for a, b in zip(lines, lines[1:]))
    plans = plan_cells(lines, "1000", snapshot(), f, cfg())
    assert sum((cell.capital for cell in plans), Decimal(0)) <= Decimal("1000")
    assert all(cell.capital <= Decimal("300") for cell in plans)


def test_rejects_less_than_four_levels():
    with pytest.raises(GridConfigError, match="at least 4"):
        plan_cells([Decimal("90"), Decimal("95"), Decimal("100"), Decimal("105")], 100, snapshot(), filters(), cfg())


def test_rejects_step_below_configured_minimum():
    lines = [Decimal("99.00"), Decimal("99.10"), Decimal("99.20"), Decimal("99.30"), Decimal("100.00")]
    with pytest.raises(GridConfigError, match="GRID_MIN_STEP_PCT"):
        plan_cells(lines, "1000", snapshot("99.15", "99.16", "99.15"), filters(), cfg())


def test_rejects_exchange_mid_outside_range():
    lines = [Decimal("101"), Decimal("102"), Decimal("103"), Decimal("104"), Decimal("105")]
    with pytest.raises(GridConfigError, match="strictly inside"):
        plan_cells(lines, "1000", snapshot(), filters(), cfg())


def test_rejects_cell_buy_below_min_notional():
    lines = [Decimal("90"), Decimal("95"), Decimal("100"), Decimal("105"), Decimal("110")]
    with pytest.raises(GridConfigError, match="buy does not meet"):
        plan_cells(lines, "10", snapshot(), filters(), cfg())


def test_rejects_net_sell_below_min_notional_when_buy_passes(monkeypatch):
    monkeypatch.setattr(levels_module, "FEE_RATE_ESTIMATE", Decimal("0.001"))
    lines = [Decimal("50.00"), Decimal("50.20"), Decimal("50.40"), Decimal("50.60"), Decimal("50.80")]
    f = filters()
    with pytest.raises(GridConfigError, match="net sell does not meet"):
        plan_cells(lines, "24", snapshot("50.3", "50.5", "50.4"), f, cfg())


def test_live_fee_estimate_rejects_net_sell_below_min_notional():
    lines = [Decimal("50.00"), Decimal("50.20"), Decimal("50.40"), Decimal("50.60"), Decimal("50.80")]
    with pytest.raises(GridConfigError, match="net sell does not meet"):
        plan_cells(lines, "24", snapshot("50.3", "50.5", "50.4"), filters(), cfg())


def test_decimal_cell_capital_sweep_never_exceeds_total():
    f = SymbolFilters(
        tick_size=Decimal("0.01"), min_price=Decimal("0.01"), max_price=Decimal("100000"),
        step_size=Decimal("0.00000001"), min_qty=Decimal("0.00000001"), max_qty=Decimal("100000"),
        min_notional=Decimal("0.01"), apply_min_to_market=True, max_num_orders=200,
    )
    for capital in ("33.33", "50", "100", "250", "1000"):
        for n in range(4, 41):
            lines = [Decimal(100) + Decimal(i) for i in range(n + 1)]
            plans = plan_cells(
                lines, capital,
                snapshot(str(100 + n // 2), str(100 + n // 2) + ".01", str(100 + n // 2)),
                f, cfg(grid_min_step_pct=Decimal("0"), capital_max_por_nivel_pct=Decimal("1")),
            )
            assert sum((p.capital for p in plans), Decimal(0)) <= Decimal(capital)


def test_cells_above_market_start_idle():
    lines = [Decimal("90"), Decimal("95"), Decimal("100"), Decimal("105"), Decimal("110")]
    plans = plan_cells(lines, "1000", snapshot(), filters(), cfg())
    assert [cell.initial_state for cell in plans] == ["BUY_OPEN", "BUY_OPEN", "IDLE", "IDLE"]


def test_rejects_level_cap_invariant_if_configured_too_low():
    lines = [Decimal("90"), Decimal("95"), Decimal("100"), Decimal("105"), Decimal("110")]
    with pytest.raises(GridConfigError, match="cell capital exceeds"):
        plan_cells(lines, "1000", snapshot(), filters(), cfg(capital_max_por_nivel_pct=Decimal("0.20")))
