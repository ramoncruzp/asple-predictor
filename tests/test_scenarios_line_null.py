import numpy as np

from grid.sim.data import CandleData
from grid.sim.meta_portfolio import pack_curve
from research.scenarios.evaluate import evaluate_history, _summarize_null_comparison


class _SyntheticStore:
    def __init__(self, _path):
        pass

    def load(self, _symbol, _interval):
        count = 65 * 288 + 1
        rng = np.random.default_rng(91)
        returns = rng.normal(.00001, .0008, count - 1)
        close = 100 * np.exp(np.r_[0, np.cumsum(returns)])
        open_ = np.r_[close[0], close[:-1]]
        high = np.maximum(open_, close) * 1.001
        low = np.minimum(open_, close) * .999
        ts = 1_700_000_100 + np.arange(count, dtype=np.int64) * 300
        return CandleData(ts, open_, high, low, close, 0)

    def filters(self, _symbol):
        return None


def test_null_comparison_reports_bridge_and_paired_effects_by_date():
    result = _summarize_null_comparison([
        {"timestamp": 1_700_000_000, "real_history": 2, "null_history": 1,
            "scenario_real": .5, "scenario_null": .25},
        {"timestamp": 1_700_000_000 + 7 * 86400, "real_history": 3,
            "null_history": 1.5, "scenario_real": .5, "scenario_null": .25},
    ], seed=4, replicas=100, scenarios_per_start=200, step_days=7)
    null = result["historia_sin_tendencia_menos_escenarios_sin_tendencia"]
    real = result["historia_real_menos_escenarios"]
    assert null["media_usdt"] == 1.0
    assert null["ic95_usdt"][0] > 0
    assert real["media_usdt"] == 2.0
    assert result["evidencia_rebote_real"] is True


def test_history_computes_real_and_detrended_fhs_with_same_start_seed(monkeypatch):
    import research.scenarios.evaluate as module

    monkeypatch.setattr(module, "CandleStore", _SyntheticStore)
    call_count = {"simple": 0}

    def fake_simple(_bars, *, capital, **kwargs):
        call_count["simple"] += 1
        pnl = 2.0 if call_count["simple"] % 2 else 1.0
        curves = [pack_curve([0, 900], [capital, capital + pnl],
            [capital, capital + pnl], capital) for _ in range(len(_bars["close"]))]
        n = len(curves)
        return {"curves": curves, "cycles": np.zeros(n), "inventory_qty": np.zeros(n),
            "inventory_value": np.zeros(n), "buys": np.zeros(n, dtype=int),
            "sells": np.zeros(n, dtype=int)}

    scenario_flags = []
    scenario_calls = []
    def fake_evaluate(_pieces, *, detrend_scenarios=False, seed, before, **_kwargs):
        scenario_flags.append(detrend_scenarios)
        scenario_calls.append((seed, before))
        pnl = .25 if detrend_scenarios else .5
        return {"pieces": [{"scenario_results": [{"pnl_usdt": pnl}, {"pnl_usdt": pnl}]}]}

    monkeypatch.setattr("research.scenarios.fastsim.simulate_simple", fake_simple)
    monkeypatch.setattr(module, "evaluate_pieces", fake_evaluate)
    result = evaluate_history([{"symbol": "SYNTHUSDT", "strategy": "simple",
        "capital": 1000, "n": 6, "width_sigma": 1}], perfil="RAPIDO",
        capital=1000, meta=3, db_path="unused.sqlite", months=2, step_days=7,
        replicas=10, scenario_null=True, null_scenarios=2, null_replicas=100)
    row = result["symbols"]["SYNTHUSDT"]["pieces"][0]["linea_nula_fhs"]
    assert row["n_arranques"] > 0
    assert row["escenarios_por_arranque"] == 2
    assert row["historia_sin_tendencia_menos_escenarios_sin_tendencia"]["media_usdt"] > 0
    assert row["historia_real_menos_escenarios"]["media_usdt"] > 0
    assert scenario_flags == [False, True] * row["n_arranques"]
    assert all(scenario_calls[index] == scenario_calls[index + 1]
        for index in range(0, len(scenario_calls), 2))


def test_history_cli_enables_line_null_with_200_scenarios_and_seven_day_step(monkeypatch, capsys):
    import scripts.escenarios_evaluar as cli

    received = {}
    def fake_history(_pieces, **kwargs):
        received.update(kwargs)
        return {"symbols": {}}

    monkeypatch.setattr(cli, "evaluate_history", fake_history)
    monkeypatch.setattr(cli, "_rss_mb", lambda: 1.0)
    assert cli.main(["--db", "synthetic.sqlite", "--perfil", "RAPIDO",
        "--capital", "100", "--meta", "3", "--modo", "historia",
        "--escenarios", "500", "--pieza", "SYNTHUSDT:simple:70:n=6:ancho=1sigma"]) == 0
    assert received["scenario_null"] is True
    assert received["null_scenarios"] == 200
    assert received["null_step_days"] == 7
    assert "rss_max_mb=1.0" in capsys.readouterr().out
