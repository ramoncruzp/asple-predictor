"""Regresión de la interfaz escalar de FastSim respecto a 6f3bb7b."""
import hashlib
import json

import numpy as np

from research.scenarios.fastsim import simulate_bot, simulate_simple


def _digest(value):
    if isinstance(value, dict):
        return {key: _digest(item) for key, item in sorted(value.items())
            if key not in {"low", "high"}}
    if isinstance(value, np.ndarray):
        return {"dtype": value.dtype.str, "shape": value.shape,
            "sha256": hashlib.sha256(value.tobytes()).hexdigest()}
    if isinstance(value, (list, tuple)):
        return [_digest(item) for item in value]
    if isinstance(value, (np.integer, np.floating)):
        return value.item()
    return value


def _hash_result(value):
    encoded = json.dumps(_digest(value), sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _bars():
    returns = np.asarray([
        [0, .01, .015, -.01, -.02, .005, 0, .03, -.015, .002],
        [0, -.01, -.025, -.01, .015, .025, -.005, .01, .02, -.01],
        [0, .02, .01, .005, -.03, -.01, .015, .005, -.01, .025]], dtype=float)
    close = 100 * np.exp(np.cumsum(returns, axis=1))
    return {"open": np.c_[close[:, :1], close[:, :-1]],
        "high": np.maximum(close, np.c_[close[:, :1], close[:, :-1]]) * 1.003,
        "low": np.minimum(close, np.c_[close[:, :1], close[:, :-1]]) * .997,
        "close": close}


def test_scalar_fast_sim_matches_frozen_6f3bb7b_results():
    bars = _bars()
    simple = simulate_simple(bars, n_levels=6, capital=1000, low=80, high=120,
        fee_pct=.1, initial_timestamp=0, pack_curves=True)
    bot = simulate_bot(bars, capital=1000, buy_price=98, sell_price=102,
        fee_pct=.1, initial_timestamp=0, pack_curves=True)
    assert _hash_result(simple) == "42417397e092b5ffe936d00688d83d91c5941caf0fea044a38d431dc39376a1a"
    assert _hash_result(bot) == "2f80233e2ad568c8b3bdfa77d8b0a5004b42b20b89b15b1b12c7e1cfd14b8afe"


def test_equal_per_scenario_bounds_match_scalar_for_simple_and_bot():
    bars = _bars()
    scalar = simulate_simple(bars, n_levels=6, capital=1000, low=80, high=120,
        fee_pct=.1, initial_timestamp=0, pack_curves=True)
    vector = simulate_simple(bars, n_levels=6, capital=1000,
        low=np.full(3, 80.0), high=np.full(3, 120.0), fee_pct=.1,
        initial_timestamp=0, pack_curves=True)
    assert _hash_result(scalar) == _hash_result(vector)
    scalar = simulate_bot(bars, capital=1000, buy_price=98, sell_price=102,
        fee_pct=.1, initial_timestamp=0, pack_curves=True)
    vector = simulate_bot(bars, capital=1000, buy_price=np.full(3, 98.0),
        sell_price=np.full(3, 102.0), fee_pct=.1, initial_timestamp=0,
        pack_curves=True)
    assert _hash_result(scalar) == _hash_result(vector)
