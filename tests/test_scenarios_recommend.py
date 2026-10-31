import pytest

from research.scenarios.recommend import recommend_grid


def test_recommend_grid_usa_probabilidad_y_ganancia_neta_sinteticas():
    captured = {}

    def evaluate(pieces, **kwargs):
        captured["pieces"] = pieces
        captured["kwargs"] = kwargs
        return {"pieces": [
            {"piece": pieces[0], "scenario_results": [
                {"pnl_usdt": 1, "loss_cut": False}, {"pnl_usdt": -1, "loss_cut": True}]},
            {"piece": pieces[1], "scenario_results": [
                {"pnl_usdt": 3, "loss_cut": False}, {"pnl_usdt": 1, "loss_cut": False}]},
        ]}

    result = recommend_grid("XRPUSDT", "simple", 70, 20, price_now=1.0,
        sigma_24h=.04, n_scenarios=2, width_multipliers=(.5, 1.0), evaluator=evaluate)

    assert result["recommendations"][0]["p_profit"] == 1
    assert result["recommendations"][0]["pnl_mean_usdt"] == 2
    assert captured["kwargs"]["max_loss_pct"] == 18
    assert captured["kwargs"]["horizon_days"] == 20
    expected_width = .04 * (20 ** .5)
    assert captured["pieces"][1]["high"] == pytest.approx(__import__("math").exp(expected_width))
    assert result["recommendations"][1]["p_loss"] == .5


def test_recommend_grid_bot_expresa_desvio_y_rechaza_estrategia_desconocida():
    captured = {}

    def evaluate(pieces, **_kwargs):
        captured["pieces"] = pieces
        return {"pieces": []}

    recommend_grid("JUPUSDT", "bot", 20, 10, price_now=.25,
        sigma_24h=.03, bot_offsets=(-1.5,), evaluator=evaluate)

    assert captured["pieces"][0]["buy_offset_pct"] == -1.5
    assert captured["pieces"][0]["sell_offset_pct"] == 1.5
    with pytest.raises(ValueError, match="strategy"):
        recommend_grid("JUPUSDT", "invalida", 20, 10, price_now=.25,
            sigma_24h=.03, evaluator=evaluate)
