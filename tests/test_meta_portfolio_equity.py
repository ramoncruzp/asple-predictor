"""Efectivo para cumplimiento y patrimonio para riesgo, agregados por Meta."""
from types import SimpleNamespace

import numpy as np
import base64
import json
import pytest

from grid.meta_operator import combine_aligned_replays, MetaOperator
from grid.meta_learning import REPLAY_METHOD_VERSION, fit_signal_weights, stable_template
from grid.sim.data import CandleData
from grid.sim.meta_portfolio import pack_curve, unpack_curve, evaluate_portfolio, simulate_piece_curve
from database.db_manager import DBManager
from datetime import datetime, timezone


def curve(capital, cash, equity=None):
    return pack_curve(np.arange(len(cash)) * 900, cash, cash if equity is None else equity, capital)


def _pack_curve_v2(timestamps, cash, equity, capital):
    """Codificación histórica exacta para comparar el lector v2."""
    import zlib
    def encode(values, dtype):
        raw = np.asarray(values, dtype=dtype).tobytes()
        if dtype == "<f4":
            raw = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 4).T.tobytes()
        return base64.b64encode(zlib.compress(raw, 9)).decode("ascii")
    return {"start_ts": int(timestamps[0]), "step_s": 900, "count": len(timestamps),
        "offsets_i32": None, "curve_format_version": 2,
        "cash_zlib_f32": encode(cash, "<f4"), "equity_zlib_f32": encode(equity, "<f4"),
        "capital_usdt": float(capital)}


def test_delta_xor_v3_is_bit_exact_against_v2_and_reads_v1():
    from grid.sim.meta_portfolio import pack_curve, unpack_curve, CURVE_FORMAT_VERSION

    count = 180 * 24 * 4
    stamps = np.arange(count, dtype=np.int64) * 900
    x = np.arange(count, dtype=np.float64)
    cash = (70 + np.floor(x / 73) * .013).astype("<f4")
    equity = (cash + np.sin(x / 28) * .8 + np.cos(x / 103) * .3).astype("<f4")
    v3 = pack_curve(stamps, cash, equity, 70)
    v2 = _pack_curve_v2(stamps, cash, equity, 70)
    restored = unpack_curve(v3)
    restored_v2 = unpack_curve(v2)
    assert v3["curve_format_version"] == CURVE_FORMAT_VERSION == 3
    for left, right in zip(restored, restored_v2):
        assert np.array_equal(left.view("u4") if left.dtype.kind == "f" else left,
                              right.view("u4") if right.dtype.kind == "f" else right)
    metrics_v3 = evaluate_portfolio([v3], capital_initial=100, target_usdt=3, max_loss_pct=18)
    metrics_v2 = evaluate_portfolio([v2], capital_initial=100, target_usdt=3, max_loss_pct=18)
    assert metrics_v3 == metrics_v2

    legacy = {"start_ts": 0, "step_s": 900, "count": 2, "offsets_i32": None,
        "cash_f32": base64.b64encode(np.array([100, 101], dtype="<f4").tobytes()).decode(),
        "equity_f32": base64.b64encode(np.array([100, 102], dtype="<f4").tobytes()).decode(),
        "capital_usdt": 100}
    assert [row.tolist() for row in unpack_curve(legacy)] == [[0, 900], [100, 101], [100, 102]]

    legacy_equivalent = {"start_ts": 0, "step_s": 900, "count": count, "offsets_i32": None,
        "cash_f32": base64.b64encode(cash.tobytes()).decode(),
        "equity_f32": base64.b64encode(equity.tobytes()).decode(), "capital_usdt": 70}
    metrics_new = evaluate_portfolio([v3], capital_initial=100, target_usdt=1)
    metrics_legacy = evaluate_portfolio([legacy_equivalent], capital_initial=100, target_usdt=1)
    assert metrics_new == metrics_legacy


