import json
import sqlite3

from research.scenarios.objective import (exploration_objective_v2, objective_limits, objective_v2,
    rank_alternatives_v2)


def _metrics(**overrides):
    row = {"pnl_mean_usdt": -.5, "cvar_5_usdt": -5.0,
        "cut_slippage_mean_usdt": 1.0, "cut_slippage_p90_usdt": 2.0,
        "cut_slippage_expected_usdt": .01, "p_loss": .01, "p_target": .3,
        "days_to_target_p50": 8.0, "p_loss_ci95": [.01, .04]}
    row.update(overrides)
    return row


def test_objective_reports_each_infeasibility_reason():
    ev = objective_v2(_metrics(pnl_mean_usdt=-1.01), perfil="RAPIDO",
        capital_meta=100, max_loss_pct=18, p_limit_cap=5)
    assert not ev["factible"] and "ganancia esperada" in ev["motivos"][0]

    risk = objective_v2(_metrics(p_loss_ci95=[.01, .051]), perfil="RAPIDO",
        capital_meta=100, max_loss_pct=18, p_limit_cap=5)
    assert not risk["factible"] and "límite superior" in risk["motivos"][0]

    tail = objective_v2(_metrics(cut_slippage_expected_usdt=.51), perfil="RAPIDO",
        capital_meta=100, max_loss_pct=18, p_limit_cap=15)
    assert not tail["factible"] and "desliz esperado" in tail["motivos"][0]

    cvar_is_informative_only = objective_v2(_metrics(cvar_5_usdt=-99,
        cut_slippage_mean_usdt=1.0, cut_slippage_expected_usdt=.01), perfil="RAPIDO", capital_meta=100,
        max_loss_pct=18, p_limit_cap=15)
    assert cvar_is_informative_only["factible"]

    no_ci = objective_v2({key: value for key, value in _metrics().items()
        if key != "p_loss_ci95"}, perfil="RAPIDO", capital_meta=100)
    assert not no_ci["factible"] and "metrica_incompleta" in no_ci["motivos"][0]


def test_objective_incompleto_no_lanza_error_y_es_no_factible():
    result = objective_v2({"p_target": .2, "p_loss": 0}, perfil="RAPIDO",
        capital_meta=100)
    assert result["factible"] is False
    assert result["motivos"][0].startswith("metrica_incompleta")


def test_rank_alternatives_uses_target_then_days_then_mean():
    rows = [
        {"piece": {"label": "dias_8"}, "metrics": _metrics(p_target=.5,
            days_to_target_p50=8, pnl_mean_usdt=.4)},
        {"piece": {"label": "dias_5_menor_ev"}, "metrics": _metrics(p_target=.5,
            days_to_target_p50=5, pnl_mean_usdt=.1)},
        {"piece": {"label": "dias_5_mayor_ev"}, "metrics": _metrics(p_target=.5,
            days_to_target_p50=5, pnl_mean_usdt=.2)},
        {"piece": {"label": "menos_meta"}, "metrics": _metrics(p_target=.4,
            days_to_target_p50=1, pnl_mean_usdt=1.0)},
    ]
    ranked = rank_alternatives_v2(rows, perfil="RAPIDO", capital_meta=100,
        max_loss_pct=18, p_limit_cap=15)
    assert [row["piece"]["label"] for row in ranked] == [
        "dias_5_mayor_ev", "dias_5_menor_ev", "dias_8", "menos_meta"]
    assert all(row["objetivo"]["factible"] for row in ranked)


def test_rank_returns_usdt_quiet_when_no_candidate_is_feasible():
    ranked = rank_alternatives_v2([
        {"piece": {"label": "mala"}, "metrics": _metrics(pnl_mean_usdt=-5)}
    ], perfil="RAPIDO", capital_meta=100)
    assert ranked[0]["piece"]["strategy"] == "usdt_quieto"
    assert ranked[0]["objetivo"]["factible"] is True
    assert ranked[1]["objetivo"]["factible"] is False


def test_objective_limits_read_profile_json_from_sqlite_read_only(tmp_path):
    path = tmp_path / "settings.sqlite"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE meta_profile_settings(profile TEXT PRIMARY KEY, settings TEXT NOT NULL)")
        conn.execute("INSERT INTO meta_profile_settings VALUES(?,?)",
            ("RAPIDO", json.dumps({"max_loss_pct": 17, "p_limit_cap": 12,
                "ev_floor_pct": .5, "cut_slippage_expected_max_pct": .7})))
    limits = objective_limits("RAPIDO", settings_db=path)
    assert limits == {"max_loss_pct": 17.0, "p_limit_cap": 12.0, "ev_floor_pct": .5,
        "cut_slippage_expected_max_pct": .7}
    assert objective_limits("PRUDENTE", settings_db=tmp_path / "missing.sqlite") == {
        "max_loss_pct": 8.0, "p_limit_cap": 5.0, "ev_floor_pct": 1.0,
        "cut_slippage_expected_max_pct": 0.5}


def test_desliz_esperado_f1_con_casos_s1_y_sin_cortes():
    zro = objective_v2(_metrics(pnl_mean_usdt=1, p_loss=.008,
        cut_slippage_mean_usdt=15.08, cut_slippage_expected_usdt=.008 * 15.08),
        perfil="RAPIDO", capital_meta=100)
    pepe = objective_v2(_metrics(pnl_mean_usdt=1, p_loss=.005,
        cut_slippage_mean_usdt=9.79, cut_slippage_expected_usdt=.005 * 9.79),
        perfil="RAPIDO", capital_meta=100)
    high_risk = objective_v2(_metrics(pnl_mean_usdt=1, p_loss=.10,
        cut_slippage_mean_usdt=8, cut_slippage_expected_usdt=.8),
        perfil="RAPIDO", capital_meta=100)
    no_cuts = objective_v2(_metrics(pnl_mean_usdt=1, p_loss=0,
        cut_slippage_mean_usdt=0, cut_slippage_expected_usdt=0),
        perfil="RAPIDO", capital_meta=100)
    assert zro["factible"] and pepe["factible"]
    assert not high_risk["factible"]
    assert "desliz esperado del corte supera 0,5 % del capital" in high_risk["motivos"]
    assert no_cuts["factible"]


def test_objetivo_exploracion_quita_piso_ev_y_conserva_frenos_de_riesgo():
    metrics = {"pnl_mean_usdt": -0.5, "p_target": 0.01,
        "p_loss_ci95": [0.0, 0.01], "cut_slippage_expected_usdt": .1}
    result = exploration_objective_v2(metrics, perfil="RAPIDO", capital_meta=100)
    assert result["factible"]
    metrics["p_loss_ci95"] = [0.0, .2]
    rejected = exploration_objective_v2(metrics, perfil="RAPIDO", capital_meta=100)
    assert not rejected["factible"]
    assert any("P(p" in reason for reason in rejected["motivos"])
