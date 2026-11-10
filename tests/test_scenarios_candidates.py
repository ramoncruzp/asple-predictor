from decimal import Decimal

import pytest

from data.exchange_filters import SymbolFilters
from research.scenarios.candidates import (build_candidates, build_reconversion_candidates,
    build_smart_candidates)
from research.scenarios.evaluate import _piece_geometry
from research.scenarios.fastsim import simulate_bot, simulate_simple


def _filters(min_notional="5", step_size="0.00000001"):
    return SymbolFilters(tick_size=Decimal("0.00000001"), min_price=Decimal("0.00000001"),
        max_price=Decimal("100000"), step_size=Decimal(step_size),
        min_qty=Decimal(step_size), max_qty=Decimal("100000000"),
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


@pytest.mark.parametrize(("profile", "cap"), [("RAPIDO", 70), ("PRUDENTE", 40)])
def test_candidate_generator_respeta_tope_de_capital_por_moneda(profile, cap):
    result = build_candidates(symbols=["JUPUSDT"], capital_libre=100,
        perfil=profile, filters_by_symbol={"JUPUSDT": _filters()},
        price_by_symbol={"JUPUSDT": .25}, sigma_by_symbol={"JUPUSDT": .04},
        horizon_days=20, capital_max_by_symbol={"JUPUSDT": cap})

    assert result["candidates"][-1]["strategy"] == "usdt_quieto"
    assert all(row["capital"] <= cap for row in result["candidates"]
        if row["strategy"] != "usdt_quieto")
    assert result["discarded_by_symbol"]["JUPUSDT"]


def test_candidate_generator_discards_pepe_grid_below_per_cell_notional():
    result = build_candidates(symbols=["PEPEUSDT"], capital_libre=20,
        perfil="RAPIDO", filters_by_symbol={"PEPEUSDT": _filters("5")},
        price_by_symbol={"PEPEUSDT": .00001}, sigma_by_symbol={"PEPEUSDT": .08},
        horizon_days=20)
    assert result["invalid_count"] > 0
    assert any("notional" in reason.lower() for reason in result["discarded"])
    assert result["candidates"][-1]["strategy"] == "usdt_quieto"


def test_candidate_generator_descarta_venta_neta_jup_con_capital_50_y_n10():
    result = build_candidates(symbols=["JUPUSDT"], capital_libre=106,
        perfil="RAPIDO", filters_by_symbol={"JUPUSDT": _filters("5", "1")},
        price_by_symbol={"JUPUSDT": .26}, sigma_by_symbol={"JUPUSDT": .015},
        horizon_days=15)
    assert any("net sell" in reason.lower() or "venta" in reason.lower()
        for reason in result["discarded"])
    assert not any(row["strategy"] == "simple" and row["n"] == 10
        and row["capital"] == 53 for row in result["candidates"])
    assert result["candidates"][-1]["strategy"] == "usdt_quieto"


def test_todos_los_candidatos_jup_y_pepe_validos_pasan_fastsim():
    import numpy as np

    for symbol, price, filters in (
            ("JUPUSDT", .26, _filters("5", "1")),
            ("PEPEUSDT", .00001, _filters("5", "0.00000001"))):
        for capital in (50, 70, 100):
            built = build_candidates(symbols=[symbol], capital_libre=capital,
                perfil="RAPIDO", filters_by_symbol={symbol: filters},
                price_by_symbol={symbol: price}, sigma_by_symbol={symbol: .015},
                horizon_days=15)
            candles = np.full((1, 2), price, dtype=float)
            scenarios = {"open": candles.copy(), "high": candles * 1.01,
                "low": candles * .99, "close": candles.copy()}
            for piece in built["candidates"]:
                if piece["strategy"] == "simple":
                    simulate_simple(scenarios, n_levels=piece["n"],
                        capital=piece["capital"], low=piece["low"],
                        high=piece["high"], filters=filters)
                elif piece["strategy"] == "bot":
                    simulate_bot(scenarios, capital=piece["capital"],
                        buy_price=piece["buy_price"], sell_price=piece["sell_price"],
                        filters=filters)


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


def test_smart_ignora_reconversion_rango_7d_y_simple_sin_width_sigma():
    smart = build_smart_candidates([
        {"piece": {"symbol": "ZROUSDT", "strategy": "simple", "capital": 53,
            "n": 6, "range_type": "rango_7d", "reconversion": True}},
        {"piece": {"symbol": "JUPUSDT", "strategy": "simple", "capital": 30,
            "n": 6}},
        {"piece": {"symbol": "WIFUSDT", "strategy": "simple", "capital": 30,
            "n": 6, "width_sigma": 1}},
    ])

    assert len(smart) == 1
    assert smart[0]["symbol"] == "WIFUSDT"
    assert smart[0]["strategy"] == "smart"


def test_reconversion_de_bot_con_inventario_genera_geometrias_y_liquida():
    result = build_reconversion_candidates(live_pieces=[{"grid_id": 18,
        "piece_type": "individual_bot_grid", "symbol": "ZROUSDT",
        "capital": 70, "has_inventory": True}],
        price_by_symbol={"ZROUSDT": 1.63}, sigma_by_symbol={"ZROUSDT": .08},
        wide_range_by_symbol={"ZROUSDT": (1.5, 1.9)},
        filters_by_symbol={"ZROUSDT": _filters()}, horizon_days=10)
    assert {row["n"] for row in result} == {6, 8, 10}
    assert {row["reconversion_geometry"] for row in result} == {"sigma_1", "sigma_2", "rango_7d"}
    assert all(row["reconvert_from_grid_id"] == 18 and
        row["reconversion_variant"] == "vender_inventario_a_mercado" for row in result)
    assert not build_reconversion_candidates(live_pieces=[{"grid_id": 19,
        "piece_type": "individual_bot_grid", "symbol": "ZROUSDT", "capital": 70,
        "has_inventory": False}], price_by_symbol={"ZROUSDT": 1.63},
        sigma_by_symbol={"ZROUSDT": .05}, wide_range_by_symbol={},
        filters_by_symbol={"ZROUSDT": _filters()}, horizon_days=10)


def test_reconversion_zro_expone_geometrias_sigma_y_rango_7d_validas():
    stats = {}
    rows = build_reconversion_candidates(live_pieces=[{"grid_id": 18,
        "piece_type": "individual_bot_grid", "symbol": "ZROUSDT",
        "capital": 53, "has_inventory": True}],
        price_by_symbol={"ZROUSDT": 1.556}, sigma_by_symbol={"ZROUSDT": .0595},
        wide_range_by_symbol={"ZROUSDT": (1.54, 1.61)},
        filters_by_symbol={"ZROUSDT": _filters()}, horizon_days=17,
        stats_by_symbol=stats)

    assert len(rows) >= 6
    assert stats["ZROUSDT"]["generated"] == len(rows)
    assert stats["ZROUSDT"].get("failed", 0) == 0
    assert stats["ZROUSDT"]["discarded"].get("reconvertir: paso menor al mínimo", 0) == 0
    assert all("width_sigma" not in row for row in rows if row.get("range_type") == "rango_7d")
    for row in rows:
        low, high = _piece_geometry(row, symbol="ZROUSDT", price=1.556,
            sigma24=.0595, horizon=17, profile={"min_margin_pct": .3})
        assert low == row["low"]
        assert high == row["high"]


def test_reconversion_sigma_ancora_el_precio_dentro_del_rango_planificable():
    rows = build_reconversion_candidates(live_pieces=[{"grid_id": 18,
        "piece_type": "individual_bot_grid", "symbol": "ZROUSDT",
        "capital": 53, "has_inventory": True}],
        price_by_symbol={"ZROUSDT": 1.556}, sigma_by_symbol={"ZROUSDT": .0595},
        wide_range_by_symbol={}, filters_by_symbol={"ZROUSDT": _filters()},
        horizon_days=17)
    sigma_rows = [row for row in rows if row["reconversion_geometry"] in {"sigma_1", "sigma_2"}]

    assert len(sigma_rows) >= 5
    for row in sigma_rows:
        lines = __import__("grid.levels", fromlist=["compute_lines"]).compute_lines(
            str(row["low"]), str(row["high"]), row["n"], _filters())
        assert lines[0] < Decimal("1.556") < lines[-1]
        shifted_mid = Decimal("1.556") * Decimal("1.035")
        assert lines[0] < shifted_mid < lines[-1]


@pytest.mark.parametrize("price", [1.2, 2.0])
def test_reconversion_rango_7d_se_amplia_para_dejar_margen_de_un_paso(price):
    rows = build_reconversion_candidates(live_pieces=[{"grid_id": 18,
        "piece_type": "individual_bot_grid", "symbol": "ZROUSDT",
        "capital": 53, "has_inventory": True}],
        price_by_symbol={"ZROUSDT": price}, sigma_by_symbol={"ZROUSDT": .0595},
        wide_range_by_symbol={"ZROUSDT": (1.5, 1.9)},
        filters_by_symbol={"ZROUSDT": _filters()}, horizon_days=17)
    ranges = [row for row in rows if row.get("range_type") == "rango_7d"]
    assert ranges
    assert all(row["low"] + (row["high"] - row["low"]) / row["n"] <= price + 1e-9
        and row["high"] - (row["high"] - row["low"]) / row["n"] >= price - 1e-9
        for row in ranges)