def test_v3_allocation_shadow_matches_v2_with_realistic_180_day_cache_entry():
    from grid.meta_allocation import evaluate_extended_allocation

    count = 180 * 24 * 4
    timestamps = np.arange(count, dtype=np.int64) * 900
    x = np.arange(count, dtype=np.float64)
    cash = (70 + np.floor(x / 73) * .013).astype("<f4")
    equity = (cash + np.sin(x / 28) * .8 + np.cos(x / 103) * .3).astype("<f4")
    piece_v2 = _pack_curve_v2(timestamps, cash, equity, 70)
    piece_v3 = pack_curve(timestamps, cash, equity, 70)
    def plan(packed):
        return {"alternatives": [{"alternative_id": "a", "pieces": [{"symbol": "XRPUSDT",
            "piece_type": "grid", "strategy": "simple", "capital_usdt": 70}],
            "capital_variants": [{"pieces": [{"symbol": "XRPUSDT", "piece_type": "grid",
                "strategy": "simple", "capital_usdt": 70}],
                "window_results": [{"window_start": "w", "curve": packed}],
                "testnet_openability": {"allowed": True}}],
            "testnet_openability": {"allowed": True}}]}
    result_v2 = evaluate_extended_allocation(plan(piece_v2), {}, free_cash=100,
        capital_initial=100, target_usdt=3, window_days=20, limits={"max_grids": 1, "max_bots": 0})
    result_v3 = evaluate_extended_allocation(plan(piece_v3), {}, free_cash=100,
        capital_initial=100, target_usdt=3, window_days=20, limits={"max_grids": 1, "max_bots": 0})
    assert result_v3 == result_v2

    rows = []
    curve_bytes = 0
    for idx in range(11):
        packed = pack_curve(timestamps + idx * 7 * 24 * 4 * 900, cash, equity, 70)
        curve_bytes += len(json.dumps(packed, separators=(",", ":")))
        rows.append({"window_start": f"w{idx}", "curve": packed, "pnl_usdt": 0.2,
            "days": None, "drawdown_pct": 2.1, "max_advance_usdt": 3.2})
    full = {"method_version": "rolling_7d_v3_equity", "summary": {"probability": .3, "windows": 11},
        "window_results": rows}
    full_bytes = len(json.dumps(full, separators=(",", ":")))
    metric_only = {**full, "window_results": [{k: v for k, v in row.items() if k != "curve"}
        for row in rows], "curves_omitted": True}
    durable_bytes = len(json.dumps(metric_only, separators=(",", ":")))
    assert durable_bytes < 100_000
    assert full_bytes > 100_000
    assert curve_bytes > full_bytes * .95
    estimated_stable_table_mb = durable_bytes * 883 / (1024 * 1024)
    assert estimated_stable_table_mb < 1


def test_grid_a_earns_2_5_and_b_0_5_without_individual_targets():
    outcome = evaluate_portfolio([curve(70, [70, 72.5]), curve(30, [30, 30.5])],
        capital_initial=100, target_usdt=3, max_loss_pct=18)
    assert outcome["reached"] and outcome["days"] == 900 / 86400
    assert outcome["pnl_usdt"] == 3
    # Antes, A cerraba en su mitad del objetivo, sin seguir aportando el exceso.
    legacy, _ = combine_aligned_replays([
        {"window_results": [{"window_start": "w", "pnl_usdt": 1.5}]},
        {"window_results": [{"window_start": "w", "pnl_usdt": .5}]}], 3, 20)
    assert legacy["probability"] == 0


def test_grid_reaches_full_target_with_small_bot_and_unused_capital():
    outcome = evaluate_portfolio([curve(70, [70, 73]), curve(20, [20, 20.125])],
        capital_initial=100, target_usdt=3, max_loss_pct=18)
    assert outcome["reached"]
    assert outcome["capital_unused_usdt"] == 10
    assert outcome["cash_final"] == 103.125


def test_separate_crossings_do_not_imply_a_portfolio_crossing():
    outcome = evaluate_portfolio([curve(70, [70, 73, 67]), curve(30, [30, 27, 33])],
        capital_initial=100, target_usdt=3)
    assert not outcome["reached"]
    assert outcome["pnl_usdt"] == 0


def test_inventory_profit_is_progress_but_never_cash_success():
    outcome = evaluate_portfolio([curve(100, [100, 80, 100], [100, 110, 110])],
        capital_initial=100, target_usdt=3)
    assert not outcome["reached"]
    assert outcome["pnl_usdt"] == 10


def test_portfolio_loss_cut_precedes_recovery_and_uses_initial_capital():
    outcome = evaluate_portfolio([curve(70, [70, 65, 75], [70, 60, 75]),
        curve(20, [20, 12, 25], [20, 12, 25])], capital_initial=100, target_usdt=3, max_loss_pct=18)
    assert outcome["loss_cut"] and not outcome["reached"]
    assert outcome["pnl_usdt"] == -18
    assert outcome["worst_loss_vs_capital_pct"] == 18
    assert outcome["drawdown_pct"] == 18


def test_single_piece_uses_same_method_as_portfolio_and_float32_storage():
    packed = curve(70, [70, 73], [70, 74])
    expected = evaluate_portfolio([packed], capital_initial=100, target_usdt=3)
    summary, rows = combine_aligned_replays([{"window_results": [{"window_start": "w", "curve": packed}]}],
        3, 20, capital_initial=100)
    assert rows[0] == {"window_start": "w", **expected}
    assert summary["probability"] == 1
    assert unpack_curve(packed)[1].dtype == np.dtype("<f4")


def test_timestamp_intersection_avoids_adding_different_moments():
    a = pack_curve([0, 900, 1800], [70, 75, 70], [70, 75, 70], 70)
    b = pack_curve([0, 1800], [30, 30], [30, 30], 30)
    assert not evaluate_portfolio([a, b], capital_initial=100, target_usdt=3)["reached"]


