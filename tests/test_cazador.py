import copy

import pytest

from grid.engines.cazador import decide_cazador, piece_activity


@pytest.fixture
def meta7_cazador_context():
    return {"now_s": 2_000_000_000, "capital_initial": 100,
        "result_pct": -3.0, "free_cash": 9.0,
        "profile": {"max_loss_pct": 18, "min_margin_pct": .3,
            "smart_grid_enabled": True},
        "limits": {"max_grids": 4, "max_bots": 8,
            "capital_per_coin_usdt": 70,
            "capital_by_symbol": {"ZROUSDT": 0, "PEPEUSDT": 0, "XRPUSDT": 0, "JUPUSDT": 21}},
        "markets": {
            "ZROUSDT": {"sigma_24h": .005, "low_24h": 1.52,
                "high_24h": 1.58, "mid": 1.55},
            "PEPEUSDT": {"sigma_24h": .10, "low_24h": .000007,
                "high_24h": .000013, "mid": .000010},
            "XRPUSDT": {"sigma_24h": .05, "low_24h": .50,
                "high_24h": .55, "mid": .525},
            "JUPUSDT": {"sigma_24h": .02, "low_24h": .235,
                "high_24h": .265, "mid": .25}},
        "pieces": [{"grid_id": 18, "symbol": "ZROUSDT",
            "piece_type": "individual_bot_grid", "strategy": "bot",
            "capital": 70, "liquidation_value": 60.6,
            "buy_price": 1.789, "sell_price": 1.872,
            "pending_prices": [1.872], "held_qty": 39.12,
            "opened_by_cazador_at_s": None, "last_cycle_at_s": None},
            {"grid_id": 21, "symbol": "JUPUSDT", "piece_type": "individual_bot_grid",
            "strategy": "bot", "capital": 21, "liquidation_value": 21,
            "buy_price": .2425, "sell_price": .2498, "pending_prices": [.2425],
            "held_qty": 0, "opened_by_cazador_at_s": None, "last_cycle_at_s": None}]}


def test_cazador_meta7_rota_bot_dormido_a_cartera_diversa(meta7_cazador_context):
    before = copy.deepcopy(meta7_cazador_context)

    result = decide_cazador(meta7_cazador_context)

    assert result["accion"] == "rotar"
    assert result["pieza"] == 18
    assert result["puntaje_pieza"] < .01
    assert result["puntaje_destino"] >= 1.5 * result["puntaje_pieza"]
    assert result["sigma_por_moneda"]["PEPEUSDT"] >= 2 * result["sigma_por_moneda"]["ZROUSDT"]
    assert {row["symbol"] for row in result["destino"]} >= {"PEPEUSDT", "XRPUSDT"}
    assert sum(row["capital"] for row in result["destino"]) <= 70
    pieza_jup = next(row for row in result["puntajes_piezas"] if row["grid_id"] == 21)
    assert pieza_jup["presupuesto"] == 21  # 9 USDT libres no alcanzan el minimo de agregado.
    assert not any(row["symbol"] == "JUPUSDT" and row["type"] == "bot"
        for row in pieza_jup["destino"])
    assert meta7_cazador_context == before


@pytest.mark.parametrize("piece_update, reason", [
    ({"sell_price": 1.552, "pending_prices": [1.552]}, "venta pendiente"),
    ({"last_cycle_at_s": 2_000_000_000 - 3600}, "ciclo completo"),
    ({"opened_by_cazador_at_s": 2_000_000_000 - 3600}, "abierta por el Cazador"),
])
def test_cazador_no_rota_con_frenos_de_seguridad(meta7_cazador_context,
        piece_update, reason):
    meta7_cazador_context["pieces"][0].update(piece_update)

    result = decide_cazador(meta7_cazador_context)

    assert result["accion"] == "ninguna"
    assert any(reason in item for item in result["puntajes_piezas"][0]["motivos_freno"])


def test_cazador_agrega_con_efectivo_sin_pieza_dormida(meta7_cazador_context):
    meta7_cazador_context["pieces"] = []
    meta7_cazador_context["free_cash"] = 30

    result = decide_cazador(meta7_cazador_context)

    assert result["accion"] == "agregar"
    assert result["destino"]
    assert result["capital_total"] <= 30


def test_puntaje_grid_y_retorna_cero_fuera_de_rango():
    market = {"sigma_24h": .05, "mid": 100}
    inside = {"strategy": "simple", "low": 90, "high": 110, "n_levels": 6}
    outside = {**inside, "low": 80, "high": 90}

    assert 0 < piece_activity(inside, market) <= 10
    assert piece_activity(outside, market) == 0


def test_cazador_rota_grid_grande_si_otra_moneda_tiene_mas_actividad(meta7_cazador_context):
    ctx = meta7_cazador_context
    ctx["markets"]["ZROUSDT"]["sigma_24h"] = .0005
    ctx["pieces"][0].update({"piece_type": "grid", "strategy": "simple",
        "low": 1.50, "high": 1.60, "n_levels": 6, "capital": 60,
        "liquidation_value": 60, "sell_price": None, "pending_prices": []})
    result = decide_cazador(ctx)
    assert result["accion"] == "rotar"
    assert result["pieza"] == 18
    assert result["capital_total"] <= 60


def test_cazador_bloquea_si_presupuesto_de_perdida_esta_agotado(meta7_cazador_context):
    meta7_cazador_context["result_pct"] = -18.0
    result = decide_cazador(meta7_cazador_context)
    assert result["accion"] == "ninguna"
    assert any("presupuesto" in item
        for row in result["puntajes_piezas"] for item in row["motivos_freno"])


def test_cazador_respeta_slots_de_perfil_y_minimos(meta7_cazador_context):
    meta7_cazador_context["limits"].update({"max_grids": 0, "max_bots": 2})
    result = decide_cazador(meta7_cazador_context)
    assert result["accion"] == "rotar"
    assert all(row["type"] == "bot" for row in result["destino"])
    assert all(row["capital"] >= 10 for row in result["destino"])
