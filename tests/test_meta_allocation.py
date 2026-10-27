import numpy as np

from grid.meta_allocation import evaluate_extended_allocation, rank_with_tie_tolerance
from grid.sim.meta_portfolio import pack_curve


def _curve_alternative(symbol, kind="grid", *, winners=0, capital=10):
    rows = []
    for index in range(100):
        gain = capital * .6 if index < winners else 0
        rows.append({"window_start": f"w{index:03d}", "curve": pack_curve(
            np.array([0, 900]), np.array([capital, capital + gain]),
            np.array([capital, capital + gain]), capital)})
    return {"alternative_id": symbol, "symbol": symbol, "strategy": "simple",
        "testnet_openability": {"allowed": True}, "pieces": [{"symbol": symbol,
            "strategy": "simple", "piece_type": kind, "capital_usdt": capital,
            "n_levels": 4, "floor": 90, "ceiling": 110}], "window_results": rows}


def _alternative(symbol, kind="grid", *, winners=0, capital=10):
    result = _curve_alternative(symbol, kind, winners=winners, capital=capital)
    result["capital_variants"] = [
        {**_curve_alternative(symbol, kind, winners=winners, capital=amount),
            "testnet_openability": {"allowed": True}}
        for amount in (5.5, 10.5, 15.5, 20.5, 25.5, 30.5)]
    return result


def _current():
    rows = [{"window_start": f"w{index:03d}", "curve": pack_curve(
        np.array([0, 900]), np.array([70, 70]), np.array([70, 70]), 70)}
        for index in range(100)]
    return {"12": {"grid_id": 12, "piece": {"symbol": "AAAUSDT", "piece_type": "grid",
        "capital_usdt": 70}, "estimate": {"probability": .0}, "window_results": rows}}


def test_extended_allocation_adds_three_point_replay_gain_and_respects_caps():
    result = evaluate_extended_allocation({"alternatives": [_alternative("BBBUSDT", winners=3)]},
        _current(), free_cash=30, capital_initial=100, target_usdt=3, window_days=20,
        limits={"capital_per_coin_pct": 70, "max_grids": 2, "max_bots": 1, "max_loss_pct": 18})
    assert result["decision"] == "agregar"
    assert result["selected"]["delta_probability_pp"] >= 2
    assert 5 <= result["selected"]["capital_usdt"] <= 30
    assert "evaluated_top" in result


def test_extended_allocation_rejects_unopenable_and_coin_cap_exhausted():
    candidate = _alternative("AAAUSDT", winners=100)
    candidate["testnet_openability"] = {"allowed": False, "reason": "libro unilateral"}
    result = evaluate_extended_allocation({"alternatives": [candidate]}, _current(),
        free_cash=30, capital_initial=100, target_usdt=3, window_days=20,
        limits={"capital_per_coin_pct": 70, "max_grids": 2, "max_bots": 1})
    assert result["decision"] == "no_agregar"
    assert result["selected"] is None


def test_allocation_materiality_thresholds(monkeypatch):
    import grid.meta_operator as operator_module

    candidate = _alternative("BBBUSDT", winners=3)
    current = _current()
    def decide(probability, progress):
        def combine(alternatives, *_args, **_kwargs):
            if len(alternatives) == 1:
                return {"probability": .40, "median_progress_pct": 50,
                    "days_median": 10, "worst_drawdown_pct": 4}, [{}]
            return {"probability": probability, "median_progress_pct": progress,
                "days_median": 9, "worst_drawdown_pct": 4}, [{}]
        monkeypatch.setattr(operator_module, "combine_aligned_replays", combine)
        return evaluate_extended_allocation({"alternatives": [candidate]}, current,
            free_cash=30, capital_initial=100, target_usdt=3, window_days=20,
            limits={"capital_per_coin_pct": 70, "max_grids": 2, "max_bots": 1})

    assert decide(.43, 50)["decision"] == "agregar"
    assert decide(.41, 57.5)["decision"] == "agregar"
    assert decide(.41, 52.5)["decision"] == "no_agregar"
    assert decide(.39, 45)["decision"] == "no_agregar"


def test_allocation_obeys_grid_bot_coin_and_free_cash_limits():
    current = _current()
    candidate = _alternative("AAAUSDT", winners=100)
    result = evaluate_extended_allocation({"alternatives": [candidate]}, current,
        free_cash=30, capital_initial=100, target_usdt=3, window_days=20,
        limits={"capital_per_coin_pct": 70, "max_grids": 1, "max_bots": 0})
    assert result["decision"] == "no_agregar"
    result = evaluate_extended_allocation({"alternatives": [_alternative("BBBUSDT", "individual_bot_grid", winners=100)]},
        current, free_cash=4, capital_initial=100, target_usdt=3, window_days=20,
        limits={"capital_per_coin_pct": 70, "max_grids": 2, "max_bots": 1})
    assert result["decision"] == "no_agregar"


def test_extended_allocation_caps_new_capital_for_an_already_used_symbol():
    current = _current()
    current["12"]["piece"]["capital_usdt"] = 64
    candidate = _alternative("AAAUSDT", winners=100, capital=10)
    result = evaluate_extended_allocation({"alternatives": [candidate]}, current,
        free_cash=30, capital_initial=100, target_usdt=3, window_days=20,
        limits={"capital_per_coin_pct": 70, "max_grids": 2, "max_bots": 1})
    assert result["decision"] == "agregar"
    assert result["selected"]["capital_usdt"] <= 6


def test_rank_with_tie_tolerance_uses_progress_then_days_then_drawdown():
    rows = [
        {"alternative_id": "prob", "probability_replay": .55, "median_progress_pct": 20,
            "days_median": 8, "worst_drawdown_pct": 5},
        {"alternative_id": "progress", "probability_replay": .51, "median_progress_pct": 40,
            "days_median": 10, "worst_drawdown_pct": 6},
        {"alternative_id": "days", "probability_replay": .50, "median_progress_pct": 40,
            "days_median": 5, "worst_drawdown_pct": 4},
    ]
    assert [row["alternative_id"] for row in rank_with_tie_tolerance(rows, 5)] == ["days", "progress", "prob"]


def test_allocator_ranks_probability_outside_tie_tolerance_before_progress(monkeypatch):
    import grid.meta_operator as operator_module

    alternatives = [_alternative("LOWUSDT", winners=100), _alternative("HIGHUSDT", winners=100)]
    for item, probability in zip(alternatives, (.42, .50)):
        item["estimate"] = {"probability_replay": probability}
        item["replay_probability"] = probability
    def combine(rows, *_args, **_kwargs):
        if len(rows) == 1:
            if rows[0]["pieces"][0]["symbol"] == "AAAUSDT":
                return {"probability": .40, "median_progress_pct": 20}, [{}]
            return {"probability": .50, "median_progress_pct": 100}, [{}]
        symbol = rows[-1]["pieces"][0]["symbol"]
        probability = .42 if symbol == "LOWUSDT" else .50
        return {"probability": probability, "median_progress_pct": 10 if symbol == "HIGHUSDT" else 100,
            "days_median": 10, "worst_drawdown_pct": 1}, [{}]
    monkeypatch.setattr(operator_module, "combine_aligned_replays", combine)
    result = evaluate_extended_allocation({"alternatives": alternatives}, _current(),
        free_cash=30, capital_initial=100, target_usdt=3, window_days=20,
        limits={"capital_per_coin_pct": 70, "max_grids": 2, "max_bots": 1},
        tie_tolerance_pp=5)
    assert result["selected"]["symbol"] == "HIGHUSDT"
    assert result["selected"]["probability_replay"] == .50
