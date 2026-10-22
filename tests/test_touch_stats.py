from types import SimpleNamespace

import numpy as np
import pytest

from grid.sim.touch_stats import aggregate_touch_stats, touch_stats


def candles(lows, highs):
    return SimpleNamespace(low=np.asarray(lows, dtype=float), high=np.asarray(highs, dtype=float))


def test_touch_then_later_sell_reports_delay_and_adverse_excursion():
    result = touch_stats(candles([11, 9, 8, 10.5, 10.5], [11.2, 10.5, 10.8, 12, 11]), 10, 12, 3)
    assert result["touches"] == result["sold_within_horizon"] == 1
    assert result["sell_rate"] == 1
    assert result["median_candles_to_sell"] == 2
    assert result["adverse_excursion_mean_pct"] == pytest.approx(20)
    assert result["adverse_excursion_max_pct"] == pytest.approx(20)


def test_unsold_touch_uses_the_full_horizon_and_adjacent_touches_are_deduplicated():
    result = touch_stats(candles([9, 8, 9, 11, 11], [10.5, 10.5, 10.5, 11, 11]), 10, 12, 2)
    assert result["touches"] == 1
    assert result["sold_within_horizon"] == 0
    assert result["sell_rate"] == 0
    assert result["adverse_excursion_mean_pct"] == pytest.approx(20)


def test_touch_rearms_after_price_moves_up_half_the_cell():
    result = touch_stats(candles([9, 10.5, 9, 10.5, 11, 11],
                                 [10.5, 11, 10.5, 12, 11, 11]), 10, 12, 2)
    assert result["touches"] == 2
    assert result["sold_within_horizon"] == 1


def test_touch_rearms_after_a_sale():
    result = touch_stats(candles([9, 12, 9, 12, 12], [10, 12, 10, 12, 12]), 10, 12, 2)
    assert result["touches"] == 2
    assert result["sold_within_horizon"] == 2


def test_touch_without_full_horizon_is_censored_not_counted_as_failure():
    result = touch_stats(candles([11, 11, 9, 11], [11.5, 11.5, 10, 11.5]), 10, 12, 2)
    assert result["touches"] == 0
    assert result["sold_within_horizon"] == 0
    assert result["sell_rate"] is None
    assert result["censored_touches"] == 1


def test_no_touches_has_undefined_rate_and_no_sale_samples():
    result = touch_stats(candles([11, 11, 11], [11.5, 11.5, 11.5]), 10, 12, 1)
    assert result["touches"] == 0
    assert result["sell_rate"] is None
    assert result["median_candles_to_sell"] is None


def test_touch_and_sell_in_same_candle_does_not_count_as_a_sale():
    result = touch_stats(candles([9, 11, 11, 11], [12, 11.5, 11.5, 11.5]), 10, 12, 2)
    assert result["touches"] == 1
    assert result["sold_within_horizon"] == 0


def test_aggregate_weights_rates_by_touches_and_keeps_exact_sell_median():
    result = aggregate_touch_stats([
        {"touches": 1, "sold_within_horizon": 1, "sell_candles": [2],
         "adverse_excursion_mean_pct": 4, "adverse_excursion_max_pct": 4, "censored_touches": 1},
        {"touches": 3, "sold_within_horizon": 0, "sell_candles": [],
         "adverse_excursion_mean_pct": 8, "adverse_excursion_max_pct": 10, "censored_touches": 0},
    ])
    assert result["sell_rate"] == pytest.approx(.25)
    assert result["median_candles_to_sell"] == 2
    assert result["adverse_excursion_mean_pct"] == pytest.approx(7)
    assert result["censored_touches"] == 1


def test_meta_signal_reuses_replay_window_and_rescales_levels(monkeypatch, tmp_path):
    from grid.meta_operator import MetaOperator
    from grid.sim.data import CandleData
    from database.db_manager import DBManager
    import grid.meta_operator as operator_module

    count = 20 * 24 * 12
    high = np.full(count, 155.0)
    low = np.full(count, 140.0)
    high[1], low[1] = 160.0, 134.0
    high[2] = 166.0
    data = CandleData(timestamp=np.arange(count, dtype=np.int64) * 300,
        open=np.full(count, 150.0), high=high, low=low, close=np.full(count, 150.0), gaps=0)
    monkeypatch.setattr(operator_module, "build_windows", lambda *_args: [
        {"start": 0, "start_idx": 0, "end_idx": count}])
    db = DBManager(f"sqlite:///{tmp_path / 'touch-signal.sqlite'}")
    operator = MetaOperator(object(), db, None, None, None, clock=lambda: 1_800_000_000,
                            cache_enabled=True, cache_writes=True)
    summary, observations = operator._touch_signal_for(
        "XRPUSDT", "individual_bot", 1, 90, 110, 100, data, 20)
    assert summary["horizons"]["4h"]["touches"] == 1
    assert summary["horizons"]["4h"]["sold_within_horizon"] == 1
    assert summary["horizons"]["4h"]["normalized_value"] == 1
    assert summary["horizons"]["20d"]["touches"] == 0
    assert summary["horizons"]["20d"]["censored_touches"] == 1
    assert observations[0]["buy_relative"] == pytest.approx(.9)
    assert observations[0]["sell_relative"] == pytest.approx(1.1)
    cached, _ = operator._touch_signal_for("XRPUSDT", "individual_bot", 1,
        90, 110, 100, data, 20)
    assert cached == summary
    assert operator._analysis_timing["touch_signal_cache_hits"] == 1
    from sqlalchemy import select
    with db.engine.connect() as conn:
        assert conn.execute(select(db.meta_analysis_cache.c.cache_kind)).scalar_one() == "touch_signal"
    grid_summary, _ = operator._touch_signal_for("XRPUSDT", "simple", 4,
        90, 110, 100, data, 20, windows=[])
    assert len(grid_summary["cells"]) == 4
    db.engine.dispose()


def test_touch_signal_is_ignored_by_probability_and_ranking():
    from copy import deepcopy
    from grid.meta_operator import MetaOperator, rank_alternatives

    db = SimpleNamespace(get_latest_prediction=lambda *_args: None)
    operator = MetaOperator(object(), db, None, None, None)
    base = {"symbol": "XRPUSDT", "strategy": "simple", "replay_probability": .4,
        "estimate": {"probability": .4, "evidence_sufficient": True},
        "pieces": [{"capital_usdt": 50}], "signals_used": []}
    with_signal = deepcopy(base)
    with_signal["touch_signal"] = {"weight": 0, "shadow_only": True,
        "horizons": {"20d": {"sell_rate": 1, "normalized_value": 1}}}
    row = {"symbol": "XRPUSDT", "sigma_24h_open": .01, "score": .8,
           "model_statistics": {"volatility_forecasts": {}}}
    operator._apply_intelligence(base, row, 100, 90, 110, {"signal_weights": {}})
    operator._apply_intelligence(with_signal, row, 100, 90, 110, {"signal_weights": {}})
    assert with_signal["estimate"]["probability"] == base["estimate"]["probability"]
    assert with_signal["signals_used"] == base["signals_used"]
    alternatives = [base, {**with_signal, "symbol": "ADAUSDT", "replay_probability": .6,
        "estimate": {"probability": .6, "probability_replay": .6}}]
    before = [item["symbol"] for item in rank_alternatives(alternatives)]
    alternatives[0]["touch_signal"] = {"weight": 0, "shadow_only": True,
        "horizons": {"20d": {"normalized_value": 1}}}
    assert [item["symbol"] for item in rank_alternatives(alternatives)] == before
