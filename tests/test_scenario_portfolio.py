import json
import time

import numpy as np
import pytest

from research.scenarios.portfolio import evaluate_trajectory_portfolio, greedy_portfolio
from grid.sim.meta_portfolio import evaluate_portfolio, pack_curve


def _piece(key, symbol, cash, capital=40):
    equity = np.asarray(cash, dtype=np.float32).copy()
    return {"candidate_key": key, "piece": {"symbol": symbol,
        "strategy": "simple", "capital": capital, "n": 6, "low": .8,
        "high": 1.2}, "capital": capital, "cash": np.asarray(cash, dtype=np.float32),
        "equity": equity, "inventory_qty": np.zeros(len(cash), dtype=np.float32),
        "inventory_value": np.zeros(len(cash), dtype=np.float32), "is_current": False}


def test_portfolio_voraz_agrega_segunda_pieza_y_respeta_limites(tmp_path):
    timestamps = np.asarray([0, 3600, 7200], dtype=np.int64)
    metadata = []
    arrays = {"timestamps": timestamps}
    first = _piece("a", "ZROUSDT", [[40, 40, 46], [40, 40, 40],
        [40, 40, 40], [40, 40, 40]])
    second = _piece("b", "PEPEUSDT", [[40, 40, 40], [40, 40, 46],
        [40, 40, 40], [40, 40, 40]])
    for index, row in enumerate((first, second)):
        metadata.append({"candidate_key": row["candidate_key"], "piece": row["piece"],
            "metrics": {}, "is_current": False})
        for key in ("cash", "equity", "inventory_qty", "inventory_value"):
            arrays[f"{key}_{index}"] = row[key]
    arrays["metadata_json"] = np.asarray(json.dumps(metadata))
    archive = tmp_path / "candidates.npz"
    np.savez(archive, **arrays)

    started = time.perf_counter()
    result = greedy_portfolio(archive, profile="RAPIDO", capital=100, free_cash=80,
        target=5, max_loss_pct=18, horizon_days=2, profile_config={
            "capital_per_coin_pct": 70, "max_grids": 4, "max_bots": 8})
    elapsed = time.perf_counter() - started
    print(f"Tiempo de cartera sintética: {elapsed:.4f} s")

    assert [row["candidate_key"] for row in result["added"]] == ["a", "b"]
    assert [step["p_target_delta_pp"] for step in result["steps"]] == [25, 25]
    assert sum(row["piece"]["capital"] for row in result["selected"]) <= 80
    assert elapsed < 2
    assert result["elapsed_seconds"] < 2


def test_metricas_trayectoria_igualan_evaluate_portfolio():
    timestamps = np.asarray([0, 3600, 7200], dtype=np.int64)
    first = _piece("a", "ZROUSDT", [[40, 40, 46], [40, 20, 20],
        [40, 40, 40], [40, 40, 40]])
    empty = _piece("b", "PEPEUSDT", np.full((4, 3), 40), 40)
    rows = [first, empty]
    metrics = evaluate_trajectory_portfolio(rows, timestamps=timestamps,
        capital=100, target=5, max_loss_pct=18, horizon_days=2)
    outcomes = [evaluate_portfolio([
        pack_curve(timestamps, row["cash"][scenario], row["equity"][scenario], 40)
        for row in rows], capital_initial=100, target_usdt=5, max_loss_pct=18)
        for scenario in range(4)]

    assert metrics["p_target"] == sum(row["reached"] for row in outcomes) / 4
    assert metrics["p_loss"] == sum(row["loss_cut"] for row in outcomes) / 4
    assert metrics["pnl_mean_usdt"] == pytest.approx(
        sum(row["pnl_usdt"] for row in outcomes) / 4, abs=1e-9)


def test_horizonte_restante_recorta_y_alinea_trayectorias_5m_con_horarias():
    timestamps = np.arange(433, dtype=np.int64) * 3600
    timestamps_5m = np.arange(20 * 24 * 12 + 1, dtype=np.int64) * 300
    hourly = np.full((1, len(timestamps)), 100, dtype=np.float32)
    low = np.full((1, len(timestamps_5m)), 100, dtype=np.float32)
    low[0, 10 * 24 * 12] = 80
    row = {"candidate_key": "larga", "piece": {"symbol": "XUSDT"},
        "capital": 100, "cash": hourly, "equity": hourly,
        "inventory_qty": np.zeros(1, dtype=np.float32),
        "inventory_value": np.zeros(1, dtype=np.float32),
        "timestamps_5m": timestamps_5m, "low_equity_5m": low}

    metrics = evaluate_trajectory_portfolio([row], timestamps=timestamps,
        capital=100, target=3, max_loss_pct=18, horizon_days=1.5)

    assert metrics["p_loss"] == 0
    assert metrics["p_target"] == 0
    assert metrics["pnl_mean_usdt"] == pytest.approx(0)


def test_cartera_informa_los_cinco_intentos_no_agregados(tmp_path):
    timestamps = np.asarray([0, 3600, 7200], dtype=np.int64)
    metadata = []
    arrays = {"timestamps": timestamps}
    rows = [_piece(f"x{index}", f"X{index}USDT", np.full((4, 3), 10), 10)
        for index in range(5)]
    for index, row in enumerate(rows):
        metadata.append({"candidate_key": row["candidate_key"], "piece": row["piece"],
            "metrics": {}, "is_current": False})
        for key in ("cash", "equity", "inventory_qty", "inventory_value"):
            arrays[f"{key}_{index}"] = row[key]
    arrays["metadata_json"] = np.asarray(json.dumps(metadata))
    archive = tmp_path / "no-additions.npz"
    np.savez(archive, **arrays)

    result = greedy_portfolio(archive, profile="RAPIDO", capital=100, free_cash=80,
        target=5, max_loss_pct=18, horizon_days=2, profile_config={
            "capital_per_coin_pct": 70, "max_grids": 8, "max_bots": 8})

    assert len(result["not_added_reasons"]) == 5
    assert {row["reason"] for row in result["not_added_reasons"]} == {
        "no sube P(meta) al menos 2 pp"}

