"""Paridad exacta y aislamiento de las lecturas por plan (41c-0)."""
import copy
from decimal import Decimal

import pytest

from tests.meta_portfolio_benchmark import scenario
from tests.test_meta_own_grid_evaluation import plan_setup


def test_production_sized_warm_plan_matches_a062e09_with_fewer_reads():
    before = scenario("a062e09")
    # Se fija el refactor de caché: 41c-1 cambia el método de replay deliberadamente.
    after = scenario("c0bc8da")
    old_plan, new_plan = copy.deepcopy(before["plan"]), copy.deepcopy(after["plan"])
    old_plan.pop("timings_s")
    new_plan.pop("timings_s")
    assert old_plan == new_plan
    assert before["decision"] == after["decision"]
    assert after["combinations"] == 100
    assert after["counts"]["_apply_intelligence"] == 297
    assert after["counts"]["signal_adjusted_probability"] == 894
    for method in ("DB.list_grids_by_status", "DB.count_open_grids", "DB.get_latest_prediction",
        "Exchange.get_open_orders", "Exchange.get_balance"):
        assert after["counts"][method] < before["counts"][method] / 10
    assert after["plan"]["timings_s"]["plan_read_cache_hits"] > 1000
    detail = after["plan"]["timings_s"]["portfolio_detail"]
    assert {"combination_assembly", "allocation_vectors", "candidate_alternatives", "testnet_openability",
        "combine_aligned_replays", "combine_piece_signals", "signal_adjusted_probability",
        "rank_alternatives", "apply_intelligence"} <= detail.keys()


def test_market_reads_are_fresh_between_plans_and_when_executing(monkeypatch):
    operator, _, goal, _, _ = plan_setup()
    calls = []
    original = operator.engine.exchange.get_balance
    def balance(*args):
        calls.append(args)
        return original(*args)
    monkeypatch.setattr(operator.engine.exchange, "get_balance", balance)
    first = operator._build_plan(goal, record=False)
    assert len(calls) == 1
    assert any(row["symbol"] == "XRPUSDT" for row in first["alternatives"])
    operator.engine.exchange.free_usdt = Decimal(0)
    second = operator._build_plan(goal, record=False)
    assert len(calls) == 2
    assert not any(row["symbol"] == "XRPUSDT" for row in second["alternatives"])
    piece = next(row for row in first["alternatives"] if row["symbol"] == "XRPUSDT")["pieces"][0]
    assert not operator._testnet_openability(piece, goal)["allowed"]
    operator.engine.exchange.free_usdt = Decimal(1000)
    assert operator._testnet_openability(piece, goal)["allowed"]
    assert len(calls) == 4


def test_plan_read_context_is_discarded_after_exception(monkeypatch):
    operator, _, goal, _, _ = plan_setup()
    def fail(*_args):
        raise ValueError("plan sintético interrumpido")
    monkeypatch.setattr(operator, "_candidate_alternatives", fail)
    with pytest.raises(ValueError, match="interrumpido"):
        operator._build_plan(goal, record=False)
    assert operator._plan_read(operator.db, "count_open_grids") == 1
    monkeypatch.setattr(operator.db, "count_open_grids", lambda: 4)
    assert operator._plan_read(operator.db, "count_open_grids") == 4


def test_current_piece_estimation_keeps_41c0_warm_benchmark_within_ten_percent():
    measured = scenario("working", .05)
    # Referencia documentada en REPORTE_FASE41.md para el mismo fixture v3:
    # 2,39 s de análisis más decisión antes de añadir la pieza actual.
    assert measured["review_total_s"] <= 2.39 * 1.10
    assert measured["combinations"] == 100
    assert measured["plan"]["current_pieces"]["12"]["estimate"] is not None
