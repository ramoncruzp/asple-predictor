import numpy as np

from research.scenarios.validate import (baseline_window_indices, scale_v1_geometry,
    summarize_validation, v1_reference_price)
from data.market_store import CandleStore
from grid.sim.data import CandleData
from research.scenarios.evaluate import evaluate_pieces
from scripts.escenarios_validar import _rss_max_mb


def test_validation_baseline_uses_only_23_complete_windows_before_t():
    stamps = 1_700_000_000 + np.arange(300 * 288, dtype=np.int64) * 300
    target_index = 250 * 288
    chosen = baseline_window_indices(stamps, int(stamps[target_index]))
    assert len(chosen) == 23
    assert np.all(chosen < target_index)
    assert np.all(chosen + 20 * 288 <= target_index)
    assert np.all(np.diff(chosen) == 7 * 288)


def test_validation_metrics_compute_coverage_pit_brier_and_date_block_ci():
    pairs = []
    for date_index in range(20):
        date = f"2026-01-{date_index + 1:02d}"
        for geometry in range(2):
            samples = [-2.0, -1.0, 0.0, 1.0, 2.0]
            pairs.append({"date": date, "predicted_samples": samples,
                "predicted_p10": -2.0, "predicted_p90": 2.0,
                "predicted_pnl": 0.0, "actual_pnl": .25,
                "v2_p_target": .5, "v1_p_target": .7,
                "actual_target": float(date_index % 2 == 0),
                "v2_p_loss": .1, "v1_p_loss": .2,
                "actual_loss": float(date_index % 3 == 0)})
    result = summarize_validation(pairs, seed=7, replicas=100)
    assert result["n"] == 40
    assert result["coverage_p10_p90"] == 1.0
    assert sum(result["pit_deciles"]) == 40
    assert result["brier_target_v2"] < result["brier_target_v1"]
    assert result["pnl_bias_predicted_minus_actual"] == -.25
    assert result["pnl_bias_ci95"][0] == result["pnl_bias_ci95"][1] == -.25


def test_validation_excludes_pairs_without_v1_windows():
    pairs = [{"date": "2026-01-01", "predicted_pnl": 0, "actual_pnl": 0,
        "predicted_samples": [-1, 0, 1], "predicted_p10": -1,
        "predicted_p90": 1, "v2_p_target": .5, "v1_p_target": None,
        "v2_p_loss": .1, "v1_p_loss": None, "actual_target": 1,
        "actual_loss": 0}]
    result = summarize_validation(pairs, replicas=10)
    assert result["v1_pairs"] == 0
    assert result["v1_excluded_pairs"] == 1
    assert result["brier_target_v1"] is None


def test_v1_scaled_geometry_matches_synthetic_store_inside_and_outside_range(monkeypatch):
    from types import SimpleNamespace
    import grid.levels
    import grid.sim.meta_portfolio
    from research.scenarios.validate import _v1_baseline

    count = 31 * 288 + 20 * 288
    timestamps = np.arange(count, dtype=np.int64) * 300
    opens = np.full(count, 50.0)
    closes = opens.copy()
    data = CandleData(timestamps, opens, np.full(count, 51.0),
        np.full(count, 49.0), closes, 0)
    captured = []
    monkeypatch.setattr(grid.levels, "compute_lines", lambda *args: object())
    monkeypatch.setattr(grid.levels, "plan_cells", lambda *args: [])
    monkeypatch.setattr(grid.sim.meta_portfolio, "simulate_piece_curve",
        lambda candles, **kwargs: (captured.append((kwargs["low"], kwargs["high"])) or
            {"curve": {"start_ts": int(candles.timestamp[0]), "step_s": 300,
                "count": 2, "offsets_i32": None, "curve_format_version": 3,
                "cash_zlib_f32": "", "equity_zlib_f32": "", "capital_usdt": 70}}))
    monkeypatch.setattr(grid.sim.meta_portfolio, "evaluate_portfolio",
        lambda *args, **kwargs: {"reached": False, "loss_cut": False})
    spec = ("simple", 6, 1.0, 70.0)
    common = dict(data=data, window_indexes=[31 * 288], piece_spec=spec,
        low=80, high=120, filters=object(), capital_initial=100, meta=3,
        max_loss_pct=8, fee_pct=.1,
        calibration_timeline=(np.empty(0, dtype=np.int64), np.empty(0)))
    inside_ref = v1_reference_price(80, 120, 100)
    _v1_baseline(reference_price=inside_ref, **common)
    assert captured[-1] == (40.0, 60.0)
    outside_ref = v1_reference_price(80, 120, 150)
    _v1_baseline(reference_price=outside_ref, **common)
    assert np.allclose(captured[-1], (80 * 50 / np.sqrt(80 * 120),
        120 * 50 / np.sqrt(80 * 120)))


