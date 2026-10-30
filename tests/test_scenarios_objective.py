import json
import sqlite3

from research.scenarios.objective import (objective_limits, objective_v2,
    rank_alternatives_v2)


def _metrics(**overrides):
    row = {"pnl_mean_usdt": -.5, "cvar_5_usdt": -5.0, "p_target": .3,
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

    tail = objective_v2(_metrics(cvar_5_usdt=-18.01), perfil="RAPIDO",
        capital_meta=100, max_loss_pct=18, p_limit_cap=15)
    assert not tail["factible"] and "CVaR 5 %" in tail["motivos"][0]

    no_ci = objective_v2({key: value for key, value in _metrics().items()
        if key != "p_loss_ci95"}, perfil="RAPIDO", capital_meta=100)
    assert not no_ci["factible"] and "falta el límite superior" in no_ci["motivos"][0]


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
                "ev_floor_pct": .5})))
    limits = objective_limits("RAPIDO", settings_db=path)
    assert limits == {"max_loss_pct": 17.0, "p_limit_cap": 12.0, "ev_floor_pct": .5}
    assert objective_limits("PRUDENTE", settings_db=tmp_path / "missing.sqlite") == {
        "max_loss_pct": 8.0, "p_limit_cap": 5.0, "ev_floor_pct": 1.0}
