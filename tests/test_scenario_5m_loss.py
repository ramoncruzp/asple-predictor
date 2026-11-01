from __future__ import annotations

import numpy as np
import pytest

import scripts.escenarios_cache as cache_script
from grid.sim.runner import FILTERS
from research.scenarios.fastsim import simulate_simple


def _row(*, hourly, low_5m, capital=100):
    hourly = np.asarray(hourly, dtype=np.float32)
    low_5m = np.asarray(low_5m, dtype=np.float32)
    return {"piece": {"capital": capital}, "scenario_outcomes": {
        "trajectory_cash_f32": hourly[None, :],
        "trajectory_equity_f32": hourly[None, :],
        "trajectory_timestamps": np.asarray([0, 3600], dtype=np.int64),
        "trajectory_low_equity_5m_f32": low_5m[None, :],
        "trajectory_timestamps_5m": np.arange(len(low_5m), dtype=np.int64) * 300,
        "inventory_qty": np.asarray([0], dtype=np.float32),
        "inventory_value": np.asarray([0], dtype=np.float32)}}


def test_corte_a_5m_detecta_cruce_y_congela_en_la_confirmacion():
    low = [100, 96, 92, 88, 80, 84, 90, 95, 100, 100, 100, 100]
    result = cache_script._portfolio_outcome_metrics([_row(hourly=[100, 100], low_5m=low)],
        capital=100, target=3, max_loss_pct=17, horizon=1)
    assert result["p_loss"] == 1
    assert result["pnl_mean_usdt"] == pytest.approx(-20, abs=.01)
    assert result["cut_slippage_mean_usdt"] == pytest.approx(3, abs=.01)


def test_corte_a_5m_refleja_salto_de_precio_en_la_vela():
    low = [100, 100, 100, 100, 90, 100, 100, 100, 100, 100, 100, 100]
    result = cache_script._portfolio_outcome_metrics([_row(hourly=[100, 100], low_5m=low)],
        capital=100, target=3, max_loss_pct=5, horizon=1)
    assert result["p_loss"] == 1
    assert result["pnl_mean_usdt"] == pytest.approx(-10, abs=.01)
    assert result["cut_slippage_mean_usdt"] == pytest.approx(5, abs=.01)


def test_confirmacion_se_redondea_a_velas_de_cinco_minutos():
    low = [100, 80, 100, 100, 100, 100]
    row = _row(hourly=[100, 100], low_5m=low)
    one_breach = cache_script._portfolio_outcome_metrics([row], capital=100,
        target=3, max_loss_pct=17, horizon=1, loss_confirm_minutes=7)
    assert one_breach["p_loss"] == 0
    low[2] = 80
    row = _row(hourly=[100, 100], low_5m=low)
    two_breaches = cache_script._portfolio_outcome_metrics([row], capital=100,
        target=3, max_loss_pct=17, horizon=1, loss_confirm_minutes=7)
    assert two_breaches["p_loss"] == 1
    assert two_breaches["pnl_mean_usdt"] == pytest.approx(-20, abs=.01)


def test_cartera_una_pieza_coincide_con_fastsim_en_el_corte():
    close = np.asarray([100, 99, 95, 80, 78, 82, 84], dtype=np.float64)
    opened = np.r_[close[0], close[:-1]]
    bars = {"open": opened[None, :], "high": (np.maximum(opened, close) * 1.002)[None, :],
        "low": (np.minimum(opened, close) * .998)[None, :], "close": close[None, :]}
    fast = simulate_simple(bars, n_levels=6, capital=1000, low=70, high=130,
        filters=FILTERS, max_loss_pct=17, target_usdt=5, keep_5m=True)
    hourly = fast["equity_curve"]
    row = {"piece": {"capital": 1000}, "scenario_outcomes": {
        "trajectory_cash_f32": fast["cash_curve"],
        "trajectory_equity_f32": hourly,
        "trajectory_timestamps": fast["timestamps"],
        "trajectory_low_equity_5m_f32": fast["low_equity_5m_f32"],
        "trajectory_timestamps_5m": fast["timestamps_5m"],
        "inventory_qty": fast["inventory_qty"], "inventory_value": fast["inventory_value"]}}
    portfolio = cache_script._portfolio_outcome_metrics([row], capital=1000,
        target=5, max_loss_pct=17, horizon=1)
    assert portfolio["p_loss"] == pytest.approx(float(fast["loss_cut"].mean()), abs=.005)
    assert portfolio["pnl_mean_usdt"] == pytest.approx(float(fast["pnl_usdt"].mean()), abs=.05)


def test_curvas_5m_de_dos_contextos_y_cinco_monedas_quedan_bajo_700_mb():
    import gc
    import time

    baseline = cache_script._rss_max_mb() or 0
    started = time.perf_counter()
    context_peaks = []
    for _context in range(2):
        rows = []
        timestamps = np.arange(15 * 288, dtype=np.int64) * 300
        for _symbol in range(5):
            for _candidate in range(2):
                low = np.full((1000, len(timestamps)), 100, dtype=np.float32)
                rows.append({"piece": {"capital": 10}, "scenario_outcomes": {
                    "trajectory_cash_f32": np.full((1000, 361), 10, dtype=np.float32),
                    "trajectory_equity_f32": np.full((1000, 361), 10, dtype=np.float32),
                    "trajectory_timestamps": np.arange(361, dtype=np.int64) * 3600,
                    "trajectory_low_equity_5m_f32": low,
                    "trajectory_timestamps_5m": timestamps,
                    "inventory_qty": np.zeros(1000, dtype=np.float32),
                    "inventory_value": np.zeros(1000, dtype=np.float32)}})
        metrics = cache_script._portfolio_outcome_metrics(rows, capital=100,
            target=3, max_loss_pct=17, horizon=15)
        assert metrics["n"] == 1000
        del rows, metrics
        gc.collect()
        context_peaks.append(cache_script._rss_max_mb() or 0)
    elapsed = time.perf_counter() - started
    print(f"RSS HWM por contexto sintético (5 monedas): {context_peaks}; "
        f"pico={max(context_peaks, default=baseline):.2f} MB; tiempo={elapsed:.3f} s")
    assert max(context_peaks, default=0) < 700