def test_validation_reports_no_pairs_without_inventing_metrics():
    assert summarize_validation([]) == {"n": 0, "estado": "sin_pares"}


def test_validate_scenarios_skips_symbol_without_library(tmp_path, monkeypatch):
    import research.scenarios.validate as module

    class FakeStore:
        def __init__(self, _path):
            self.path = _path

    monkeypatch.setattr(module, "CandleStore", FakeStore)
    monkeypatch.setattr(module, "market_db_path", lambda value: value)
    result = module.validate_scenarios(db_path="unused.sqlite", symbols=["GRAMUSDT"],
        library_dir=tmp_path / "no-existe", n_scenarios=2, n_smart=1)
    assert result["pairs"] == []
    assert result["skipped_symbols"] == {"GRAMUSDT": "biblioteca de escenarios ausente"}


def test_validation_rss_helper_reports_peak_on_this_platform():
    peak = _rss_max_mb()
    assert peak is not None
    assert 0 < peak < 600


def test_estimation_measures_two_symbols_by_strategy_family(monkeypatch):
    import scripts.escenarios_validar as cli

    stamps = 1_700_000_000 + np.arange(250 * 288, dtype=np.int64) * 300
    close = np.full(len(stamps), 100.0)
    data = CandleData(stamps, close, close * 1.001, close * .999, close, 0)

    class FakeStore:
        def __init__(self, _path):
            pass

        def load(self, _symbol, _interval):
            return data

    calls = []
    monkeypatch.setattr("data.market_store.CandleStore", FakeStore)
    monkeypatch.setattr(cli, "market_db_path", lambda value: value)
    monkeypatch.setattr(cli, "_operating_symbol_count", lambda _store: 21)
    monkeypatch.setattr(cli, "validate_scenarios", lambda **kwargs: calls.append(kwargs))
    assert cli._estimate("synthetic.sqlite", symbols=["AAA", "BBB"],
        include_smart=False) == 0
    assert len(calls) == 2
    assert {tuple(call["strategies"]) for call in calls} == {("simple", "bot")}
    assert all(call["evaluation_timestamp"] == int(stamps[-20 * 288]) for call in calls)


def test_gbm_like_matching_distribution_has_expected_coverage_and_flat_pit():
    rng = np.random.default_rng(20261030)
    pairs = []
    for index in range(400):
        samples = rng.normal(0, 1, 1000)
        actual = float(rng.normal(0, 1))
        pairs.append({"date": f"2025-{index // 30 + 1:02d}-{index % 30 + 1:02d}",
            "predicted_samples": samples.tolist(), "predicted_p10": float(np.quantile(samples, .1)),
            "predicted_p90": float(np.quantile(samples, .9)), "predicted_pnl": float(samples.mean()),
            "actual_pnl": actual, "v2_p_target": .5, "v1_p_target": .5,
            "actual_target": float(actual > 0), "v2_p_loss": .1, "v1_p_loss": .1,
            "actual_loss": 0.0})
    result = summarize_validation(pairs, seed=9, replicas=200)
    assert .70 <= result["coverage_p10_p90"] <= .90
    assert max(result["pit_deciles"]) < 60
    assert result["pit_chi2"] < 25


