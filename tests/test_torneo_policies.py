import pytest

from grid.engines.torneo import (control_geometry, follower_geometry,
    follower_recenter, highest_free_ladder_step, ladder_orders)


def test_escalera_respeta_escalones_tope_y_bot_comprado():
    orders = ladder_orders(100, .02, bought=[{"buy_price": 98}, None, None, None])
    assert [row["buy_price"] for row in orders[1:]] == pytest.approx(
        [100 * __import__("math").exp(-.02), 100 * __import__("math").exp(-.03),
            100 * __import__("math").exp(-.04)])
    assert orders[0]["sell_price"] > 98
    assert all(row["sell_price"] > 100 for row in orders[1:])
    assert len({round(row["sell_price"], 8) for row in orders[1:]}) == 3
    assert all(row["buy_price"] <= 100 * __import__("math").exp(-.3 * .02) for row in orders)
    capped = ladder_orders(100, .02, reprices_today=6,
        previous=[{"buy_price": 90}] * 4)
    assert all(not row["repriced"] and row["buy_price"] == 90 for row in capped)


def test_escalera_elige_escalon_libre_mas_alto():
    orders = ladder_orders(100, .02, capital=12)
    selected = highest_free_ladder_step(100, .02, 12,
        [orders[0]["buy_price"], orders[1]["buy_price"]])
    assert selected["buy_price"] == pytest.approx(orders[2]["buy_price"])


def test_seguidor_aplica_sesgo_y_geometria_de_ocho_niveles():
    result = follower_geometry(100, .02, inventory_pct=61)
    assert result["levels"] == 8 and result["spacing_sigma"] == .9
    assert result["bias"] == {"buy_shift_levels": -1, "sell_closer": True}
    assert result["recenter_cooldown_hours"] == 6


def test_control_es_fijo_a_dos_sigma():
    result = control_geometry(100, .02)
    assert result["levels"] == 8 and result["interventions"] is False
    assert result["low"] == pytest.approx(100 * __import__("math").exp(-.04))


def test_seguidor_recentra_con_enfriamiento_y_protege_costo():
    result = follower_recenter(120, 90, 110, .02, now_s=100_000,
        inventory=[{"quantity": 2, "cost_price": 121}])
    assert result["recenter"] is True
    assert result["inventory_sells"][0]["price"] >= 121
    waiting = follower_recenter(120, 90, 110, .02, last_recenter_s=99_000,
        now_s=100_000, inventory=[{"quantity": 2, "cost_price": 121}])
    assert waiting["recenter"] is False and waiting["reason"] == "cooldown"
