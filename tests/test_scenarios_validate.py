import numpy as np

from research.scenarios.validate import baseline_window_indices, summarize_validation
from data.market_store import CandleStore
from grid.sim.data import CandleData
from research.scenarios.evaluate import evaluate_pieces


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


def test_validation_reports_no_pairs_without_inventing_metrics():
    assert summarize_validation([]) == {"n": 0, "estado": "sin_pares"}


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


def test_validate_scenarios_wires_causal_forecast_actual_and_23_v1_windows(monkeypatch):
    import research.scenarios.validate as module

    count = 250 * 288
    stamps = 1_700_000_000 + np.arange(count, dtype=np.int64) * 300
    close = 100 * np.exp(np.arange(count) * .000001)
    data = CandleData(stamps, close.copy(), close * 1.001, close * .999, close.copy(), 0)

    class FakeStore:
        def __init__(self, _path):
            pass

        def load(self, _symbol, _interval):
            return data

    seen_before = []
    def fake_prediction(pieces, *, before, **_kwargs):
        seen_before.append(before)
        return {"pieces": [{"scenario_results": [
            {"pnl_usdt": float(value), "reached": value > 0, "loss_cut": value < -10}
            for value in (-2, -1, 0, 1, 2)]} for _ in pieces]}

    def fake_history(pieces, *, since, until, **_kwargs):
        actual_ts = int(until)
        baseline_ts = [actual_ts - 20 * 86400 - index * 7 * 86400 for index in range(23)]
        starts = [{"timestamp": stamp, "pnl_usdt": 1.0, "reached": True, "loss_cut": False}
            for stamp in baseline_ts]
        starts.append({"timestamp": actual_ts, "pnl_usdt": -.5, "reached": False, "loss_cut": False})
        return {"symbols": {"SYNTHUSDT": {"pieces": [{"piece": piece,
            "variantes": {"real": {"starts": starts}}} for piece in pieces]}}}

    monkeypatch.setattr(module, "CandleStore", FakeStore)
    monkeypatch.setattr(module, "market_db_path", lambda value: value)
    monkeypatch.setattr(module, "evaluate_pieces", fake_prediction)
    monkeypatch.setattr(module, "evaluate_history", fake_history)
    result = module.validate_scenarios(db_path="synthetic.sqlite", months=1, step_days=30,
        symbols=["SYNTHUSDT"], n_scenarios=5, n_smart=2, replicas=20)
    assert len(result["pairs"]) == 5
    assert len(seen_before) == 1
    assert seen_before[0] == int(stamps[220 * 288 - 1])
    assert all(row["v1_windows"] == 23 for row in result["pairs"])
    assert result["summary"]["agregado"]["n"] == 5
