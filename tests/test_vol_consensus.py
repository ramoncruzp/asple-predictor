from __future__ import annotations

import numpy as np

from models.volatility.consensus import (
    dispersion_confidence, inverse_mse_weights, median_logvol, weighted_logvol,
)
from models.volatility.evaluation import moving_block_bootstrap_difference
from scripts.vol_consensus_eval import _select_and_score_splits, _holm
from data.splits import chronological_split
from config.models_config import VOL_MODELS


def test_inverse_mse_weights_are_normalized_and_zero_noneligible():
    weights = inverse_mse_weights({"a": 1.0, "b": 2.0, "c": 0.5}, ["a", "b"])
    assert np.isclose(sum(weights.values()), 1.0)
    assert weights["a"] > weights["b"]
    assert weights["c"] == 0.0


def test_injected_weights_drive_weighted_result_and_median():
    values = {"a": -5.0, "b": -3.0, "excluded": 90.0}
    assert weighted_logvol(values, {"a": 0.75, "b": 0.25, "excluded": 0.0}) == -4.5
    assert median_logvol(values, ["a", "b"]) == -4.0
    assert weighted_logvol(values, {}) is None
    assert median_logvol(values, []) is None


def test_dispersion_confidence_uses_fixed_iqr_boundaries_and_minimum_count():
    assert dispersion_confidence({"a": 0.0, "b": 0.0, "c": 0.099, "d": 0.099}, ["a", "b", "c", "d"])["confidence"] == "alta"
    assert dispersion_confidence({"a": 0.0, "b": 0.0, "c": 0.10, "d": 0.10}, ["a", "b", "c", "d"])["confidence"] == "media"
    assert dispersion_confidence({"a": 0.0, "b": 0.0, "c": 0.25, "d": 0.25}, ["a", "b", "c", "d"])["confidence"] == "media"
    assert dispersion_confidence({"a": 0.0, "b": 0.0, "c": 0.251, "d": 0.251}, ["a", "b", "c", "d"])["confidence"] == "baja"
    assert dispersion_confidence({"a": 0.0, "b": 0.02}, ["a", "b"])["confidence"] == "baja"
    assert dispersion_confidence({}, [])["dispersion_iqr"] is None


def test_chronological_split_embargo_is_horizon_rows():
    train, validation, test = chronological_split(1000, train=0.70, val=0.15, embargo=24)
    assert validation.start - train.stop == 24
    assert test.start - validation.stop == 24


def test_validation_weights_do_not_change_when_test_data_changes():
    rng = np.random.default_rng(1919)
    actual_val = -5.0 + rng.normal(scale=0.1, size=700)
    actual_test = -5.0 + rng.normal(scale=0.1, size=500)
    noisy = actual_val + rng.normal(scale=0.08, size=len(actual_val))
    predictions_val = {name: noisy.copy() for name in VOL_MODELS}
    predictions_val["Persistence"] = noisy
    predictions_val["EWMA"] = actual_val + rng.normal(scale=0.002, size=len(actual_val))
    test_noisy = actual_test + rng.normal(scale=0.08, size=len(actual_test))
    predictions_test = {name: test_noisy.copy() for name in VOL_MODELS}
    predictions_test["EWMA"] = actual_test + rng.normal(scale=0.002, size=len(actual_test))
    baseline = _select_and_score_splits(actual_val, predictions_val, actual_test, predictions_test)
    changed_test = actual_test + 0.5
    changed_predictions = {name: changed_test.copy() for name in VOL_MODELS}
    changed = _select_and_score_splits(actual_val, predictions_val, changed_test, changed_predictions)
    assert baseline["selection"] == changed["selection"]
    assert baseline["selection"]["eligible_models"] == ["EWMA"]
    assert baseline["selection"]["weights"]["EWMA"] == 1.0
    assert all(baseline["selection"]["weights"][name] == 0.0 for name in VOL_MODELS if name != "EWMA")


def test_bootstrap_reports_deterministic_one_sided_p_value():
    reference = np.linspace(1.0, 2.0, 600) + 1.0
    candidate = np.linspace(1.0, 2.0, 600)
    result = moving_block_bootstrap_difference(reference, candidate, seed=42)
    assert result["beats"] is True
    assert result["p_value_one_sided"] < 0.05
    assert result == moving_block_bootstrap_difference(reference, candidate, seed=42)


def test_holm_adjustment_is_monotone_and_applies_to_eight_comparisons():
    values = {f"h{i}": i / 100 for i in range(8)}
    adjusted = _holm(values)
    assert len(adjusted) == 8
    ordered = sorted(values, key=values.get)
    seq = [adjusted[key] for key in ordered]
    assert seq == sorted(seq)
