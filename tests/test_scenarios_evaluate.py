import numpy as np
import math

from grid.sim.meta_portfolio import pack_curve
from data.market_store import CandleStore
from research.scenarios.evaluate import (evaluate_pieces, _paired_alternatives,
    _log_width_from_sigma_24h)


def test_evaluate_pieces_uses_temporary_market_and_synthetic_library(tmp_path, monkeypatch):
    db = tmp_path / "market_data_synthetic.sqlite"
    store = CandleStore(db)
    rows = []
    previous = 100.0
    for index in range(50 * 288):
        close = 100 + .5 * np.sin(index / 37)
        rows.append({"open_ts": index * 300, "open": previous,
            "high": max(previous, close) + .01, "low": min(previous, close) - .01,
            "close": close, "volume": 1, "quote_volume": 100, "trades": 1})
        previous = close
    store.upsert("SYNTHUSDT", "5m", rows, "test")
    store.save_symbol_filters("SYNTHUSDT", {"filters": [
        {"filterType": "PRICE_FILTER", "minPrice": "0.0001", "maxPrice": "100000", "tickSize": "0.0001"},
        {"filterType": "LOT_SIZE", "minQty": "0.0001", "maxQty": "100000000", "stepSize": "0.0001"},
        {"filterType": "MIN_NOTIONAL", "minNotional": "0.01", "applyToMarket": True},
    ]})
    library = tmp_path / "library"
    library.mkdir()
    values = np.zeros((50, 288, 3), dtype=np.float32)
    values[:, :, 1], values[:, :, 2] = .2, -.2
    dates = np.arange(1_700_000_000 // 86400, 1_700_000_000 // 86400 + 50,
        dtype=np.int64) * 86400
    np.savez_compressed(library / "SYNTHUSDT.npz", values=values, dates=dates,
        daily_z_return_sum=np.zeros(50), mean_z_return=np.float32(0))
    result = evaluate_pieces([
        {"symbol": "SYNTHUSDT", "strategy": "simple", "capital": 40,
            "low": 70, "high": 130, "n": 6},
        {"symbol": "SYNTHUSDT", "strategy": "smart", "capital": 40,
            "low": 70, "high": 130, "n": 6}], perfil="RAPIDO",
        capital=100, meta=3, n_scenarios=2, n_smart=1, seed=43,
        db_path=db, library_dir=library, workers=1)
    assert result["n_scenarios"] == 2
    assert result["mode"] == "alternativas"
    assert result["pieces"][0]["metrics"]["n"] == 2
    assert result["pieces"][1]["metrics"]["n"] == 1
    assert result["pieces"][0]["aviso"] == "la geometría casi nunca se toca"
    assert all(row["piece"]["capital"] == 40 for row in result["pieces"])
    assert all(row["metrics"]["capital_unused_usdt"] == 60 for row in result["pieces"])
    assert result["portfolio"] is None
    assert len(result["smart_paired"]) == 1
    assert result["smart_paired"][0]["label"] == "smart_menos_simple"
    assert all(set(row["pnl_usdt"]) == {"mean", "ci_low", "ci_high"}
        for row in result["smart_paired"])

    portfolio = evaluate_pieces([
        {"symbol": "SYNTHUSDT", "strategy": "simple", "capital": 40,
            "low": 70, "high": 130, "n": 6}], perfil="RAPIDO",
        capital=100, meta=3, n_scenarios=2, seed=43, mode="cartera",
        db_path=db, library_dir=library, workers=1)
    assert portfolio["mode"] == "cartera"
    assert portfolio["portfolio"]["capital_unused_usdt"] == 60

    import research.scenarios.evaluate as evaluate_module
    monkeypatch.setattr(evaluate_module, "_sigma_estimates", lambda _data: (.04, .04))
    simulate_simple = evaluate_module.simulate_simple
    resolved = []
    def capture_geometry(*args, low, high, **kwargs):
        resolved.append((float(low), float(high)))
        return simulate_simple(*args, low=low, high=high, **kwargs)
    monkeypatch.setattr(evaluate_module, "simulate_simple", capture_geometry)
    rng = np.random.default_rng(43)
    z_return = rng.normal(0, 1, (50, 288)).astype(np.float32)
    wide_values = np.empty((50, 288, 3), dtype=np.float32)
    wide_values[:, :, 0] = z_return
    wide_values[:, :, 1] = np.maximum(z_return, 0) + 60
    wide_values[:, :, 2] = np.minimum(z_return, 0) - 60
    np.savez_compressed(library / "SYNTHUSDT.npz", values=wide_values, dates=dates,
        daily_z_return_sum=z_return.sum(axis=1), mean_z_return=np.float32(0))
    wide = evaluate_pieces([{"symbol": "SYNTHUSDT", "strategy": "simple",
        "capital": 70, "n": 6, "width_sigma": 2}], perfil="RAPIDO",
        capital=100, meta=3, n_scenarios=80, seed=43, db_path=db,
        library_dir=library, workers=1)
    expected_log_width = 2 * .04 * math.sqrt(20)
    assert math.isclose(_log_width_from_sigma_24h(.04, 20, 2), expected_log_width)
    assert math.isclose(math.log(resolved[-1][1] / resolved[-1][0]) / 2,
        expected_log_width, rel_tol=1e-9)
    assert wide["pieces"][0]["fills_mean"] > 5


