"""Feature invariance and bounded-window regression tests."""

import numpy as np
import pandas as pd

from data.feature_engineer import FeatureEngineer
from ta.momentum import RSIIndicator
from ta.volatility import AverageTrueRange


def _ohlcv(rows: int = 300) -> pd.DataFrame:
    rng = np.random.default_rng(713)
    close = 100.0 + np.cumsum(rng.normal(0.0, 0.4, size=rows))
    return pd.DataFrame({
        "timestamp": pd.date_range("2024-01-01", periods=rows, freq="h", tz="UTC"),
        "open": close - 0.05,
        "high": close + 0.3,
        "low": close - 0.3,
        "close": close,
        "volume": rng.uniform(10.0, 100.0, size=rows),
    })


def test_relative_features_are_invariant_to_price_scale():
    engineer = FeatureEngineer()
    frame = _ohlcv()
    scaled = frame.copy()
    scaled[["open", "high", "low", "close"]] *= 100.0

    original_features = engineer.compute_features(frame)[engineer.FEATURE_NAMES].to_numpy()
    scaled_features = engineer.compute_features(scaled)[engineer.FEATURE_NAMES].to_numpy()

    assert np.allclose(original_features, scaled_features, equal_nan=True)


def test_features_do_not_depend_on_start_of_window():
    engineer = FeatureEngineer()
    frame = _ohlcv(300)
    full = engineer.compute_features(frame)
    shorter = engineer.compute_features(frame.tail(250).reset_index(drop=True))

    assert np.allclose(
        full[engineer.FEATURE_NAMES].tail(50).to_numpy(),
        shorter[engineer.FEATURE_NAMES].tail(50).to_numpy(),
        equal_nan=True,
    )


def test_finite_ewm_tracks_adjust_false_reference():
    rng = np.random.default_rng(29831)
    close = pd.Series(100 + np.cumsum(rng.normal(0, 0.5, size=5000)))
    for span in (9, 21, 50):
        actual = FeatureEngineer._finite_ewm(close, span).tail(1000).to_numpy()
        expected = close.ewm(span=span, adjust=False).mean().tail(1000).to_numpy()
        relative_error = np.abs(actual - expected) / np.maximum(np.abs(expected), 1e-12)
        assert np.nanmax(relative_error) < 2e-2


def test_rsi_and_atr_track_ta_references():
    frame = _ohlcv(5000)
    engineer = FeatureEngineer()
    actual = engineer.compute_features(frame)
    reference_rsi = RSIIndicator(frame["close"], window=14).rsi().iloc[-1000:].to_numpy()
    actual_rsi = actual["rsi_14"].iloc[-1000:].to_numpy()
    assert np.nanmax(np.abs(actual_rsi - reference_rsi)) < 1.0

    reference_atr = AverageTrueRange(
        frame["high"], frame["low"], frame["close"], window=14
    ).average_true_range().iloc[-1000:].to_numpy()
    actual_atr = actual["atr_14"].iloc[-1000:].to_numpy()
    relative_error = np.abs(actual_atr - reference_atr) / np.maximum(np.abs(reference_atr), 1e-12)
    assert np.nanmax(relative_error) < 2e-2


def test_finite_ewm_step_first_response_matches_alpha():
    span = 9
    alpha = 2.0 / (span + 1.0)
    values = pd.Series([100.0] * 200 + [110.0] * 20)
    actual = FeatureEngineer._finite_ewm(values, span).iloc[200]
    expected = 100.0 + alpha * 10.0
    normalized_tail_error = abs(actual - expected) / 10.0
    assert normalized_tail_error <= 1e-6