def test_real_bot_curve_keeps_cash_after_sale_without_rebuying_or_target_stop():
    times = np.arange(0, 7200, 300)
    candles = CandleData(timestamp=times, open=np.full(len(times), 100.),
        high=np.full(len(times), 102.), low=np.full(len(times), 98.),
        close=np.full(len(times), 100.), gaps=0)
    result = simulate_piece_curve(candles, strategy="individual_bot", n=1, capital=20,
        low=99, high=101)
    stamps, cash, equity = unpack_curve(result["curve"])
    assert result["bought"] and result["sold"]
    assert cash[-1] > 20
    assert np.all(cash[1:] == cash[1])
    assert np.all(np.diff(stamps)[:-1] == 900)
    assert stamps[-1] == times[-1]


def test_real_grid_curve_is_stored_every_fifteen_minutes():
    times = np.arange(0, 3 * 3600 + 300, 300)
    prices = np.full(len(times), 100.)
    candles = CandleData(timestamp=times, open=prices, high=prices + .1,
        low=prices - .1, close=prices, gaps=0)
    result = simulate_piece_curve(candles, strategy="simple", n=4, capital=100,
        low=90, high=110)
    stamps, cash, equity = unpack_curve(result["curve"])
    assert stamps[0] == times[0] and stamps[-1] == times[-1]
    assert np.all(np.diff(stamps) == 900)
    assert len(cash) == len(equity) == len(stamps)


def test_old_process_or_persistent_replay_is_rejected(monkeypatch):
    operator = MetaOperator(object(), object(), None, None, None, clock=lambda: 1_800_000_000)
    sample = CandleData(timestamp=np.array([0, 300, 600]), open=np.full(3, 100.),
        high=np.full(3, 101.), low=np.full(3, 99.), close=np.full(3, 100.), gaps=0)
    operator._analysis_timing = {"replay_s": 0}
    operator._load_candles = lambda _symbol: sample
    operator._symbol_filters = lambda _symbol: None
    operator._touch_signal_for = lambda *_args, **_kwargs: ({}, [])
    operator._persistent_cache_get = lambda *_args: {"method_version": "rolling_7d_v2",
        "summary": {"probability": .99}, "window_results": []}
    operator._persistent_cache_set = lambda *_args: None
    called = []
    def replay(*args, **kwargs):
        called.append(1)
        return {"window_start": "w", "curve": curve(100, [100, 100]), "reached": False,
            "days": None, "pnl_usdt": 0, "drawdown_pct": 0, "step_days": 7}
    operator._replay_window = replay
    monkeypatch.setattr("grid.meta_operator.build_windows", lambda *_args: [
        {"start_idx": 0, "end_idx": 3, "start": 0}])
    result = operator._evaluate({"symbol": "XRPUSDT"}, "simple", 100, 3, 90, 110, 4, {"max_days": 20})
    assert result["replay_probability"] == 0
    assert called == [1]
    assert next(iter(operator._cache))[0] == REPLAY_METHOD_VERSION


def test_learning_template_cache_and_fitting_do_not_mix_versions(monkeypatch):
    import grid.meta_learning as learning
    parameters = {"capital_usdt": 100, "target_usdt": 3, "pieces": []}
    current, _ = stable_template(parameters, meta_capital_usdt=100)
    monkeypatch.setattr(learning, "REPLAY_METHOD_VERSION", "rolling_7d_v2")
    old, _ = stable_template(parameters, meta_capital_usdt=100)
    assert current != old
    with pytest.raises(ValueError, match="versiones"):
        fit_signal_weights([{"base_method_version": REPLAY_METHOD_VERSION},
            {"base_method_version": "rolling_7d_v2"}])


def test_learning_migration_recalculates_old_base_but_not_current_base(tmp_path):
    db = DBManager(f"sqlite:///{tmp_path / 'meta-learning-migration.sqlite'}")
    table = db.meta_learning_samples
    common = {"sample_at": datetime.now(timezone.utc), "source_kind": "historical_reconstructed",
        "symbol": "XRPUSDT", "profile": "RAPIDO", "parameters_json": "{}",
        "signals_json": "{}", "provenance_json": "{}", "created_at": datetime.now(timezone.utc)}
    with db.engine.begin() as conn:
        conn.execute(table.insert(), [
            {**common, "sample_key": "current", "base_method_version": REPLAY_METHOD_VERSION,
             "recalculate_base": False},
            {**common, "sample_key": "old", "base_method_version": "rolling_7d_v2",
             "recalculate_base": False},
        ])
    db._migrate_meta_learning_columns()
    with db.engine.connect() as conn:
        rows = conn.execute(table.select().order_by(table.c.sample_key)).mappings().all()
    assert [(row["sample_key"], bool(row["recalculate_base"])) for row in rows] == [
        ("current", False), ("old", True)]
    db.engine.dispose()
