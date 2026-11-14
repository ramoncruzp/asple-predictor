import pytest

from grid.engines.torneo import control_geometry, follower_geometry, ladder_orders


def test_escalera_respeta_escalones_tope_y_bot_comprado():
    orders = ladder_orders(100, .02, bought=[{"buy_price": 98}, None, None, None])
    assert [row["buy_price"] for row in orders[1:]] == pytest.approx(
        [100 * __import__("math").exp(-.02), 100 * __import__("math").exp(-.03),
            100 * __import__("math").exp(-.04)])
    assert orders[0]["sell_price"] > 98
    assert all(row["buy_price"] <= 100 * __import__("math").exp(-.3 * .02) for row in orders)
    capped = ladder_orders(100, .02, reprices_today=6,
        previous=[{"buy_price": 90}] * 4)
    assert all(not row["repriced"] and row["buy_price"] == 90 for row in capped)


def test_seguidor_aplica_sesgo_y_geometria_de_ocho_niveles():
    result = follower_geometry(100, .02, inventory_pct=61)
    assert result["levels"] == 8 and result["spacing_sigma"] == .9
    assert result["bias"] == {"buy_shift_levels": -1, "sell_closer": True}
    assert result["recenter_cooldown_hours"] == 6


def test_control_es_fijo_a_dos_sigma():
    result = control_geometry(100, .02)
    assert result["levels"] == 8 and result["interventions"] is False
    assert result["low"] == pytest.approx(100 * __import__("math").exp(-.04))
