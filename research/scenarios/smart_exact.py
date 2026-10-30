"""Evaluación de Smart mediante el replay exacto de producción, solo para investigación."""
from __future__ import annotations

from multiprocessing import get_context

import numpy as np

from grid.sim.data import CandleData
from grid.sim.meta_portfolio import simulate_piece_curve
from grid.sim.runner import FILTERS

START_TS = 1_700_000_100 // 300 * 300


def _one_smart(task):
    bars, sigma, n, capital, low, high, filters, max_loss_pct, scenario_index = task
    count = bars["close"].shape[0]
    candles = CandleData(START_TS + np.arange(count, dtype=np.int64) * 300,
        bars["open"], bars["high"], bars["low"], bars["close"], 0)
    result = simulate_piece_curve(candles, strategy="smart", n=n, capital=capital,
        low=low, high=high, filters=filters, sigma_values=sigma,
        meta_max_loss_pct=max_loss_pct, include_replay=True)
    replay = result["replay"]
    return {"curve": result["curve"], "metrics": replay["metrics"],
        "events": replay["events"], "effective": replay["effective_params"],
        "scenario_index": scenario_index}


def evaluate_smart_scenarios(scenarios, *, n_levels, capital, low, high,
        filters=None, sigma_path=None, n_smart=200, workers=2, max_loss_pct=None):
    """Reproduce Smart para los primeros escenarios comunes con Simple.

    Desactiva crédito, compound, ADJUST, reespaciado, reducción de niveles ociosos,
    stop-loss por celda y filtro direccional. Mantiene «fuera de rango: esperar».
    Estas opciones reflejan los parámetros de run_simulation y no alteran el operador.
    """
    if not isinstance(scenarios, dict) or n_smart <= 0:
        raise ValueError("lote de escenarios y n_smart positivo requeridos")
    bars = {key: np.asarray(scenarios[key], dtype=np.float64)
        for key in ("open", "high", "low", "close")}
    n = min(int(n_smart), bars["close"].shape[0])
    length = bars["close"].shape[1]
    sigma = (None if sigma_path is None else
        np.repeat(np.asarray(sigma_path, dtype=np.float64)[:n], 288, axis=1)[:, :length])
    filters = FILTERS if filters is None else filters
    tasks = []
    for index in range(n):
        row = {key: value[index].copy() for key, value in bars.items()}
        row_sigma = None if sigma is None else sigma[index].copy()
        tasks.append((row, row_sigma, int(n_levels), float(capital), float(low), float(high),
            filters, max_loss_pct, index))
    if int(workers) > 1 and len(tasks) > 1:
        with get_context("spawn").Pool(processes=int(workers)) as pool:
            return pool.map(_one_smart, tasks, chunksize=1)
    return [_one_smart(task) for task in tasks]


def paired_smart_simple(smart_results, simple_curves, *, capital_initial,
        target_usdt, max_loss_pct=None, replicas=1000, seed=43):
    """Resume diferencia pareada de resultados y probabilidades por escenario."""
    from grid.sim.meta_portfolio import evaluate_portfolio
    count = min(len(smart_results), len(simple_curves))
    if count < 1:
        raise ValueError("se requiere al menos un escenario pareado")
    smart_rows = [evaluate_portfolio([smart_results[i]["curve"]],
        capital_initial=capital_initial, target_usdt=target_usdt,
        max_loss_pct=max_loss_pct) for i in range(count)]
    simple_rows = [evaluate_portfolio([simple_curves[i]],
        capital_initial=capital_initial, target_usdt=target_usdt,
        max_loss_pct=max_loss_pct) for i in range(count)]
    delta = {
        "pnl_usdt": np.asarray([smart_rows[i]["pnl_usdt"] - simple_rows[i]["pnl_usdt"]
            for i in range(count)], dtype=float),
        "loss_probability": np.asarray([float(smart_rows[i]["loss_cut"]) -
            float(simple_rows[i]["loss_cut"]) for i in range(count)], dtype=float),
        "target_probability": np.asarray([float(smart_rows[i]["reached"]) -
            float(simple_rows[i]["reached"]) for i in range(count)], dtype=float),
    }
    rng = np.random.default_rng(seed)
    output = {"n": count}
    for name, values in delta.items():
        means = np.mean(values[rng.integers(0, count, size=(int(replicas), count))], axis=1)
        output[name] = {"mean": float(values.mean()),
            "ci_low": float(np.quantile(means, .025)),
            "ci_high": float(np.quantile(means, .975))}
    return output
