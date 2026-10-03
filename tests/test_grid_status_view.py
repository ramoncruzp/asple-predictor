"""Tests for grid/status_view.py (Fase 17A) -- all pure functions, synthetic data."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from grid import status_view as sv


def _grid(**overrides):
    base = {
        "id": 1, "symbol": "XRPUSDT", "strategy": "simple", "status": "ACTIVE",
        "created_at": datetime(2026, 1, 1, tzinfo=timezone.utc), "capital_total": 1000.0,
        "dust_qty": "0.0", "params": {},
    }
    base.update(overrides)
    return base


def test_new_phase_events_have_clear_labels_and_dynamic_dust_severity():
    now = datetime(2026, 10, 3, tzinfo=timezone.utc)
    events = sv.events_view([
        {"ts": now, "event_type": "BUY_PARTIAL_SETTLED", "details": {}},
        {"ts": now, "event_type": "TESTNET_RESET_RECOVERED", "details": {}},
        {"ts": now, "event_type": "DUST_RECONCILIATION", "details": {"severity": "warning"}},
    ])
    assert events[0]["message"] == "Compra parcial contabilizada" and events[0]["severity"] == "info"
    assert events[1]["severity"] == "info"
    assert events[2]["severity"] == "warning"


def _level(level_idx, price, sell_price, *, capital=100.0, state="IDLE", held_qty=0.0,
           entry_price=None, cycles_completed=0, pnl=0.0, fee_paid=0.0):
    return {"level_idx": level_idx, "price": price, "sell_price": sell_price, "capital": capital,
            "state": state, "held_qty": held_qty, "entry_price": entry_price,
            "cycles_completed": cycles_completed, "pnl": pnl, "fee_paid": fee_paid}


NOW = datetime(2026, 1, 6, tzinfo=timezone.utc)


def test_grid_summary_known_figures_bruto_fees_neto_no_realizado_total():
    grid = _grid()
    levels = [
        _level(0, 1.40, 1.45, state="SELL_OPEN", held_qty=70.0, entry_price=1.40,
               cycles_completed=3, pnl=5.0, fee_paid=0.3),
        _level(1, 1.35, 1.40, state="BUY_OPEN", cycles_completed=2, pnl=3.0, fee_paid=0.2),
        _level(2, 1.50, 1.55, state="IDLE", cycles_completed=1, pnl=1.0, fee_paid=0.1),
    ]
    summary = sv.grid_summary(grid, levels, 1.42, now=NOW, fee_pct=0.1)

    assert summary["net_realized_usdt"] == pytest.approx(9.0)  # sum(pnl)
    assert summary["fee_real_usdt"] == pytest.approx(0.6)  # sum(fee_paid)
    assert summary["gross_realized_usdt"] == pytest.approx(9.6)  # net + real fee
    # fees_estimated = sum(capital * cycles * 2 * fee_rate) = (100*3+100*2+100*1)*2*0.001
    assert summary["fees_estimated_usdt"] == pytest.approx(1.2)
    assert summary["net_after_estimated_fees_usdt"] == pytest.approx(9.6 - 1.2)
    # inventory: qty=70, cost=70*1.40=98, market=70*1.42*(1-0.001)=99.3006
    assert summary["inventory"]["cost_usdt"] == pytest.approx(98.0)
    assert summary["inventory"]["market_value_usdt"] == pytest.approx(99.3006)
    assert summary["inventory"]["unrealized_pnl_usdt"] == pytest.approx(1.3006)
    assert summary["total_with_inventory_usdt"] == pytest.approx(9.0 + 1.3006)
    assert summary["capital_deployed_usdt"] == pytest.approx(200.0)  # level 0 (held) + level 1 (BUY_OPEN)
    assert summary["capital_deployed_pct"] == pytest.approx(20.0)
    assert summary["cycles_completed"] == 6


def test_grid_summary_price_absent_everything_price_dependent_is_none():
    grid = _grid()
    levels = [_level(0, 1.40, 1.45, state="SELL_OPEN", held_qty=70.0, entry_price=1.40, pnl=5.0)]
    summary = sv.grid_summary(grid, levels, None, now=NOW)

    assert summary["price"] is None
    assert summary["inventory"]["market_value_usdt"] is None
    assert summary["inventory"]["unrealized_pnl_usdt"] is None
    assert summary["inventory"]["unavailable_reason"] == "precio actual no disponible"
    assert summary["total_with_inventory_usdt"] is None
    assert summary["dust_value_usdt"] is None
    assert summary["recovery_mode"] is None


def test_unrealized_always_accompanies_net_realized_when_price_known():
    grid = _grid()
    levels = [_level(0, 1.0, 1.1, state="SELL_OPEN", held_qty=10.0, entry_price=1.0, pnl=2.0)]
    summary = sv.grid_summary(grid, levels, 1.05, now=NOW)
    assert summary["net_realized_usdt"] is not None
    assert summary["inventory"]["unrealized_pnl_usdt"] is not None
    assert summary["total_with_inventory_usdt"] is not None


@pytest.mark.parametrize("price,expected", [
    (1.40, True),   # exactly at a level's buy price
    (1.60, True),   # above the ceiling (highest buy price 1.50)
    (1.10, True),   # below the floor (lowest buy price 1.35)
])
def test_cells_view_marker_inserted_at_exact_position(price, expected):
    levels = [
        _level(0, 1.40, 1.45, state="SELL_OPEN", held_qty=5.0, entry_price=1.40),
        _level(1, 1.35, 1.40, state="BUY_OPEN"),
        _level(2, 1.50, 1.55, state="IDLE"),
    ]
    cells = sv.cells_view(levels, price, 0.1)
    rows = cells["rows"]
    marker_positions = [i for i, row in enumerate(rows) if row.get("marker") == "precio_actual"]
    assert len(marker_positions) == 1
    marker_idx = marker_positions[0]
    above = rows[:marker_idx]
    below = rows[marker_idx + 1:]
    # The marker is inserted right before the first row (scanning top-down)
    # whose buy_price the current price has reached or passed, so rows above
    # are strictly higher and rows below are at or below the current price.
    assert all(row["buy_price"] > price for row in above if "buy_price" in row and row["buy_price"] is not None)
    assert all(row["buy_price"] <= price for row in below if "buy_price" in row and row["buy_price"] is not None)


def test_cells_view_is_sorted_highest_price_first():
    levels = [_level(0, 1.00, 1.05), _level(1, 1.50, 1.55), _level(2, 1.20, 1.25)]
    cells = sv.cells_view(levels, None, 0.1)
    prices = [row["buy_price"] for row in cells["rows"]]
    assert prices == sorted(prices, reverse=True)


def test_cells_view_distance_to_fill_pct_correct():
    levels = [
        _level(0, 1.35, 1.40, state="BUY_OPEN"),       # waiting buy, price below it
        _level(1, 1.40, 1.45, state="SELL_OPEN", held_qty=5.0, entry_price=1.40),  # waiting sell
    ]
    cells = sv.cells_view(levels, 1.42, 0.1)
    by_idx = {row["level_idx"]: row for row in cells["rows"] if "level_idx" in row}
    # buy at 1.35, price 1.42: (1.35-1.42)/1.42*100
    assert by_idx[0]["distance_to_fill_pct"] == pytest.approx((1.35 - 1.42) / 1.42 * 100)
    # sell at 1.45, price 1.42: (1.45-1.42)/1.42*100
    assert by_idx[1]["distance_to_fill_pct"] == pytest.approx((1.45 - 1.42) / 1.42 * 100)


def test_cells_view_state_labels_paused_variants():
    levels = [
        _level(0, 1.0, 1.1, state="SELL_OPEN", held_qty=5.0, entry_price=1.0),
        _level(1, 1.2, 1.3, state="IDLE"),
    ]
    paused_cells = sv.cells_view(levels, None, 0.1, grid_paused=True)
    labels = {row["level_idx"]: row["state_label"] for row in paused_cells["rows"] if "level_idx" in row}
    assert labels[0] == "pausado con posición"
    assert labels[1] == "pausado"


@pytest.mark.parametrize("price,expected", [
    (1.40, True),   # below the only held sell price (1.45) -> recovery
    (1.46, False),  # above the only held sell price -> not recovery
    (None, None),   # no price -> unknown, not False
])
def test_recovery_mode(price, expected):
    levels = [_level(0, 1.40, 1.45, state="SELL_OPEN", held_qty=5.0, entry_price=1.40)]
    assert sv.is_recovery_mode(levels, price) is expected


def test_recovery_mode_false_when_nothing_held():
    levels = [_level(0, 1.40, 1.45, state="IDLE")]
    assert sv.is_recovery_mode(levels, 1.30) is False


def test_progress_view_target_pct_cash_basis():
    grid = _grid(capital_total=1000.0, params={"target_pct": 5.0, "target_basis": "cash"})
    levels = [_level(0, 1.0, 1.1, pnl=25.0)]
    progress = sv.progress_view(grid, levels, NOW)
    assert progress["target"]["target_profit_usdt"] == pytest.approx(50.0)
    assert progress["target"]["basis"] == "cash"
    assert progress["target"]["progress_pct"] == pytest.approx(50.0)


def test_cash_target_progress_subtracts_held_basis_instead_of_using_realized_alone():
    grid = _grid(capital_total=100, params={"target_usdt": 5, "target_basis": "cash"})
    levels = [_level(0, 3.0, 3.5, held_qty=10, entry_price=3, pnl=3)]
    target = sv.progress_view(grid, levels, NOW)["target"]
    assert target["cash_now_usdt"] == pytest.approx(72.97)
    assert target["cash_profit_usdt"] == pytest.approx(-27.03)
    assert target["progress_pct"] == pytest.approx(-540.6)
    assert "celdas con ganancia" in target["note"]


def test_cash_target_progress_without_inventory_and_with_dust_proceeds():
    levels = [_level(0, 1, 1.1, pnl=3)]
    plain = sv.progress_view(_grid(capital_total=100, params={"target_usdt": 10}), levels, NOW)["target"]
    with_dust = sv.progress_view(_grid(capital_total=100,
        params={"target_usdt": 10, "dust_cash_proceeds": "2"}), levels, NOW)["target"]
    assert plain["cash_profit_usdt"] == pytest.approx(3)
    assert with_dust["cash_profit_usdt"] == pytest.approx(5)


def test_equity_target_progress_reports_equity_only_when_price_is_available():
    grid = _grid(capital_total=100, params={"target_usdt": 10, "target_basis": "equity"})
    levels = [_level(0, 3, 3.5, held_qty=10, entry_price=3, pnl=3)]
    without_price = sv.progress_view(grid, levels, NOW)["target"]
    with_price = sv.progress_view(grid, levels, NOW, price=4)["target"]
    assert without_price["equity_now_usdt"] is None
    assert with_price["equity_now_usdt"] == pytest.approx(112.93)


def test_progress_view_max_days_remaining():
    grid = _grid(created_at=NOW - timedelta(days=4), params={"max_days": 10})
    progress = sv.progress_view(grid, [], NOW)
    assert progress["max_days"]["max_days"] == 10
    assert progress["max_days"]["age_days"] == pytest.approx(4.0)
    assert progress["max_days"]["days_remaining"] == pytest.approx(6.0)
    assert progress["max_days"]["expired"] is False


def test_progress_view_no_target_or_max_days_is_none():
    grid = _grid(params={})
    progress = sv.progress_view(grid, [], NOW)
    assert progress["target"] is None
    assert progress["max_days"] is None


def test_operations_view_liquidation_does_not_count_as_cycle():
    events = [
        {"id": 1, "event_type": "BUY_FILLED", "level_idx": 0, "ts": NOW - timedelta(hours=2),
         "price": 1.0, "details": {"executed_qty": "10", "fee_usdt": "0.01"}},
        {"id": 2, "event_type": "TARGET_REACHED", "level_idx": None, "ts": NOW,
         "price": 1.2, "details": {"sell_cells": [{"level_idx": 0, "qty": "10", "pnl": "2.0"}]}},
    ]
    operations = sv.operations_view(events)
    assert operations == []


def test_operations_view_pairs_buy_and_sell_and_computes_duration():
    events = [
        {"id": 1, "event_type": "BUY_FILLED", "level_idx": 0, "ts": NOW - timedelta(hours=3),
         "price": 1.0, "details": {"executed_qty": "10", "fee_usdt": "0.01"}},
        {"id": 2, "event_type": "SELL_FILLED", "level_idx": 0, "ts": NOW,
         "price": 1.1, "details": {"executed_qty": "10", "cycle_pnl": 0.9, "cycles_completed": 1}},
    ]
    operations = sv.operations_view(events)
    assert len(operations) == 1
    op = operations[0]
    assert op["net_pnl_usdt"] == 0.9
    assert op["buy_price_approx"] == 1.0
    assert op["sell_price_approx"] == 1.1
    assert op["duration_hours"] == pytest.approx(3.0)
    assert op["unavailable_reason"] is None
    assert op["gross_pnl_usdt"] is None  # not derivable, must not be invented


def test_operations_view_sell_without_matching_buy_is_marked_unavailable():
    events = [{"id": 1, "event_type": "SELL_FILLED", "level_idx": 0, "ts": NOW,
               "price": 1.1, "details": {"executed_qty": "10", "cycle_pnl": 0.9}}]
    operations = sv.operations_view(events)
    assert len(operations) == 1
    assert operations[0]["buy_price_approx"] is None
    assert "no se encontr" in operations[0]["unavailable_reason"]


def test_operations_view_exposes_negative_stoploss_as_non_cycle_and_uses_execution_prices():
    event = {"id": 1, "event_type": "CELL_STOPLOSS", "level_idx": 2, "ts": NOW,
        "price": 1.2, "details": {"realized_pnl": "-4.5", "entry_price": "1.25",
            "execution_price": "1.10", "held_qty": "10"}}
    operation = sv.operations_view([event], levels=[_level(2, 1.25, 1.30)])[0]
    assert operation["kind"] == "stop_loss"
    assert operation["net_pnl_usdt"] == "-4.5"
    assert operation["buy_price_approx"] == "1.25"
    assert operation["sell_price_approx"] == "1.10"
    assert operation["prices_approx"] is False
    assert operation["unavailable_reason"] is None


def test_operations_view_uses_level_limit_prices_until_that_cell_is_adjusted():
    levels = [_level(2, 1.20, 1.25)]
    events = [
        {"id": 1, "event_type": "BUY_FILLED", "level_idx": 2, "ts": NOW - timedelta(hours=1),
         "price": 1.19, "details": {"executed_qty": "10"}},
        {"id": 2, "event_type": "SELL_FILLED", "level_idx": 2, "ts": NOW,
         "price": 1.24, "details": {"executed_qty": "10", "cycle_pnl": "0.4"}},
    ]
    exact = sv.operations_view(events, levels=levels)[0]
    assert exact["buy_price_approx"] == 1.2
    assert exact["sell_price_approx"] == 1.25
    assert exact["prices_approx"] is False
    adjusted = [{**events[0]}, {"id": 3, "event_type": "GRID_ADJUSTED", "level_idx": None,
        "ts": NOW - timedelta(minutes=30), "details": {"mapping": [{"level_idx": 2, "price": "1.3"}],
        "fixed_cells": []}}, events[1]]
    fallback = sv.operations_view(adjusted, levels=levels)[0]
    assert fallback["buy_price_approx"] == 1.19
    assert fallback["sell_price_approx"] == 1.24
    assert fallback["prices_approx"] is True


def test_daily_profit_includes_cycle_stoploss_and_liquidation_pnl_and_reconciles():
    events = [
        {"event_type": "SELL_FILLED", "ts": NOW, "details": {"cycle_pnl": "2"}},
        {"event_type": "CELL_STOPLOSS", "ts": NOW, "details": {"realized_pnl": "-5"}},
        {"event_type": "CELL_LIQUIDATED", "ts": NOW, "details": {"cycle_pnl": "-3"}},
    ]
    daily = sv.daily_profit_view(events, net_realized_usdt=-7, truncated=True)
    assert daily["daily"][0] == {"date": "2026-01-05", "cycles": 2.0, "stoploss": -5.0,
                                 "liquidations": -3.0, "net_pnl_usdt": -6.0}
    assert daily["unattributed_usdt"] is None
    assert daily["unattributed_note"] == "conciliación solo disponible cuando el rango cubre toda la vida del grid"
    assert daily["truncated"] is True


def test_equity_curve_uses_summary_realized_once_falls_back_and_keeps_runs_distinct():
    snapshots = [
        {"run_id": 10, "ts": NOW - timedelta(hours=2), "level_idx": 0,
         "pnl_realized": 5, "unrealized_pnl": 4},
        {"run_id": 10, "ts": NOW - timedelta(hours=2), "level_idx": None,
         "pnl_realized": 5, "unrealized_pnl": None},
        {"run_id": 11, "ts": NOW - timedelta(hours=1), "level_idx": 0,
         "pnl_realized": -2, "unrealized_pnl": -3},
        {"run_id": 11, "ts": NOW - timedelta(hours=1), "level_idx": None,
         "pnl_realized": -2, "unrealized_pnl": None},
        {"run_id": 12, "ts": NOW, "level_idx": 0,
         "pnl_realized": 7, "unrealized_pnl": 1},
    ]
    curve = sv.equity_curve_view(snapshots, points=2)["points"]
    assert len(curve) == 2
    assert [point["realized_usdt"] for point in curve] == [-2.0, 7.0]
    assert [point["with_inventory_usdt"] for point in curve] == [-5.0, 8.0]
    assert [point["source"] for point in curve] == ["summary_row", "cell_rows_fallback"]


def test_equity_curve_synthetic_monitor_shape_does_not_double_count_realized():
    snapshots = [
        {"run_id": 5, "ts": NOW, "grid_id": 1, "level_idx": 0,
         "pnl_realized": 3, "unrealized_pnl": 0.5},
        {"run_id": 5, "ts": NOW, "grid_id": 1, "level_idx": 1,
         "pnl_realized": 2, "unrealized_pnl": 1.5},
        {"run_id": 5, "ts": NOW, "grid_id": 1, "level_idx": None,
         "pnl_realized": 5, "unrealized_pnl": None},
    ]
    point = sv.equity_curve_view(snapshots)["points"][0]
    assert point["realized_usdt"] == 5
    assert point["with_inventory_usdt"] == 7
    assert point["source"] == "summary_row"


def test_events_view_shows_unknown_event_types_raw_not_hidden():
    events = [{"event_type": "SOME_FUTURE_EVENT", "ts": NOW, "reason": None, "grid_id": 1, "level_idx": None}]
    rendered = sv.events_view(events)
    assert rendered[0]["type"] == "SOME_FUTURE_EVENT"
    assert rendered[0]["message"] == "SOME_FUTURE_EVENT"


def test_events_view_maps_known_event_types_to_spanish():
    events = [{"event_type": "GRID_PAUSED", "ts": NOW, "reason": "x", "grid_id": 1, "level_idx": None}]
    rendered = sv.events_view(events)
    assert rendered[0]["message"] == "Grid pausado"
    assert rendered[0]["severity"] == "info"


def test_fees_estimated_usdt_matches_hand_computation():
    levels = [_level(0, 1.0, 1.1, cycles_completed=4, capital=250.0)]
    fees = sv.fees_view(levels, fee_pct=0.2)
    # 250 * 4 cycles * 2 legs * 0.2% = 4.0
    assert fees["fees_estimated_usdt"] == pytest.approx(4.0)


def test_same_symbol_warning_fires_only_with_two_or_more_open_same_symbol():
    one = [{"symbol": "XRPUSDT", "status": "ACTIVE"}]
    assert sv.same_symbol_warning(one) is None
    two = [{"symbol": "XRPUSDT", "status": "ACTIVE"}, {"symbol": "XRPUSDT", "status": "PAUSED"}]
    assert sv.same_symbol_warning(two) is not None
    different = [{"symbol": "XRPUSDT", "status": "ACTIVE"}, {"symbol": "ETHUSDT", "status": "ACTIVE"}]
    assert sv.same_symbol_warning(different) is None


def test_same_symbol_warning_ignores_closed_grids():
    rows = [{"symbol": "XRPUSDT", "status": "ACTIVE"}, {"symbol": "XRPUSDT", "status": "CLOSED"}]
    assert sv.same_symbol_warning(rows) is None
