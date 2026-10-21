from __future__ import annotations

import numpy as np
import json
from decimal import Decimal

from grid.sim.data import CandleData
from data.exchange_filters import SymbolFilters
from grid.levels import compute_lines
from config.settings import Settings
from grid.policy import DEFAULT_SMART_PARAMS, validate_params
from scripts.bench_smart_oos import (DEFAULT_VARIANTS, history_before_window, paired_block_bootstrap,
                                     load_symbol_filters, run_benchmark, summarize)


def _hourly(close):
    close = np.asarray(close, dtype=float)
    ts = np.arange(len(close), dtype=np.int64) * 3600
    return CandleData(ts, close.copy(), close * 1.01, close * .99, close.copy(), 0)


def test_history_for_range_ends_strictly_before_simulation_window():
    data = _hourly(np.linspace(100, 130, 12))
    history = history_before_window(data, 8 * 3600, 6 * 3600)
    assert history.timestamp.tolist() == [2 * 3600, 3 * 3600, 4 * 3600, 5 * 3600, 6 * 3600, 7 * 3600]
    changed_future = _hourly(np.r_[data.close[:8], [900, 1, 700, 2]])
    mutated_history = history_before_window(changed_future, 8 * 3600, 6 * 3600)
    assert np.array_equal(history.close, mutated_history.close)


def test_benchmark_summary_calculates_synthetic_known_pnl_and_tail():
    variants = [{"name": "ref", "reference": True}, {"name": "candidate"}]
    rows = []
    for index, (reference, candidate) in enumerate(((1.0, 2.0), (2.0, 1.0), (3.0, -1.0))):
        rows.append({"start_iso": f"window-{index}", "results": {
            "ref": {"metrics": {"pnl_total_net_usdt": reference, "max_drawdown_pct": 1,
                                 "pause_pct": 5}},
            "candidate": {"metrics": {"pnl_total_net_usdt": candidate, "max_drawdown_pct": 2,
                                       "pause_pct": 10}},
        }})
    result = summarize(rows, variants, 7)
    assert result["variants"]["candidate"]["mean_pnl"] == (2 + 1 - 1) / 3
    assert result["variants"]["candidate"]["p5_pnl"] == -.8
    assert result["variants"]["candidate"]["worst_pnl"] == -1
    assert result["variants"]["candidate"]["mean_drawdown_pct"] == 2
    assert result["variants"]["candidate"]["mean_live_time_pct"] == 90
    assert result["variants"]["candidate"]["vs_reference"]["wins"] == 1


def test_paired_block_bootstrap_is_deterministic():
    differences = [1, -2, 3, -4, 5, -6, 7, -8, 9, -10, 11]
    first = paired_block_bootstrap(differences, block=5, samples=300, seed=34)
    second = paired_block_bootstrap(differences, block=5, samples=300, seed=34)
    assert first == second
    assert first["windows"] == 11 and first["block"] == 5


def test_pepe_symbol_filters_keep_grid_levels_above_zero(tmp_path):
    path = tmp_path / "pepe_filters.json"
    path.write_text(json.dumps({"symbols": [{"symbol": "PEPEUSDT", "filters": [
        {"filterType": "PRICE_FILTER", "tickSize": "0.00000001",
         "minPrice": "0.00000001", "maxPrice": "1000"},
        {"filterType": "LOT_SIZE", "stepSize": "1", "minQty": "1", "maxQty": "100000000000"},
        {"filterType": "MIN_NOTIONAL", "minNotional": "5", "applyToMarket": True},
    ]}]}), encoding="utf-8")
    filters = load_symbol_filters(path, "PEPE")
    assert filters.tick_size == Decimal("0.00000001")
    lines = compute_lines("0.000009", "0.000011", 10, filters)
    assert all(line > 0 for line in lines)


def test_benchmark_passes_symbol_filters_into_level_selection_and_runner(tmp_path):
    path = tmp_path / "pepe_filters.json"
    path.write_text(json.dumps({"symbol": "PEPEUSDT", "filters": [
        {"filterType": "PRICE_FILTER", "tickSize": "0.00000001",
         "minPrice": "0.00000001", "maxPrice": "1000"},
        {"filterType": "LOT_SIZE", "stepSize": "1", "minQty": "1", "maxQty": "100000000000"},
        {"filterType": "MIN_NOTIONAL", "minNotional": "5", "applyToMarket": True},
    ]}), encoding="utf-8")
    filters = load_symbol_filters(path, "PEPE")
    hourly_count = 8 * 24
    hourly_close = 0.00001 * np.exp(.05 * np.sin(np.arange(hourly_count) / 5)
                                    + .0001 * np.arange(hourly_count))
    hourly = _hourly(hourly_close)
    ts5 = np.arange((8 * 24 * 60) // 5, dtype=np.int64) * 300
    close5 = np.interp(ts5, hourly.timestamp, hourly.close, right=hourly.close[-1])
    candles5 = CandleData(ts5, close5.copy(), close5 * 1.001, close5 * .999, close5.copy(), 0)
    result = run_benchmark(candles5, hourly, symbol="PEPEUSDT", capital=1000,
        variants=[{"name": "simple", "strategy": "simple", "reference": True}],
        window_days=2, step_days=1, history_days=2, fee_pct=0, filters=filters,
        filters_source=str(path))
    assert result["summary"]["usable_windows"] >= 3
    assert result["summary"]["skipped_windows"] == 0


def test_walk_forward_benchmark_runs_on_synthetic_hourly_and_five_minute_candles():
    hourly_count = 8 * 24
    hourly_close = 100 * np.exp(.1 * np.sin(np.arange(hourly_count) / 5)
                                + .0001 * np.arange(hourly_count))
    hourly = _hourly(hourly_close)
    ts5 = np.arange((8 * 24 * 60) // 5, dtype=np.int64) * 300
    close5 = np.interp(ts5, hourly.timestamp, hourly.close, right=hourly.close[-1])
    close5 *= np.exp(.0002 * np.sin(np.arange(len(ts5)) / 3))
    candles5 = CandleData(ts5, close5.copy(), close5 * 1.001, close5 * .999, close5.copy(), 0)
    result = run_benchmark(candles5, hourly, symbol="TESTUSDT", capital=1000,
        variants=[{"name": "reference", "strategy": "simple", "reference": True}],
        window_days=2, step_days=1, history_days=2, range_mode="centered", fee_pct=0)
    assert result["summary"]["usable_windows"] >= 3
    assert all("results" in row for row in result["windows"])
    assert all(row["start"] >= candles5.timestamp[0] + 2 * 86400 for row in result["windows"])


def test_smart_actual_variant_matches_settings_switches_and_policy_defaults():
    variant = next(row for row in DEFAULT_VARIANTS if row["name"] == "smart_actual")
    settings = Settings.model_construct()
    assert variant["adjust"] is settings.smart_adjust_enabled is False
    assert variant["close_wait"] is settings.smart_close_wait_enabled is True
    assert variant["stoploss"] is settings.stoploss_enabled is False
    assert variant["vol_spacing"] is settings.advisor_vol_spacing_enabled is True
    assert variant["params"] == {}
    effective = validate_params(variant["params"], 10)
    assert {key: effective[key] for key in DEFAULT_SMART_PARAMS} == DEFAULT_SMART_PARAMS