def test_portfolio_mode_rejects_piece_sum_over_meta_capital(tmp_path):
    import pytest

    with pytest.raises(ValueError, match=r"las piezas suman 120 USDT y la meta tiene 100.*--modo alternativas"):
        evaluate_pieces([{"symbol": "X", "strategy": "simple", "capital": 120}],
            perfil="RAPIDO", capital=100, meta=3, mode="cartera",
            db_path=tmp_path / "not-opened.sqlite", library_dir=tmp_path / "library")


def test_alternative_without_smart_simple_geometry_pairs_against_first():
    timestamps = [0, 900]
    first = pack_curve(timestamps, [40, 41], [40, 41], 40)
    other = pack_curve(timestamps, [40, 42], [40, 42], 40)
    pieces = [
        {"symbol": "AUSDT", "strategy": "simple", "capital": 40},
        {"symbol": "BUSDT", "strategy": "bot", "capital": 40},
    ]
    result = _paired_alternatives(pieces, [[first], [other]], capital=100,
        target=3, loss_pct=18, seed=43)
    assert len(result) == 1
    assert result[0]["label"] == "pieza_menos_primera"
    assert (result[0]["from_index"], result[0]["to_index"]) == (0, 1)


def test_simple_uses_all_scenarios_when_smart_uses_n_smart(tmp_path, monkeypatch):
    import research.scenarios.evaluate as evaluate_module

    store = CandleStore(tmp_path / "market_synthetic.sqlite")
    rows = []
    for index in range(30 * 288 + 1):
        rows.append({"open_ts": index * 300, "open": 100, "high": 101,
            "low": 99, "close": 100, "volume": 1, "quote_volume": 100, "trades": 1})
    store.upsert("SYNTHUSDT", "5m", rows, "test")
    monkeypatch.setattr(evaluate_module, "_sigma_estimates", lambda _data: (.04, .04))
    monkeypatch.setattr(evaluate_module, "_calibration_factors", lambda *_args: {})
    scenario_count, smart_count = 1000, 200
    length = 20 * 288

    def fake_generate(_symbols, *, n_scenarios, **_kwargs):
        assert n_scenarios == scenario_count
        for start in (0, 500):
            ids = np.arange(start, start + 500, dtype=np.float64)
            close = np.broadcast_to((100 + ids * .001)[:, None], (500, length))
            ohlc = {"open": close, "high": np.broadcast_to((101 + ids * .001)[:, None], close.shape),
                "low": np.broadcast_to((99 + ids * .001)[:, None], close.shape), "close": close,
                "sigma_path": np.full((500, 20), .04)}
            yield {"scenario_start": start, "symbols": {"SYNTHUSDT": ohlc}}

    monkeypatch.setattr(evaluate_module, "generate", fake_generate)
    timestamps = [0, 900]

    def fake_fast(_bars, *, capital, **_kwargs):
        ids = np.asarray(_bars["close"][:, 0]) - 100
        curves = [pack_curve(timestamps, [capital, capital + value],
            [capital, capital + value], capital) for value in ids]
        count = len(ids)
        return {"curves": curves, "cycles": np.zeros(count),
            "inventory_qty": np.zeros(count), "inventory_value": np.zeros(count),
            "buys": np.zeros(count, dtype=int), "sells": np.zeros(count, dtype=int)}

    monkeypatch.setattr(evaluate_module, "simulate_simple", fake_fast)
    smart_calls = []
    smart_scenario_ids = []

    def fake_smart(_bars, *, capital, n_smart, **_kwargs):
        smart_calls.append(n_smart)
        ids = np.asarray(_bars["close"][:n_smart, 0]) - 100
        smart_scenario_ids.extend(ids.tolist())
        return [{"curve": pack_curve(timestamps, [capital, capital + value],
            [capital, capital + value], capital), "metrics": {"cycles_completed": 0},
            "inventory_qty": 0, "inventory_value": 0, "events": []}
            for value in ids]

    monkeypatch.setattr(evaluate_module, "evaluate_smart_scenarios", fake_smart)
    result = evaluate_pieces([
        {"symbol": "SYNTHUSDT", "strategy": "simple", "capital": 40, "low": 80, "high": 120},
        {"symbol": "SYNTHUSDT", "strategy": "smart", "capital": 40, "low": 80, "high": 120},
    ], perfil="RAPIDO", capital=100, meta=3, n_scenarios=scenario_count,
        n_smart=smart_count, seed=43, db_path=tmp_path / "market_synthetic.sqlite",
        library_dir=tmp_path / "unused", workers=1)

    assert result["n_scenarios"] == scenario_count
    assert result["pieces"][0]["metrics"]["n"] == scenario_count
    assert result["pieces"][1]["metrics"]["n"] == smart_count
    assert smart_calls == [smart_count]
    assert np.allclose(smart_scenario_ids, np.arange(smart_count, dtype=float) * .001,
        rtol=0, atol=1e-12)
    assert result["smart_paired"][0]["n"] == smart_count
    assert abs(result["smart_paired"][0]["pnl_usdt"]["mean"]) < 1e-6