def test_future_candle_mutation_does_not_change_before_t_prediction(tmp_path):
    count = 51 * 288
    cutoff_index = 35 * 288
    original = 100 + .5 * np.sin(np.arange(count) / 37)
    datasets = []
    for changed in (False, True):
        close = original.copy()
        if changed:
            tail = np.arange(count - cutoff_index)
            close[cutoff_index:] *= np.exp(tail * .0002)
        open_ = np.r_[close[0], close[:-1]]
        rows = [{"open_ts": index * 300, "open": float(open_[index]),
            "high": float(max(open_[index], close[index]) * 1.001),
            "low": float(min(open_[index], close[index]) * .999),
            "close": float(close[index]), "volume": 1, "quote_volume": 100, "trades": 1}
            for index in range(count)]
        db = tmp_path / ("mutated.sqlite" if changed else "original.sqlite")
        store = CandleStore(db)
        store.upsert("SYNTHUSDT", "5m", rows, "synthetic")
        store.save_symbol_filters("SYNTHUSDT", {"filters": [
            {"filterType": "PRICE_FILTER", "minPrice": "0.0001", "maxPrice": "100000", "tickSize": "0.0001"},
            {"filterType": "LOT_SIZE", "minQty": "0.0001", "maxQty": "100000000", "stepSize": "0.0001"},
            {"filterType": "MIN_NOTIONAL", "minNotional": "0.01", "applyToMarket": True},
        ]})
        datasets.append(db)
    library = tmp_path / "library"
    library.mkdir()
    rng = np.random.default_rng(19)
    z = rng.normal(size=(51, 288)).astype(np.float32)
    values = np.zeros((51, 288, 3), dtype=np.float32)
    values[:, :, 0] = z
    values[:, :, 1] = np.maximum(z, 0) + 2
    values[:, :, 2] = np.minimum(z, 0) - 2
    np.savez_compressed(library / "SYNTHUSDT.npz", values=values,
        dates=np.arange(51, dtype=np.int64) * 86400,
        daily_z_return_sum=z.sum(axis=1), mean_z_return=np.float32(0))
    before = cutoff_index * 300
    piece = [{"symbol": "SYNTHUSDT", "strategy": "simple", "capital": 100,
        "low": 70, "high": 130, "n": 6}]
    results = [evaluate_pieces(piece, perfil="RAPIDO", capital=100, meta=3,
        n_scenarios=3, seed=83, before=before, db_path=db, library_dir=library)
        for db in datasets]
    assert results[0]["sigma_today_24h"] == results[1]["sigma_today_24h"]
    assert results[0]["pieces"][0]["scenario_results"] == results[1]["pieces"][0]["scenario_results"]


def test_validate_scenarios_wires_causal_forecast_actual_and_23_v1_windows(monkeypatch, tmp_path):
    import research.scenarios.validate as module

    count = 250 * 288
    stamps = 1_700_000_000 + np.arange(count, dtype=np.int64) * 300
    close = 100 * np.exp(np.arange(count) * .000001)
    data = CandleData(stamps, close.copy(), close * 1.001, close * .999, close.copy(), 0)
    library_dir = tmp_path / "library"
    library_dir.mkdir()
    np.savez_compressed(library_dir / "SYNTHUSDT.npz", dates=np.arange(3, dtype=np.int64))

    class FakeStore:
        def __init__(self, _path):
            self.path = _path

        def load(self, _symbol, _interval):
            return data

        def filters(self, _symbol):
            from grid.sim.runner import FILTERS
            return FILTERS

    seen_before = []
    def fake_prediction(pieces, *, before, **_kwargs):
        seen_before.append(before)
        return {"sigma_today_24h": {"SYNTHUSDT": .01}, "pieces": [{"scenario_results": [
            {"pnl_usdt": float(value), "reached": value > 0, "loss_cut": value < -10}
            for value in (-2, -1, 0, 1, 2)]} for _ in pieces]}

    monkeypatch.setattr(module, "CandleStore", FakeStore)
    monkeypatch.setattr(module, "market_db_path", lambda value: value)
    monkeypatch.setattr(module, "evaluate_pieces", fake_prediction)
    monkeypatch.setattr(module, "_v1_baseline", lambda *args, **kwargs: {
        "n": 23, "excluded_windows": 0, "p_target": 1.0, "p_loss": 0.0})
    monkeypatch.setattr(module, "_run_actual_piece", lambda *args, **kwargs: {
        "pnl_usdt": -.5, "reached": False, "loss_cut": False})
    monkeypatch.setattr("research.scenarios.evaluate._calibration_factor_timeline",
        lambda *args: (np.empty(0, dtype=np.int64), np.empty(0)))
    result = module.validate_scenarios(db_path="synthetic.sqlite", months=1, step_days=30,
        symbols=["SYNTHUSDT"], n_scenarios=5, n_smart=2, replicas=20,
        library_dir=library_dir)
    assert len(result["pairs"]) == 5
    assert len(seen_before) == 1
    assert seen_before[0] == int(stamps[220 * 288 - 1])
    assert all(row["v1_windows"] == 23 for row in result["pairs"])
    assert result["summary"]["agregado"]["n"] == 5
