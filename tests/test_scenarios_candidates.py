from decimal import Decimal

from data.exchange_filters import SymbolFilters
from research.scenarios.candidates import build_candidates, build_smart_candidates


def _filters(min_notional="5"):
    return SymbolFilters(tick_size=Decimal("0.00000001"), min_price=Decimal("0.00000001"),
        max_price=Decimal("100000"), step_size=Decimal("0.00000001"),
        min_qty=Decimal("0.00000001"), max_qty=Decimal("100000000"),
        min_notional=Decimal(min_notional), apply_min_to_market=True,
        max_num_orders=None)


def test_candidate_generator_includes_quiet_and_expected_simple_bot_variants():
    result = build_candidates(symbols=["JUPUSDT"], capital_libre=100,
        perfil="RAPIDO", filters_by_symbol={"JUPUSDT": _filters()},
        price_by_symbol={"JUPUSDT": .25}, sigma_by_symbol={"JUPUSDT": .04},
        horizon_days=20)
    rows = result["candidates"]
    assert rows[-1]["strategy"] == "usdt_quieto"
    assert sum(row["strategy"] == "simple" for row in rows) == 15
    assert sum(row["strategy"] == "bot" for row in rows) == 12
    assert all(row["candidate_key"] for row in rows)
    assert result["invalid_count"] == 3


def test_candidate_generator_discards_pepe_grid_below_per_cell_notional():
    result = build_candidates(symbols=["PEPEUSDT"], capital_libre=20,
        perfil="RAPIDO", filters_by_symbol={"PEPEUSDT": _filters("5")},
        price_by_symbol={"PEPEUSDT": .00001}, sigma_by_symbol={"PEPEUSDT": .08},
        horizon_days=20)
    assert result["invalid_count"] > 0
    assert any("notional" in reason.lower() for reason in result["discarded"])
    assert result["candidates"][-1]["strategy"] == "usdt_quieto"


def test_smart_candidates_are_limited_to_two_simple_geometries():
    simple = [{"piece": {"symbol": "JUPUSDT", "strategy": "simple", "capital": 70,
        "n": 6, "width_sigma": .5}},
        {"piece": {"symbol": "JUPUSDT", "strategy": "simple", "capital": 50,
            "n": 10, "width_sigma": 2}},
        {"piece": {"symbol": "XRPUSDT", "strategy": "simple", "capital": 100,
            "n": 6, "width_sigma": 1}}]
    smart = build_smart_candidates(simple)
    assert len(smart) == 2
    assert all(row["strategy"] == "smart" and row["n"] == 6 for row in smart)
